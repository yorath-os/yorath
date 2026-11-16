"""ModelGateway on a live database: PDP call_model with the context class, usage accounting, providers."""

import json

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.fixtures.corpus import AGENTS_DIR, OWNER, REPLY
from tests.fixtures.world import open_for_owner, register_model, seed_chat
from yorath.control.agents.loader import sync_agents_dir
from yorath.control.journal import AuditEvent, AuditEventType, GenAI, SpanKind, TraceSpan
from yorath.control.model import DataClass, ExecutionAttempt, ExecutionStatus, ModelProvider
from yorath.enforcement.gateway import GatewayDenied, GatewayError, finish
from yorath.runtime.llm import ChatMessage, ChatRequest, collect

pytestmark = pytest.mark.group

QUESTION = [ChatMessage(role="user", content="How many vacation days can I carry over?")]


@pytest.mark.covers("INV-MOD-3", "INV-MOD-2")
async def test_chat_through_gateway_accounts_usage(make_app, session, tmp_path, backends):
    api, app = await seed_chat(session, make_app, tmp_path)
    ctx = await open_for_owner(app, session)
    gw = app.state.model_gateway
    out = await collect(gw.chat(ctx, session, ChatRequest(messages=QUESTION)))
    assert out.text == "".join(REPLY) and out.usage.input_tokens == 321
    out2 = await collect(
        gw.chat(ctx, session, ChatRequest(messages=QUESTION, model="local/default"))
    )
    assert out2.text == out.text
    assert ctx.usage == {"input_tokens": 642, "output_tokens": 24, "calls": 2}
    assert ctx.model is not None and ctx.model.name == "local/default"
    assert backends.chat[0]["think"] is False

    spans = (
        await session.scalars(select(TraceSpan).where(TraceSpan.execution_id == ctx.execution_id))
    ).all()
    llm = [s for s in spans if s.kind is SpanKind.LLM]
    assert len(llm) == 2 and llm[0].model == "fake-chat"
    assert llm[0].attributes[GenAI.OUTPUT_MESSAGES][0]["content"] == out.text
    assert (
        llm[0].attributes[GenAI.INPUT_TOKENS] == 321
        and llm[0].attributes["yorath.data_class"] == "public"
    )
    audit = (await session.scalars(select(AuditEvent).where(
        AuditEvent.execution_id == ctx.execution_id, AuditEvent.event_type == AuditEventType.LLM_CALLED
    ))).all()  # fmt: skip
    assert (
        len(audit) == 2
        and audit[0].model == "fake-chat"
        and audit[0].data_class is DataClass.PUBLIC
    )
    assert "56" not in json.dumps(audit[0].details)

    await finish(ctx, session, app.state.journal, ExecutionStatus.SUCCEEDED)
    attempt = await session.scalar(
        select(ExecutionAttempt).where(ExecutionAttempt.execution_id == ctx.execution_id)
    )
    assert attempt.model == "fake-chat" and attempt.usage["calls"] == 2 and attempt.cost_estimated

    with pytest.raises(GatewayDenied) as denied:
        await collect(gw.chat(ctx, session, ChatRequest(messages=QUESTION, model="local/other")))
    assert denied.value.code == "model_not_allowed"
    await api.aclose()


@pytest.mark.covers("INV-MOD-1")
async def test_model_denied_by_context_class(make_app, session, tmp_path, backends):
    api, app = await seed_chat(session, make_app, tmp_path, external_classes=[DataClass.PUBLIC])
    ctx = await open_for_owner(app, session)
    assert ctx.raise_class(DataClass.CONFIDENTIAL)
    with pytest.raises(GatewayDenied) as denied:
        await collect(app.state.model_gateway.chat(ctx, session, ChatRequest(messages=QUESTION)))
    assert denied.value.code == "model_denied" and "confidential" in denied.value.reason
    assert "no permitting policy" in denied.value.reason and backends.chat == []
    event = await session.scalar(select(AuditEvent).where(
        AuditEvent.execution_id == ctx.execution_id, AuditEvent.event_type == AuditEventType.POLICY_DENIED
    ))  # fmt: skip
    assert event.model == "fake-chat" and event.data_class is DataClass.CONFIDENTIAL
    assert not [s for s in (await session.scalars(select(TraceSpan).where(
        TraceSpan.execution_id == ctx.execution_id))).all() if s.kind is SpanKind.LLM]  # fmt: skip
    await api.aclose()


@pytest.mark.covers("INV-MOD-6")
async def test_openrouter_provider_needs_key_from_secret_store(
    make_app, session, tmp_path, backends
):
    backends.openrouter_reply, backends.openrouter_usage = "external answer", (5, 2)
    app = make_app(
        documents_dir=tmp_path / "docs",
        agents_dir=AGENTS_DIR,
        secret_key=Fernet.generate_key().decode(),
    )
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await api.post("/auth/setup", json=OWNER)
    await sync_agents_dir(session, AGENTS_DIR)
    await register_model(session, "openrouter/default", ModelProvider.OPENROUTER, "openai/x",
                         [DataClass.PUBLIC, DataClass.INTERNAL])  # fmt: skip
    ctx = await open_for_owner(app, session)
    ctx.card = ctx.card.model_copy(update={"model": "openrouter/default"})
    with pytest.raises(GatewayError, match="OpenRouter key"):
        await collect(app.state.model_gateway.chat(ctx, session, ChatRequest(messages=QUESTION)))
    await app.state.secrets.put("openrouter_api_key", "sk-or-test")
    out = await collect(app.state.model_gateway.chat(ctx, session, ChatRequest(messages=QUESTION)))
    assert out.text == "external answer" and backends.openrouter[0]["auth"] == "Bearer sk-or-test"
    assert backends.openrouter[0]["body"]["model"] == "openai/x" and ctx.usage["input_tokens"] == 5
    await api.aclose()


@pytest.mark.covers("INV-MOD-5")
async def test_runner_crash_is_retried_once(make_app, session, tmp_path, backends):
    """Ollama OOM-kills the runner: the first call gets 500, the retry passes; two in a row is an error."""
    backends.chat_failures, backends.reply = 1, ["answer"]
    app = make_app(documents_dir=tmp_path / "docs", agents_dir=AGENTS_DIR)
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await api.post("/auth/setup", json=OWNER)
    from yorath.control.agents.loader import sync_agents_dir

    await sync_agents_dir(session, AGENTS_DIR)
    await register_model(
        session, "local/default", ModelProvider.OLLAMA, "fake-chat", list(DataClass)
    )
    ctx = await open_for_owner(app, session)
    out = await collect(app.state.model_gateway.chat(ctx, session, ChatRequest(messages=QUESTION)))
    assert out.text == "answer" and len(backends.chat) == 2 and ctx.usage["calls"] == 1
    span = await session.scalar(select(TraceSpan).where(
        TraceSpan.execution_id == ctx.execution_id, TraceSpan.kind == SpanKind.LLM))  # fmt: skip
    assert span.attributes["yorath.retries"] == 1

    backends.chat.clear()
    backends.chat_failures = 2  # two failures in a row: an honest error, not an endless retry
    with pytest.raises(GatewayError, match="unexpectedly stopped"):
        await collect(app.state.model_gateway.chat(ctx, session, ChatRequest(messages=QUESTION)))
    assert len(backends.chat) == 2
    await api.aclose()


class _Silent:
    """A provider that is silent the first time (like a hung OpenRouter), then answers."""

    name = "silent"

    def __init__(self, silent_calls: int) -> None:
        self.silent_calls, self.calls = silent_calls, 0

    async def chat(self, model_id, request):
        import asyncio

        from yorath.runtime.llm import ChatChunk, Usage

        self.calls += 1
        if self.calls <= self.silent_calls:
            await asyncio.sleep(30)
        yield ChatChunk()
        yield ChatChunk(text="answer")
        yield ChatChunk(
            done=True, finish_reason="stop", usage=Usage(input_tokens=7, output_tokens=1)
        )


async def _silent_gateway(app, provider):
    from yorath.enforcement.gateway.models import ModelGateway

    async def factory(_model, _lease):
        return provider

    return ModelGateway(
        app.state.pdp, app.state.journal, factory, app.state.model_slots,
        timeout_seconds=5, first_token_seconds=0.2, retry_delay_seconds=0,
    )  # fmt: skip


@pytest.mark.covers("INV-MOD-5")
async def test_silent_model_is_retried_after_first_token_timeout(
    make_app, session, tmp_path, backends
):
    api, app = await seed_chat(session, make_app, tmp_path)
    ctx = await open_for_owner(app, session)
    provider = _Silent(silent_calls=1)
    gw = await _silent_gateway(app, provider)
    out = await collect(gw.chat(ctx, session, ChatRequest(messages=QUESTION)))
    assert out.text == "answer" and provider.calls == 2 and ctx.usage["calls"] == 1
    span = await session.scalar(select(TraceSpan).where(
        TraceSpan.execution_id == ctx.execution_id, TraceSpan.kind == SpanKind.LLM))  # fmt: skip
    assert span.attributes["yorath.retries"] == 1
    await api.aclose()


@pytest.mark.covers("INV-MOD-5", "INV-MOD-8")
async def test_silent_model_twice_is_failed_call_in_audit(make_app, session, tmp_path, backends):
    api, app = await seed_chat(session, make_app, tmp_path)
    ctx = await open_for_owner(app, session)
    provider = _Silent(silent_calls=2)
    gw = await _silent_gateway(app, provider)
    with pytest.raises(GatewayError) as failed:
        await collect(gw.chat(ctx, session, ChatRequest(messages=QUESTION)))
    assert failed.value.code == "model_timeout" and provider.calls == 2
    assert str(failed.value) == "model did not start answering within 0 s"
    audit = (await session.scalars(select(AuditEvent).where(
        AuditEvent.execution_id == ctx.execution_id, AuditEvent.event_type == AuditEventType.LLM_CALLED
    ))).all()  # fmt: skip
    assert len(audit) == 1 and audit[0].model == "fake-chat"
    assert audit[0].details["status"] == "failed" and audit[0].details["error"] == "model_timeout"
    assert audit[0].cost_usd == 0 and ctx.usage.get("calls", 0) == 0
    await api.aclose()
