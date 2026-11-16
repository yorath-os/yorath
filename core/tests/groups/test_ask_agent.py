"""Inter-agent route on a live database: real Cedar, fake Ollama and Tavily (criterion 5)."""

import pytest
from sqlalchemy import select

from tests.fixtures.corpus import AGENTS_DIR
from tests.fixtures.world import seed_web
from yorath.control.journal import AuditEvent, AuditEventType
from yorath.control.model import (
    AgentRevision,
    DataClass,
    Execution,
    ExecutionStatus,
    ExecutionTrigger,
    Outcome,
    User,
)
from yorath.enforcement.gateway import load_agent, open_context
from yorath.enforcement.gateway.agents import shift_citations

pytestmark = pytest.mark.group


async def parent_context(app, session):
    await app.state.secrets.put("tavily_api_key", "tvly-test-key")
    owner = await session.scalar(select(User).where(User.role == "owner"))
    agent, rev, card, body = await load_agent(session, "document-assistant", AGENTS_DIR)
    ctx = await open_context(
        session, app.state.journal, agent=agent, revision=rev, card=card, body=body,
        actor=owner, effective_principal=owner, trigger=ExecutionTrigger.USER_MESSAGE,
    )  # fmt: skip
    app.state.contexts.add(ctx)
    return ctx, owner


async def child_of(session, ctx) -> Execution:
    return await session.scalar(
        select(Execution).where(Execution.parent_execution_id == ctx.execution_id)
    )


@pytest.mark.covers("INV-AGT-2")
async def test_allowed_call_creates_child_execution(make_app, session, backends):
    api, app = await seed_web(session, make_app, backends)
    ctx, owner = await parent_context(app, session)
    ctx.raise_class(DataClass.INTERNAL)
    result = await app.state.tool_gateway.call_tool(
        ctx, session, "ask_agent", {"agent": "research-agent", "question": "how many vacation days?"},
        meta={"citation_offset": 3},
    )  # fmt: skip
    assert not result.is_error and "Vacation is 28 days [4]." in result.content
    assert result.citations[0].n == 4 and result.citations[0].url and len(backends.tavily) == 1
    assert result.data_class is DataClass.INTERNAL  # derived data is not below its source

    child = await child_of(session, ctx)
    assert (
        child.status is ExecutionStatus.SUCCEEDED and child.trigger is ExecutionTrigger.AGENT_CALL
    )
    assert child.initiated_by_principal_id == child.effective_principal_id == owner.id
    assert child.max_data_class_seen is DataClass.INTERNAL
    assert (await session.get(AgentRevision, child.agent_revision_id)).path == "research-agent.md"
    assert await session.scalar(select(Outcome).where(Outcome.execution_id == child.id))
    invoked = await session.scalar(select(AuditEvent).where(
        AuditEvent.execution_id == ctx.execution_id, AuditEvent.event_type == AuditEventType.AGENT_INVOKED))  # fmt: skip
    assert invoked.details["child_execution_id"] == str(child.id)
    events = (
        await session.scalars(select(AuditEvent).where(AuditEvent.execution_id == child.id))
    ).all()
    assert events and all(e.parent_execution_id == ctx.execution_id for e in events)
    await api.aclose()


@pytest.mark.covers("INV-AGT-1")
async def test_confidential_context_blocks_research_agent(make_app, session, backends):
    api, app = await seed_web(session, make_app, backends)
    ctx, _ = await parent_context(app, session)
    ctx.raise_class(DataClass.CONFIDENTIAL)
    result = await app.state.tool_gateway.call_tool(
        ctx, session, "ask_agent", {"agent": "research-agent", "question": "secret"}
    )
    assert result.is_error and result.meta["denied"] and "confidential" in result.content
    assert backends.tavily == [] and backends.chat == []  # the agent did not run
    child = await child_of(session, ctx)
    assert child.status is ExecutionStatus.DENIED and child.finished_at is not None
    assert child.max_data_class_seen is DataClass.CONFIDENTIAL
    denied = await session.scalar(select(AuditEvent).where(
        AuditEvent.execution_id == ctx.execution_id, AuditEvent.event_type == AuditEventType.POLICY_DENIED))  # fmt: skip
    assert (
        denied.tool == "ask_agent" and denied.data_class is DataClass.CONFIDENTIAL and denied.reason
    )
    await api.aclose()


@pytest.mark.covers("INV-AGT-3")
async def test_target_outside_card_is_denied(make_app, session, backends):
    api, app = await seed_web(session, make_app, backends)
    ctx, _ = await parent_context(app, session)
    result = await app.state.tool_gateway.call_tool(
        ctx, session, "ask_agent", {"agent": "document-assistant", "question": "to myself"}
    )
    assert result.is_error and result.meta["denied"] and backends.chat == []
    unknown = await app.state.tool_gateway.call_tool(
        ctx, session, "ask_agent", {"agent": "nobody", "question": "who"}
    )
    assert unknown.is_error and "unavailable" in unknown.content
    await api.aclose()


def test_shift_citations():
    assert shift_citations("see [1] and [12], [x]", 3) == "see [4] and [15], [x]"
