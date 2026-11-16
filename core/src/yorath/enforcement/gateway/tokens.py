"""The Execution token is the "session" of a sandboxed runtime with the Model Gateway (decision 21).

Only sha256 is stored; issuing and revoking are the audit events ``secret.issued`` /
``secret.revoked``.
This is not the secret store: provider keys stay in ``secrets``.
"""

import hashlib
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.journal import AuditEventType, Journal
from yorath.control.model import ExecutionToken
from yorath.db import utcnow

TOKEN_PREFIX = "yorath_exec_"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_execution_token(
    session: AsyncSession, journal: Journal | None, ctx_fields: dict, ttl_minutes: int
) -> str:
    """Issues a token for the duration of the Execution; ``ctx_fields`` is
    ``ExecutionContext.audit_fields()``."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ExecutionToken(
        execution_id=ctx_fields["execution_id"],
        token_hash=_hash(token),
        expires_at=utcnow() + timedelta(minutes=ttl_minutes),
    )
    session.add(row)
    await session.commit()
    if journal is not None:
        await journal.audit(
            AuditEventType.SECRET_ISSUED,
            **ctx_fields,
            details={"kind": "execution_token", "expires_at": row.expires_at.isoformat()},
        )
    return token


async def resolve_execution_token(session: AsyncSession, token: str) -> ExecutionToken | None:
    row = await session.scalar(
        select(ExecutionToken).where(ExecutionToken.token_hash == _hash(token))
    )
    if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
        return None
    return row


async def revoke_execution_tokens(
    session: AsyncSession, journal: Journal | None, ctx_fields: dict, execution_id: uuid.UUID
) -> int:
    rows = (
        await session.scalars(
            select(ExecutionToken).where(
                ExecutionToken.execution_id == execution_id, ExecutionToken.revoked_at.is_(None)
            )
        )
    ).all()
    now = utcnow()
    for row in rows:
        row.revoked_at = now
    await session.commit()
    if rows and journal is not None:
        await journal.audit(
            AuditEventType.SECRET_REVOKED,
            **ctx_fields,
            details={"kind": "execution_token", "count": len(rows)},
        )
    return len(rows)
