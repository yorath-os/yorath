"""OpenAI Chat Completions on top of ``ChatChunk``: the shared format of ``/gateway/v1`` and
``/v1``.

The Gateway speaks its own types (``ChatRequest``/``ChatChunk``); this is only the translation to
and from the OpenAI format, with no checks or accounting; those live in ModelGateway.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from yorath.runtime.llm import ChatChunk, ChatMessage, ChatRequest, ToolCall, Usage
from yorath.runtime.llm.types import new_call_id


class CompletionIn(BaseModel):
    """The subset of OpenAI Chat Completions the Gateway accepts."""

    model: str
    messages: list[dict[str, Any]] = Field(min_length=1)
    tools: list[dict[str, Any]] = []
    stream: bool = False
    temperature: float = 0
    max_tokens: int | None = None


def message_from_openai(raw: dict[str, Any]) -> ChatMessage:
    calls = [
        ToolCall(
            id=str(t.get("id") or new_call_id()),
            name=str((t.get("function") or {}).get("name", "")),
            arguments=str((t.get("function") or {}).get("arguments") or "{}"),
        )
        for t in raw.get("tool_calls") or []
    ]
    content = raw.get("content")
    if isinstance(content, list):  # OpenAI allows a list of parts; take the text ones
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ChatMessage(
        role=raw["role"],
        content=content,
        tool_calls=calls,
        tool_call_id=raw.get("tool_call_id"),
        name=raw.get("name"),
    )


def chat_request(data: CompletionIn) -> ChatRequest:
    return ChatRequest(
        model=data.model,
        messages=[message_from_openai(m) for m in data.messages],
        tools=data.tools,
        temperature=data.temperature,
        max_tokens=data.max_tokens,
    )


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, "type": "yorath_gateway"}}, status
    )


def line(obj: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()


def chunk_lines(envelope: dict[str, Any], chunk: ChatChunk) -> list[bytes]:
    base = {**envelope, "object": "chat.completion.chunk"}
    out: list[bytes] = []
    if chunk.text:
        out.append(line({**base, "choices": [choice({"content": chunk.text})]}))
    if chunk.done:
        if chunk.tool_calls:
            out.append(
                line(
                    {
                        **base,
                        "choices": [
                            choice(
                                {
                                    "tool_calls": [
                                        {"index": i, **c.openai()}
                                        for i, c in enumerate(chunk.tool_calls)
                                    ]
                                }
                            )
                        ],
                    }
                )
            )
        out.append(
            line(
                {
                    **base,
                    "choices": [choice({}, finish=chunk.finish_reason or "stop")],
                    "usage": usage_json(chunk.usage),
                }
            )
        )
    return out


def choice(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
    return {"index": 0, "delta": {"role": "assistant", **delta}, "finish_reason": finish}


def usage_json(usage: Usage | None) -> dict[str, int]:
    u = usage or Usage()
    return {
        "prompt_tokens": u.input_tokens or 0,
        "completion_tokens": u.output_tokens or 0,
        "total_tokens": (u.input_tokens or 0) + (u.output_tokens or 0),
    }


async def collect(
    envelope: dict[str, Any], first: ChatChunk, rest: AsyncIterator[ChatChunk]
) -> JSONResponse:
    """The Gateway generator is read to the end: after ``done`` it writes usage and audit."""
    text, calls, finish, usage = "", [], None, None
    chunk: ChatChunk | None = first
    while chunk is not None:
        text += chunk.text
        if chunk.done:
            calls, finish, usage = chunk.tool_calls, chunk.finish_reason, chunk.usage
        chunk = await anext(rest, None)
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = [c.openai() for c in calls]
    return JSONResponse(
        {
            **envelope,
            "object": "chat.completion",
            "choices": [{"index": 0, "message": message, "finish_reason": finish or "stop"}],
            "usage": usage_json(usage),
        }
    )
