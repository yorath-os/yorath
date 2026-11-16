"""FastAPI application assembly. Dependencies go into app.state so tests can replace them."""

import asyncio
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI

from yorath import PRODUCT_NAME, __version__, plugins
from yorath.control.agents.connected import connected_addresses
from yorath.control.agents.connected_router import node_router
from yorath.control.agents.connected_router import router as connected_router
from yorath.control.agents.node_body import NodeBodyLimit
from yorath.control.agents.router import router as agents_router
from yorath.control.cluster.router import router as cluster_router
from yorath.control.cluster.service import heartbeat_loop
from yorath.control.data.router import router as data_router
from yorath.control.desk.router import router as desk_router
from yorath.control.identity.api_keys import router as api_keys_router
from yorath.control.identity.router import router as identity_router
from yorath.control.journal import Journal, retention_loop
from yorath.control.journal.egress import egress_loop
from yorath.control.journal.router import router as journal_router
from yorath.control.setup.router import router as setup_router
from yorath.db import make_engine, make_sessions
from yorath.distribution.health import db_check, ollama_check
from yorath.distribution.manifest import load_manifest
from yorath.distribution.router import router as distribution_router
from yorath.enforcement import gateway
from yorath.enforcement.gateway.public import router as endpoint_router
from yorath.enforcement.gateway.router import router as gateway_router
from yorath.enforcement.pdp import CedarPDP
from yorath.enforcement.policy_store import builtin_policies, combine, load_policy_text
from yorath.enforcement.router import router as policy_router
from yorath.enforcement.secrets import DbSecretStore
from yorath.runtime.chat.router import router as chat_router
from yorath.runtime.chat.runs import ChatRuns
from yorath.runtime.developer.router import router as github_router
from yorath.runtime.rag.embeddings import OllamaEmbeddings
from yorath.runtime.rag.indexer import Indexer
from yorath.schema import alembic_head
from yorath.settings import Settings, get_settings


def _keepalive_transport() -> httpx.AsyncHTTPTransport:
    """TCP keepalive for model server connections: a dead peer is detected in about 25 s.

    Without it a call on an already open connection to a node that went down waits for the whole
    first-token timeout (lab run 14.11.2026: 120 s). The answer itself has no read limit. Model
    servers are reached directly, never through the egress proxy, so this client has no proxy.
    """
    import socket

    options: list[tuple[int, int, int]] = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    for name, value in (("TCP_KEEPIDLE", 10), ("TCP_KEEPINTVL", 5), ("TCP_KEEPCNT", 3)):
        if hasattr(socket, name):  # Linux; other systems keep their defaults
            options.append((socket.IPPROTO_TCP, getattr(socket, name), value))
    return httpx.AsyncHTTPTransport(socket_options=options)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        sessions = make_sessions(engine)
        client = httpx.AsyncClient()
        app.state.http = client
        # calls to model servers: a cluster node that dies mid-call is noticed by TCP keepalive
        model_client = httpx.AsyncClient(transport=_keepalive_transport())
        app.state.http_models = model_client
        app.state.sessions = sessions
        app.state.journal = Journal(sessions)
        app.state.secrets = DbSecretStore(sessions, settings.secret_key)
        app.state.indexer = Indexer(
            sessions,
            lambda model: OllamaEmbeddings(client, settings.ollama_url, model),
            app.state.manifest.rag,
        )
        async with sessions() as s:
            stored = await load_policy_text(s)
        if stored is not None:
            app.state.pdp.load(stored)  # empty table → the built-in set stays
        gateway.install(app)
        await app.state.indexer.schedule_pending()  # documents left unindexed before a restart
        app.state.checks = {
            "db": db_check(engine, settings.health_timeout_seconds),
            "ollama": ollama_check(client, settings.ollama_url, settings.health_timeout_seconds),
        }
        retention = asyncio.create_task(
            retention_loop(sessions, app.state.manifest.journal.trace_retention_days)
        )
        egress = asyncio.create_task(
            egress_loop(sessions, app.state.journal, settings.egress_node_log,
                        settings.host_report_dir, settings.node_name,
                        agents=connected_addresses(app.state.manifest))
        )  # fmt: skip
        pulse = asyncio.create_task(heartbeat_loop(sessions, app.state.journal))
        try:
            yield
        finally:
            for task in (egress, pulse):
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await app.state.chat_runs.shutdown()
            await app.state.indexer.wait_all()
            retention.cancel()
            with suppress(asyncio.CancelledError):
                await retention
            await client.aclose()
            await model_client.aclose()
            await engine.dispose()

    app = FastAPI(title=f"{PRODUCT_NAME} core", version=__version__, lifespan=lifespan)
    app.add_middleware(NodeBodyLimit)  # a worker node's requests are bounded (S6)
    app.state.settings = settings
    app.state.manifest = load_manifest(settings.manifest_path)
    app.state.schema_head = alembic_head()
    app.state.build_sha = settings.build_sha
    app.state.plugins = plugins.discover()
    app.state.checks = {}
    app.state.github_deliveries = set()
    app.state.github_running = set()
    app.state.background = set()  # detached tasks (Developer Agent runs)
    app.state.chat_runs = ChatRuns()  # chat answers in progress
    app.state.pdp = CedarPDP()
    app.state.pdp.load(combine(builtin_policies()))
    app.include_router(distribution_router)
    app.include_router(identity_router)
    app.include_router(api_keys_router)
    app.include_router(policy_router)
    app.include_router(setup_router)
    app.include_router(data_router)
    app.include_router(connected_router)
    app.include_router(agents_router)
    app.include_router(node_router)
    app.include_router(cluster_router)
    app.include_router(gateway_router)
    app.include_router(endpoint_router)
    app.include_router(chat_router)
    app.include_router(github_router)
    app.include_router(journal_router)
    app.include_router(desk_router)
    return app


app = create_app()
