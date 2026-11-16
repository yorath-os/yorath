"""Reading the journal for the screen: names instead of ids, the Execution chain, the trace."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.journal.events import Decision
from yorath.control.journal.models import AuditEvent, TraceSpan
from yorath.control.model import (
    Agent,
    AgentRevision,
    ApiKey,
    DataAsset,
    Execution,
    ExecutionAttempt,
    Principal,
)

MAX_DEPTH = 10


class EventOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    event_type: str
    execution_id: uuid.UUID | None
    parent_execution_id: uuid.UUID | None
    actor: str | None
    effective_principal: str | None
    agent: str | None
    revision_no: int | None
    runtime: str | None
    model: str | None
    tool: str | None
    data_asset: str | None
    data_class: str | None
    decision: str | None
    reason: str | None
    cost_usd: Decimal | None
    control_level: str | None
    details: dict[str, Any]


class ChainItem(BaseModel):
    id: uuid.UUID
    parent_execution_id: uuid.UUID | None
    agent: str
    revision_no: int
    status: str
    trigger: str
    created_at: datetime
    finished_at: datetime | None
    actor: str | None
    effective_principal: str | None
    model: str | None
    max_data_class_seen: str | None
    api_key: str | None = None  # "name (prefix…)" of the key the /v1 call came with


class SpanOut(BaseModel):
    span_id: str
    parent_span_id: str | None
    execution_id: uuid.UUID
    name: str
    kind: str
    model: str | None
    status: str
    started_at: datetime
    ended_at: datetime | None
    attributes: dict[str, Any]


class ExecutionDetail(BaseModel):
    chain: list[ChainItem]
    events: list[EventOut]
    spans: list[SpanOut]


async def _names(session: AsyncSession, column: Any, name: Any, ids: set[Any]) -> dict[Any, Any]:
    ids.discard(None)
    if not ids:
        return {}
    return {k: v for k, v in (await session.execute(select(column, name).where(column.in_(ids))))}


async def _events_out(session: AsyncSession, events: list[AuditEvent]) -> list[EventOut]:
    principals = await _names(
        session,
        Principal.id,
        Principal.display_name,
        {e.actor_principal_id for e in events} | {e.effective_principal_id for e in events},
    )
    agents = await _names(session, Agent.id, Agent.name, {e.agent_id for e in events})
    revisions = await _names(
        session,
        AgentRevision.id,
        AgentRevision.revision_no,
        {e.agent_revision_id for e in events},
    )
    assets = await _names(session, DataAsset.id, DataAsset.name, {e.data_asset_id for e in events})
    return [
        EventOut(
            id=e.id,
            occurred_at=e.occurred_at,
            event_type=e.event_type.value,
            execution_id=e.execution_id,
            parent_execution_id=e.parent_execution_id,
            actor=principals.get(e.actor_principal_id),
            effective_principal=principals.get(e.effective_principal_id),
            agent=agents.get(e.agent_id),
            revision_no=revisions.get(e.agent_revision_id),
            runtime=e.runtime_type,
            model=e.model,
            tool=e.tool,
            data_asset=assets.get(e.data_asset_id),
            data_class=e.data_class.value if e.data_class else None,
            decision=e.decision.value if e.decision else None,
            reason=e.reason,
            cost_usd=e.cost_usd,
            control_level=e.control_level.value if e.control_level else None,
            details=e.details,
        )
        for e in events
    ]


async def list_events(
    session: AsyncSession,
    *,
    agent: str | None = None,
    user: uuid.UUID | None = None,
    decision: Decision | None = None,
    before: datetime | None = None,
    limit: int = 50,
) -> list[EventOut]:
    query = select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(limit)
    if agent:
        query = query.join(Agent, Agent.id == AuditEvent.agent_id).where(Agent.name == agent)
    if user:
        query = query.where(
            or_(AuditEvent.actor_principal_id == user, AuditEvent.effective_principal_id == user)
        )
    if decision:
        query = query.where(AuditEvent.decision == decision)
    if before:
        query = query.where(AuditEvent.occurred_at < before)
    return await _events_out(session, list((await session.scalars(query)).all()))


async def _chain_ids(session: AsyncSession, execution: Execution) -> list[uuid.UUID]:
    """The root and all descendants: a request by a child Execution gives the same chain."""
    root = execution
    for _ in range(MAX_DEPTH):
        if root.parent_execution_id is None:
            break
        parent = await session.get(Execution, root.parent_execution_id)
        if parent is None:
            break
        root = parent
    ids, frontier = [root.id], [root.id]
    for _ in range(MAX_DEPTH):
        frontier = list(
            (
                await session.scalars(
                    select(Execution.id).where(Execution.parent_execution_id.in_(frontier))
                )
            ).all()
        )
        if not frontier:
            break
        ids += frontier
    return ids


async def execution_detail(session: AsyncSession, execution: Execution) -> ExecutionDetail:
    ids = await _chain_ids(session, execution)
    rows = (
        await session.execute(
            select(
                Execution,
                Agent.name,
                AgentRevision.revision_no,
                ExecutionAttempt.model,
                ApiKey.name,
                ApiKey.prefix,
            )
            .join(AgentRevision, AgentRevision.id == Execution.agent_revision_id)
            .outerjoin(ApiKey, ApiKey.id == Execution.api_key_id)
            .join(Agent, Agent.id == AgentRevision.agent_id)
            .outerjoin(
                ExecutionAttempt,
                (ExecutionAttempt.execution_id == Execution.id)
                & (ExecutionAttempt.attempt_no == 1),
            )
            .where(Execution.id.in_(ids))
            .order_by(Execution.created_at)
        )
    ).all()
    principals = await _names(
        session,
        Principal.id,
        Principal.display_name,
        {e.initiated_by_principal_id for e, *_ in rows}
        | {e.effective_principal_id for e, *_ in rows},
    )
    events = (
        await session.scalars(
            select(AuditEvent)
            .where(AuditEvent.execution_id.in_(ids))
            .order_by(AuditEvent.occurred_at)
        )
    ).all()
    spans = (
        await session.scalars(
            select(TraceSpan).where(TraceSpan.execution_id.in_(ids)).order_by(TraceSpan.started_at)
        )
    ).all()
    return ExecutionDetail(
        chain=[
            ChainItem(
                id=e.id,
                parent_execution_id=e.parent_execution_id,
                agent=name,
                revision_no=revision_no,
                status=e.status.value,
                trigger=e.trigger.value,
                created_at=e.created_at,
                finished_at=e.finished_at,
                actor=principals.get(e.initiated_by_principal_id),
                effective_principal=principals.get(e.effective_principal_id),
                model=model,
                max_data_class_seen=e.max_data_class_seen.value if e.max_data_class_seen else None,
                api_key=f"{key_name} ({key_prefix}…)" if key_name else None,
            )
            for e, name, revision_no, model, key_name, key_prefix in rows
        ],
        events=await _events_out(session, list(events)),
        spans=[
            SpanOut(
                span_id=s.span_id,
                parent_span_id=s.parent_span_id,
                execution_id=s.execution_id,
                name=s.name,
                kind=s.kind.value,
                model=s.model,
                status=s.status.value,
                started_at=s.started_at,
                ended_at=s.ended_at,
                attributes=s.attributes,
            )
            for s in spans
        ],
    )
