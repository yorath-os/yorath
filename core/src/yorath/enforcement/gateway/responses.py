"""Responses API on top of the Gateway: Codex does not speak ``chat/completions``.

Format translation only; checks stay in ``ModelGateway``. No state: Codex sends the whole
history itself, so ``previous_response_id`` is not needed.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from yorath.enforcement.gateway.errors import GatewayError
from yorath.runtime.llm import ChatChunk, ChatMessage, ChatRequest, ToolCall, Usage
from yorath.runtime.llm.types import new_call_id


class ResponsesIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str
    instructions: str | None = None
    input: str | list[dict[str, Any]] = Field(default="")
    tools: list[dict[str, Any]] = []
    stream: bool = False
    temperature: float = 0
    max_output_tokens: int | None = None


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return "" if content is None else str(content)


def _messages(data: ResponsesIn) -> list[ChatMessage]:
    out: list[ChatMessage] = []
    if data.instructions:
        out.append(ChatMessage(role="system", content=data.instructions))
    if isinstance(data.input, str):
        return [*out, ChatMessage(role="user", content=data.input)]
    for item in data.input:
        kind = item.get("type", "message")
        if kind == "message":
            role = {"developer": "system"}.get(item.get("role", "user"), item.get("role", "user"))
            out.append(ChatMessage(role=role, content=_text(item.get("content"))))
        elif kind == "function_call":
            call = ToolCall(
                id=str(item.get("call_id") or new_call_id()),
                name=str(item.get("name", "")),
                arguments=str(item.get("arguments") or "{}"),
            )
            if out and out[-1].role == "assistant" and out[-1].tool_calls:
                out[-1].tool_calls.append(call)  # parallel calls are one message
            else:
                out.append(ChatMessage(role="assistant", content=None, tool_calls=[call]))
        elif kind == "function_call_output":
            out.append(
                ChatMessage(
                    role="tool",
                    content=_text(item.get("output")),
                    tool_call_id=str(item.get("call_id") or ""),
                )
            )
    return out


def _tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("parameters") or {"type": "object", "properties": {}},
            },
        }
        for t in tools
        if t.get("type") == "function"
    ]


def to_chat_request(data: ResponsesIn) -> ChatRequest:
    return ChatRequest(
        model=data.model,
        messages=_messages(data),
        tools=_tools(data.tools),
        temperature=data.temperature,
        max_tokens=data.max_output_tokens,
    )


def _usage(usage: Usage | None) -> dict[str, Any]:
    u = usage or Usage()
    inp, out = u.input_tokens or 0, u.output_tokens or 0
    return {
        "input_tokens": inp,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": out,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": inp + out,
    }


def _message_item(item_id: str, text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "id": item_id,
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _call_item(call: ToolCall) -> dict[str, Any]:
    return {
        "type": "function_call",
        "id": f"fc_{call.id}",
        "call_id": call.id,
        "name": call.name,
        "arguments": call.arguments,
        "status": "completed",
    }


def _envelope(response_id: str, model: str, status: str) -> dict[str, Any]:
    return {"id": response_id, "object": "response", "model": model, "status": status, "output": []}


def _line(event: str, payload: dict[str, Any]) -> bytes:
    body = json.dumps({"type": event, **payload}, ensure_ascii=False)
    return f"event: {event}\ndata: {body}\n\n".encode()


async def stream_events(
    response_id: str, model: str, first: ChatChunk, rest: AsyncIterator[ChatChunk]
) -> AsyncIterator[bytes]:
    """``response.*`` events: reads the Gateway to the end, where usage and audit are written."""
    item_id = f"msg_{response_id}"
    yield _line("response.created", {"response": _envelope(response_id, model, "in_progress")})
    text, opened, done = "", False, None
    try:
        chunk: ChatChunk | None = first
        while chunk is not None:
            if chunk.text:
                if not opened:
                    opened = True
                    added = {**_message_item(item_id, ""), "status": "in_progress", "content": []}
                    yield _line("response.output_item.added", {"output_index": 0, "item": added})
                text += chunk.text
                yield _line(
                    "response.output_text.delta",
                    {
                        "item_id": item_id,
                        "output_index": 0,
                        "content_index": 0,
                        "delta": chunk.text,
                    },
                )
            if chunk.done:
                done = chunk
            chunk = await anext(rest, None)
    except GatewayError as exc:
        failed = _envelope(response_id, model, "failed")
        failed["error"] = {"code": exc.code, "message": str(exc)}
        yield _line("response.failed", {"response": failed})
        return
    output: list[dict[str, Any]] = []
    if text:
        output.append(_message_item(item_id, text))
    output.extend(_call_item(c) for c in (done.tool_calls if done else []))
    for index, item in enumerate(output):
        yield _line("response.output_item.done", {"output_index": index, "item": item})
    completed = _envelope(response_id, model, "completed")
    completed["output"] = output
    completed["usage"] = _usage(done.usage if done else None)
    yield _line("response.completed", {"response": completed})


async def collect(
    response_id: str, model: str, first: ChatChunk, rest: AsyncIterator[ChatChunk]
) -> dict[str, Any]:
    text, done = "", None
    chunk: ChatChunk | None = first
    while chunk is not None:
        text += chunk.text
        if chunk.done:
            done = chunk
        chunk = await anext(rest, None)
    output: list[dict[str, Any]] = [_message_item(f"msg_{response_id}", text)] if text else []
    output.extend(_call_item(c) for c in (done.tool_calls if done else []))
    body = _envelope(response_id, model, "completed")
    body["output"] = output
    body["usage"] = _usage(done.usage if done else None)
    return body
