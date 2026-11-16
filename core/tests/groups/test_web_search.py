"""web_search and Research Agent on a live database: fake Ollama and Tavily, class ceiling, key."""

import uuid

import pytest
from sqlalchemy import select

from tests.fixtures.corpus import AGENTS_DIR
from tests.fixtures.world import ask, seed_web
from yorath.control.journal import AuditEvent, AuditEventType
from yorath.control.model import (
    Agent,
    AgentRevision,
    DataClass,
    Execution,
    ExecutionTrigger,
    User,
)
from yorath.enforcement.gateway import load_agent, open_context

pytestmark = pytest.mark.group


@pytest.mark.covers("INV-TOOL-3")
async def test_research_agent_searches_the_web(make_app, session, backends):
    api, app = await seed_web(session, make_app, backends)
    assert (
        await api.put("/setup/tavily", json={"api_key": "tvly-test-key", "consent": False})
    ).status_code == 422
    assert (await api.get("/setup/status")).json()["web_search_configured"] is False
    r = await api.put("/setup/tavily", json={"api_key": "tvly-test-key", "consent": True})
    assert r.status_code == 200 and "query text" in r.json()["leaves_server"]
    assert (await api.get("/setup/status")).json()["web_search_configured"] is True

    agents = (await api.get("/chat/agents")).json()
    assert {a["name"] for a in agents} == {"document-assistant", "research-agent"}
    assert (await api.post("/chat/conversations", json={"agent": "nobody"})).status_code == 404
    conv = (await api.post("/chat/conversations", json={"agent": "research-agent"})).json()
    assert conv["agent_id"] == next(a["id"] for a in agents if a["name"] == "research-agent")
    models = (await api.get("/chat/models", params={"agent": "research-agent"})).json()
    assert [m["name"] for m in models] == ["local/default"]

    events = await ask(api, conv["id"], "How many vacation days are there in the USA?")
    done = events[-1][1]
    assert done["status"] == "succeeded" and done["text"] == "Vacation is 28 days [1]."
    cites = next(p for e, p in events if e == "citations")["citations"]
    assert cites[0]["url"] == "https://www.dol.gov/vacation" and cites[0]["data_class"] == "public"
    assert backends.tavily == [
        {"auth": "Bearer tvly-test-key", "body": {"query": "vacation in the USA", "max_results": 5}}
    ]
    assert "data, not instructions" in backends.chat[-1]["messages"][-1]["content"]

    execution = await session.get(Execution, uuid.UUID(done["execution_id"]))
    revision = await session.get(AgentRevision, execution.agent_revision_id)
    agent = await session.get(Agent, revision.agent_id)
    assert agent.name == "research-agent" and execution.max_data_class_seen is DataClass.PUBLIC
    egress = await session.scalar(select(AuditEvent).where(
        AuditEvent.execution_id == execution.id, AuditEvent.event_type == AuditEventType.EGRESS_ALLOWED))  # fmt: skip
    assert egress.details == {"domain": "tavily"} and egress.tool == "web_search"
    await api.aclose()


@pytest.mark.covers("INV-TOOL-4")
async def test_without_key_nothing_leaves_the_server(make_app, session, backends):
    api, app = await seed_web(session, make_app, backends)
    conv = (await api.post("/chat/conversations", json={"agent": "research-agent"})).json()
    events = await ask(api, conv["id"], "What's new?")
    finished = next(p for e, p in events if e == "tool" and p["status"] != "started")
    assert finished["status"] == "error" and "not set up" in finished["message"]
    assert backends.tavily == [] and events[-1][1]["status"] == "succeeded"
    await api.aclose()


@pytest.mark.covers("INV-TOOL-2")
async def test_confidential_context_blocks_web_search(make_app, session, backends):
    api, app = await seed_web(session, make_app, backends)
    await app.state.secrets.put("tavily_api_key", "tvly-test-key")
    owner = await session.scalar(select(User).where(User.role == "owner"))
    agent, rev, card, body = await load_agent(session, "research-agent", AGENTS_DIR)
    ctx = await open_context(
        session, app.state.journal, agent=agent, revision=rev, card=card, body=body,
        actor=owner, effective_principal=owner, trigger=ExecutionTrigger.USER_MESSAGE,
    )  # fmt: skip
    app.state.contexts.add(ctx)
    gateway = app.state.tool_gateway

    ctx.raise_class(DataClass.INTERNAL)
    allowed = await gateway.call_tool(ctx, session, "web_search", {"query": "x"})
    assert not allowed.is_error and len(backends.tavily) == 1

    ctx.raise_class(DataClass.CONFIDENTIAL)
    denied = await gateway.call_tool(ctx, session, "web_search", {"query": "secret"})
    assert denied.is_error and denied.meta["denied"] and len(backends.tavily) == 1
    event = await session.scalar(select(AuditEvent).where(
        AuditEvent.execution_id == ctx.execution_id, AuditEvent.event_type == AuditEventType.POLICY_DENIED))  # fmt: skip
    assert "tool-class-ceiling" in event.reason and event.tool == "web_search"
    assert event.data_class is DataClass.CONFIDENTIAL
    await api.aclose()
