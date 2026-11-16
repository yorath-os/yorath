"""Policies as data and denial audit on a live database."""

import pytest
from sqlalchemy import select

from tests.fixtures.corpus import OWNER
from tests.fixtures.pdp_cases import ENTITIES, IVANOV, REC_IVANOV, SCENARIOS
from yorath.control.journal import AuditEvent, AuditEventType, Journal
from yorath.control.journal import Decision as AuditDecision
from yorath.control.model import DataClass, Policy
from yorath.enforcement.authorize import enforce
from yorath.enforcement.pdp import AuthzRequest, CedarPDP, EntityRef
from yorath.enforcement.policy_store import builtin_policies, combine, sync_default_policies

pytestmark = pytest.mark.group


async def test_sync_creates_rows_and_keeps_owner_edits(api, session):
    report = await sync_default_policies(session)
    assert set(report.created) == set(builtin_policies()) and report.warnings == []
    assert (await sync_default_policies(session)).created == []

    await api.post("/auth/setup", json=OWNER)
    rows = (await api.get("/policies")).json()
    assert {r["name"] for r in rows} == set(builtin_policies())
    assert all(r["description"].startswith("builtin:") for r in rows)

    custom = (
        builtin_policies()["models"]
        + '\n@id("extra")\npermit(principal, action == Yorath::Action::"use_tool", resource);'
    )
    r = await api.put("/policies/models", json={"cedar": custom})
    assert r.status_code == 200 and r.json()["description"] == "custom"

    report = await sync_default_policies(session)
    assert report.updated == [] and len(report.warnings) == 1 and "models" in report.warnings[0]
    row = await session.scalar(select(Policy).where(Policy.name == "models"))
    assert row.cedar == custom


@pytest.mark.covers("INV-POL-1")
async def test_invalid_policy_rejected_via_api(api, session):
    await sync_default_policies(session)
    await api.post("/auth/setup", json=OWNER)
    r = await api.put(
        "/policies/users",
        json={"cedar": "permit(principal, action, resource) when { principal.nope == 1 };"},
    )
    assert r.status_code == 422 and "nope" in r.json()["detail"]["errors"][0]
    row = await session.scalar(select(Policy).where(Policy.name == "users"))
    assert row.cedar == builtin_policies()["users"]
    # the PDP keeps deciding with the previous set
    name, request_, allowed, reasons = SCENARIOS[0]
    r = await api.post(
        "/authz/check", json={"request": request_.model_dump(), "entities": ENTITIES}
    )
    assert (
        r.status_code == 200 and r.json()["allowed"] is allowed and r.json()["reasons"] == reasons
    )


@pytest.mark.covers("INV-ID-2")
async def test_member_cannot_read_policies(api):
    await api.post("/auth/setup", json=OWNER)
    await api.post(
        "/users", json={"login": "anna", "password": "anna-pass-1", "display_name": "Anna"}
    )
    await api.post("/auth/logout")
    await api.post("/auth/login", json={"login": "anna", "password": "anna-pass-1"})
    assert (await api.get("/policies")).status_code == 403
    assert (
        await api.post(
            "/authz/check", json={"request": SCENARIOS[0][1].model_dump(), "entities": []}
        )
    ).status_code == 403


@pytest.mark.covers("INV-AUD-3")
async def test_enforce_writes_policy_denied(sessions, session):
    pdp = CedarPDP()
    pdp.load(combine(builtin_policies()))
    journal = Journal(sessions)
    denied = AuthzRequest.build(
        EntityRef(type="User", id=str(IVANOV.id)),
        "query_record",
        EntityRef(type="Record", id=str(REC_IVANOV)),
        agent="research-agent",
        context_class=1,
    )
    d = await enforce(pdp, journal, denied, ENTITIES, actor_principal_id=IVANOV.id,
                      effective_principal_id=IVANOV.id, tool="query_records", data_class=DataClass.CONFIDENTIAL)  # fmt: skip
    assert d.allowed is False
    row = await session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == AuditEventType.POLICY_DENIED)
        .order_by(AuditEvent.occurred_at.desc())
    )
    assert row.decision is AuditDecision.DENY and "agent-class-ceiling" in row.reason
    assert row.effective_principal_id == IVANOV.id and row.details["reasons"] == d.reasons

    allowed = await enforce(pdp, journal, SCENARIOS[0][1], ENTITIES, actor_principal_id=IVANOV.id)
    assert allowed.allowed is True
