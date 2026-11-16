"""Node service (change connected-agents, design §2): reconciling containers on a fake Docker."""

import json
from pathlib import Path

import pytest

from yorath.distribution.manifest import load_manifest
from yorath.runtime.connected.config import openclaw_config
from yorath.runtime.connected.node_agent import Container, NodeSettings, reconcile

MANIFEST = load_manifest(Path(__file__).resolve().parents[3] / "distribution.yaml")
OC = MANIFEST.connected_agents["openclaw"]
DEP = "11111111-1111-1111-1111-111111111111"


class FakeDocker:
    """In-memory Docker: containers, volumes, images and an action log."""

    def __init__(self) -> None:
        self.containers: dict[str, Container] = {}
        self.files: dict[str, dict[str, str]] = {}  # volume → files
        self.images: set[str] = set()
        self.volumes: set[str] = set()
        self.actions: list[str] = []
        self.specs: dict[str, dict] = {}
        self.pull_fails: set[str] = set()
        self.reconfigured = 0
        self._n = 0

    def find(self, name):
        return self.containers.get(name)

    def labeled(self):
        return [c for c in self.containers.values() if "yorath.connected" in c.labels]

    def has_image(self, image):
        return image in self.images

    def pull(self, image):
        self.actions.append(f"pull {image}")
        if image in self.pull_fails:
            raise RuntimeError("manifest unknown")
        self.images.add(image)

    def image_version(self, image):
        return image.split(":", 1)[1].split("@", 1)[0], "sha256:" + "d" * 64

    def _volume(self, cid):
        return next(s["volume"] for s in self.specs.values() if s["_id"] == cid)

    def read_files(self, container_id, directory, names):
        files = self.files.get(self._volume(container_id), {})
        return {n: files.get(n) for n in names}

    def remove(self, container_id):
        name = next(n for n, c in self.containers.items() if c.id == container_id)
        self.actions.append(f"remove {name}")
        del self.containers[name]

    def create(self, spec):
        self._n += 1
        cid = f"c{self._n}"
        self.specs[spec["name"]] = spec | {"_id": cid}
        self.volumes.add(spec["volume"])
        self.containers[spec["name"]] = Container(cid, spec["name"], spec["labels"], running=False)
        self.actions.append(f"create {spec['name']}")
        return cid

    def write_files(self, container_id, directory, files, owner):
        self.files.setdefault(self._volume(container_id), {}).update(files)
        self.actions.append(f"write {sorted(files)} owner={owner}")

    def start(self, container_id):
        for c in self.containers.values():
            if c.id == container_id:
                c.running = True

    def remove_volume(self, name):
        self.actions.append(f"remove volume {name}")
        self.volumes.discard(name)
        self.files.pop(name, None)

    def reconfigure_proxy(self):
        self.reconfigured += 1


def running(image: str | None = None, key: str = "yorath_key_1", nonce: str = "") -> dict:
    cfg = openclaw_config(key, "entry", 65536, OC.port)
    return {
        "agent": "openclaw", "deployment_id": DEP, "address": "172.31.0.10", "state": "running",
        "runtime": "openclaw", "image": image or OC.image(), "repository": OC.repository,
        "pull_nonce": nonce, "port": OC.port, "health_path": OC.health_path, "user": OC.user,
        "cap_add": OC.cap_add, "memory_mb": OC.memory_mb, "command": OC.command,
        "state_dir": OC.state_dir,
        "config": {"managed": cfg.managed, "env": cfg.env, "dotenv": cfg.dotenv},
        "config_sha": cfg.sha(),
    }  # fmt: skip


@pytest.fixture
def node(tmp_path):
    return FakeDocker(), NodeSettings(token="t", deny_file=tmp_path / "egress" / "yorath-deny.conf")


@pytest.mark.covers("INV-CON-2")
def test_first_start_creates_unprivileged_container_with_config(node):
    docker, settings = node
    [report] = reconcile({"agents": [running()], "deny": []}, docker, MANIFEST, settings)
    assert report["state"] == "starting" and report["version"] == "2026.11.3"
    spec = docker.specs["yorath-agent-openclaw"]
    assert spec["cap_drop"] == ["ALL"] and spec["security_opt"] == ["no-new-privileges"]
    assert spec["user"] == "1000:1000" and spec["network"] == "yorath_agents"
    assert spec["ipv4_address"] == "172.31.0.10" and spec["volume"] == "yorath-agent-openclaw"
    assert spec["environment"]["YORATH_API_KEY"] == "yorath_key_1"
    written = json.loads(docker.files["yorath-agent-openclaw"]["openclaw.json"])
    assert written["models"]["providers"]["yorath"]["baseUrl"] == "http://core:8000/v1"
    assert "write ['openclaw.json'] owner=(1000, 1000)" in docker.actions


def test_same_state_is_left_alone_and_reported_running(node):
    docker, settings = node
    reconcile({"agents": [running()]}, docker, MANIFEST, settings)
    docker.actions.clear()
    [report] = reconcile({"agents": [running()]}, docker, MANIFEST, settings)
    assert docker.actions == [] and report["state"] == "running"


def test_new_key_recreates_and_keeps_owner_settings(node):
    docker, settings = node
    reconcile({"agents": [running()]}, docker, MANIFEST, settings)
    vol = docker.files["yorath-agent-openclaw"]
    vol["openclaw.json"] = json.dumps({**json.loads(vol["openclaw.json"]), "ui": {"x": 1}})
    vol["memory.db"] = "memory"
    docker.actions.clear()
    reconcile({"agents": [running(key="yorath_key_2")]}, docker, MANIFEST, settings)
    assert docker.actions[:2] == ["remove yorath-agent-openclaw", "create yorath-agent-openclaw"]
    assert docker.specs["yorath-agent-openclaw"]["environment"]["YORATH_API_KEY"] == "yorath_key_2"
    assert json.loads(docker.files["yorath-agent-openclaw"]["openclaw.json"])["ui"] == {"x": 1}
    assert docker.files["yorath-agent-openclaw"]["memory.db"] == "memory"


@pytest.mark.covers("INV-CON-2")
def test_image_outside_catalog_repository_is_refused(node):
    docker, settings = node
    item = running(image="evil.example/openclaw:2026.11.3")
    [report] = reconcile({"agents": [item]}, docker, MANIFEST, settings)
    assert report["state"] == "failed" and "not from the node catalog" in report["detail"]
    assert docker.containers == {} and docker.actions == []


def test_owner_tag_accepted_and_failed_pull_keeps_old_container(node):
    docker, settings = node
    reconcile({"agents": [running()]}, docker, MANIFEST, settings)
    old = docker.containers["yorath-agent-openclaw"].id
    bad = f"{OC.repository}:9999.1.1"
    docker.pull_fails.add(bad)
    [report] = reconcile({"agents": [running(image=bad, nonce="n1")]}, docker, MANIFEST, settings)
    assert report["state"] == "failed" and "not pulled" in report["detail"]
    assert docker.containers["yorath-agent-openclaw"].id == old  # the previous agent keeps running
    good = f"{OC.repository}:2026.11.5"
    [report] = reconcile({"agents": [running(image=good, nonce="n2")]}, docker, MANIFEST, settings)
    assert report["state"] == "starting" and report["version"] == "2026.11.5"


def test_latest_is_pulled_again_only_with_new_nonce(node):
    docker, settings = node
    latest = f"{OC.repository}:latest"
    reconcile({"agents": [running(image=latest, nonce="a")]}, docker, MANIFEST, settings)
    docker.actions.clear()
    reconcile({"agents": [running(image=latest, nonce="a")]}, docker, MANIFEST, settings)
    assert docker.actions == []
    reconcile({"agents": [running(image=latest, nonce="b")]}, docker, MANIFEST, settings)
    assert docker.actions[0] == f"pull {latest}"


def test_stopped_keeps_volume_purge_wipes_it_and_orphan_is_removed(node):
    docker, settings = node
    reconcile({"agents": [running()]}, docker, MANIFEST, settings)
    stopped = {"agent": "openclaw", "deployment_id": DEP, "address": "x", "state": "stopped"}
    [report] = reconcile({"agents": [stopped]}, docker, MANIFEST, settings)
    assert report["state"] == "stopped" and "yorath-agent-openclaw" in docker.volumes
    assert docker.containers == {}
    reconcile({"agents": [running()]}, docker, MANIFEST, settings)
    [report] = reconcile({"agents": [stopped | {"state": "purge"}]}, docker, MANIFEST, settings)
    assert report["state"] == "purged" and "yorath-agent-openclaw" not in docker.volumes
    reconcile({"agents": [running()]}, docker, MANIFEST, settings)
    reconcile({"agents": []}, docker, MANIFEST, settings)  # the core no longer knows the agent
    assert docker.containers == {} and "yorath-agent-openclaw" in docker.volumes


def test_deny_file_written_and_proxy_reconfigured_only_on_change(node):
    docker, settings = node
    reconcile({"agents": [], "deny": []}, docker, MANIFEST, settings)
    assert settings.deny_file.read_text().endswith("acl yorath_off src 255.255.255.255/32\n")
    reconcile({"agents": [], "deny": ["172.31.0.10"]}, docker, MANIFEST, settings)
    assert settings.deny_file.read_text().endswith("acl yorath_off src 172.31.0.10\n")
    assert docker.reconfigured == 2
    reconcile({"agents": [], "deny": ["172.31.0.10"]}, docker, MANIFEST, settings)
    assert docker.reconfigured == 2


def test_container_that_failed_to_start_is_started_again(node):
    """The first start failed (cluster lab 13.11.2026: cgroup); the next reconcile starts it."""
    docker, settings = node
    reconcile({"agents": [running()]}, docker, MANIFEST, settings)
    c = docker.containers["yorath-agent-openclaw"]
    c.running, c.status, c.error = False, "created", "OCI runtime create failed"
    docker.actions.clear()
    [report] = reconcile({"agents": [running()]}, docker, MANIFEST, settings)
    assert report["state"] == "starting" and c.running
    assert not any(a.startswith(("create", "remove")) for a in docker.actions)
    c.running, c.status = False, "dead"
    [report] = reconcile({"agents": [running()]}, docker, MANIFEST, settings)
    assert report["state"] == "failed"


def test_host_report_reads_the_scan_file_and_skips_a_broken_one(tmp_path):
    """The scan file the heartbeat carries (that it does: tests/groups/test_outside_agents.py)."""
    from yorath.runtime.connected.node_agent import NodeSettings, host_report

    path = tmp_path / "host.json"
    settings = NodeSettings(token="t", host_report_path=path)
    assert host_report(settings) is None, "no scan yet: nothing to send"
    path.write_text(
        '{"checked_at": "2026-11-15T18:40:44+00:00", "agents": [{"agent": "openclaw"}]}'
    )
    assert host_report(settings) == {
        "checked_at": "2026-11-15T18:40:44+00:00",
        "agents": [{"agent": "openclaw"}],
        "traces": [],
        "containers": [],
    }
    path.write_text("{broken")
    assert host_report(settings) is None, "a half-written file is skipped, the heartbeat still goes"


def test_processes_of_one_agent_are_one_entry_and_the_lists_stay_within_the_limits(tmp_path):
    """R3: the scan lists every process; the main node gets one entry per agent, within limits."""
    from yorath.runtime.connected.node_agent import NodeSettings, host_report

    procs = [{"agent": "openclaw", "where": "host", "pid": 100 + i, "user": "root", "root": True,
              "command": "node"} for i in range(300)]  # fmt: skip
    traces = [
        {"agent": "hermes", "path": f"/home/u{i}/.hermes", "owner": f"u{i}"} for i in range(250)
    ]
    path = tmp_path / "host.json"
    path.write_text(json.dumps({"checked_at": "2026-11-16T10:00:00+00:00", "agents": procs,
                                "traces": traces}))  # fmt: skip
    report = host_report(NodeSettings(token="t", host_report_path=path))
    assert report is not None
    assert [(a["agent"], a["pid"], a["processes"]) for a in report["agents"]] == [
        ("openclaw", 100, 300)
    ]
    assert (len(report["traces"]), report["truncated"]) == (200, True)


def test_egress_lines_are_sent_in_batches_the_main_node_accepts(tmp_path):
    from yorath.runtime.connected.node_agent import NodeSettings, new_egress_lines

    log_file, offset = tmp_path / "access.log", tmp_path / "shipped.offset"
    log_file.write_text("".join(f"line {i}\n" for i in range(12)))
    settings = NodeSettings(token="t", egress_log=log_file, egress_offset=offset)
    first, position = new_egress_lines(settings, max_lines=5)
    assert first == [f"line {i}" for i in range(5)]
    offset.write_text(position)
    second, position = new_egress_lines(settings, max_lines=5)
    assert second == [f"line {i}" for i in range(5, 10)], "the next batch starts after the first"
    offset.write_text(position)
    assert new_egress_lines(settings, max_lines=5)[0] == ["line 10", "line 11"]


def test_restart_recreates_the_container_with_its_volume_without_pulling(node):
    """Field-fixes-0-7: Restart is a new restart nonce; the same image, config and volume."""
    docker, settings = node
    reconcile({"agents": [running()]}, docker, MANIFEST, settings)
    old = docker.containers["yorath-agent-openclaw"].id
    docker.actions.clear()
    [report] = reconcile(
        {"agents": [running() | {"restart_nonce": "r1"}]}, docker, MANIFEST, settings
    )
    assert report["state"] == "starting"
    assert docker.containers["yorath-agent-openclaw"].id != old
    assert not any(a.startswith("pull") for a in docker.actions)
    assert "yorath-agent-openclaw" in docker.volumes
    docker.actions.clear()
    reconcile({"agents": [running() | {"restart_nonce": "r1"}]}, docker, MANIFEST, settings)
    assert docker.actions == []  # the same nonce: nothing to do


def test_an_update_adding_the_restart_label_recreates_nothing(node):
    docker, settings = node
    reconcile({"agents": [running()]}, docker, MANIFEST, settings)
    docker.containers["yorath-agent-openclaw"].labels.pop("yorath.restart")  # made by 0.7.x
    docker.actions.clear()
    reconcile({"agents": [running()]}, docker, MANIFEST, settings)
    assert docker.actions == []
