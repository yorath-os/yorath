"""Ollama: listing and pulling models with a progress stream; Model Resource registration."""

import json
import logging
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.model import DataClass, ModelProvider, ModelResource

log = logging.getLogger(__name__)

LOCAL_CHAT = "local/default"
LOCAL_EMBEDDINGS = "local/embeddings"
EXTERNAL_DEFAULT = "openrouter/default"


class OllamaClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base = str(base_url).rstrip("/")

    @property
    def http(self) -> httpx.AsyncClient:
        return self._http

    @property
    def base_url(self) -> str:
        return self._base

    async def tags(self) -> list[str]:
        r = await self._http.get(f"{self._base}/api/tags", timeout=10)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    async def unload(self, model: str) -> bool:
        """Unloads the model from memory (``keep_alive: 0``); a failure doesn't block the switch."""
        try:
            r = await self._http.post(
                f"{self._base}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30
            )
            r.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            log.warning("ollama: could not unload %s: %s", model, exc)
            return False

    async def capabilities(self, model: str) -> list[str] | None:
        """``/api/show``: what the model can do (``tools``, ``vision``…); None if unknown."""
        try:
            r = await self._http.post(f"{self._base}/api/show", json={"model": model}, timeout=30)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("ollama: no info about %s: %s", model, exc)
            return None
        caps = r.json().get("capabilities")
        return [str(c) for c in caps] if isinstance(caps, list) else None

    async def pull(self, model: str) -> AsyncIterator[dict[str, Any]]:
        """Ollama progress lines (status, digest, total, completed); the last one is success."""
        async with self._http.stream(
            "POST",
            f"{self._base}/api/pull",
            json={"model": model, "stream": True},
            timeout=httpx.Timeout(None, connect=10.0),  # a node that is down fails at once
        ) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if line.strip():
                    yield json.loads(line)


async def register_model(
    session: AsyncSession,
    name: str,
    provider: ModelProvider,
    model_id: str,
    classes: list[DataClass],
    *,
    is_default: bool = False,
    prices: tuple[Decimal, Decimal] | None = None,
    context_length: int | None = None,
) -> ModelResource:
    """Idempotent by ``name``: updates model_id and classes if the row already exists."""
    row = await session.scalar(select(ModelResource).where(ModelResource.name == name))
    if row is None:
        row = ModelResource(
            name=name, provider=provider, model_id=model_id, allowed_data_classes=[]
        )
        session.add(row)
    row.provider, row.model_id = provider, model_id
    row.allowed_data_classes = [c.value for c in classes]
    row.is_default, row.enabled = is_default, True
    if prices is not None:
        row.input_price_per_mtok, row.output_price_per_mtok = prices
    if context_length is not None:
        row.context_length = context_length
    await session.commit()
    return row
