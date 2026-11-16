"""Trace retention: deletes spans older than ``journal.trace_retention_days``. Audit stays."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yorath.control.journal.models import EgressFlow, TraceSpan
from yorath.db import utcnow

log = logging.getLogger(__name__)


async def purge_traces(
    session: AsyncSession, retention_days: int, now: datetime | None = None
) -> int:
    cutoff = (now or utcnow()) - timedelta(days=retention_days)
    result = cast(
        CursorResult[Any],
        await session.execute(delete(TraceSpan).where(TraceSpan.started_at < cutoff)),
    )
    await session.execute(
        delete(EgressFlow).where(EgressFlow.hour < cutoff)
    )  # node network summary
    await session.commit()
    return result.rowcount or 0


async def retention_loop(
    sessions: async_sessionmaker[AsyncSession], retention_days: int, interval_s: float = 86400
) -> None:
    """Core background task: once a day. A database error does not kill the task."""
    while True:
        try:
            async with sessions() as s:
                removed = await purge_traces(s, retention_days)
            log.info(
                "trace retention: removed %d spans older than %d days", removed, retention_days
            )
        except Exception:  # noqa: BLE001
            log.exception("trace retention failed")
        await asyncio.sleep(interval_s)
