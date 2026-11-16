"""Developer Agent on a fake GitHub and runner: signature, label, Outcome only from GitHub."""

import hashlib
import hmac
import json

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.fixtures.corpus import AGENTS_DIR, OWNER
from tests.fixtures.world import register_model
from yorath.control.agents.loader import sync_agents_dir
from yorath.control.identity.service import ensure_service_accounts
from yorath.control.journal import AuditEvent, AuditEventType, ControlLevel, TraceSpan
from yorath.control.model import (
    Agent,
    DataClass,
    Execution,
    ExecutionStatus,
    ExecutionTrigger,
    ModelProvider,
    Outcome,
    OutcomeKind,
    OutcomeStatus,
    ServiceAccount,
)

pytestmark = pytest.mark.group

SECRET = "webhook-secret"
REPO = "acme/pagey"
PAYLOAD = {
    "action": "labeled",
    "label": {"name": "yorath"},
    "repository": {"full_name": REPO, "default_branch": "main"},
    "issue": {"number": 5, "title": "Fix the test", "body": "test_x fails"},
}


def private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


CONFIG = {
    "app_id": "1",
    "installation_id": "42",
    "private_key": private_key(),
    "webhook_secret": SECRET,
    "repositories": [REPO],
}


@pytest.fixture
async def world(make_app, session, tmp_path):
    app = make_app(
        agents_dir=AGENTS_DIR,
        documents_dir=tmp_path / "docs",
        secret_key=Fernet.generate_key().decode(),
        sandbox_dir=tmp_path / "sandbox",
        sandbox_url="http://runner.test",
        sandbox_token="t",
        github_api_url="http://gh.test",
        egress_log=str(tmp_path / "access.log"),
    )
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await api.post("/auth/setup", json=OWNER)
    await sync_agents_dir(session, AGENTS_DIR)
    await ensure_service_accounts(session)
    await register_model(
        session, "openrouter/default", ModelProvider.OLLAMA, "fake", list(DataClass)
    )
    assert (await api.put("/github/app", json=CONFIG)).status_code == 200
    yield api, app, tmp_path
    await api.aclose()


async def post_event(api, payload=PAYLOAD, *, event="issues", delivery="d1", secret=SECRET):
    body = json.dumps(payload).encode()
    sign = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "X-Hub-Signature-256": sign,
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
    }
    return await api.post("/github/webhook", content=body, headers=headers)


async def wait(app):
    import asyncio

    while app.state.background:
        await asyncio.gather(*list(app.state.background))


async def executions(session) -> list[Execution]:
    session.expire_all()
    return list((await session.scalars(select(Execution))).all())


async def audit_types(session, execution_id) -> list[str]:
    rows = await session.scalars(select(AuditEvent).where(AuditEvent.execution_id == execution_id))
    return [r.event_type.value for r in rows]


@pytest.mark.covers("INV-DEV-1")
async def test_webhook_refuses_bad_signature(world, session):
    api, _, _ = world
    r = await post_event(api, secret="wrong")
    assert r.status_code == 401 and await executions(session) == []


@pytest.mark.covers("INV-DEV-1")
async def test_webhook_needs_configuration(make_app, session, tmp_path):
    app = make_app(agents_dir=AGENTS_DIR, secret_key=Fernet.generate_key().decode())
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    assert (await post_event(api)).status_code == 503
    await api.aclose()


@pytest.mark.covers("INV-DEV-1")
@pytest.mark.parametrize(
    "change",
    [
        {"label": {"name": "bug"}},
        {"action": "opened"},
        {"repository": {"full_name": "evil/repo", "default_branch": "main"}},
        {"issue": {"number": 5, "title": "t", "body": "", "pull_request": {}}},
    ],
)
async def test_unrelated_events_do_not_start(world, session, backends, change):
    api, app, _ = world
    r = await post_event(api, {**PAYLOAD, **change})
    assert r.status_code == 202 and r.json() == {"accepted": False}
    await wait(app)
    assert await executions(session) == [] and backends.sandbox_runs == []


async def test_other_github_event_type_is_ignored(world, session):
    api, _, _ = world
    assert (await post_event(api, event="push")).json() == {"accepted": False}


@pytest.mark.covers("INV-DEV-2", "INV-DEV-3")
async def test_issue_becomes_pull_request_outcome(world, session, backends):
    api, app, tmp_path = world
    (tmp_path / "access.log").write_text(
        "9999999999.0 5 172.30.0.9 TCP_DENIED/403 10 CONNECT evil.test:443 - HIER_NONE/- -\n"
        "9999999999.0 5 172.30.0.8 TCP_DENIED/403 10 CONNECT other.test:443 - HIER_NONE/- -\n"
    )
    assert (await post_event(api)).json() == {"accepted": True}
    await wait(app)

    (execution,) = await executions(session)
    assert execution.status is ExecutionStatus.SUCCEEDED
    assert execution.trigger is ExecutionTrigger.EVENT
    agent = await session.scalar(select(Agent).where(Agent.name == "developer-agent"))
    account = await session.scalar(
        select(ServiceAccount).where(ServiceAccount.agent_id == agent.id)
    )
    assert execution.initiated_by_principal_id == account.id == execution.effective_principal_id

    outcome = await session.scalar(select(Outcome))
    assert outcome.kind is OutcomeKind.PULL_REQUEST and outcome.status is OutcomeStatus.PENDING
    assert outcome.ref == "https://gh/pull/7" and outcome.data["commits"] == ["abc123"]

    token_call = next(c for c in backends.github if c[1].endswith("/access_tokens"))
    assert token_call[2]["repositories"] == ["pagey"]
    pull = next(c for c in backends.github if c[1].endswith("/pulls"))
    assert pull[2]["head"] == "yorath/issue-5" and pull[2]["base"] == "main"
    assert (
        pull[2]["title"] == "chore: Fix the test"
    )  # the "Done" summary is not in Conventional format
    assert "Closes #5" in pull[2]["body"] and "Done" in pull[2]["body"]
    assert ("DELETE", "/installation/token", None) in backends.github
    assert not any("merge" in c[1] for c in backends.github)

    types = await audit_types(session, execution.id)
    for needed in ("sandbox.started", "sandbox.stopped", "secret.issued", "secret.revoked",
                   "egress.blocked", "outcome.recorded"):  # fmt: skip
        assert needed in types
    rows = (
        await session.scalars(select(AuditEvent).where(AuditEvent.execution_id == execution.id))
    ).all()
    assert "ghs_secret_token" not in str([r.details for r in rows])
    blocked = next(r for r in rows if r.event_type is AuditEventType.EGRESS_BLOCKED)
    assert blocked.details == {"domain": "evil.test", "count": 1}
    assert blocked.control_level is ControlLevel.SANDBOX

    run = backends.sandbox_runs[0]
    assert "ghs_secret_token" not in json.dumps(run) and run["command"][0] == "sh"
    assert not list((tmp_path / "sandbox").iterdir())  # the run directory with tokens is removed


@pytest.mark.covers("INV-DEV-2")
async def test_success_claimed_without_commits_gives_no_outcome(world, session, backends):
    api, app, _ = world
    backends.github_commits = []
    await post_event(api)
    await wait(app)
    assert await session.scalar(select(Outcome)) is None
    assert not any(c[1].endswith("/pulls") for c in backends.github)
    comment = next(c for c in backends.github if c[1].endswith("/comments"))
    assert "the branch has no changes" in comment[2]["body"]


@pytest.mark.covers("INV-DEV-3")
async def test_sandbox_failure_revokes_token_and_marks_failed(world, session, backends):
    api, app, tmp_path = world
    backends.sandbox_lines = [{"exit": 125, "timed_out": False, "error": "unknown runtime: runsc"}]
    await post_event(api)
    await wait(app)
    (execution,) = await executions(session)
    assert execution.status is ExecutionStatus.FAILED
    assert ("DELETE", "/installation/token", None) in backends.github
    assert await session.scalar(select(Outcome)) is None
    assert not list((tmp_path / "sandbox").iterdir())


async def test_push_failure_is_named(world, session, backends):
    api, app, _ = world
    backends.sandbox_lines = [{"exit": 4, "timed_out": False, "stderr": "Codex noise"}]
    await post_event(api)
    await wait(app)
    comment = next(c for c in backends.github if c[1].endswith("/comments"))
    assert "could not push the branch" in comment[2]["body"]


async def test_timeout_status_is_kept(world, session, backends):
    api, app, _ = world
    backends.sandbox_lines = [{"started": True, "ip": None}, {"exit": 137, "timed_out": True}]
    await post_event(api)
    await wait(app)
    (execution,) = await executions(session)
    assert execution.status is ExecutionStatus.TIMEOUT


async def test_github_failure_closes_execution(world, session, backends):
    api, app, _ = world
    backends.github_failures = {"/access_tokens"}
    await post_event(api)
    await wait(app)
    (execution,) = await executions(session)
    assert execution.status is ExecutionStatus.FAILED and backends.sandbox_runs == []


@pytest.mark.covers("INV-DEV-4")
async def test_same_delivery_runs_once(world, session):
    api, app, _ = world
    assert (await post_event(api)).json() == {"accepted": True}
    await wait(app)
    assert (await post_event(api)).json() == {"accepted": False}
    assert len(await executions(session)) == 1


async def test_pull_title_comes_from_agent_summary(world, session, backends):
    api, app, _ = world
    message = {"type": "agent_message", "text": "fix(tests): repair test_x\n\nFixed the import."}
    backends.sandbox_lines[2] = {"line": json.dumps({"type": "item.completed", "item": message})}
    await post_event(api)
    await wait(app)
    pull = next(c for c in backends.github if c[1].endswith("/pulls"))
    assert pull[2]["title"] == "fix(tests): repair test_x"
    assert pull[2]["body"].startswith("Fixed the import.\n\nCloses #5")


async def test_runtime_spans_are_marked_and_do_not_set_cost(world, session, backends):
    api, app, tmp_path = world
    change = {
        "type": "file_change",
        "status": "failed",
        "changes": [
            {"path": f"{tmp_path}/sandbox/{{execution_id}}/ws/CLAUDE.md", "kind": "update"}
        ],
    }
    line = json.dumps({"type": "item.completed", "item": change})
    backends.sandbox_lines.insert(2, {"line": line})
    await post_event(api)
    await wait(app)
    (execution,) = await executions(session)
    spans = (
        await session.scalars(select(TraceSpan).where(TraceSpan.execution_id == execution.id))
    ).all()
    tool = next(s for s in spans if s.name == "tool.called")
    assert tool.attributes == {
        "kind": "file_change",
        "status": "failed",
        "paths": ["CLAUDE.md"],
        "yorath.source": "runtime",
    }
    llm = next(s for s in spans if s.name == "llm.called")
    assert llm.attributes["yorath.source"] == "runtime"
    assert llm.attributes["usage"] == {"input_tokens": 5, "output_tokens": 2}
    finished = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.execution_id == execution.id,
            AuditEvent.event_type == AuditEventType.EXECUTION_FINISHED,
        )
    )
    # there were no calls through the Gateway: the runtime's 5 + 2 tokens do not become cost
    assert finished.cost_usd == 0 and finished.details["usage"] == {}


@pytest.mark.covers("INV-DEV-4")
async def test_redelivery_runs_after_refused_start(world, session, backends):
    api, app, _ = world
    await api.patch("/agents/developer-agent", json={"enabled": False})
    assert (await post_event(api)).json() == {"accepted": True}
    await wait(app)
    assert await executions(session) == []
    await api.patch("/agents/developer-agent", json={"enabled": True})
    assert (await post_event(api)).json() == {"accepted": True}  # GitHub repeated the same delivery
    await wait(app)
    assert len(await executions(session)) == 1 and len(backends.sandbox_runs) == 1


async def test_owner_created_after_core_start_can_run_agent(make_app, session, tmp_path):
    # the real order: the core syncs agents without an owner, then the wizard creates the owner
    app = make_app(
        agents_dir=AGENTS_DIR,
        documents_dir=tmp_path / "docs",
        secret_key=Fernet.generate_key().decode(),
        sandbox_dir=tmp_path / "sandbox",
        sandbox_url="http://runner.test",
        sandbox_token="t",
        github_api_url="http://gh.test",
        egress_log=str(tmp_path / "access.log"),
    )
    await sync_agents_dir(session, AGENTS_DIR)
    assert await ensure_service_accounts(session) == (
        [],
        ["developer-agent: service account not created: no user with the owner role"],
    )
    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    assert (await api.post("/auth/setup", json=OWNER)).status_code == 201
    await register_model(
        session, "openrouter/default", ModelProvider.OLLAMA, "fake", list(DataClass)
    )
    assert (await api.put("/github/app", json=CONFIG)).status_code == 200
    assert (await post_event(api)).json() == {"accepted": True}
    await wait(app)
    runs = await executions(session)
    assert len(runs) == 1 and runs[0].status is ExecutionStatus.SUCCEEDED
    await api.aclose()


async def test_disabled_agent_does_not_run(world, session, backends):
    api, app, _ = world
    await api.patch("/agents/developer-agent", json={"enabled": False})
    await post_event(api)
    await wait(app)
    assert await executions(session) == [] and backends.sandbox_runs == []


async def test_status_hides_secrets_and_is_owner_only(world):
    api, app, _ = world
    body = (await api.get("/github/app")).json()
    assert body["configured"] is True and body["repositories"] == [REPO]
    assert "private_key" not in body and "webhook_secret" not in body
    api.cookies.clear()
    assert (await api.get("/github/app")).status_code == 401
    assert (await api.put("/github/app", json=CONFIG)).status_code == 401
