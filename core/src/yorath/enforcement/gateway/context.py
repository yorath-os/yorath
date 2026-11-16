"""The Execution context is the only "currency" of the Gateway: who, through which agent, with which
class.

Creates the Execution and its attempt, accumulates usage and data class, finalizes any outcome
and closes Executions interrupted by a core restart.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.agents.loader import AgentCard, parse_agent_file
from yorath.control.agents.model_choice import agent_model, with_model
from yorath.control.journal import AuditEventType, ControlLevel, Journal
from yorath.control.model import (
    Agent,
    AgentRevision,
    AttemptStatus,
    DataClass,
    Execution,
    ExecutionAttempt,
    ExecutionStatus,
    ExecutionTrigger,
    ModelResource,
    Principal,
)
from yorath.db import utcnow
from yorath.enforcement.gateway.errors import GatewayError
from yorath.enforcement.gateway.tokens import revoke_execution_tokens

ATTEMPT_STATUS = {
    ExecutionStatus.SUCCEEDED: AttemptStatus.SUCCEEDED,
    ExecutionStatus.DENIED: AttemptStatus.FAILED,
    ExecutionStatus.FAILED: AttemptStatus.FAILED,
    ExecutionStatus.TIMEOUT: AttemptStatus.TIMEOUT,
    ExecutionStatus.CONFIG_ERROR: AttemptStatus.CONFIG_ERROR,
    ExecutionStatus.CANCELLED: AttemptStatus.CANCELLED,
}
UNFINISHED = (ExecutionStatus.CREATED, ExecutionStatus.QUEUED, ExecutionStatus.RUNNING)


@dataclass
class ExecutionContext:
    execution: Execution
    attempt: ExecutionAttempt
    actor: Principal
    effective_principal: Principal
    agent: Agent
    revision: AgentRevision
    card: AgentCard
    body: str
    control_level: ControlLevel
    max_data_class_seen: DataClass | None = None
    root_span_id: str | None = None
    unavailable_tools: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: Decimal = Decimal(0)
    model: ModelResource | None = None
    finished: bool = False
    # False: the trace gets no prompt, answer or call arguments (model endpoint: client text)
    record_content: bool = True

    @property
    def execution_id(self) -> uuid.UUID:
        return self.execution.id

    @property
    def context_class(self) -> DataClass:
        return self.max_data_class_seen or DataClass.PUBLIC

    def raise_class(self, data_class: DataClass | None) -> bool:
        """The label follows the data: the class only grows. Returns whether it changed."""
        if data_class is None or (
            self.max_data_class_seen is not None and data_class <= self.max_data_class_seen
        ):
            return False
        self.max_data_class_seen = data_class
        return True

    def add_usage(self, usage: dict[str, Any], cost: Decimal) -> None:
        for key in ("input_tokens", "output_tokens"):
            if usage.get(key):
                self.usage[key] = self.usage.get(key, 0) + int(usage[key])
        self.usage["calls"] = self.usage.get("calls", 0) + 1
        self.cost_usd += cost

    def audit_fields(self) -> dict[str, Any]:
        return dict(
            execution_id=self.execution.id,
            actor_principal_id=self.actor.id,
            effective_principal_id=self.effective_principal.id,
            agent_id=self.agent.id,
            agent_revision_id=self.revision.id,
            runtime_type=self.card.runtime,
            control_level=self.control_level,
            parent_execution_id=self.execution.parent_execution_id,
        )


async def load_agent(
    session: AsyncSession, name: str, agents_dir: Path, *, allow_disabled: bool = False
) -> tuple[Agent, AgentRevision, AgentCard, str]:
    """Agent, latest revision and the parsed md file; errors are ``GatewayError`` with a code.

    ``allow_disabled`` is for a denial that must stay a ``denied`` Execution in the journal
    (a model call by a disabled connected agent).
    """
    agent = await session.scalar(select(Agent).where(Agent.name == name))
    if agent is None:
        raise GatewayError("agent_not_found", f"agent {name} is not registered")
    if not agent.enabled and not allow_disabled:
        raise GatewayError("agent_disabled", f"agent {name} is disabled by the owner")
    rev = await session.scalar(
        select(AgentRevision)
        .where(AgentRevision.agent_id == agent.id)
        .order_by(AgentRevision.revision_no.desc())
        .limit(1)
    )
    if rev is None:
        raise GatewayError("agent_not_found", f"agent {name} has no revision")
    path = agents_dir / rev.path
    if not path.is_file():
        raise GatewayError("agent_file_missing", f"agent file {rev.path} not found")
    parsed = parse_agent_file(path)
    # the model chosen in the agent's settings (field-fixes-0-7) replaces the md file's default
    card = with_model(parsed.card, await agent_model(session, name))
    return agent, rev, card, parsed.body.strip()


async def open_context(
    session: AsyncSession,
    journal: Journal,
    *,
    agent: Agent,
    revision: AgentRevision,
    card: AgentCard,
    body: str,
    actor: Principal,
    effective_principal: Principal,
    trigger: ExecutionTrigger,
    control_level: ControlLevel = ControlLevel.TOOL,
    parent: Execution | None = None,
    api_key: Any = None,
) -> ExecutionContext:
    """A ``running`` Execution with one attempt; a child inherits the parent class (§9)."""
    execution = Execution(
        agent_revision_id=revision.id,
        initiated_by_principal_id=actor.id,
        effective_principal_id=effective_principal.id,
        parent_execution_id=parent.id if parent else None,
        trigger=trigger,
        status=ExecutionStatus.RUNNING,
        max_data_class_seen=parent.max_data_class_seen if parent else None,
        api_key_id=api_key.id if api_key is not None else None,
    )
    session.add(execution)
    await session.flush()
    attempt = ExecutionAttempt(
        execution_id=execution.id,
        attempt_no=1,
        status=AttemptStatus.RUNNING,
        started_at=utcnow(),
    )
    session.add(attempt)
    await session.commit()
    ctx = ExecutionContext(
        execution=execution,
        attempt=attempt,
        actor=actor,
        effective_principal=effective_principal,
        agent=agent,
        revision=revision,
        card=card,
        body=body,
        control_level=control_level,
        max_data_class_seen=execution.max_data_class_seen,
    )
    # which key the /v1 call came with: name and visible prefix, not the value
    extra = {"details": {"api_key": f"{api_key.name} ({api_key.prefix}…)"}} if api_key else {}
    await journal.audit(AuditEventType.EXECUTION_STARTED, **ctx.audit_fields(), **extra)
    return ctx


async def finish(
    ctx: ExecutionContext,
    session: AsyncSession,
    journal: Journal,
    status: ExecutionStatus,
    *,
    error: str | None = None,
) -> None:
    """Closes the attempt and the Execution with accumulated usage, cost and class; idempotent."""
    if ctx.finished:
        return
    ctx.finished = True
    now = utcnow()
    await session.execute(
        update(ExecutionAttempt)
        .where(ExecutionAttempt.id == ctx.attempt.id)
        .values(
            status=ATTEMPT_STATUS.get(status, AttemptStatus.FAILED),
            finished_at=now,
            usage=ctx.usage or None,
            cost_usd=ctx.cost_usd,
            cost_estimated=True,
            error_message=(error or None) and str(error)[:2000],
            model_resource_id=ctx.model.id if ctx.model else None,
            model=ctx.model.model_id if ctx.model else None,
        )
    )
    await session.execute(
        update(Execution)
        .where(Execution.id == ctx.execution.id)
        .values(status=status, finished_at=now, max_data_class_seen=ctx.max_data_class_seen)
    )
    await session.commit()
    await revoke_execution_tokens(session, journal, ctx.audit_fields(), ctx.execution_id)
    await journal.audit(
        AuditEventType.EXECUTION_FINISHED,
        **ctx.audit_fields(),
        cost_usd=ctx.cost_usd,
        details={"status": status.value, "usage": ctx.usage, "error": error},
    )


class ContextRegistry:
    """Active contexts of the process: core tools find their Execution through ``meta``."""

    def __init__(self) -> None:
        self._items: dict[uuid.UUID, ExecutionContext] = {}

    def add(self, ctx: ExecutionContext) -> None:
        self._items[ctx.execution_id] = ctx

    def get(self, execution_id: uuid.UUID) -> ExecutionContext | None:
        return self._items.get(execution_id)

    def remove(self, execution_id: uuid.UUID) -> None:
        self._items.pop(execution_id, None)

    def __len__(self) -> int:
        return len(self._items)


SWEEP_REASON = "interrupted by a core restart"


async def sweep_unfinished(session: AsyncSession, journal: Journal | None = None) -> int:
    """On core start: anything left ``running`` by the previous process becomes ``cancelled``."""
    rows = (await session.scalars(select(Execution).where(Execution.status.in_(UNFINISHED)))).all()
    now = utcnow()
    for execution in rows:
        execution.status, execution.finished_at = ExecutionStatus.CANCELLED, now
        await session.execute(
            update(ExecutionAttempt)
            .where(
                ExecutionAttempt.execution_id == execution.id,
                ExecutionAttempt.status == AttemptStatus.RUNNING,
            )
            .values(status=AttemptStatus.CANCELLED, finished_at=now, error_message=SWEEP_REASON)
        )
    await session.commit()
    if journal is not None:
        for execution in rows:
            await journal.audit(
                AuditEventType.EXECUTION_FINISHED,
                execution_id=execution.id,
                actor_principal_id=execution.initiated_by_principal_id,
                effective_principal_id=execution.effective_principal_id,
                details={"status": ExecutionStatus.CANCELLED.value, "error": SWEEP_REASON},
            )
    return len(rows)
