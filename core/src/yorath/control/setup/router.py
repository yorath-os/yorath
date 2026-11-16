"""``/setup/*``: setup wizard steps, owner only."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from yorath.control.identity.deps import Db, Owner
from yorath.control.model import DataClass, ModelProvider, ModelResource, get_setting, set_setting
from yorath.control.setup import openrouter, tavily
from yorath.control.setup.budget import fits, footprint
from yorath.control.setup.hardware import assess, detect, local_window
from yorath.control.setup.install import install_model
from yorath.control.setup.model_cli import fit_for, model_rows
from yorath.control.setup.ollama import (
    EXTERNAL_DEFAULT,
    LOCAL_CHAT,
    LOCAL_EMBEDDINGS,
    OllamaClient,
    register_model,
)
from yorath.db import utcnow
from yorath.enforcement.secrets import DbSecretStore, SecretStoreUnavailable

log = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["setup"])

EXTERNAL_CLASSES = [DataClass.PUBLIC, DataClass.INTERNAL]


def _budget(request: Request, model: str) -> dict[str, Any]:
    """Model footprint and verdict: does it fit with embeddings in the container limit."""
    manifest = request.app.state.manifest
    limit = manifest.ollama_limit_gb(detect(request.app.state.settings).ram_gb)
    chat = footprint(model, manifest)
    embeddings = footprint(manifest.models.embeddings, manifest)
    return {**asdict(chat), "limit_gb": limit, "fits": fits(chat, embeddings, limit)}


def _ollama(request: Request) -> OllamaClient:
    return OllamaClient(request.app.state.http, request.app.state.settings.ollama_url)


@router.get("/status")
async def status(_: Owner, request: Request, db: Db) -> dict[str, Any]:
    rows = (await db.scalars(select(ModelResource).order_by(ModelResource.name))).all()
    registered = {m.name: m.model_id for m in rows if m.enabled}
    store: DbSecretStore = request.app.state.secrets
    return {
        "setup_completed": await get_setting(db, "setup_completed", False),
        "models": {
            "chat": registered.get(LOCAL_CHAT),
            "embeddings": registered.get(LOCAL_EMBEDDINGS),
            "external": registered.get(EXTERNAL_DEFAULT),
        },
        "resources": [
            {
                "name": m.name,
                "model_id": m.model_id,
                "provider": m.provider.value,
                "classes": m.allowed_data_classes,
                "context_length": m.context_length,
                "input_price_per_mtok": str(m.input_price_per_mtok)
                if m.input_price_per_mtok is not None
                else None,
                "output_price_per_mtok": str(m.output_price_per_mtok)
                if m.output_price_per_mtok is not None
                else None,
            }
            for m in rows
            if m.enabled
        ],
        "openrouter_configured": EXTERNAL_DEFAULT in registered,
        "web_search_configured": tavily.SECRET_NAME in await store.names(),
        "secrets_available": store.available,
    }


REPORT_STALE = timedelta(minutes=15)  # the yorath-scan timer runs every 5 minutes


@router.get("/host-report")
async def host_report(_: Owner, request: Request) -> dict[str, Any]:
    """Agents outside Yorath from the ``yorath scan`` report (decision 36).

    The core runs in a container and cannot see them itself.
    """
    path = request.app.state.settings.host_report_dir / "host.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        checked = datetime.fromisoformat(report["checked_at"])
    except OSError, ValueError, KeyError:
        return {"available": False, "stale": False, "checked_at": None, "agents": [],
                "traces": [], "containers": [], "egress": None}  # fmt: skip
    return {
        "available": True,
        "stale": utcnow() - checked > REPORT_STALE,
        "checked_at": report["checked_at"],
        "agents": report.get("agents", []),
        "traces": report.get("traces", []),
        "containers": report.get("containers", []),
        "egress": report.get("egress"),  # node network control (change node-egress)
    }


@router.get("/hardware")
async def hardware(_: Owner, request: Request) -> dict[str, Any]:
    return assess(request.app.state.manifest, detect(request.app.state.settings))


@router.get("/models")
async def models(_: Owner, request: Request, db: Db) -> dict[str, Any]:
    info = assess(request.app.state.manifest, detect(request.app.state.settings))
    try:
        installed = await _ollama(request).tags()
    except httpx.HTTPError as exc:
        raise HTTPException(503, detail=f"ollama_unavailable: {exc}") from exc
    registered = [
        {"name": m.name, "model_id": m.model_id, "provider": m.provider.value}
        for m in (await db.scalars(select(ModelResource).order_by(ModelResource.name))).all()
    ]
    hw = detect(request.app.state.settings)
    current = next((m["model_id"] for m in registered if m["name"] == LOCAL_CHAT), None)
    rows = model_rows(request.app.state.manifest, hw, installed, current)
    return {
        "recommended": {"chat": info["recommended_model"], "embeddings": info["embeddings_model"]},
        "window": local_window(request.app.state.manifest, hw),
        "catalog": [asdict(r) for r in rows],
        "local_mode": info["local_mode"],
        "installed": installed,
        "registered": registered,
        "budgets": {
            name: _budget(request, name)
            for name in dict.fromkeys(
                [info["recommended_model"], *request.app.state.manifest.models.catalog, *installed]
            )
        },
    }


class PullIn(BaseModel):
    model: str = Field(max_length=200)
    role: str = Field(default="chat", pattern="^(chat|embeddings)$")
    force: bool = False  # pull a model that does not fit the budget


@router.post("/models/pull")
async def pull(data: PullIn, owner: Owner, request: Request) -> StreamingResponse:
    """NDJSON stream of Ollama progress; after success the model is registered as a Model Resource.

    A model that does not fit the budget requires ``force``. Pull, registration, unloading the
    previous model and audit are ``install_model``, the same as ``yorath model pull``.
    """
    if data.role == "chat" and not data.force:
        budget = _budget(request, data.model)
        hw = detect(request.app.state.settings)
        # same estimate as yorath model: video memory on GPU, the container limit on CPU
        if fit_for(data.model, request.app.state.manifest, hw)[2] is False:
            raise HTTPException(409, detail={"code": "does_not_fit", **budget})
    window = local_window(request.app.state.manifest, detect(request.app.state.settings))
    install = install_model(
        _ollama(request), request.app.state.sessions, request.app.state.journal,
        model=data.model, role=data.role, window=window, actor_id=owner.id, source="web",
        manifest=request.app.state.manifest, hw=detect(request.app.state.settings),
    )  # fmt: skip

    events: asyncio.Queue[bytes | None] = asyncio.Queue()

    def put(event: dict[str, Any]) -> None:
        events.put_nowait((json.dumps(event, ensure_ascii=False) + "\n").encode())

    async def run() -> None:
        """Pull and registration don't depend on the client: a dropped stream won't cancel them."""
        try:
            async for event in install:
                put(event)
                indexer = getattr(request.app.state, "indexer", None)
                if event.get("status") == "registered" and data.role == "embeddings" and indexer:
                    await indexer.schedule_pending()  # documents that waited for this model
        except httpx.HTTPError as exc:
            put({"error": f"ollama: {exc}"})
        except Exception as exc:  # noqa: BLE001 — background task: the error shows in the stream and log
            log.exception("model pull %s", data.model)
            put({"error": f"{type(exc).__name__}: {exc}"})
        finally:
            events.put_nowait(None)

    task = asyncio.create_task(run())
    pulls: set[asyncio.Task[None]] | None = getattr(request.app.state, "pulls", None)
    if pulls is None:
        pulls = request.app.state.pulls = set()
    pulls.add(task)  # strong reference while the task runs
    task.add_done_callback(pulls.discard)

    async def stream() -> AsyncIterator[bytes]:
        while (line := await events.get()) is not None:
            yield line

    return StreamingResponse(stream(), media_type="application/x-ndjson")


REGION = (
    "OpenRouter refuses this server (403): this usually means a regional block, "
    "the key is not the problem. The key was not saved. Use internet access through another "
    "region or a local model on a GPU."
)


class OpenRouterIn(BaseModel):
    api_key: str = Field(min_length=10, max_length=500)
    consent: bool


@router.put("/openrouter")
async def set_openrouter(data: OpenRouterIn, _: Owner, request: Request, db: Db) -> dict[str, Any]:
    if not data.consent:
        raise HTTPException(422, detail="consent_required")
    store: DbSecretStore = request.app.state.secrets
    if not store.available:
        raise HTTPException(503, detail="secret_store_unavailable")
    settings = request.app.state.settings
    try:
        check = await openrouter.verify_key(
            request.app.state.http, settings.openrouter_url, data.api_key
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, detail=f"openrouter_unreachable: {exc}") from exc
    if check == "invalid":
        raise HTTPException(422, detail="invalid_api_key")
    if check == "denied":
        raise HTTPException(422, detail={"code": "provider_access_denied", "message": REGION})
    try:
        await store.put(openrouter.SECRET_NAME, data.api_key)
    except SecretStoreUnavailable as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    external = request.app.state.manifest.models.default_external
    prices, window = await openrouter.fetch_model(
        request.app.state.http, settings.openrouter_url, data.api_key, external
    )
    cap = request.app.state.manifest.models.external_context_max
    model = await register_model(
        db, EXTERNAL_DEFAULT, ModelProvider.OPENROUTER, external, EXTERNAL_CLASSES, prices=prices,
        context_length=min(window, cap) if window else openrouter.WINDOW_UNKNOWN,
    )  # fmt: skip
    await set_setting(db, "openrouter_consent", True)
    return {
        "model": {
            "name": model.name,
            "model_id": model.model_id,
            "classes": model.allowed_data_classes,
            "input_price_per_mtok": str(prices[0]) if prices else None,
            "output_price_per_mtok": str(prices[1]) if prices else None,
        },
        "warnings": ([] if prices else ["pricing_unavailable"])
        + ([] if window else ["window_unavailable"]),
        "leaves_server": openrouter.LEAVES_SERVER,
    }


class TavilyIn(BaseModel):
    api_key: str = Field(min_length=10, max_length=500)
    consent: bool


@router.put("/tavily")
async def set_tavily(data: TavilyIn, _: Owner, request: Request) -> dict[str, Any]:
    """One search of one result tries the key (one credit): a rejected key is not saved; any other
    failure saves it and says why search won't work yet (field-fixes-0-7)."""
    if not data.consent:
        raise HTTPException(422, detail="consent_required")
    store: DbSecretStore = request.app.state.secrets
    answer = await tavily.probe(
        request.app.state.http, request.app.state.settings.tavily_url, data.api_key
    )
    if answer == "invalid":
        raise HTTPException(422, detail="invalid_api_key")
    try:
        await store.put(tavily.SECRET_NAME, data.api_key)
    except SecretStoreUnavailable as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    warning = None if answer == "ok" else tavily.REASONS[answer]
    return {"leaves_server": tavily.LEAVES_SERVER, "warning": warning}


@router.post("/complete")
async def complete(_: Owner, db: Db) -> dict[str, bool]:
    await set_setting(db, "setup_completed", True)
    return {"setup_completed": True}
