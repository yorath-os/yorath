"""Cluster API (change cluster-nodes): tokens and nodes for the owner, joining by token."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from yorath.control.cluster import placements, service
from yorath.control.identity.deps import Db, JournalDep, Owner
from yorath.control.journal.egress import read_report
from yorath.control.model import Node
from yorath.control.setup.hardware import detect

log = logging.getLogger(__name__)

router = APIRouter(prefix="/cluster", tags=["cluster"])


def _raise(exc: service.ClusterError | placements.PlacementError) -> HTTPException:
    return HTTPException(exc.status, detail={"code": exc.code, "message": str(exc)})


def peers_file(request: Request) -> Path | None:
    return request.app.state.settings.cluster_dir / "peers.conf"


@router.get("/nodes", response_model=list[service.NodeOut])
async def nodes(_: Owner, db: Db):
    return await service.list_nodes(db)


class TokenOut(BaseModel):
    token: str
    expires_at: str
    command: str


@router.post("/tokens", response_model=TokenOut, status_code=201)
async def token(owner: Owner, db: Db, request: Request):
    """Token from the panel: the main node tunnel must already be up (``yorath node token``)."""
    main = await service.get_setting(db, service.SETTINGS_KEY, None)
    if not main:
        raise HTTPException(
            409, detail={"code": "cluster_not_ready",
                         "message": "first, once on the main node server: yorath node token"},
        )  # fmt: skip
    url = str(request.base_url).rstrip("/").removesuffix("/api")
    out = await service.issue_token(
        db, owner.id, main["endpoint"], main["public_key"], main["ca"], url
    )
    return TokenOut(token=out.token, expires_at=out.expires_at.isoformat(), command=out.command)


@router.post("/join", response_model=service.JoinOut)
async def join(data: service.JoinIn, db: Db, journal: JournalDep):
    try:
        return await service.join(db, journal, data)
    except service.ClusterError as exc:
        raise _raise(exc) from exc


class Action(BaseModel):
    action: str = Field(pattern="^(approve|revoke)$")


@router.post("/nodes/{name}/{action}", response_model=service.NodeOut)
async def node_action(name: str, action: str, owner: Owner, db: Db, journal: JournalDep,
                      request: Request):  # fmt: skip
    fn = {"approve": service.approve, "revoke": service.revoke}.get(action)
    if fn is None:
        raise HTTPException(404, detail="unknown_action")
    try:
        return await fn(db, journal, owner.id, name, peers_file(request))
    except service.ClusterError as exc:
        raise _raise(exc) from exc


# --- model catalog and placements (change model-placements) ---------------------------------


class OutsideNode(BaseModel):
    """Agents outside Yorath on one node, from its last "yorath scan" (decision 36)."""

    node: str
    main: bool
    node_status: str
    checked_at: str | None
    agents: list[dict[str, Any]]
    traces: list[dict[str, Any]]
    truncated: bool = False  # the scan found more than a report carries (R3)
    # where an agent on this node reaches the Yorath model: a worker node through the cluster tunnel
    # (no certificate, no internet); None for the main node, whose panel address is the one to use
    model_url: str | None


@router.get("/outside-agents")
async def outside_agents(_: Owner, db: Db, request: Request) -> dict[str, list[OutsideNode]]:
    """Every node's agents outside Yorath: the main node's from its report file, a worker
    node's from the report it sends with its heartbeat (change outside-agents-cluster)."""
    out = []
    for node in (
        await db.scalars(
            select(Node).where(Node.status != "revoked").order_by(Node.role, Node.name)
        )
    ).all():
        main = node.role == "main"
        report = (
            read_report(request.app.state.settings.host_report_dir) if main else node.host_report
        )
        report = report or {}
        out.append(
            OutsideNode(
                node=node.name, main=main, node_status=node.status,
                checked_at=report.get("checked_at"),
                agents=report.get("agents", []), traces=report.get("traces", []),
                truncated=bool(report.get("truncated")),
                model_url=None if main else f"http://{service.MAIN_IP}:8000/v1",
            )
        )  # fmt: skip
    return {"nodes": out}


class ModelsOut(BaseModel):
    models: list[placements.CatalogModel]
    servers: list[placements.ServerOut]


@router.get("/models", response_model=ModelsOut)
async def models(_: Owner, db: Db, request: Request):
    """The catalog with placements, plus what only the running core knows: how many calls wait
    for each model and how many run on each server."""
    catalog = await placements.catalog(db)
    servers = await placements.servers(db, request.app.state.manifest)
    slots = getattr(request.app.state, "model_slots", None)
    if slots is not None:
        waiting, flying = slots.waiting(), slots.in_flight()
        for model in catalog:
            model.queued = waiting.get(model.id, 0)
        for server in servers:
            server.in_flight = flying.get(server.id, 0)
    return ModelsOut(models=catalog, servers=servers)


class PlaceIn(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    node: str | None = Field(default=None, max_length=100)
    gpu: int | None = Field(default=None, ge=0, le=63)
    force: bool = False
    replace: bool = False
    move: bool = False  # to a server with another window, without downtime (R2)


@router.post("/models/place")
async def place(data: PlaceIn, owner: Owner, db: Db, request: Request) -> StreamingResponse:
    """NDJSON stream of Ollama progress, the last line is ``placed`` or ``error``.

    The rules are checked before the stream starts, so a refusal is an HTTP error with the
    reason; the pull itself does not depend on the client staying connected.
    """
    state = request.app.state
    main = await service.main_node(db)
    if data.node is None or (main is not None and data.node == main.name):
        # the main node's cards as seen now: a GPU added after the wizard counts at once (R6)
        await placements.sync_main_hardware(db, detect(state.settings))
        await db.commit()
    try:
        await placements.plan(
            db, state.manifest, data.model, node_name=data.node, gpu=data.gpu, force=data.force,
            replace=data.replace, move=data.move,
        )  # fmt: skip
    except placements.PlacementError as exc:
        raise _raise(exc) from exc
    actor = owner.id  # read now: the request session is gone when the background task runs
    events: asyncio.Queue[bytes | None] = asyncio.Queue()

    def put(event: dict[str, Any]) -> None:
        events.put_nowait((json.dumps(event, ensure_ascii=False) + "\n").encode())

    async def run() -> None:
        try:
            async for event in placements.place(
                state.http, str(state.settings.ollama_url), state.sessions, state.journal,
                state.manifest, data.model, node_name=data.node, gpu=data.gpu, force=data.force,
                replace=data.replace, move=data.move, actor_id=actor, source="web",
            ):  # fmt: skip
                put(event)
        except placements.PlacementError as exc:
            put({"error": str(exc), "code": exc.code})
        except httpx.HTTPError as exc:
            put({"error": f"ollama: {exc}"})
        except Exception as exc:  # noqa: BLE001 — background task: the error shows in the stream and log
            log.exception("model placement %s", data.model)
            put({"error": f"{type(exc).__name__}: {exc}"})
        finally:
            events.put_nowait(None)

    task = asyncio.create_task(run())
    pulls: set[asyncio.Task[None]] | None = getattr(state, "pulls", None)
    if pulls is None:
        pulls = state.pulls = set()
    pulls.add(task)  # strong reference while the task runs
    task.add_done_callback(pulls.discard)

    async def stream() -> AsyncIterator[bytes]:
        while (line := await events.get()) is not None:
            yield line

    return StreamingResponse(stream(), media_type="application/x-ndjson")


class RemoveIn(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    node: str | None = Field(default=None, max_length=100)
    gpu: int | None = Field(default=None, ge=0, le=63)
    force: bool = False


@router.post("/models/remove")
async def remove(data: RemoveIn, owner: Owner, db: Db, journal: JournalDep, request: Request):
    state = request.app.state
    try:
        removed = await placements.remove(
            db, state.http, str(state.settings.ollama_url), journal, data.model,
            node_name=data.node, gpu=data.gpu, force=data.force, actor_id=owner.id, source="web",
        )  # fmt: skip
    except placements.PlacementError as exc:
        raise _raise(exc) from exc
    return {"removed": removed}


class DefaultIn(BaseModel):
    model: str = Field(min_length=1, max_length=200)


@router.post("/models/default")
async def default(data: DefaultIn, owner: Owner, db: Db, journal: JournalDep):
    try:
        target = await placements.set_default(
            db, journal, data.model, actor_id=owner.id, source="web"
        )
    except placements.PlacementError as exc:
        raise _raise(exc) from exc
    return {"name": target.name, "model_id": target.model_id}


class ClassesIn(BaseModel):
    classes: list[str] = Field(min_length=1, max_length=4)


@router.put("/nodes/{name}/classes")
async def node_classes(name: str, data: ClassesIn, owner: Owner, db: Db, journal: JournalDep):
    try:
        classes = await placements.set_node_classes(
            db, journal, name, data.classes, actor_id=owner.id
        )
    except placements.PlacementError as exc:
        raise _raise(exc) from exc
    return {"node": name, "classes": classes}
