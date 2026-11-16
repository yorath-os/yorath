"""OpenRouter: key check. The key lives only in the SecretStore (§6: hybrid by explicit consent)."""

from decimal import Decimal
from typing import Any, Literal

import httpx
from sqlalchemy import select

from yorath.control.model import ModelProvider, ModelResource

SECRET_NAME = "openrouter_api_key"
LEAVES_SERVER = (
    "When an external model is called, the request text, the selected document fragments of "
    "classes Public and Internal, and the model's answer leave the server. Confidential and "
    "Restricted documents are never sent to an external model: such a call is blocked by policy."
)


KeyCheck = Literal["ok", "invalid", "denied"]


async def verify_key(http: httpx.AsyncClient, base_url: str, api_key: str) -> KeyCheck:
    """``invalid``: the key is rejected (401); ``denied``: the provider refuses the server (403,
    which is how OpenRouter shows a regional block). Network errors raise an httpx exception."""
    r = await http.get(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    if r.status_code == 401:
        return "invalid"
    if r.status_code == 403:
        return "denied"
    r.raise_for_status()
    return "ok"


WINDOW_UNKNOWN = 32768  # the catalog didn't answer: a usable window, not the CPU 4,096


async def fetch_model(
    http: httpx.AsyncClient, base_url: str, api_key: str, model_id: str
) -> tuple[tuple[Decimal, Decimal] | None, int | None]:
    """Price per million tokens (input, output) and context window from the OpenRouter catalog;
    None for what is unavailable."""
    try:
        r = await http.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        r.raise_for_status()
        item = next((i for i in r.json().get("data", []) if i.get("id") == model_id), None)
    except httpx.HTTPError, ValueError:
        return None, None
    if item is None:
        return None, None
    window = item.get("context_length")
    window = int(window) if isinstance(window, int | float) and window > 0 else None
    try:
        price = item.get("pricing") or {}
        million = Decimal(1_000_000)
        prices = (
            Decimal(str(price["prompt"])) * million,
            Decimal(str(price["completion"])) * million,
        )
    except KeyError, ValueError, ArithmeticError:
        prices = None
    return prices, window


async def refresh_external_windows(
    sessions: Any, http: httpx.AsyncClient, base_url: str, store: Any, cap: int
) -> None:
    """An external model registered before its window came from the catalog has the CPU 4,096: the
    loop then drops tool results. At core start such a model gets its window from the catalog."""
    async with sessions() as session:
        rows = (
            await session.scalars(
                select(ModelResource).where(
                    ModelResource.provider == ModelProvider.OPENROUTER,
                    ModelResource.context_length == 4096,
                )
            )
        ).all()
        if not rows:
            return
        key = await store.get(SECRET_NAME)
        if not key:
            return
        for row in rows:
            _, window = await fetch_model(http, base_url, key, row.model_id)
            row.context_length = min(window, cap) if window else WINDOW_UNKNOWN
        await session.commit()
