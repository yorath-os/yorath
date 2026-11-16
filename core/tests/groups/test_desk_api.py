"""Desk and task manager: aggregates from real Executions and the journal."""

from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.fixtures.world import ask, seed_chat
from yorath.control.model import (
    Cluster,
    DataClass,
    Execution,
    ExecutionAttempt,
    ExecutionStatus,
    Node,
)
from yorath.db import utcnow

pytestmark = pytest.mark.group

QUESTION = "how many days can be carried over?"


async def fail_last(session, status=ExecutionStatus.FAILED, message="model failure", hours=0):
    """The last Execution became an error: the model stub answers successfully."""
    run = await session.scalar(select(Execution).order_by(Execution.created_at.desc()))
    run.status = status
    run.created_at = utcnow() - timedelta(hours=hours)
    run.finished_at = run.created_at
    attempt = await session.scalar(
        select(ExecutionAttempt).where(ExecutionAttempt.execution_id == run.id)
    )
    attempt.error_message = message
    await session.commit()
    return run


@pytest.fixture
async def world(make_app, session, tmp_path):
    api, app = await seed_chat(session, make_app, tmp_path)
    conversation = (await api.post("/chat/conversations", json={})).json()["id"]
    await ask(api, conversation, QUESTION)
    yield api, app, conversation
    await api.aclose()


async def test_summary_counts_real_runs_and_errors(world, session):
    api, _, conversation = world
    await ask(api, conversation, QUESTION)
    await fail_last(session)
    await ask(api, conversation, QUESTION)
    await fail_last(session, ExecutionStatus.TIMEOUT, "timed out", hours=5)

    desk = (await api.get("/desk/summary")).json()
    assert desk["running"] == 0 and desk["queued"] == 0
    assert desk["errors_hour"] == 1  # the timeout was five hours ago
    assert desk["agent_errors"] == [
        {"agent": "document-assistant", "total": 2, "by_status": {"failed": 1, "timeout": 1}}
    ]
    assert [e["message"] for e in desk["error_log"]] == ["model failure", "timed out"]
    assert desk["error_log"][0]["status"] == "failed"
    assert float(desk["cost_usd_24h"]) >= 0


async def test_summary_lists_running_task(world, session):
    api, _, _ = world
    run = await session.scalar(select(Execution).order_by(Execution.created_at.desc()))
    run.status, run.finished_at = ExecutionStatus.RUNNING, None
    await session.commit()
    desk = (await api.get("/desk/summary")).json()
    assert desk["running"] == 1
    assert desk["tasks"][0]["id"] == str(run.id) and desk["tasks"][0]["status"] == "running"
    assert desk["more_tasks"] == 0


async def test_summary_shows_policy_denials_with_reason(make_app, session, tmp_path):
    api, _ = await seed_chat(session, make_app, tmp_path, external_classes=[DataClass.PUBLIC])
    conversation = (await api.post("/chat/conversations", json={})).json()["id"]
    await ask(api, conversation, QUESTION)
    policies = (await api.get("/desk/summary")).json()["policies"]
    assert policies["denied"] >= 1 and policies["reasons"][0]["count"] >= 1
    assert policies["reasons"][0]["reason"]
    await api.aclose()


async def test_summary_nodes_come_from_inventory(world, session):
    api, _, _ = world
    cluster = await session.scalar(select(Cluster))
    if cluster is None:
        cluster = Cluster(name="local")
        session.add(cluster)
        await session.flush()
    session.add(Node(cluster_id=cluster.id, name="vps-01", cpu_count=8, ram_gb=8, gpu=None))
    await session.commit()
    nodes = (await api.get("/desk/summary")).json()["nodes"]
    assert {"name": "vps-01", "cpu_count": 8, "ram_gb": 8, "gpu": None, "vram_gb": None} in nodes


async def test_tasks_filter_and_counts(world, session):
    api, _, conversation = world
    await ask(api, conversation, QUESTION)
    failed = await fail_last(session)

    body = (await api.get("/tasks")).json()
    assert body["counts"] == {"all": 2, "running": 0, "queued": 0, "failed": 1}
    assert [t["id"] for t in body["tasks"]][0] == str(failed.id)

    only = (await api.get("/tasks?status=failed")).json()
    assert [t["id"] for t in only["tasks"]] == [str(failed.id)]
    assert only["counts"]["all"] == 2  # counters do not depend on the selected group
    task = only["tasks"][0]
    assert task["agent"] == "document-assistant" and task["trigger"] == "user_message"
    assert task["model"] == "fake-chat" and task["error"] == "model failure"
    assert task["started_by"] and task["revision_no"] == 1

    assert (await api.get("/tasks?agent=research-agent")).json()["tasks"] == []
    assert (await api.get("/tasks?trigger=schedule")).json()["tasks"] == []


async def test_tasks_never_contain_question_text(world):
    api, _, _ = world
    assert QUESTION not in (await api.get("/tasks")).text
    assert QUESTION not in (await api.get("/desk/summary")).text


async def test_member_is_refused(world):
    api, app, _ = world
    login = {"login": "member1", "password": "password-1"}
    r = await api.post("/users", json={**login, "display_name": "Member"})
    assert r.status_code == 201
    member = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    assert (await member.post("/auth/login", json=login)).status_code == 200
    assert (await member.get("/desk/summary")).status_code == 403
    assert (await member.get("/tasks")).status_code == 403
    await member.aclose()
