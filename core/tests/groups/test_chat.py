"""Chat as an Execution on a live database with a fake Ollama: loop, trace, audit, Outcome, filter before the LLM."""

import asyncio
import json
import uuid

import pytest
from sqlalchemy import select

from tests.fixtures.corpus import NO_HITS_REPLY, REPLY
from tests.fixtures.world import ask, ask_model, event, external_chat, parse_sse, seed_chat
from yorath.control.journal import AuditEvent, AuditEventType, GenAI, SpanKind, TraceSpan
from yorath.control.model import (
    AttemptStatus,
    DataClass,
    Execution,
    ExecutionAttempt,
    ExecutionStatus,
    Message,
    Outcome,
    User,
)

pytestmark = pytest.mark.group


@pytest.mark.covers("INV-CLS-1", "INV-EXEC-3", "INV-MOD-3", "INV-AUD-2")
async def test_question_becomes_execution_with_trace_and_outcome(
    make_app, session, tmp_path, backends
):
    api, app = await seed_chat(session, make_app, tmp_path)
    conv = (await api.post("/chat/conversations", json={})).json()
    events = await ask(api, conv["id"], "How many vacation days can I carry over?")

    assert events[0][0] == "started" and events[0][1]["started_at"]
    events = events[1:]
    kinds = [e for e, _ in events]
    assert kinds[:4] == ["tool", "tool", "citations", "token"] and kinds[-1] == "done"
    assert kinds.count("token") == 3
    assert events[0][1] == {"name": "search_docs", "status": "started",
                            "arguments": {"query": "How many vacation days can I carry over?"}}  # fmt: skip
    assert events[1][1]["status"] == "done" and events[1][1]["data_class"] == "internal"
    citations = event(events, "citations")["citations"]
    assert citations and citations[0]["heading_path"] == "Vacation policy > Carryover"
    assert event(events, "citations")["denied"] == [
        "hr-salaries"
    ]  # the agent is given only team-docs
    done = events[-1][1]
    assert done["status"] == "succeeded" and done["text"] == "".join(REPLY)
    assert done["usage"] == {"input_tokens": 642, "output_tokens": 24, "calls": 2}
    assert (
        len(backends.chat) == 2
        and backends.chat[0]["tools"][0]["function"]["name"] == "search_docs"
    )

    execution = await session.get(Execution, uuid.UUID(done["execution_id"]))
    owner = await session.scalar(select(User).where(User.role == "owner"))
    assert execution.status is ExecutionStatus.SUCCEEDED
    assert execution.initiated_by_principal_id == execution.effective_principal_id == owner.id
    assert execution.max_data_class_seen is DataClass.INTERNAL
    attempt = await session.scalar(
        select(ExecutionAttempt).where(ExecutionAttempt.execution_id == execution.id)
    )
    assert attempt.status is AttemptStatus.SUCCEEDED and attempt.model == "fake-chat"
    assert attempt.usage["output_tokens"] == 24 and attempt.cost_estimated is True
    outcome = await session.scalar(select(Outcome).where(Outcome.execution_id == execution.id))
    assert outcome.kind.value == "answer" and outcome.data["citations"] == [
        c["n"] for c in citations
    ]

    spans = (await session.scalars(
        select(TraceSpan).where(TraceSpan.execution_id == execution.id).order_by(TraceSpan.started_at)
    )).all()  # fmt: skip
    assert [s.kind for s in spans] == [
        SpanKind.EXECUTION,
        SpanKind.LLM,
        SpanKind.TOOL,
        SpanKind.LLM,
    ]
    assert all(s.trace_id == execution.id for s in spans)
    root, first_llm, tool, second_llm = spans
    assert tool.parent_span_id == root.span_id and second_llm.parent_span_id == root.span_id
    assert (
        root.attributes["yorath.loop.steps"] == 2
        and root.attributes["yorath.loop.no_tools"] is False
    )
    assert root.attributes["yorath.tools.unavailable"] == []
    assert (
        first_llm.attributes["yorath.tools"] == ["search_docs", "query_records", "ask_agent"]
        and first_llm.model == "fake-chat"
    )
    prompt = json.dumps(second_llm.attributes[GenAI.INPUT_MESSAGES], ensure_ascii=False)
    assert (
        "56 days" in prompt and "350000" not in prompt
    )  # the forbidden source is not in the prompt
    assert (
        "You are an assistant for internal documents" in prompt
    )  # the md agent body is the system prompt
    assert second_llm.attributes[GenAI.INPUT_TOKENS] == 321

    audit = (
        await session.scalars(select(AuditEvent).where(AuditEvent.execution_id == execution.id))
    ).all()
    types = [a.event_type for a in audit]
    for t in (
        AuditEventType.EXECUTION_STARTED,
        AuditEventType.TOOL_CALLED,
        AuditEventType.DATA_ACCESSED,
        AuditEventType.POLICY_DENIED,
        AuditEventType.LLM_CALLED,
        AuditEventType.OUTCOME_RECORDED,
        AuditEventType.EXECUTION_FINISHED,
    ):
        assert t in types, t
    llm_audit = [a for a in audit if a.event_type is AuditEventType.LLM_CALLED]
    assert len(llm_audit) == 2 and llm_audit[1].data_class is DataClass.INTERNAL
    assert all(a.model == "fake-chat" and "56" not in json.dumps(a.details) for a in llm_audit)
    denied = next(a for a in audit if a.event_type is AuditEventType.POLICY_DENIED)
    assert "agent-not-allowed-for-asset" in denied.reason

    msgs = (await api.get(f"/chat/conversations/{conv['id']}/messages")).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert (
        msgs[1]["citations"][0]["filename"] == "hr-policy.md"
        and msgs[1]["execution_id"] == done["execution_id"]
    )
    convs = (await api.get("/chat/conversations")).json()
    assert convs[0]["title"].startswith("How many vacation days")
    # the history goes into the next prompt
    await ask(api, conv["id"], "And how many days ahead do I submit a request?")
    assert any(m["content"] == "".join(REPLY) for m in backends.chat[-1]["messages"])
    await api.aclose()


@pytest.mark.covers("INV-MOD-1", "INV-EXEC-2")
async def test_model_denied_by_class_blocks_call(make_app, session, tmp_path, backends):
    api, app = await seed_chat(session, make_app, tmp_path, external_classes=[DataClass.PUBLIC])
    conv = (await api.post("/chat/conversations", json={})).json()
    events = await ask(api, conv["id"], "How many vacation days can I carry over?")
    kinds = [e for e, _ in events]
    # the first call (public context) is allowed; after search_docs the context is internal: denied
    assert "error" in kinds and "token" not in kinds and len(backends.chat) == 1
    err = event(events, "error")
    assert err["code"] == "model_denied" and "model-allowed-class" not in err["message"]
    assert "no permitting policy" in err["message"]
    done = events[-1][1]
    execution = await session.get(Execution, uuid.UUID(done["execution_id"]))
    assert execution.status is ExecutionStatus.DENIED
    assert await session.scalar(select(Outcome).where(Outcome.execution_id == execution.id)) is None
    denied = (await session.scalars(
        select(AuditEvent).where(AuditEvent.execution_id == execution.id,
                                 AuditEvent.event_type == AuditEventType.POLICY_DENIED)
    )).all()  # fmt: skip
    assert any(a.model == "fake-chat" and a.data_class is DataClass.INTERNAL for a in denied)
    msgs = (await api.get(f"/chat/conversations/{conv['id']}/messages")).json()
    assert msgs[-1]["content"].startswith("Denied by policy: call to model")
    await api.aclose()


async def test_no_hits_gives_honest_answer_from_model(make_app, session, tmp_path, backends):
    api, app = await seed_chat(session, make_app, tmp_path)
    conv = (await api.post("/chat/conversations", json={})).json()
    events = await ask(api, conv["id"], "qwertyuiop asdfghjkl")
    done = events[-1][1]
    assert done["status"] == "succeeded" and done["text"] == NO_HITS_REPLY
    assert len(backends.chat) == 2
    cites = event(events, "citations")
    assert cites["citations"] == [] and cites["denied"] == ["hr-salaries"]
    tool_done = [p for e, p in events if e == "tool" and p["status"] == "done"][0]
    assert tool_done["hits"] == 0
    await api.aclose()


@pytest.mark.covers("INV-ID-3")
async def test_member_can_chat_and_cannot_read_others(make_app, session, tmp_path):
    api, app = await seed_chat(session, make_app, tmp_path)
    owner_conv = (await api.post("/chat/conversations", json={})).json()
    await api.post(
        "/users", json={"login": "anna", "password": "anna-pass-1", "display_name": "Anna"}
    )
    await api.post("/auth/logout")
    await api.post("/auth/login", json={"login": "anna", "password": "anna-pass-1"})
    assert (await api.get(f"/chat/conversations/{owner_conv['id']}/messages")).status_code == 404
    conv = (await api.post("/chat/conversations", json={"title": "Vacations"})).json()
    events = await ask(api, conv["id"], "How many vacation days can I carry over?")
    assert events[-1][1]["status"] == "succeeded" and event(events, "citations")["citations"]
    msg = await session.scalar(select(Message).where(Message.conversation_id == uuid.UUID(conv["id"]),
                                                     Message.role == "assistant"))  # fmt: skip
    assert msg is not None
    await api.aclose()


async def _start(
    app, session, conv_id: str, text: str = "How many vacation days can I carry over?"
):
    """Starts the answer like the POST route (through the registry) and waits for ``started``: the Execution is running.

    ASGITransport returns the body only whole, so a hanging stream cannot be opened over HTTP.
    """
    from types import SimpleNamespace

    from yorath.runtime.chat.router import chat_deps
    from yorath.runtime.chat.service import run_answer

    owner = await session.scalar(select(User).where(User.role == "owner"))
    deps = chat_deps(SimpleNamespace(app=app))  # type: ignore[arg-type]
    cid = uuid.UUID(conv_id)
    run = app.state.chat_runs.start(cid, lambda r: run_answer(deps, r, owner.id, cid, text, None))
    async with asyncio.timeout(5):
        while not any(kind == "started" for kind, _ in run.events):
            await asyncio.sleep(0.01)
    return run


async def _wait_finished(app, session):
    for run in [r for r in list(app.state.chat_runs._runs.values()) if r.task]:
        await asyncio.wait({run.task})
    query = select(Execution).order_by(Execution.created_at.desc()).limit(1)
    execution = await session.scalar(query)
    await session.refresh(execution)
    return execution


async def _messages(api, conv_id: str) -> list[dict]:
    return (await api.get(f"/chat/conversations/{conv_id}/messages")).json()


@pytest.mark.covers("INV-EXEC-1")
async def test_client_disconnect_does_not_cancel_answer(make_app, session, tmp_path, backends):
    api, app = await seed_chat(session, make_app, tmp_path)
    conv = (await api.post("/chat/conversations", json={})).json()
    backends.chat_gate = asyncio.Event()
    run = await _start(app, session, conv["id"])
    subscription = run.subscribe()
    assert (await anext(subscription))[0] == "started"
    await subscription.aclose()  # the client left the page mid-answer
    assert not run.queues
    listed = (await api.get("/chat/conversations")).json()
    assert listed[0]["answering"] is True  # the answer goes on without the client

    backends.chat_gate.set()
    execution = await _wait_finished(app, session)
    assert execution.status is ExecutionStatus.SUCCEEDED
    saved = await _messages(api, conv["id"])
    assert [m["role"] for m in saved] == ["user", "assistant"]
    assert saved[1]["content"] == "".join(REPLY)
    assert await session.scalar(select(Outcome).where(Outcome.execution_id == execution.id))
    assert (await api.get("/chat/conversations")).json()[0]["answering"] is False
    await api.aclose()


@pytest.mark.covers("INV-EXEC-1")
async def test_return_to_running_answer_replays_events(make_app, session, tmp_path, backends):
    api, app = await seed_chat(session, make_app, tmp_path)
    conv = (await api.post("/chat/conversations", json={})).json()
    url = f"/chat/conversations/{conv['id']}"
    assert (await api.get(f"{url}/stream")).status_code == 204  # anchor: no answer, no subscription
    backends.chat_gate = asyncio.Event()
    run = await _start(app, session, conv["id"])
    returned = asyncio.create_task(api.get(f"{url}/stream"))  # the screen is opened again
    async with asyncio.timeout(5):
        while not run.queues:
            await asyncio.sleep(0.01)
    backends.chat_gate.set()
    r = await returned
    assert r.status_code == 200
    events = parse_sse(r.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "started" and kinds[-1] == "done"
    assert (
        "tool" in kinds and "token" in kinds
    )  # events from the start of the answer, not just the tail
    assert events[-1][1]["status"] == "succeeded"
    await api.aclose()


@pytest.mark.covers("INV-EXEC-1")
async def test_cancel_stops_answer_and_records_message(make_app, session, tmp_path, backends):
    api, app = await seed_chat(session, make_app, tmp_path)
    conv = (await api.post("/chat/conversations", json={})).json()
    url = f"/chat/conversations/{conv['id']}"
    assert (await api.post(f"{url}/cancel")).status_code == 404
    backends.chat_gate = asyncio.Event()
    run = await _start(app, session, conv["id"])
    r = await api.post(f"{url}/cancel")
    assert r.status_code == 202
    assert run.events[-1][0] == "done" and run.events[-1][1]["status"] == "cancelled"

    execution = await _wait_finished(app, session)
    assert execution.status is ExecutionStatus.CANCELLED and execution.finished_at is not None
    attempt = await session.scalar(
        select(ExecutionAttempt).where(ExecutionAttempt.execution_id == execution.id)
    )
    assert attempt.status is AttemptStatus.CANCELLED
    assert attempt.error_message == "stopped by the user"
    finished = await session.scalar(select(AuditEvent).where(
        AuditEvent.execution_id == execution.id, AuditEvent.event_type == AuditEventType.EXECUTION_FINISHED
    ))  # fmt: skip
    assert finished.details["status"] == "cancelled"
    assert await session.scalar(select(Outcome).where(Outcome.execution_id == execution.id)) is None
    saved = await _messages(api, conv["id"])
    assert [(m["role"], m["content"]) for m in saved] == [
        ("user", "How many vacation days can I carry over?"),
        ("assistant", "Answer stopped."),
    ]
    await api.aclose()


async def test_second_question_while_answering_gets_409(make_app, session, tmp_path, backends):
    api, app = await seed_chat(session, make_app, tmp_path)
    conv = (await api.post("/chat/conversations", json={})).json()
    other = (await api.post("/chat/conversations", json={})).json()
    backends.chat_gate = asyncio.Event()
    await _start(app, session, conv["id"])
    busy = await api.post(f"/chat/conversations/{conv['id']}/messages", json={"content": "more"})
    assert busy.status_code == 409 and busy.json()["detail"] == "answer_in_progress"
    await _start(app, session, other["id"])  # another conversation is not blocked
    backends.chat_gate.set()
    await _wait_finished(app, session)
    saved = await _messages(api, conv["id"])
    assert [m["content"] for m in saved if m["role"] == "user"] == [
        "How many vacation days can I carry over?"
    ]
    await api.aclose()


@pytest.mark.covers("INV-EXEC-1")
async def test_core_shutdown_cancels_running_answer(make_app, session, tmp_path, backends):
    api, app = await seed_chat(session, make_app, tmp_path)
    conv = (await api.post("/chat/conversations", json={})).json()
    backends.chat_gate = asyncio.Event()
    await _start(app, session, conv["id"])
    await app.state.chat_runs.shutdown()
    execution = await _wait_finished(app, session)
    assert execution.status is ExecutionStatus.CANCELLED
    attempt = await session.scalar(
        select(ExecutionAttempt).where(ExecutionAttempt.execution_id == execution.id)
    )
    assert attempt.error_message == "stopped by the core"
    await api.aclose()


@pytest.mark.covers("INV-MOD-2", "INV-MOD-4")
async def test_external_model_chosen_in_chat(make_app, session, tmp_path, backends):
    api = await external_chat(session, make_app, tmp_path)
    models = (await api.get("/chat/models")).json()
    assert [m["name"] for m in models] == ["local/default", "openrouter/default"]
    assert models[0]["default"] and models[0]["leaves_server"] is None
    assert "leave the server" in models[1]["leaves_server"] and models[1]["data_classes"] == [
        "public",
        "internal",
    ]

    conv = (await api.post("/chat/conversations", json={})).json()
    events = await ask_model(
        api, conv["id"], "How many vacation days can I carry over?", "openrouter/default"
    )
    done = events[-1][1]
    assert done["status"] == "succeeded" and done["model"] == "openrouter/default"
    assert done["text"] == "External answer [1]." and backends.chat == []
    assert backends.openrouter[0]["auth"] == "Bearer sk-or-test"
    assert (
        backends.openrouter[0]["body"]["max_tokens"] == 2048
    )  # otherwise the model maximum is reserved
    attempt = await session.scalar(select(ExecutionAttempt).where(
        ExecutionAttempt.execution_id == uuid.UUID(done["execution_id"])))  # fmt: skip
    assert attempt.model == "openai/x" and float(attempt.cost_usd) == 0.006
    llm = (await session.scalars(select(TraceSpan).where(
        TraceSpan.execution_id == attempt.execution_id, TraceSpan.kind == SpanKind.LLM))).first()  # fmt: skip
    assert llm.attributes[GenAI.PROVIDER_NAME] == "openrouter"

    events = await ask_model(api, conv["id"], "question", "local/other")
    assert next(p for e, p in events if e == "error")["code"] == "model_denied"
    assert "local/other" in events[-1][1]["text"] and len(backends.openrouter) == 2
    await api.aclose()


@pytest.mark.covers("INV-MOD-1", "INV-CLS-2")
async def test_external_model_blocked_on_confidential_context(
    make_app, session, tmp_path, backends
):
    api = await external_chat(session, make_app, tmp_path, secret_class="confidential")
    conv = (await api.post("/chat/conversations", json={})).json()
    events = await ask_model(
        api, conv["id"], "How many vacation days can I carry over?", "openrouter/default"
    )
    done = events[-1][1]
    err = next(p for e, p in events if e == "error")
    assert (
        done["status"] == "denied"
        and err["code"] == "model_denied"
        and "confidential" in err["message"]
    )
    # the first call chose a tool on an empty context; the confidential passage did not go outside
    assert len(backends.openrouter) == 1 and backends.chat == []
    assert "56 days" not in json.dumps(backends.openrouter, ensure_ascii=False)
    denied = await session.scalar(select(AuditEvent).where(
        AuditEvent.execution_id == uuid.UUID(done["execution_id"]),
        AuditEvent.event_type == AuditEventType.POLICY_DENIED, AuditEvent.model == "openai/x"))  # fmt: skip
    assert denied.data_class is DataClass.CONFIDENTIAL
    await api.aclose()


@pytest.mark.covers("INV-CLS-3")
async def test_history_class_blocks_external_model_after_switch(
    make_app, session, tmp_path, backends
):
    """An answer from a Confidential document in the history: switching the model does not take it outside."""
    api = await external_chat(session, make_app, tmp_path, secret_class="confidential")
    conv = (await api.post("/chat/conversations", json={})).json()
    first = await ask(api, conv["id"], "How many vacation days can I carry over?")
    assert first[-1][1]["status"] == "succeeded" and backends.openrouter == []

    events = await ask_model(
        api, conv["id"], "And what about over the limit?", "openrouter/default"
    )
    done = events[-1][1]
    assert (
        done["status"] == "denied"
        and "confidential" in next(p for e, p in events if e == "error")["message"]
    )
    assert backends.openrouter == []  # the external provider was never called
    execution = await session.get(Execution, uuid.UUID(done["execution_id"]))
    assert execution.max_data_class_seen is DataClass.CONFIDENTIAL
    await api.aclose()
