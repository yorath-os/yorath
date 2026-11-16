"""``yorath model`` on the server: current model, catalog with "fits" marks, pull.

Whoever is logged in on the server (root) has the rights: no owner password is needed, but a
model change is marked with source ``host`` in the audit log. Pulling uses the same
``install_model`` as the wizard.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TextIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath import PRODUCT_NAME
from yorath.control.cluster import placements
from yorath.control.journal import Journal
from yorath.control.model import ModelResource
from yorath.control.setup.budget import fits, footprint
from yorath.control.setup.hardware import HardwareInfo, assess, local_window
from yorath.control.setup.install import NO_TOOLS, install_model
from yorath.control.setup.ollama import LOCAL_CHAT, LOCAL_EMBEDDINGS, OllamaClient
from yorath.distribution.manifest import Manifest

SEARCH = "https://ollama.com/search?c=tools"


@dataclass(frozen=True)
class ModelRow:
    name: str
    memory_gb: float | None  # video memory on GPU, RAM on CPU
    estimated: bool
    fits: bool | None
    recommended: bool
    installed: bool
    current: bool
    measured: str


def fit_for(
    model: str, manifest: Manifest, hw: HardwareInfo
) -> tuple[float | None, bool, bool | None]:
    """Model memory (video memory on GPU, RAM on CPU), whether it's an estimate, whether it fits."""
    chat, emb = footprint(model, manifest), footprint(manifest.models.embeddings, manifest)
    if hw.gpu_name and hw.vram_gb:
        if chat.vram_gb is None:
            return None, True, None
        return chat.vram_gb, chat.estimated, chat.vram_gb + (emb.vram_gb or 0) <= hw.vram_gb
    return chat.rss_gb, chat.estimated, fits(chat, emb, manifest.ollama_limit_gb(hw.ram_gb))


def model_rows(
    manifest: Manifest, hw: HardwareInfo, installed: list[str], current: str | None
) -> list[ModelRow]:
    """Distribution catalog and pulled models; the recommended one comes first."""
    recommended = assess(manifest, hw)["recommended_model"]
    names = [n for n in manifest.models.catalog if n != manifest.models.embeddings]
    names += [n for n in installed if n not in names and n != manifest.models.embeddings]
    rows = []
    for name in names:
        memory, estimated, ok = fit_for(name, manifest, hw)
        known = manifest.models.catalog.get(name)
        rows.append(
            ModelRow(
                name=name, memory_gb=memory, estimated=estimated, fits=ok,
                recommended=name == recommended, installed=name in installed,
                current=name == current,
                measured=known.measured_on if known else f"not verified by {PRODUCT_NAME}",
            )
        )  # fmt: skip
    return sorted(rows, key=lambda r: (not r.recommended, r.name not in manifest.models.catalog))


def _hardware(hw: HardwareInfo, window: int) -> str:
    where = f"{hw.gpu_name}, {hw.vram_gb} GB video memory" if hw.gpu_name else "no GPU"
    return f"Server: {hw.vcpu} vCPU, {hw.ram_gb} GB RAM, {where}; context window {window}"


def _gb(row: ModelRow) -> str:
    if row.memory_gb is None:
        return "?"
    return f"{'~' if row.estimated else ''}{row.memory_gb:g} GB"


def _verdict(ok: bool | None) -> str:
    return {True: "yes", False: "NO", None: "?"}[ok]


async def _registered(sessions: Callable[[], AsyncSession]) -> dict[str, ModelResource]:
    async with sessions() as s:
        rows = (await s.scalars(select(ModelResource))).all()
    return {r.name: r for r in rows}


async def show_status(
    out: TextIO, manifest: Manifest, hw: HardwareInfo, sessions: Callable[[], AsyncSession]
) -> int:
    window = local_window(manifest, hw)
    reg = await _registered(sessions)
    chat, emb = reg.get(LOCAL_CHAT), reg.get(LOCAL_EMBEDDINGS)
    recommended = assess(manifest, hw)["recommended_model"]
    print(_hardware(hw, window), file=out)
    if chat:
        print(f"Main model ({LOCAL_CHAT}): {chat.model_id}, window {chat.context_length}", file=out)
    else:
        print(f"Main model ({LOCAL_CHAT}): not pulled", file=out)
    print(f"Document search ({LOCAL_EMBEDDINGS}): {emb.model_id if emb else '—'}", file=out)
    print(f"Recommended for this server: {recommended}", file=out)
    if chat is None or chat.model_id != recommended:
        print("  → yorath model pull: pull the recommended one", file=out)
    elif chat.context_length != window:
        print(
            f"  → window is {chat.context_length}, the server allows {window}: yorath model pull",
            file=out,
        )
    return 0


async def show_list(
    out: TextIO,
    manifest: Manifest,
    hw: HardwareInfo,
    sessions: Callable[[], AsyncSession],
    client: OllamaClient,
) -> int:
    installed = await client.tags()
    reg = await _registered(sessions)
    current = reg[LOCAL_CHAT].model_id if LOCAL_CHAT in reg else None
    rows = model_rows(manifest, hw, installed, current)
    memory = "VIDEO MEMORY" if hw.gpu_name else "MEMORY"
    print(_hardware(hw, local_window(manifest, hw)), file=out)
    print(f"\n  {'MODEL':<24} {memory:<12} {'FITS':<11} {'ON SERVER':<16} MEASURED", file=out)
    for r in rows:
        mark = "*" if r.recommended else " "
        # "pulled": on this server; "default": local/default points at it, wherever it is placed
        where = ", ".join(w for w, on in (("pulled", r.installed), ("default", r.current)) if on)
        line = f"{mark} {r.name:<24} {_gb(r):<12} {_verdict(r.fits):<11} {where:<16} {r.measured}"
        print(line, file=out)
    print("\n* recommended for this server", file=out)
    print(
        f"Other models: {SEARCH} (needs the tools tag) or hf.co/<author>/<repo>:<quant>",
        file=out,
    )
    print("Pull: yorath model pull <name>; without a name, the recommended one", file=out)
    await show_catalog(out, sessions)
    return 0


async def show_catalog(out: TextIO, sessions: Callable[[], AsyncSession]) -> None:
    """Catalog models with their placements, then every model server of the cluster."""
    async with sessions() as s:
        models = await placements.catalog(s)
        servers = await placements.servers(s)
    print(f"\n  {'CATALOG MODEL':<30} {'WINDOW':<8} PLACEMENTS", file=out)
    for m in models:
        where = ", ".join(
            f"{p.node}/{p.server}"
            + ("" if p.status == "ready" and p.node_status == "active" else f" ({_state(p)})")
            for p in m.placements
        )
        mark = "*" if m.default else " "
        print(f"{mark} {m.name:<30} {m.context_length:<8} {where or 'not placed'}", file=out)
    if not models:
        print("  no chat model is placed: yorath model pull", file=out)
    print(f"\n* {LOCAL_CHAT} points here (yorath model default <name>)", file=out)
    print(f"\n  {'MODEL SERVER':<22} {'GPU':<22} MODEL", file=out)
    for srv in servers:
        card = f"{srv.gpu or 'GPU'} {srv.vram_gb or '?'} GB" if srv.gpus else "no GPU"
        note = "" if srv.node_status == "active" else f" (node {srv.node_status})"
        label = f"{srv.node}/{srv.server}"
        print(f"  {label:<22} {card:<22} {srv.model or 'free'}{note}", file=out)
    print(
        "Place on a node: yorath model pull <name> --node <node> [--gpu N]; "
        "take off: yorath model remove <name> --node <node>",
        file=out,
    )


def _state(p: placements.PlacementOut) -> str:
    if p.node_status != "active":
        return f"node {p.node_status}"
    if p.status == "pulling":
        return f"pulling {p.progress}%"
    return f"{p.status}: {p.detail}" if p.detail else p.status


async def pull_on_node(
    out: TextIO,
    manifest: Manifest,
    sessions: Callable[[], AsyncSession],
    journal: Journal,
    client: OllamaClient,
    *,
    name: str | None,
    node: str | None,
    gpu: int | None,
    force: bool,
    replace: bool,
) -> int:
    """``yorath model pull <name> --node <node>``: the main node pulls into the node's Ollama."""
    model = name or manifest.models.default_gpu
    if model.removeprefix("local/") not in manifest.models.catalog:
        print(
            f"{model} is not verified by {PRODUCT_NAME}: quality and tool calling are unmeasured.",
            file=out,
        )
    last = -1
    try:
        async for event in placements.place(
            client.http, client.base_url, sessions, journal, manifest, model,
            node_name=node, gpu=gpu, force=force, replace=replace, actor_id=None, source="host",
        ):  # fmt: skip
            if event.get("error"):
                print(f"Ollama error: {event['error']}", file=out)
                return 1
            if event.get("status") == "skipped":
                print(f"  busy, left alone: {', '.join(event['servers'])}", file=out)
            elif event.get("status") == "placed":
                where = ", ".join(f"{event['node']}/{srv}" for srv in event["servers"])
                print(f"Done: {event['name']} on {where}, window {event['window']}", file=out)
                if event.get("default"):
                    print(f"  {LOCAL_CHAT} now points at {event['name']}", file=out)
                if event.get("warning") == NO_TOOLS:
                    print("Warning: the model cannot call tools, so agents with tools "
                          "(documents, search, Codex) will not work on it.", file=out)  # fmt: skip
                return 0
            elif event.get("total") and event.get("completed"):
                pct = int(100 * event["completed"] / event["total"])
                if pct // 10 != last // 10:
                    last = pct
                    print(f"  {pct}% of {event['total'] / 1024**3:.1f} GB", file=out, flush=True)
            elif event.get("status") and not event.get("total"):
                print(f"  {event['status']}", file=out, flush=True)
    except placements.PlacementError as exc:
        print(f"error ({exc.code}): {exc}", file=out)
        return 2
    print("Ollama did not confirm the pull (no success): the model is not placed", file=out)
    return 1


async def remove(
    out: TextIO,
    sessions: Callable[[], AsyncSession],
    journal: Journal,
    client: OllamaClient,
    *,
    name: str,
    node: str | None,
    gpu: int | None,
    force: bool,
) -> int:
    try:
        async with sessions() as s:
            labels = await placements.remove(
                s, client.http, client.base_url, journal, name, node_name=node, gpu=gpu,
                force=force, actor_id=None, source="host",
            )  # fmt: skip
    except placements.PlacementError as exc:
        print(f"error ({exc.code}): {exc}", file=out)
        return 2
    print(f"Removed {placements.catalog_name(name)} from {', '.join(labels)}", file=out)
    return 0


async def set_default(
    out: TextIO, sessions: Callable[[], AsyncSession], journal: Journal, *, name: str
) -> int:
    try:
        async with sessions() as s:
            target = await placements.set_default(s, journal, name, actor_id=None, source="host")
    except placements.PlacementError as exc:
        print(f"error ({exc.code}): {exc}", file=out)
        return 2
    print(f"{LOCAL_CHAT} → {target.name}, window {target.context_length}", file=out)
    return 0


async def pull(
    out: TextIO,
    manifest: Manifest,
    hw: HardwareInfo,
    sessions: Callable[[], AsyncSession],
    journal: Journal,
    client: OllamaClient,
    *,
    name: str | None,
    force: bool,
) -> int:
    model = name or assess(manifest, hw)["recommended_model"]
    _, _, ok = fit_for(model, manifest, hw)
    if ok is False and not force:
        print(f"{model} is estimated not to fit in this server's memory.", file=out)
        print(
            f"Estimate: yorath model list; pull anyway: yorath model pull {model} --force",
            file=out,
        )
        return 2
    if model not in manifest.models.catalog:
        print(
            f"{model} is not verified by {PRODUCT_NAME}: quality and tool calling are unmeasured.",
            file=out,
        )
    window = local_window(manifest, hw)
    print(f"Pulling {model} as {LOCAL_CHAT}, window {window}", file=out)
    last = -1
    result: dict[str, Any] | None = None
    async for event in install_model(
        client, sessions, journal, model=model, role="chat", window=window,
        actor_id=None, source="host", manifest=manifest, hw=hw,
    ):  # fmt: skip
        if event.get("error"):
            kind = f"error ({event['code']})" if event.get("code") else "Ollama error"
            print(f"{kind}: {event['error']}", file=out)
            return 1
        if event.get("status") == "registered":
            result = event
        elif event.get("total") and event.get("completed"):
            pct = int(100 * event["completed"] / event["total"])
            if pct // 10 != last // 10:
                last = pct
                print(f"  {pct}% of {event['total'] / 1024**3:.1f} GB", file=out, flush=True)
        elif event.get("status") and not event.get("total"):
            print(f"  {event['status']}", file=out, flush=True)
    if result is None:
        print("Ollama did not confirm the pull (no success): the model is not registered", file=out)
        return 1
    print(f"Done: {LOCAL_CHAT} → {model}, window {window}", file=out)
    if result.get("warning") == NO_TOOLS:
        print("Warning: the model cannot call tools, so agents with tools "
              "(documents, search, Codex) will not work on it.", file=out)  # fmt: skip
    return 0
