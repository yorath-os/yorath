"""My Agents on a live database: permissions from the revision, Execution and cost summary, latest Executions."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from tests.fixtures.corpus import AGENTS_DIR, OWNER
from tests.fixtures.world import ask, event, open_for_owner, seed_chat
from yorath.control.agents.loader import sync_agents_dir
from yorath.control.journal import AuditEvent, AuditEventType
from yorath.control.model import DataClass, Execution

pytestmark = pytest.mark.group


async def test_agents_list_shows_card_and_execution_summary(make_app, session, tmp_path):
    api, app = await seed_chat(session, make_app, tmp_path)
    before = (await api.get("/agents")).json()
    da = next(a for a in before if a["name"] == "document-assistant")
    assert da["mode"] == "delegated" and da["runtime"] == "native" and da["control_level"] == "tool"
    assert da["hosting"] == "managed" and da["enabled"] is True and da["service_account"] is None
    card = da["revision"]["card"]
    assert da["revision"]["revision_no"] == 1 and da["revision"]["path"] == "document-assistant.md"
    assert {t["name"] for t in card["tools"]} == {"search_docs", "query_records", "ask_agent"}
    assert next(t for t in card["tools"] if t["name"] == "ask_agent")["targets"] == [
        "research-agent"
    ]
    assert (
        card["data"] == ["team-docs", "hr-vacations"] and card["max_data_class"] == "confidential"
    )
    assert card["model"] == "local/default" and card["triggers"] == ["chat"]
    assert da["executions"] == {"total": 0, "by_status": {}, "last_at": None}
    assert da["cost_usd"] == "0"

    conv = (await api.post("/chat/conversations", json={})).json()
    done = (await ask(api, conv["id"], "how many days can be carried over?"))[-1][1]

    da = next(a for a in (await api.get("/agents")).json() if a["name"] == "document-assistant")
    assert da["executions"]["total"] == 1 and da["executions"]["by_status"] == {"succeeded": 1}
    assert da["executions"]["last_at"] is not None

    runs = (await api.get("/agents/document-assistant/executions")).json()
    assert len(runs) == 1 and runs[0]["id"] == done["execution_id"]
    run = runs[0]
    assert run["status"] == "succeeded" and run["trigger"] == "user_message"
    assert run["initiated_by"]["display_name"] == OWNER["display_name"]
    assert run["effective_principal"]["id"] == run["initiated_by"]["id"]
    assert run["model"] == "fake-chat" and run["usage"]["input_tokens"] == 642
    assert run["cost_estimated"] is True and run["max_data_class_seen"] == "internal"
    assert run["outcome"]["kind"] == "answer" and run["outcome"]["status"] == "pending"
    assert run["revision_no"] == 1 and run["finished_at"] is not None
    assert (await api.get("/agents/nobody/executions")).status_code == 404
    await api.aclose()


async def test_denied_execution_listed_without_outcome(make_app, session, tmp_path):
    api, app = await seed_chat(session, make_app, tmp_path, external_classes=[DataClass.PUBLIC])
    conv = (await api.post("/chat/conversations", json={})).json()
    await ask(api, conv["id"], "how many days can be carried over?")
    runs = (await api.get("/agents/document-assistant/executions?limit=5")).json()
    assert runs[0]["status"] == "denied" and runs[0]["outcome"] is None
    da = next(a for a in (await api.get("/agents")).json() if a["name"] == "document-assistant")
    assert da["executions"]["by_status"] == {"denied": 1}
    await api.aclose()


@pytest.mark.covers("INV-ID-2")
async def test_member_gets_403(make_app, session, tmp_path):
    api, app = await seed_chat(session, make_app, tmp_path)
    await api.post(
        "/users", json={"login": "anna", "password": "anna-pass-1", "display_name": "Anna"}
    )
    await api.post("/auth/logout")
    await api.post("/auth/login", json={"login": "anna", "password": "anna-pass-1"})
    assert (await api.get("/agents")).status_code == 403
    assert (await api.get("/agents/document-assistant/executions")).status_code == 403
    await api.aclose()


async def test_anonymous_me_is_200(make_app):
    api = AsyncClient(transport=ASGITransport(app=make_app()), base_url="http://test")
    r = await api.get("/auth/me")
    assert r.status_code == 200 and r.json() == {"authenticated": False, "setup_required": True}
    await api.post("/auth/setup", json=OWNER)
    me = (await api.get("/auth/me")).json()
    assert me["authenticated"] is True and me["login"] == OWNER["login"]
    await api.post("/auth/logout")
    r = await api.get("/auth/me")
    assert r.status_code == 200 and r.json() == {"authenticated": False, "setup_required": False}
    assert (await api.get("/users")).status_code == 401
    await api.aclose()


async def member_api(api, app):
    login = {"login": "member1", "password": "password-1"}
    assert (await api.post("/users", json={**login, "display_name": "Member"})).status_code == 201
    member = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    assert (await member.post("/auth/login", json=login)).status_code == 200
    return member


async def test_owner_toggles_agent_and_it_is_audited(make_app, session, tmp_path):
    api, app = await seed_chat(session, make_app, tmp_path)
    r = await api.patch("/agents/research-agent", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert "research-agent" not in [a["name"] for a in (await api.get("/chat/agents")).json()]
    events = (await session.scalars(select(AuditEvent).where(
        AuditEvent.event_type == AuditEventType.AGENT_UPDATED))).all()  # fmt: skip
    assert [e.details for e in events] == [{"agent": "research-agent", "enabled": False}]

    await sync_agents_dir(session, AGENTS_DIR)
    await session.commit()
    listed = {a["name"]: a for a in (await api.get("/agents")).json()}
    assert (
        listed["research-agent"]["enabled"] is False
    )  # the file does not decide whether the agent is enabled

    assert (await api.patch("/agents/nobody", json={"enabled": False})).status_code == 404
    member = await member_api(api, app)
    assert (await member.patch("/agents/research-agent", json={"enabled": True})).status_code == 403
    await member.aclose()
    await api.aclose()


@pytest.mark.covers("INV-AGT-4")
async def test_disabled_agent_does_not_run_in_open_conversation(make_app, session, tmp_path):
    api, _ = await seed_chat(session, make_app, tmp_path)
    conversation = (await api.post("/chat/conversations", json={})).json()["id"]
    await ask(
        api, conversation, "how many days can be carried over?"
    )  # anchor: the conversation works
    before = await session.scalar(select(func.count(Execution.id)))

    await api.patch("/agents/document-assistant", json={"enabled": False})
    events = await ask(api, conversation, "and one more question")
    assert event(events, "error")["code"] == "agent_disabled"
    assert await session.scalar(select(func.count(Execution.id))) == before
    await api.aclose()


@pytest.mark.covers("INV-AGT-4")
async def test_disabled_agent_is_refused_on_agent_route(make_app, session, tmp_path):
    api, app = await seed_chat(session, make_app, tmp_path)
    ctx = await open_for_owner(app, session)
    await api.patch("/agents/research-agent", json={"enabled": False})
    before = await session.scalar(select(func.count(Execution.id)))
    result = await app.state.tool_gateway.call_tool(
        ctx, session, "ask_agent", {"agent": "research-agent", "question": "a question"}
    )
    assert result.is_error and "disabled" in result.content
    assert await session.scalar(select(func.count(Execution.id))) == before
    await api.aclose()


async def test_model_api_is_external_agent_outside_chat(make_app, session, tmp_path):
    api, _ = await seed_chat(session, make_app, tmp_path)
    agents = {a["name"]: a for a in (await api.get("/agents")).json()}
    model_api = agents["model-api"]
    assert model_api["hosting"] == "external" and model_api["control_level"] == "gateway"
    assert model_api["builtin"] is True
    assert model_api["revision"]["card"]["triggers"] == ["api"]
    assert agents["document-assistant"]["control_level"] == "tool"  # anchor
    chat = {a["name"] for a in (await api.get("/chat/agents")).json()}
    assert "document-assistant" in chat and "model-api" not in chat
