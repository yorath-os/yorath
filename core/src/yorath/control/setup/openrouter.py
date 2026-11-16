"""OpenRouter: key check. The key lives only in the SecretStore (§6: hybrid by explicit consent)."""

from decimal import Decimal
from typing import Literal

import httpx

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


async def fetch_pricing(
    http: httpx.AsyncClient, base_url: str, api_key: str, model_id: str
) -> tuple[Decimal, Decimal] | None:
    """Price per million tokens (input, output) from the OpenRouter catalog; None if unavailable."""
    try:
        r = await http.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        r.raise_for_status()
        for item in r.json().get("data", []):
            if item.get("id") == model_id:
                price = item.get("pricing") or {}
                million = Decimal(1_000_000)
                return Decimal(str(price["prompt"])) * million, Decimal(
                    str(price["completion"])
                ) * million
    except httpx.HTTPError, KeyError, ValueError, ArithmeticError:
        return None
    return None
