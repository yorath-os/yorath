"""Agent loop: messages → ModelGateway with tools → ToolGateway → … → answer.

The loop knows nothing about conversations, SSE or the database: the caller (chat, later
``ask_agent`` and triggers)
creates the Execution and context, translates events into its own format and records the outcome.
Vision §7.3: a few hundred lines for full control, not an agent framework.
"""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.model import ExecutionStatus, Message
from yorath.distribution.manifest import AgentLoopSettings
from yorath.enforcement.gateway.errors import GatewayDenied, GatewayError
from yorath.runtime.llm import ChatChunk, ChatMessage, ChatRequest
from yorath.runtime.tools.types import Citation, ToolResult

PREAMBLE = (
    "Working rules: to get facts, call the available tools. "
    "Cite passages by their numbers in square brackets, for example [1]. "
    "If the tools found nothing, say plainly that there is no answer, and do not make things up."
)
NUDGE = "First use the tools to find the facts, and only then answer."
ANSWER_NUDGE = "Now answer the question from the passages found, with [n] citations."
TRIMMED = "[tool result removed: did not fit in the context]"

CHARS_PER_TOKEN = 3  # rough, conservative estimate
CONTEXT_SHARE = 0.7  # share of the context window for the prompt, the rest is for the answer
CITATION_RE = re.compile(r"\[(\d+)\]")


class ToolStarted(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


class ToolFinished(BaseModel):
    name: str
    status: Literal["done", "denied", "error"]
    data_class: str | None = None
    hits: int | None = None
    message: str | None = None


class Citations(BaseModel):
    citations: list[Citation] = []
    denied: list[str] = []


class Waiting(BaseModel):
    """The model call waits for a free model server (change model-queue)."""

    ahead: int


class Token(BaseModel):
    text: str


class Final(BaseModel):
    text: str
    citations: list[Citation] = []
    all_citations: list[Citation] = []
    no_tools: bool = False
    steps: int = 0


class Failure(BaseModel):
    code: str
    message: str
    status: ExecutionStatus


LoopEvent = ToolStarted | ToolFinished | Citations | Waiting | Token | Final | Failure


class ToolsPort(Protocol):
    async def list_tools(self, ctx: Any) -> list[dict[str, Any]]: ...

    async def call_tool(
        self,
        ctx: Any,
        session: AsyncSession,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        meta: dict[str, Any] | None = None,
    ) -> ToolResult: ...


class ModelsPort(Protocol):
    def chat(
        self, ctx: Any, session: AsyncSession, request: ChatRequest
    ) -> AsyncIterator[ChatChunk]: ...


@dataclass
class LoopDeps:
    tools: ToolsPort
    models: ModelsPort
    settings: AgentLoopSettings
    num_ctx: int = 4096


class CitationBook:
    """Continuous passage numbering across all tool calls of one Execution."""

    def __init__(self) -> None:
        self.citations: list[Citation] = []
        self.denied: list[str] = []

    @property
    def count(self) -> int:
        return len(self.citations)

    def add(self, result: ToolResult) -> None:
        self.citations.extend(result.citations)
        for name in result.assets_denied:
            if name not in self.denied:
                self.denied.append(name)

    def cited(self, text: str) -> list[Citation]:
        used = {int(n) for n in CITATION_RE.findall(text)}
        return [c for c in self.citations if c.n in used]


def build_messages(
    system: str,
    history: list[Message],
    task: str,
    steps: list[ChatMessage],
    *,
    num_ctx: int = 4096,
    history_limit: int = 6,
    preamble: str = PREAMBLE,
) -> list[ChatMessage]:
    """The system prompt and the task are never trimmed; tool results go first
    (oldest first), then the conversation history."""
    budget = int(num_ctx * CONTEXT_SHARE * CHARS_PER_TOKEN)
    system_text = f"{system}\n\n{preamble}" if preamble else system
    fixed = len(system_text) + len(task)
    kept = [m.model_copy() for m in steps]
    size = lambda ms: sum(len(m.content or "") + 40 * len(m.tool_calls) for m in ms)  # noqa: E731
    for step_msg in kept:
        if fixed + size(kept) <= budget:
            break
        if step_msg.role == "tool" and step_msg.content != TRIMMED:
            step_msg.content = TRIMMED
    used = fixed + size(kept)
    tail: list[Message] = []
    for past in reversed(history[-history_limit:]):
        if used + len(past.content) > budget:
            break
        tail.insert(0, past)
        used += len(past.content)
    messages = [ChatMessage(role="system", content=system_text)]
    messages += [ChatMessage(role=h.role.value, content=h.content) for h in tail]  # type: ignore[arg-type]
    messages.append(ChatMessage(role="user", content=task))
    messages.extend(kept)
    return messages


def _arguments(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


async def run(
    ctx: Any,
    session: AsyncSession,
    deps: LoopDeps,
    task: str,
    history: list[Message] | None = None,
    *,
    model: str | None = None,
) -> AsyncIterator[LoopEvent]:
    """Loop events: ToolStarted/ToolFinished/Citations along the way, Token for the final answer,
    and at the end exactly one of Final | Failure."""
    settings = deps.settings
    tools_spec = await deps.tools.list_tools(ctx)
    book = CitationBook()
    steps: list[ChatMessage] = []
    used_tools = nudged = retried_empty = False
    try:
        async with asyncio.timeout(settings.timeout_seconds):
            for step in range(1, settings.max_steps + 1):
                messages = build_messages(
                    ctx.body, history or [], task, steps, num_ctx=deps.num_ctx
                )
                # The last step answers from the facts so far: otherwise the model searches until
                # it hits the limit and fails.
                offered = [] if used_tools and step == settings.max_steps else tools_spec
                # On the first step with tools the text is buffered: it is either a tool call
                # or an answer without facts that the nudge replaces. Later tokens stream at once.
                buffer_text = (
                    bool(tools_spec) and settings.first_step_nudge and not nudged and not used_tools
                )
                text, calls = "", []
                try:
                    async for chunk in deps.models.chat(
                        ctx, session, ChatRequest(model=model, messages=messages, tools=offered)
                    ):
                        if chunk.queued is not None:
                            yield Waiting(ahead=chunk.queued)
                            continue
                        if chunk.text:
                            text += chunk.text
                            if not buffer_text:
                                yield Token(text=chunk.text)
                        if chunk.done:
                            calls = chunk.tool_calls
                except GatewayDenied as exc:
                    yield Failure(code=exc.code, message=exc.reason, status=ExecutionStatus.DENIED)
                    return
                if calls:
                    used_tools = True
                    steps.append(
                        ChatMessage(role="assistant", content=text or None, tool_calls=calls)
                    )
                    for call in calls:
                        args = _arguments(call.arguments)
                        yield ToolStarted(name=call.name, arguments=args)
                        result = await deps.tools.call_tool(
                            ctx, session, call.name, args, meta={"citation_offset": book.count}
                        )
                        status: Literal["done", "denied", "error"] = (
                            "denied"
                            if result.meta.get("denied")
                            else "error"
                            if result.is_error
                            else "done"
                        )
                        if not result.is_error:
                            book.add(result)
                        yield ToolFinished(
                            name=call.name,
                            status=status,
                            data_class=result.data_class.value if result.data_class else None,
                            hits=result.meta.get("hits"),
                            message=result.content if result.is_error else None,
                        )
                        if result.citations or result.assets_denied:
                            yield Citations(citations=book.citations, denied=book.denied)
                        steps.append(
                            ChatMessage(
                                role="tool",
                                name=call.name,
                                tool_call_id=call.id,
                                content=result.content,
                            )
                        )
                    continue
                if buffer_text and text.strip():
                    nudged = True  # an answer without a single tool: one retry with a hint
                    steps.append(ChatMessage(role="assistant", content=text))
                    steps.append(ChatMessage(role="user", content=NUDGE))
                    continue
                if not text.strip():
                    # empty answer (gemma4:e2b after a tool result, 4.11.2026): one retry
                    if not retried_empty:
                        retried_empty = True
                        steps.append(ChatMessage(role="user", content=ANSWER_NUDGE))
                        continue
                    yield Failure(
                        code="empty_answer",
                        message="the model returned an empty answer",
                        status=ExecutionStatus.FAILED,
                    )
                    return
                if buffer_text:
                    yield Token(text=text)
                yield Final(
                    text=text,
                    citations=book.cited(text),
                    all_citations=book.citations,
                    no_tools=not used_tools,
                    steps=step,
                )
                return
            yield Failure(
                code="max_steps",
                message=f"the model gave no answer within {settings.max_steps} steps",
                status=ExecutionStatus.FAILED,
            )
    except TimeoutError:
        yield Failure(
            code="timeout",
            message=f"the agent loop did not finish within {settings.timeout_seconds:.0f} s",
            status=ExecutionStatus.FAILED,
        )
    except GatewayError as exc:
        yield Failure(code=exc.code, message=str(exc), status=ExecutionStatus.FAILED)
