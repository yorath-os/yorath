"""Agents outside Yorath (change foreign-agents): the node scanner on a fake /proc and container list."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "installer" / "host_scan.py"
OC_ID = "a" * 64
SANDBOX_ID = "b" * 64
DB_ID = "c" * 64
MINE_ID = "d" * 64


@pytest.fixture(scope="module")
def hs():
    spec = importlib.util.spec_from_file_location("host_scan", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def proc_entry(root: Path, pid: int, argv: list[str], uid: int = 0, container: str | None = None):
    d = root / str(pid)
    d.mkdir()
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (d / "status").write_text(f"Name:\tx\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    cg = (
        f"0::/system.slice/docker-{container}.scope\n"
        if container
        else "0::/user.slice/session-1.scope\n"
    )
    (d / "cgroup").write_text(cg)


def container(cid, name, image, project="", yorath=None):
    ours = project == "yorath" if yorath is None else yorath
    return {"id": cid, "name": name, "image": image, "project": project, "yorath": ours}


@pytest.fixture
def node(tmp_path):
    """Field run node: Hermes as root on the host, OpenClaw in its own container, Yorath with a sandbox."""
    proc = tmp_path / "proc"
    proc.mkdir()
    proc_entry(
        proc, 101, ["/root/.hermes/hermes-agent/venv/bin/python3", "/root/.local/bin/hermes"]
    )
    proc_entry(proc, 102, ["node", "dist/index.js", "gateway"], uid=1000, container=OC_ID)
    proc_entry(proc, 103, ["codex", "exec", "--json"], uid=1000, container=SANDBOX_ID)
    proc_entry(proc, 104, ["postgres"], uid=999, container=DB_ID)
    proc_entry(proc, 105, ["grep", "hermes"])
    proc_entry(proc, 106, ["vi", "/opt/openclaw-test/.env"])
    proc_entry(
        proc, 107, ["/home/anna/.local/bin/claude"], uid=4242
    )  # a uid with no user on any machine
    containers = [
        container(
            OC_ID,
            "openclaw-test-openclaw-gateway-1",
            "ghcr.io/openclaw/openclaw:2026.11.3",
            "openclaw-test",
        ),
        container(SANDBOX_ID, "yorath-sandbox-x", "yorath-sandbox:0.2.5", yorath=True),
        container(DB_ID, "yorath-db-1", "pgvector/pgvector:pg17", "yorath"),
        container(MINE_ID, "supabase_db", "supabase/postgres:17", "supabase"),
    ]
    home = tmp_path / "root"
    (home / ".hermes").mkdir(parents=True)
    (home / ".codex").mkdir()
    return proc, containers, [home]


def test_finds_agents_outside_yorath_and_skips_its_own(hs, node):
    proc, containers, homes = node
    report = hs.scan(proc, containers, homes)
    host = sorted(
        (a["agent"], a.get("user"), a.get("root")) for a in report["agents"] if a["where"] == "host"
    )
    assert host == [("claude", "4242", False), ("hermes", "root", True)]
    boxed = [(a["agent"], a["container"]) for a in report["agents"] if a["where"] == "container"]
    assert boxed == [("openclaw", "openclaw-test-openclaw-gateway-1")]
    # Yorath's own containers (the Codex sandbox and the database) are not agents outside Yorath
    assert all(a["agent"] != "codex" for a in report["agents"])
    assert [c["name"] for c in report["containers"]] == ["supabase_db"]
    assert sorted(t["agent"] for t in report["traces"]) == ["codex", "hermes"]


def test_grep_and_editor_are_not_agents(hs):
    assert hs.agent_of_cmdline(["grep", "hermes"]) is None
    assert hs.agent_of_cmdline(["vi", "/opt/openclaw-test/.env"]) is None
    assert hs.agent_of_cmdline(["python3", "/root/.local/bin/hermes"]) == "hermes"  # anchor
    assert (
        hs.agent_of_cmdline(["/root/.hermes/venv/bin/python", "-m", "hermes_cli.main"]) == "hermes"
    )


def test_report_is_public_and_atomic(hs, tmp_path):
    out = tmp_path / "report" / "host.json"
    hs.write_report(out, {"agents": [], "checked_at": "2026-11-12T12:00:00+00:00"})
    assert json.loads(out.read_text())["agents"] == []
    assert out.stat().st_mode & 0o777 == 0o644
    assert [f.name for f in out.parent.iterdir()] == ["host.json"]  # no temporary files left


def test_egress_fingerprint_ignores_handles_and_sees_changes(hs):
    base = {"nftables": [{"table": {"family": "inet", "name": "yorath", "handle": 7}},
                         {"rule": {"chain": "out", "expr": [{"redirect": {"port": 3130}}], "handle": 12}}]}  # fmt: skip
    renumbered = json.loads(json.dumps(base).replace('"handle": 12', '"handle": 99'))
    changed = json.loads(json.dumps(base).replace("3130", "3131"))
    assert hs.egress_fingerprint(json.dumps(base)) == hs.egress_fingerprint(json.dumps(renumbered))
    assert hs.egress_fingerprint(json.dumps(base)) != hs.egress_fingerprint(json.dumps(changed))
    assert hs.egress_fingerprint(None) is None  # no table


def test_host_out_summary(hs):
    log = (
        "yorath-host-out IN= OUT=eth0 SRC=10.0.0.5 DST=140.82.121.4 LEN=60 PROTO=TCP SPT=40000 DPT=22 UID=0 GID=0\n"
        "yorath-host-out IN= OUT=eth0 SRC=10.0.0.5 DST=140.82.121.4 LEN=60 PROTO=TCP SPT=40001 DPT=22 UID=0 GID=0\n"
        "other kernel line\n"
    )
    assert hs.parse_host_out(log) == [
        {"dst": "140.82.121.4", "proto": "TCP", "port": "22", "uid": "0", "count": 2}
    ]
