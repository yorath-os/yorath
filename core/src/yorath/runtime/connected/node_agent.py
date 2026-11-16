"""``node-agent`` node service (change connected-agents, design §2): reconciles agent containers.

Every few seconds it fetches the desired state from the core (``GET /node/desired`` with the node
token),
brings the connected agent containers to it and sends a report (``POST /node/report``). It is the
second
service with the Docker socket besides ``sandbox-runner``, so the boundary is here:

- the image comes only from the manifest catalog repository in the service image, not from the core
response;
- the container is unprivileged: ``cap_drop: ALL`` (+ catalog capabilities), ``no-new-privileges``,
  the catalog user, a memory limit, only the agents network with the catalog address, only its own
  volume;
- the new image is pulled before the old container is removed: a bad version does not leave you
without an agent.

Internet bans for agents are the node proxy file ``yorath-deny.conf`` and ``squid -k reconfigure``.
"""

import asyncio
import contextlib
import io
import json
import logging
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

from yorath.distribution.manifest import Manifest, load_manifest
from yorath.runtime.connected.config import AgentConfig, files_for
from yorath.runtime.connected.limits import (
    EGRESS_CHUNK_BYTES,
    EGRESS_LINES,
    HOST_AGENTS,
    HOST_CONTAINERS,
    HOST_TRACES,
)
from yorath.runtime.connected.telegram_check import TelegramChecks

log = logging.getLogger("yorath.node_agent")

LABEL = "yorath.connected"
NETWORK = "yorath_agents"
EMPTY_DENY = "255.255.255.255/32"  # yorath_off is always defined: squid.conf refers to it


class NodeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="YORATH_NODE_", extra="ignore")

    token: str = ""
    core_url: str = "http://core:8000"
    interval_s: float = 5
    network: str = NETWORK
    deny_file: Path = Path("/var/lib/yorath-egress/yorath-deny.conf")
    egress_service: str = "egress"  # Compose service of the node proxy
    project: str = "yorath"
    # cluster worker node: its proxy log goes to the main node (on the main node the core reads it)
    ship_egress: bool = False
    egress_log: Path = Path("/var/log/yorath-egress/access.log")
    egress_offset: Path = Path("/var/lib/yorath-egress/shipped.offset")
    # the node's cards, written by join.sh (change model-placements): one model server per card
    gpu_count: int | None = None
    gpu_name: str | None = None
    gpu_vram_gb: int | None = None
    model: bool = False  # a node without a GPU runs a model server (YORATH_NODE_MODEL=on)
    # the node's "yorath scan" report, mounted from installer/.host-report (outside-agents-cluster)
    host_report_path: Path = Path("/host-report/host.json")


def hardware_report(settings: NodeSettings) -> dict[str, Any] | None:
    """The node's cards for the main node; None on a node that does not know them (main node)."""
    if settings.gpu_count is None:
        return None
    return {
        "gpu_count": settings.gpu_count,
        "gpu": settings.gpu_name or None,
        "vram_gb": settings.gpu_vram_gb,
        "has_model": settings.model,
    }


def host_report(settings: NodeSettings) -> dict[str, Any] | None:
    """The node's last scan for the main node: agents outside Yorath (decision 36). None before the
    first scan or while the file is being rewritten; the heartbeat goes either way."""
    try:
        raw = json.loads(settings.host_report_path.read_text(encoding="utf-8"))
        agents = _one_per_agent(list(raw.get("agents", [])))
        traces, containers = list(raw.get("traces", [])), list(raw.get("containers", []))
    except OSError, ValueError, KeyError, TypeError, AttributeError:
        return None
    cut = (
        len(agents) > HOST_AGENTS or len(traces) > HOST_TRACES or len(containers) > HOST_CONTAINERS
    )
    report: dict[str, Any] = {
        "checked_at": str(raw.get("checked_at", "")),
        "agents": agents[:HOST_AGENTS],
        "traces": traces[:HOST_TRACES],
        "containers": containers[:HOST_CONTAINERS],
    }
    if not report["checked_at"]:
        return None
    if cut:  # the main node would cut it too; saying so keeps the panel honest (R3)
        report["truncated"] = True
    return report


def _one_per_agent(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Processes of one agent on the host (a gateway and its workers) are one entry with their
    number: the scan lists every process, the main node needs the agent."""
    out: list[dict[str, Any]] = []
    seen: dict[tuple, dict[str, Any]] = {}
    for a in agents:
        if a.get("where") != "host":
            out.append(a)
            continue
        key = (a.get("agent"), a.get("user"), a.get("root"))
        if key in seen:
            seen[key]["processes"] = seen[key].get("processes", 1) + 1
            continue
        seen[key] = dict(a)
        out.append(seen[key])
    return out


@dataclass
class Container:
    id: str
    name: str
    labels: dict[str, str]
    running: bool
    health: str | None = None
    error: str | None = None
    status: str = ""  # created | running | exited | restarting | …


@dataclass
class Plan:
    """What the service will do for one agent, separate from Docker so tests can check it."""

    pull: bool = False
    recreate: bool = False
    start: bool = False
    remove: bool = False
    remove_volume: bool = False
    report: dict[str, Any] = field(default_factory=dict)


class Docker(Protocol):
    def find(self, name: str) -> Container | None: ...
    def labeled(self) -> list[Container]: ...
    def has_image(self, image: str) -> bool: ...
    def pull(self, image: str) -> None: ...
    def image_version(self, image: str) -> tuple[str | None, str | None]: ...
    def read_files(
        self, container_id: str, directory: str, names: list[str]
    ) -> dict[str, str | None]: ...  # noqa: E501
    def remove(self, container_id: str) -> None: ...
    def create(self, spec: dict[str, Any]) -> str: ...
    def write_files(
        self, container_id: str, directory: str, files: dict[str, str], owner: tuple[int, int]
    ) -> None: ...  # noqa: E501
    def start(self, container_id: str) -> None: ...
    def remove_volume(self, name: str) -> None: ...
    def reconfigure_proxy(self) -> None: ...


def container_name(agent: str) -> str:
    return f"yorath-agent-{agent}"


def volume_name(agent: str) -> str:
    return f"yorath-agent-{agent}"


def allowed_image(manifest: Manifest, agent: str, image: str) -> bool:
    """The image comes only from this agent's catalog repository, with any tag (a custom
    version)."""
    spec = manifest.connected_agents.get(agent)
    return spec is not None and image.startswith(spec.repository + ":")


def owner_of(item: dict[str, Any]) -> tuple[int, int]:
    """Owner of the config files: the catalog user; without one, root (the image init will
    hand rights to its own user, like s6 in Hermes)."""
    user = item.get("user")
    if not user:
        return 0, 0
    uid, _, gid = str(user).partition(":")
    return int(uid), int(gid or uid)


def plan(item: dict[str, Any], current: Container | None, manifest: Manifest) -> Plan:
    """The decision for one agent from the desired state and the current container."""
    p = Plan(report={"deployment_id": item["deployment_id"]})
    state = item["state"]
    if state == "purge":
        p.remove, p.remove_volume = current is not None, True
        p.report["state"] = "purged"
        return p
    if state == "stopped":
        p.remove = current is not None
        p.report |= {"state": "stopped", "detail": item.get("detail")}
        return p
    if not allowed_image(manifest, item["agent"], item["image"]):
        p.report |= {
            "state": "failed",
            "detail": f"image {item['image']} is not from the node catalog",
        }
        return p
    want = {
        "yorath.image": item["image"],
        "yorath.config": item["config_sha"],
        "yorath.pull": item.get("pull_nonce", ""),
        "yorath.restart": item.get("restart_nonce", ""),  # Restart: same image and volume
    }
    # a label the container doesn't have yet is empty: an update adding one recreates nothing
    stale = current is None or any(current.labels.get(k, "") != v for k, v in want.items())
    if stale:
        p.recreate = True
        p.pull = current is None or current.labels.get("yorath.pull") != want["yorath.pull"]
        p.report["state"] = "starting"
        return p
    assert current is not None
    if not current.running and current.status in ("created", "exited"):
        # first start failed or the container was stopped by hand: the desired state is running
        p.start = True
        p.report |= {"state": "starting", "detail": current.error or None}
    elif not current.running:
        p.report |= {"state": "failed", "detail": current.error or "container stopped"}
    elif current.health == "starting":
        p.report["state"] = "starting"
    else:
        p.report["state"] = "running"
    p.report["container_id"] = current.id
    return p


def container_spec(item: dict[str, Any], network: str) -> dict[str, Any]:
    """Agent container parameters: everything except the image and configuration comes from the node
    catalog."""
    agent = item["agent"]
    return {
        "name": container_name(agent),
        "image": item["image"],
        "command": item.get("command") or None,
        "user": item.get("user") or None,
        "environment": item["config"].get("env") or {},
        "labels": {
            LABEL: agent,
            "yorath.deployment": item["deployment_id"],
            "yorath.image": item["image"],
            "yorath.config": item["config_sha"],
            "yorath.pull": item.get("pull_nonce", ""),
            "yorath.restart": item.get("restart_nonce", ""),
        },
        "cap_drop": ["ALL"],
        "cap_add": item.get("cap_add") or [],
        "security_opt": ["no-new-privileges"],
        "mem_limit": f"{int(item['memory_mb'])}m",
        "volume": volume_name(agent),
        "state_dir": item["state_dir"],
        "network": network,
        "ipv4_address": item["address"],
    }


def deny_conf(addresses: list[str]) -> str:
    srcs = " ".join(addresses) if addresses else EMPTY_DENY
    return (
        "# Written by the Yorath node service (connected-agents): "
        "agents with internet turned off.\n"
        f"acl yorath_off src {srcs}\n"
    )


def reconcile(
    desired: dict[str, Any], docker: Docker, manifest: Manifest, settings: NodeSettings
) -> list[dict[str, Any]]:
    """Brings the node to the desired state; returns the report per Instance."""
    reports: list[dict[str, Any]] = []
    wanted = {item["agent"] for item in desired.get("agents", [])}
    for item in desired.get("agents", []):
        agent = item["agent"]
        current = docker.find(container_name(agent))
        p = plan(item, current, manifest)
        try:
            if p.recreate:
                _recreate(item, current, p, docker, settings)
            elif p.start and current is not None:
                docker.start(current.id)
            elif p.remove and current is not None:
                docker.remove(current.id)
            if p.remove_volume:
                docker.remove_volume(volume_name(agent))
        except Exception as exc:  # noqa: BLE001 — a failure report matters more than crashing the loop
            log.exception("agent %s: reconcile failed", agent)
            p.report |= {"state": "failed", "detail": str(exc)[:480]}
        reports.append(p.report)
    for c in docker.labeled():  # an agent container the core no longer knows
        if c.labels.get(LABEL) not in wanted:
            docker.remove(c.id)
    _write_deny(desired.get("deny", []), docker, settings)
    return reports


def _recreate(
    item: dict[str, Any], current: Container | None, p: Plan, docker: Docker,
    settings: NodeSettings,
) -> None:  # fmt: skip
    image = item["image"]
    if p.pull or not docker.has_image(image):
        try:
            docker.pull(image)
        except Exception as exc:  # noqa: BLE001
            p.report |= {"state": "failed", "detail": f"image {image} not pulled: {exc}"[:480]}
            if current is not None:
                p.report["container_id"] = current.id  # the previous container keeps running
            return
    config = AgentConfig(
        item["runtime"], item["config"]["managed"], item["config"].get("env") or {},
        item["config"].get("dotenv") or {},
    )  # fmt: skip
    names = list(files_for(config, {}))
    if current is not None:
        existing = docker.read_files(current.id, item["state_dir"], names)
        docker.remove(current.id)
    else:
        existing = {}
    new_id = docker.create(container_spec(item, settings.network))
    if current is None:  # the volume may be left from an earlier connection: read from the new one
        existing = docker.read_files(new_id, item["state_dir"], names)
    docker.write_files(new_id, item["state_dir"], files_for(config, existing), owner_of(item))
    docker.start(new_id)
    version, digest = docker.image_version(image)
    p.report |= {"state": "starting", "container_id": new_id, "version": version, "digest": digest}


def _write_deny(addresses: list[str], docker: Docker, settings: NodeSettings) -> None:
    text = deny_conf(sorted(addresses))
    path = settings.deny_file
    try:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        log.exception("could not write %s", path)
        return
    try:
        docker.reconfigure_proxy()
    except Exception:  # noqa: BLE001
        log.exception("node proxy did not reload the bans")


# ---- Docker SDK ------------------------------------------------------------------------------


class DockerSdk:
    """``Docker`` implementation on the docker SDK (socket ``/var/run/docker.sock``)."""

    def __init__(self, settings: NodeSettings) -> None:
        import docker

        self.client = docker.from_env()
        self.api = self.client.api
        self.settings = settings

    def _info(self, c: Any) -> Container:
        state = c.attrs.get("State", {})
        health = (state.get("Health") or {}).get("Status")
        return Container(
            id=c.id, name=c.name, labels=c.labels or {}, running=state.get("Running", False),
            health=health, error=state.get("Error") or None, status=state.get("Status", ""),
        )  # fmt: skip

    def find(self, name: str) -> Container | None:
        import docker.errors

        try:
            return self._info(self.client.containers.get(name))
        except docker.errors.NotFound:
            return None

    def labeled(self) -> list[Container]:
        found = self.client.containers.list(all=True, filters={"label": LABEL})
        return [self._info(c) for c in found]

    def has_image(self, image: str) -> bool:
        import docker.errors

        try:
            self.client.images.get(image)
            return True
        except docker.errors.ImageNotFound:
            return False

    def pull(self, image: str) -> None:
        repo, _, rest = image.partition(":")
        tag = rest.split("@", 1)[0]
        if "@" in rest:
            self.api.pull(repo, tag=rest.split("@", 1)[1])  # by digest: exactly the tested image
            self.api.tag(f"{repo}@{rest.split('@', 1)[1]}", repo, tag)
        else:
            self.api.pull(repo, tag=tag)

    def image_version(self, image: str) -> tuple[str | None, str | None]:
        img = self.client.images.get(image)
        labels = img.labels or {}
        tag = image.split(":", 1)[1].split("@", 1)[0] if ":" in image else None
        digests = img.attrs.get("RepoDigests") or []
        digest = digests[0].split("@", 1)[1] if digests else None
        return labels.get("org.opencontainers.image.version") or tag, digest

    def read_files(
        self, container_id: str, directory: str, names: list[str]
    ) -> dict[str, str | None]:  # noqa: E501
        import docker.errors

        out: dict[str, str | None] = {}
        for name in names:
            try:
                stream, _ = self.api.get_archive(container_id, f"{directory}/{name}")
            except docker.errors.NotFound:
                out[name] = None
                continue
            with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as tar:
                member = tar.next()
                f = tar.extractfile(member) if member else None
                out[name] = f.read().decode("utf-8", errors="replace") if f else None
        return out

    def remove(self, container_id: str) -> None:
        self.api.remove_container(container_id, force=True)

    def create(self, spec: dict[str, Any]) -> str:
        host = self.api.create_host_config(
            binds={spec["volume"]: {"bind": spec["state_dir"], "mode": "rw"}},
            cap_drop=spec["cap_drop"], cap_add=spec["cap_add"] or None,
            security_opt=spec["security_opt"], mem_limit=spec["mem_limit"],
            restart_policy={"Name": "unless-stopped"},
            log_config={"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}},
        )  # fmt: skip
        net = self.api.create_networking_config(
            {spec["network"]: self.api.create_endpoint_config(ipv4_address=spec["ipv4_address"])}
        )
        created = self.api.create_container(
            spec["image"], command=spec["command"], name=spec["name"], user=spec["user"],
            environment=spec["environment"], labels=spec["labels"], host_config=host,
            networking_config=net, detach=True,
        )  # fmt: skip
        return created["Id"]

    def write_files(
        self, container_id: str, directory: str, files: dict[str, str], owner: tuple[int, int]
    ) -> None:  # noqa: E501
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for name, text in files.items():
                data = text.encode("utf-8")
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o600  # secrets: only the agent user
                info.uid, info.gid = owner
                tar.addfile(info, io.BytesIO(data))
        self.api.put_archive(container_id, directory, buf.getvalue())

    def start(self, container_id: str) -> None:
        self.api.start(container_id)

    def remove_volume(self, name: str) -> None:
        import docker.errors

        with contextlib.suppress(docker.errors.NotFound):
            self.client.volumes.get(name).remove(force=True)

    def reconfigure_proxy(self) -> None:
        filters = {
            "label": [
                f"com.docker.compose.project={self.settings.project}",
                f"com.docker.compose.service={self.settings.egress_service}",
            ]
        }
        for c in self.client.containers.list(filters=filters):
            c.exec_run(["squid", "-k", "reconfigure"])


def new_egress_lines(
    settings: NodeSettings, limit: int = EGRESS_CHUNK_BYTES, max_lines: int = EGRESS_LINES
) -> tuple[list[str], str]:
    """New node proxy log lines and the "inode:offset" position after them (save it after
    a successful send). Rotation (another inode or a shorter file) means reading from the start.
    One call stays within what the main node accepts per request; the rest goes next pass."""
    try:
        st = settings.egress_log.stat()
    except OSError:
        return [], ""
    try:
        inode, _, off = settings.egress_offset.read_text().strip().partition(":")
        offset = int(off) if inode == str(st.st_ino) else 0
    except OSError, ValueError:
        offset = 0
    if offset > st.st_size:
        offset = 0
    with settings.egress_log.open("rb") as f:
        f.seek(offset)
        chunk = f.read(limit)
    end = chunk.rfind(b"\n") + 1
    raw = chunk[:end].splitlines(keepends=True)[:max_lines]
    end = sum(len(line) for line in raw)
    lines = [line.decode(errors="replace").rstrip("\r\n") for line in raw]
    return lines, f"{st.st_ino}:{offset + end}"


async def run(settings: NodeSettings, manifest_path: Path) -> None:
    """Service loop: desired state → reconcile → report; an error in one pass does not crash the
    loop."""
    if not settings.token:
        raise SystemExit("YORATH_NODE_TOKEN is not set: the node service cannot reach the core")
    logging.getLogger("httpx").setLevel(
        logging.WARNING
    )  # polling every 5 s does not belong in the container log
    manifest = load_manifest(manifest_path)
    docker = DockerSdk(settings)
    headers = {"Authorization": f"Bearer {settings.token}"}
    async with httpx.AsyncClient(base_url=settings.core_url, headers=headers, timeout=30) as http:
        while True:
            try:
                await one_pass(http, docker, manifest, settings)
            except Exception:  # noqa: BLE001
                log.exception("node service pass failed")
            await asyncio.sleep(settings.interval_s)


TELEGRAM = TelegramChecks()


def telegram_checks(
    desired: dict[str, Any], reports: list[dict[str, Any]], checks: TelegramChecks
) -> None:
    """Adds the bot check to the report of each running agent with a bot (change telegram-check)."""
    deny = set(desired.get("deny", []))
    by_id = {r.get("deployment_id"): r for r in reports}
    for item in desired.get("agents", []):
        report = by_id.get(item["deployment_id"])
        if report is None or item.get("state") != "running":
            continue
        try:
            found = checks.result(item, item.get("address") in deny)
        except Exception:  # noqa: BLE001 — a failed check must not cost the heartbeat
            log.exception("agent %s: Telegram check failed", item["agent"])
            continue
        if found is not None:
            report["telegram"] = found


async def one_pass(
    http: httpx.AsyncClient, docker: Any, manifest: Any, settings: NodeSettings,
    checks: TelegramChecks = TELEGRAM,
) -> None:  # fmt: skip
    """Desired state → reconcile → bot checks → heartbeat with the report → proxy log lines."""
    r = await http.get("/node/desired")
    r.raise_for_status()
    desired = r.json()
    reports = await asyncio.to_thread(reconcile, desired, docker, manifest, settings)
    await asyncio.to_thread(telegram_checks, desired, reports, checks)
    report: dict[str, Any] = {"instances": reports}
    if (hardware := hardware_report(settings)) is not None:
        report["hardware"] = hardware
    if (host := host_report(settings)) is not None:
        report["host"] = host
    r = await http.post("/node/report", json=report)
    if r.status_code >= 300:
        # a refused heartbeat makes the node look lost on the main node: say why here (R3)
        log.error("heartbeat refused by the main node: %s %s", r.status_code, r.text[:300])
    if settings.ship_egress:
        lines, position = new_egress_lines(settings)
        if lines:
            r = await http.post("/node/egress", json={"lines": lines})
            r.raise_for_status()
        if position:
            settings.egress_offset.write_text(position)
