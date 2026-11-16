"""Agents outside Yorath on every node (decision 36, change outside-agents-cluster).

A worker node sends its ``yorath scan`` report with the heartbeat; the main node keeps the last one and
lists the agents of all nodes. The main node's own report is the file the core reads, as before.
"""

import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fixtures.corpus import AGENTS_DIR, OWNER
from yorath.control.agents.loader import sync_agents_dir
from yorath.control.cluster import service as cluster
from yorath.control.setup.hardware import HardwareInfo
from yorath.control.setup.inventory import upsert_node
from yorath.runtime.connected.node_agent import NodeSettings, one_pass

pytestmark = pytest.mark.group

MAIN_KEY = "M" * 43 + "="
KEY_2 = "A" * 43 + "="
KEY_3 = "B" * 43 + "="
MEMBER = {
    "login": "jordan",
    "display_name": "Jordan",
    "password": "member-pass-1",
    "role": "member",
}
OPENCLAW_ON_NODE = {
    "checked_at": "2026-11-15T18:40:44+00:00",
    "agents": [
        {
            "agent": "openclaw",
            "where": "host",
            "pid": 18661,
            "user": "root",
            "root": True,
            "command": "node",
        },
    ],
    "traces": [{"agent": "openclaw", "path": "/root/.openclaw", "owner": "root"}],
    "containers": [],
}


@pytest.fixture
async def world(make_app, session, tmp_path):
    app = make_app(agents_dir=AGENTS_DIR)
    app.state.settings.cluster_dir = tmp_path / "cluster"
    app.state.settings.host_report_dir = tmp_path / "host-report"
    owner = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await owner.post("/auth/setup", json=OWNER)
    await sync_agents_dir(session, AGENTS_DIR)
    await upsert_node(session, "main", HardwareInfo(vcpu=8, ram_gb=16, disk_free_gb=90))
    await cluster.issue_token(session, None, "203.0.113.5:51820", MAIN_KEY, "-", "https://main")
    token = (await owner.post("/cluster/tokens")).json()["token"]
    body = {"token": token, "name": "vm13234", "wg_public_key": KEY_2, "cpu_count": 18, "ram_gb": 31,
            "gpu_count": 1, "gpu": "NVIDIA A10", "vram_gb": 22}  # fmt: skip
    joined = (await anon.post("/cluster/join", json=body)).json()
    assert (await owner.post("/cluster/nodes/vm13234/approve")).status_code == 200
    node = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {joined['node_token']}"},
    )  # fmt: skip
    yield SimpleNamespace(app=app, owner=owner, node=node, report_dir=tmp_path / "host-report")
    for c in (owner, anon, node):
        await c.aclose()


async def second_node(w, name: str, key: str) -> AsyncClient:
    token = (await w.owner.post("/cluster/tokens")).json()["token"]
    body = {"token": token, "name": name, "wg_public_key": key, "cpu_count": 4, "ram_gb": 8,
            "gpu_count": 0}  # fmt: skip
    joined = (await w.owner.post("/cluster/join", json=body)).json()
    assert (await w.owner.post(f"/cluster/nodes/{name}/approve")).status_code == 200
    return AsyncClient(
        transport=ASGITransport(app=w.app), base_url="http://test",
        headers={"Authorization": f"Bearer {joined['node_token']}"},
    )  # fmt: skip


class NoContainers:
    """Docker of a node without connected agents: the pass reconciles nothing."""

    def labeled(self):
        return []

    def reconfigure_proxy(self):
        pass


async def outside(w) -> dict[str, dict]:
    r = await w.owner.get("/cluster/outside-agents")
    assert r.status_code == 200, r.text
    return {n["node"]: n for n in r.json()["nodes"]}


async def test_a_node_reports_its_scan_and_the_main_node_lists_it(world):
    before = await outside(world)
    assert before["vm13234"]["checked_at"] is None and before["vm13234"]["agents"] == []
    r = await world.node.post("/node/report", json={"instances": [], "host": OPENCLAW_ON_NODE})
    assert r.status_code == 204
    after = (await outside(world))["vm13234"]
    assert after["checked_at"] == "2026-11-15T18:40:44+00:00"
    assert [(a["agent"], a["where"], a["root"]) for a in after["agents"]] == [
        ("openclaw", "host", True)
    ]
    assert after["traces"] == [{"agent": "openclaw", "path": "/root/.openclaw", "owner": "root"}]
    assert after["main"] is False
    assert after["model_url"] == "http://10.88.0.1:8000/v1", (
        "a node reaches the model through the tunnel"
    )


async def test_the_main_node_comes_from_its_own_file(world):
    world.report_dir.mkdir()
    report = {**OPENCLAW_ON_NODE, "agents": [
        {"agent": "hermes", "where": "container", "container": "hermes-1", "image": "x/hermes:1"}
    ]}  # fmt: skip
    (world.report_dir / "host.json").write_text(json.dumps(report))
    main = (await outside(world))["main"]
    assert (main["main"], main["model_url"]) == (True, None)
    assert [(a["agent"], a["where"]) for a in main["agents"]] == [("hermes", "container")]


async def test_the_report_is_for_the_owner_only(world):
    member = AsyncClient(transport=ASGITransport(app=world.app), base_url="http://test")
    assert (await member.get("/cluster/outside-agents")).status_code == 401
    r = await world.owner.post("/users", json=MEMBER)
    assert r.status_code == 201, r.text
    login = {"login": MEMBER["login"], "password": MEMBER["password"]}
    assert (await member.post("/auth/login", json=login)).status_code == 200
    assert (await member.get("/cluster/outside-agents")).status_code == 403
    assert (await world.owner.get("/cluster/outside-agents")).status_code == 200  # anchor
    await member.aclose()


async def test_a_node_only_reports_on_itself(world):
    other = await second_node(world, "vm2", KEY_3)
    r = await world.node.post("/node/report", json={"instances": [], "host": OPENCLAW_ON_NODE})
    assert r.status_code == 204
    rows = await outside(world)
    assert [a["agent"] for a in rows["vm13234"]["agents"]] == ["openclaw"]  # anchor
    assert rows["vm2"]["agents"] == [] and rows["vm2"]["checked_at"] is None, (
        "a node's report never lands on another node"
    )
    await other.aclose()


async def test_only_a_new_scan_replaces_the_stored_report(world):
    await world.node.post("/node/report", json={"instances": [], "host": OPENCLAW_ON_NODE})
    same_time = {**OPENCLAW_ON_NODE, "agents": []}  # the same checked_at: the same scan
    await world.node.post("/node/report", json={"instances": [], "host": same_time})
    assert [a["agent"] for a in (await outside(world))["vm13234"]["agents"]] == ["openclaw"]
    newer = {**OPENCLAW_ON_NODE, "checked_at": "2026-11-15T18:45:44+00:00", "agents": []}
    await world.node.post("/node/report", json={"instances": [], "host": newer})
    row = (await outside(world))["vm13234"]
    assert (row["checked_at"], row["agents"]) == ("2026-11-15T18:45:44+00:00", [])


async def test_a_report_over_the_limits_is_cut_not_refused(world):
    """R3: an oversized scan must not refuse the heartbeat, or the node would look lost."""
    many = [
        {"agent": "openclaw", "where": "container", "container": f"c{i}", "image": "x/openclaw:1"}
        for i in range(250)
    ]
    host = {**OPENCLAW_ON_NODE, "agents": many, "traces": [{"agent": "a" * 99, "path": "/p" * 400}]}
    r = await world.node.post("/node/report", json={"instances": [], "host": host})
    assert r.status_code == 204, r.text
    row = (await outside(world))["vm13234"]
    assert (len(row["agents"]), row["truncated"]) == (200, True)
    assert (len(row["traces"][0]["agent"]), len(row["traces"][0]["path"])) == (40, 300)
    nodes = {n["name"]: n for n in (await world.owner.get("/cluster/nodes")).json()}
    assert nodes["vm13234"]["status"] == "active"


async def test_the_node_service_sends_its_scan_with_the_heartbeat(world, tmp_path):
    """The node service pass against the real core: the scan file reaches the main node."""
    scan = tmp_path / "host.json"
    scan.write_text(json.dumps(OPENCLAW_ON_NODE))
    settings = NodeSettings(token="t", host_report_path=scan, deny_file=tmp_path / "deny")
    await one_pass(world.node, NoContainers(), None, settings)
    row = (await outside(world))["vm13234"]
    assert (row["checked_at"], [a["agent"] for a in row["agents"]]) == (
        "2026-11-15T18:40:44+00:00",
        ["openclaw"],
    )
