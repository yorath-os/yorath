"""Pulling a local model and registering it: shared by the wizard and ``yorath model pull``.

The chat model goes through a placement on the main node (decision 44): the model that stood
there is replaced and ``local/default`` moves to the new one, so every agent with
``local/default`` answers with it. Both steps go to the audit log (``model.placed``,
``model.registered``) with who did it: the owner from the web or ``yorath`` on the server.
"""

from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.cluster import placements
from yorath.control.journal import AuditEventType, Journal
from yorath.control.model import DataClass, ModelProvider, ModelResource
from yorath.control.setup.hardware import HardwareInfo
from yorath.control.setup.ollama import LOCAL_CHAT, LOCAL_EMBEDDINGS, OllamaClient, register_model
from yorath.distribution.manifest import Manifest

ALL_CLASSES = list(DataClass)
NO_TOOLS = placements.NO_TOOLS


async def install_model(
    client: OllamaClient,
    sessions: Callable[[], AsyncSession],
    journal: Journal,
    *,
    model: str,
    role: str,
    window: int,
    actor_id: UUID | None,
    source: str,
    manifest: Manifest,
    hw: HardwareInfo,
) -> AsyncIterator[dict[str, Any]]:
    """Ollama progress lines, then ``{"status": "registered", ...}``.

    The previous chat model is unloaded, otherwise three models end up in memory. A model without
    ``tools`` is registered with a warning: agents with tools will not work on it. The caller has
    already checked that the model fits, so the placement is forced.
    """
    if role == "chat":
        try:
            async for event in placements.place(
                client.http, client.base_url, sessions, journal, manifest, model,
                replace=True, make_default=True, force=True, main_hardware=hw,
                actor_id=actor_id, source=source,
            ):  # fmt: skip
                if event.get("status") == "placed":
                    warning = {"warning": event["warning"]} if event.get("warning") else {}
                    yield {"status": "registered", "name": LOCAL_CHAT} | warning
                else:
                    yield event
        except placements.PlacementError as exc:
            yield {"error": str(exc), "code": exc.code}
        return
    name = LOCAL_EMBEDDINGS
    async with sessions() as s:
        current = await s.scalar(select(ModelResource).where(ModelResource.name == name))
        previous = current.model_id if current else None
    done = False
    async for event in client.pull(model):
        done = done or event.get("status") == "success"
        yield event
    if not done:
        return
    async with sessions() as s:
        await register_model(
            s, name, ModelProvider.OLLAMA, model, ALL_CLASSES, is_default=True,
            context_length=window,
        )  # fmt: skip
    caps = await client.capabilities(model)
    await journal.audit(
        AuditEventType.MODEL_REGISTERED,
        actor_principal_id=actor_id,
        model=model,
        details={
            "name": name,
            "model_id": model,
            "previous": previous,
            "context_length": window,
            "source": source,
            "capabilities": caps,
        },
    )
    yield {"status": "registered", "name": name}
