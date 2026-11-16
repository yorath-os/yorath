"""Journal on a live database: spans, audit, the immutability trigger, retention."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from tests.fixtures.world import make_agent, make_user
from yorath.control.journal import (
    AuditEvent,
    AuditEventType,
    ControlLevel,
    Decision,
    GenAI,
    Journal,
    SpanKind,
    SpanStatus,
    TraceSpan,
    purge_traces,
)
from yorath.control.journal.events import llm_attributes
from yorath.control.model import (
    AgentMode,
    DataClass,
    Execution,
    ExecutionTrigger,
)
from yorath.db import utcnow

pytestmark = pytest.mark.group


async def make_execution(session) -> Execution:
    anna = await make_user(session, "anna-j")
    rev = await make_agent(session, "da-j", AgentMode.DELEGATED)
    e = Execution(
        agent_revision_id=rev.id,
        initiated_by_principal_id=anna.id,
        effective_principal_id=anna.id,
        trigger=ExecutionTrigger.USER_MESSAGE,
    )
    session.add(e)
    await session.commit()
    return e


async def test_span_records_start_end_and_attributes(sessions, session):
    e = await make_execution(session)
    journal = Journal(sessions)
    async with journal.span(
        e.id,
        "chat gemma4:e2b",
        SpanKind.LLM,
        model="gemma4:e2b",
        attributes=llm_attributes(provider="ollama", request_model="gemma4:e2b"),
    ) as span:
        span.attributes[GenAI.INPUT_TOKENS] = 10
        span.attributes[GenAI.OUTPUT_TOKENS] = 3
    async with journal.span(e.id, "search_docs", SpanKind.TOOL, parent_span_id=span.span_id):
        pass

    rows = (
        await session.scalars(
            select(TraceSpan).where(TraceSpan.execution_id == e.id).order_by(TraceSpan.started_at)
        )
    ).all()
    assert len(rows) == 2 and all(r.trace_id == e.id for r in rows)
    llm, tool = rows
    assert llm.status is SpanStatus.OK and llm.ended_at is not None
    assert llm.model == llm.attributes[GenAI.REQUEST_MODEL] == "gemma4:e2b"
    assert llm.attributes[GenAI.INPUT_TOKENS] == 10
    assert tool.parent_span_id == llm.span_id and len(tool.span_id) == 16


async def test_span_marks_error_on_exception(sessions, session):
    e = await make_execution(session)
    journal = Journal(sessions)
    with pytest.raises(RuntimeError):
        async with journal.span(e.id, "boom", SpanKind.TOOL):
            raise RuntimeError("tool failed")
    row = await session.scalar(select(TraceSpan).where(TraceSpan.execution_id == e.id))
    assert row.status is SpanStatus.ERROR and row.ended_at is not None


@pytest.mark.covers("INV-AUD-3")
async def test_audit_policy_denied_has_chain_fields(sessions, session):
    e = await make_execution(session)
    journal = Journal(sessions)
    await journal.audit(
        AuditEventType.POLICY_DENIED,
        execution_id=e.id,
        actor_principal_id=e.initiated_by_principal_id,
        effective_principal_id=e.effective_principal_id,
        agent_revision_id=e.agent_revision_id,
        tool="ask_agent",
        data_class=DataClass.CONFIDENTIAL,
        decision=Decision.DENY,
        reason="class Confidential is above the allowed Internal for research-agent",
        control_level=ControlLevel.TOOL,
    )
    row = await session.scalar(select(AuditEvent).where(AuditEvent.execution_id == e.id))
    assert row.event_type is AuditEventType.POLICY_DENIED and row.decision is Decision.DENY
    assert row.effective_principal_id == e.effective_principal_id and "Confidential" in row.reason
    assert row.details == {}


@pytest.mark.covers("INV-AUD-1")
async def test_audit_is_append_only(sessions, session):
    eid = (await make_execution(session)).id  # the rollback below resets the object attributes
    await Journal(sessions).audit(AuditEventType.EXECUTION_STARTED, execution_id=eid)
    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(
            text("update audit_events set reason = 'x' where execution_id = :id"), {"id": eid}
        )
    await session.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(
            text("delete from audit_events where execution_id = :id"), {"id": eid}
        )
    await session.rollback()
    assert (
        await session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.execution_id == eid)
        )
        == 1
    )


async def test_purge_traces_keeps_recent_spans_and_audit(sessions, session):
    e = await make_execution(session)
    old = TraceSpan(
        trace_id=e.id,
        span_id="00" * 8,
        name="old",
        kind=SpanKind.EXECUTION,
        execution_id=e.id,
        started_at=utcnow() - timedelta(days=40),
    )
    fresh = TraceSpan(
        trace_id=e.id, span_id="11" * 8, name="fresh", kind=SpanKind.EXECUTION, execution_id=e.id
    )
    session.add_all([old, fresh])
    await session.commit()
    await Journal(sessions).audit(AuditEventType.EXECUTION_FINISHED, execution_id=e.id)

    removed = await purge_traces(session, retention_days=30)
    assert removed >= 1
    left = (
        await session.scalars(select(TraceSpan.name).where(TraceSpan.execution_id == e.id))
    ).all()
    assert left == ["fresh"]
    assert (
        await session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.execution_id == e.id)
        )
        == 1
    )
