"""HR demo set: three users with issued passwords ask the same question (criterion 4)."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

import yorath.control.demo
from tests.fixtures.world import ask_as, seed_demo
from yorath.control.journal import AuditEvent, AuditEventType
from yorath.control.model import Chunk, DataRecord, User

pytestmark = pytest.mark.group


@pytest.fixture
async def demo(make_app, session, tmp_path):
    api, app = await seed_demo(session, make_app, tmp_path)
    yield app, api
    await api.aclose()


async def tool_result(app, backends, user: dict) -> str:
    """The tool result as the model sees it."""
    await ask_as(app, backends, user)
    return next(m["content"] for m in backends.chat[-1]["messages"] if m["role"] == "tool")


@pytest.mark.covers("INV-REC-1")
async def test_three_personas_get_three_answers(demo, backends):
    app, api = demo
    r = await api.post("/data/demo-hr")
    assert r.status_code == 201 and r.json()["rows"] == 15
    users = {u["title"]: u for u in r.json()["users"]}
    assert set(users) == {"Employee", "Manager", "HRBP"}

    employee = await tool_result(app, backends, users["Employee"])
    manager = await tool_result(app, backends, users["Manager"])
    hrbp = await tool_result(app, backends, users["HRBP"])
    assert employee == "No records match the query."
    assert "Peter Miller" in manager and "vacation_days_left: 12" in manager
    assert "Ryan Miller" not in manager
    assert "Ryan Miller" in hrbp and "vacation_days_left: 9" in hrbp
    assert "Peter Miller" not in hrbp


async def test_demo_uses_regular_journal_and_indexes_policy(demo, session):
    app, api = demo
    assert (await api.post("/data/demo-hr")).status_code == 201
    await app.state.indexer.wait_all()
    docs = (await api.get("/data/documents")).json()
    assert [(d["filename"], d["data_class"]) for d in docs] == [
        ("hr-vacation-policy.md", "internal")
    ]
    uploads = await session.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.event_type == AuditEventType.DOCUMENT_UPLOADED)
    )  # fmt: skip
    assert uploads == 2  # the table and the document


async def test_policy_uploaded_earlier_does_not_break_demo(demo):
    _, api = demo
    assets = {a["name"]: a for a in (await api.get("/data/assets")).json()}
    policy = (Path(yorath.control.demo.__file__).parent / "hr-vacation-policy.md").read_bytes()
    r = await api.post(
        f"/data/assets/{assets['team-docs']['id']}/documents",
        files={"file": ("hr-vacation-policy.md", policy, "text/markdown")},
    )
    assert r.status_code == 201
    r = await api.post("/data/demo-hr")
    assert r.status_code == 201 and len(r.json()["users"]) == 3
    assert len((await api.get("/data/documents")).json()) == 1


async def test_second_enable_changes_nothing(demo, session):
    _, api = demo
    first = await api.post("/data/demo-hr")
    assert first.status_code == 201
    hashes = {u.login: u.password_hash for u in (await session.scalars(select(User))).all()}
    second = await api.post("/data/demo-hr")
    assert second.status_code == 409 and second.json()["detail"]["code"] == "already_enabled"
    session.expire_all()
    assert hashes == {u.login: u.password_hash for u in (await session.scalars(select(User))).all()}
    assert await session.scalar(select(func.count()).select_from(DataRecord)) == 15


async def test_existing_login_blocks_before_any_write(demo, session):
    _, api = demo
    body = {"login": "anna.parker", "password": "password-1", "display_name": "Another Anna"}
    assert (await api.post("/users", json=body)).status_code == 201
    assert (await api.post("/data/demo-hr")).status_code == 409
    names = [a["name"] for a in (await api.get("/data/assets")).json()]
    assert "team-docs" in names and "hr-vacations" not in names  # anchor: the list is readable


@pytest.mark.covers("INV-ID-2")
async def test_only_owner_enables_demo(demo, session):
    app, api = demo
    body = {"login": "member1", "password": "password-1", "display_name": "Member"}
    assert (await api.post("/users", json=body)).status_code == 201
    member = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await member.post("/auth/login", json={"login": "member1", "password": "password-1"})
    assert (await member.post("/data/demo-hr")).status_code == 403
    await member.aclose()
    assert await session.scalar(select(func.count()).select_from(DataRecord)) == 0


async def test_a_document_uploaded_before_the_embeddings_model_is_indexed_once_it_arrives(
    make_app, session, backends
):
    """The HR demo enabled before the wizard's model step: its policy waits, then gets indexed."""
    app = make_app()
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await api.post("/auth/setup", json={"login": "owner", "password": "password-1",
                                        "display_name": "Owner"})  # fmt: skip
    assert (await api.post("/data/demo-hr")).status_code == 201
    await app.state.indexer.wait_all()
    [doc] = (await api.get("/data/documents")).json()
    assert doc["status"] == "parsed", "no embeddings model yet: not indexed"
    pull = {"model": "embeddinggemma-2:570m", "role": "embeddings"}
    assert (await api.post("/setup/models/pull", json=pull)).status_code == 200
    await app.state.indexer.wait_all()
    [doc] = (await api.get("/data/documents")).json()
    assert doc["status"] == "indexed"
    assert await session.scalar(select(func.count()).select_from(Chunk)) > 0
    await api.aclose()
