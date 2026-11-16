"""Calling an agent with an API key (change agent-api): the agent loop without a Conversation.

Stateless Chat Completions protocol: the client sends the history, no conversation is created in
the panel. Execution, spans, Outcome and data class are as for a chat answer
(``runtime/chat/service.answer``); chat keeps its own copy of this run for now because its
cancellation is handled by ``ChatRuns``. The caller closes the Execution: it can see whether the
client dropped the stream.
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.journal import AuditEventType, SpanKind
from yorath.control.journal.events import agent_attributes
from yorath.control.model import ExecutionStatus, Message, MessageRole, Outcome, OutcomeKind
from yorath.enforcement.gateway import ExecutionContext
from yorath.enforcement.gateway.models import context_window
from yorath.runtime.agent_loop import Failure, Final, LoopDeps, Token, run
from yorath.runtime.chat.service import ChatDeps

PREFIX = "agent/"
_HISTORY_ROLES = {"user": MessageRole.USER, "assistant": MessageRole.ASSISTANT}


class BadMessages(ValueError):
    """No task: ``messages`` is empty or does not end with a ``user`` message."""


def _text(raw: dict[str, Any]) -> str:
    content = raw.get("content")
    if isinstance(content, list):  # OpenAI allows a list of parts; take the text ones
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content or "")


def split_messages(messages: list[dict[str, Any]]) -> tuple[str, list[Message]]:
    """The task is the last ``user`` message; the history is the earlier ``user``/``assistant``
    ones.

    Client ``system`` and ``tool`` messages are dropped: instructions and tools come from the agent
    card.
    History messages are not added to the session and are not written to the database.
    """
    if not messages or messages[-1].get("role") != "user" or not _text(messages[-1]).strip():
        raise BadMessages("the last message in messages must be from user and contain text")
    history = [
        Message(role=_HISTORY_ROLES[m["role"]], content=_text(m))
        for m in messages[:-1]
        if m.get("role") in _HISTORY_ROLES and _text(m)
    ]
    return _text(messages[-1]), history


@dataclass
class AgentResult:
    status: ExecutionStatus = ExecutionStatus.SUCCEEDED
    error: str | None = None
    code: str | None = None
    text: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)


async def run_agent(
    ctx: ExecutionContext,
    session: AsyncSession,
    deps: ChatDeps,
    task: str,
    history: list[Message],
) -> AsyncGenerator[str | AgentResult]:
    """Pieces of the answer text, ending with exactly one ``AgentResult``. The Outcome is written
    here."""
    window = await context_window(session, ctx.card.model, deps.num_ctx)
    loop_deps = LoopDeps(tools=deps.tools, models=deps.models, settings=deps.loop, num_ctx=window)
    result, streamed = AgentResult(), ""
    try:
        async with deps.journal.span(
            ctx.execution_id,
            f"execution {ctx.agent.name}",
            SpanKind.EXECUTION,
            attempt_id=ctx.attempt.id,
            agent_id=ctx.agent.id,
            attributes=agent_attributes(ctx.agent.name),
        ) as root:
            ctx.root_span_id = root.span_id
            async for ev in run(ctx, session, loop_deps, task, history):
                if isinstance(ev, Token):
                    streamed += ev.text
                    yield ev.text
                elif isinstance(ev, Final):
                    result.text = ev.text
                    result.citations = [c.model_dump() for c in ev.all_citations]
                    root.attributes["yorath.loop.no_tools"] = ev.no_tools
                    root.attributes["yorath.loop.steps"] = ev.steps
                    root.attributes["yorath.loop.tool_results_shortened"] = (
                        ev.tool_results_shortened
                    )
                    root.attributes["yorath.loop.tool_results_removed"] = ev.tool_results_removed
                    # the first step with tools is buffered (nudge): send what has not gone out yet
                    if ev.text.startswith(streamed) and len(ev.text) > len(streamed):
                        yield ev.text[len(streamed) :]
                elif isinstance(ev, Failure):
                    result.status, result.error = ev.status, ev.message
                    result.code = "model_denied" if ev.status is ExecutionStatus.DENIED else ev.code
            root.attributes["yorath.tools.unavailable"] = ctx.unavailable_tools
    except Exception as exc:  # noqa: BLE001 — the outcome must reach the database, not get lost
        result.status, result.error, result.code = ExecutionStatus.FAILED, str(exc), "model_error"
    if result.status is ExecutionStatus.SUCCEEDED:
        session.add(
            Outcome(
                execution_id=ctx.execution_id,
                kind=OutcomeKind.ANSWER,
                data={
                    "kind": "answer",
                    "text": result.text,
                    "citations": [c["n"] for c in result.citations],
                },
            )
        )
        await session.commit()
        await deps.journal.audit(
            AuditEventType.OUTCOME_RECORDED, **ctx.audit_fields(), details={"kind": "answer"}
        )
    yield result


__all__ = ["PREFIX", "AgentResult", "BadMessages", "run_agent", "split_messages"]
