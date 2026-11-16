"""Login, sessions, roles and service accounts on a live database."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.fixtures.corpus import OWNER
from tests.fixtures.world import make_agent
from yorath.control.identity.service import ensure_service_accounts, open_session
from yorath.control.journal import AuditEvent, AuditEventType
from yorath.control.model import (
    AgentMode,
    Principal,
    PrincipalKind,
    ServiceAccount,
    User,
    UserSession,
)

pytestmark = pytest.mark.group


@pytest.mark.covers("INV-ID-1")
async def test_setup_then_login_logout_flow(api, session):
    r = await api.get("/auth/me")
    assert r.status_code == 200 and r.json() == {"authenticated": False, "setup_required": True}
    assert (await api.get("/users")).status_code == 404  # protected routes: setup_required

    r = await api.post("/auth/setup", json=OWNER)
    assert r.status_code == 201 and r.json()["role"] == "owner" and "password" not in r.text
    assert "yorath_session" in r.cookies
    assert (await api.get("/auth/me")).json()["login"] == "alex"

    r = await api.post("/auth/setup", json=OWNER)
    assert r.status_code == 409

    token = api.cookies["yorath_session"]
    r = await api.post("/auth/logout")
    assert r.status_code == 204
    assert (await api.get("/auth/me")).json()["authenticated"] is False
    assert (await api.get("/users")).status_code == 401
    # the client clears the cookie itself on logout: the server-side revoke is checked with the same cookie only
    api.cookies.set("yorath_session", token)
    assert (await api.get("/auth/me")).json()["authenticated"] is False
    assert (await api.get("/users")).status_code == 401
    api.cookies.clear()

    r = await api.post("/auth/login", json={"login": "alex", "password": "nope"})
    assert r.status_code == 401
    r = await api.post("/auth/login", json={"login": "nobody", "password": "nope"})
    assert r.status_code == 401 and r.json()["detail"] == "invalid_credentials"
    r = await api.post("/auth/login", json={"login": "alex", "password": OWNER["password"]})
    assert r.status_code == 200

    types = (
        await session.scalars(select(AuditEvent.event_type).order_by(AuditEvent.occurred_at))
    ).all()
    assert types == [
        AuditEventType.USER_CREATED,
        AuditEventType.AUTH_LOGOUT,
        AuditEventType.AUTH_LOGIN_FAILED,
        AuditEventType.AUTH_LOGIN_FAILED,
        AuditEventType.AUTH_LOGIN,
    ]
    failed = (
        await session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == AuditEventType.AUTH_LOGIN_FAILED)
        )
    ).all()
    assert {e.details["login"] for e in failed} == {"alex", "nobody"}
    assert all("password" not in e.details for e in failed)
    ok = await session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == AuditEventType.AUTH_LOGIN)
    )
    assert ok.actor_principal_id is not None

    row = await session.scalar(select(UserSession).order_by(UserSession.created_at.desc()).limit(1))
    assert row.token_hash != api.cookies["yorath_session"] and len(row.token_hash) == 64


@pytest.mark.covers("INV-ID-2", "INV-ID-1")
async def test_owner_manages_users_and_member_cannot(api, session):
    await api.post("/auth/setup", json=OWNER)
    r = await api.post(
        "/users",
        json={
            "login": "anna",
            "password": "anna-pass-1",
            "display_name": "Anna",
            "role": "member",
            "department": "HR",
        },
    )
    assert r.status_code == 201
    anna_id = r.json()["id"]
    assert r.json()["department"] == "HR" and r.json()["is_active"] is True
    r = await api.post(
        "/users", json={"login": "anna", "password": "anna-pass-1", "display_name": "x"}
    )
    assert r.status_code == 409 and r.json()["detail"] == "login_taken"
    assert len((await api.get("/users")).json()) == 2

    await api.post("/auth/logout")
    r = await api.post("/auth/login", json={"login": "anna", "password": "anna-pass-1"})
    assert r.status_code == 200
    assert (await api.get("/users")).status_code == 403
    assert (
        await api.post("/users", json={"login": "x", "password": "12345678", "display_name": "x"})
    ).status_code == 403

    # the owner deactivates Anna: her session and login are closed
    anna_token = api.cookies["yorath_session"]
    api.cookies.clear()
    await api.post("/auth/login", json={"login": "alex", "password": OWNER["password"]})
    r = await api.patch(f"/users/{anna_id}", json={"is_active": False, "password": "new-pass-123"})
    assert r.status_code == 200 and r.json()["is_active"] is False
    api.cookies.clear()
    api.cookies.set("yorath_session", anna_token)
    assert (await api.get("/auth/me")).json()["authenticated"] is False
    r = await api.post("/auth/login", json={"login": "anna", "password": "new-pass-123"})
    assert r.status_code == 401
    updated = await session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == AuditEventType.USER_UPDATED)
    )
    assert updated.details["changes"]["password"] == "***"


@pytest.mark.covers("INV-ID-1")
async def test_expired_session_is_rejected(api, session):
    await api.post("/auth/setup", json=OWNER)
    user = await session.scalar(select(User))
    _, token = await open_session(session, user, timedelta(hours=-1))
    api.cookies.clear()
    api.cookies.set("yorath_session", token)
    assert (await api.get("/auth/me")).json()["authenticated"] is False
    assert (await api.get("/chat/conversations")).status_code == 401


async def test_service_accounts_for_autonomous_agents(api, session):
    pagey = await make_agent(session, "pagey", AgentMode.AUTONOMOUS, runtime="codex")
    await make_agent(session, "da", AgentMode.DELEGATED)
    await session.commit()
    created, warnings = await ensure_service_accounts(session)
    assert created == [] and len(warnings) == 1 and "owner" in warnings[0]

    await api.post("/auth/setup", json=OWNER)  # the first owner gets agent accounts right away
    created, warnings = await ensure_service_accounts(session)
    assert created == [] and warnings == []

    accounts = (await session.scalars(select(ServiceAccount))).all()
    assert len(accounts) == 1 and accounts[0].agent_id == pagey.agent_id
    owner = await session.scalar(select(User).where(User.role == "owner"))
    assert accounts[0].owner_user_id == owner.id
    principal = await session.get(Principal, accounts[0].id)
    assert principal.kind is PrincipalKind.SERVICE and principal.display_name.endswith("-svc")
