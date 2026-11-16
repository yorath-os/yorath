"""OpenRouter through ``/v1/chat/completions`` as a stream (SSE), usage through
``stream_options``."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from yorath import PRODUCT_NAME
from yorath.runtime.llm.types import ChatChunk, ChatRequest, ProviderError, ToolCall, Usage


def _reason(body: bytes) -> str:
    """Error text from ``{"error": {"message": ...}}``, otherwise the start of the response body."""
    text = body.decode(errors="replace")
    try:
        return str(json.loads(text)["error"]["message"])[:300]
    except ValueError, KeyError, TypeError:
        return text[:300]


class _CallBuilder:
    """Assembles ``delta.tool_calls`` by ``index``: the name comes once, the arguments in pieces."""

    def __init__(self) -> None:
        self._parts: dict[int, dict[str, str]] = {}

    def add(self, deltas: list[dict[str, Any]]) -> None:
        for d in deltas:
            idx = int(d.get("index", 0))
            part = self._parts.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            if d.get("id"):
                part["id"] = d["id"]
            fn = d.get("function") or {}
            if fn.get("name"):
                part["name"] += fn["name"]
            if fn.get("arguments"):
                part["arguments"] += fn["arguments"]

    def calls(self) -> list[ToolCall]:
        out = []
        for idx in sorted(self._parts):
            p = self._parts[idx]
            out.append(
                ToolCall(
                    id=p["id"] or f"call_{idx}", name=p["name"], arguments=p["arguments"] or "{}"
                )
            )
        return out


class OpenRouterChat:
    name = "openrouter"

    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: str) -> None:
        self._http = http
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}", "X-Title": PRODUCT_NAME}

    async def chat(self, model_id: str, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        body: dict[str, Any] = {
            "model": model_id,
            "messages": [m.openai() for m in request.messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": request.temperature,
        }
        if request.tools:
            body["tools"] = request.tools
        if request.max_tokens:
            body["max_tokens"] = request.max_tokens
        builder, finish, usage, model = _CallBuilder(), None, Usage(), None
        async with self._http.stream(
            "POST", self._url, json=body, headers=self._headers, timeout=None
        ) as r:
            if r.status_code >= 400:
                raise ProviderError(f"OpenRouter HTTP {r.status_code}: {_reason(await r.aread())}")
            started = False
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue  # including keep-alive comments: the model has not started yet
                if not started:
                    started = True
                    yield ChatChunk()  # first model event (a tool call comes without text)
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                event = json.loads(data)
                if "error" in event:
                    raise ProviderError(f"OpenRouter: {event['error']}")
                model = event.get("model") or model
                if event.get("usage"):
                    usage = Usage(
                        input_tokens=event["usage"].get("prompt_tokens"),
                        output_tokens=event["usage"].get("completion_tokens"),
                    )
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("tool_calls"):
                        builder.add(delta["tool_calls"])
                    if delta.get("content"):
                        yield ChatChunk(text=delta["content"])
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
        calls = builder.calls()
        yield ChatChunk(
            done=True,
            tool_calls=calls,
            finish_reason="tool_calls" if calls else finish,
            usage=usage,
            model=model,
        )
