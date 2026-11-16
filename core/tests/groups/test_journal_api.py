"""Journal screen: real events from chat, filters, the Execution chain, proof in the trace."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.fixtures.world import ask_as, event, open_for_owner, seed_demo, seed_web
from yorath.control.model import DataClass, Execution, User

pytestmark = pytest.mark.group


@pytest.fixture
async def journal(make_app, session, tmp_path, backends):
    """HR demo set and the manager's question about Miller: the namesake is dropped."""
    api, app = await seed_demo(session, make_app, tmp_path)
    users = {u["title"]: u for u in (await api.post("/data/demo-hr")).json()["users"]}
    events = await ask_as(app, backends, users["Manager"])
    yield api, app, users, event(events, "done")["execution_id"]
    await api.aclose()


async def manager(session) -> User:
    return await session.scalar(select(User).where(User.login == "max.lewis"))


@pytest.mark.covers("INV-AUD-5")
async def test_chain_fields_have_names_for_execution(journal, session):
    api, _, _, execution_id = journal
    r = await api.get(f"/journal/executions/{execution_id}")
    assert r.status_code == 200
    detail = r.json()
    assert [c["agent"] for c in detail["chain"]] == ["document-assistant"]
    assert detail["chain"][0]["actor"] == "Max Lewis"

    tool = next(e for e in detail["events"] if e["event_type"] == "data.accessed")
    assert tool["actor"] == tool["effective_principal"] == "Max Lewis"
    assert tool["agent"] == "document-assistant" and tool["revision_no"] == 1
    assert tool["tool"] == "query_records" and tool["data_asset"] == "hr-vacations"
    assert tool["data_class"] == "confidential" and tool["decision"] == "permit"
    llm = next(e for e in detail["events"] if e["event_type"] == "llm.called")
    assert llm["model"] == "fake-chat"


@pytest.mark.covers("INV-REC-1", "INV-AUD-5")
async def test_trace_proves_denied_namesake_never_reached_model(journal):
    api, _, _, execution_id = journal
    detail = (await api.get(f"/journal/executions/{execution_id}")).json()
    trace = str([s["attributes"] for s in detail["spans"]])
    assert "Peter Miller" in trace  # anchor: the model context is in the trace
    assert "Ryan Miller" not in trace
    denied = next(e for e in detail["events"] if e["decision"] == "deny")
    assert denied["details"] == {"rows_denied": 1} and denied["reason"]
    assert "Miller" not in str(denied)


async def test_filters(journal, session, backends):
    api, app, users, _ = journal
    await ask_as(app, backends, users["HRBP"])
    lebedev = await manager(session)

    everything = (await api.get("/journal/events?limit=200")).json()
    assert {e["actor"] for e in everything} >= {
        "Max Lewis",
        "Olivia Newman",
    }
    times = [e["occurred_at"] for e in everything]
    assert times == sorted(times, reverse=True)

    mine = (await api.get(f"/journal/events?user={lebedev.id}&limit=200")).json()
    assert mine and {e["effective_principal"] for e in mine} <= {"Max Lewis", None}
    assert all(e["actor"] != "Olivia Newman" for e in mine)

    denied = (await api.get("/journal/events?decision=deny")).json()
    assert denied and {e["decision"] for e in denied} == {"deny"}
    assert len(denied) < len(everything)

    agent = (await api.get("/journal/events?agent=document-assistant&limit=200")).json()
    assert agent and {e["agent"] for e in agent} == {"document-assistant"}
    assert (await api.get("/journal/events?agent=research-agent")).json() == []


async def test_pages_do_not_overlap(journal):
    api, *_ = journal
    first = (await api.get("/journal/events?limit=3")).json()
    second = (
        await api.get("/journal/events", params={"limit": 3, "before": first[-1]["occurred_at"]})
    ).json()
    assert len(first) == len(second) == 3
    assert not {e["id"] for e in first} & {e["id"] for e in second}
    assert second[0]["occurred_at"] < first[-1]["occurred_at"]


@pytest.mark.covers("INV-AUD-5")
async def test_child_and_parent_return_same_chain(make_app, session, backends):
    api, app = await seed_web(session, make_app, backends)
    ctx = await open_for_owner(app, session)
    ctx.raise_class(DataClass.INTERNAL)
    result = await app.state.tool_gateway.call_tool(
        ctx, session, "ask_agent", {"agent": "research-agent", "question": "how many days?"}
    )
    assert not result.is_error
    child = await session.scalar(
        select(Execution).where(Execution.parent_execution_id == ctx.execution_id)
    )
    parent_view = (await api.get(f"/journal/executions/{ctx.execution_id}")).json()
    child_view = (await api.get(f"/journal/executions/{child.id}")).json()
    assert [c["id"] for c in parent_view["chain"]] == [c["id"] for c in child_view["chain"]]
    assert [c["agent"] for c in parent_view["chain"]] == ["document-assistant", "research-agent"]
    assert parent_view["chain"][1]["parent_execution_id"] == str(ctx.execution_id)
    assert {e["execution_id"] for e in child_view["events"]} == {
        str(ctx.execution_id),
        str(child.id),
    }
    await api.aclose()


@pytest.mark.covers("INV-ID-2")
async def test_journal_is_closed_for_member(journal):
    api, app, users, execution_id = journal
    assert (await api.get("/journal/events")).status_code == 200  # anchor: open to the owner
    employee = users["Employee"]
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await client.post(
        "/auth/login", json={"login": employee["login"], "password": employee["password"]}
    )
    assert (await client.get("/journal/events")).status_code == 403
    assert (await client.get(f"/journal/executions/{execution_id}")).status_code == 403
    await client.aclose()


async def test_unknown_execution_is_404(journal):
    api, *_ = journal
    assert (await api.get(f"/journal/executions/{uuid.uuid7()}")).status_code == 404
