"""``/chat/*``: conversations and an SSE answer stream. Open to every signed-in user; the PDP
decides rights."""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from yorath.control.agents.service import latest_revisions
from yorath.control.cluster.placements import card_allows, card_rank
from yorath.control.identity.deps import CurrentUser, Db
from yorath.control.model import Agent, Conversation, Message, ModelProvider, ModelResource
from yorath.control.setup.openrouter import LEAVES_SERVER
from yorath.enforcement.gateway import GatewayError, load_agent
from yorath.runtime.chat.runs import AnswerInProgress, ChatRuns, Event
from yorath.runtime.chat.service import AGENT_NAME, ChatDeps, ConnectedDeps, run_answer, sse

router = APIRouter(prefix="/chat", tags=["chat"])


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    answering: bool = False


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    execution_id: uuid.UUID | None
    citations: list[dict[str, Any]]
    created_at: datetime


class ConversationIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    agent: str | None = Field(
        default=None, max_length=100, description="agent with the chat trigger"
    )


class ChatAgentOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    default: bool


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    model: str | None = Field(
        default=None, max_length=100, description="the agent's Model Resource"
    )


class ChatModelOut(BaseModel):
    name: str
    model_id: str
    provider: str
    data_classes: list[str]
    default: bool
    input_price_per_mtok: Decimal | None
    output_price_per_mtok: Decimal | None
    leaves_server: str | None


def chat_deps(request: Request) -> ChatDeps:
    state = request.app.state
    return ChatDeps(
        journal=state.journal,
        contexts=state.contexts,
        tools=state.tool_gateway,
        models=state.model_gateway,
        loop=state.manifest.agent_loop,
        agents_dir=state.settings.agents_dir,
        sessions=state.sessions,
        num_ctx=state.manifest.ollama.context_length,
        connected=ConnectedDeps(state.http, state.secrets, state.manifest.connected_agents),
    )


async def _own(db: Db, user_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation:
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user_id:
        raise HTTPException(404, detail="conversation_not_found")
    return conv


@router.get("/agents", response_model=list[ChatAgentOut])
async def list_agents(_: CurrentUser, db: Db):
    """Enabled agents whose latest revision declares the ``chat`` trigger."""
    revisions = await latest_revisions(db)
    rows = (
        await db.scalars(select(Agent).where(Agent.enabled.is_(True)).order_by(Agent.name))
    ).all()
    return [
        ChatAgentOut(id=a.id, name=a.name, description=a.description, default=a.name == AGENT_NAME)
        for a in rows
        if a.id in revisions
        and "chat" in (revisions[a.id].front_matter.get("triggers") or ["chat"])
    ]


@router.get("/models", response_model=list[ChatModelOut])
async def list_models(_: CurrentUser, db: Db, request: Request, agent: str = AGENT_NAME):
    """Models allowed to the chat agent: registered and enabled, the agent's model first."""
    try:
        _agent, _rev, card, _body = await load_agent(
            db, agent, request.app.state.settings.agents_dir
        )
    except GatewayError as exc:
        raise HTTPException(404, detail=exc.code) from exc
    enabled = await db.scalars(
        select(ModelResource).where(ModelResource.enabled.is_(True)).order_by(ModelResource.name)
    )
    rows = [m for m in enabled.all() if card_allows(card.models, m.name)]
    return [
        ChatModelOut(
            name=m.name,
            model_id=m.model_id,
            provider=m.provider.value,
            data_classes=m.allowed_data_classes,
            default=m.name == card.model,
            input_price_per_mtok=m.input_price_per_mtok,
            output_price_per_mtok=m.output_price_per_mtok,
            leaves_server=LEAVES_SERVER if m.provider is ModelProvider.OPENROUTER else None,
        )
        for m in sorted(rows, key=lambda m: card_rank(card.models, m.name))
    ]


def _runs(request: Request) -> ChatRuns:
    return request.app.state.chat_runs


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(user: CurrentUser, db: Db, request: Request):
    rows = (
        await db.scalars(
            select(Conversation)
            .where(Conversation.user_id == user.id)
            .order_by(Conversation.updated_at.desc())
        )
    ).all()
    runs = _runs(request)
    return [
        ConversationOut.model_validate(c).model_copy(update={"answering": c.id in runs})
        for c in rows
    ]


@router.post("/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(data: ConversationIn, user: CurrentUser, db: Db):
    agent = await db.scalar(select(Agent).where(Agent.name == (data.agent or AGENT_NAME)))
    if agent is None:
        raise HTTPException(404 if data.agent else 409, detail="agent_not_found")
    conv = Conversation(user_id=user.id, agent_id=agent.id, title=data.title or "New conversation")
    db.add(conv)
    await db.commit()
    return conv


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(conversation_id: uuid.UUID, user: CurrentUser, db: Db):
    conv = await _own(db, user.id, conversation_id)
    return (
        await db.scalars(
            select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at)
        )
    ).all()


def _stream(events: AsyncIterator[Event]) -> StreamingResponse:
    async def body() -> AsyncIterator[bytes]:
        async for event, payload in events:
            yield sse(event, payload)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID, data: MessageIn, user: CurrentUser, db: Db, request: Request
) -> StreamingResponse:
    """Starts the answer as a core task and subscribes the request to it. A disconnect does not
    cancel the answer."""
    conv = await _own(db, user.id, conversation_id)
    deps = chat_deps(request)
    try:
        current = _runs(request).start(
            conv.id,
            lambda r: run_answer(deps, r, user.id, conv.id, data.content, data.model),
        )
    except AnswerInProgress:
        raise HTTPException(409, detail="answer_in_progress") from None
    return _stream(current.subscribe())


@router.get("/conversations/{conversation_id}/stream", response_model=None)
async def stream_answer(
    conversation_id: uuid.UUID, user: CurrentUser, db: Db, request: Request
) -> StreamingResponse | Response:
    """Events of the running answer from the start; no answer gives 204."""
    conv = await _own(db, user.id, conversation_id)
    current = _runs(request).get(conv.id)
    if current is None:
        return Response(status_code=204)
    return _stream(current.subscribe())


@router.post("/conversations/{conversation_id}/cancel", status_code=202)
async def cancel_answer(conversation_id: uuid.UUID, user: CurrentUser, db: Db, request: Request):
    """Stops the answer and returns once the Execution is closed and the message is written."""
    conv = await _own(db, user.id, conversation_id)
    if not await _runs(request).cancel(conv.id):
        raise HTTPException(404, detail="no_answer_in_progress")
    return {"status": "cancelled"}
