"""Cluster, nodes, runtimes and models as resources (invariants 5, 6, 8)."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, false, true
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from yorath.db import Base, CreatedAt, UUIDPk, enum_column

ALL_DATA_CLASSES = ("public", "internal", "confidential", "restricted")


class Cluster(UUIDPk, CreatedAt, Base):
    """One ``local`` row in the MVP; every Deployment must reference it."""

    __tablename__ = "clusters"

    name: Mapped[str] = mapped_column(String(100), unique=True)


class Node(UUIDPk, CreatedAt, Base):
    """Node inventory (decision 23): filled in by the installer."""

    __tablename__ = "nodes"

    cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clusters.id"))
    name: Mapped[str] = mapped_column(String(100))
    cpu_count: Mapped[int]
    ram_gb: Mapped[int]
    gpu: Mapped[str | None] = mapped_column(String(100))
    vram_gb: Mapped[int | None]
    gpu_count: Mapped[int] = mapped_column(default=0, server_default="0")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # cluster (change cluster-nodes): main or worker node, WireGuard tunnel, heartbeat
    role: Mapped[str] = mapped_column(String(16), default="main", server_default="main")
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    overlay_ip: Mapped[str | None] = mapped_column(String(45))
    wg_public_key: Mapped[str | None] = mapped_column(String(64))
    token_hash: Mapped[str | None] = mapped_column(String(64))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    has_model: Mapped[bool] = mapped_column(default=False, server_default=false())
    # data classes that may be sent to this node (decision 44); checked when a model is placed
    allowed_data_classes: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)),
        default=lambda: list(ALL_DATA_CLASSES),
        server_default="{public,internal,confidential,restricted}",
    )
    # the last "yorath scan" report of a worker node: agents outside Yorath (outside-agents-cluster)
    host_report: Mapped[dict | None] = mapped_column(JSONB)

    cluster: Mapped[Cluster] = relationship()


class RuntimeType(UUIDPk, Base):
    """Technology: ``native``, ``codex``, ``openclaw`` (reference table, filled by a migration)."""

    __tablename__ = "runtime_types"

    name: Mapped[str] = mapped_column(String(50), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))


class RuntimeInstallation(UUIDPk, CreatedAt, Base):
    """A specific installed copy of a runtime on a node (spike runtime-adapter-contract §4)."""

    __tablename__ = "runtime_installations"

    runtime_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runtime_types.id"))
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nodes.id"))
    version: Mapped[str] = mapped_column(String(50))
    path: Mapped[str | None] = mapped_column(String(500))
    probed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    streams_events: Mapped[bool] = mapped_column(default=False, server_default=false())

    runtime_type: Mapped[RuntimeType] = relationship()
    node: Mapped[Node] = relationship()


class ModelProvider(StrEnum):
    OLLAMA = "ollama"
    OPENROUTER = "openrouter"


class ModelResource(UUIDPk, CreatedAt, Base):
    """A model as a resource with allowed data classes (§6 "Models as resources")."""

    __tablename__ = "model_resources"

    name: Mapped[str] = mapped_column(String(100), unique=True)
    provider: Mapped[ModelProvider] = mapped_column(enum_column(ModelProvider, "model_provider"))
    model_id: Mapped[str] = mapped_column(String(200))
    allowed_data_classes: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    input_price_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    output_price_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    is_default: Mapped[bool] = mapped_column(default=False, server_default=false())
    enabled: Mapped[bool] = mapped_column(default=True, server_default=true())
    context_length: Mapped[int] = mapped_column(default=4096, server_default="4096")  # num_ctx
    # ``local/default`` points at a catalog model (decision 44); its model_id, window and classes
    # mirror the target so that readers of ``local/default`` need no join
    alias_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_resources.id"))


class ModelServer(UUIDPk, CreatedAt, Base):
    """One Ollama on a node with the GPUs pinned to it (decision 44): the unit of capacity.

    ``ollama`` is the main node's Ollama, ``gpu<N>`` a worker's Ollama on card N, ``cpu`` a
    worker's Ollama without a GPU. The slot belongs to the server, not to a placement.
    """

    __tablename__ = "model_servers"
    __table_args__ = (UniqueConstraint("node_id", "name"),)

    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nodes.id"))
    name: Mapped[str] = mapped_column(String(32))
    gpu_indexes: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    slots: Mapped[int] = mapped_column(default=1, server_default="1")

    node: Mapped[Node] = relationship()


class PlacementStatus(StrEnum):
    PULLING = "pulling"
    READY = "ready"
    FAILED = "failed"


class ModelPlacement(UUIDPk, CreatedAt, Base):
    """A model on a model server: desired state declared on the main node (decision 44)."""

    __tablename__ = "model_placements"
    __table_args__ = (UniqueConstraint("model_resource_id", "model_server_id"),)

    model_resource_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_resources.id"))
    model_server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_servers.id"))
    status: Mapped[PlacementStatus] = mapped_column(
        enum_column(PlacementStatus, "placement_status"), default=PlacementStatus.PULLING
    )
    status_detail: Mapped[str | None] = mapped_column(String(500))
    progress: Mapped[int] = mapped_column(default=0, server_default="0")  # pull percent
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    model: Mapped[ModelResource] = relationship()
    server: Mapped[ModelServer] = relationship()


class ClusterJoinToken(UUIDPk, CreatedAt, Base):
    """One-time node join token (change cluster-nodes): only sha256 is stored."""

    __tablename__ = "cluster_join_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[uuid.UUID | None]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    node_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nodes.id"))
