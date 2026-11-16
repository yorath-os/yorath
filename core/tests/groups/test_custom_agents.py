"""A custom agent from the panel: a file on the volume, a new revision, card limits on the real PDP."""

import shutil

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from tests.fixtures.corpus import AGENTS_DIR
from tests.fixtures.world import ask, event, seed_chat
from yorath.control.agents.loader import sync_agents_dir, sync_custom_agents
from yorath.control.journal import AuditEvent, AuditEventType
from yorath.control.model import Agent, AgentRevision

pytestmark = pytest.mark.group

FORM = {
    "name": "faq-helper",
    "description": "Answers from the FAQ",
    "instructions": "You answer from the company FAQ. Secret instruction 777.",
    "data": ["team-docs"],
    "tools": ["search_docs"],
}


@pytest.fixture
async def world(make_app, session, tmp_path):
    agents = tmp_path / "agents"
    shutil.copytree(AGENTS_DIR, agents)
    api, app = await seed_chat(session, make_app, tmp_path, agents_dir=agents)
    yield api, app, agents
    await api.aclose()


async def member_api(api, app):
    login = {"login": "member1", "password": "password-1"}
    body = {**login, "display_name": "Member"}
    assert (await api.post("/users", json=body)).status_code == 201
    member = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    assert (await member.post("/auth/login", json=login)).status_code == 200
    return member


async def test_owner_reads_example_text(world):
    api, _, agents = world
    r = await api.get("/agents/document-assistant/source")
    assert r.status_code == 200
    body = r.json()
    assert body["builtin"] is True and body["path"] == "document-assistant.md"
    assert body["text"] == (agents / "document-assistant.md").read_text(encoding="utf-8")
    assert (
        body["instructions"].startswith("You are an assistant")
        and "---" not in body["instructions"][:5]
    )
    assert (await api.get("/agents/nobody/source")).status_code == 404


@pytest.mark.covers("INV-AGT-7")
async def test_member_cannot_read_create_or_change(world):
    api, app, agents = world
    member = await member_api(api, app)
    assert (await member.get("/agents/document-assistant/source")).status_code == 403
    assert (await member.get("/agents/options")).status_code == 403
    assert (await member.post("/agents", json=FORM)).status_code == 403
    assert (await api.post("/agents", json=FORM)).status_code == 201
    form = {k: v for k, v in FORM.items() if k != "name"}
    assert (await member.put("/agents/faq-helper", json=form)).status_code == 403
    await member.aclose()


@pytest.mark.covers("INV-AGT-7")
async def test_created_agent_answers_without_rebuild(world, session, backends):
    api, _, agents = world
    r = await api.post("/agents", json=FORM)
    assert r.status_code == 201
    card = r.json()["revision"]["card"]
    assert (
        r.json()["revision"]["path"] == "custom/faq-helper.md" and r.json()["runtime"] == "native"
    )
    assert card["max_data_class"] == "internal" and card["models"] == ["local/default"]
    assert (agents / "custom" / "faq-helper.md").is_file()
    builtin = {a["name"]: a["builtin"] for a in (await api.get("/agents")).json()}
    assert builtin["faq-helper"] is False and builtin["document-assistant"] is True

    listed = {a["name"] for a in (await api.get("/chat/agents")).json()}
    assert "faq-helper" in listed
    conv = (await api.post("/chat/conversations", json={"agent": "faq-helper"})).json()
    backends.tool_call = ("search_docs", {"query": "how many days can be carried over"})
    events = await ask(api, conv["id"], "how many days can be carried over?")
    assert event(events, "tool")["name"] == "search_docs"
    assert "Secret instruction 777" in str(backends.chat[0]["messages"])

    row = await session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == AuditEventType.AGENT_CREATED)
    )
    assert row.details["agent"] == "faq-helper" and row.details["revision"] == 1
    assert "777" not in str(row.details) and row.actor_principal_id is not None


async def test_card_limits_agent_data(world, backends):
    api, _, _ = world
    await api.post("/agents", json=FORM)
    conv = (await api.post("/chat/conversations", json={"agent": "faq-helper"})).json()
    backends.tool_call = ("search_docs", {"query": "Miller salary"})
    await ask(api, conv["id"], "what is the salary?")
    assert "salary is 350000" not in str(backends.chat).lower()  # hr-salaries is not in the card

    wider = {**FORM, "name": "salary-helper", "data": ["hr-salaries"]}
    await api.post("/agents", json=wider)
    conv = (await api.post("/chat/conversations", json={"agent": "salary-helper"})).json()
    backends.tool_call = ("search_docs", {"query": "Miller salary"})
    await ask(api, conv["id"], "what is the salary?")
    assert (
        "salary is 350000" in str(backends.chat).lower()
    )  # anchor: the source from the card is available


@pytest.mark.covers("INV-AGT-5")
@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"tools": ["ask_agent"]}, "tool_not_allowed"),
        ({"tools": ["shell"]}, "tool_not_allowed"),
        ({"data": ["no-such-asset"]}, "asset_not_found"),
        ({"data": []}, "source_required"),  # meta agent 7.11: search without a source
        ({"tools": ["query_records"]}, "source_required"),  # team-docs holds documents, not tables
        ({"models": ["nope/model"]}, "model_not_found"),
        ({"models": ["local/embeddings"]}, "model_not_found"),
    ],
)
async def test_refused_form_leaves_nothing(world, session, change, code):
    api, _, agents = world
    r = await api.post("/agents", json={**FORM, **change})
    assert r.status_code == 422 and r.json()["detail"]["code"] == code
    assert not (agents / "custom" / "faq-helper.md").exists()
    assert await session.scalar(select(Agent).where(Agent.name == "faq-helper")) is None


async def test_blank_instruction_and_bad_name_are_refused(world):
    api, _, _ = world
    assert (await api.post("/agents", json={**FORM, "instructions": "   "})).status_code == 422
    assert (await api.post("/agents", json={**FORM, "name": "../evil"})).status_code == 422


@pytest.mark.covers("INV-AGT-6")
async def test_builtin_agent_is_protected(world, session):
    api, _, agents = world
    before = (agents / "research-agent.md").read_text(encoding="utf-8")
    r = await api.post("/agents", json={**FORM, "name": "research-agent"})
    assert r.status_code == 409
    form = {k: v for k, v in FORM.items() if k != "name"}
    r = await api.put("/agents/document-assistant", json=form)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "builtin_agent"
    assert (agents / "research-agent.md").read_text(encoding="utf-8") == before
    assert not (agents / "custom").exists()


@pytest.mark.covers("INV-AGT-7")
async def test_edit_creates_revision_and_keeps_old_execution(world, session, backends):
    api, _, agents = world
    await api.post("/agents", json=FORM)
    form = {k: v for k, v in FORM.items() if k != "name"}
    r = await api.put("/agents/faq-helper", json={**form, "instructions": "New instruction 888."})
    assert r.status_code == 200 and r.json()["revision"]["revision_no"] == 2
    conv = (await api.post("/chat/conversations", json={"agent": "faq-helper"})).json()
    await ask(api, conv["id"], "question")
    assert "New instruction 888." in str(backends.chat[-1]["messages"])
    row = await session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == AuditEventType.AGENT_UPDATED)
        .order_by(AuditEvent.occurred_at.desc())
    )
    assert row.details["revision"] == 2 and "888" not in str(row.details)
    source = (await api.get("/agents/faq-helper/source")).json()
    assert source["builtin"] is False and source["instructions"] == "New instruction 888."


async def test_taken_name_gives_conflict_after_create(world):
    api, _, _ = world
    assert (await api.post("/agents", json=FORM)).status_code == 201
    assert (await api.post("/agents", json=FORM)).status_code == 409


async def test_options_offer_only_allowed_choices(world):
    api, _, _ = world
    body = (await api.get("/agents/options")).json()
    assert {t["name"] for t in body["tools"]} == {"search_docs", "query_records", "web_search"}
    assert "team-docs" in {a["name"] for a in body["assets"]}
    assert [m["name"] for m in body["models"]] == ["local/default"]


@pytest.mark.covers("INV-AGT-8", "INV-AGT-6")
async def test_bad_custom_file_warns_and_others_sync(world, session):
    _, _, agents = world
    custom = agents / "custom"
    custom.mkdir()
    (custom / "broken.md").write_text("---\nname: [\n---\ntext\n", encoding="utf-8")
    (custom / "thief.md").write_text(
        "---\nname: research-agent\nmode: delegated\n---\nstolen\n", encoding="utf-8"
    )
    (custom / "good.md").write_text(
        "---\nname: good-agent\nmode: delegated\n---\ntext\n", encoding="utf-8"
    )
    results, warnings = await sync_custom_agents(session, agents)
    assert [r.name for r in results] == ["good-agent"]
    assert len(warnings) == 2 and "broken.md" in warnings[0] and "research-agent" in warnings[1]
    research = await session.scalar(select(Agent).where(Agent.name == "research-agent"))
    revs = await session.scalar(
        select(func.count()).select_from(AgentRevision).where(AgentRevision.agent_id == research.id)
    )
    assert revs == 1
    assert (await sync_agents_dir(session, agents)) and (agents / "custom" / "good.md").is_file()


async def test_agent_survives_restart_on_same_volume(world, session):
    api, _, agents = world
    await api.post("/agents", json=FORM)
    results, warnings = await sync_custom_agents(session, agents)
    assert warnings == [] and [(r.revision_no, r.created) for r in results] == [(1, False)]
