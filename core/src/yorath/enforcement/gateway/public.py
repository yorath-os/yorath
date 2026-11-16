"""``/v1`` is the model endpoint for external clients with a user key (decision 32, criterion 12).

Formats: ``chat/completions`` and ``responses`` (Codex). Each call is an Execution of the built-in
``model-api`` agent (``hosting: external``) on behalf of the key owner: the same PDP, token
accounting
and cost as for agents, and a journal without the request and answer text. Only the models of the
``model-api`` card are available.

A key narrowed to an agent (change agent-api) calls only ``agent/<name>`` through
``chat/completions``: an Execution of that agent with its loop, tools and rights.

A connected agent key (``scope: agent_self``, change connected-agents) is for model calls by the
agent itself: an Execution of that agent (actor is its service account, effective principal the
owner),
models from its card. ``agent/<name>`` of a connected agent goes to its container endpoint.
"""

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath import PRODUCT_NAME
from yorath.control.cluster.placements import card_allows
from yorath.control.identity.api_keys import AgentNotCallable, api_agent, resolve_key
from yorath.control.identity.deps import Db
from yorath.control.journal import ControlLevel
from yorath.control.model import (
    Agent,
    ApiKey,
    ExecutionStatus,
    ExecutionTrigger,
    ModelResource,
    ServiceAccount,
    User,
)
from yorath.enforcement.gateway import openai_format as fmt
from yorath.enforcement.gateway import responses
from yorath.enforcement.gateway.context import ExecutionContext, finish, load_agent, open_context
from yorath.enforcement.gateway.errors import QUEUE_STATUS, GatewayDenied, GatewayError
from yorath.runtime.agent_api import PREFIX, AgentResult, BadMessages, run_agent, split_messages
from yorath.runtime.chat.router import chat_deps
from yorath.runtime.connected.invoke import run_connected
from yorath.runtime.llm import ChatChunk, ChatRequest, first_answer_chunk

AGENT = "model-api"
CLIENT_GONE = "client closed the connection"

router = APIRouter(prefix="/v1", tags=["model-endpoint"])
_closing: set[asyncio.Task] = (
    set()
)  # closing the Execution after a disconnect outlives the cancelled request


class EndpointError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def response(self) -> JSONResponse:
        return fmt.error(self.status, self.code, self.message)


async def _key(request: Request, db: Db) -> tuple[User, ApiKey]:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    found = await resolve_key(db, token.strip()) if scheme.lower() == "bearer" else None
    if found is None:
        raise EndpointError(
            401, "invalid_api_key", "a valid API key is required (Authorization: Bearer)"
        )
    return found


SELF = "agent_self"


async def _user(request: Request, db: Db) -> tuple[User, ApiKey]:
    """Key for a model: an agent-call key does not open a bare model; an agent's own key does."""
    user, key = await _key(request, db)
    if key.agent_id is not None and key.scope != SELF:
        raise EndpointError(
            403, "model_not_allowed", "the key is issued for calling an agent: model = agent/<name>"
        )
    return user, key


async def _agent(request: Request, db: Db, key: ApiKey | None = None):
    """Whose Execution a model call becomes: ``model-api`` or the connected agent of its key."""
    try:
        if key is not None and key.scope == SELF:
            name = await db.scalar(select(Agent.name).where(Agent.id == key.agent_id))
            return await load_agent(
                db, name or "", request.app.state.settings.agents_dir, allow_disabled=True
            )
        return await load_agent(db, AGENT, request.app.state.settings.agents_dir)
    except GatewayError as exc:
        raise EndpointError(
            403 if exc.code == "agent_disabled" else 503, exc.code, str(exc)
        ) from exc


@router.get("/models")
async def list_models(request: Request, db: Db):
    try:
        _, key = await _key(request, db)
        if key.agent_id is not None and key.scope != SELF:
            agent = await api_agent(db, key.agent_id)
            return {
                "object": "list",
                "data": [
                    {
                        "id": PREFIX + agent.name,
                        "object": "model",
                        "created": int(agent.created_at.timestamp()),
                        "owned_by": "yorath",
                    }
                ],
            }
        _, _, card, _ = await _agent(request, db, key)
    except AgentNotCallable as exc:
        return fmt.error(403, exc.code, str(exc))
    except EndpointError as exc:
        return exc.response()
    enabled = await db.scalars(
        select(ModelResource).where(ModelResource.enabled.is_(True)).order_by(ModelResource.name)
    )
    rows = [m for m in enabled.all() if card_allows(card.models, m.name)]
    return {
        "object": "list",
        "data": [
            {
                "id": m.name,
                "object": "model",
                "created": int(m.created_at.timestamp()),
                "owned_by": "yorath",
                "context_length": m.context_length,
            }
            for m in rows
        ],
    }


async def _close(
    request: Request, ctx: ExecutionContext, status: ExecutionStatus, error: str | None
):
    request.app.state.contexts.remove(ctx.execution_id)
    async with request.app.state.sessions() as session:
        await finish(ctx, session, request.app.state.journal, status, error=error)


def _track(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _closing.add(task)
    task.add_done_callback(_closing.discard)
    return task


def _close_later(
    request: Request,
    ctx: ExecutionContext,
    chunks: AsyncGenerator[Any] | None,
    session: AsyncSession | None = None,
) -> None:
    """Client disconnect before the answer starts: the request is already cancelled, closing runs as
    a separate task."""

    async def run() -> None:
        if chunks is not None:
            await chunks.aclose()  # stops the provider stream
        if session is not None:
            await session.close()
        await _close(request, ctx, ExecutionStatus.CANCELLED, CLIENT_GONE)

    _track(run())


Outcome = Callable[[Any], tuple[ExecutionStatus, str | None] | None]


async def _relay(
    request: Request,
    ctx: ExecutionContext,
    source: AsyncGenerator[Any],
    session: AsyncSession,
    *,
    last: Callable[[Any], bool],
    outcome: Outcome = lambda _: None,
) -> AsyncGenerator[Any]:
    """Gateway or agent stream for the client answer; a separate core task reads it.

    Token accounting, audit and closing the Execution happen in the source after the last piece, and
    clients (Hermes) drop the connection right after it: if the request itself read the source,
    cancelling the request would cut accounting and the Execution would stay ``running``. The task
    reads
    the source to the end and closes the Execution in its own session; a client leaving before the
    last
    piece stops the model and gives ``cancelled``. A Gateway error reaches the client as an
    exception.
    """
    queue: asyncio.Queue[Any] = asyncio.Queue()
    seen_last = settled = False
    end = object()

    async def produce() -> None:
        nonlocal seen_last, settled
        status, error = ExecutionStatus.SUCCEEDED, None
        try:
            async for item in source:
                seen_last = seen_last or last(item)
                if (result := outcome(item)) is not None:
                    status, error = result
                queue.put_nowait(item)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()  # what follows is closing in the database, not a new cancellation
            await source.aclose()
            status, error = ExecutionStatus.CANCELLED, CLIENT_GONE
        except GatewayError as exc:
            status, error = ExecutionStatus.FAILED, exc.code
            queue.put_nowait(exc)
        except Exception as exc:  # noqa: BLE001 — the outcome must reach the database
            status, error = ExecutionStatus.FAILED, f"{type(exc).__name__}: {exc}"
            queue.put_nowait(GatewayError("model_error", str(exc)))
        settled = True
        try:
            await session.close()
            await _close(request, ctx, status, error)
        finally:
            queue.put_nowait(end)

    producer = _track(produce())
    finished = False
    failure: GatewayError | None = None
    try:
        while (item := await queue.get()) is not end:
            if isinstance(item, GatewayError):
                failure = item  # hand to the client after closing the Execution as failed
            else:
                yield item
        finished = True
        if failure is not None:
            raise failure
    finally:
        if not finished and not seen_last and not settled:
            producer.cancel()  # the client left mid-answer: the model is no longer needed


_SSE = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


async def _start(
    request: Request, db: Db, chat: ChatRequest
) -> tuple[ExecutionContext, AsyncGenerator[ChatChunk], AsyncSession] | JSONResponse:
    """Key, the model-api Execution and the first piece of the answer; a denial is a ready error
    response.

    The model stream runs in its own session: the ``_relay`` task reads it to the end and outlives
    the request, while the request session closes with the request.
    """
    try:
        user, key = await _user(request, db)
        agent, revision, card, body = await _agent(request, db, key)
    except EndpointError as exc:
        return exc.response()
    actor = user
    if (
        key.scope == SELF
    ):  # the agent calls the model itself: it is the actor, the key owner is accountable
        actor = (
            await db.scalar(select(ServiceAccount).where(ServiceAccount.agent_id == agent.id))
            or user
        )
    ctx = await open_context(
        db, request.app.state.journal, agent=agent, revision=revision, card=card, body=body,
        actor=actor, effective_principal=user, trigger=ExecutionTrigger.API,
        control_level=ControlLevel.GATEWAY, api_key=key,
    )  # fmt: skip
    ctx.record_content = False
    if not agent.enabled:  # disabled agent: denied before the model, but recorded in the journal
        await _close(request, ctx, ExecutionStatus.DENIED, "agent_disabled")
        return fmt.error(403, "agent_disabled", f"agent {agent.name} is disabled by the owner")
    request.app.state.contexts.add(ctx)
    own = request.app.state.sessions()
    chunks = request.app.state.model_gateway.chat(ctx, own, chat)
    try:
        first = await first_answer_chunk(chunks)
    except StopAsyncIteration:
        first = ChatChunk(done=True)
    except GatewayDenied as exc:
        await own.close()
        await _close(request, ctx, ExecutionStatus.DENIED, exc.code)
        return fmt.error(403, exc.code, exc.reason)
    except GatewayError as exc:
        await own.close()
        await _close(request, ctx, ExecutionStatus.FAILED, exc.code)
        status = QUEUE_STATUS.get(exc.code, 504 if exc.code == "model_timeout" else 502)
        return fmt.error(status, exc.code, str(exc))
    except asyncio.CancelledError:
        _close_later(request, ctx, chunks, own)
        raise
    return ctx, _with_first(first, chunks), own


async def _with_first(
    first: ChatChunk, rest: AsyncGenerator[ChatChunk]
) -> AsyncGenerator[ChatChunk]:
    """The first piece is already read so a denial comes before 200; the rest is as it was."""
    try:
        yield first
        async for chunk in rest:
            yield chunk
    finally:
        await rest.aclose()


def _model_relay(request: Request, ctx: ExecutionContext, chunks, own) -> AsyncGenerator[ChatChunk]:
    return _relay(request, ctx, chunks, own, last=lambda c: c.done)


@router.post("/chat/completions")
async def chat_completions(data: fmt.CompletionIn, request: Request, db: Db):
    if data.model.startswith(PREFIX):
        return await _call_agent(data, request, db)
    started = await _start(request, db, fmt.chat_request(data))
    if isinstance(started, JSONResponse):
        return started
    ctx, chunks, own = started
    relay = _model_relay(request, ctx, chunks, own)
    envelope = {
        "id": f"chatcmpl-{ctx.execution_id}",
        "created": int(time.time()),
        "model": data.model,
    }
    if not data.stream:
        try:
            return await fmt.collect(envelope, await anext(relay), relay)
        except GatewayError as exc:
            return fmt.error(502, exc.code, str(exc))

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in relay:
                for part in fmt.chunk_lines(envelope, chunk):
                    yield part
        except GatewayError as exc:
            yield fmt.line({"error": {"code": exc.code, "message": str(exc)}})
        yield b"data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers=_SSE)


@router.post("/responses")
async def create_response(data: responses.ResponsesIn, request: Request, db: Db):
    """Responses API for Codex and other clients that do not speak ``chat/completions``."""
    started = await _start(request, db, responses.to_chat_request(data))
    if isinstance(started, JSONResponse):
        return started
    ctx, chunks, own = started
    relay = _model_relay(request, ctx, chunks, own)
    response_id = f"resp_{ctx.execution_id.hex}"
    if not data.stream:
        try:
            body = await responses.collect(response_id, data.model, await anext(relay), relay)
        except GatewayError as exc:
            return fmt.error(502, exc.code, str(exc))
        return JSONResponse(body)

    async def stream() -> AsyncIterator[bytes]:
        # stream_events turns a Gateway error into response.failed; _relay closes the Execution
        async for part in responses.stream_events(
            response_id, data.model, await anext(relay), relay
        ):
            yield part

    return StreamingResponse(stream(), media_type="text/event-stream", headers=_SSE)


async def _call_agent(data: fmt.CompletionIn, request: Request, db: Db):
    """``agent/<name>`` with a key narrowed to that agent: the agent's Execution on behalf of the
    key owner."""
    try:
        user, key = await _key(request, db)
    except EndpointError as exc:
        return exc.response()
    name = data.model.removeprefix(PREFIX)
    spec = request.app.state.manifest.connected_agents.get(name)
    if key.scope == SELF:
        return fmt.error(403, "model_not_allowed", "an agent's own key is only for model calls")
    if key.agent_id is None and spec is None:
        return fmt.error(403, "model_not_allowed", f"the key is not issued for agent {name}")
    if key.agent_id is not None:
        try:
            bound = await api_agent(db, key.agent_id)
        except AgentNotCallable as exc:
            return fmt.error(403, exc.code, str(exc))
        if bound.name != name:
            return fmt.error(403, "model_not_allowed", f"the key is issued for agent {bound.name}")
    try:
        task, history = split_messages(data.messages)
    except BadMessages as exc:
        return fmt.error(400, "invalid_request", str(exc))
    try:
        agent, revision, card, body = await load_agent(
            db, name, request.app.state.settings.agents_dir
        )
    except GatewayError as exc:
        return fmt.error(503, exc.code, str(exc))
    if card.runtime != "native" and spec is None:
        return fmt.error(
            403, "agent_not_callable", f"agent {name} does not run on the {PRODUCT_NAME} loop"
        )
    ctx = await open_context(
        db, request.app.state.journal, agent=agent, revision=revision, card=card, body=body,
        actor=user, effective_principal=user, trigger=ExecutionTrigger.API, api_key=key,
        control_level=ControlLevel.GATEWAY if spec else ControlLevel.TOOL,
    )  # fmt: skip
    request.app.state.contexts.add(ctx)
    own = request.app.state.sessions()
    result = AgentResult()

    def outcome(part: Any) -> tuple[ExecutionStatus, str | None] | None:
        nonlocal result
        if not isinstance(part, AgentResult):
            return None
        result = part
        return part.status, part.error

    if spec is not None:  # connected agent: to its container endpoint
        state = request.app.state
        produce = run_connected(
            ctx, own, chat_deps(request), state.http, state.secrets, spec, data.messages
        )
    else:
        produce = run_agent(ctx, own, chat_deps(request), task, history)
    parts = _relay(
        request, ctx, produce, own,
        last=lambda part: isinstance(part, AgentResult), outcome=outcome,
    )  # fmt: skip
    envelope = {
        "id": f"chatcmpl-{ctx.execution_id}",
        "created": int(time.time()),
        "model": data.model,
    }

    if not data.stream:
        text = "".join([part async for part in parts if isinstance(part, str)])
        if result.status is ExecutionStatus.DENIED:
            return fmt.error(403, result.code or "model_denied", result.error or "")
        if result.code == "agent_unavailable":
            return fmt.error(503, result.code, result.error or "")
        if result.status is not ExecutionStatus.SUCCEEDED:
            return fmt.error(502, result.code or "model_error", result.error or "")
        return JSONResponse(
            {
                **envelope,
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": result.text or text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": _usage(ctx),
                "yorath": {"execution_id": str(ctx.execution_id), "citations": result.citations},
            }
        )

    async def stream() -> AsyncIterator[bytes]:
        async for part in parts:
            if isinstance(part, str):
                yield fmt.line(
                    {
                        **envelope,
                        "object": "chat.completion.chunk",
                        "choices": [fmt.choice({"content": part})],
                    }
                )
        if result.status is ExecutionStatus.SUCCEEDED:
            yield fmt.line(
                {
                    **envelope,
                    "object": "chat.completion.chunk",
                    "choices": [fmt.choice({}, finish="stop")],
                    "usage": _usage(ctx),
                    "yorath": {
                        "execution_id": str(ctx.execution_id),
                        "citations": result.citations,
                    },
                }
            )
        else:
            yield fmt.line({"error": {"code": result.code, "message": result.error}})
        yield b"data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers=_SSE)


def _usage(ctx: ExecutionContext) -> dict[str, int]:
    prompt, completion = ctx.usage.get("input_tokens", 0), ctx.usage.get("output_tokens", 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
