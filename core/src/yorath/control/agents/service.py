"""Agent registry view for My Agents: permissions from the revision, Execution summary."""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.agents.loader import CONNECTED_RUNTIMES, is_builtin_path
from yorath.control.agents.model_choice import chosen
from yorath.control.agents.schemas import (
    AgentOut,
    CardOut,
    ExecutionOut,
    ExecutionSummary,
    OutcomeOut,
    PrincipalOut,
    RevisionOut,
    ToolOut,
)
from yorath.control.journal import ControlLevel
from yorath.control.model import (
    Agent,
    AgentRevision,
    Execution,
    ExecutionAttempt,
    Hosting,
    Outcome,
    Principal,
    RuntimeType,
    ServiceAccount,
)


def control_level_for(runtime_name: str, hosting: Hosting = Hosting.MANAGED) -> ControlLevel:
    """§6: the own loop means every call goes through the Gateway; an external runtime means the
    sandbox boundary; an external agent (endpoint client) and a connected agent with its own
    runtime (OpenClaw, Hermes) have model calls, agent calls and network under control, but not
    their tools."""
    if hosting is Hosting.EXTERNAL or runtime_name in CONNECTED_RUNTIMES:
        return ControlLevel.GATEWAY
    return ControlLevel.TOOL if runtime_name == "native" else ControlLevel.SANDBOX


def normalize_tools(tools: list[Any]) -> list[ToolOut]:
    """Header ``tools``: a string or ``{name: [targets]}`` → a list of ``{name, targets}``."""
    out: list[ToolOut] = []
    for tool in tools or []:
        if isinstance(tool, str):
            out.append(ToolOut(name=tool))
        elif isinstance(tool, dict):
            for name, targets in tool.items():
                out.append(ToolOut(name=str(name), targets=[str(t) for t in (targets or [])]))
    return out


def card_out(front_matter: dict[str, Any]) -> CardOut:
    model = str(front_matter.get("model", "local/default"))
    listed = [str(m) for m in front_matter.get("models") or []]
    return CardOut(
        model=model,
        models=listed if model in listed else [model, *listed],
        tools=normalize_tools(front_matter.get("tools") or []),
        data=[str(d) for d in front_matter.get("data") or []],
        max_data_class=str(front_matter.get("max_data_class", "internal")),
        triggers=[str(t) for t in front_matter.get("triggers") or ["chat"]],
    )


def _principal_out(p: Principal | None) -> PrincipalOut | None:
    if p is None:
        return None
    return PrincipalOut(id=p.id, display_name=p.display_name, kind=p.kind.value)


async def latest_revisions(session: AsyncSession) -> dict[uuid.UUID, AgentRevision]:
    rows = (
        await session.scalars(
            select(AgentRevision).order_by(AgentRevision.agent_id, desc(AgentRevision.revision_no))
        )
    ).all()
    latest: dict[uuid.UUID, AgentRevision] = {}
    for rev in rows:
        latest.setdefault(rev.agent_id, rev)
    return latest


async def _summaries(session: AsyncSession) -> dict[uuid.UUID, ExecutionSummary]:
    rows = (
        await session.execute(
            select(
                AgentRevision.agent_id,
                Execution.status,
                func.count(Execution.id),
                func.max(Execution.created_at),
            )
            .join(AgentRevision, AgentRevision.id == Execution.agent_revision_id)
            .group_by(AgentRevision.agent_id, Execution.status)
        )
    ).all()
    out: dict[uuid.UUID, ExecutionSummary] = {}
    for agent_id, status, count, last_at in rows:
        s = out.setdefault(agent_id, ExecutionSummary())
        s.total += count
        s.by_status[status.value] = count
        if s.last_at is None or last_at > s.last_at:
            s.last_at = last_at
    return out


async def _costs(session: AsyncSession) -> dict[uuid.UUID, Decimal]:
    rows = (
        await session.execute(
            select(AgentRevision.agent_id, func.coalesce(func.sum(ExecutionAttempt.cost_usd), 0))
            .join(Execution, Execution.id == ExecutionAttempt.execution_id)
            .join(AgentRevision, AgentRevision.id == Execution.agent_revision_id)
            .group_by(AgentRevision.agent_id)
        )
    ).all()
    return {agent_id: Decimal(cost or 0) for agent_id, cost in rows}


async def list_agents(session: AsyncSession) -> list[AgentOut]:
    agents = (await session.scalars(select(Agent).order_by(Agent.name))).all()
    runtimes = {r.id: r.name for r in (await session.scalars(select(RuntimeType))).all()}
    accounts = {a.agent_id: a for a in (await session.scalars(select(ServiceAccount))).all()}
    owner_ids = {a.owner_id for a in agents if a.owner_id}
    owners = (
        {
            p.id: p
            for p in (
                await session.scalars(select(Principal).where(Principal.id.in_(owner_ids)))
            ).all()
        }
        if owner_ids
        else {}
    )
    revisions = await latest_revisions(session)
    summaries = await _summaries(session)
    costs = await _costs(session)
    models = await chosen(session)
    out: list[AgentOut] = []
    for agent in agents:
        runtime = runtimes.get(agent.runtime_type_id, "native")
        rev = revisions.get(agent.id)
        out.append(
            AgentOut(
                id=agent.id,
                name=agent.name,
                display_name=agent.display_name,
                description=agent.description,
                hosting=agent.hosting.value,
                mode=agent.mode.value,
                enabled=agent.enabled,
                builtin=rev is None or is_builtin_path(rev.path),
                runtime=runtime,
                control_level=control_level_for(runtime, agent.hosting).value,
                owner=_principal_out(owners.get(agent.owner_id)) if agent.owner_id else None,
                service_account=_principal_out(accounts.get(agent.id)),
                revision=RevisionOut(
                    id=rev.id,
                    revision_no=rev.revision_no,
                    path=rev.path,
                    content_sha256=rev.content_sha256,
                    created_at=rev.created_at,
                    card=card_out(rev.front_matter),
                )
                if rev
                else None,
                executions=summaries.get(agent.id, ExecutionSummary()),
                cost_usd=costs.get(agent.id, Decimal(0)),
                model=models.get(agent.name),
            )
        )
    return out


async def recent_executions(
    session: AsyncSession, agent: Agent, limit: int = 20
) -> list[ExecutionOut]:
    """The agent's latest Executions across revisions; attempt #1 and Outcome via outer joins."""
    rows = (
        await session.execute(
            select(Execution, AgentRevision.revision_no, ExecutionAttempt, Outcome)
            .join(AgentRevision, AgentRevision.id == Execution.agent_revision_id)
            .outerjoin(
                ExecutionAttempt,
                (ExecutionAttempt.execution_id == Execution.id)
                & (ExecutionAttempt.attempt_no == 1),
            )
            .outerjoin(Outcome, Outcome.execution_id == Execution.id)
            .where(AgentRevision.agent_id == agent.id)
            .order_by(desc(Execution.created_at))
            .limit(limit)
        )
    ).all()
    ids = {e.initiated_by_principal_id for e, *_ in rows} | {
        e.effective_principal_id for e, *_ in rows
    }
    principals = (
        {
            p.id: p
            for p in (await session.scalars(select(Principal).where(Principal.id.in_(ids)))).all()
        }
        if ids
        else {}
    )
    out: list[ExecutionOut] = []
    for execution, revision_no, attempt, outcome in rows:
        out.append(
            ExecutionOut(
                id=execution.id,
                status=execution.status.value,
                trigger=execution.trigger.value,
                created_at=execution.created_at,
                finished_at=execution.finished_at,
                initiated_by=_principal_out(principals.get(execution.initiated_by_principal_id)),
                effective_principal=_principal_out(
                    principals.get(execution.effective_principal_id)
                ),
                revision_no=revision_no,
                model=attempt.model if attempt else None,
                usage=attempt.usage if attempt else None,
                cost_usd=attempt.cost_usd if attempt else None,
                cost_estimated=attempt.cost_estimated if attempt else False,
                max_data_class_seen=execution.max_data_class_seen.value
                if execution.max_data_class_seen
                else None,
                parent_execution_id=execution.parent_execution_id,
                outcome=OutcomeOut(
                    kind=outcome.kind.value,
                    status=outcome.status.value,
                    ref=outcome.ref,
                    useful=outcome.useful,
                )
                if outcome
                else None,
            )
        )
    return out
