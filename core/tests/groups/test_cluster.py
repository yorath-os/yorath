"""Cluster (change cluster-nodes, decision 35, criterion 14): nodes by token, placement, routes.

There is no WireGuard tunnel here: the worker node gateway is a third-party stub (``Backends``);
the channel was checked on a lab setup of real nodes.
"""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from tests.fixtures.corpus import AGENTS_DIR
from tests.fixtures.world import seed_chat
from yorath.control.cluster import service as cluster
from yorath.control.journal import AuditEvent, AuditEventType
from yorath.control.model import (
    ClusterJoinToken,
    Instance,
    InstanceStatus,
    ModelResource,
    Node,
)
from yorath.control.setup.hardware import HardwareInfo
from yorath.control.setup.inventory import upsert_node
from yorath.enforcement.secrets import DbSecretStore

pytestmark = pytest.mark.group

MAIN_KEY = "M" * 43 + "="
KEY_2, KEY_3, KEY_4 = ("A" * 43 + "=", "B" * 43 + "=", "C" * 43 + "=")


def join_body(token: str, name: str, key: str, gpu: int = 0) -> dict:
    return {"token": token, "name": name, "wg_public_key": key, "cpu_count": 4, "ram_gb": 8,
            "gpu_count": gpu}  # fmt: skip


@pytest.fixture
async def world(make_app, session, backends, tmp_path):
    report = tmp_path / "report"
    report.mkdir()
    (report / "host.json").write_text(json.dumps({"egress": {"mode": "on", "enforced": True}}))
    owner, app = await seed_chat(session, make_app, tmp_path, agents_dir=AGENTS_DIR)
    app.state.settings.secret_key = Fernet.generate_key().decode()
    app.state.secrets = DbSecretStore(app.state.sessions, app.state.settings.secret_key)
    app.state.settings.host_report_dir = report
    app.state.settings.node_token = "main-node-token"
    app.state.settings.cluster_dir = tmp_path / "cluster"
    await session.execute(
        update(ModelResource)
        .where(ModelResource.name == "local/default")
        .values(context_length=65536)
    )
    await session.commit()
    await upsert_node(session, "main", HardwareInfo(vcpu=4, ram_gb=8, disk_free_gb=40))
    out = await cluster.issue_token(
        session, None, "203.0.113.5:51820", MAIN_KEY, "-", "https://main"
    )

    def client(key: str | None = None) -> AsyncClient:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=headers
        )

    w = SimpleNamespace(app=app, owner=owner, client=client, token=out.token, anon=client(),
                        peers=tmp_path / "cluster" / "peers.conf")  # fmt: skip
    yield w
    await owner.aclose()
    await w.anon.aclose()


async def new_token(w) -> str:
    return (await w.owner.post("/cluster/tokens")).json()["token"]


async def joined(
    w, name: str = "node-2", key: str = KEY_2, approve: bool = True, gpu: int = 0
) -> dict:
    r = await w.anon.post("/cluster/join", json=join_body(await new_token(w), name, key, gpu))
    assert r.status_code == 200, r.text
    if approve:
        assert (await w.owner.post(f"/cluster/nodes/{name}/approve")).status_code == 200
    return r.json()


@pytest.mark.covers("INV-CLU-1")
async def test_join_command_has_main_url_and_absolute_path(session):
    out = await cluster.issue_token(
        session, None, "203.0.113.5:51820", MAIN_KEY, "-", "https://203.0.113.5"
    )
    assert (
        out.command == f"sh /opt/yorath/installer/install.sh --join https://203.0.113.5 {out.token}"
    )
    bare = await cluster.issue_token(session, None, "203.0.113.5:51820", MAIN_KEY, "-", "")
    assert bare.command == (
        f"sh /opt/yorath/installer/install.sh --join https://<main node address> {bare.token}"
    )


async def test_join_pending_until_approved_and_token_single_use(world, session):
    r = await world.anon.post("/cluster/join", json=join_body(world.token, "node-2", KEY_2))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["overlay_ip"] == "10.88.0.2" and out["main_public_key"] == MAIN_KEY
    assert out["endpoint"] == "203.0.113.5:51820" and out["status"] == "pending"
    assert out["node_token"].startswith("yorath_node_")
    node = world.client(out["node_token"])
    assert (await node.get("/node/desired")).status_code == 403  # waiting for approval
    again = await world.anon.post("/cluster/join", json=join_body(world.token, "node-x", KEY_3))
    assert again.status_code == 403 and again.json()["detail"]["code"] == "join_token_used"
    assert not world.peers.exists()  # no peer until the owner approves
    r = await world.owner.post("/cluster/nodes/node-2/approve")
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert (await node.get("/node/desired")).status_code == 200
    assert f"PublicKey = {KEY_2}\nAllowedIPs = 10.88.0.2/32" in world.peers.read_text()
    events = [e.event_type for e in (await session.scalars(select(AuditEvent))).all()]
    assert AuditEventType.NODE_JOINED in events and AuditEventType.NODE_APPROVED in events
    await node.aclose()


async def test_two_simultaneous_joins_with_one_token_make_one_node(world, session):
    # the token is claimed by a conditional update: of two joins at once only one passes
    one, two = await asyncio.gather(
        world.anon.post("/cluster/join", json=join_body(world.token, "node-2", KEY_2)),
        world.anon.post("/cluster/join", json=join_body(world.token, "node-3", KEY_3)),
    )
    assert sorted([one.status_code, two.status_code]) == [200, 403]
    refused = one if one.status_code == 403 else two
    assert refused.json()["detail"]["code"] == "join_token_used"
    workers = (await session.scalars(select(Node).where(Node.role == "worker"))).all()
    assert len(workers) == 1


async def test_a_refusal_after_the_claim_leaves_the_token_free(world, session):
    await joined(world, "node-2", KEY_2)
    token = await new_token(world)
    taken = await world.anon.post("/cluster/join", json=join_body(token, "node-2", KEY_3))
    assert taken.status_code == 409 and taken.json()["detail"]["code"] == "node_exists"
    r = await world.anon.post("/cluster/join", json=join_body(token, "node-3", KEY_3))
    assert r.status_code == 200, r.text  # the same token joins under another name


async def test_expired_and_unknown_tokens(world, session):
    await session.execute(
        update(ClusterJoinToken).values(expires_at=cluster.now() - timedelta(minutes=1))
    )
    await session.commit()
    r = await world.anon.post("/cluster/join", json=join_body(world.token, "node-2", KEY_2))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "join_token_expired"
    r = await world.anon.post(
        "/cluster/join", json=join_body("yorath_join_nope.-", "node-2", KEY_2)
    )
    assert r.status_code == 403 and r.json()["detail"]["code"] == "join_token_invalid"
    assert (await world.client().post("/cluster/tokens")).status_code == 401  # not the owner


@pytest.mark.covers("INV-CLU-3")
async def test_community_limit_counts_main_and_gpus(world):
    await joined(world, "node-2", KEY_2)
    r = await world.anon.post(
        "/cluster/join", json=join_body(await new_token(world), "gpu-1", KEY_3, gpu=2)
    )
    assert r.status_code == 403 and r.json()["detail"]["code"] == "community_limit"
    assert "Enterprise" in r.json()["detail"]["message"]
    await joined(world, "node-3", KEY_3)  # main + 2 nodes = 3 units
    r = await world.anon.post(
        "/cluster/join", json=join_body(await new_token(world), "node-4", KEY_4)
    )
    assert r.status_code == 403 and r.json()["detail"]["code"] == "community_limit"


@pytest.mark.covers("INV-CLU-1")
async def test_revoked_node_loses_token_peer_and_agents(world, session, backends):
    out = await joined(world)
    node = world.client(out["node_token"])
    r = await world.owner.post("/agents/connected/openclaw", json={"node": "node-2"})
    assert r.status_code == 200 and r.json()["node"] == "node-2"
    [item] = (await node.get("/node/desired")).json()["agents"]
    rep = {"instances": [{"deployment_id": item["deployment_id"], "state": "running"}]}
    assert (await node.post("/node/report", json=rep)).status_code == 204
    assert (await world.owner.post("/cluster/nodes/node-2/revoke")).status_code == 200
    assert (await node.get("/node/desired")).status_code == 401
    assert "PublicKey" not in world.peers.read_text()
    inst = await session.scalar(select(Instance))
    await session.refresh(inst)
    assert inst.status is InstanceStatus.LOST
    plain = (await world.owner.post("/api-keys", json={"name": "k"})).json()["key"]
    user = world.client(plain)
    r = await user.post("/v1/chat/completions", json={
        "model": "agent/openclaw", "messages": [{"role": "user", "content": "hello"}]})  # fmt: skip
    assert r.status_code == 503 and r.json()["error"]["code"] == "agent_unavailable"
    assert backends.connected == []
    for c in (node, user):
        await c.aclose()


@pytest.mark.covers("INV-CLU-2")
async def test_each_node_gets_only_its_deployments(world, session):
    two = await joined(world, "node-2", KEY_2)
    three = await joined(world, "node-3", KEY_3)
    await world.owner.post("/agents/connected/openclaw", json={"node": "node-2"})
    await world.owner.post("/agents/connected/hermes", json={})  # main node
    n2, n3, main = (
        world.client(two["node_token"]),
        world.client(three["node_token"]),
        world.client("main-node-token"),
    )
    assert [a["agent"] for a in (await n2.get("/node/desired")).json()["agents"]] == ["openclaw"]
    assert (await n3.get("/node/desired")).json()["agents"] == []
    [hermes] = (await main.get("/node/desired")).json()["agents"]
    assert hermes["agent"] == "hermes"
    # node 3 cannot report for node 2's Deployment
    [oc] = (await n2.get("/node/desired")).json()["agents"]
    rep = {"instances": [{"deployment_id": oc["deployment_id"], "state": "failed", "detail": "x"}]}
    assert (await n3.post("/node/report", json=rep)).status_code == 204
    assert await session.scalar(select(Instance)) is None
    for c in (n2, n3, main):
        await c.aclose()


async def test_lost_node_and_back(world, session):
    out = await joined(world)
    node = world.client(out["node_token"])
    await world.owner.post("/agents/connected/openclaw", json={"node": "node-2"})
    [item] = (await node.get("/node/desired")).json()["agents"]
    await node.post(
        "/node/report",
        json={"instances": [{"deployment_id": item["deployment_id"], "state": "running"}]},
    )
    await session.execute(
        update(Node)
        .where(Node.name == "node-2")
        .values(last_seen_at=cluster.now() - timedelta(minutes=2))
    )
    await session.commit()
    assert await cluster.sweep_lost(session, world.app.state.journal) == ["node-2"]
    row = next(
        a for a in (await world.owner.get("/agents/connected")).json() if a["name"] == "openclaw"
    )
    assert row["status"] == "lost"
    await node.post(
        "/node/report",
        json={"instances": [{"deployment_id": item["deployment_id"], "state": "running"}]},
    )
    n = await session.scalar(select(Node).where(Node.name == "node-2"))
    await session.refresh(n)
    assert n.status == "active"
    events = [e.event_type for e in (await session.scalars(select(AuditEvent))).all()]
    assert AuditEventType.NODE_LOST in events and AuditEventType.NODE_BACK in events
    await node.aclose()


async def test_model_and_agent_on_node_are_called_through_its_gateway(world, session, backends):
    out = await joined(world, gpu=1)
    # the model is placed on the node; local/default now points at it (the catalog itself is
    # covered in test_model_placements.py)
    r = await world.owner.post(
        "/cluster/models/place", json={"model": "fake-chat", "node": "node-2"}
    )
    assert r.status_code == 200 and '"status": "placed"' in r.text, r.text
    await world.owner.post("/agents/connected/openclaw", json={"node": "node-2"})
    plain = (await world.owner.post("/api-keys", json={"name": "k"})).json()["key"]
    user = world.client(plain)
    r = await user.post("/v1/chat/completions", json={"model": "local/default",
                        "messages": [{"role": "user", "content": "hello"}]})  # fmt: skip
    assert r.status_code == 200, r.text
    assert "10.88.0.2 /ollama/gpu0/api/chat" in backends.node_gateway
    r = await user.post("/v1/chat/completions", json={"model": "agent/openclaw",
                        "messages": [{"role": "user", "content": "Capital?"}]})  # fmt: skip
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "Paris"
    assert "10.88.0.2 /agents/172.31.0.10:18789/v1/chat/completions" in backends.node_gateway
    # node revoked: the model is unavailable with a reason, no switch to another model
    await world.owner.post("/cluster/nodes/node-2/revoke")
    calls = len(backends.chat)
    r = await user.post("/v1/chat/completions", json={"model": "local/default",
                        "messages": [{"role": "user", "content": "hello"}]})  # fmt: skip
    assert r.status_code == 502 and r.json()["error"]["code"] == "model_unavailable"
    assert len(backends.chat) == calls
    await user.aclose()
    assert out["overlay_ip"] == "10.88.0.2"


async def test_node_egress_lines_land_under_node_and_agent(world, session):
    out = await joined(world)
    node = world.client(out["node_token"])
    t = cluster.now().timestamp()
    lines = [
        f"{t:.3f} 172.31.0.10 TCP_DENIED/0 CONNECT 8.6.112.0:443 example.com - 0",
        f"{t:.3f} 172.31.0.10 TCP_TUNNEL/200 CONNECT api.telegram.org:443 api.telegram.org 1.2.3.4 900",
    ]
    assert (await node.post("/node/egress", json={"lines": lines})).status_code == 204
    flows = (await world.owner.get("/journal/egress?hours=1")).json()
    got = {(f["node"], f["source"], f["domain"], f["blocked"]) for f in flows}
    assert ("node-2", "openclaw", "example.com", 1) in got
    assert ("node-2", "openclaw", "api.telegram.org", 0) in got
    await node.aclose()


# --- what a node may send (pre-public-hardening S6) ------------------------------------------


async def test_a_node_request_over_a_megabyte_is_refused_before_the_route(world, session):
    out = await joined(world)
    node = world.client(out["node_token"])
    big = {"instances": [], "host": {"checked_at": "x", "agents": [], "pad": "x" * 1_100_000}}
    r = await node.post("/node/report", json=big)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "payload_too_large"
    row = await session.scalar(select(Node).where(Node.name == "node-2"))
    await session.refresh(row)
    assert row.host_report is None
    assert (await node.post("/node/report", json={"instances": []})).status_code == 204  # anchor
    await node.aclose()


async def test_a_flood_of_denials_fills_the_summary_not_the_audit(world, session):
    out = await joined(world)
    node = world.client(out["node_token"])
    t = cluster.now().timestamp()
    deny = f"{t:.3f} 172.31.0.10 TCP_DENIED/0 CONNECT 8.6.112.0:443 example.com - 0"
    too_many = await node.post("/node/egress", json={"lines": [deny] * 5001})
    assert too_many.status_code == 422, "more lines than the node service ever sends"
    assert (await node.post("/node/egress", json={"lines": [deny] * 600})).status_code == 204
    flows = (await world.owner.get("/journal/egress?hours=1")).json()
    [flow] = [f for f in flows if f["domain"] == "example.com"]
    assert (flow["requests"], flow["blocked"]) == (600, 600)
    blocked = (
        await session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == AuditEventType.EGRESS_BLOCKED)
        )
    ).all()
    assert len(blocked) == 500
    await node.aclose()


async def test_a_heartbeat_cannot_add_gpus_over_the_community_limit(world, session):
    out = await joined(world)  # main 1 + node-2 1 = 2 of 3 units
    node = world.client(out["node_token"])
    hw = {"gpu_count": 8, "gpu": "NVIDIA A10", "vram_gb": 23}
    assert (
        await node.post("/node/report", json={"instances": [], "hardware": hw})
    ).status_code == 204
    row = await session.scalar(select(Node).where(Node.name == "node-2"))
    await session.refresh(row)
    assert row.gpu_count == 0
    [event] = (
        await session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == AuditEventType.NODE_OVER_LIMIT)
        )
    ).all()
    assert event.details["gpu_count"] == 8 and event.details["limit"] == 3
    # anchor: two cards fit (main 1 + node-2 2 = 3 units)
    hw["gpu_count"] = 2
    await node.post("/node/report", json={"instances": [], "hardware": hw})
    await session.refresh(row)
    assert row.gpu_count == 2
    await node.aclose()
