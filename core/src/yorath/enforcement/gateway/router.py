"""``/gateway/v1`` is the OpenAI-compatible Model Gateway endpoint for sandboxed runtimes.

Authentication is the Execution token (Bearer); checks, trace and audit are the same as for
the native loop, with the ``control_level`` of the Execution context.
"""

import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select

from yorath.control.identity.deps import Db
from yorath.control.model import ModelResource
from yorath.enforcement.gateway import openai_format as fmt
from yorath.enforcement.gateway import responses
from yorath.enforcement.gateway.context import ExecutionContext
from yorath.enforcement.gateway.errors import QUEUE_STATUS, GatewayDenied, GatewayError
from yorath.enforcement.gateway.tokens import resolve_execution_token
from yorath.runtime.llm import ChatChunk, first_answer_chunk

router = APIRouter(prefix="/gateway/v1", tags=["gateway"])


async def _context(request: Request, db: Db) -> ExecutionContext:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, detail="execution_token_required")
    row = await resolve_execution_token(db, token.strip())
    ctx = request.app.state.contexts.get(row.execution_id) if row else None
    if ctx is None or ctx.finished:
        raise HTTPException(401, detail="execution_token_invalid")
    return ctx


@router.get("/models")
async def list_models(request: Request, db: Db):
    ctx = await _context(request, db)
    rows = (
        await db.scalars(
            select(ModelResource)
            .where(ModelResource.enabled.is_(True))
            .order_by(ModelResource.name)
        )
    ).all()
    return {
        "object": "list",
        "data": [
            {
                "id": m.name,
                "object": "model",
                "owned_by": m.provider.value,
                "allowed_for_execution": m.name in ctx.card.models,
            }
            for m in rows
        ],
    }


@router.post("/chat/completions")
async def chat_completions(data: fmt.CompletionIn, request: Request, db: Db):
    ctx = await _context(request, db)
    gateway = request.app.state.model_gateway
    chunks = gateway.chat(ctx, db, fmt.chat_request(data))
    try:
        first = await first_answer_chunk(chunks)
    except GatewayDenied as exc:
        return fmt.error(403, exc.code, exc.reason)
    except GatewayError as exc:
        status = QUEUE_STATUS.get(exc.code, 404 if exc.code == "model_not_found" else 502)
        return fmt.error(status, exc.code, str(exc))
    except StopAsyncIteration:
        first = ChatChunk(done=True)
    envelope = {
        "id": f"chatcmpl-{ctx.execution_id}",
        "created": int(time.time()),
        "model": data.model,
    }
    if not data.stream:
        try:
            return await fmt.collect(envelope, first, chunks)
        except GatewayError as exc:
            return fmt.error(502, exc.code, str(exc))

    async def stream() -> AsyncIterator[bytes]:
        try:
            for part in fmt.chunk_lines(envelope, first):
                yield part
            async for chunk in chunks:
                for part in fmt.chunk_lines(envelope, chunk):
                    yield part
        except GatewayError as exc:
            yield fmt.line({"error": {"code": exc.code, "message": str(exc)}})
        yield b"data: [DONE]\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/responses")
async def create_response(data: responses.ResponsesIn, request: Request, db: Db):
    """Responses API for Codex: the same token, PDP and accounting as ``chat/completions``."""
    ctx = await _context(request, db)
    chunks = request.app.state.model_gateway.chat(ctx, db, responses.to_chat_request(data))
    try:
        first = await first_answer_chunk(chunks)
    except GatewayDenied as exc:
        return fmt.error(403, exc.code, exc.reason)
    except GatewayError as exc:
        status = QUEUE_STATUS.get(exc.code, 404 if exc.code == "model_not_found" else 502)
        return fmt.error(status, exc.code, str(exc))
    except StopAsyncIteration:
        first = ChatChunk(done=True)
    response_id = f"resp_{uuid.uuid7().hex}"
    if not data.stream:
        try:
            return JSONResponse(await responses.collect(response_id, data.model, first, chunks))
        except GatewayError as exc:
            return fmt.error(502, exc.code, str(exc))
    return StreamingResponse(
        responses.stream_events(response_id, data.model, first, chunks),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
