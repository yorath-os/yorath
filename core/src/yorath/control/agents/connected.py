"""Connected agents (change connected-agents, decision 34): catalog, connection, node state.

The core is the only source of desired state: what runs, with which version, key, bot and
internet access. Containers are run by the node service ``node-agent`` (``yorath node-agent``):
it fetches ``desired_state`` and sends ``apply_report``. Secrets live in the built-in store; the
database keeps only the bot token hash (uniqueness: one bot, one agent).
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.cluster.service import NodeHardware
from yorath.control.identity.api_keys import issue_api_key, revoke_api_key
from yorath.control.journal import AuditEventType, Journal
from yorath.control.journal.egress import protected
from yorath.control.model import (
    Agent,
    AgentRevision,
    ApiKey,
    Cluster,
    Deployment,
    Instance,
    InstanceStatus,
    ModelResource,
    ModelServer,
    Node,
    RuntimeInstallation,
    RuntimeType,
    ServiceAccount,
    User,
)
from yorath.distribution.manifest import ConnectedAgentSpec, Manifest
from yorath.enforcement.secrets import SecretStore
from yorath.runtime.connected.config import BUILDERS, MODEL, AgentConfig
from yorath.runtime.connected.limits import HOST_AGENTS, HOST_CONTAINERS, HOST_TRACES

SUBNET = "172.31.0"
SELF_SCOPE = "agent_self"


def address(spec: ConnectedAgentSpec) -> str:
    return f"{SUBNET}.{spec.address}"


def secret_name(agent: str, kind: str) -> str:
    """``model_key``: the agent's model key, ``entry_token``: its entry token, ``telegram``: bot."""
    return f"connected/{agent}/{kind}"


class ConnectedError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


class ConnectIn(BaseModel):
    version: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    node: str | None = Field(
        default=None, max_length=63, description="cluster node; None means the main node"
    )
    telegram_token: str | None = Field(default=None, max_length=200)
    telegram_users: list[str] = Field(default=[], max_length=50)


class ChannelIn(BaseModel):
    telegram_token: str | None = Field(default=None, max_length=200, description="None disables it")
    telegram_users: list[str] = Field(default=[], max_length=50)


class ConnectedOut(BaseModel):
    name: str
    description: str | None
    license: str
    homepage: str
    tested_version: str
    min_context: int
    connected: bool
    enabled: bool
    version: str | None  # the owner's own version; None means the verified one
    untested: bool
    network_enabled: bool
    telegram: bool
    telegram_users: list[str]
    # the bot as the agent's node sees it: state ok | unreachable | rejected | internet_off, bot,
    # detail, checked_at; None until the first check (change telegram-check)
    telegram_check: dict[str, Any] | None = None
    node: str | None  # cluster node (change cluster-nodes); None means the main node
    status: str | None  # Instance: starting | running | stopped | failed | lost
    status_detail: str | None
    running_version: str | None
    running_digest: str | None
    reported_at: datetime | None


@dataclass
class Found:
    spec: ConnectedAgentSpec
    agent: Agent
    deployment: Deployment | None


def _spec(manifest: Manifest, name: str) -> ConnectedAgentSpec:
    spec = manifest.connected_agents.get(name)
    if spec is None:
        known = ", ".join(sorted(manifest.connected_agents)) or "none"
        raise ConnectedError(
            404,
            "unknown_connected_agent",
            f"agent {name!r} is not in the catalog; available: {known}",
        )
    return spec


async def _deployment(session: AsyncSession, agent: Agent) -> Deployment | None:
    return await session.scalar(
        select(Deployment)
        .join(AgentRevision, AgentRevision.id == Deployment.agent_revision_id)
        .where(AgentRevision.agent_id == agent.id)
        .order_by(Deployment.created_at.desc())
        .limit(1)
    )


async def _find(session: AsyncSession, manifest: Manifest, name: str) -> Found:
    spec = _spec(manifest, name)
    agent = await session.scalar(select(Agent).where(Agent.name == name))
    if agent is None:
        raise ConnectedError(503, "catalog_not_loaded", f"card {name} is not loaded by the core")
    return Found(spec, agent, await _deployment(session, agent))


async def _target_node(session: AsyncSession, name: str) -> Node | None:
    node = await session.scalar(select(Node).where(Node.name == name))
    if node is None:
        raise ConnectedError(
            404, "node_not_found", f"node {name} does not exist: see System → Nodes"
        )
    if node.role == "main":
        return None
    if node.status != "active":
        raise ConnectedError(409, "node_not_active", f"node {name} is {node.status}: approve it")
    return node


def _bot_hash(token: str) -> str:
    return hashlib.sha256(token.strip().encode()).hexdigest()


async def _check_bot(session: AsyncSession, agent: Agent, token: str) -> str:
    digest = _bot_hash(token)
    other = await session.scalar(
        select(Agent.name)
        .join(AgentRevision, AgentRevision.agent_id == Agent.id)
        .join(Deployment, Deployment.agent_revision_id == AgentRevision.id)
        .where(Deployment.channel_hash == digest, Agent.id != agent.id)
        .limit(1)
    )
    if other:
        raise ConnectedError(409, "bot_in_use", f"this bot is already connected to agent {other}")
    return digest


async def _revoke_self_keys(
    session: AsyncSession, journal: Journal | None, actor: uuid.UUID, agent: Agent
) -> None:
    keys = await session.scalars(
        select(ApiKey).where(
            ApiKey.agent_id == agent.id, ApiKey.scope == SELF_SCOPE, ApiKey.revoked_at.is_(None)
        )
    )
    for key in keys.all():
        await revoke_api_key(session, journal, actor, key)


async def _service_account(session: AsyncSession, agent: Agent, owner: User) -> None:
    if await session.scalar(select(ServiceAccount).where(ServiceAccount.agent_id == agent.id)):
        return
    session.add(
        ServiceAccount(display_name=f"{agent.name}-svc", owner_user_id=owner.id, agent_id=agent.id)
    )


async def _gpu_server(session: AsyncSession) -> str | None:
    """A GPU model server of an active node, as node/server, for the hint of a refusal."""
    row = (
        await session.execute(
            select(Node.name, ModelServer.name)
            .join(Node, Node.id == ModelServer.node_id)
            .where(Node.status == "active", func.cardinality(ModelServer.gpu_indexes) > 0)
            .order_by(Node.name, ModelServer.name)
            .limit(1)
        )
    ).first()
    return f"{row[0]}/{row[1]}" if row else None


async def _window(session: AsyncSession) -> int:
    resource = await session.scalar(select(ModelResource).where(ModelResource.name == MODEL))
    return resource.context_length if resource else 0


async def connect(
    session: AsyncSession,
    journal: Journal,
    store: SecretStore,
    manifest: Manifest,
    report: dict[str, Any],
    owner: User,
    name: str,
    data: ConnectIn,
) -> None:
    """Enables the agent, creates a Deployment, issues an ``agent_self`` key and an entry token."""
    found = await _find(session, manifest, name)
    target = await _target_node(session, data.node) if data.node else None
    # joining (install.sh --join) turns on network control on a worker node; the main node by report
    if target is None and protected(report) is not True:
        raise ConnectedError(
            409, "egress_off",
            "network control is off on the node: without it the agent has direct internet access. "
            "Turn it on: yorath egress on",
        )  # fmt: skip
    window = await _window(session)
    if window < found.spec.min_context:
        need = (
            f"the agent needs a {MODEL} context window of {found.spec.min_context}+ tokens, "
            f"currently {window}. "
        )
        gpu = await _gpu_server(session)
        raise ConnectedError(
            409, "context_too_small",
            need + (
                f"A GPU is in the cluster: install a model on {gpu} (Models → Install a model, "
                "or yorath model pull <name> --node …) and make it the default model"
                if gpu else "Use a node with a GPU (window 65,536) or a larger model window"
            ),
        )  # fmt: skip
    if not getattr(store, "available", True):
        raise ConnectedError(503, "secrets_unavailable", "the secret store is unavailable")
    agent = found.agent
    deployment = found.deployment
    if data.telegram_token:
        await _check_bot(session, agent, data.telegram_token)
    revision = await session.scalar(
        select(AgentRevision)
        .where(AgentRevision.agent_id == agent.id)
        .order_by(AgentRevision.revision_no.desc())
        .limit(1)
    )
    assert revision is not None
    cluster = await session.scalar(select(Cluster).order_by(Cluster.created_at).limit(1))
    if cluster is None:
        raise ConnectedError(
            503, "no_cluster", "the installation cluster is not created (yorath bootstrap)"
        )
    if deployment is not None and deployment.settings.get("purge"):
        raise ConnectedError(
            409, "agent_removing", "the agent is still being removed on the node; retry in a minute"
        )
    fresh = deployment is None or bool(deployment.settings.get("removed"))
    if deployment is None:
        deployment = Deployment(agent_revision_id=revision.id, cluster_id=cluster.id)
        session.add(deployment)
    # reconnecting keeps the previous version, bot and internet access unless given;
    # after removal the agent connects from a clean slate
    if fresh:
        deployment.runtime_version, deployment.channel_hash = None, None
        deployment.settings, deployment.network_enabled = {}, True
    deployment.agent_revision_id = revision.id
    deployment.enabled = True
    deployment.desired_instances = 1
    if data.version is not None:
        deployment.runtime_version = data.version
    if data.node is not None or fresh:
        deployment.node_id = target.id if target else None
    if data.telegram_token:
        deployment.channel_hash = _bot_hash(data.telegram_token)
        deployment.settings = {**deployment.settings, "telegram_users": list(data.telegram_users)}
    agent.enabled = True
    agent.owner_id = agent.owner_id or owner.id
    await _service_account(session, agent, owner)
    await session.commit()
    await _revoke_self_keys(session, journal, owner.id, agent)
    _, token = await issue_api_key(session, journal, owner, f"agent:{name}", agent, SELF_SCOPE)
    await store.put(secret_name(name, "model_key"), token)
    if await store.get(secret_name(name, "entry_token")) is None:
        await store.put(secret_name(name, "entry_token"), secrets.token_urlsafe(32))
    if data.telegram_token:
        await store.put(secret_name(name, "telegram"), data.telegram_token.strip())
    elif fresh:
        await store.delete(secret_name(name, "telegram"))
    await journal.audit(
        AuditEventType.AGENT_CONNECTED,
        actor_principal_id=owner.id,
        agent_id=agent.id,
        details={"agent": name, "version": deployment.runtime_version or found.spec.version,
                 "untested": deployment.runtime_version is not None,
                 "telegram": deployment.channel_hash is not None},
    )  # fmt: skip


async def disconnect(
    session: AsyncSession, journal: Journal, store: SecretStore, manifest: Manifest,
    owner: User, name: str, purge: bool = False,
) -> None:  # fmt: skip
    """Disable: the key is revoked, the container stops. ``purge`` wipes the volume and secrets."""
    found = await _find(session, manifest, name)
    agent = found.agent
    agent.enabled = False
    if found.deployment is not None:
        found.deployment.enabled = False
        if purge:
            found.deployment.desired_instances = 0
            found.deployment.channel_hash = None
            found.deployment.settings = {**found.deployment.settings, "purge": True}
    await session.commit()
    await _revoke_self_keys(session, journal, owner.id, agent)
    await store.delete(secret_name(name, "model_key"))
    if purge:
        await store.delete(secret_name(name, "entry_token"))
        await store.delete(secret_name(name, "telegram"))
    await journal.audit(
        AuditEventType.AGENT_REMOVED if purge else AuditEventType.AGENT_DISCONNECTED,
        actor_principal_id=owner.id,
        agent_id=agent.id,
        details={"agent": name},
    )


async def _connected(session: AsyncSession, manifest: Manifest, name: str) -> Found:
    found = await _find(session, manifest, name)
    if found.deployment is None or not found.deployment.enabled:
        raise ConnectedError(409, "not_connected", f"agent {name} is not connected")
    return found


async def set_network(
    session: AsyncSession, journal: Journal, manifest: Manifest, owner: User, name: str, on: bool
) -> None:
    found = await _connected(session, manifest, name)
    assert found.deployment is not None
    found.deployment.network_enabled = on
    await session.commit()
    await journal.audit(
        AuditEventType.AGENT_NETWORK_CHANGED,
        actor_principal_id=owner.id,
        agent_id=found.agent.id,
        details={"agent": name, "network": on},
    )


async def set_channel(
    session: AsyncSession, journal: Journal, store: SecretStore, manifest: Manifest,
    owner: User, name: str, data: ChannelIn,
) -> None:  # fmt: skip
    """Telegram bot: set, change or disconnect (``telegram_token = None``)."""
    found = await _connected(session, manifest, name)
    deployment = found.deployment
    assert deployment is not None
    if data.telegram_token:
        deployment.channel_hash = await _check_bot(session, found.agent, data.telegram_token)
        await store.put(secret_name(name, "telegram"), data.telegram_token.strip())
    else:
        deployment.channel_hash = None
        await store.delete(secret_name(name, "telegram"))
    deployment.settings = {**deployment.settings, "telegram_users": list(data.telegram_users)}
    await _recheck_telegram(session, deployment)
    await session.commit()
    await journal.audit(
        AuditEventType.AGENT_CHANNEL_CHANGED,
        actor_principal_id=owner.id,
        agent_id=found.agent.id,
        details={"agent": name, "telegram": bool(data.telegram_token),
                 "users": len(data.telegram_users)},
    )  # fmt: skip


async def _recheck_telegram(session: AsyncSession, deployment: Deployment) -> None:
    """The node checks the bot on its next pass; the old result must not stand for a new token."""
    deployment.settings = {**deployment.settings, "telegram_check": secrets.token_hex(4)}
    instance = await _instance(session, deployment)
    if instance is not None:
        instance.channels = None


async def check_telegram(session: AsyncSession, manifest: Manifest, name: str) -> None:
    """The owner's "Check again": the agent's node asks Telegram again on its next pass."""
    found = await _connected(session, manifest, name)
    assert found.deployment is not None
    if not found.deployment.channel_hash:
        raise ConnectedError(409, "no_bot", "the agent has no Telegram bot")
    await _recheck_telegram(session, found.deployment)
    await session.commit()


async def set_version(
    session: AsyncSession, journal: Journal, manifest: Manifest, owner: User, name: str,
    version: str | None,
) -> None:  # fmt: skip
    """Own version or back to the verified one (``None``); ``latest`` is downloaded again."""
    found = await _connected(session, manifest, name)
    deployment = found.deployment
    assert deployment is not None
    deployment.runtime_version = version
    deployment.settings = {**deployment.settings, "pull_nonce": secrets.token_hex(4)}
    await session.commit()
    await journal.audit(
        AuditEventType.AGENT_VERSION_CHANGED,
        actor_principal_id=owner.id,
        agent_id=found.agent.id,
        details={"agent": name, "version": version or found.spec.version,
                 "untested": version is not None},
    )  # fmt: skip


async def _instance(session: AsyncSession, deployment: Deployment) -> Instance | None:
    return await session.scalar(
        select(Instance)
        .where(Instance.deployment_id == deployment.id)
        .order_by(Instance.created_at.desc())
        .limit(1)
    )


async def catalog(session: AsyncSession, manifest: Manifest) -> list[ConnectedOut]:
    out: list[ConnectedOut] = []
    for name, spec in sorted(manifest.connected_agents.items()):
        agent = await session.scalar(select(Agent).where(Agent.name == name))
        deployment = await _deployment(session, agent) if agent else None
        instance = await _instance(session, deployment) if deployment else None
        installation = (
            await session.get(RuntimeInstallation, instance.runtime_installation_id)
            if instance and instance.runtime_installation_id
            else None
        )
        live = deployment is not None and deployment.enabled
        node = (
            await session.get(Node, deployment.node_id)
            if deployment and deployment.node_id
            else None
        )
        out.append(
            ConnectedOut(
                name=name,
                description=agent.description if agent else None,
                license=spec.license,
                homepage=spec.homepage,
                tested_version=spec.version,
                min_context=spec.min_context,
                connected=live,
                enabled=bool(agent and agent.enabled),
                version=deployment.runtime_version if deployment else None,
                untested=bool(deployment and deployment.runtime_version),
                network_enabled=deployment.network_enabled if deployment else True,
                node=node.name if node else None,
                telegram=bool(deployment and deployment.channel_hash),
                telegram_users=deployment.settings.get("telegram_users", []) if deployment else [],
                telegram_check=(
                    (instance.channels or {}).get("telegram")
                    if instance and live and deployment and deployment.channel_hash
                    else None
                ),
                status=instance.status.value if instance and live else None,
                status_detail=instance.status_detail if instance and live else None,
                running_version=installation.version if installation else None,
                running_digest=installation.path if installation else None,
                reported_at=instance.reported_at if instance else None,
            )
        )
    return out


# ---- node service --------------------------------------------------------------------------


def _on_node(deployment: Deployment, node: Node | None) -> bool:
    """This node's Deployments: on the main node, no node or its own id; on a worker, its own id."""
    if node is None or node.role == "main":
        return deployment.node_id is None or (node is not None and deployment.node_id == node.id)
    return deployment.node_id == node.id


async def desired_state(
    session: AsyncSession, store: SecretStore, manifest: Manifest, node: Node | None = None
) -> dict[str, Any]:
    """What should run on the node: containers, their config and per-address network bans."""
    agents: list[dict[str, Any]] = []
    deny: list[str] = []
    window = await _window(session)
    for name, spec in sorted(manifest.connected_agents.items()):
        agent = await session.scalar(select(Agent).where(Agent.name == name))
        deployment = await _deployment(session, agent) if agent else None
        if agent is None or deployment is None or not _on_node(deployment, node):
            continue
        item: dict[str, Any] = {
            "agent": name,
            "deployment_id": str(deployment.id),
            "address": address(spec),
            "telegram_check": deployment.settings.get("telegram_check", ""),
        }
        if deployment.settings.get("purge"):
            agents.append(item | {"state": "purge"})
            continue
        if not (deployment.enabled and agent.enabled):
            agents.append(item | {"state": "stopped"})
            continue
        key = await store.get(secret_name(name, "model_key"))
        entry = await store.get(secret_name(name, "entry_token"))
        if key is None or entry is None:
            detail = "no agent key: connect again"
            agents.append(item | {"state": "stopped", "detail": detail})
            continue
        config: AgentConfig = BUILDERS[name](  # card runtime = catalog agent name
            key, entry, max(window, spec.min_context), spec.port,
            await store.get(secret_name(name, "telegram")),
            deployment.settings.get("telegram_users") or [],
        )  # fmt: skip
        agents.append(
            item
            | {
                "state": "running",
                "runtime": config.runtime,
                "image": spec.image(deployment.runtime_version),
                "repository": spec.repository,
                "pull_nonce": deployment.settings.get("pull_nonce", ""),
                "port": spec.port,
                "health_path": spec.health_path,
                "user": spec.user,
                "cap_add": spec.cap_add,
                "memory_mb": spec.memory_mb,
                "command": spec.command,
                "state_dir": spec.state_dir,
                "config": {"managed": config.managed, "env": config.env, "dotenv": config.dotenv},
                "config_sha": config.sha(),
            }  # fmt: skip
        )
        if not deployment.network_enabled:
            deny.append(address(spec))
    return {"agents": agents, "deny": deny}


class InstanceReport(BaseModel):
    deployment_id: uuid.UUID
    state: str = Field(pattern=r"^(starting|running|stopped|failed|purged)$")
    container_id: str | None = Field(default=None, max_length=200)
    detail: str | None = Field(default=None, max_length=500)
    version: str | None = Field(default=None, max_length=100)
    digest: str | None = Field(default=None, max_length=100)
    telegram: TelegramCheck | None = None


def _cut(n: int):
    """A host report text field: cut to ``n`` characters instead of refusing the heartbeat."""
    return BeforeValidator(lambda v: v if v is None else str(v)[:n])


class FoundAgent(BaseModel):
    """One agent outside Yorath; several processes of one agent are one entry with ``processes``."""

    agent: Annotated[str, _cut(40)]
    where: Literal["host", "container"]
    container: Annotated[str | None, _cut(200)] = None
    image: Annotated[str | None, _cut(300)] = None
    pid: int | None = None
    user: Annotated[str | None, _cut(64)] = None
    root: bool = False
    command: Annotated[str | None, _cut(200)] = None
    processes: int = Field(default=1, ge=1)


class FoundTrace(BaseModel):
    agent: Annotated[str, _cut(40)]
    path: Annotated[str, _cut(300)]
    owner: Annotated[str, _cut(64)] = "?"


class OtherContainer(BaseModel):
    name: Annotated[str, _cut(200)]
    image: Annotated[str, _cut(300)] = ""
    project: Annotated[str, _cut(200)] = ""


class HostReport(BaseModel):
    """The node's "yorath scan" report (decision 36): agents and their traces outside Yorath.

    Over the limits the lists are cut and ``truncated`` is set: one oversized scan must not refuse
    the whole heartbeat, or the node would look lost (pre-public-hardening R3).
    """

    checked_at: Annotated[str, _cut(40)]
    agents: list[FoundAgent] = []
    traces: list[FoundTrace] = []
    containers: list[OtherContainer] = []
    truncated: bool = False

    @model_validator(mode="before")
    @classmethod
    def _cap(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out, cut = dict(data), bool(data.get("truncated"))
        for key, limit in (("agents", HOST_AGENTS), ("traces", HOST_TRACES),
                           ("containers", HOST_CONTAINERS)):  # fmt: skip
            items = out.get(key)
            if isinstance(items, list) and len(items) > limit:
                out[key], cut = items[:limit], True
        out["truncated"] = cut
        return out


class TelegramCheck(BaseModel):
    """The node's ``getMe`` result for the agent's bot; never the token (change telegram-check)."""

    state: Literal["ok", "unreachable", "rejected", "internet_off"]
    bot: Annotated[str | None, _cut(64)] = None
    detail: Annotated[str | None, _cut(100)] = None
    checked_at: Annotated[str, _cut(40)]


InstanceReport.model_rebuild()  # its ``telegram`` field is declared above TelegramCheck


class NodeReport(BaseModel):
    instances: list[InstanceReport] = []
    hardware: NodeHardware | None = None  # the node's cards (change model-placements)
    host: HostReport | None = None  # agents outside Yorath on the node (outside-agents-cluster)


async def _installation(
    session: AsyncSession, runtime: str, version: str, digest: str | None, node: Node | None
) -> RuntimeInstallation | None:
    rtype = await session.scalar(select(RuntimeType).where(RuntimeType.name == runtime))
    if rtype is None or node is None:
        return None
    found = await session.scalar(
        select(RuntimeInstallation).where(
            RuntimeInstallation.runtime_type_id == rtype.id,
            RuntimeInstallation.node_id == node.id,
            RuntimeInstallation.version == version,
            RuntimeInstallation.path == digest,
        )
    )
    if found is None:
        found = RuntimeInstallation(
            runtime_type_id=rtype.id, node_id=node.id, version=version, path=digest
        )
        session.add(found)
    found.probed_at = datetime.now(UTC)
    return found


async def apply_report(session: AsyncSession, report: NodeReport, node: Node | None = None) -> None:
    """Node service report → Instances of connected agents (one row per Deployment).

    A node reports only on its own Deployments: other nodes' rows in the report are ignored.
    """
    now = datetime.now(UTC)
    if node is None:
        node = await session.scalar(select(Node).order_by(Node.created_at).limit(1))
    if (
        report.host is not None
        and node is not None
        and ((node.host_report or {}).get("checked_at") != report.host.checked_at)
    ):  # the same report comes with every heartbeat: write only a new scan
        node.host_report = report.host.model_dump(exclude_none=True)
    for item in report.instances:
        deployment = await session.get(Deployment, item.deployment_id)
        if deployment is None or not _on_node(deployment, node):
            continue
        if item.state == "purged":
            deployment.settings = {"removed": True}  # volume wiped: reconnect from a clean slate
            item = item.model_copy(update={"state": "stopped"})
        instance = await _instance(session, deployment)
        if instance is None:
            instance = Instance(
                deployment_id=deployment.id,
                node_id=node.id if node else None,
                status=InstanceStatus(item.state),
            )
            session.add(instance)
        instance.node_id = node.id if node else None
        status = InstanceStatus(item.state)
        if status is InstanceStatus.RUNNING and instance.status is not InstanceStatus.RUNNING:
            instance.started_at = now
        if status is InstanceStatus.STOPPED and instance.status is not InstanceStatus.STOPPED:
            instance.stopped_at = now
        instance.status = status
        instance.status_detail = item.detail
        instance.external_ref = item.container_id
        instance.reported_at = now
        instance.channels = {"telegram": item.telegram.model_dump()} if item.telegram else None
        if item.version:
            agent = await session.scalar(
                select(Agent)
                .join(AgentRevision, AgentRevision.agent_id == Agent.id)
                .where(AgentRevision.id == deployment.agent_revision_id)
            )
            if agent is not None:
                installation = await _installation(
                    session, agent.name, item.version, item.digest, node
                )
                if installation is not None:
                    await session.flush()
                    instance.runtime_installation_id = installation.id
    await session.commit()


def connected_addresses(manifest: Manifest) -> dict[str, str]:
    """IP → agent name for the network journal: addresses are fixed, from the catalog."""
    return {address(spec): name for name, spec in manifest.connected_agents.items()}
