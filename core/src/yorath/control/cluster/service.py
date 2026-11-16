"""Cluster nodes: join tokens, joining, approval, revocation, limit, heartbeat.

The main node is the only entry point and PEP: a node gets only its own identity (node token) and
an address in the WireGuard tunnel; it has no database, journal or other nodes' secrets. Revocation
is status ``revoked``: the node token is rejected and the peer is removed from ``peers.conf``
(applied by ``yorath node sync`` on the host).
"""

import asyncio
import hashlib
import ipaddress
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yorath.control.cluster.placements import ensure_servers, failover_default
from yorath.control.journal import AuditEventType, Journal
from yorath.control.model import (
    AgentRevision,
    Cluster,
    ClusterJoinToken,
    Deployment,
    Instance,
    InstanceStatus,
    Node,
    get_setting,
    set_setting,
)

__all__ = ["get_setting"]

log = logging.getLogger(__name__)

OVERLAY = ipaddress.ip_network("10.88.0.0/24")
MAIN_IP = "10.88.0.1"
TOKEN_TTL = timedelta(hours=24)
LOST_AFTER = timedelta(seconds=60)
COMMUNITY_UNITS = 3
SETTINGS_KEY = "cluster_main"  # endpoint, main node public key, root CA fingerprint
JOIN_PREFIX = "yorath_join_"
NODE_PREFIX = "yorath_node_"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def now() -> datetime:
    return datetime.now(UTC)


class ClusterError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


class JoinIn(BaseModel):
    token: str = Field(max_length=200)
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,62}$", max_length=63)
    wg_public_key: str = Field(pattern=r"^[A-Za-z0-9+/]{42,43}=$")
    cpu_count: int = Field(ge=1, le=4096)
    ram_gb: int = Field(ge=0, le=65536)
    gpu_count: int = Field(default=0, ge=0, le=64)
    gpu: str | None = Field(default=None, max_length=100)
    vram_gb: int | None = Field(default=None, ge=0, le=4096)  # memory of one card
    has_model: bool = False


class JoinOut(BaseModel):
    node_id: uuid.UUID
    overlay_ip: str
    main_overlay_ip: str = MAIN_IP
    main_public_key: str
    endpoint: str
    node_token: str  # the only time the value leaves the main node
    status: str


class NodeOut(BaseModel):
    id: uuid.UUID
    name: str
    role: str
    status: str
    overlay_ip: str | None
    cpu_count: int
    ram_gb: int
    gpu: str | None
    gpu_count: int
    has_model: bool
    last_seen_at: datetime | None
    units: int
    vram_gb: int | None = None
    allowed_data_classes: list[str] = []


@dataclass
class TokenOut:
    token: str
    expires_at: datetime
    command: str


def units(node: Node) -> int:
    """Community units: a node is 1, a node with GPUs is the number of GPUs (decision 35)."""
    return max(1, node.gpu_count or 0)


def node_out(n: Node) -> NodeOut:
    return NodeOut(
        id=n.id, name=n.name, role=n.role, status=n.status, overlay_ip=n.overlay_ip,
        cpu_count=n.cpu_count, ram_gb=n.ram_gb, gpu=n.gpu, gpu_count=n.gpu_count,
        has_model=n.has_model, last_seen_at=n.last_seen_at, units=units(n),
        vram_gb=n.vram_gb, allowed_data_classes=list(n.allowed_data_classes or []),
    )  # fmt: skip


async def list_nodes(session: AsyncSession) -> list[NodeOut]:
    rows = await session.scalars(select(Node).order_by(Node.role, Node.created_at))
    return [node_out(n) for n in rows.all()]


async def issue_token(
    session: AsyncSession, actor: uuid.UUID | None, endpoint: str, main_key: str, ca_fp: str,
    main_url: str,
) -> TokenOut:  # fmt: skip
    """One-time join token; the main node parameters are stored for the reply to the node.

    Format ``yorath_join_<random>.<sha256 of the main node root CA | ->``: the node checks the main
    node certificate against the fingerprint before the first request.
    """
    await set_setting(
        session, SETTINGS_KEY, {"endpoint": endpoint, "public_key": main_key, "ca": ca_fp}
    )
    raw = JOIN_PREFIX + secrets.token_urlsafe(24)
    token = f"{raw}.{ca_fp}"
    expires = now() + TOKEN_TTL
    session.add(ClusterJoinToken(token_hash=_hash(raw), created_by=actor, expires_at=expires))
    await session.commit()
    # absolute path: the command works from any directory once the sources are in /opt/yorath
    url = main_url or "https://<main node address>"
    command = f"sh /opt/yorath/installer/install.sh --join {url} {token}"
    return TokenOut(token=token, expires_at=expires, command=command)


async def _next_ip(session: AsyncSession) -> str:
    taken = set((await session.scalars(select(Node.overlay_ip))).all())
    for host in OVERLAY.hosts():
        ip = str(host)
        if ip != MAIN_IP and ip not in taken:
            return ip
    raise ClusterError(409, "overlay_full", "no free addresses left in the cluster tunnel")


async def join(session: AsyncSession, journal: Journal | None, data: JoinIn) -> JoinOut:
    """Join by token: the node is ``pending`` until the owner approves it.

    The token is claimed by one conditional update, so of two simultaneous joins with one token only
    one passes; a refusal after the claim rolls it back and the token stays free.
    """
    try:
        return await _join(session, journal, data)
    except ClusterError:
        await session.rollback()
        raise


async def _claim(session: AsyncSession, token: str) -> ClusterJoinToken:
    h = _hash(token.rsplit(".", 1)[0])
    claimed = await session.scalar(
        update(ClusterJoinToken)
        .where(
            ClusterJoinToken.token_hash == h,
            ClusterJoinToken.used_at.is_(None),
            ClusterJoinToken.expires_at >= now(),
        )
        .values(used_at=now())
        .returning(ClusterJoinToken)
    )
    if claimed is not None:
        return claimed
    row = await session.scalar(select(ClusterJoinToken).where(ClusterJoinToken.token_hash == h))
    if row is None:
        raise ClusterError(403, "join_token_invalid", "join token not found")
    if row.used_at is not None:
        raise ClusterError(403, "join_token_used", "join token already used")
    raise ClusterError(403, "join_token_expired", "the token has expired: yorath node token")


async def _join(session: AsyncSession, journal: Journal | None, data: JoinIn) -> JoinOut:
    row = await _claim(session, data.token)
    main = await get_setting(session, SETTINGS_KEY, None)
    if not main:
        raise ClusterError(
            409, "cluster_not_ready", "the main node has not set up the tunnel: yorath node token"
        )
    if await session.scalar(select(Node).where(Node.name == data.name)):
        raise ClusterError(
            409, "node_exists", f"node {data.name} already exists: set YORATH_NODE_NAME"
        )
    alive = (await session.scalars(select(Node).where(Node.status != "revoked"))).all()
    used = sum(units(n) for n in alive) + max(1, data.gpu_count)
    if used > COMMUNITY_UNITS:
        raise ClusterError(
            403, "community_limit",
            f"Community allows up to {COMMUNITY_UNITS} units (node = 1, node with GPUs = number "
            f"of GPUs, the main node counts); with this node it would be {used}. "
            "More needs Enterprise",
        )  # fmt: skip
    cluster = await session.scalar(select(Cluster).order_by(Cluster.created_at).limit(1))
    if cluster is None:
        raise ClusterError(409, "cluster_not_ready", "the installation cluster is not created")
    node_token = NODE_PREFIX + secrets.token_urlsafe(32)
    node = Node(
        cluster_id=cluster.id, name=data.name, cpu_count=data.cpu_count, ram_gb=data.ram_gb,
        gpu=data.gpu, gpu_count=data.gpu_count, vram_gb=data.vram_gb, role="worker",
        status="pending",
        overlay_ip=await _next_ip(session), wg_public_key=data.wg_public_key,
        token_hash=_hash(node_token), has_model=data.has_model, updated_at=now(),
    )  # fmt: skip
    session.add(node)
    await session.flush()
    await ensure_servers(session, node)  # one model server per card (decision 44)
    row.node_id = node.id
    await session.commit()
    if journal is not None:
        await journal.audit(
            AuditEventType.NODE_JOINED,
            details={"node": node.name, "overlay_ip": node.overlay_ip, "cpu": node.cpu_count,
                     "ram_gb": node.ram_gb, "gpu_count": node.gpu_count},
        )  # fmt: skip
    assert node.overlay_ip is not None
    return JoinOut(
        node_id=node.id, overlay_ip=node.overlay_ip, main_public_key=main["public_key"],
        endpoint=main["endpoint"], node_token=node_token, status=node.status,
    )  # fmt: skip


async def _named(session: AsyncSession, name: str) -> Node:
    node = await session.scalar(select(Node).where(Node.name == name))
    if node is None or node.role != "worker":
        raise ClusterError(404, "node_not_found", f"worker node {name} does not exist")
    return node


async def approve(
    session: AsyncSession, journal: Journal | None, actor: uuid.UUID | None, name: str,
    peers_file: Path | None,
) -> NodeOut:  # fmt: skip
    node = await _named(session, name)
    if node.status == "revoked":
        raise ClusterError(
            409, "node_revoked", "the node is revoked: join it again with a new token"
        )
    node.status = "active"
    await session.commit()
    await write_peers(session, peers_file)
    if journal is not None:
        await journal.audit(
            AuditEventType.NODE_APPROVED, actor_principal_id=actor, details={"node": name}
        )
    return node_out(node)


async def revoke(
    session: AsyncSession, journal: Journal | None, actor: uuid.UUID | None, name: str,
    peers_file: Path | None,
) -> NodeOut:  # fmt: skip
    """Revoke: node token rejected, peer removed, node Instances lost (Deployment Degraded)."""
    node = await _named(session, name)
    node.status = "revoked"
    await _lose_instances(session, node)
    await session.commit()
    await write_peers(session, peers_file)
    if journal is not None:
        await journal.audit(
            AuditEventType.NODE_REVOKED, actor_principal_id=actor, details={"node": name}
        )
    return node_out(node)


async def write_peers(session: AsyncSession, path: Path | None) -> None:
    """``peers.conf`` for ``wg syncconf`` on the main node: only active and lost nodes.

    A ``lost`` node stays a peer: it comes back by itself when the network recovers.
    """
    if path is None:
        return
    nodes = (
        await session.scalars(
            select(Node).where(Node.role == "worker", Node.status.in_(("active", "lost")))
            .order_by(Node.overlay_ip)
        )
    ).all()  # fmt: skip
    text = "".join(
        f"\n# {n.name}\n[Peer]\nPublicKey = {n.wg_public_key}\nAllowedIPs = {n.overlay_ip}/32\n"
        for n in nodes
        if n.wg_public_key and n.overlay_ip
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)
    except OSError:
        log.exception("%s not written: tunnel peers not updated", path)


async def authenticate(session: AsyncSession, token: str) -> Node | None:
    """Node by its token; ``pending`` and ``revoked`` are separate errors at the caller."""
    if not token.startswith(NODE_PREFIX):
        return None
    return await session.scalar(select(Node).where(Node.token_hash == _hash(token)))


async def main_node(session: AsyncSession) -> Node | None:
    return await session.scalar(
        select(Node).where(Node.role == "main").order_by(Node.created_at).limit(1)
    )


class NodeHardware(BaseModel):
    """What the node service sees on its host; sent with every report."""

    gpu_count: int = Field(ge=0, le=64)
    gpu: str | None = Field(default=None, max_length=100)
    vram_gb: int | None = Field(default=None, ge=0, le=4096)
    has_model: bool = False


async def heartbeat(
    session: AsyncSession, journal: Journal | None, node: Node,
    hardware: NodeHardware | None = None,
) -> None:  # fmt: skip
    """Node service report is the heartbeat; a lost node comes back.

    The report repeats the node's cards: a node joined before model servers existed, or one that
    got a GPU later, gets its model servers here. Cards never disappear from a report silently:
    a server is only added.
    """
    back = node.status == "lost"
    node.last_seen_at = now()
    over: dict[str, Any] | None = None
    if hardware is not None and node.role == "worker":
        if hardware.gpu_count > (node.gpu_count or 0):
            # more cards count as more units: the Community limit holds here as at join (S6)
            others = (
                await session.scalars(
                    select(Node).where(Node.status != "revoked", Node.id != node.id)
                )
            ).all()
            used = sum(units(n) for n in others) + max(1, hardware.gpu_count)
            if used > COMMUNITY_UNITS:
                over = {"node": node.name, "gpu_count": hardware.gpu_count,
                        "kept": node.gpu_count or 0, "units": used,
                        "limit": COMMUNITY_UNITS}  # fmt: skip
            else:
                node.gpu_count = hardware.gpu_count
        node.gpu = hardware.gpu or node.gpu
        node.vram_gb = hardware.vram_gb or node.vram_gb
        node.has_model = node.has_model or hardware.has_model
        await ensure_servers(session, node)
    if back:
        node.status = "active"
    await session.commit()
    if back and journal is not None:
        await journal.audit(AuditEventType.NODE_BACK, details={"node": node.name})
    if over is not None:
        log.warning("node %s reports %s GPUs: over the Community limit", node.name,
                    over["gpu_count"])  # fmt: skip
        if journal is not None:
            await journal.audit(AuditEventType.NODE_OVER_LIMIT, details=over)


async def _lose_instances(session: AsyncSession, node: Node) -> None:
    await session.execute(
        update(Instance)
        .where(Instance.node_id == node.id, Instance.status != InstanceStatus.STOPPED)
        .values(status=InstanceStatus.LOST, status_detail=f"node {node.name} is unreachable")
    )


async def sweep_lost(session: AsyncSession, journal: Journal | None) -> list[str]:
    """A worker node silent for ``LOST_AFTER`` is unreachable; its Instances are ``lost``."""
    stale = (
        await session.scalars(
            select(Node).where(
                Node.role == "worker", Node.status == "active",
                (Node.last_seen_at.is_(None) & (Node.updated_at < now() - LOST_AFTER))
                | (Node.last_seen_at < now() - LOST_AFTER),
            )
        )
    ).all()  # fmt: skip
    for node in stale:
        node.status = "lost"
        await _lose_instances(session, node)
    await session.commit()
    if journal is not None:
        for node in stale:
            await journal.audit(AuditEventType.NODE_LOST, details={"node": node.name})
    return [n.name for n in stale]


async def heartbeat_loop(
    sessions: async_sessionmaker[AsyncSession], journal: Journal, interval_s: float = 15
) -> None:
    while True:
        try:
            async with sessions() as s:
                await sweep_lost(s, journal)
                await failover_default(s, journal)  # change field-fixes-0-7
        except Exception:  # noqa: BLE001
            log.exception("node heartbeat check failed")
        await asyncio.sleep(interval_s)


async def deployment_node(session: AsyncSession, deployment: Deployment) -> Node | None:
    return await session.get(Node, deployment.node_id) if deployment.node_id else None


async def agent_deployments(session: AsyncSession, node: Node) -> dict[Any, Any]:
    """Deployments of connected agents on this node (for debugging and the Nodes screen)."""
    rows = await session.execute(
        select(Deployment.id, AgentRevision.agent_id)
        .join(AgentRevision, AgentRevision.id == Deployment.agent_revision_id)
        .where(Deployment.node_id == node.id)
    )
    return {d: a for d, a in rows.all()}
