"""Row rules on real Cedar (criterion 4): who sees what and what reaches the model."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.fixtures.corpus import AGENTS_DIR, OWNER
from tests.fixtures.world import ask, event, register_model
from yorath.control.agents.loader import sync_agents_dir
from yorath.control.journal import AuditEvent, AuditEventType
from yorath.control.model import DataClass, DataRecord, ExecutionTrigger, ModelProvider, User
from yorath.enforcement.gateway import GatewayDenied, load_agent, open_context
from yorath.runtime.llm import ChatMessage, ChatRequest, collect

pytestmark = pytest.mark.group

CSV = """employee_id,full_name,department,manager_id,vacation_days_left,last_vacation_start
E001,Peter Miller,Engineering,E010,12,2026-07-06
E015,Ryan Miller,Field Sales,E014,9,2026-08-11
E002,Anna Parker,Engineering,E010,21,2026-05-12
E011,Natalie Millerton,Field Sales,E014,13,2026-07-20
"""
PEOPLE = {
    "miller": {"attributes": {"employee_id": "E001"}, "department": "Engineering"},
    "parker": {"attributes": {"employee_id": "E002"}, "department": "Engineering"},
    "manager": {"attributes": {"employee_id": "E010"}, "department": "Engineering"},
    "hrbp": {"attributes": {"assigned_departments": ["Field Sales"]}, "role": "hrbp"},
    "hradmin": {"role": "hr_admin"},
}


@pytest.fixture
async def hr(make_app, session):
    """Owner, the hr-vacations source with a table, users with attributes."""
    app = make_app(agents_dir=AGENTS_DIR)
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await api.post("/auth/setup", json=OWNER)
    await sync_agents_dir(session, AGENTS_DIR)
    r = await api.post(
        "/data/assets",
        json={"name": "hr-vacations", "data_class": "confidential", "kind": "records"},
    )
    assert r.status_code == 201
    asset = r.json()
    r = await api.post(
        f"/data/assets/{asset['id']}/records", files={"file": ("hr.csv", CSV.encode(), "text/csv")}
    )
    assert r.status_code == 201 and r.json()["rows"] == 4
    for login, extra in PEOPLE.items():
        body = {"login": login, "password": "password-1", "display_name": login, **extra}
        assert (await api.post("/users", json=body)).status_code == 201
    yield app, api, asset
    await api.aclose()


async def as_user(app, session, login: str, agent_name: str = "document-assistant"):
    user = await session.scalar(select(User).where(User.login == login))
    agent, rev, card, body = await load_agent(session, agent_name, AGENTS_DIR)
    ctx = await open_context(
        session, app.state.journal, agent=agent, revision=rev, card=card, body=body,
        actor=user, effective_principal=user, trigger=ExecutionTrigger.USER_MESSAGE,
    )  # fmt: skip
    app.state.contexts.add(ctx)
    return ctx


async def query(app, session, ctx, **args):
    return await app.state.tool_gateway.call_tool(ctx, session, "query_records", args)


@pytest.mark.covers("INV-REC-1", "INV-REC-4")
@pytest.mark.parametrize(
    ("login", "expected", "absent"),
    [
        ("miller", ["Peter Miller"], ["Ryan Miller"]),
        ("manager", ["Peter Miller"], ["Ryan Miller"]),
        ("hrbp", ["Ryan Miller"], ["Peter Miller"]),
        ("hradmin", ["Peter Miller", "Ryan Miller"], []),
    ],
)
async def test_each_user_sees_only_allowed_namesakes(hr, session, login, expected, absent):
    app, _, _ = hr
    ctx = await as_user(app, session, login)
    result = await query(app, session, ctx, name="Miller's")
    assert not result.is_error
    for text in expected:
        assert text in result.content
    for text in absent:
        assert text not in result.content
    assert result.meta["hits"] == len(expected)


@pytest.mark.covers("INV-REC-4")
async def test_allowed_row_is_returned_whole(hr, session):
    app, _, _ = hr
    ctx = await as_user(app, session, "manager")
    result = await query(app, session, ctx, name="Miller")
    assert "vacation_days_left: 12" in result.content and "2026-07-06" in result.content
    assert result.citations[0].heading_path == "Peter Miller"


@pytest.mark.covers("INV-REC-2")
async def test_count_shows_only_allowed_rows(hr, session):
    app, _, _ = hr
    admin = await query(app, session, await as_user(app, session, "hradmin"), name="Miller")
    assert admin.content.startswith("People found: 2\n")
    own = await query(app, session, await as_user(app, session, "miller"), name="Miller")
    assert own.content.startswith("People found: 1\n")  # the namesake is not counted


@pytest.mark.covers("INV-REC-2")
async def test_denied_row_is_indistinguishable_from_missing(hr, session):
    app, _, _ = hr
    ctx = await as_user(app, session, "miller")
    own = await query(app, session, ctx, name="Miller")
    assert own.meta["hits"] == 1  # anchor: the query runs and finds rows
    denied = await query(app, session, ctx, name="Parker")
    missing = await query(app, session, ctx, name="Nobody")
    assert denied.content == missing.content == "No records match the query."
    assert denied.meta == missing.meta and denied.data_class is None

    events = (await session.scalars(select(AuditEvent).where(
        AuditEvent.execution_id == ctx.execution_id, AuditEvent.event_type == AuditEventType.POLICY_DENIED))).all()  # fmt: skip
    assert sorted(e.details["rows_denied"] for e in events) == [1, 1]  # the namesake and Parker
    assert all("Parker" not in str(e.details) for e in events)


@pytest.mark.covers("INV-REC-1")
async def test_name_inside_another_word_does_not_match(hr, session):
    app, _, _ = hr
    ctx = await as_user(app, session, "hradmin")
    result = await query(app, session, ctx, name="Miller")
    assert result.meta["hits"] == 2 and "Millerton" not in result.content


@pytest.mark.covers("INV-REC-3")
async def test_reading_row_raises_class_and_blocks_external_tools(hr, session):
    app, _, _ = hr
    ctx = await as_user(app, session, "miller")
    await query(app, session, ctx, name="Miller")
    assert ctx.max_data_class_seen is DataClass.CONFIDENTIAL
    refused = await app.state.tool_gateway.call_tool(
        ctx,
        session,
        "ask_agent",
        {"agent": "research-agent", "question": "compare with the market"},
    )
    assert refused.is_error and "confidential" in refused.content


@pytest.mark.covers("INV-REC-5")
async def test_agent_without_asset_gets_nothing(hr, session):
    app, _, _ = hr
    ctx = await as_user(app, session, "hradmin")
    assert (await query(app, session, ctx, name="Miller")).meta["hits"] == 2  # anchor
    ctx.card = ctx.card.model_copy(update={"data": ["team-docs"]})
    result = await query(app, session, ctx, name="Miller")
    assert result.meta["hits"] == 0 and "Miller" not in result.content


async def test_csv_upload_validates_and_replaces(hr, session):
    app, api, asset = hr
    url = f"/data/assets/{asset['id']}/records"
    bad = await api.post(
        url, files={"file": ("x.csv", b"employee_id,full_name\nE1,A\n", "text/csv")}
    )
    assert bad.status_code == 422 and "department" in str(bad.json())
    rows = (await session.scalars(select(DataRecord))).all()
    assert len(rows) == 4 and {r.data_class for r in rows} == {DataClass.CONFIDENTIAL}

    small = "employee_id,full_name,department,manager_id\nE9,Ian Newman,HR,\n"
    r = await api.post(url, files={"file": ("x.csv", small.encode(), "text/csv")})
    assert r.status_code == 201 and r.json()["rows"] == 1
    docs = next(a for a in (await api.get("/data/assets")).json() if a["name"] == "team-docs")
    wrong = await api.post(
        f"/data/assets/{docs['id']}/records", files={"file": ("x.csv", small.encode(), "text/csv")}
    )
    assert wrong.status_code == 422
    doc = await api.post(
        f"/data/assets/{asset['id']}/documents", files={"file": ("a.md", b"# A", "text/markdown")}
    )
    assert doc.status_code == 422 and doc.json()["detail"]["code"] == "wrong_kind"


async def member_chat(app, login: str):
    """A real login and a conversation with Document Assistant."""
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await api.post("/auth/login", json={"login": login, "password": "password-1"})
    assert r.status_code == 200
    r = await api.post("/chat/conversations", json={})
    assert r.status_code == 201
    return api, r.json()["id"]


def sent_to_model(backends) -> str:
    return " ".join(str(m.get("content")) for b in backends.chat for m in b["messages"])


@pytest.mark.covers("INV-REC-1")
async def test_denied_row_never_reaches_model_through_chat(hr, session, backends):
    app, _, _ = hr
    await register_model(
        session,
        "local/default",
        ModelProvider.OLLAMA,
        "fake-chat",
        list(DataClass),
        is_default=True,
    )
    backends.tool_call = ("query_records", {"name": "Miller"})
    api, conversation = await member_chat(app, "miller")
    events = await ask(api, conversation, "How many vacation days does Miller have left?")
    await api.aclose()

    assert event(events, "tool")["name"] == "query_records"
    prompt = sent_to_model(backends)
    assert "Peter Miller" in prompt and "vacation_days_left: 12" in prompt  # anchor
    assert "Ryan Miller" not in prompt and "2026-08-11" not in prompt


@pytest.mark.covers("INV-REC-3")
async def test_external_model_is_blocked_after_reading_row(hr, session, backends):
    app, _, _ = hr
    await register_model(
        session,
        "local/default",
        ModelProvider.OLLAMA,
        "fake-chat",
        [DataClass.PUBLIC],
        is_default=True,
    )
    ctx = await as_user(app, session, "miller")
    request = ChatRequest(messages=[ChatMessage(role="user", content="question")])
    await collect(
        app.state.model_gateway.chat(ctx, session, request)
    )  # anchor: the model is available
    await query(app, session, ctx, name="Miller")
    calls = len(backends.chat)
    with pytest.raises(GatewayDenied) as denied:
        await collect(app.state.model_gateway.chat(ctx, session, request))
    assert denied.value.code == "model_denied" and "confidential" in denied.value.reason
    assert len(backends.chat) == calls


@pytest.mark.covers("INV-AUD-4")
async def test_upload_is_audited_without_content(hr, session):
    _, _, asset = hr
    row = await session.scalar(select(AuditEvent).where(
        AuditEvent.event_type == AuditEventType.DOCUMENT_UPLOADED,
        AuditEvent.data_asset_id == uuid.UUID(asset["id"])))  # fmt: skip
    assert row.details == {"filename": "hr.csv", "rows": 4}
