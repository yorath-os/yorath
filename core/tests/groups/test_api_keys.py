"""Users' API keys (change model-endpoint): shown once, hash in the database, revoke, audit."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.fixtures.corpus import OWNER
from yorath.control.identity.api_keys import resolve_api_key
from yorath.control.journal import AuditEvent, AuditEventType
from yorath.control.model import ApiKey, User

pytestmark = pytest.mark.group

MEMBERS = ("anna", "boris")


@pytest.fixture
async def people(make_app):
    """The owner and two members, each with their own login."""
    app = make_app()
    owner = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await owner.post("/auth/setup", json=OWNER)
    clients = {"owner": owner}
    for login in MEMBERS:
        body = {"login": login, "password": "password-1", "display_name": login}
        assert (await owner.post("/users", json=body)).status_code == 201
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        r = await client.post("/auth/login", json={"login": login, "password": "password-1"})
        assert r.status_code == 200
        clients[login] = client
    yield clients
    for client in clients.values():
        await client.aclose()


async def create(client, name: str) -> dict:
    r = await client.post("/api-keys", json={"name": name})
    assert r.status_code == 201
    return r.json()


async def audit(session, event: AuditEventType) -> list[AuditEvent]:
    return list(
        (await session.scalars(select(AuditEvent).where(AuditEvent.event_type == event))).all()
    )


@pytest.mark.covers("INV-KEY-1")
async def test_key_is_shown_once_and_stored_as_hash(people, session):
    created = await create(people["anna"], "laptop")
    key = created["key"]
    assert key.startswith("yorath_key_") and len(key) > 40
    assert created["prefix"] == key[:18] and created["login"] == "anna"

    listed = (await people["anna"].get("/api-keys")).json()
    assert [k["name"] for k in listed] == ["laptop"] and "key" not in listed[0]
    row = await session.scalar(select(ApiKey))
    assert row.token_hash != key and len(row.token_hash) == 64

    user = await resolve_api_key(session, key)
    assert user is not None and user.login == "anna"
    assert await resolve_api_key(session, key[:-1] + ("A" if key[-1] != "A" else "B")) is None

    issued = await audit(session, AuditEventType.SECRET_ISSUED)
    assert [e.details["kind"] for e in issued] == ["api_key"]
    assert key not in str(issued[0].details) and issued[0].details["name"] == "laptop"


async def test_member_sees_and_revokes_only_own_keys(people, session):
    anna = await create(people["anna"], "a")
    await create(people["boris"], "b")
    assert [k["name"] for k in (await people["boris"].get("/api-keys")).json()] == ["b"]

    r = await people["boris"].delete(f"/api-keys/{anna['id']}")
    assert r.status_code == 404
    assert await resolve_api_key(session, anna["key"]) is not None


@pytest.mark.covers("INV-KEY-1")
async def test_owner_sees_all_and_revokes_member_key(people, session):
    anna = await create(people["anna"], "a")
    await create(people["boris"], "b")
    listed = (await people["owner"].get("/api-keys")).json()
    assert sorted(k["login"] for k in listed) == ["anna", "boris"]

    assert (await people["owner"].delete(f"/api-keys/{anna['id']}")).status_code == 204
    assert await resolve_api_key(session, anna["key"]) is None
    revoked = await audit(session, AuditEventType.SECRET_REVOKED)
    owner = await session.scalar(select(User).where(User.role == "owner"))
    assert len(revoked) == 1 and revoked[0].actor_principal_id == owner.id
    assert revoked[0].details["key_id"] == anna["id"]


@pytest.mark.covers("INV-KEY-1")
async def test_disabling_user_revokes_all_keys(people, session):
    first, second = await create(people["anna"], "1"), await create(people["anna"], "2")
    boris = await create(people["boris"], "b")
    anna_id = await session.scalar(select(User.id).where(User.login == "anna"))
    r = await people["owner"].patch(f"/users/{anna_id}", json={"is_active": False})
    assert r.status_code == 200
    session.expire_all()
    assert await resolve_api_key(session, first["key"]) is None
    assert await resolve_api_key(session, second["key"]) is None
    assert await resolve_api_key(session, boris["key"]) is not None  # anchor: other keys are alive
    keys = (await session.scalars(select(ApiKey).where(ApiKey.user_id == anna_id))).all()
    assert all(k.revoked_at is not None for k in keys)
