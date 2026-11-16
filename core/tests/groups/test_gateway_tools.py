"""ToolGateway on a live database: header ∩ registry, PDP use_tool, search_docs through MCP, label, sweep."""

import uuid

import pytest
from mcp.server.mcpserver import MCPServer
from sqlalchemy import select

from tests.fixtures.world import open_for_owner, seed_chat
from yorath.control.journal import AuditEvent, AuditEventType, SpanKind, SpanStatus, TraceSpan
from yorath.control.model import (
    AttemptStatus,
    DataClass,
    Execution,
    ExecutionAttempt,
    ExecutionStatus,
)
from yorath.enforcement.gateway import finish, sweep_unfinished
from yorath.runtime.tools.types import ToolResult

pytestmark = pytest.mark.group


def echo_server() -> MCPServer:
    srv = MCPServer("echo")

    @srv.tool(name="echo")
    async def echo(text: str) -> ToolResult:
        """Returns the text."""
        return ToolResult(content=text, data_class=DataClass.RESTRICTED)

    return srv


async def test_list_tools_is_card_intersect_registry(make_app, session, tmp_path):
    api, app = await seed_chat(session, make_app, tmp_path)
    ctx = await open_for_owner(app, session)
    tools = await app.state.tool_gateway.list_tools(ctx)
    assert [t["function"]["name"] for t in tools] == ["search_docs", "query_records", "ask_agent"]
    schema = tools[0]["function"]["parameters"]
    assert schema["required"] == ["query"] and "title" not in schema
    assert "numbered passages" in tools[0]["function"]["description"]
    assert ctx.unavailable_tools == []
    await api.aclose()


@pytest.mark.covers("INV-CLS-1", "INV-CLS-2", "INV-AUD-2")
async def test_search_docs_through_gateway(make_app, session, tmp_path):
    api, app = await seed_chat(session, make_app, tmp_path)
    ctx = await open_for_owner(app, session)
    result = await app.state.tool_gateway.call_tool(
        ctx, session, "search_docs", {"query": "How many vacation days can I carry over?"}
    )
    assert not result.is_error and result.content.startswith("[1] hr-policy.md › ")
    assert "56 days" in result.content and "350000" not in result.content
    assert result.citations[0].heading_path == "Vacation policy > Carryover"
    assert result.assets_denied == ["hr-salaries"] and result.data_class is DataClass.INTERNAL
    assert ctx.max_data_class_seen is DataClass.INTERNAL
    execution = await session.get(Execution, ctx.execution_id)
    await session.refresh(execution)
    assert execution.max_data_class_seen is DataClass.INTERNAL

    spans = (
        await session.scalars(select(TraceSpan).where(TraceSpan.execution_id == ctx.execution_id))
    ).all()
    tool_span = next(s for s in spans if s.kind is SpanKind.TOOL)
    assert tool_span.name == "search_docs" and tool_span.status is SpanStatus.OK
    assert tool_span.attributes["yorath.hits"] == 1 and tool_span.attributes[
        "yorath.assets.denied"
    ] == ["hr-salaries"]

    audit = (
        await session.scalars(select(AuditEvent).where(AuditEvent.execution_id == ctx.execution_id))
    ).all()
    types = [a.event_type for a in audit]
    assert AuditEventType.TOOL_CALLED in types and AuditEventType.DATA_ACCESSED in types
    assert AuditEventType.POLICY_DENIED in types  # hr-salaries is not given to the agent
    called = next(a for a in audit if a.event_type is AuditEventType.TOOL_CALLED)
    assert called.tool == "search_docs" and called.data_class is DataClass.INTERNAL
    assert called.details["hits"] == 1 and "56" not in str(called.details)

    # an empty result is honest text, the class does not change
    empty = await app.state.tool_gateway.call_tool(
        ctx, session, "search_docs", {"query": "qwertyuiop"}
    )
    assert (
        not empty.is_error and "do not have the answer" in empty.content and empty.citations == []
    )
    await api.aclose()


@pytest.mark.covers("INV-TOOL-1")
async def test_tool_outside_card_is_denied_by_pdp(make_app, session, tmp_path):
    api, app = await seed_chat(session, make_app, tmp_path)
    app.state.tools.register("echo", echo_server())
    ctx = await open_for_owner(app, session)
    assert [t["function"]["name"] for t in await app.state.tool_gateway.list_tools(ctx)] == [
        "search_docs",
        "query_records",
        "ask_agent",
    ]
    result = await app.state.tool_gateway.call_tool(ctx, session, "echo", {"text": "secret"})
    assert result.is_error and "denied by policy" in result.content and result.meta["denied"]
    assert ctx.max_data_class_seen is None  # the tool did not run, the label was not raised
    denied = (await session.scalars(select(AuditEvent).where(
        AuditEvent.execution_id == ctx.execution_id, AuditEvent.event_type == AuditEventType.POLICY_DENIED
    ))).all()  # fmt: skip
    assert denied and denied[0].tool == "echo" and "agent-tool-not-allowed" in denied[0].reason
    assert not any(s.kind is SpanKind.TOOL for s in (await session.scalars(
        select(TraceSpan).where(TraceSpan.execution_id == ctx.execution_id))).all())  # fmt: skip

    unknown = await app.state.tool_gateway.call_tool(ctx, session, "nope", {})
    assert unknown.is_error and "unavailable" in unknown.content
    await api.aclose()


@pytest.mark.covers("INV-EXEC-1")
async def test_finish_and_sweep(make_app, session, tmp_path):
    api, app = await seed_chat(session, make_app, tmp_path)
    ctx = await open_for_owner(app, session)
    ctx.add_usage({"input_tokens": 10, "output_tokens": 5}, cost=ctx.cost_usd)
    ctx.add_usage({"input_tokens": 7, "output_tokens": 1}, cost=ctx.cost_usd)
    await finish(ctx, session, app.state.journal, ExecutionStatus.FAILED, error="test")
    await finish(ctx, session, app.state.journal, ExecutionStatus.SUCCEEDED)  # idempotent
    attempt = await session.scalar(
        select(ExecutionAttempt).where(ExecutionAttempt.execution_id == ctx.execution_id)
    )
    assert attempt.status is AttemptStatus.FAILED and attempt.error_message == "test"
    assert attempt.usage == {"input_tokens": 17, "output_tokens": 6, "calls": 2}
    execution = await session.get(Execution, ctx.execution_id)
    await session.refresh(execution)
    assert execution.status is ExecutionStatus.FAILED and execution.finished_at is not None

    stuck = await open_for_owner(app, session)
    assert (await session.get(Execution, stuck.execution_id)).status is ExecutionStatus.RUNNING
    assert await sweep_unfinished(session, app.state.journal) == 1
    await session.refresh(stuck.execution)
    assert stuck.execution.status is ExecutionStatus.CANCELLED
    attempt = await session.scalar(
        select(ExecutionAttempt).where(ExecutionAttempt.execution_id == stuck.execution_id)
    )
    assert attempt.status is AttemptStatus.CANCELLED and "core restart" in attempt.error_message
    assert await sweep_unfinished(session) == 0
    assert uuid.UUID(str(stuck.execution_id)) == stuck.execution.id
    await api.aclose()
