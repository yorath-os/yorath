#!/usr/bin/env python3
"""Agents outside Yorath on the node (change foreign-agents, decision 36): who runs outside its control.

Runs on the host as root (`yorath scan`, `yorath doctor`, systemd timer). Stdlib only.
Finds processes of known agents on the host and in containers outside the Yorath project, their directories
in home directories, and other containers. The report has no secrets: names, users, paths, images.

    python3 installer/host_scan.py [--out /var/lib/yorath/report/host.json] [--proc /proc]
"""

import argparse
import hashlib
import ipaddress
import json
import os
import pwd
import re
import socket
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

# agent name → what appears in the executable, image or container name
AGENTS = {
    "hermes": ("hermes", "hermes-agent"),
    "openclaw": ("openclaw",),
    "codex": ("codex",),
    "claude": ("claude",),
    "opencode": ("opencode",),
    "goose": ("goose", "goosed"),
    "aider": ("aider",),
    "openhands": ("openhands",),
}
# agent state directories in the home directory
TRACES = {
    "hermes": ".hermes",
    "openclaw": ".openclaw",
    "codex": ".codex",
    "claude": ".claude",
    "opencode": ".config/opencode",
    "goose": ".config/goose",
}
YORATH_PROJECT = "yorath"
SANDBOX_LABEL = "yorath.sandbox"  # Developer Agent sandboxes are started by sandbox-runner, not Compose
CONNECTED_LABEL = "yorath.connected"  # connected agents are started by node-agent (connected-agents)
CONTAINER_ID = re.compile(r"[0-9a-f]{64}")


def agent_of_name(name: str) -> str | None:
    """Agent by the file base name: ``hermes``, ``openclaw-gateway``, but not ``codexfoo.py``."""
    base = os.path.basename(name).lower()
    for agent, names in AGENTS.items():
        for n in names:
            if base == n or base.startswith(n + "-") or base.startswith(n + "."):
                return agent
    return None


def agent_of_image(image: str, container: str) -> str | None:
    text = f"{image} {container}".lower()
    for agent, names in AGENTS.items():
        if any(re.search(rf"(^|[/:_\-. ]){re.escape(n)}($|[/:_\-. @])", text) for n in names):
            return agent
    return None


INTERPRETERS = re.compile(r"^(python[0-9.]*|node(js)?|bun|deno|uv|uvx|npx|pnpm|sh|bash)$")


def agent_of_cmdline(argv: list[str]) -> str | None:
    """``hermes …``, ``python …/bin/hermes``, ``node …/openclaw/dist/index.js``, path ``/.hermes/``.

    The script is the second argument only for an interpreter: ``grep hermes`` and ``vi …/openclaw`` are not agents.
    """
    head = argv[:2] if INTERPRETERS.match(os.path.basename(argv[0])) else argv[:1]
    for arg in head:
        if found := agent_of_name(arg):
            return found
    for arg in head:
        for agent, trace in TRACES.items():
            if f"/{trace}/" in arg:
                return agent
        parts = Path(arg).parts
        for agent, names in AGENTS.items():
            if any(n in parts for n in names):
                return agent
    return None


def docker_containers() -> list[dict]:
    """Running containers: id, name, image, Compose project; empty without Docker."""
    fmt = (
        '{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Label "com.docker.compose.project"}}'
        f'\t{{{{.Label "{SANDBOX_LABEL}"}}}}\t{{{{.Label "{CONNECTED_LABEL}"}}}}'
    )
    try:
        out = subprocess.run(
            ["docker", "ps", "--no-trunc", "--format", fmt],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        cid, name, image, project, sandbox, connected = (line.split("\t") + [""] * 6)[:6]
        rows.append({"id": cid, "name": name, "image": image, "project": project,
                     "yorath": project == YORATH_PROJECT or bool(sandbox) or bool(connected)})  # fmt: skip
    return rows


def _user(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def processes(proc: Path) -> list[dict]:
    out = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
            argv = [a.decode(errors="replace") for a in raw.split(b"\0") if a]
            uid = int(
                next(
                    line.split()[1]
                    for line in (entry / "status").read_text().splitlines()
                    if line.startswith("Uid:")
                )
            )
            cgroup = (entry / "cgroup").read_text()
        except (OSError, StopIteration, ValueError, IndexError):
            continue  # the process exited or the kernel does not expose the fields
        if argv:
            ids = CONTAINER_ID.findall(cgroup)
            out.append({"pid": int(entry.name), "argv": argv, "uid": uid, "container": ids[-1] if ids else None})
    return out


def scan(proc: Path, containers: list[dict], homes: list[Path], *, self_pid: int | None = None) -> dict:
    by_id = {c["id"]: c for c in containers}
    agents: list[dict] = []
    seen_containers: set[str] = set()
    for c in containers:
        if c["yorath"]:
            continue
        if agent := agent_of_image(c["image"], c["name"]):
            agents.append({"agent": agent, "where": "container", "container": c["name"], "image": c["image"]})
            seen_containers.add(c["id"])
    for p in processes(proc):
        if p["pid"] == self_pid:
            continue
        agent = agent_of_cmdline(p["argv"])
        if agent is None:
            continue
        cid = p["container"]
        if cid is not None:
            c = by_id.get(cid)
            if c is None or c["yorath"] or cid in seen_containers:
                continue  # a Yorath container of our own, or one already listed
            agents.append({"agent": agent, "where": "container", "container": c["name"], "image": c["image"]})
            seen_containers.add(cid)
            continue
        user = _user(p["uid"])
        agents.append({
            "agent": agent, "where": "host", "pid": p["pid"], "user": user,
            "root": p["uid"] == 0, "command": os.path.basename(p["argv"][0]),
        })  # fmt: skip
    traces = []
    for home in homes:
        for agent, rel in TRACES.items():
            path = home / rel
            if path.is_dir():
                try:
                    owner = _user(path.stat().st_uid)
                except OSError:
                    owner = "?"
                traces.append({"agent": agent, "path": str(path), "owner": owner})
    others = [
        {"name": c["name"], "image": c["image"], "project": c["project"]}
        for c in containers
        if not c["yorath"] and c["id"] not in seen_containers
    ]
    return {
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "agents": agents,
        "traces": traces,
        "containers": others,
    }


def homes() -> list[Path]:
    found = [Path("/root")]
    base = Path("/home")
    if base.is_dir():
        found += [p for p in sorted(base.iterdir()) if p.is_dir()]
    return found


def _run(cmd: list[str], timeout: int = 20) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _strip_handles(node):
    if isinstance(node, dict):
        return {k: _strip_handles(v) for k, v in sorted(node.items()) if k not in ("handle", "metainfo")}
    if isinstance(node, list):
        return [_strip_handles(v) for v in node]
    return node


def egress_fingerprint(listing: str | None) -> str | None:
    """Fingerprint of the inet yorath table without rule handles: a removed or modified table shows up."""
    if not listing:
        return None
    try:
        data = json.loads(listing)
    except ValueError:
        return None
    body = json.dumps(_strip_handles(data.get("nftables", [])), sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def egress_state(state_dir: Path, env_local: Path) -> dict:
    """Node egress control (change node-egress): mode, table, fingerprint, proxy, service, alarm."""
    mode = "unset"
    try:
        for line in env_local.read_text(encoding="utf-8").splitlines():
            if line.startswith("YORATH_EGRESS_LOCKDOWN="):
                mode = "off" if line.split("=", 1)[1].strip().lower() == "off" else "on"
    except OSError:
        pass
    current = egress_fingerprint(_run(["nft", "-j", "list", "table", "inet", "yorath"]))
    try:
        expected = (state_dir / "egress.sum").read_text().strip()
    except OSError:
        expected = None
    service = (_run(["systemctl", "is-active", "yorath-egress"]) or "").strip() or None
    alarm = None
    try:
        alarm = (state_dir / "egress.alarm").read_text(encoding="utf-8").strip() or None
    except OSError:
        pass
    return {
        "mode": mode,
        "table": current is not None,
        "intact": current is not None and current == expected,
        "proxy": port_open(3130),
        "service": service,
        "alarm": alarm,
        "enforced": mode == "on" and current is not None and current == expected and port_open(3130),
    }


def container_ips() -> dict[str, list[str]]:
    """IP of every running container → name: the source in the network journal."""
    ids = (_run(["docker", "ps", "-q"]) or "").split()
    if not ids:
        return {}
    out = _run(["docker", "inspect", "--format",
                "{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", *ids])  # fmt: skip
    found: dict[str, list[str]] = {}
    for line in (out or "").splitlines():
        name, *words = line.split()
        # a host-network container has no address of its own: Docker prints "invalid IP" for it
        found[name.lstrip("/")] = [w for w in words if _is_ip(w)]
    return found


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def host_ips() -> list[str]:
    out = _run(["ip", "-j", "addr"])
    try:
        return sorted({a["local"] for i in json.loads(out or "[]") for a in i.get("addr_info", [])})
    except (ValueError, KeyError, TypeError):
        return []


def parse_host_out(text: str) -> list[dict]:
    """Lines ``yorath-host-out … DST=… PROTO=TCP … DPT=22 … UID=0`` → summary by (address, protocol, port, uid)."""
    seen: dict[tuple, int] = {}
    for line in text.splitlines():
        if "yorath-host-out" not in line:
            continue
        f = dict(t.split("=", 1) for t in line.split() if "=" in t)
        key = (f.get("DST", "?"), f.get("PROTO", "?"), f.get("DPT", ""), f.get("UID", ""))
        seen[key] = seen.get(key, 0) + 1
    return [{"dst": d, "proto": p, "port": port, "uid": uid, "count": n} for (d, p, port, uid), n in seen.items()]


def host_out(since: str = "-6min") -> list[dict]:
    """Other protocols from the host (decision 36: journal, not deny) from the node kernel log."""
    return parse_host_out(_run(["journalctl", "-k", "-o", "cat", "--since", since, "--no-pager"]) or "")


def write_report(path: Path, report: dict) -> None:
    """Atomic: the core never reads half a file. The report has no secrets, so 0644."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o755)
    # own temporary file: the timer and yorath doctor may write the report at the same time
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".host-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=1))
    os.chmod(name, 0o644)
    os.replace(name, path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path)
    ap.add_argument("--proc", type=Path, default=Path("/proc"))
    ap.add_argument("--state", type=Path, default=Path("/var/lib/yorath"))
    ap.add_argument("--env-local", type=Path, default=Path(__file__).resolve().parent / ".env.local")
    ap.add_argument("--egress-fingerprint", action="store_true", help="fingerprint of the inet yorath table")
    a = ap.parse_args()
    if a.egress_fingerprint:
        print(egress_fingerprint(_run(["nft", "-j", "list", "table", "inet", "yorath"])) or "")
        return
    report = scan(a.proc, docker_containers(), homes(), self_pid=os.getpid())
    report["egress"] = egress_state(a.state, a.env_local)
    report["container_ips"] = container_ips()
    report["host_ips"] = host_ips()
    report["host_out"] = host_out()
    if a.out:
        write_report(a.out, report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
