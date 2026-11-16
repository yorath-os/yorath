"""Providers behind the OpenAI shape: Ollama mapping and OpenRouter SSE parsing, no database."""

import json

import httpx
import pytest

from yorath.runtime.llm import (
    ChatMessage,
    ChatRequest,
    OllamaChat,
    OpenRouterChat,
    ProviderError,
    ToolCall,
    collect,
)

TOOLS = [
    {"type": "function", "function": {"name": "search_docs", "parameters": {"type": "object"}}}
]


def ollama_stream(lines: list[dict]) -> httpx.AsyncClient:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, content=("\n".join(json.dumps(x) for x in lines) + "\n").encode()
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.captured = captured  # type: ignore[attr-defined]
    return client


async def test_ollama_tool_calls_and_message_mapping():
    http = ollama_stream(
        [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "index": 0,
                                "name": "search_docs",
                                "arguments": {"query": "vacation"},
                            },
                        }
                    ],
                },
                "done": False,
            },  # fmt: skip
            {
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 373,
                "eval_count": 30,
                "model": "qwen3.5:4b",
            },  # fmt: skip
        ]
    )
    provider = OllamaChat(http, "http://ollama:11434", num_ctx=2048)
    request = ChatRequest(
        tools=TOOLS,
        messages=[
            ChatMessage(role="system", content="s"),
            ChatMessage(role="user", content="q"),
            ChatMessage(
                role="assistant",
                tool_calls=[ToolCall(id="c0", name="search_docs", arguments='{"query": "x"}')],
            ),
            ChatMessage(role="tool", name="search_docs", tool_call_id="c0", content="[1] text"),
        ],
    )
    out = await collect(provider.chat("qwen3.5:4b", request))
    assert out.finish_reason == "tool_calls" and out.text == ""
    assert out.tool_calls[0].id == "call_1" and out.tool_calls[0].name == "search_docs"
    assert json.loads(out.tool_calls[0].arguments) == {"query": "vacation"}
    assert (
        out.usage.input_tokens == 373
        and out.usage.output_tokens == 30
        and out.model == "qwen3.5:4b"
    )
    body = http.captured["body"]
    assert body["think"] is False and body["options"]["num_ctx"] == 2048 and body["tools"] == TOOLS
    assert body["messages"][2]["tool_calls"] == [
        {"function": {"name": "search_docs", "arguments": {"query": "x"}}}
    ]
    assert body["messages"][3] == {
        "role": "tool",
        "content": "[1] text",
        "tool_name": "search_docs",
    }


async def test_ollama_text_stream_and_error():
    http = ollama_stream(
        [
            {"message": {"content": "Carry over "}, "done": False},
            {"message": {"content": "56 days"}, "done": False},
            {"message": {"content": ""}, "done": True, "done_reason": "stop", "eval_count": 3},
        ]
    )
    out = await collect(
        OllamaChat(http, "http://x").chat(
            "m", ChatRequest(messages=[ChatMessage(role="user", content="q")])
        )
    )
    assert out.text == "Carry over 56 days" and out.finish_reason == "stop" and out.tool_calls == []

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="model runner has unexpectedly stopped")

    bad = OllamaChat(httpx.AsyncClient(transport=httpx.MockTransport(failing)), "http://x")
    with pytest.raises(ProviderError, match="HTTP 500"):
        await collect(bad.chat("m", ChatRequest(messages=[ChatMessage(role="user", content="q")])))


def sse(events: list[dict]) -> bytes:
    return ("".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n").encode()


async def test_openrouter_sse_with_split_tool_call_and_usage():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=sse(
                [
                    {
                        "model": "openai/gpt-5.6-luna",
                        "choices": [{"delta": {"content": "Ans"}, "finish_reason": None}],
                    },
                    {"choices": [{"delta": {"content": "wer"}, "finish_reason": None}]},
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_a",
                                            "function": {
                                                "name": "search_docs",
                                                "arguments": '{"que',
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {"index": 0, "function": {"arguments": 'ry": "x"}'}}
                                    ]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ]
                    },
                    {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 7}},
                ]
            ),
        )

    provider = OpenRouterChat(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        "https://openrouter.ai/api/v1",
        "sk-test",
    )
    out = await collect(
        provider.chat(
            "openai/gpt-5.6-luna",
            ChatRequest(messages=[ChatMessage(role="user", content="q")], tools=TOOLS),
        )
    )
    assert out.text == "Answer" and out.finish_reason == "tool_calls"
    assert out.tool_calls == [ToolCall(id="call_a", name="search_docs", arguments='{"query": "x"}')]
    assert (
        out.usage.input_tokens == 11
        and out.usage.output_tokens == 7
        and out.model == "openai/gpt-5.6-luna"
    )
    assert captured["headers"]["authorization"] == "Bearer sk-test"
    assert (
        captured["body"]["stream_options"] == {"include_usage": True}
        and captured["body"]["tools"] == TOOLS
    )

    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    bad = OpenRouterChat(
        httpx.AsyncClient(transport=httpx.MockTransport(unauthorized)), "https://x", "k"
    )
    with pytest.raises(ProviderError, match="HTTP 401: bad key$"):
        await collect(bad.chat("m", ChatRequest(messages=[ChatMessage(role="user", content="q")])))
