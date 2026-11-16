"""Agent ≠ Agent Revision ≠ Deployment ≠ Instance (invariant 1, decision 25)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from yorath.db import Base, CreatedAt, UUIDPk, enum_column


class Hosting(StrEnum):
    MANAGED = "managed"
    EXTERNAL = "external"


class AgentMode(StrEnum):
    DELEGATED = "delegated"
    AUTONOMOUS = "autonomous"


class Agent(UUIDPk, CreatedAt, Base):
    """A logical business function; the owner means responsibility, not authorization."""

    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(100), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.principal_id"))
    hosting: Mapped[Hosting] = mapped_column(enum_column(Hosting, "hosting"))
    mode: Mapped[AgentMode] = mapped_column(enum_column(AgentMode, "agent_mode"))
    runtime_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runtime_types.id"))
    enabled: Mapped[bool] = mapped_column(default=True, server_default=true())

    revisions: Mapped[list[AgentRevision]] = relationship(
        back_populates="agent", order_by="AgentRevision.revision_no"
    )


class AgentRevision(UUIDPk, CreatedAt, Base):
    """Snapshot of an md file from ``agents/``: path, sha256, parsed header. No text is stored."""

    __tablename__ = "agent_revisions"
    __table_args__ = (UniqueConstraint("agent_id", "revision_no"),)

    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"))
    revision_no: Mapped[int]
    path: Mapped[str] = mapped_column(String(500))
    content_sha256: Mapped[str] = mapped_column(String(64))
    front_matter: Mapped[dict[str, Any]] = mapped_column(JSONB)

    agent: Mapped[Agent] = relationship(back_populates="revisions")


class Deployment(UUIDPk, CreatedAt, Base):
    """Desired state: revision, cluster, instance count; not bound to a Runtime Installation."""

    __tablename__ = "deployments"

    agent_revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_revisions.id"))
    cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clusters.id"))
    desired_instances: Mapped[int] = mapped_column(default=1, server_default="1")
    budget: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(default=True, server_default=true())
    # connected agent (change connected-agents): internet, own version (None means the verified
    # one from the manifest), bot token sha256 (one bot, one agent), other channel settings
    network_enabled: Mapped[bool] = mapped_column(default=True, server_default=true())
    runtime_version: Mapped[str | None] = mapped_column(String(100))
    channel_hash: Mapped[str | None] = mapped_column(String(64))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # the node the agent runs on (change cluster-nodes); NULL means the main node
    node_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nodes.id"))

    agent_revision: Mapped[AgentRevision] = relationship()


class InstanceStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    LOST = "lost"  # node without a heartbeat (change cluster-nodes)


class Instance(UUIDPk, CreatedAt, Base):
    """One running process or container; the runtime installation is chosen here."""

    __tablename__ = "instances"

    deployment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deployments.id"))
    node_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nodes.id"))
    runtime_installation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runtime_installations.id")
    )
    status: Mapped[InstanceStatus] = mapped_column(enum_column(InstanceStatus, "instance_status"))
    external_ref: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_detail: Mapped[str | None] = mapped_column(String(500))
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # channel checks as the node sees them, {"telegram": {...}} (change telegram-check)
    channels: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    deployment: Mapped[Deployment] = relationship()
