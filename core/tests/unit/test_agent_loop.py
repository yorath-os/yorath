"""Loop with fake Gateways: event order, nudge, limits, citations, message budget."""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from yorath.control.model import DataClass, ExecutionStatus, Message, MessageRole
from yorath.distribution.manifest import AgentLoopSettings
from yorath.enforcement.gateway.errors import GatewayDenied, GatewayError
from yorath.runtime.agent_loop import (
    CitationBook,
    Citations,
    Failure,
    Final,
    LoopDeps,
    Token,
    ToolFinished,
    ToolStarted,
    build_messages,
    run,
)
from yorath.runtime.agent_loop.loop import ANSWER_NUDGE, NUDGE, TRIMMED, Trim
from yorath.runtime.llm import ChatChunk, ChatMessage, ToolCall, Usage
from yorath.runtime.tools.types import Citation, ToolResult
from yorath.runtime.tools.web_search import render

SEARCH = {"type": "function", "function": {"name": "search_docs", "parameters": {"type": "object"}}}


@dataclass
class Ctx:
    body: str = "You are an assistant."
    execution_id: uuid.UUID = field(default_factory=uuid.uuid7)


def cit(n: int) -> Citation:
    return Citation(
        n=n,
        document_id="d",
        filename="f.md",
        heading_path="h",
        asset="team-docs",
        data_class="internal",
    )


@dataclass
class FakeTools:
    spec: list[dict] = field(default_factory=lambda: [SEARCH])
    calls: list[tuple[str, dict, dict]] = field(default_factory=list)
    hits: int = 2
    denied_tools: set[str] = field(default_factory=set)

    async def list_tools(self, ctx):
        return self.spec

    async def call_tool(self, ctx, session, name, arguments=None, *, meta=None):
        self.calls.append((name, arguments or {}, meta or {}))
        if name in self.denied_tools:
            return ToolResult(content="denied by policy", is_error=True, meta={"denied": True})
        start = int((meta or {}).get("citation_offset", 0)) + 1
        cites = [cit(i) for i in range(start, start + self.hits)]
        return ToolResult(
            content="\n\n".join(f"[{c.n}] f.md › h: text" for c in cites) or "none",
            data_class=DataClass.INTERNAL,
            citations=cites,
            assets_denied=["hr-salaries"],
            meta={"hits": len(cites)},
        )


def text_reply(text: str) -> list[ChatChunk]:
    return [
        ChatChunk(text=text),
        ChatChunk(done=True, finish_reason="stop", usage=Usage(input_tokens=5, output_tokens=2)),
    ]


def tool_reply(*names: str) -> list[ChatChunk]:
    calls = [ToolCall(id=f"c{i}", name=n, arguments='{"query": "q"}') for i, n in enumerate(names)]
    return [
        ChatChunk(
            done=True,
            tool_calls=calls,
            finish_reason="tool_calls",
            usage=Usage(input_tokens=5, output_tokens=1),
        )
    ]


@dataclass
class FakeModels:
    script: list[Any]  # replies per step: list[ChatChunk] | Exception
    requests: list[Any] = field(default_factory=list)

    async def chat(self, ctx, session, request):
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        for chunk in item:
            yield chunk


def deps(models, tools=None, **kw) -> LoopDeps:
    return LoopDeps(tools=tools or FakeTools(), models=models, settings=AgentLoopSettings(**kw))


async def events(models, tools=None, task="question", **kw):
    out = []
    async for ev in run(Ctx(), None, deps(models, tools, **kw), task, []):
        out.append(ev)
    return out


async def test_tool_then_answer_with_citations():
    tools = FakeTools()
    models = FakeModels([tool_reply("search_docs"), text_reply("Answer per [2].")])
    out = await events(models, tools)
    kinds = [type(e).__name__ for e in out]
    assert kinds == ["ToolStarted", "ToolFinished", "Citations", "Token", "Final"]
    assert isinstance(out[0], ToolStarted) and out[0].arguments == {"query": "q"}
    assert isinstance(out[1], ToolFinished) and out[1].status == "done" and out[1].hits == 2
    assert isinstance(out[2], Citations) and out[2].denied == ["hr-salaries"]
    final = out[-1]
    assert isinstance(final, Final) and [c.n for c in final.citations] == [2] and not final.no_tools
    assert final.steps == 2 and len(final.all_citations) == 2
    # the second step sees the tool call and its result as OpenAI messages
    roles = [m.role for m in models.requests[1].messages]
    assert roles == ["system", "user", "assistant", "tool"]
    assert models.requests[1].messages[3].tool_call_id == "c0" and models.requests[1].tools == [
        SEARCH
    ]
    assert tools.calls[0][2] == {"citation_offset": 0}


async def test_two_searches_number_citations_through():
    tools = FakeTools(hits=3)
    models = FakeModels(
        [tool_reply("search_docs"), tool_reply("search_docs"), text_reply("see [5]")]
    )
    out = await events(models, tools)
    assert tools.calls[1][2] == {"citation_offset": 3}
    final = out[-1]
    assert isinstance(final, Final) and [c.n for c in final.citations] == [5]
    assert [c.n for c in final.all_citations] == [1, 2, 3, 4, 5, 6]


async def test_nudge_once_when_model_skips_tools():
    models = FakeModels([text_reply("Answering right away"), text_reply("Still no tools")])
    out = await events(models)
    assert [type(e).__name__ for e in out] == ["Token", "Final"]
    final = out[-1]
    assert isinstance(final, Final) and final.no_tools and final.text == "Still no tools"
    assert final.steps == 2
    second = models.requests[1].messages
    assert second[-1].content == NUDGE and second[-2].content == "Answering right away"


async def test_no_tools_agent_streams_from_first_step():
    models = FakeModels([text_reply("Hello")])
    out = await events(models, FakeTools(spec=[]))
    assert [type(e).__name__ for e in out] == ["Token", "Final"] and models.requests[0].tools == []
    assert isinstance(out[0], Token) and out[0].text == "Hello"


async def test_denied_tool_is_result_not_failure():
    tools = FakeTools(denied_tools={"query_records"})
    models = FakeModels([tool_reply("query_records"), text_reply("No data")])
    out = await events(models, tools)
    finished = next(e for e in out if isinstance(e, ToolFinished))
    assert finished.status == "denied" and "denied" in (finished.message or "")
    assert isinstance(out[-1], Final) and not out[-1].no_tools
    assert models.requests[1].messages[-1].content == "denied by policy"


async def test_last_step_answers_from_collected_facts_without_tools():
    models = FakeModels(
        [tool_reply("search_docs"), tool_reply("search_docs"), text_reply("Answer")]
    )
    out = await events(models, max_steps=3)
    assert isinstance(out[-1], Final) and out[-1].text == "Answer"
    assert [bool(r.tools) for r in models.requests] == [True, True, False]


async def test_single_step_keeps_tools_until_first_call():
    models = FakeModels([tool_reply("search_docs")])
    out = await events(models, max_steps=1)
    assert models.requests[0].tools
    assert isinstance(out[-1], Failure) and out[-1].code == "max_steps"


async def test_max_steps_and_model_denied_and_error():
    models = FakeModels([tool_reply("search_docs"), tool_reply("search_docs"), text_reply("")] * 2)
    out = await events(models, max_steps=3)
    assert isinstance(out[-1], Failure) and out[-1].code == "max_steps"
    assert out[-1].status is ExecutionStatus.FAILED

    denied = GatewayDenied(
        "model_denied", "no permitting policy", action="call_model", resource="m"
    )
    out = await events(FakeModels([denied]))
    assert isinstance(out[-1], Failure) and out[-1].status is ExecutionStatus.DENIED
    assert out[-1].message == "no permitting policy"

    out = await events(FakeModels([GatewayError("model_error", "Ollama HTTP 500")]))
    assert isinstance(out[-1], Failure) and out[-1].code == "model_error"
    assert out[-1].status is ExecutionStatus.FAILED


async def test_timeout_fails_execution():
    class Slow:
        async def chat(self, ctx, session, request):
            await asyncio.sleep(1)
            yield ChatChunk(done=True)

    out = await events(Slow(), timeout_seconds=0.05)
    assert isinstance(out[-1], Failure) and out[-1].code == "timeout"


def test_citation_book_cited_by_numbers():
    book = CitationBook()
    book.add(ToolResult(content="", citations=[cit(1), cit(2)], assets_denied=["a"]))
    book.add(ToolResult(content="", citations=[cit(3)], assets_denied=["a", "b"]))
    assert book.count == 3 and book.denied == ["a", "b"]
    assert [c.n for c in book.cited("see [3] and [1], but not [9]")] == [1, 3]


def msg(role: MessageRole, text: str) -> Message:
    return Message(conversation_id=uuid.uuid7(), role=role, content=text)


def test_budget_shortens_tool_results_then_drops_history():
    history = [msg(MessageRole.USER, "legacy" * 700), msg(MessageRole.ASSISTANT, "answer")]
    steps = [
        ChatMessage(role="assistant", tool_calls=[ToolCall(id="c0", name="search_docs")]),
        ChatMessage(role="tool", name="search_docs", tool_call_id="c0", content="x" * 5000),
        ChatMessage(role="assistant", tool_calls=[ToolCall(id="c1", name="search_docs")]),
        ChatMessage(role="tool", name="search_docs", tool_call_id="c1", content="y" * 5000),
    ]
    trim = Trim()
    messages = build_messages("SYS", history, "q", steps, num_ctx=4096, trim=trim)  # ≈ 8600 chars
    tools_msgs = [m for m in messages if m.role == "tool"]
    # both results stay, shortened, instead of the old one dropped whole
    assert [m.content[0] for m in tools_msgs] == ["x", "y"]
    assert all(m.content.endswith("…") and len(m.content) < 5000 for m in tools_msgs)
    assert (trim.shortened, trim.removed) == (2, 0)
    assert messages[0].content.startswith("SYS\n\n") and messages[0].role == "system"
    assert all(m.content != "legacy" * 700 for m in messages)
    assert [m.role for m in messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    assert steps[1].content == "x" * 5000  # the original steps are not mutated


def test_five_search_results_in_a_small_window_all_reach_the_model():
    """5 web results of 4,000 characters each at a 4,096-token window: every [n] with its title and
    URL reaches the model, the text shortened (field-fixes-0-7: the model saw none of them)."""
    results = [
        {"title": f"Title {i}", "url": f"https://example.com/{i}", "content": "z" * 4000}
        for i in range(1, 6)
    ]
    steps = [
        ChatMessage(role="assistant", tool_calls=[ToolCall(id="c0", name="web_search")]),
        ChatMessage(role="tool", name="web_search", tool_call_id="c0", content=render(results)),
    ]
    trim = Trim()
    messages = build_messages("SYS", [], "what is it?", steps, num_ctx=4096, trim=trim)
    seen = messages[-1].content
    assert seen != TRIMMED
    for i in range(1, 6):
        assert f"[{i}] Title {i} (https://example.com/{i}): zzz" in seen
    assert (trim.shortened, trim.removed) == (1, 0)
    assert len("".join(m.content or "" for m in messages)) <= 4096 * 0.7 * 3


def test_results_that_do_not_fit_even_shortened_are_dropped_oldest_first():
    steps = []
    for i in range(20):
        steps += [
            ChatMessage(role="assistant", tool_calls=[ToolCall(id=f"c{i}", name="search_docs")]),
            ChatMessage(
                role="tool", name="search_docs", tool_call_id=f"c{i}", content=str(i) * 3000
            ),
        ]
    trim = Trim()
    messages = build_messages("SYS", [], "q", steps, num_ctx=4096, trim=trim)
    tools_msgs = [m for m in messages if m.role == "tool"]
    assert tools_msgs[0].content == TRIMMED and tools_msgs[-1].content.startswith("19")
    assert trim.removed > 0 and trim.shortened > 0


@pytest.mark.parametrize("raw", ['{"query": "x"}', "not json", "[1, 2]"])
def test_arguments_never_crash(raw):
    from yorath.runtime.agent_loop.loop import _arguments

    assert isinstance(_arguments(raw), dict)


async def test_empty_answer_is_retried_once_then_fails():
    models = FakeModels([tool_reply("search_docs"), text_reply(""), text_reply("Answer [1].")])
    out = await events(models)
    final = out[-1]
    assert isinstance(final, Final) and final.text == "Answer [1]." and final.steps == 3
    assert models.requests[2].messages[-1].content == ANSWER_NUDGE

    models = FakeModels([tool_reply("search_docs"), text_reply(""), text_reply("  ")])
    out = await events(models)
    assert isinstance(out[-1], Failure) and out[-1].code == "empty_answer"
    assert out[-1].status is ExecutionStatus.FAILED
    assert not any(isinstance(e, Token) and e.text.strip() for e in out)  # no meaningful tokens
