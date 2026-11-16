"""``yorath agent …`` in the core container (change connected-agents): same as the panel buttons.

root runs the command on the host (``yorath agent add`` in ``yorath.sh``), so it acts on behalf
of the installation's first owner; the audit log says ``via: cli``. The bot token is read from
stdin, not arguments: arguments are visible in the process list and shell history.
"""

import argparse
import sys

from sqlalchemy import select

from yorath.control.agents import connected
from yorath.control.journal import Journal
from yorath.control.journal.egress import read_report
from yorath.control.model import User
from yorath.db import make_engine, make_sessions
from yorath.distribution.manifest import load_manifest
from yorath.enforcement.secrets import DbSecretStore
from yorath.settings import get_settings


def add_parser(sub: argparse._SubParsersAction) -> None:
    ag = sub.add_parser("agent", help="connected agents: OpenClaw, Hermes")
    s = ag.add_subparsers(dest="agent_cmd", required=True)
    s.add_parser("list", help="catalog: connected or not, version, status, internet, bot")
    add = s.add_parser("add", help="connect an agent from the catalog")
    add.add_argument("name")
    add.add_argument("--version", default=None, help="image tag; defaults to the verified one")
    add.add_argument("--node", default=None, help="cluster node; defaults to the main node")
    add.add_argument("--telegram", action="store_true", help="bot token as a line from stdin")
    add.add_argument("--telegram-users", default="", help="who to answer: comma-separated ids")
    rm = s.add_parser("remove", help="disable the agent; --purge wipes memory and secrets")
    rm.add_argument("name")
    rm.add_argument("--purge", action="store_true")
    up = s.add_parser(
        "upgrade", help="change the version; without --version, back to the verified one"
    )
    up.add_argument("name")
    up.add_argument("--version", default=None)
    tg = s.add_parser("telegram", help="Telegram bot: token from stdin; --off disconnects it")
    tg.add_argument("name")
    tg.add_argument("--off", action="store_true")
    tg.add_argument("--users", default="", help="who to answer: comma-separated ids")
    net = s.add_parser("internet", help="agent internet access: on | off")
    net.add_argument("name")
    net.add_argument("state", choices=["on", "off"])


def _users(raw: str) -> list[str]:
    return [u.strip() for u in raw.split(",") if u.strip()]


def _token() -> str:
    token = sys.stdin.readline().strip()
    if not token:
        raise connected.ConnectedError(400, "telegram_token_empty", "the bot token is empty")
    return token


def _print(rows: list[connected.ConnectedOut]) -> None:
    h = ("AGENT", "CONNECTED", "NODE", "VERSION", "STATUS", "INTERNET")
    print(f"{h[0]:10} {h[1]:10} {h[2]:10} {h[3]:22} {h[4]:10} {h[5]:9} TELEGRAM")
    for r in rows:
        version = r.running_version or r.version or r.tested_version
        if r.untested:
            version += " (not verified)"
        status = r.status or "-"
        if r.status_detail:
            status += f": {r.status_detail}"
        print(
            f"{r.name:10} {'yes' if r.connected else 'no':10} {r.node or 'main':10} "
            f"{version:22} {status:10} "
            f"{'on' if r.network_enabled else 'off':9} {'set' if r.telegram else '-'}"
        )


async def run(a: argparse.Namespace) -> int:
    settings = get_settings()
    manifest = load_manifest(settings.manifest_path)
    engine = make_engine(settings.database_url)
    sessions = make_sessions(engine)
    journal, store = Journal(sessions), DbSecretStore(sessions, settings.secret_key)
    try:
        async with sessions() as db:
            owner = await db.scalar(
                select(User).where(User.role == "owner").order_by(User.created_at).limit(1)
            )
            if a.agent_cmd == "list":
                _print(await connected.catalog(db, manifest))
                return 0
            if owner is None:
                print("error: no owner yet; finish the setup wizard", file=sys.stderr)
                return 1
            name = a.name
            if a.agent_cmd == "add":
                data = connected.ConnectIn(
                    version=a.version,
                    node=a.node,
                    telegram_token=_token() if a.telegram else None,
                    telegram_users=_users(a.telegram_users),
                )
                report = read_report(settings.host_report_dir)
                await connected.connect(db, journal, store, manifest, report, owner, name, data)
                print(f"{name}: connected; the container starts within a minute: yorath agent list")
            elif a.agent_cmd == "remove":
                await connected.disconnect(db, journal, store, manifest, owner, name, a.purge)
                print(f"{name}: {'removed along with its memory' if a.purge else 'disabled'}")
            elif a.agent_cmd == "upgrade":
                await connected.set_version(db, journal, manifest, owner, name, a.version)
                print(f"{name}: version {a.version or 'verified'}; the container will be recreated")
            elif a.agent_cmd == "telegram":
                channel = connected.ChannelIn(
                    telegram_token=None if a.off else _token(), telegram_users=_users(a.users)
                )
                await connected.set_channel(db, journal, store, manifest, owner, name, channel)
                print(f"{name}: bot {'disconnected' if a.off else 'saved'}")
            elif a.agent_cmd == "internet":
                on = a.state == "on"
                await connected.set_network(db, journal, manifest, owner, name, on)
                print(f"{name}: internet {'on' if on else 'off'}")
        return 0
    except connected.ConnectedError as exc:
        print(f"error ({exc.code}): {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()
