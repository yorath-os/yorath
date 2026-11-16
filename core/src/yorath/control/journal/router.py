"""``/journal``: the Journal screen, read-only, owner only."""

import uuid
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from yorath.control.identity.deps import Db, Owner
from yorath.control.journal import views
from yorath.control.journal.events import Decision
from yorath.control.journal.models import EgressFlow
from yorath.control.model import Execution
from yorath.db import utcnow

router = APIRouter(prefix="/journal", tags=["journal"])


@router.get("/events", response_model=list[views.EventOut])
async def list_events(
    _: Owner,
    db: Db,
    agent: str | None = None,
    user: uuid.UUID | None = None,
    decision: Decision | None = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    return await views.list_events(
        db, agent=agent, user=user, decision=decision, before=before, limit=limit
    )


@router.get("/egress")
async def egress_flows(_: Owner, db: Db, hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24):
    """Node network (change node-egress): source → domain for the last hours, denials separately."""
    since = utcnow() - timedelta(hours=hours)
    rows = (
        await db.execute(
            select(
                EgressFlow.node, EgressFlow.source, EgressFlow.domain,
                func.sum(EgressFlow.requests), func.sum(EgressFlow.blocked),
                func.sum(EgressFlow.bytes), func.max(EgressFlow.last_at),
            )
            .where(EgressFlow.hour >= since.replace(minute=0, second=0, microsecond=0))
            .group_by(EgressFlow.node, EgressFlow.source, EgressFlow.domain)
            .order_by(func.max(EgressFlow.last_at).desc())
            .limit(500)
        )
    ).all()  # fmt: skip
    return [
        {"node": n, "source": s, "domain": d, "requests": int(r), "blocked": int(b),
         "bytes": int(by), "last_at": last}
        for n, s, d, r, b, by, last in rows
    ]  # fmt: skip


@router.get("/executions/{execution_id}", response_model=views.ExecutionDetail)
async def execution_detail(execution_id: uuid.UUID, _: Owner, db: Db):
    execution = await db.get(Execution, execution_id)
    if execution is None:
        raise HTTPException(404, detail="execution_not_found")
    return await views.execution_detail(db, execution)
