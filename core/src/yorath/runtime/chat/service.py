"""Answering a question as an Execution: agent loop through the Gateway, SSE events, messages,
Outcome.

A core task (``run_answer``) runs ``answer``, not the request: a client disconnect does not cancel
the answer.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yorath.control.journal import AuditEventType, Journal, SpanKind
from yorath.control.journal.events import agent_attributes
from yorath.control.model import (
    Agent,
    Conversation,
    Execution,
    ExecutionStatus,
    ExecutionTrigger,
    Message,
    MessageRole,
    Outcome,
    OutcomeKind,
    User,
)
from yorath.db import utcnow
from yorath.distribution.manifest import AgentLoopSettings
from yorath.enforcement.gateway import (
    ContextRegistry,
    ExecutionContext,
    GatewayError,
    ModelGateway,
    ToolGateway,
    finish,
    load_agent,
    open_context,
)
from yorath.enforcement.gateway.models import context_window
from yorath.runtime.agent_loop import (
    Citations,
    Failure,
    Final,
    LoopDeps,
    Token,
    ToolFinished,
    ToolStarted,
    Waiting,
    run,
)
from yorath.runtime.chat.runs import STOPPED_BY_CORE, Run

AGENT_NAME = "document-assistant"
STOPPED_TEXT = "Answer stopped."
FAILURE_TEXT = {
    "max_steps": "Could not get an answer: the agent step limit was exceeded.",
    "timeout": "Could not get an answer: the wait timed out.",
}


class ChatError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ConnectedDeps:
    """For a chat with a connected agent: HTTP client, secret store, manifest catalog."""

    http: Any
    store: Any
    specs: dict[str, Any]


@dataclass
class ChatDeps:
    journal: Journal
    contexts: ContextRegistry
    tools: ToolGateway
    models: ModelGateway
    loop: AgentLoopSettings
    agents_dir: Path
    sessions: async_sessionmaker[AsyncSession]
    num_ctx: int = 4096  # manifest ollama.context_length: more and the runner dies at a 6 GB limit
    connected: ConnectedDeps | None = None


Event = tuple[str, dict[str, Any]]


async def answer(
    session: AsyncSession,
    deps: ChatDeps,
    user: User,
    conversation: Conversation,
    question: str,
    model: str | None = None,
    on_open: Callable[[ExecutionContext], None] | None = None,
) -> AsyncIterator[Event]:
    """SSE events: tool* / citations* → token* → done | error. Any outcome is recorded in the
    database.

    Cancellation (stopped by the user or the core) is finalized by the caller: ``on_open`` gives it
    the Execution context.
    """
    try:
        owner = await session.get(Agent, conversation.agent_id)
        name = owner.name if owner else AGENT_NAME
        agent, rev, card, system = await load_agent(session, name, deps.agents_dir)
    except GatewayError as exc:
        raise ChatError(exc.code, str(exc)) from exc
    history = list(
        (
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at)
            )
        ).all()
    )
    session.add(Message(conversation_id=conversation.id, role=MessageRole.USER, content=question))
    ctx = await open_context(
        session,
        deps.journal,
        agent=agent,
        revision=rev,
        card=card,
        body=system,
        actor=user,
        effective_principal=user,
        trigger=ExecutionTrigger.USER_MESSAGE,
    )
    deps.contexts.add(ctx)
    if on_open is not None:
        on_open(ctx)
    # The conversation history goes into the prompt: its class is the context class from the first
    # model call, otherwise an answer from a Confidential document would reach an external model
    # after a model switch in the same conversation.
    seen = await session.scalars(
        select(Execution.max_data_class_seen)
        .join(Message, Message.execution_id == Execution.id)
        .where(
            Message.conversation_id == conversation.id,
            Execution.max_data_class_seen.is_not(None),
        )
    )
    for data_class in seen.all():
        ctx.raise_class(data_class)
    journal = deps.journal
    window = await context_window(session, model or ctx.card.model, deps.num_ctx)
    loop_deps = LoopDeps(tools=deps.tools, models=deps.models, settings=deps.loop, num_ctx=window)

    text, citations = "", []
    final = ExecutionStatus.SUCCEEDED
    error: str | None = None
    error_event: Event | None = None
    outcome = False
    try:
        spec = deps.connected.specs.get(agent.name) if deps.connected else None
        if spec is not None and deps.connected is not None:
            # connected agent: the history goes to its container endpoint (connected-agents)
            msgs = [{"role": m.role.value, "content": m.content} for m in history]
            msgs.append({"role": "user", "content": question})
            from yorath.runtime.connected.invoke import run_connected  # import cycle via ChatDeps

            result = None
            async for part in run_connected(
                ctx, session, deps, deps.connected.http, deps.connected.store, spec, msgs
            ):
                if isinstance(part, str):
                    text += part
                    yield ("token", {"text": part})
                else:
                    result = part
            if result is not None and result.status is not ExecutionStatus.SUCCEEDED:
                final, error = result.status, result.error
                text = f"The agent did not answer: {result.error}"
                error_event = ("error", {"code": result.code, "message": result.error})
        else:
            async with journal.span(
                ctx.execution_id,
                f"execution {agent.name}",
                SpanKind.EXECUTION,
                attempt_id=ctx.attempt.id,
                agent_id=agent.id,
                attributes={
                    **agent_attributes(agent.name),
                    "gen_ai.conversation.id": str(conversation.id),
                },
            ) as root:
                ctx.root_span_id = root.span_id
                async for ev in run(ctx, session, loop_deps, question, history, model=model):
                    if isinstance(ev, ToolStarted):
                        yield (
                            "tool",
                            {"name": ev.name, "status": "started", "arguments": ev.arguments},
                        )
                    elif isinstance(ev, ToolFinished):
                        yield ("tool", ev.model_dump())
                    elif isinstance(ev, Citations):
                        citations = [c.model_dump() for c in ev.citations]
                        yield ("citations", {"citations": citations, "denied": ev.denied})
                    elif isinstance(ev, Waiting):
                        # every model server is busy: the answer waits in the model's queue
                        yield ("queued", {"ahead": ev.ahead})
                    elif isinstance(ev, Token):
                        text += ev.text
                        yield ("token", {"text": ev.text})
                    elif isinstance(ev, Final):
                        text, outcome = ev.text, True
                        citations = [c.model_dump() for c in ev.all_citations]
                        root.attributes["yorath.loop.no_tools"] = ev.no_tools
                        root.attributes["yorath.loop.steps"] = ev.steps
                        root.attributes["yorath.loop.tool_results_shortened"] = (
                            ev.tool_results_shortened
                        )
                        root.attributes["yorath.loop.tool_results_removed"] = (
                            ev.tool_results_removed
                        )
                    elif isinstance(ev, Failure):
                        final, error = ev.status, ev.message
                        code = "model_denied" if ev.status is ExecutionStatus.DENIED else ev.code
                        text = (
                            f"Denied by policy: {ev.message}."
                            if ev.status is ExecutionStatus.DENIED
                            else FAILURE_TEXT.get(ev.code, f"Model error: {ev.message}")
                        )
                        error_event = ("error", {"code": code, "message": ev.message})
                root.attributes["yorath.tools.unavailable"] = ctx.unavailable_tools
    except Exception as exc:  # noqa: BLE001 — the outcome must reach the database, not get lost
        final, error = ExecutionStatus.FAILED, str(exc)
        text = f"Model error: {exc}"
        error_event = ("error", {"code": "model_error", "message": str(exc)})
    finally:
        deps.contexts.remove(ctx.execution_id)

    # recording the outcome: always a message, an Outcome only when the model answered
    session.add(
        Message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content=text,
            execution_id=ctx.execution_id,
            citations=citations,
        )
    )
    if outcome and final is ExecutionStatus.SUCCEEDED:
        session.add(
            Outcome(
                execution_id=ctx.execution_id,
                kind=OutcomeKind.ANSWER,
                data={"kind": "answer", "text": text, "citations": [c["n"] for c in citations]},
            )
        )
    conversation.updated_at = utcnow()
    if conversation.title == "New conversation":
        conversation.title = question[:80]
    await session.commit()
    if outcome and final is ExecutionStatus.SUCCEEDED:
        await journal.audit(
            AuditEventType.OUTCOME_RECORDED, **ctx.audit_fields(), details={"kind": "answer"}
        )
    await finish(ctx, session, journal, final, error=error)
    if error_event:
        yield error_event
    yield (
        "done",
        {
            "execution_id": str(ctx.execution_id),
            "status": final.value,
            "usage": ctx.usage,
            "text": text,
            "model": ctx.model.name if ctx.model else model,
        },
    )


async def run_answer(
    deps: ChatDeps,
    current: Run,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    question: str,
    model: str | None,
) -> None:
    """Body of the answer task: its own session, events into the ``Run`` buffer, finalizing a
    stop."""
    opened: list[ExecutionContext] = []

    def on_open(ctx: ExecutionContext) -> None:
        opened.append(ctx)
        current.publish(
            (
                "started",
                {
                    "execution_id": str(ctx.execution_id),
                    "started_at": current.started_at.isoformat(),
                },
            )
        )

    try:
        async with deps.sessions() as session:
            user = await session.get(User, user_id)
            conversation = await session.get(Conversation, conversation_id)
            if user is None or conversation is None:
                current.publish(("error", {"code": "conversation_not_found", "message": ""}))
                return
            try:
                async for event in answer(
                    session, deps, user, conversation, question, model, on_open=on_open
                ):
                    current.publish(event)
            except ChatError as exc:
                current.publish(("error", {"code": exc.code, "message": str(exc)}))
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None:
            # plain awaits from here: finalizing in the database, not a new cancellation
            task.uncancel()
        reason = current.stop_reason or STOPPED_BY_CORE
        ctx = opened[0] if opened else None
        if ctx is not None and not ctx.finished:
            await _finalize_stopped(deps, ctx, conversation_id, reason)
        current.publish(
            (
                "done",
                {
                    "execution_id": str(ctx.execution_id) if ctx else None,
                    "status": ExecutionStatus.CANCELLED.value,
                    "usage": ctx.usage if ctx else {},
                    "text": STOPPED_TEXT,
                    "model": ctx.model.name if ctx and ctx.model else model,
                },
            )
        )


async def _finalize_stopped(
    deps: ChatDeps, ctx: ExecutionContext, conversation_id: uuid.UUID, reason: str
) -> None:
    """A new session: the cancelled task's session may have been left mid-transaction."""
    async with deps.sessions() as session:
        written = await session.scalar(
            select(Message.id).where(
                Message.execution_id == ctx.execution_id, Message.role == MessageRole.ASSISTANT
            )
        )
        if written is None:
            session.add(
                Message(
                    conversation_id=conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=STOPPED_TEXT,
                    execution_id=ctx.execution_id,
                    citations=[],
                )
            )
            conversation = await session.get(Conversation, conversation_id)
            if conversation is not None:
                conversation.updated_at = utcnow()
            await session.commit()
        await finish(ctx, session, deps.journal, ExecutionStatus.CANCELLED, error=reason)


def sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


__all__ = ["ChatDeps", "ChatError", "answer", "run_answer", "sse"]
