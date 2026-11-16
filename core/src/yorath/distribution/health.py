"""Dependency checks for /health. Each check is a coroutine returning (ok, detail)."""

import asyncio
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

Check = Callable[[], Awaitable[tuple[bool, str]]]


def db_check(engine: AsyncEngine, timeout: float) -> Check:
    async def run() -> tuple[bool, str]:
        try:
            async with asyncio.timeout(timeout), engine.connect() as conn:
                version = (await conn.execute(text("select version()"))).scalar_one()
            return True, str(version).split(" on ")[0]
        except Exception as exc:  # noqa: BLE001 — any error means the dependency is unavailable
            return False, f"{type(exc).__name__}: {exc}"[:200]

    return run


def ollama_check(client: httpx.AsyncClient, base_url: str, timeout: float) -> Check:
    async def run() -> tuple[bool, str]:
        try:
            r = await client.get(f"{base_url}/api/version", timeout=timeout)
            r.raise_for_status()
            return True, f"ollama {r.json().get('version', '?')}"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"[:200]

    return run


async def run_checks(checks: dict[str, Check]) -> dict:
    names = list(checks)
    results = await asyncio.gather(*(checks[n]() for n in names))
    detail = {n: {"ok": ok, "detail": d} for n, (ok, d) in zip(names, results, strict=True)}
    return {"status": "ok" if all(r[0] for r in results) else "degraded", "checks": detail}
