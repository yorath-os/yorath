"""Model Gateway shape is OpenAI Chat Completions: messages, tools, calls, usage.

Our own minimal models: no need for the ``openai`` dependency just for types; the fields are stable.
Providers (Ollama, OpenRouter) translate to their APIs and back.
"""

import secrets
from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class ProviderError(RuntimeError):
    """Provider error (HTTP, format, broken stream), not a policy decision."""


def new_call_id() -> str:
    return f"call_{secrets.token_hex(6)}"


class ToolCall(BaseModel):
    id: str = Field(default_factory=new_call_id)
    name: str
    arguments: str = "{}"  # JSON string, as in OpenAI

    def openai(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


class Usage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_duration_ns: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] = []
    tool_call_id: str | None = None
    name: str | None = None  # tool name for role=tool

    def openai(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            out["tool_calls"] = [t.openai() for t in self.tool_calls]
        if self.tool_call_id:
            out["tool_call_id"] = self.tool_call_id
        if self.name:
            out["name"] = self.name
        return out


class ChatRequest(BaseModel):
    model: str | None = None  # Model Resource name; None means the model from the agent header
    messages: list[ChatMessage]
    tools: list[dict[str, Any]] = []
    stream: bool = True
    temperature: float = 0
    max_tokens: int | None = None


class ChatChunk(BaseModel):
    """A piece of the answer: text as it is generated; the last one (``done``) has full calls and
    usage."""

    text: str = ""
    tool_calls: list[ToolCall] = []
    done: bool = False
    finish_reason: str | None = None
    usage: Usage | None = None
    model: str | None = None
    # Not a piece of the answer: the call waits for a model server, with this many calls ahead
    # (change model-queue). The chat shows it; OpenAI-compatible endpoints skip it.
    queued: int | None = None


async def first_answer_chunk(chunks: AsyncIterator[ChatChunk]) -> ChatChunk:
    """The first piece that belongs to the answer. A call to a local model may wait for a model
    server first (``queued`` pieces): endpoints that answer with an HTTP status wait here, so a
    queue refusal still comes as a status and not in the middle of a stream."""
    while True:
        chunk = await anext(chunks)
        if chunk.queued is None:
            return chunk


class ChatProvider(Protocol):
    name: str

    def chat(self, model_id: str, request: ChatRequest) -> AsyncIterator[ChatChunk]: ...


class Collected(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = []
    finish_reason: str | None = None
    usage: Usage = Usage()
    model: str | None = None


async def collect(chunks: AsyncIterator[ChatChunk]) -> Collected:
    out = Collected()
    async for c in chunks:
        out.text += c.text
        if c.done:
            out.tool_calls, out.finish_reason = c.tool_calls, c.finish_reason
            out.usage, out.model = c.usage or Usage(), c.model
    return out
