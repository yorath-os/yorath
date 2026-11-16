"""Ollama through the native ``/api/chat``: ``tools``, ``think: false`` and ``num_ctx`` exist there
but not in the Ollama OpenAI-compatible path (checked 4.11.2026). Parameters are from spike
local-model-cpu.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from yorath.runtime.llm.types import (
    ChatChunk,
    ChatMessage,
    ChatRequest,
    ProviderError,
    ToolCall,
    Usage,
)


def to_ollama(message: ChatMessage) -> dict[str, Any]:
    out: dict[str, Any] = {"role": message.role, "content": message.content or ""}
    if message.tool_calls:
        out["tool_calls"] = [
            {"function": {"name": t.name, "arguments": _args(t.arguments)}}
            for t in message.tool_calls
        ]
    if message.role == "tool" and message.name:
        out["tool_name"] = message.name
    return out


def _args(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def from_ollama_tool_call(raw: dict[str, Any]) -> ToolCall:
    fn = raw.get("function") or {}
    args = fn.get("arguments")
    return ToolCall(
        id=raw.get("id") or ToolCall.model_fields["id"].get_default(call_default_factory=True),
        name=str(fn.get("name", "")),
        arguments=args if isinstance(args, str) else json.dumps(args or {}, ensure_ascii=False),
    )


# No limit on the answer itself (the Gateway has its own deadlines), but a model server on a cluster
# node that just went down must fail the call in seconds, not hold it until the first-token timeout
# (lab run 14.11.2026: a call hung for 200 s while the node was not yet marked lost).
STREAM_TIMEOUT = httpx.Timeout(None, connect=10.0)


class OllamaChat:
    name = "ollama"

    def __init__(self, http: httpx.AsyncClient, base_url: str, *, num_ctx: int = 4096) -> None:
        self._http = http
        self._url = f"{base_url.rstrip('/')}/api/chat"
        self._num_ctx = num_ctx

    async def chat(self, model_id: str, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        body: dict[str, Any] = {
            "model": model_id,
            "messages": [to_ollama(m) for m in request.messages],
            "stream": True,
            "think": False,
            "options": {"temperature": request.temperature, "num_ctx": self._num_ctx},
        }
        if request.tools:
            body["tools"] = request.tools
        if request.max_tokens:
            body["options"]["num_predict"] = request.max_tokens
        async for chunk in self._run(body):
            yield chunk

    async def _run(self, body: dict[str, Any]) -> AsyncIterator[ChatChunk]:
        calls: list[ToolCall] = []
        async with self._http.stream("POST", self._url, json=body, timeout=STREAM_TIMEOUT) as r:
            if r.status_code == 400 and "think" in body:
                body.pop("think")  # models without a thinking mode reject the parameter
                async for chunk in self._run(body):
                    yield chunk
                return
            if r.status_code >= 400:
                detail = (await r.aread()).decode(errors="replace")[:500]
                raise ProviderError(f"Ollama HTTP {r.status_code}: {detail}")
            started = False
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                if not started:
                    started = True
                    yield ChatChunk()  # first model event: the first-token deadline is lifted
                event = json.loads(line)
                if "error" in event:
                    raise ProviderError(f"Ollama: {event['error']}")
                message = event.get("message") or {}
                calls.extend(from_ollama_tool_call(t) for t in message.get("tool_calls") or [])
                text = message.get("content") or ""
                if event.get("done"):
                    yield ChatChunk(
                        text=text,
                        done=True,
                        tool_calls=calls,
                        finish_reason="tool_calls" if calls else event.get("done_reason"),
                        usage=Usage(
                            input_tokens=event.get("prompt_eval_count"),
                            output_tokens=event.get("eval_count"),
                            total_duration_ns=event.get("total_duration"),
                        ),
                        model=event.get("model"),
                    )
                elif text:
                    yield ChatChunk(text=text)
