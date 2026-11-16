"""Model Gateway HTTP endpoint: Execution token, OpenAI stream, denials, revoke on finish."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.fixtures.corpus import REPLY
from tests.fixtures.world import open_for_owner, seed_chat
from yorath.control.journal import AuditEvent, AuditEventType, ControlLevel
from yorath.control.model import DataClass, ExecutionStatus, ExecutionToken
from yorath.db import utcnow
from yorath.enforcement.gateway import finish
from yorath.enforcement.gateway.tokens import issue_execution_token

pytestmark = pytest.mark.group

BODY = {"model": "local/default", "messages": [{"role": "user", "content": "how many days?"}]}


async def issue(app, session, ctx) -> str:
    return await issue_execution_token(session, app.state.journal, ctx.audit_fields(), 120)


def parse_sse(text: str) -> list[dict]:
    out = []
    for block in text.split("\n\n"):
        if block.startswith("data: ") and block[6:] != "[DONE]":
            out.append(json.loads(block[6:]))
    return out


@pytest.mark.covers("INV-GW-1")
async def test_token_required_and_validated(make_app, session, tmp_path):
    api, app = await seed_chat(session, make_app, tmp_path)
    api.cookies.clear()  # a user session cookie is not authentication for the Gateway
    assert (await api.post("/gateway/v1/chat/completions", json=BODY)).status_code == 401
    r = await api.post(
        "/gateway/v1/chat/completions", json=BODY, headers={"Authorization": "Bearer nope"}
    )
    assert r.status_code == 401 and r.json()["detail"] == "execution_token_invalid"
    assert (
        await api.get("/gateway/v1/models", headers={"Authorization": "Basic x"})
    ).status_code == 401

    ctx = await open_for_owner(app, session)
    expired = await issue(app, session, ctx)
    row = await session.scalar(
        select(ExecutionToken).where(ExecutionToken.execution_id == ctx.execution_id)
    )
    row.expires_at = utcnow() - timedelta(minutes=1)
    await session.commit()
    r = await api.get("/gateway/v1/models", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401
    await api.aclose()


@pytest.mark.covers("INV-GW-1", "INV-GW-2")
async def test_stream_and_models_with_sandbox_control_level(make_app, session, tmp_path, backends):
    api, app = await seed_chat(session, make_app, tmp_path)
    api.cookies.clear()
    ctx = await open_for_owner(app, session)
    ctx.control_level = ControlLevel.SANDBOX
    token = await issue(app, session, ctx)
    issued = await session.scalar(select(AuditEvent).where(
        AuditEvent.execution_id == ctx.execution_id, AuditEvent.event_type == AuditEventType.SECRET_ISSUED
    ))  # fmt: skip
    assert issued.details["kind"] == "execution_token"
    headers = {"Authorization": f"Bearer {token}"}

    models = (await api.get("/gateway/v1/models", headers=headers)).json()
    assert models["object"] == "list" and {m["id"] for m in models["data"]} >= {
        "local/default",
        "local/embeddings",
    }
    assert (
        next(m for m in models["data"] if m["id"] == "local/default")["allowed_for_execution"]
        is True
    )

    async with api.stream(
        "POST", "/gateway/v1/chat/completions", json={**BODY, "stream": True}, headers=headers
    ) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        text = (await r.aread()).decode()
    assert text.rstrip().endswith("data: [DONE]")
    events = parse_sse(text)
    content = "".join(e["choices"][0]["delta"].get("content", "") for e in events if e["choices"])
    assert content == "".join(REPLY) and events[0]["object"] == "chat.completion.chunk"
    last = events[-1]
    assert last["choices"][0]["finish_reason"] == "stop" and last["usage"]["prompt_tokens"] == 321

    r = await api.post("/gateway/v1/chat/completions", json=BODY, headers=headers)
    body = r.json()
    assert r.status_code == 200 and body["object"] == "chat.completion"
    assert (
        body["choices"][0]["message"]["content"] == "".join(REPLY)
        and body["usage"]["completion_tokens"] == 12
    )
    assert backends.chat[-1]["messages"][-1]["content"] == "how many days?"

    called = (await session.scalars(select(AuditEvent).where(
        AuditEvent.execution_id == ctx.execution_id, AuditEvent.event_type == AuditEventType.LLM_CALLED
    ))).all()  # fmt: skip
    assert len(called) == 2 and all(a.control_level is ControlLevel.SANDBOX for a in called)
    assert ctx.usage["calls"] == 2

    r = await api.post(
        "/gateway/v1/chat/completions",
        json={**BODY, "model": "local/other"},
        headers=headers,
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "model_not_allowed"

    await finish(ctx, session, app.state.journal, ExecutionStatus.SUCCEEDED)
    assert (await api.get("/gateway/v1/models", headers=headers)).status_code == 401
    revoked = await session.scalar(select(AuditEvent).where(
        AuditEvent.execution_id == ctx.execution_id, AuditEvent.event_type == AuditEventType.SECRET_REVOKED
    ))  # fmt: skip
    assert revoked is not None and revoked.details["count"] == 1
    await api.aclose()


async def test_tool_calls_in_openai_form(make_app, session, tmp_path, backends):
    api, app = await seed_chat(session, make_app, tmp_path)
    api.cookies.clear()
    ctx = await open_for_owner(app, session)
    token = await issue(app, session, ctx)
    body = {
        **BODY,
        "messages": [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "search_docs", "arguments": '{"query": "x"}'},
                    }
                ],
            },  # fmt: skip
            {
                "role": "tool",
                "tool_call_id": "c1",
                "name": "search_docs",
                "content": "[1] passage",
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {"name": "search_docs", "parameters": {"type": "object"}},
            }
        ],
    }
    r = await api.post(
        "/gateway/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    sent = backends.chat[-1]
    assert sent["tools"] == body["tools"]
    assert sent["messages"][1]["tool_calls"][0]["function"] == {
        "name": "search_docs",
        "arguments": {"query": "x"},
    }
    assert sent["messages"][2] == {
        "role": "tool",
        "content": "[1] passage",
        "tool_name": "search_docs",
    }
    await api.aclose()


RESPONSES = {
    "model": "local/default",
    "instructions": "You are a developer.",
    "input": [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "how many?"}],
        }
    ],
}
SHELL_TOOL = {
    "type": "function",
    "name": "shell",
    "description": "Team",
    "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
}


def parse_responses(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


@pytest.mark.covers("INV-GW-1")
async def test_responses_requires_execution_token(make_app, session, tmp_path):
    api, _ = await seed_chat(session, make_app, tmp_path)
    api.cookies.clear()
    assert (await api.post("/gateway/v1/responses", json=RESPONSES)).status_code == 401
    await api.aclose()


async def test_responses_stream_text_and_usage(make_app, session, tmp_path, backends):
    api, app = await seed_chat(session, make_app, tmp_path)
    ctx = await open_for_owner(app, session)
    auth = {"Authorization": f"Bearer {await issue(app, session, ctx)}"}
    r = await api.post("/gateway/v1/responses", json={**RESPONSES, "stream": True}, headers=auth)
    assert r.status_code == 200
    events = parse_responses(r.text)
    assert events[0]["type"] == "response.created" and events[-1]["type"] == "response.completed"
    text = "".join(e["delta"] for e in events if e["type"] == "response.output_text.delta")
    assert text == "".join(REPLY)
    done = events[-1]["response"]
    assert done["usage"]["input_tokens"] > 0
    assert done["output"][0]["content"][0]["text"] == "".join(REPLY)
    sent = backends.chat[-1]["messages"]
    assert sent[0] == {"role": "system", "content": "You are a developer."}
    await api.aclose()


async def test_responses_function_call_round_trip(make_app, session, tmp_path, backends):
    api, app = await seed_chat(session, make_app, tmp_path)
    ctx = await open_for_owner(app, session)
    auth = {"Authorization": f"Bearer {await issue(app, session, ctx)}"}
    backends.tool_call = ("shell", {"command": "ls"})
    first = {**RESPONSES, "tools": [SHELL_TOOL], "stream": True}
    r = await api.post("/gateway/v1/responses", json=first, headers=auth)
    items = [e["item"] for e in parse_responses(r.text) if e["type"] == "response.output_item.done"]
    call = next(i for i in items if i["type"] == "function_call")
    assert call["name"] == "shell" and json.loads(call["arguments"]) == {"command": "ls"}

    history = [*RESPONSES["input"], call, {
        "type": "function_call_output", "call_id": call["call_id"], "output": "a.txt",
    }]  # fmt: skip
    body = {**RESPONSES, "input": history, "tools": [SHELL_TOOL]}
    r = await api.post("/gateway/v1/responses", json=body, headers=auth)
    assert r.status_code == 200 and r.json()["output"][0]["type"] == "message"
    sent = backends.chat[-1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "tool"]
    assert sent[-1]["content"] == "a.txt"
    await api.aclose()


@pytest.mark.covers("INV-GW-2")
async def test_responses_blocked_by_class_like_chat(make_app, session, tmp_path, backends):
    api, app = await seed_chat(session, make_app, tmp_path, external_classes=[DataClass.PUBLIC])
    ctx = await open_for_owner(app, session)
    auth = {"Authorization": f"Bearer {await issue(app, session, ctx)}"}
    ctx.max_data_class_seen = DataClass.CONFIDENTIAL
    calls = len(backends.chat)
    r = await api.post("/gateway/v1/responses", json=RESPONSES, headers=auth)
    assert r.status_code == 403 and r.json()["error"]["code"] == "model_denied"
    assert len(backends.chat) == calls
    await api.aclose()
