"""Model endpoint (criterion 12): user key, model-api Execution, journal without text."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from openai import AsyncOpenAI
from sqlalchemy import select

from tests.fixtures.corpus import AGENTS_DIR
from tests.fixtures.world import post_and_leave, register_model, seed_chat
from yorath.control.journal import AuditEvent, AuditEventType
from yorath.control.journal.models import TraceSpan
from yorath.control.model import (
    Agent,
    AgentRevision,
    DataClass,
    Execution,
    ExecutionStatus,
    ExecutionTrigger,
    ModelProvider,
    User,
)
from yorath.enforcement.gateway import load_agent, open_context, public
from yorath.enforcement.secrets import DbSecretStore
from yorath.runtime.llm import ChatMessage, ChatRequest, collect

pytestmark = pytest.mark.group

SECRET = "secret-phrase-42"
REPLY = "model-answer-77"


async def journal_text(session, execution_id) -> str:
    spans = (
        await session.scalars(select(TraceSpan).where(TraceSpan.execution_id == execution_id))
    ).all()
    events = (await session.scalars(select(AuditEvent).where(AuditEvent.execution_id == execution_id))).all()  # fmt: skip
    return " ".join(str(s.attributes) for s in spans) + " ".join(str(e.details) for e in events)


@pytest.mark.covers("INV-API-1")
@pytest.mark.parametrize("record", [True, False])
async def test_gateway_skips_content_when_asked(make_app, session, backends, tmp_path, record):
    _, app = await seed_chat(session, make_app, tmp_path)
    backends.reply = [REPLY]
    owner = await session.scalar(select(User).where(User.role == "owner"))
    agent, rev, card, body = await load_agent(session, "model-api", AGENTS_DIR)
    ctx = await open_context(
        session, app.state.journal, agent=agent, revision=rev, card=card, body=body,
        actor=owner, effective_principal=owner, trigger=ExecutionTrigger.API,
    )  # fmt: skip
    ctx.record_content = record
    request = ChatRequest(messages=[ChatMessage(role="user", content=SECRET)])
    await collect(app.state.model_gateway.chat(ctx, session, request))

    text = await journal_text(session, ctx.execution_id)
    assert (
        "input_tokens" in text or "gen_ai.usage.input_tokens" in text
    )  # accounting is always there
    assert (SECRET in text, REPLY in text) == (record, record)


@pytest.fixture
async def endpoint(make_app, session, backends, tmp_path):
    """Owner, local/default and openrouter/default, member anna with an API key."""
    owner, app = await seed_chat(session, make_app, tmp_path)
    await register_model(
        session, "openrouter/default", ModelProvider.OPENROUTER, "fake-or",
        [DataClass.PUBLIC, DataClass.INTERNAL],
    )  # fmt: skip
    body = {"login": "anna", "password": "password-1", "display_name": "anna"}
    assert (await owner.post("/users", json=body)).status_code == 201
    member = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await member.post("/auth/login", json={"login": "anna", "password": "password-1"})
    created = (await member.post("/api-keys", json={"name": "laptop"})).json()
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {created['key']}"},
    )
    backends.reply = [REPLY]
    backends.chat.clear()
    yield SimpleNamespace(app=app, owner=owner, client=client, key=created, member=member)
    for c in (owner, member, client):
        await c.aclose()


def completion(stream: bool = False, model: str = "local/default") -> dict:
    return {"model": model, "messages": [{"role": "user", "content": SECRET}], "stream": stream}


async def api_executions(session) -> list[Execution]:
    return list(
        (await session.scalars(
            select(Execution).where(Execution.trigger == ExecutionTrigger.API)
            .order_by(Execution.created_at)
        )).all()
    )  # fmt: skip


@pytest.mark.covers("INV-API-1", "INV-API-3")
async def test_completion_is_execution_of_model_api_without_text(endpoint, session):
    r = await endpoint.client.post("/v1/chat/completions", json=completion())
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "chat.completion" and data["model"] == "local/default"
    assert data["choices"][0]["message"]["content"] == REPLY
    assert data["usage"]["total_tokens"] > 0

    [execution] = await api_executions(session)
    anna = await session.scalar(select(User).where(User.login == "anna"))
    revision = await session.get(AgentRevision, execution.agent_revision_id)
    agent = await session.get(Agent, revision.agent_id)
    assert agent.name == "model-api" and execution.status is ExecutionStatus.SUCCEEDED
    assert execution.initiated_by_principal_id == execution.effective_principal_id == anna.id

    text = await journal_text(session, execution.id)
    assert "input_tokens" in text  # anchor: accounting is recorded
    assert SECRET not in text and REPLY not in text


async def test_stream_ends_with_usage_and_done(endpoint, session):
    r = await endpoint.client.post("/v1/chat/completions", json=completion(stream=True))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    lines = [ln.removeprefix("data: ") for ln in r.text.split("\n\n") if ln.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    chunks = [json.loads(ln) for ln in lines[:-1]]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in chunks) == REPLY
    assert (
        chunks[-1]["choices"][0]["finish_reason"] == "stop" and chunks[-1]["usage"]["total_tokens"]
    )
    [execution] = await api_executions(session)
    assert execution.status is ExecutionStatus.SUCCEEDED


@pytest.mark.covers("INV-KEY-1")
async def test_request_without_valid_key_is_refused(endpoint, session, backends):
    ok = await endpoint.client.post("/v1/chat/completions", json=completion())
    assert ok.status_code == 200 and len(backends.chat) == 1  # anchor: the key works
    backends.chat.clear()
    anon = AsyncClient(transport=ASGITransport(app=endpoint.app), base_url="http://test")
    wrong = {"Authorization": "Bearer yorath_key_" + "x" * 43}
    for headers in ({}, wrong, {"Authorization": "Basic abc"}):
        r = await anon.post("/v1/chat/completions", json=completion(), headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"
    assert (await anon.get("/v1/models")).status_code == 401

    await endpoint.member.delete(f"/api-keys/{endpoint.key['id']}")
    r = await endpoint.client.post("/v1/chat/completions", json=completion())
    assert r.status_code == 401 and backends.chat == []
    await anon.aclose()


async def test_disabled_user_key_is_refused(endpoint, session, backends):
    assert (await endpoint.client.get("/v1/models")).status_code == 200  # anchor
    anna_id = await session.scalar(select(User.id).where(User.login == "anna"))
    await endpoint.owner.patch(f"/users/{anna_id}", json={"is_active": False})
    r = await endpoint.client.post("/v1/chat/completions", json=completion())
    assert r.status_code == 401 and backends.chat == []


async def test_models_list_shows_the_models_of_the_key(endpoint):
    r = await endpoint.client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()["data"]
    # a new key: every chat model, the external one connected with consent too
    assert [m["id"] for m in data] == ["local/default", "openrouter/default"]
    assert data[0]["context_length"] == 4096 and "fake-chat" not in r.text
    await endpoint.member.patch(f"/api-keys/{endpoint.key['id']}", json={"models": ["local/*"]})
    r = await endpoint.client.get("/v1/models")
    assert [m["id"] for m in r.json()["data"]] == ["local/default"]


@pytest.mark.covers("INV-API-2")
async def test_external_model_is_refused_without_calling_provider(endpoint, session, backends):
    # a key with the local models only (as every key issued before 0.8.0)
    r = await endpoint.member.patch(f"/api-keys/{endpoint.key['id']}", json={"models": ["local/*"]})
    assert r.status_code == 200 and r.json()["models"] == ["local/*"]
    r = await endpoint.client.post(
        "/v1/chat/completions", json=completion(model="openrouter/default")
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "model_not_allowed"
    assert backends.openrouter == [] and backends.chat == []
    unknown = await endpoint.client.post("/v1/chat/completions", json=completion(model="nope"))
    assert unknown.status_code == 403 and unknown.json()["error"]["code"] == "model_not_allowed"

    executions = await api_executions(session)
    assert [e.status for e in executions] == [ExecutionStatus.DENIED, ExecutionStatus.DENIED]
    denied = (await session.scalars(select(AuditEvent).where(
        AuditEvent.execution_id == executions[0].id,
        AuditEvent.event_type == AuditEventType.POLICY_DENIED))).all()  # fmt: skip
    assert [e.model for e in denied] == ["openrouter/default"]


@pytest.mark.covers("INV-API-3")
async def test_client_disconnect_cancels_execution(endpoint, session, backends):
    backends.chat_gate = asyncio.Event()  # the model "thinks" until the client leaves
    request = asyncio.create_task(
        endpoint.client.post("/v1/chat/completions", json=completion(stream=True))
    )
    for _ in range(100):
        if backends.chat:
            break
        await asyncio.sleep(0.02)
    [running] = await api_executions(session)
    assert running.status is ExecutionStatus.RUNNING  # anchor: the request reached the model
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    backends.chat_gate.set()
    await asyncio.gather(*public._closing)
    session.expire_all()
    [execution] = await api_executions(session)
    assert execution.status is ExecutionStatus.CANCELLED


async def test_official_openai_client(endpoint):
    http = AsyncClient(transport=ASGITransport(app=endpoint.app), base_url="http://test")
    client = AsyncOpenAI(base_url="http://test/v1", api_key=endpoint.key["key"], http_client=http)
    models = await client.models.list()
    assert [m.id for m in models.data] == ["local/default", "openrouter/default"]
    answer = await client.chat.completions.create(
        model="local/default", messages=[{"role": "user", "content": "hello"}]
    )
    assert answer.choices[0].message.content == REPLY
    stream = await client.chat.completions.create(
        model="local/default", messages=[{"role": "user", "content": "hello"}], stream=True
    )
    text = "".join([c.choices[0].delta.content or "" async for c in stream if c.choices])
    assert text == REPLY
    await client.close()


def response_request(stream: bool = False, model: str = "local/default") -> dict:
    return {"model": model, "instructions": "Be brief", "input": SECRET, "stream": stream}


def response_events(text: str) -> list[dict]:
    return [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")]


@pytest.mark.covers("INV-API-1", "INV-API-3")
async def test_responses_is_execution_of_model_api_without_text(endpoint, session):
    r = await endpoint.client.post("/v1/responses", json=response_request())
    assert r.status_code == 200
    data = r.json()
    assert (data["object"], data["status"], data["model"]) == (
        "response",
        "completed",
        "local/default",
    )
    assert data["output"][0]["content"][0]["text"] == REPLY
    assert data["usage"]["total_tokens"] > 0

    [execution] = await api_executions(session)
    anna = await session.scalar(select(User).where(User.login == "anna"))
    assert execution.status is ExecutionStatus.SUCCEEDED
    assert execution.initiated_by_principal_id == execution.effective_principal_id == anna.id
    text = await journal_text(session, execution.id)
    assert "input_tokens" in text  # anchor: accounting is recorded
    assert SECRET not in text and REPLY not in text


async def test_responses_stream_completes_and_closes_execution(endpoint, session):
    r = await endpoint.client.post("/v1/responses", json=response_request(stream=True))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = response_events(r.text)
    assert events[0]["type"] == "response.created" and events[-1]["type"] == "response.completed"
    deltas = [e["delta"] for e in events if e["type"] == "response.output_text.delta"]
    assert "".join(deltas) == REPLY
    assert events[-1]["response"]["usage"]["total_tokens"] > 0
    [execution] = await api_executions(session)
    assert execution.status is ExecutionStatus.SUCCEEDED


@pytest.mark.covers("INV-API-3")
async def test_responses_stream_model_error_fails_execution(endpoint, session, backends):
    backends.chat_stream_error = "runner crashed"
    r = await endpoint.client.post("/v1/responses", json=response_request(stream=True))
    assert r.status_code == 200
    events = response_events(r.text)
    assert events[0]["type"] == "response.created"  # anchor: the stream started
    assert events[-1]["type"] == "response.failed"
    assert events[-1]["response"]["error"]["code"] == "model_error"
    [execution] = await api_executions(session)
    assert execution.status is ExecutionStatus.FAILED
    finished = await session.scalar(select(AuditEvent).where(
        AuditEvent.execution_id == execution.id,
        AuditEvent.event_type == AuditEventType.EXECUTION_FINISHED))  # fmt: skip
    assert finished.details["error"] == "model_error"


@pytest.mark.covers("INV-API-2", "INV-KEY-1")
async def test_responses_refuses_without_key_and_foreign_model(endpoint, session, backends):
    ok = await endpoint.client.post("/v1/responses", json=response_request())
    assert ok.status_code == 200 and len(backends.chat) == 1  # anchor: the key and model match
    backends.chat.clear()
    anon = AsyncClient(transport=ASGITransport(app=endpoint.app), base_url="http://test")
    r = await anon.post("/v1/responses", json=response_request())
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"
    await endpoint.member.patch(f"/api-keys/{endpoint.key['id']}", json={"models": ["local/*"]})
    r = await endpoint.client.post(
        "/v1/responses", json=response_request(model="openrouter/default")
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "model_not_allowed"
    assert backends.chat == [] and backends.openrouter == []
    await anon.aclose()


async def test_execution_records_which_key_called(endpoint, session):
    """A person has several keys (Hermes, Codex): the journal shows which one the call came with."""
    second = (await endpoint.member.post("/api-keys", json={"name": "hermes"})).json()
    hermes = AsyncClient(
        transport=ASGITransport(app=endpoint.app), base_url="http://test",
        headers={"Authorization": f"Bearer {second['key']}"},
    )  # fmt: skip
    assert (await hermes.post("/v1/chat/completions", json=completion())).status_code == 200
    assert (
        await endpoint.client.post("/v1/chat/completions", json=completion())
    ).status_code == 200
    first, last = await api_executions(session)
    assert (str(first.api_key_id), str(last.api_key_id)) == (second["id"], endpoint.key["id"])
    started = await session.scalar(select(AuditEvent).where(
        AuditEvent.execution_id == first.id,
        AuditEvent.event_type == AuditEventType.EXECUTION_STARTED))  # fmt: skip
    assert started.details["api_key"] == f"hermes ({second['prefix']}…)"
    assert second["key"] not in str(started.details)  # the key value is not written
    owner_view = (await endpoint.owner.get(f"/journal/executions/{first.id}")).json()
    assert owner_view["chain"][0]["api_key"] == f"hermes ({second['prefix']}…)"
    await hermes.aclose()


@pytest.mark.covers("INV-API-3")
@pytest.mark.parametrize(
    ("path", "body", "last"),
    [
        ("/v1/chat/completions", completion(stream=True), b'"finish_reason": "stop"'),
        ("/v1/responses", response_request(stream=True), b"response.completed"),
    ],
)
async def test_client_leaving_after_last_chunk_still_closes_execution(
    endpoint, session, path, body, last
):
    """Hermes drops the connection right after the last piece: the answer was delivered, so the Execution succeeded with accounting."""
    got = await post_and_leave(endpoint.app, path, body, endpoint.key["key"], last)
    assert last in got  # anchor: the client got the whole answer
    await asyncio.gather(*public._closing)
    session.expire_all()
    [execution] = await api_executions(session)
    assert execution.status is ExecutionStatus.SUCCEEDED
    text = await journal_text(session, execution.id)
    assert "input_tokens" in text


async def test_official_openai_client_responses(endpoint):
    http = AsyncClient(transport=ASGITransport(app=endpoint.app), base_url="http://test")
    client = AsyncOpenAI(base_url="http://test/v1", api_key=endpoint.key["key"], http_client=http)
    answer = await client.responses.create(model="local/default", input="hello")
    assert answer.output_text == REPLY
    stream = await client.responses.create(model="local/default", input="hello", stream=True)
    deltas = [e.delta async for e in stream if e.type == "response.output_text.delta"]
    assert "".join(deltas) == REPLY
    await client.close()


async def test_a_key_with_every_model_reaches_the_external_one(
    endpoint, session, sessions, backends
):
    """Field-fixes-0-7: a new key is "all models", the external model connected with consent included."""
    assert endpoint.key["models"] is None
    endpoint.app.state.secrets = DbSecretStore(sessions, Fernet.generate_key().decode())
    await endpoint.app.state.secrets.put("openrouter_api_key", "sk-or-test")
    r = await endpoint.client.post(
        "/v1/chat/completions", json=completion(model="openrouter/default")
    )
    assert r.status_code == 200 and len(backends.openrouter) == 1
    [execution] = await api_executions(session)
    assert execution.status is ExecutionStatus.SUCCEEDED


async def test_key_models_change_without_reissue(endpoint, session, backends):
    r = await endpoint.member.patch(
        f"/api-keys/{endpoint.key['id']}", json={"models": ["local/default"]}
    )
    assert r.status_code == 200 and r.json()["prefix"] == endpoint.key["prefix"]
    ok = await endpoint.client.post("/v1/chat/completions", json=completion())
    assert ok.status_code == 200  # anchor: the same key value still works
    r = await endpoint.client.post(
        "/v1/chat/completions", json=completion(model="openrouter/default")
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "model_not_allowed"
    assert backends.openrouter == []
    changed = await session.scalar(select(AuditEvent).where(
        AuditEvent.event_type == AuditEventType.API_KEY_MODELS_CHANGED))  # fmt: skip
    assert changed.details["before"] is None and changed.details["after"] == ["local/default"]


async def test_key_models_must_be_catalog_models(endpoint):
    choices = (await endpoint.member.get("/api-keys/models")).json()
    assert [(c["name"], c["external"]) for c in choices] == [
        ("local/default", False),
        ("openrouter/default", True),
    ]
    for models, code in (
        (["nope"], "unknown_model"),
        (["local/embeddings"], "unknown_model"),
        ([], "no_models"),
    ):
        r = await endpoint.member.patch(f"/api-keys/{endpoint.key['id']}", json={"models": models})
        assert r.status_code == 422 and r.json()["detail"]["code"] == code
    other = await endpoint.owner.post(
        "/api-keys", json={"name": "x", "models": ["openrouter/default"]}
    )
    assert other.status_code == 201 and other.json()["models"] == ["openrouter/default"]


async def test_migration_keeps_old_keys_to_the_local_models(database_url):
    """A key issued before 0.8.0 reached only local models: the update keeps it so ("local/*"),
    instead of opening the external models to it; the downgrade drops the column."""
    import uuid

    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    from tests.groups.test_model_placements import _alembic
    from yorath.db import make_engine

    base = make_url(database_url)
    name = f"{base.database}_keys"
    admin = make_engine(base.set(database="postgres").render_as_string(hide_password=False))
    admin = admin.execution_options(isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'drop database if exists "{name}"'))
        await conn.execute(text(f'create database "{name}"'))
    url = base.set(database=name).render_as_string(hide_password=False)
    engine = make_engine(url)
    try:
        _alembic(url, "upgrade", "0005_instance_channels")
        user = uuid.uuid7()
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into principals (id, kind, display_name) values (:id, 'user', 'anna')"
                ),
                {"id": user},
            )
            await conn.execute(
                text("insert into users (principal_id, login, password_hash, role) "
                     "values (:id, 'anna', 'x', 'member')"),
                {"id": user},
            )  # fmt: skip
            await conn.execute(
                text("insert into api_keys (id, user_id, name, prefix, token_hash) "
                     "values (:id, :u, 'laptop', 'yorath_key_abc', 'h')"),
                {"id": uuid.uuid7(), "u": user},
            )  # fmt: skip
        _alembic(url, "upgrade", "head")
        async with engine.connect() as conn:
            assert await conn.scalar(text("select models from api_keys")) == ["local/*"]
        _alembic(url, "downgrade", "0005_instance_channels")
        async with engine.connect() as conn:
            columns = await conn.scalars(text(
                "select column_name from information_schema.columns where table_name = 'api_keys'"))  # fmt: skip
            assert "models" not in columns.all()
    finally:
        await engine.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'drop database if exists "{name}"'))
        await admin.dispose()
