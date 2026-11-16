"""Single emission point: every plane writes spans and audit through ``Journal``."""

import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yorath.control.journal.events import AuditEventType
from yorath.control.journal.models import AuditEvent, SpanKind, SpanStatus, TraceSpan
from yorath.db import utcnow

FORBIDDEN_AUDIT_DETAILS = frozenset({"prompt", "messages", "input_messages", "output_messages"})


def new_span_id() -> str:
    """8 random bytes in hex, like an OTel span_id."""
    return secrets.token_hex(8)


class Journal:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @asynccontextmanager
    async def span(
        self,
        execution_id: uuid.UUID,
        name: str,
        kind: SpanKind,
        *,
        attempt_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        model: str | None = None,
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
        raw: dict[str, Any] | None = None,
    ) -> AsyncIterator[TraceSpan]:
        """Writes the span start, hands it to the caller, at the end writes ``ended_at`` and status.

        Attributes can be added along the way (``span.attributes[...] = ...``); an exception
        inside the block gives status ``error`` and propagates.
        """
        span = TraceSpan(
            trace_id=execution_id,
            span_id=new_span_id(),
            parent_span_id=parent_span_id,
            name=name,
            kind=kind,
            execution_id=execution_id,
            attempt_id=attempt_id,
            agent_id=agent_id,
            model=model,
            attributes=dict(attributes or {}),
            raw=raw,
        )
        async with self._sessions() as s:
            s.add(span)
            await s.commit()
        status = SpanStatus.OK
        try:
            yield span
        except BaseException:
            status = SpanStatus.ERROR
            raise
        finally:
            await self._finish(span, status)

    async def _finish(self, span: TraceSpan, status: SpanStatus) -> None:
        span.ended_at = utcnow()
        if span.status is not SpanStatus.ERROR:  # an error can also be set without an exception
            span.status = status
        status = span.status
        async with self._sessions() as s:
            await s.execute(
                update(TraceSpan)
                .where(TraceSpan.id == span.id)
                .values(
                    ended_at=span.ended_at,
                    status=status,
                    attributes=span.attributes,
                    model=span.model,
                    raw=span.raw,
                )
            )
            await s.commit()

    async def audit(self, event_type: AuditEventType, **fields: Any) -> AuditEvent:
        """One audit record. Prompts and messages are not accepted here."""
        details = fields.get("details") or {}
        leaked = FORBIDDEN_AUDIT_DETAILS & set(details)
        if leaked:
            raise ValueError(f"the audit log does not store prompts: {sorted(leaked)}")
        event = AuditEvent(event_type=event_type, **fields)
        async with self._sessions() as s:
            s.add(event)
            await s.commit()
        return event
