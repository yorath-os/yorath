"""``yorath``: runs the core and service commands."""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import uvicorn

from yorath.distribution.manifest import load_manifest
from yorath.settings import get_settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="yorath")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the core")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")
    sub.add_parser("check-manifest", help="validate distribution.yaml against the schema")
    sub.add_parser(
        "sandbox-runner", help="sandbox runner service, the only one with the Docker socket"
    )
    sub.add_parser("node-agent", help="node service: containers of connected agents")
    from yorath.control.agents.connected_cli import add_parser as add_agent_parser

    add_agent_parser(sub)
    from yorath.control.cluster.cli import add_parser as add_node_parser

    add_node_parser(sub)
    sa = sub.add_parser("sync-agents", help="create agents and revisions from md files")
    sa.add_argument(
        "--dir", type=Path, default=None, help="agents/ directory (defaults to the settings)"
    )
    sub.add_parser("purge-traces", help="delete traces older than journal.trace_retention_days")
    sub.add_parser("sync-policies", help="load the built-in Cedar policies into the policies table")
    sub.add_parser("migrate", help="apply migrations (alembic upgrade head)")
    sub.add_parser("reindex", help="reindex all parsed documents")
    sub.add_parser("inventory", help="record node resources in nodes")
    sub.add_parser(
        "bootstrap", help="migrate + sync-policies + sync-agents + inventory (container start)"
    )
    cs = sub.add_parser("changes-since", help="JSON: how much data appeared after a moment")
    cs.add_argument("moment", help="ISO 8601 with a time zone")
    rr = sub.add_parser("record-restore", help="record installation.restored in the audit log")
    rr.add_argument("--backup-at", required=True, help="time of the restored backup, ISO 8601")
    rr.add_argument("--version-before", required=True)
    rr.add_argument("--version-after", required=True)
    rr.add_argument("--replaced", required=True, help="JSON from changes-since before the restore")
    rr.add_argument("--safety-backup", default=None)
    rr.add_argument("--reason", default="restore", choices=["restore", "rollback", "failed-update"])
    mo = sub.add_parser("model", help="local model: current, list, pull")
    mo_sub = mo.add_subparsers(dest="model_cmd")
    mo_sub.add_parser("status", help="current model, context window, recommended (default)")
    mo_sub.add_parser("list", help='catalog with the "fits" estimate and pulled models')
    mp = mo_sub.add_parser("pull", help="pull and make it the main one (local/default)")
    mp.add_argument(
        "name", nargs="?", help="Ollama name or hf.co/...; omitted means the recommended one"
    )
    mp.add_argument(
        "--force", action="store_true", help="even if the estimate says it does not fit"
    )
    mp.add_argument("--node", help="place on this cluster node (default: the main node)")
    mp.add_argument("--gpu", type=int, help="only the model server on this card of the node")
    mp.add_argument(
        "--replace", action="store_true", help="swap the model that stands on the server"
    )
    mr = mo_sub.add_parser("remove", help="take a model off a node's model servers")
    mr.add_argument("name", help="Ollama name of a catalog model")
    mr.add_argument("--node", help="cluster node (default: the main node)")
    mr.add_argument("--gpu", type=int, help="only the model server on this card")
    mr.add_argument(
        "--force", action="store_true", help="even if local/default loses its last placement"
    )
    md = mo_sub.add_parser("default", help="point local/default at a catalog model")
    md.add_argument("name", help="Ollama name of a catalog model")
    cu = sub.add_parser("create-user", help="create a user; the password is read from stdin")
    cu.add_argument("--login", required=True)
    cu.add_argument("--display-name", default=None)
    cu.add_argument("--role", default="member")
    a = ap.parse_args(argv)
    settings = get_settings()
    if a.cmd == "check-manifest":
        m = load_manifest(settings.manifest_path)
        print(
            f"{m.distribution.name} {m.distribution.version}, "
            f"schema {m.distribution.schema_version}: ok"
        )
        return 0
    if a.cmd == "sandbox-runner":
        from yorath.enforcement.sandbox.runner import main as runner_main

        runner_main()
        return 0
    if a.cmd == "node-agent":
        import logging

        from yorath.runtime.connected.node_agent import NodeSettings
        from yorath.runtime.connected.node_agent import run as node_run

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        asyncio.run(node_run(NodeSettings(), settings.manifest_path))
        return 0
    if a.cmd == "node":
        from yorath.control.cluster.cli import run as node_cli

        return asyncio.run(node_cli(a))
    if a.cmd == "agent":
        from yorath.control.agents.connected_cli import run as agent_run

        return asyncio.run(agent_run(a))
    if a.cmd == "sync-agents":
        return asyncio.run(_sync_agents(a.dir or settings.agents_dir))
    if a.cmd == "purge-traces":
        return asyncio.run(_purge_traces())
    if a.cmd == "sync-policies":
        return asyncio.run(_sync_policies())
    if a.cmd == "migrate":
        return _migrate()
    if a.cmd == "reindex":
        return asyncio.run(_reindex())
    if a.cmd == "inventory":
        return asyncio.run(_inventory())
    if a.cmd == "bootstrap":
        return _bootstrap()
    if a.cmd == "changes-since":
        return asyncio.run(_changes_since(a.moment))
    if a.cmd == "record-restore":
        return asyncio.run(_record_restore(a))
    if a.cmd == "model":
        return asyncio.run(_model(a))
    if a.cmd == "create-user":
        password = sys.stdin.readline().rstrip("\n")
        return asyncio.run(_create_user(a.login, a.display_name or a.login, a.role, password))
    uvicorn.run("yorath.app:app", host=a.host, port=a.port, reload=a.reload)
    return 0


async def _model(a: argparse.Namespace) -> int:
    import httpx

    from yorath.control.journal import Journal
    from yorath.control.setup import model_cli
    from yorath.control.setup.hardware import detect
    from yorath.control.setup.ollama import OllamaClient
    from yorath.db import make_engine, make_sessions

    settings = get_settings()
    manifest = load_manifest(settings.manifest_path)
    hw = detect(settings)
    engine = make_engine(settings.database_url)
    sessions = make_sessions(engine)
    try:
        async with httpx.AsyncClient() as http:
            client = OllamaClient(http, settings.ollama_url)
            if a.model_cmd == "list":
                return await model_cli.show_list(sys.stdout, manifest, hw, sessions, client)
            if a.model_cmd == "pull" and a.node is None and a.gpu is None:
                return await model_cli.pull(
                    sys.stdout, manifest, hw, sessions, Journal(sessions), client,
                    name=a.name, force=a.force,
                )  # fmt: skip
            if a.model_cmd == "pull":
                return await model_cli.pull_on_node(
                    sys.stdout, manifest, sessions, Journal(sessions), client,
                    name=a.name, node=a.node, gpu=a.gpu, force=a.force, replace=a.replace,
                )  # fmt: skip
            if a.model_cmd == "remove":
                return await model_cli.remove(
                    sys.stdout, sessions, Journal(sessions), client,
                    name=a.name, node=a.node, gpu=a.gpu, force=a.force,
                )  # fmt: skip
            if a.model_cmd == "default":
                return await model_cli.set_default(
                    sys.stdout, sessions, Journal(sessions), name=a.name
                )
            return await model_cli.show_status(sys.stdout, manifest, hw, sessions)
    finally:
        await engine.dispose()


def _moment(text: str):
    from datetime import datetime

    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError(f"moment has no time zone: {text}")
    return moment


async def _changes_since(text: str) -> int:
    import json

    from yorath.db import make_engine, make_sessions
    from yorath.distribution.lifecycle import changes_since

    engine = make_engine(get_settings().database_url)
    try:
        async with make_sessions(engine)() as session:
            print(json.dumps(await changes_since(session, _moment(text))))
        return 0
    finally:
        await engine.dispose()


async def _record_restore(a: argparse.Namespace) -> int:
    import json

    from yorath.control.journal import Journal
    from yorath.db import make_engine, make_sessions
    from yorath.distribution.lifecycle import record_restore

    engine = make_engine(get_settings().database_url)
    try:
        await record_restore(
            Journal(make_sessions(engine)),
            backup_at=_moment(a.backup_at),
            version_before=a.version_before,
            version_after=a.version_after,
            replaced={k: int(v) for k, v in json.loads(a.replaced).items()},
            safety_backup=a.safety_backup,
            reason=a.reason,
        )
        print("record-restore: ok")
        return 0
    finally:
        await engine.dispose()


async def _sync_agents(directory: Path) -> int:
    from yorath.control.agents.loader import AgentFileError, sync_agents_dir, sync_custom_agents
    from yorath.control.identity.service import ensure_service_accounts
    from yorath.db import make_engine, make_sessions

    engine = make_engine(get_settings().database_url)
    try:
        async with make_sessions(engine)() as session:
            try:
                results = await sync_agents_dir(session, directory)
            except AgentFileError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            custom, custom_warnings = await sync_custom_agents(session, directory)
            results = [*results, *custom]
            created, warnings = await ensure_service_accounts(session)
            warnings = [*custom_warnings, *warnings]
        for r in results:
            print(f"{r.name}: revision {r.revision_no} ({'new' if r.created else 'unchanged'})")
        for name in created:
            print(f"{name}: service account created")
        for w in warnings:
            print(f"warning: {w}", file=sys.stderr)
        return 0
    finally:
        await engine.dispose()


async def _create_user(login: str, display_name: str, role: str, password: str) -> int:
    from pydantic import ValidationError

    from yorath.control.identity.schemas import UserCreate
    from yorath.control.identity.service import IdentityError, create_user
    from yorath.db import make_engine, make_sessions

    try:
        data = UserCreate(login=login, password=password, display_name=display_name, role=role)
    except ValidationError as exc:
        print(f"error: {exc.errors()[0]['msg']}", file=sys.stderr)
        return 1
    engine = make_engine(get_settings().database_url)
    try:
        async with make_sessions(engine)() as session:
            try:
                user = await create_user(session, None, data, actor=None)
            except IdentityError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
        print(f"{user.login}: {user.role}, id {user.id}")
        return 0
    finally:
        await engine.dispose()


def _migrate() -> int:
    import subprocess

    from yorath.schema import ALEMBIC_INI

    print("migrate: alembic upgrade head")
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ALEMBIC_INI.parent
    ).returncode


async def _reindex() -> int:
    import httpx
    from sqlalchemy import select

    from yorath.control.model import Document, DocumentStatus
    from yorath.db import make_engine, make_sessions
    from yorath.runtime.rag.embeddings import OllamaEmbeddings
    from yorath.runtime.rag.indexer import Indexer

    settings = get_settings()
    manifest = load_manifest(settings.manifest_path)
    engine = make_engine(settings.database_url)
    try:
        async with httpx.AsyncClient() as http:
            indexer = Indexer(
                make_sessions(engine),
                lambda model: OllamaEmbeddings(http, settings.ollama_url, model),
                manifest.rag,
            )
            async with make_sessions(engine)() as session:
                ids = (
                    await session.scalars(
                        select(Document.id).where(Document.status != DocumentStatus.FAILED)
                    )
                ).all()
            failed = 0
            for doc_id in ids:
                try:
                    n = await indexer.run(doc_id)
                    print(f"{doc_id}: {n} chunks")
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    print(f"{doc_id}: error {exc}", file=sys.stderr)
        print(f"reindex: {len(ids)} documents, {failed} failed")
        return 1 if failed else 0
    finally:
        await engine.dispose()


async def _inventory() -> int:
    from yorath.control.setup.hardware import detect
    from yorath.control.setup.inventory import upsert_node
    from yorath.db import make_engine, make_sessions

    settings = get_settings()
    hw = detect(settings)
    engine = make_engine(settings.database_url)
    try:
        async with make_sessions(engine)() as session:
            node = await upsert_node(session, settings.node_name, hw)
        print(
            f"inventory: {node.name} vcpu={node.cpu_count} ram={node.ram_gb}G "
            f"gpu={node.gpu or '-'} vram={node.vram_gb or '-'}"
        )
        return 0
    finally:
        await engine.dispose()


async def _sweep_executions() -> int:
    """Executions left ``running`` by a previous process are closed as ``cancelled``."""
    from yorath.control.journal import Journal
    from yorath.db import make_engine, make_sessions
    from yorath.enforcement.gateway import sweep_unfinished

    engine = make_engine(get_settings().database_url)
    try:
        sessions = make_sessions(engine)
        async with sessions() as session:
            n = await sweep_unfinished(session, Journal(sessions))
        print(f"sweep-executions: closed {n}")
        return 0
    finally:
        await engine.dispose()


def _bootstrap() -> int:
    """Container start: any failure means a non-zero exit code and a clear log line."""
    settings = get_settings()
    steps: list[tuple[str, Any]] = [
        ("migrate", _migrate),
        ("sync-policies", lambda: asyncio.run(_sync_policies())),
        ("inventory", lambda: asyncio.run(_inventory())),
        ("sweep-executions", lambda: asyncio.run(_sweep_executions())),
    ]
    if settings.agents_dir.is_dir():
        steps.insert(2, ("sync-agents", lambda: asyncio.run(_sync_agents(settings.agents_dir))))
    for name, step in steps:
        print(f"bootstrap: {name}")
        code = step()
        if code != 0:
            print(f"bootstrap: step {name} exited with code {code}", file=sys.stderr)
            return code
    print("bootstrap: ok")
    return 0


async def _sync_policies() -> int:
    from yorath.db import make_engine, make_sessions
    from yorath.enforcement.pdp import CedarPDP, PolicyValidationError
    from yorath.enforcement.policy_store import load_policy_text, sync_default_policies

    engine = make_engine(get_settings().database_url)
    try:
        async with make_sessions(engine)() as session:
            report = await sync_default_policies(session)
            text = await load_policy_text(session) or ""
        for name in report.created:
            print(f"{name}: created")
        for name in report.updated:
            print(f"{name}: updated to the built-in version")
        for w in report.warnings:
            print(f"warning: {w}", file=sys.stderr)
        try:
            CedarPDP().load(text)
        except PolicyValidationError as exc:
            print(f"error: policy set in the database is invalid: {exc}", file=sys.stderr)
            return 1
        print("policy set is valid")
        return 0
    finally:
        await engine.dispose()


async def _purge_traces() -> int:
    from yorath.control.journal.retention import purge_traces
    from yorath.db import make_engine, make_sessions

    settings = get_settings()
    days = load_manifest(settings.manifest_path).journal.trace_retention_days
    engine = make_engine(settings.database_url)
    try:
        async with make_sessions(engine)() as session:
            removed = await purge_traces(session, days)
        print(f"deleted spans older than {days} days: {removed}")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
