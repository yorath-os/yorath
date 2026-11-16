"""Core side of the host ``yorath`` command: what a restore will lose, and the audit record."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.journal import AuditEvent, AuditEventType, Journal
from yorath.control.model import Document, Execution, Message


async def changes_since(session: AsyncSession, moment: datetime) -> dict[str, int]:
    """How many records appeared after ``moment``: restoring the backup replaces them."""

    async def count(column) -> int:
        stmt = select(func.count()).select_from(column.class_).where(column > moment)
        return int(await session.scalar(stmt) or 0)

    return {
        "executions": await count(Execution.created_at),
        "messages": await count(Message.created_at),
        "documents": await count(Document.created_at),
        "audit_events": await count(AuditEvent.occurred_at),
    }


async def record_restore(
    journal: Journal,
    *,
    backup_at: datetime,
    version_before: str,
    version_after: str,
    replaced: dict[str, int],
    safety_backup: str | None,
    reason: str,
) -> AuditEvent:
    """``installation.restored`` in the restored database audit: the journal does not vanish
    silently."""
    details: dict[str, Any] = {
        "backup_at": backup_at.isoformat(),
        "version_before": version_before,
        "version_after": version_after,
        "replaced": replaced,
        "safety_backup": safety_backup,
    }
    return await journal.audit(
        AuditEventType.INSTALLATION_RESTORED, reason=reason[:2000], details=details
    )
