"""Connected agents (change connected-agents, decision 34, criteria 13 and 15).

There are no containers here: the node service is tested on a fake Docker (``tests/unit/test_node_agent``),
the agent entry is a third-party stub (``Backends._connected``).
"""

import json
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from tests.fixtures.corpus import AGENTS_DIR
from tests.fixtures.world import ask, event, seed_chat
from yorath.control.journal import AuditEvent, AuditEventType
from yorath.control.model import (
    Agent,
    AgentRevision,
    ApiKey,
    Deployment,
    Execution,
    ExecutionStatus,
    Instance,
    InstanceStatus,
    ModelResource,
    ModelServer,
    Node,
    RuntimeInstallation,
    ServiceAccount,
    User,
)
from yorath.control.setup.hardware import HardwareInfo
from yorath.control.setup.inventory import upsert_node

pytestmark = pytest.mark.group

NODE_TOKEN = "node-token-for-tests"
BOT = "123456:telegram-bot-token-AAA"
EGRESS_ON = {"egress": {"mode": "on", "enforced": True, "alarm": None}}


def report(tmp_path, data) -> None:
    (tmp_path / "report").mkdir(exist_ok=True)
    (tmp_path / "report" / "host.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
async def world(make_app, session, backends, tmp_path):
    """Owner, a model with a 65,536 window, network control on; member anna with a model key."""
    report(tmp_path, EGRESS_ON)
    owner, app = await seed_chat(session, make_app, tmp_path, agents_dir=AGENTS_DIR)
    app.state.settings.secret_key = Fernet.generate_key().decode()
    from yorath.enforcement.secrets import DbSecretStore

    app.state.secrets = DbSecretStore(app.state.sessions, app.state.settings.secret_key)
    app.state.settings.host_report_dir = tmp_path / "report"
    app.state.settings.node_token = NODE_TOKEN
    await session.execute(
        update(ModelResource)
        .where(ModelResource.name == "local/default")
        .values(context_length=65536)
    )
    await session.commit()
    await upsert_node(session, "local", HardwareInfo(vcpu=4, ram_gb=8, disk_free_gb=40))
    body = {"login": "anna", "password": "password-1", "display_name": "anna"}
    assert (await owner.post("/users", json=body)).status_code == 201
    member = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await member.post("/auth/login", json={"login": "anna", "password": "password-1"})
    plain = (await member.post("/api-keys", json={"name": "laptop"})).json()

    def client(key: str) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
            headers={"Authorization": f"Bearer {key}"},
        )  # fmt: skip

    w = SimpleNamespace(app=app, owner=owner, member=member, client=client, plain=client(plain["key"]),
                        node=client(NODE_TOKEN))  # fmt: skip
    backends.reply = ["model-answer"]
    yield w
    for c in (owner, member, w.plain, w.node):
        await c.aclose()


async def desired(w) -> dict:
    r = await w.node.get("/node/desired")
    assert r.status_code == 200, r.text
    return r.json()


async def agent_key(w, name: str = "openclaw") -> str:
    """The agent's model key as the container gets it: from the node's desired state."""
    item = next(a for a in (await desired(w))["agents"] if a["agent"] == name)
    return item["config"]["env"].get("YORATH_API_KEY") or item["config"]["dotenv"]["YORATH_API_KEY"]


def model_call(model: str = "local/default") -> dict:
    return {"model": model, "messages": [{"role": "user", "content": "hello"}]}


async def test_catalog_is_registered_disabled_with_gateway_level(world, session):
    rows = {a["name"]: a for a in (await world.owner.get("/agents/connected")).json()}
    assert set(rows) == {"openclaw", "hermes"}
    assert rows["openclaw"]["tested_version"] == "2026.11.3" and not rows["openclaw"]["connected"]
    agents = {a["name"]: a for a in (await world.owner.get("/agents")).json()}
    assert agents["openclaw"]["enabled"] is False
    assert agents["openclaw"]["control_level"] == "gateway"
    assert agents["hermes"]["control_level"] == "gateway"
    assert (await desired(world))["agents"] == []


@pytest.mark.covers("INV-CON-3")
async def test_connect_issues_agent_key_and_hides_secrets(world, session):
    r = await world.owner.post("/agents/connected/openclaw", json={"telegram_token": BOT})
    assert r.status_code == 200, r.text
    assert r.json()["connected"] and r.json()["telegram"] and r.json()["version"] is None
    assert BOT not in r.text
    agent = await session.scalar(select(Agent).where(Agent.name == "openclaw"))
    assert agent.enabled
    [key] = (await session.scalars(select(ApiKey).where(ApiKey.agent_id == agent.id))).all()
    assert key.scope == "agent_self" and key.name == "agent:openclaw"
    dep = await session.scalar(
        select(Deployment).join(AgentRevision).where(AgentRevision.agent_id == agent.id)
    )
    assert dep.desired_instances == 1 and dep.cluster_id is not None
    assert dep.channel_hash and BOT not in dep.channel_hash
    # secrets are not in the audit log or the key list
    audit = (await session.scalars(select(AuditEvent))).all()
    assert all(BOT not in json.dumps(e.details, ensure_ascii=False) for e in audit)
    connected = [e for e in audit if e.event_type is AuditEventType.AGENT_CONNECTED]
    assert connected[0].details == {"agent": "openclaw", "version": "2026.11.3", "untested": False,
                                    "telegram": True}  # fmt: skip
    token = await agent_key(world)
    assert token.startswith(key.prefix)
    for path in ("/api-keys", "/agents/connected", "/agents"):
        assert token not in (await world.owner.get(path)).text
    # node desired state: the verified version image with a digest, the address, the bot in env
    [item] = (await desired(world))["agents"]
    assert item["state"] == "running" and item["address"] == "172.31.0.10"
    assert item["image"].startswith("ghcr.io/openclaw/openclaw:2026.11.3@sha256:")
    assert item["config"]["env"]["TELEGRAM_BOT_TOKEN"] == BOT


async def test_connect_refusals(world, session, tmp_path):
    r = await world.owner.post("/agents/connected/pagey-bot", json={})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "unknown_connected_agent"
    assert (await world.member.post("/agents/connected/openclaw", json={})).status_code == 403
    report(tmp_path, {"egress": {"mode": "off", "enforced": False}})
    r = await world.owner.post("/agents/connected/hermes", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "egress_off"
    assert "yorath egress on" in r.json()["detail"]["message"]
    report(tmp_path, EGRESS_ON)
    await session.execute(
        update(ModelResource)
        .where(ModelResource.name == "local/default")
        .values(context_length=32768)
    )
    await session.commit()
    r = await world.owner.post("/agents/connected/hermes", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "context_too_small"
    assert "Use a node with a GPU" in r.json()["detail"]["message"]
    # a GPU node is in the cluster: the refusal names its server and the way there
    local = await session.scalar(select(Node).where(Node.name == "local"))
    gpu_node = Node(
        cluster_id=local.cluster_id, name="node-2", role="worker", status="active",
        cpu_count=18, ram_gb=31, gpu="NVIDIA A10", vram_gb=22, gpu_count=1,
    )  # fmt: skip
    session.add(gpu_node)
    await session.flush()
    session.add(ModelServer(node_id=gpu_node.id, name="gpu0", gpu_indexes=[0]))
    await session.commit()
    r = await world.owner.post("/agents/connected/hermes", json={})
    message = r.json()["detail"]["message"]
    assert (
        r.status_code == 409 and "node-2/gpu0" in message and "Models → Install a model" in message
    )
    hermes = await session.scalar(select(Agent).where(Agent.name == "hermes"))
    await session.refresh(hermes)
    assert hermes.enabled is False
    assert await session.scalar(select(ApiKey).where(ApiKey.agent_id == hermes.id)) is None


@pytest.mark.covers("INV-CON-1")
async def test_agent_key_calls_model_as_agent_and_disabled_agent_is_denied(
    world, session, backends
):
    await world.owner.post("/agents/connected/openclaw", json={})
    bot = world.client(await agent_key(world))
    r = await bot.post("/v1/chat/completions", json=model_call())
    assert r.status_code == 200, r.text
    agent = await session.scalar(select(Agent).where(Agent.name == "openclaw"))
    svc = await session.scalar(select(ServiceAccount).where(ServiceAccount.agent_id == agent.id))
    owner = await session.scalar(select(User).where(User.role == "owner"))
    [ex] = (await session.scalars(
        select(Execution).join(AgentRevision).where(AgentRevision.agent_id == agent.id)
    )).all()  # fmt: skip
    assert ex.status is ExecutionStatus.SUCCEEDED
    assert (
        ex.initiated_by_principal_id == svc.principal_id and ex.effective_principal_id == owner.id
    )
    models = (await bot.get("/v1/models")).json()["data"]
    assert [m["id"] for m in models] == ["local/default"]
    # another model and agent calls are denied
    assert (
        await bot.post("/v1/chat/completions", json=model_call("openrouter/x"))
    ).status_code == 403
    r = await bot.post("/v1/chat/completions", json=model_call("agent/hermes"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "model_not_allowed"
    # disabled: the next model call is denied before the model, but with a denied Execution
    calls = len(backends.chat)
    assert (await world.owner.patch("/agents/openclaw", json={"enabled": False})).status_code == 200
    r = await bot.post("/v1/chat/completions", json=model_call())
    assert r.status_code in (401, 403)
    assert len(backends.chat) == calls


@pytest.mark.covers("INV-CON-1")
async def test_disabled_agent_with_live_key_gets_denied_execution(world, session, backends):
    """The agent was disabled in the database before the key was revoked: still denied before the model."""
    await world.owner.post("/agents/connected/openclaw", json={})
    bot = world.client(await agent_key(world))
    await session.execute(update(Agent).where(Agent.name == "openclaw").values(enabled=False))
    await session.commit()
    calls = len(backends.chat)
    r = await bot.post("/v1/chat/completions", json=model_call())
    assert r.status_code == 403 and r.json()["error"]["code"] == "agent_disabled"
    assert len(backends.chat) == calls
    agent = await session.scalar(select(Agent).where(Agent.name == "openclaw"))
    [ex] = (await session.scalars(
        select(Execution).join(AgentRevision).where(AgentRevision.agent_id == agent.id)
    )).all()  # fmt: skip
    assert ex.status is ExecutionStatus.DENIED


async def test_disconnect_revokes_and_reconnect_issues_new_key(world, session):
    await world.owner.post("/agents/connected/hermes", json={})
    first = await agent_key(world, "hermes")
    r = await world.owner.delete("/agents/connected/hermes")
    assert r.status_code == 200 and not r.json()["connected"]
    [item] = (await desired(world))["agents"]
    assert item["state"] == "stopped"
    assert (
        await world.client(first).post("/v1/chat/completions", json=model_call())
    ).status_code == 401
    await world.owner.post("/agents/connected/hermes", json={})
    second = await agent_key(world, "hermes")
    assert second != first
    assert (
        await world.client(second).post("/v1/chat/completions", json=model_call())
    ).status_code == 200


async def test_remove_purges_after_node_reports(world, session):
    await world.owner.post("/agents/connected/hermes", json={"telegram_token": BOT})
    assert (await world.owner.delete("/agents/connected/hermes?purge=true")).status_code == 200
    [item] = (await desired(world))["agents"]
    assert item["state"] == "purge"
    r = await world.owner.post("/agents/connected/hermes", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "agent_removing"
    done = {"instances": [{"deployment_id": item["deployment_id"], "state": "purged"}]}
    assert (await world.node.post("/node/report", json=done)).status_code == 204
    r = await world.owner.post("/agents/connected/hermes", json={})
    assert r.status_code == 200 and r.json()["telegram"] is False  # from a clean slate


@pytest.mark.covers("INV-CON-4")
async def test_one_bot_one_agent_and_bot_change(world, session):
    await world.owner.post("/agents/connected/openclaw", json={"telegram_token": BOT})
    await world.owner.post("/agents/connected/hermes", json={})
    r = await world.owner.put("/agents/connected/hermes/channel", json={"telegram_token": BOT})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "bot_in_use"
    assert "openclaw" in r.json()["detail"]["message"]
    other = "777:another-bot"
    r = await world.owner.put(
        "/agents/connected/hermes/channel", json={"telegram_token": other, "telegram_users": ["42"]}
    )
    assert r.status_code == 200 and r.json()["telegram_users"] == ["42"]
    hermes = next(a for a in (await desired(world))["agents"] if a["agent"] == "hermes")
    assert hermes["config"]["dotenv"]["TELEGRAM_BOT_TOKEN"] == other
    before = hermes["config_sha"]
    r = await world.owner.put("/agents/connected/hermes/channel", json={"telegram_token": None})
    assert r.status_code == 200 and r.json()["telegram"] is False
    hermes = next(a for a in (await desired(world))["agents"] if a["agent"] == "hermes")
    assert hermes["config"]["dotenv"]["TELEGRAM_BOT_TOKEN"] == "" and hermes["config_sha"] != before
    changed = (await session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == AuditEventType.AGENT_CHANNEL_CHANGED)
    )).all()  # fmt: skip
    assert len(changed) == 2 and all(other not in json.dumps(e.details) for e in changed)


async def test_version_choice_and_node_report(world, session):
    await world.owner.post("/agents/connected/openclaw", json={"version": "latest"})
    [item] = (await desired(world))["agents"]
    assert item["image"] == "ghcr.io/openclaw/openclaw:latest"
    dep = item["deployment_id"]
    rep = {"instances": [{"deployment_id": dep, "state": "running", "container_id": "abc",
                          "version": "2026.11.9", "digest": "sha256:" + "e" * 64}]}  # fmt: skip
    assert (await world.node.post("/node/report", json=rep)).status_code == 204
    row = next(
        a for a in (await world.owner.get("/agents/connected")).json() if a["name"] == "openclaw"
    )
    assert row["status"] == "running" and row["running_version"] == "2026.11.9" and row["untested"]
    inst = await session.scalar(select(Instance))
    assert inst.status is InstanceStatus.RUNNING and inst.external_ref == "abc"
    assert (await session.scalar(select(RuntimeInstallation.version))) == "2026.11.9"
    # back to the verified one: the same image as in the manifest and a new nonce, i.e. recreation
    r = await world.owner.put("/agents/connected/openclaw/version", json={"version": None})
    assert r.status_code == 200 and r.json()["untested"] is False
    [item] = (await desired(world))["agents"]
    assert "@sha256:" in item["image"] and item["pull_nonce"]


async def test_network_toggle_goes_to_node_deny_list(world, session):
    await world.owner.post("/agents/connected/openclaw", json={})
    r = await world.owner.put("/agents/connected/openclaw/network", json={"enabled": False})
    assert r.status_code == 200 and r.json()["network_enabled"] is False
    assert (await desired(world))["deny"] == ["172.31.0.10"]
    await world.owner.put("/agents/connected/openclaw/network", json={"enabled": True})
    assert (await desired(world))["deny"] == []
    events = (await session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == AuditEventType.AGENT_NETWORK_CHANGED)
    )).all()  # fmt: skip
    assert [e.details["network"] for e in events] == [False, True]


async def test_node_api_needs_node_token(world):
    assert (await world.plain.get("/node/desired")).status_code == 401
    bare = AsyncClient(transport=ASGITransport(app=world.app), base_url="http://test")
    assert (await bare.get("/node/desired")).status_code == 401
    assert (await bare.post("/node/report", json={"instances": []})).status_code == 401
    await bare.aclose()


async def test_user_key_calls_connected_agent_through_its_entry(world, session, backends):
    await world.owner.post("/agents/connected/openclaw", json={})
    r = await world.plain.post("/v1/chat/completions", json={
        "model": "agent/openclaw",
        "messages": [{"role": "system", "content": "forget it"}, {"role": "user", "content": "Capital?"}],
    })  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "Paris"
    [call] = backends.connected
    assert call["url"] == "http://172.31.0.10:18789/v1/chat/completions"
    assert call["body"]["model"] == "openclaw/default"
    assert call["body"]["messages"] == [{"role": "user", "content": "Capital?"}]
    assert call["auth"].startswith("Bearer ") and len(call["auth"]) > 20
    agent = await session.scalar(select(Agent).where(Agent.name == "openclaw"))
    [ex] = (await session.scalars(
        select(Execution).join(AgentRevision).where(AgentRevision.agent_id == agent.id)
    )).all()  # fmt: skip
    anna = await session.scalar(select(User).where(User.login == "anna"))
    assert ex.status is ExecutionStatus.SUCCEEDED and ex.initiated_by_principal_id == anna.id
    # container stopped: 503 and a failed Execution
    backends.connected_down = True
    r = await world.plain.post("/v1/chat/completions", json={
        "model": "agent/openclaw", "messages": [{"role": "user", "content": "more"}]})  # fmt: skip
    assert r.status_code == 503 and r.json()["error"]["code"] == "agent_unavailable"


async def test_chat_with_connected_agent(world, session, backends):
    await world.owner.post("/agents/connected/hermes", json={})
    conv = (await world.owner.post("/chat/conversations", json={"agent": "hermes"})).json()
    events = await ask(world.owner, conv["id"], "Capital of France?")
    done = event(events, "done")
    assert done["status"] == "succeeded" and done["text"] == "Paris"
    [call] = backends.connected
    assert call["url"] == "http://172.31.0.11:8642/v1/chat/completions"
    assert call["body"]["model"] == "hermes-agent"


async def test_the_bot_check_of_the_agents_node_reaches_the_card(world, session, tmp_path):
    """Change telegram-check: the node service pass asks Telegram, the card shows what it got."""
    import httpx

    from tests.unit.test_node_agent import FakeDocker
    from yorath.runtime.connected.node_agent import NodeSettings, one_pass
    from yorath.runtime.connected.telegram_check import TelegramChecks

    answers = [httpx.Response(503)]  # the node proxy could not reach Telegram

    def handler(request: httpx.Request) -> httpx.Response:
        return answers[-1]

    checks = TelegramChecks(transport=httpx.MockTransport(handler))
    manifest = world.app.state.manifest
    settings = NodeSettings(token="t", deny_file=tmp_path / "deny", host_report_path=tmp_path / "x")
    docker = FakeDocker()

    async def card() -> dict:
        rows = (await world.owner.get("/agents/connected")).json()
        return next(a for a in rows if a["name"] == "hermes")

    r = await world.owner.post("/agents/connected/hermes", json={"telegram_token": BOT})
    assert r.status_code == 200, r.text
    assert (await card())["telegram_check"] is None, "no result before the node's pass"
    await one_pass(world.node, docker, manifest, settings, checks)
    check = (await card())["telegram_check"]
    assert (check["state"], check["detail"]) == ("unreachable", "HTTP 503")
    assert BOT not in json.dumps(await card())

    # Check again: the old result is dropped, the next pass asks Telegram again
    answers.append(httpx.Response(200, json={"ok": True, "result": {"username": "yorath_bot"}}))
    r = await world.owner.post("/agents/connected/hermes/telegram/check")
    assert r.status_code == 200 and r.json()["telegram_check"] is None
    await one_pass(world.node, docker, manifest, settings, checks)
    check = (await card())["telegram_check"]
    assert (check["state"], check["bot"]) == ("ok", "yorath_bot")

    # internet off: no question to Telegram, the card says why
    await world.owner.put("/agents/connected/hermes/network", json={"enabled": False})
    await one_pass(world.node, docker, manifest, settings, checks)
    assert (await card())["telegram_check"]["state"] == "internet_off"

    # a new token drops the result at once: "ok" must not stand for a bot nobody checked
    r = await world.owner.put("/agents/connected/hermes/channel", json={"telegram_token": "77:x"})
    assert r.status_code == 200 and r.json()["telegram_check"] is None

    assert (await world.member.post("/agents/connected/hermes/telegram/check")).status_code == 403
    await world.owner.put("/agents/connected/hermes/channel", json={"telegram_token": None})
    r = await world.owner.post("/agents/connected/hermes/telegram/check")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_bot"
