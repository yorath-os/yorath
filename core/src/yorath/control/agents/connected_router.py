"""Connected agents API (change connected-agents): the owner panel and the node service."""

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from yorath.control.agents import connected
from yorath.control.cluster import service as cluster
from yorath.control.identity.deps import Db, JournalDep, Owner
from yorath.control.journal.egress import ingest_lines, read_report
from yorath.control.model import Node
from yorath.runtime.connected.limits import EGRESS_LINES

router = APIRouter(prefix="/agents/connected", tags=["agents"])
node_router = APIRouter(prefix="/node", tags=["node"])


def _raise(exc: connected.ConnectedError) -> HTTPException:
    return HTTPException(exc.status, detail={"code": exc.code, "message": str(exc)})


def _ctx(request: Request):
    state = request.app.state
    return state.secrets, state.manifest


@router.get("", response_model=list[connected.ConnectedOut])
async def list_connected(_: Owner, db: Db, request: Request):
    return await connected.catalog(db, request.app.state.manifest)


async def _one(db: Db, request: Request, name: str) -> connected.ConnectedOut:
    agents = await connected.catalog(db, request.app.state.manifest)
    return next(a for a in agents if a.name == name)


@router.post("/{name}", response_model=connected.ConnectedOut)
async def connect_agent(
    name: str, data: connected.ConnectIn, owner: Owner, db: Db, journal: JournalDep,
    request: Request,
):  # fmt: skip
    store, manifest = _ctx(request)
    report = read_report(request.app.state.settings.host_report_dir)
    try:
        await connected.connect(db, journal, store, manifest, report, owner, name, data)
    except connected.ConnectedError as exc:
        raise _raise(exc) from exc
    return await _one(db, request, name)


@router.delete("/{name}", response_model=connected.ConnectedOut)
async def disconnect_agent(
    name: str, owner: Owner, db: Db, journal: JournalDep, request: Request, purge: bool = False
):
    store, manifest = _ctx(request)
    try:
        await connected.disconnect(db, journal, store, manifest, owner, name, purge=purge)
    except connected.ConnectedError as exc:
        raise _raise(exc) from exc
    return await _one(db, request, name)


class NetworkIn(connected.BaseModel):
    enabled: bool


@router.put("/{name}/network", response_model=connected.ConnectedOut)
async def agent_network(
    name: str, data: NetworkIn, owner: Owner, db: Db, journal: JournalDep, request: Request
):
    try:
        await connected.set_network(
            db, journal, request.app.state.manifest, owner, name, data.enabled
        )
    except connected.ConnectedError as exc:
        raise _raise(exc) from exc
    return await _one(db, request, name)


@router.put("/{name}/channel", response_model=connected.ConnectedOut)
async def agent_channel(
    name: str, data: connected.ChannelIn, owner: Owner, db: Db, journal: JournalDep,
    request: Request,
):  # fmt: skip
    store, manifest = _ctx(request)
    try:
        await connected.set_channel(db, journal, store, manifest, owner, name, data)
    except connected.ConnectedError as exc:
        raise _raise(exc) from exc
    return await _one(db, request, name)


@router.post("/{name}/telegram/check", response_model=connected.ConnectedOut)
async def agent_telegram_check(name: str, owner: Owner, db: Db, request: Request):
    """Ask the agent's node to check the bot again; the result comes with its next report."""
    try:
        await connected.check_telegram(db, request.app.state.manifest, name)
    except connected.ConnectedError as exc:
        raise _raise(exc) from exc
    return await _one(db, request, name)


class VersionIn(connected.BaseModel):
    version: str | None = connected.Field(
        default=None,
        max_length=100,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="None means the verified one",
    )


@router.post("/{name}/restart", response_model=connected.ConnectedOut)
async def agent_restart(name: str, owner: Owner, db: Db, journal: JournalDep, request: Request):
    try:
        await connected.restart(db, journal, request.app.state.manifest, owner, name)
    except connected.ConnectedError as exc:
        raise _raise(exc) from exc
    return await _one(db, request, name)


@router.put("/{name}/version", response_model=connected.ConnectedOut)
async def agent_version(
    name: str, data: VersionIn, owner: Owner, db: Db, journal: JournalDep, request: Request
):
    try:
        await connected.set_version(
            db, journal, request.app.state.manifest, owner, name, data.version
        )
    except connected.ConnectedError as exc:
        raise _raise(exc) from exc
    return await _one(db, request, name)


# ---- node service: node token, not a human session --------------------------------------------


async def _node(request: Request, db: Db) -> Node:
    """Node service: the main node by ``YORATH_NODE_TOKEN``, a worker node by its own node token."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, detail="invalid_node_token")
    expected = request.app.state.settings.node_token
    if expected and hmac.compare_digest(token.encode(), expected.encode()):
        node = await cluster.main_node(db)
        if node is None:
            raise HTTPException(503, detail="main_node_missing")  # yorath inventory has not run
        return node
    node = await cluster.authenticate(db, token)
    if node is None or node.status == "revoked":
        raise HTTPException(401, detail="invalid_node_token")
    if node.status == "pending":
        raise HTTPException(403, detail="node_pending")
    return node


NodeAuth = Annotated[Node, Depends(_node)]


@node_router.get("/desired")
async def node_desired(node: NodeAuth, db: Db, request: Request):
    store, manifest = _ctx(request)
    return await connected.desired_state(db, store, manifest, node)


class EgressLines(connected.BaseModel):
    # the node service sends at most this many per request (limits.py): more is not a node of ours
    lines: list[str] = connected.Field(default=[], max_length=EGRESS_LINES)


@node_router.post("/egress", status_code=204)
async def node_egress(data: EgressLines, node: NodeAuth, db: Db, journal: JournalDep,
                      request: Request):  # fmt: skip
    """Worker node proxy log (cluster-nodes): internet from the node, journal on the main node."""
    names = connected.connected_addresses(request.app.state.manifest)
    await ingest_lines(db, journal, data.lines, names, node.name)


@node_router.post("/report", status_code=204)
async def node_report(data: connected.NodeReport, node: NodeAuth, db: Db, journal: JournalDep):
    await cluster.heartbeat(db, journal, node, data.hardware)
    await connected.apply_report(db, data, node)
