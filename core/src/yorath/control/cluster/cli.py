"""``yorath node …`` in the main node core container (change cluster-nodes): same as System → Nodes.

root runs the command on the host (``yorath node`` in ``yorath.sh``) on behalf of the first owner.
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

from yorath.control.cluster import placements, service
from yorath.control.journal import Journal
from yorath.control.model import User
from yorath.db import make_engine, make_sessions
from yorath.settings import get_settings


def add_parser(sub: argparse._SubParsersAction) -> None:
    nd = sub.add_parser("node", help="cluster: nodes by token")
    s = nd.add_subparsers(dest="node_cmd", required=True)
    tk = s.add_parser("token", help="one-time join token (24 h)")
    tk.add_argument("--endpoint", required=True, help="main node WireGuard address:port")
    tk.add_argument("--main-key", required=True, help="main node WireGuard public key")
    tk.add_argument("--ca-fingerprint", default="-", help="sha256 of the main node root CA, or -")
    tk.add_argument("--url", default="", help="main node address for the join command")
    s.add_parser("list", help="nodes: role, status, tunnel address, resources, heartbeat")
    for cmd in ("approve", "revoke"):
        p = s.add_parser(cmd, help="approve a node" if cmd == "approve" else "revoke a node")
        p.add_argument("name")
    sc = s.add_parser("set-classes", help="data classes that may be sent to a node")
    sc.add_argument("name")
    sc.add_argument("classes", nargs="+", help="public internal confidential restricted")


async def run(a: argparse.Namespace) -> int:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sessions = make_sessions(engine)
    journal = Journal(sessions)
    peers = Path(settings.cluster_dir) / "peers.conf"
    try:
        async with sessions() as db:
            owner = await db.scalar(
                select(User).where(User.role == "owner").order_by(User.created_at).limit(1)
            )
            actor = owner.id if owner else None
            if a.node_cmd == "token":
                out = await service.issue_token(
                    db, actor, a.endpoint, a.main_key, a.ca_fingerprint, a.url
                )
                print(
                    f"token valid until {out.expires_at:%Y-%m-%d %H:%M} UTC, single use. "
                    "Join the new server:"
                )
                print(f"  {out.command}")
                print("then, here on the main node: yorath node list; yorath node approve <name>")
            elif a.node_cmd == "set-classes":
                classes = await placements.set_node_classes(
                    db, journal, a.name, a.classes, actor_id=actor
                )
                print(f"{a.name}: {', '.join(classes)}")
            elif a.node_cmd == "list":
                print(
                    f"{'NODE':16} {'ROLE':7} {'STATUS':8} {'TUNNEL':11} {'CPU/RAM/GPU':14} "
                    "HEARTBEAT"
                )
                for n in await service.list_nodes(db):
                    seen = f"{n.last_seen_at:%H:%M:%S}" if n.last_seen_at else "-"
                    res = f"{n.cpu_count}/{n.ram_gb}G/{n.gpu_count}"
                    ip = n.overlay_ip or "-"
                    print(f"{n.name:16} {n.role:7} {n.status:8} {ip:11} {res:14} {seen}")
            else:
                fn = service.approve if a.node_cmd == "approve" else service.revoke
                n = await fn(db, journal, actor, a.name, peers)
                print(f"{n.name}: {n.status}")
        return 0
    except (service.ClusterError, placements.PlacementError) as exc:
        print(f"error ({exc.code}): {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()
