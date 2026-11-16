"""Model catalog, model servers and placements (decision 44, change model-placements).

A local chat model is a catalog entry ``local/<Ollama id>``; a *model server* is one Ollama on a
node with the GPUs pinned to it; a *placement* is a model on a model server. Placements are the
desired state and live only on the main node: the core pulls the model into the node's Ollama
through the tunnel. ``local/default`` is a pointer at one catalog model.

Rules checked before anything is pulled: the node allows every data class of the model, the
server gives the model's context window, the model fits the server's memory, the server is free.
"""

import logging
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.journal import AuditEventType, Journal
from yorath.control.model import (
    DataClass,
    ModelPlacement,
    ModelProvider,
    ModelResource,
    ModelServer,
    Node,
    PlacementStatus,
    get_setting,
    set_setting,
)
from yorath.control.setup.budget import fits, footprint
from yorath.control.setup.hardware import HardwareInfo, local_window
from yorath.control.setup.ollama import LOCAL_CHAT, LOCAL_EMBEDDINGS, OllamaClient
from yorath.db import utcnow
from yorath.distribution.manifest import Manifest

log = logging.getLogger(__name__)

NODE_GATEWAY_PORT = 8090  # worker node gateway in the tunnel (installer/Caddyfile.node)
MAIN_SERVER = "ollama"  # not "default": the node is often named local, and local/default is a model
NO_TOOLS = "no_tools"
CLASS_ORDER = [c.value for c in DataClass]


class PlacementError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


def catalog_name(model_id: str) -> str:
    """``qwen3.5:9b`` → ``local/qwen3.5:9b``; a full catalog name stays as it is."""
    return model_id if model_id.startswith("local/") else f"local/{model_id}"


LOCAL_ANY = "local/*"  # in an agent card: every local chat model of the catalog


def card_allows(models: list[str], name: str) -> bool:
    """Is the model in the agent card's list; ``local/*`` covers every local chat model."""
    if name in models:
        return True
    return LOCAL_ANY in models and name.startswith("local/") and name != LOCAL_EMBEDDINGS


def card_rank(models: list[str], name: str) -> int:
    """Order of a model in the card: listed ones first, in the card's order."""
    return models.index(name) if name in models else len(models)


def server_specs(role: str, gpu_count: int, has_model: bool) -> list[tuple[str, list[int]]]:
    """Model servers a node runs: the main node has one Ollama for all its GPUs, a worker has
    one Ollama per card, a worker without a GPU has one only when asked for at join."""
    if role == "main":
        return [(MAIN_SERVER, list(range(gpu_count)))]
    if gpu_count:
        return [(f"gpu{i}", [i]) for i in range(gpu_count)]
    return [("cpu", [])] if has_model else []


async def ensure_servers(session: AsyncSession, node: Node) -> list[ModelServer]:
    """Creates the node's missing model servers; never deletes one (a placement may sit on it)."""
    rows = (await session.scalars(select(ModelServer).where(ModelServer.node_id == node.id))).all()
    known = {s.name: s for s in rows}
    # old inventory rows know only the GPU name: count one card
    cards = node.gpu_count or int(bool(node.gpu))
    for name, gpus in server_specs(node.role, cards, node.has_model):
        server = known.get(name)
        if server is None:
            server = ModelServer(node_id=node.id, name=name, gpu_indexes=gpus)
            session.add(server)
            known[name] = server
        elif name == MAIN_SERVER and list(server.gpu_indexes) != gpus:
            server.gpu_indexes = gpus  # a GPU was added to the main node
    await session.flush()
    return sorted(known.values(), key=lambda s: s.name)


def server_url(ollama_url: str, node: Node, server: ModelServer) -> str:
    if node.role == "main":
        return str(ollama_url)
    return f"http://{node.overlay_ip}:{NODE_GATEWAY_PORT}/ollama/{server.name}"


def server_hardware(node: Node, server: ModelServer) -> HardwareInfo:
    """What one model server has: a worker's GPU server has one card, not the node's total."""
    has_gpu = bool(server.gpu_indexes)
    return HardwareInfo(
        vcpu=node.cpu_count, ram_gb=node.ram_gb, disk_free_gb=0,
        gpu_name=(node.gpu or "GPU") if has_gpu else None,
        vram_gb=node.vram_gb if has_gpu else None,
        gpu_count=len(server.gpu_indexes),
    )  # fmt: skip


def fits_server(
    model: str, manifest: Manifest, hw: HardwareInfo, *, with_embeddings: bool
) -> tuple[float | None, bool, bool | None]:
    """Model memory (video memory on a GPU, RAM without one), is it an estimate, does it fit.

    ``None`` means unknown: no measured video memory for the model or no card size for the node.
    Embeddings share memory with the chat model only on the main node.
    """
    chat = footprint(model, manifest)
    emb = footprint(manifest.models.embeddings, manifest) if with_embeddings else None
    if hw.gpu_name:
        if chat.vram_gb is None or not hw.vram_gb:
            return chat.vram_gb, True, None
        need = chat.vram_gb + ((emb.vram_gb or 0) if emb else 0)
        return chat.vram_gb, chat.estimated, need <= hw.vram_gb
    if emb is None:
        ok = None if chat.rss_gb is None else chat.rss_gb <= manifest.ollama_limit_gb(hw.ram_gb)
        return chat.rss_gb, chat.estimated, ok
    return chat.rss_gb, chat.estimated, fits(chat, emb, manifest.ollama_limit_gb(hw.ram_gb))


# --- the pointer -----------------------------------------------------------------------------


async def target_of(session: AsyncSession, row: ModelResource) -> ModelResource:
    """The catalog model behind a pointer; any other model is its own target."""
    if row.alias_of is None:
        return row
    target = await session.get(ModelResource, row.alias_of)
    return target or row


def _mirror(alias: ModelResource, target: ModelResource) -> None:
    alias.provider, alias.model_id = target.provider, target.model_id
    alias.context_length = target.context_length
    alias.allowed_data_classes = list(target.allowed_data_classes)
    alias.input_price_per_mtok = target.input_price_per_mtok
    alias.output_price_per_mtok = target.output_price_per_mtok
    alias.is_default, alias.enabled = True, True


async def sync_alias(session: AsyncSession, target: ModelResource) -> None:
    """Pointers mirror the target's model_id, window and classes: their readers need no join."""
    aliases = await session.scalars(
        select(ModelResource).where(ModelResource.alias_of == target.id)
    )
    for alias in aliases.all():
        _mirror(alias, target)


async def default_target(session: AsyncSession) -> ModelResource | None:
    alias = await session.scalar(select(ModelResource).where(ModelResource.name == LOCAL_CHAT))
    if alias is None or alias.alias_of is None:
        return None
    return await session.get(ModelResource, alias.alias_of)


async def ready_count(session: AsyncSession, model: ModelResource) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(ModelPlacement)
            .where(
                ModelPlacement.model_resource_id == model.id,
                ModelPlacement.status == PlacementStatus.READY,
            )
        )
        or 0
    )


async def set_default(
    session: AsyncSession, journal: Journal | None, model: str, *,
    actor_id: uuid.UUID | None, source: str, capabilities: list[str] | None = None,
) -> ModelResource:  # fmt: skip
    """Points ``local/default`` at a catalog model that has a ready placement."""
    target = await session.scalar(
        select(ModelResource).where(ModelResource.name == catalog_name(model))
    )
    if target is None or target.alias_of is not None or target.name == LOCAL_EMBEDDINGS:
        raise PlacementError(404, "model_not_found", f"{catalog_name(model)} is not in the catalog")
    if not await ready_count(session, target):
        raise PlacementError(
            409, "no_ready_placement",
            f"{target.name} has no ready placement: yorath model pull {target.model_id}",
        )  # fmt: skip
    alias = await session.scalar(select(ModelResource).where(ModelResource.name == LOCAL_CHAT))
    previous = alias.model_id if alias else None
    if alias is None:
        alias = ModelResource(
            name=LOCAL_CHAT, provider=ModelProvider.OLLAMA, model_id=target.model_id,
            allowed_data_classes=[],
        )  # fmt: skip
        session.add(alias)
    alias.alias_of = target.id
    _mirror(alias, target)
    await session.commit()
    if source != "failover":  # the panel, the CLI or the wizard: the owner decided
        await note_manual_default(session, target.name)
    if journal is not None:
        await journal.audit(
            AuditEventType.MODEL_REGISTERED,
            actor_principal_id=actor_id,
            model=target.model_id,
            details={
                "name": LOCAL_CHAT, "model_id": target.model_id, "previous": previous,
                "context_length": target.context_length, "source": source,
                "capabilities": capabilities,
            },
        )  # fmt: skip
    return target


# --- failover of the default (change field-fixes-0-7) ----------------------------------------

FAILOVER_AFTER = timedelta(minutes=2)  # the default's servers silent this long: switch it
FAILOVER = "default_failover"  # system setting: the last automatic switch and what became of it


async def _servers_of(
    session: AsyncSession, model: ModelResource
) -> list[tuple[ModelServer, Node]]:
    rows = await session.execute(
        select(ModelServer, Node)
        .join(ModelPlacement, ModelPlacement.model_server_id == ModelServer.id)
        .join(Node, Node.id == ModelServer.node_id)
        .where(
            ModelPlacement.model_resource_id == model.id,
            ModelPlacement.status == PlacementStatus.READY,
        )
    )
    return [(s, n) for s, n in rows.all()]


def _up(node: Node) -> bool:
    return node.role == "main" or node.status == "active"


async def failover_default(session: AsyncSession, journal: Journal | None) -> str | None:
    """When no server of the default's model has answered for ``FAILOVER_AFTER``, point the
    default at an available local model: on a GPU first, then the larger window. External models
    are never chosen: data would leave the server without the owner's decision. The return of
    the node is noted for the owner; the default is not moved back by itself."""
    target = await default_target(session)
    if target is None:
        return None
    state = await get_setting(session, FAILOVER) or None
    servers = await _servers_of(session, target)
    if any(_up(n) for _, n in servers):
        if state and not state.get("closed") and state.get("to") == target.name:
            origin = await session.scalar(
                select(ModelResource).where(ModelResource.name == state["from"])
            )
            back = origin is not None and any(_up(n) for _, n in await _servers_of(session, origin))
            if back and not state.get("node_back"):
                await set_setting(session, FAILOVER, {**state, "node_back": True})
        return None
    seen = [at for _, n in servers if (at := n.last_seen_at or n.updated_at) is not None]
    if seen and max(seen) > utcnow() - FAILOVER_AFTER:
        return None  # the node may be back in a moment
    candidates = (
        await session.scalars(
            select(ModelResource).where(
                ModelResource.alias_of.is_(None),
                ModelResource.provider == ModelProvider.OLLAMA,
                ModelResource.name != LOCAL_EMBEDDINGS,
                ModelResource.enabled.is_(True),
                ModelResource.id != target.id,
            )
        )
    ).all()
    ranked = []
    for model in candidates:
        up = [s for s, n in await _servers_of(session, model) if _up(n)]
        if up:
            ranked.append((any(s.gpu_indexes for s in up), model.context_length, model.name, model))
    if not ranked:
        if not state or state.get("to") != target.name or state.get("closed"):
            await set_setting(session, FAILOVER, {
                "from": target.name, "to": None, "node": _node_name(servers),
                "at": utcnow().isoformat(timespec="seconds"), "closed": False,
            })  # fmt: skip
        return None
    ranked.sort(key=lambda r: (r[0], r[1], r[2]), reverse=True)
    new = ranked[0][3]
    await set_default(session, None, new.name, actor_id=None, source="failover")
    node = _node_name(servers)
    await set_setting(session, FAILOVER, {
        "from": target.name, "to": new.name, "node": node,
        "at": utcnow().isoformat(timespec="seconds"), "node_back": False, "closed": False,
        "superseded": False,
    })  # fmt: skip
    log.warning("default model %s unavailable (node %s): switched to %s", target.name, node,
                new.name)  # fmt: skip
    if journal is not None:
        await journal.audit(
            AuditEventType.MODEL_DEFAULT_SWITCHED,
            model=new.model_id,
            details={"from": target.name, "to": new.name, "node": node, "reason": "node_lost"},
        )
    return new.name


def _node_name(servers: list[tuple[ModelServer, Node]]) -> str | None:
    return servers[0][1].name if servers else None


async def note_manual_default(session: AsyncSession, model: str) -> None:
    """The owner set the default after a switch: returning it closes the warning, another model
    leaves only the node's return to report (no button to return)."""
    state = await get_setting(session, FAILOVER)
    if not state or state.get("closed"):
        return
    if catalog_name(model) == state.get("from"):
        state = {**state, "closed": True}
    else:
        state = {**state, "superseded": True}
    await set_setting(session, FAILOVER, state)


class DefaultWarning(BaseModel):
    """An automatic switch of the default the owner hasn't closed (change field-fixes-0-7)."""

    switched_from: str
    switched_to: str | None  # None: no model was available, the pointer stayed
    node: str | None
    at: str
    node_back: bool
    can_return: bool  # the node is back and the owner hasn't chosen another default since
    short_window: list[str]  # connected agents on the default that the new window doesn't fit
    model_unavailable: list[str]  # agents with their own model that has no server up


async def default_warning(
    session: AsyncSession, manifest: Manifest, chosen: dict[str, str]
) -> DefaultWarning | None:
    state = await get_setting(session, FAILOVER)
    if not state or state.get("closed"):
        return None
    new = (
        await session.scalar(select(ModelResource).where(ModelResource.name == state["to"]))
        if state.get("to")
        else None
    )
    short = sorted(
        name
        for name, spec in manifest.connected_agents.items()
        if name not in chosen and new is not None and new.context_length < spec.min_context
    )
    lost: list[str] = []
    for agent, model in sorted(chosen.items()):
        row = await session.scalar(select(ModelResource).where(ModelResource.name == model))
        if row is not None and row.provider == ModelProvider.OLLAMA:
            target = await session.get(ModelResource, row.alias_of) if row.alias_of else row
            if target is not None and not any(
                _up(n) for _, n in await _servers_of(session, target)
            ):
                lost.append(agent)
    back = bool(state.get("node_back"))
    return DefaultWarning(
        switched_from=state["from"], switched_to=state.get("to"), node=state.get("node"),
        at=state["at"], node_back=back, can_return=back and not state.get("superseded"),
        short_window=short, model_unavailable=lost,
    )  # fmt: skip


async def dismiss_failover(session: AsyncSession) -> None:
    state = await get_setting(session, FAILOVER)
    if state:
        await set_setting(session, FAILOVER, {**state, "closed": True})


# --- the catalog -----------------------------------------------------------------------------


class PlacementOut(BaseModel):
    node: str
    server: str
    status: str
    detail: str | None
    progress: int
    node_status: str


class CatalogModel(BaseModel):
    id: uuid.UUID
    queued: int = 0  # calls waiting for a free model server (change model-queue)
    name: str
    model_id: str
    context_length: int
    classes: list[str]
    default: bool
    placements: list[PlacementOut]


class ServerModel(BaseModel):
    """A catalog model as seen from one model server (the Models screen picks the server)."""

    name: str
    memory_gb: float | None  # video memory on a GPU, RAM without one
    estimated: bool
    fits: bool | None  # None: no measurement for this kind of server
    measured: str


class ServerOut(BaseModel):
    id: uuid.UUID
    main: bool = False
    window: int | None = None  # context window a model gets on this server
    recommended: str | None = None  # the model the hardware table picks for this server
    catalog: list[ServerModel] = []
    in_flight: int = 0  # calls running on the server right now
    node: str
    server: str
    gpus: list[int]
    gpu: str | None
    vram_gb: int | None
    slots: int
    model: str | None  # catalog name of the chat model on it
    status: str | None
    node_status: str


async def catalog(session: AsyncSession) -> list[CatalogModel]:
    """Local chat models with their placements; the default one first."""
    target = await default_target(session)
    rows = (
        await session.scalars(
            select(ModelResource)
            .where(
                ModelResource.provider == ModelProvider.OLLAMA,
                ModelResource.alias_of.is_(None),
                ModelResource.enabled.is_(True),
                ModelResource.name != LOCAL_EMBEDDINGS,
                ModelResource.name != LOCAL_CHAT,
            )
            .order_by(ModelResource.name)
        )
    ).all()
    placed = (
        await session.execute(
            select(ModelPlacement, ModelServer, Node)
            .join(ModelServer, ModelServer.id == ModelPlacement.model_server_id)
            .join(Node, Node.id == ModelServer.node_id)
            .order_by(Node.role, Node.name, ModelServer.name)
        )
    ).all()
    out = []
    for m in rows:
        out.append(
            CatalogModel(
                id=m.id,
                name=m.name, model_id=m.model_id, context_length=m.context_length,
                classes=list(m.allowed_data_classes),
                default=target is not None and target.id == m.id,
                placements=[
                    PlacementOut(
                        node=n.name, server=s.name, status=p.status.value,
                        detail=p.status_detail, progress=p.progress, node_status=n.status,
                    )
                    for p, s, n in placed
                    if p.model_resource_id == m.id
                ],
            )
        )  # fmt: skip
    return sorted(out, key=lambda c: (not c.default, c.name))


async def servers(session: AsyncSession, manifest: Manifest | None = None) -> list[ServerOut]:
    """Every model server of the cluster and the chat model on it; with the manifest, also what each
    server would give a model: window, recommended model, the catalog with memory estimates."""
    rows = (
        await session.execute(
            select(ModelServer, Node)
            .join(Node, Node.id == ModelServer.node_id)
            .where(Node.status != "revoked")
            .order_by(Node.role, Node.name, ModelServer.name)
        )
    ).all()
    on = {
        p.model_server_id: (p, m)
        for p, m in (
            await session.execute(
                select(ModelPlacement, ModelResource).join(
                    ModelResource, ModelResource.id == ModelPlacement.model_resource_id
                )
            )
        ).all()
    }
    out = []
    for s, n in rows:
        p, m = on.get(s.id, (None, None))
        has_gpu = bool(s.gpu_indexes)
        row = ServerOut(
            id=s.id, main=n.role == "main",
            node=n.name, server=s.name, gpus=list(s.gpu_indexes),
            gpu=n.gpu if has_gpu else None, vram_gb=n.vram_gb if has_gpu else None,
            slots=s.slots, model=m.name if m else None,
            status=p.status.value if p else None, node_status=n.status,
        )  # fmt: skip
        if manifest is not None:
            hw = server_hardware(n, s)
            row.window = local_window(manifest, hw)
            row.recommended = manifest.tier_for(hw.vcpu, hw.ram_gb, hw.vram_gb).model
            row.catalog = [
                _server_model(name, manifest, hw, with_embeddings=row.main)
                for name in manifest.models.catalog
                if name != manifest.models.embeddings
            ]
        out.append(row)
    return out


def _server_model(name: str, manifest: Manifest, hw: HardwareInfo, *, with_embeddings: bool):
    memory, estimated, ok = fits_server(name, manifest, hw, with_embeddings=with_embeddings)
    known = manifest.models.catalog.get(name)
    return ServerModel(
        name=name, memory_gb=memory, estimated=estimated, fits=ok,
        measured=known.measured_on if known else "",
    )  # fmt: skip


async def sync_main_hardware(session: AsyncSession, hw: HardwareInfo) -> Node:
    """The main node's inventory row and its model server follow the hardware seen now."""
    from yorath.control.setup.inventory import upsert_node  # inventory imports this module

    main = await session.scalar(
        select(Node).where(Node.role == "main").order_by(Node.created_at).limit(1)
    )
    return await upsert_node(session, main.name if main else "local", hw)


# --- placing ---------------------------------------------------------------------------------


@dataclass
class Plan:
    """A checked placement: nothing has been pulled or written yet."""

    model_id: str
    name: str
    window: int
    node: Node
    servers: list[ModelServer]
    replaced: list[tuple[ModelPlacement, ModelResource]]  # other models to take off the servers
    skipped: list[str]  # busy servers left alone when no --gpu was given
    new: bool


async def _node(session: AsyncSession, name: str | None) -> Node:
    query = select(Node).where(Node.name == name) if name else select(Node).where(
        Node.role == "main"
    ).order_by(Node.created_at).limit(1)  # fmt: skip
    node = await session.scalar(query)
    if node is None:
        raise PlacementError(404, "node_not_found", f"node {name or 'main'} does not exist")
    return node


async def _occupants(
    session: AsyncSession, server_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[ModelPlacement, ModelResource]]:
    rows = await session.execute(
        select(ModelPlacement, ModelResource)
        .join(ModelResource, ModelResource.id == ModelPlacement.model_resource_id)
        .where(ModelPlacement.model_server_id.in_(server_ids))
    )
    return {p.model_server_id: (p, m) for p, m in rows.all()}


async def plan(
    session: AsyncSession, manifest: Manifest, model_id: str, *,
    node_name: str | None = None, gpu: int | None = None, force: bool = False,
    replace: bool = False, move: bool = False,
) -> Plan:  # fmt: skip
    """Checks a placement in the order of the spec; raises ``PlacementError`` with the reason."""
    model_id = model_id.removeprefix("local/")
    name = catalog_name(model_id)
    if name in (LOCAL_CHAT, LOCAL_EMBEDDINGS):
        raise PlacementError(400, "reserved_name", f"{name} is not a model: name an Ollama model")
    node = await _node(session, node_name)
    if node.status != "active":
        raise PlacementError(
            409, "node_unavailable",
            f"node {node.name} is {node.status}: models are placed on active nodes",
        )  # fmt: skip
    all_servers = await ensure_servers(session, node)
    if gpu is not None:
        all_servers = [s for s in all_servers if s.name == f"gpu{gpu}"]
        if not all_servers:
            raise PlacementError(
                404, "server_not_found", f"node {node.name} has no model server gpu{gpu}"
            )
    if not all_servers:
        raise PlacementError(
            409, "node_has_no_model_server",
            f"node {node.name} runs no model server: it has no GPU and was joined without "
            "YORATH_NODE_MODEL=on",
        )  # fmt: skip

    existing = await session.scalar(select(ModelResource).where(ModelResource.name == name))
    classes = list(existing.allowed_data_classes) if existing else CLASS_ORDER
    missing = [c for c in classes if c not in node.allowed_data_classes]
    if missing:
        raise PlacementError(
            403, "node_class_denied",
            f"node {node.name} does not allow {', '.join(missing)} data, and {name} accepts it: "
            f"yorath node set-classes {node.name} …",
        )  # fmt: skip

    hw = server_hardware(node, all_servers[0])
    window = local_window(manifest, hw)
    if not move and existing is not None and await ready_count(session, existing) and (
        existing.context_length != window
    ):  # fmt: skip
        raise PlacementError(
            409, "window_mismatch",
            f"{name} has a {existing.context_length} context window, and "
            f"{node.name}/{all_servers[0].name} gives {window}: a model has one window on every "
            "server",
        )  # fmt: skip

    _, _, ok = fits_server(model_id, manifest, hw, with_embeddings=node.role == "main")
    if ok is False and not force:
        raise PlacementError(
            409, "does_not_fit",
            f"{model_id} is estimated not to fit {node.name}/{all_servers[0].name} with a "
            f"{window} window: yorath model list; place anyway with --force",
        )  # fmt: skip

    occupied = await _occupants(session, [s.id for s in all_servers])
    chosen, replaced, skipped = [], [], []
    for server in all_servers:
        holder = occupied.get(server.id)
        if holder is None or holder[1].name == name:
            chosen.append(server)
        elif replace:
            chosen.append(server)
            replaced.append(holder)
        elif gpu is None and len(all_servers) > 1:
            skipped.append(f"{server.name} ({holder[1].name})")
        else:
            raise PlacementError(
                409, "server_busy",
                f"{node.name}/{server.name} already serves {holder[1].name}: one chat model per "
                "model server; swap it with --replace",
            )  # fmt: skip
    if not chosen:
        raise PlacementError(
            409, "server_busy",
            f"no free model server on {node.name}: {', '.join(skipped)}; swap with --replace",
        )  # fmt: skip
    return Plan(model_id, name, window, node, chosen, replaced, skipped, existing is None)


async def _drop_placement(
    session: AsyncSession, http: httpx.AsyncClient, ollama_url: str,
    placement: ModelPlacement, model: ModelResource,
) -> tuple[str, str]:  # fmt: skip
    """Removes the placement row and unloads the model from the server's memory."""
    server = await session.get(ModelServer, placement.model_server_id)
    assert server is not None
    node = await session.get(Node, server.node_id)
    assert node is not None
    await session.delete(placement)
    await session.flush()
    if node.status == "active":
        await OllamaClient(http, server_url(ollama_url, node, server)).unload(model.model_id)
    return node.name, server.name


async def _forget_if_unplaced(session: AsyncSession, model: ModelResource) -> bool:
    """A catalog model without placements leaves the catalog unless ``default`` points at it.

    The row stays, disabled: executions that used the model keep their reference to it. A call
    by its name is ``model_not_found``; a new placement brings it back.
    """
    left = await session.scalar(
        select(func.count())
        .select_from(ModelPlacement)
        .where(ModelPlacement.model_resource_id == model.id)
    )
    pointed = await session.scalar(
        select(func.count()).select_from(ModelResource).where(ModelResource.alias_of == model.id)
    )
    if left or pointed:
        return False
    model.enabled = False
    return True


async def place(
    http: httpx.AsyncClient,
    ollama_url: str,
    sessions: Callable[[], AsyncSession],
    journal: Journal | None,
    manifest: Manifest,
    model_id: str,
    *,
    node_name: str | None = None,
    gpu: int | None = None,
    force: bool = False,
    replace: bool = False,
    move: bool = False,
    make_default: bool = False,
    main_hardware: HardwareInfo | None = None,
    actor_id: uuid.UUID | None,
    source: str,
) -> AsyncIterator[dict[str, Any]]:
    """Ollama progress lines, then ``{"status": "placed", ...}``.

    The model is pulled once per node, through the first chosen server: the node's servers share
    the model files. A failed pull leaves the placements ``failed`` with the reason.
    ``main_hardware`` is what the caller sees on the main node right now (the wizard and
    ``yorath model pull``): the inventory row follows it, so a GPU added later counts at once.

    ``move``: the model goes to a server with another context window (pre-public-hardening R2).
    Its ready servers keep answering with the old window while the new one pulls; only when the
    pull succeeds do the window change and the old servers go, in one transaction. A failed pull
    leaves the old servers and the window as they were.
    """
    async with sessions() as s:
        if main_hardware is not None and node_name is None:
            await sync_main_hardware(s, main_hardware)
        p = await plan(
            s, manifest, model_id, node_name=node_name, gpu=gpu, force=force, replace=replace,
            move=move,
        )  # fmt: skip
        model = await s.scalar(select(ModelResource).where(ModelResource.name == p.name))
        # the servers the model leaves on a move: ready elsewhere with another window
        leaving: list[uuid.UUID] = []
        if move and model is not None and model.context_length != p.window:
            leaving = [
                pl.id
                for pl in (
                    await s.scalars(
                        select(ModelPlacement).where(
                            ModelPlacement.model_resource_id == model.id,
                            ModelPlacement.model_server_id.not_in([srv.id for srv in p.servers]),
                        )
                    )
                ).all()
            ]
        if model is None:
            model = ModelResource(
                name=p.name, provider=ModelProvider.OLLAMA, model_id=p.model_id,
                allowed_data_classes=CLASS_ORDER, context_length=p.window,
            )  # fmt: skip
            s.add(model)
            await s.flush()
        if not leaving:  # on a move the old servers keep their window until the new one is ready
            model.context_length = p.window
        model.enabled = True
        for server in p.servers:
            row = await s.scalar(
                select(ModelPlacement).where(
                    ModelPlacement.model_resource_id == model.id,
                    ModelPlacement.model_server_id == server.id,
                )
            )
            if row is None:
                row = ModelPlacement(model_resource_id=model.id, model_server_id=server.id)
                s.add(row)
            row.status, row.status_detail, row.progress = PlacementStatus.PULLING, None, 0
            row.updated_at = utcnow()
        await s.commit()
        model_pk, server_ids = model.id, [srv.id for srv in p.servers]
        base = server_url(ollama_url, p.node, p.servers[0])
        node_label, server_names = p.node.name, [srv.name for srv in p.servers]
    if p.skipped:
        yield {"status": "skipped", "servers": p.skipped}

    async def mark(status: PlacementStatus, detail: str | None, progress: int) -> None:
        async with sessions() as s2:
            rows = await s2.scalars(
                select(ModelPlacement).where(
                    ModelPlacement.model_resource_id == model_pk,
                    ModelPlacement.model_server_id.in_(server_ids),
                )
            )
            for row in rows.all():
                row.status, row.status_detail, row.progress = status, detail, progress
                row.updated_at = utcnow()
            await s2.commit()

    client = OllamaClient(http, base)
    done, last, error = False, 0, None
    try:
        async for event in client.pull(p.model_id):
            if event.get("error"):
                error = str(event["error"])
                break
            done = done or event.get("status") == "success"
            if event.get("total") and event.get("completed"):
                pct = int(100 * event["completed"] / event["total"])
                if pct // 10 != last // 10:
                    last = pct
                    await mark(PlacementStatus.PULLING, None, pct)
            yield event
    except httpx.HTTPError as exc:
        error = f"{type(exc).__name__}: {exc}"
    if error or not done:
        detail = (error or "Ollama did not confirm the pull")[:500]
        await mark(PlacementStatus.FAILED, detail, last)
        yield {"error": detail}
        return

    caps = await client.capabilities(p.model_id)
    moved_from: list[str] = []
    if leaving:
        # one transaction: the new window, the old servers gone, the new ones ready
        async with sessions() as s:
            fresh_model = await s.get(ModelResource, model_pk)
            assert fresh_model is not None
            for pl_id in leaving:
                old_pl = await s.get(ModelPlacement, pl_id)
                if old_pl is not None:
                    n, srv = await _drop_placement(s, http, ollama_url, old_pl, fresh_model)
                    moved_from.append(f"{n}/{srv}")
            fresh_model.context_length = p.window
            for row in (
                await s.scalars(
                    select(ModelPlacement).where(
                        ModelPlacement.model_resource_id == model_pk,
                        ModelPlacement.model_server_id.in_(server_ids),
                    )
                )
            ).all():
                row.status, row.status_detail, row.progress = PlacementStatus.READY, None, 100
                row.updated_at = utcnow()
            await sync_alias(s, fresh_model)
            await s.commit()
        if journal is not None:
            await journal.audit(
                AuditEventType.MODEL_MOVED,
                actor_principal_id=actor_id,
                model=p.model_id,
                details={"name": p.name, "from": moved_from,
                         "to": f"{node_label}/{server_names[0]}",
                         "context_length": p.window, "source": source},
            )  # fmt: skip
    async with sessions() as s:
        replaced_names = []
        for old_placement, old_model in p.replaced:
            placement = await s.get(ModelPlacement, old_placement.id)
            old = await s.get(ModelResource, old_model.id)
            if placement is not None and old is not None:
                await _drop_placement(s, http, ollama_url, placement, old)
                replaced_names.append(old.name)
        await s.commit()
    await mark(PlacementStatus.READY, None, 100)
    async with sessions() as s:
        target = await default_target(s)
        pointed_ready = target is not None and await ready_count(s, target) > 0
        became_default = make_default or not pointed_ready
        fresh = await s.get(ModelResource, model_pk)
        assert fresh is not None
        await sync_alias(s, fresh)
        await s.commit()
    if journal is not None:
        await journal.audit(
            AuditEventType.MODEL_PLACED,
            actor_principal_id=actor_id,
            model=p.model_id,
            details={
                "name": p.name, "node": node_label, "servers": server_names,
                "context_length": p.window, "replaced": replaced_names, "source": source,
                "capabilities": caps,
            },
        )  # fmt: skip
    if became_default:
        async with sessions() as s:
            await set_default(
                s, journal, p.name, actor_id=actor_id, source=source, capabilities=caps
            )
    async with sessions() as s:
        for _, old_model in p.replaced:
            old = await s.get(ModelResource, old_model.id)
            if old is not None:
                await _forget_if_unplaced(s, old)
        await s.commit()
    no_tools = caps is not None and "tools" not in caps
    yield (
        {"status": "placed", "name": p.name, "node": node_label, "servers": server_names,
         "default": became_default, "window": p.window}
        | ({"warning": NO_TOOLS} if no_tools else {})
    )  # fmt: skip


async def remove(
    session: AsyncSession, http: httpx.AsyncClient, ollama_url: str, journal: Journal | None,
    model: str, *, node_name: str | None = None, gpu: int | None = None, force: bool = False,
    actor_id: uuid.UUID | None, source: str,
) -> list[str]:  # fmt: skip
    """Takes the model off the node's servers; returns ``node/server`` labels removed."""
    name = catalog_name(model)
    row = await session.scalar(select(ModelResource).where(ModelResource.name == name))
    if row is None or row.alias_of is not None:
        raise PlacementError(404, "model_not_found", f"{name} is not in the catalog")
    node = await _node(session, node_name)
    found = (
        await session.execute(
            select(ModelPlacement, ModelServer)
            .join(ModelServer, ModelServer.id == ModelPlacement.model_server_id)
            .where(ModelPlacement.model_resource_id == row.id, ModelServer.node_id == node.id)
            .order_by(ModelServer.name)
        )
    ).all()
    if gpu is not None:
        found = [row for row in found if row[1].name == f"gpu{gpu}"]
    if not found:
        raise PlacementError(404, "placement_not_found", f"{name} is not placed on {node.name}")
    target = await default_target(session)
    total = await session.scalar(
        select(func.count())
        .select_from(ModelPlacement)
        .where(ModelPlacement.model_resource_id == row.id)
    )
    if target is not None and target.id == row.id and total == len(found) and not force:
        raise PlacementError(
            409, "default_would_be_unavailable",
            f"{name} is the default model and this is its last placement: move the pointer "
            "(yorath model default <model>) or repeat with --force",
        )  # fmt: skip
    labels = []
    for placement, _ in found:
        node_label, server_label = await _drop_placement(session, http, ollama_url, placement, row)
        labels.append(f"{node_label}/{server_label}")
    model_id = row.model_id
    forgotten = await _forget_if_unplaced(session, row)
    await session.commit()
    if journal is not None:
        await journal.audit(
            AuditEventType.MODEL_REMOVED,
            actor_principal_id=actor_id,
            model=model_id,
            details={"name": name, "servers": labels, "source": source, "forgotten": forgotten},
        )
    return labels


async def set_node_classes(
    session: AsyncSession, journal: Journal | None, node_name: str, classes: list[str], *,
    actor_id: uuid.UUID | None,
) -> list[str]:  # fmt: skip
    """Data classes that may be sent to the node; narrowing is refused while a model that
    accepts more is placed there."""
    unknown = [c for c in classes if c not in CLASS_ORDER]
    if unknown or not classes:
        raise PlacementError(400, "unknown_class", f"data classes are {', '.join(CLASS_ORDER)}")
    wanted = [c for c in CLASS_ORDER if c in classes]
    node = await _node(session, node_name)
    rows = (
        await session.execute(
            select(ModelResource.name, ModelResource.allowed_data_classes, ModelServer.name)
            .join(ModelPlacement, ModelPlacement.model_resource_id == ModelResource.id)
            .join(ModelServer, ModelServer.id == ModelPlacement.model_server_id)
            .where(ModelServer.node_id == node.id)
        )
    ).all()
    blocking = [
        f"{model} on {server}" for model, allowed, server in rows if set(allowed) - set(wanted)
    ]
    if blocking:
        raise PlacementError(
            409, "placements_block",
            f"node {node.name} holds models that accept more data classes: "
            f"{', '.join(sorted(blocking))}; remove them first",
        )  # fmt: skip
    previous = list(node.allowed_data_classes)
    node.allowed_data_classes = wanted
    await session.commit()
    if journal is not None:
        await journal.audit(
            AuditEventType.NODE_CLASSES_CHANGED,
            actor_principal_id=actor_id,
            details={"node": node.name, "classes": wanted, "previous": previous},
        )
    return wanted


# --- routing ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """A ready placement on an active node: where a call to the model may go."""

    server_id: uuid.UUID
    label: str  # node/server
    url: str
    slots: int
    main: bool = False  # on the main node: no heartbeat to tell that it is back
    seen_at: datetime | None = None  # the node's last heartbeat


async def candidates(
    session: AsyncSession, ollama_url: str, model: ModelResource
) -> tuple[list[Candidate], list[str]]:
    """Ready placements on active nodes, and why the others cannot take a call."""
    rows = (
        await session.execute(
            select(ModelPlacement, ModelServer, Node)
            .join(ModelServer, ModelServer.id == ModelPlacement.model_server_id)
            .join(Node, Node.id == ModelServer.node_id)
            .where(ModelPlacement.model_resource_id == model.id)
            .order_by(Node.role, Node.name, ModelServer.name)
        )
    ).all()
    ready, reasons = [], []
    for placement, server, node in rows:
        label = f"{node.name}/{server.name}"
        if placement.status is not PlacementStatus.READY:
            reasons.append(f"{label} is {placement.status.value}")
        elif node.status != "active" or (node.role != "main" and not node.overlay_ip):
            reasons.append(f"node {node.name} is {node.status}")
        else:
            ready.append(
                Candidate(
                    server.id, label, server_url(ollama_url, node, server), server.slots,
                    main=node.role == "main", seen_at=node.last_seen_at,
                )
            )  # fmt: skip
    return ready, reasons
