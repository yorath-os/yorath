"""Agents outside Yorath on the System screen (change foreign-agents): the yorath scan report in the core."""

import json
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fixtures.world import seed_chat
from yorath.db import utcnow

pytestmark = pytest.mark.group

HERMES = {
    "agent": "hermes",
    "where": "host",
    "pid": 101,
    "user": "root",
    "root": True,
    "command": "hermes",
}


def write(dirpath, checked_at, agents):
    dirpath.mkdir(exist_ok=True)
    report = {"checked_at": checked_at.isoformat(timespec="seconds"), "agents": agents,
              "traces": [], "containers": [{"name": "supabase_db", "image": "x", "project": "supabase"}]}  # fmt: skip
    (dirpath / "host.json").write_text(json.dumps(report))


async def test_owner_sees_agents_outside_yorath(make_app, session, tmp_path):
    reports = tmp_path / "report"
    owner, app = await seed_chat(session, make_app, tmp_path)
    app.state.settings.host_report_dir = reports
    empty = (await owner.get("/setup/host-report")).json()
    assert (empty["available"], empty["agents"]) == (False, [])  # the scan has not run yet

    write(reports, utcnow(), [HERMES])
    fresh = (await owner.get("/setup/host-report")).json()
    assert (fresh["available"], fresh["stale"]) == (True, False)
    assert fresh["agents"] == [HERMES]
    assert [c["name"] for c in fresh["containers"]] == ["supabase_db"]

    write(reports, utcnow() - timedelta(minutes=30), [HERMES])
    assert (await owner.get("/setup/host-report")).json()["stale"] is True

    body = {"login": "anna", "password": "password-1", "display_name": "anna"}
    assert (await owner.post("/users", json=body)).status_code == 201
    member = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await member.post("/auth/login", json={"login": "anna", "password": "password-1"})
    assert (await member.get("/setup/host-report")).status_code == 403
    await member.aclose()
    await owner.aclose()
