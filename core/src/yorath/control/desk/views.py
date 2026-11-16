"""Aggregates for the screens. Question text never gets here: only statuses, names and reasons."""

import uuid
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel
from sqlalchemy import Select, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.agents.model_choice import chosen
from yorath.control.cluster.placements import DefaultWarning, default_warning
from yorath.control.journal.events import Decision
from yorath.control.journal.models import AuditEvent
from yorath.control.model import (
    Agent,
    AgentRevision,
    Execution,
    ExecutionAttempt,
    ExecutionStatus,
    ExecutionTrigger,
    Node,
    Principal,
)
from yorath.db import utcnow
from yorath.distribution.manifest import Manifest

DESK_TASKS = 5
ERROR_LOG = 5
TOP_REASONS = 5
ERRORS = (
    ExecutionStatus.FAILED,
    ExecutionStatus.TIMEOUT,
    ExecutionStatus.CONFIG_ERROR,
    ExecutionStatus.DENIED,
)
ACTIVE = (ExecutionStatus.RUNNING, ExecutionStatus.QUEUED)
# a policy deny is a regular decision, not an agent failure: it shows in the policies widget
FAULTS = tuple(s for s in ERRORS if s is not ExecutionStatus.DENIED)


class TaskGroup(StrEnum):
    ALL = "all"
    RUNNING = "running"
    QUEUED = "queued"
    FAILED = "failed"


GROUPS: dict[TaskGroup, tuple[ExecutionStatus, ...]] = {
    TaskGroup.RUNNING: (ExecutionStatus.RUNNING,),
    TaskGroup.QUEUED: (ExecutionStatus.QUEUED,),
    TaskGroup.FAILED: ERRORS,
}


class TaskOut(BaseModel):
    id: uuid.UUID
    agent: str
    agent_display_name: str
    revision_no: int
    status: str
    trigger: str
    started_by: str | None
    created_at: datetime
    finished_at: datetime | None
    model: str | None
    error: str | None
    parent_execution_id: uuid.UUID | None


class TaskList(BaseModel):
    counts: dict[str, int]
    tasks: list[TaskOut]


class NodeOut(BaseModel):
    name: str
    cpu_count: int
    ram_gb: int
    gpu: str | None
    vram_gb: int | None


class AgentErrors(BaseModel):
    agent: str
    total: int
    by_status: dict[str, int]


class DenyReason(BaseModel):
    reason: str
    count: int


class PolicySummary(BaseModel):
    allowed: int
    denied: int
    reasons: list[DenyReason]


class ErrorEntry(BaseModel):
    id: uuid.UUID
    at: datetime
    status: str
    agent: str
    message: str | None


class DeskSummary(BaseModel):
    running: int
    queued: int
    errors_hour: int
    cost_usd_24h: Decimal
    tasks: list[TaskOut]
    more_tasks: int
    nodes: list[NodeOut]
    agent_errors: list[AgentErrors]
    policies: PolicySummary
    error_log: list[ErrorEntry]
    default_warning: DefaultWarning | None = None  # an automatic switch of the default model


def _task_query() -> Select:
    attempt = (
        select(ExecutionAttempt.model, ExecutionAttempt.error_message)
        .where(ExecutionAttempt.execution_id == Execution.id)
        .order_by(ExecutionAttempt.attempt_no.desc())
        .limit(1)
        .lateral()
    )
    return (
        select(
            Execution,
            Agent.name,
            Agent.display_name,
            AgentRevision.revision_no,
            Principal.display_name,
            attempt.c.model,
            attempt.c.error_message,
        )
        .join(AgentRevision, AgentRevision.id == Execution.agent_revision_id)
        .join(Agent, Agent.id == AgentRevision.agent_id)
        .outerjoin(Principal, Principal.id == Execution.initiated_by_principal_id)
        .outerjoin(attempt, true())
    )


def _task(row) -> TaskOut:
    run, agent, display_name, revision_no, started_by, model, error = row
    return TaskOut(
        id=run.id,
        agent=agent,
        agent_display_name=display_name,
        revision_no=revision_no,
        status=run.status.value,
        trigger=run.trigger.value,
        started_by=started_by,
        created_at=run.created_at,
        finished_at=run.finished_at,
        model=model,
        error=error,
        parent_execution_id=run.parent_execution_id,
    )


async def _status_counts(session: AsyncSession) -> Counter[ExecutionStatus]:
    rows = await session.execute(
        select(Execution.status, func.count(Execution.id)).group_by(Execution.status)
    )
    return Counter({status: count for status, count in rows.all()})


async def list_tasks(
    session: AsyncSession,
    *,
    group: TaskGroup,
    agent: str | None,
    trigger: ExecutionTrigger | None,
    limit: int,
) -> TaskList:
    query = _task_query().order_by(Execution.created_at.desc()).limit(limit)
    if group is not TaskGroup.ALL:
        query = query.where(Execution.status.in_(GROUPS[group]))
    if agent:
        query = query.where(Agent.name == agent)
    if trigger:
        query = query.where(Execution.trigger == trigger)
    statuses = await _status_counts(session)
    counts = {g.value: sum(statuses[s] for s in GROUPS[g]) for g in GROUPS}
    return TaskList(
        counts={TaskGroup.ALL.value: sum(statuses.values()), **counts},
        tasks=[_task(r) for r in (await session.execute(query)).all()],
    )


async def _policies(session: AsyncSession, since: datetime) -> PolicySummary:
    decisions = await session.execute(
        select(AuditEvent.decision, func.count(AuditEvent.id))
        .where(AuditEvent.occurred_at >= since, AuditEvent.decision.is_not(None))
        .group_by(AuditEvent.decision)
    )
    counts = {decision: count for decision, count in decisions.all()}
    reason = func.coalesce(AuditEvent.reason, "no reason recorded")
    reasons = await session.execute(
        select(reason, func.count(AuditEvent.id))
        .where(AuditEvent.occurred_at >= since, AuditEvent.decision == Decision.DENY)
        .group_by(reason)
        .order_by(func.count(AuditEvent.id).desc(), reason)
        .limit(TOP_REASONS)
    )
    return PolicySummary(
        allowed=counts.get(Decision.PERMIT, 0),
        denied=counts.get(Decision.DENY, 0),
        reasons=[DenyReason(reason=r, count=c) for r, c in reasons.all()],
    )


async def desk_summary(session: AsyncSession, manifest: Manifest | None = None) -> DeskSummary:
    now = utcnow()
    day, hour = now - timedelta(hours=24), now - timedelta(hours=1)
    statuses = await _status_counts(session)
    active = sum(statuses[s] for s in ACTIVE)
    running = await session.execute(
        _task_query()
        .where(Execution.status.in_(ACTIVE))
        .order_by(Execution.created_at.desc())
        .limit(DESK_TASKS)
    )
    faults = await session.execute(
        _task_query()
        .where(Execution.status.in_(FAULTS), Execution.created_at >= day)
        .order_by(Execution.created_at.desc())
    )
    failed = [_task(r) for r in faults.all()]
    by_agent: dict[str, Counter[str]] = {}
    for task in failed:
        by_agent.setdefault(task.agent, Counter())[task.status] += 1
    cost = await session.scalar(
        select(func.coalesce(func.sum(ExecutionAttempt.cost_usd), 0)).where(
            ExecutionAttempt.created_at >= day
        )
    )
    tasks = [_task(r) for r in running.all()]
    warning = await default_warning(session, manifest, await chosen(session)) if manifest else None
    return DeskSummary(
        default_warning=warning,
        running=statuses[ExecutionStatus.RUNNING],
        queued=statuses[ExecutionStatus.QUEUED],
        errors_hour=sum(1 for t in failed if t.created_at >= hour),
        cost_usd_24h=Decimal(cost or 0),
        tasks=tasks,
        more_tasks=active - len(tasks),
        nodes=[
            NodeOut(
                name=n.name, cpu_count=n.cpu_count, ram_gb=n.ram_gb, gpu=n.gpu, vram_gb=n.vram_gb
            )
            for n in (await session.scalars(select(Node).order_by(Node.name))).all()
        ],
        agent_errors=[
            AgentErrors(agent=a, total=sum(c.values()), by_status=dict(c))
            for a, c in sorted(by_agent.items(), key=lambda kv: -sum(kv[1].values()))
        ],
        policies=await _policies(session, day),
        error_log=[
            ErrorEntry(id=t.id, at=t.created_at, status=t.status, agent=t.agent, message=t.error)
            for t in failed[:ERROR_LOG]
        ],
    )
