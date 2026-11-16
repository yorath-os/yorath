"""Journal tables: ``trace_spans`` (OTel) and ``audit_events`` (append-only)."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yorath.control.journal.events import AuditEventType, ControlLevel, Decision
from yorath.control.model.governance import DataClass
from yorath.db import Base, UUIDPk, enum_column, utcnow


class SpanKind(StrEnum):
    EXECUTION = "execution"
    LLM = "llm"
    TOOL = "tool"
    DATA = "data"
    AGENT = "agent"
    RUNTIME = "runtime"


class SpanStatus(StrEnum):
    UNSET = "unset"
    OK = "ok"
    ERROR = "error"


class TraceSpan(UUIDPk, Base):
    """An OTel-model span; ``trace_id`` = Execution id; extra columns serve UI filters."""

    __tablename__ = "trace_spans"
    __table_args__ = (
        Index("ix_trace_spans_execution_started", "execution_id", "started_at"),
        Index("ix_trace_spans_started_at", "started_at"),
    )

    trace_id: Mapped[uuid.UUID]
    span_id: Mapped[str] = mapped_column(String(16))
    parent_span_id: Mapped[str | None] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[SpanKind] = mapped_column(enum_column(SpanKind, "span_kind"))
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("executions.id"))
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("execution_attempts.id"))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    model: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[SpanStatus] = mapped_column(
        enum_column(SpanStatus, "span_status"), default=SpanStatus.UNSET
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="raw runtime event")


class EgressFlow(UUIDPk, Base):
    """Hourly node network summary (change node-egress): source → domain. Kept like traces."""

    __tablename__ = "egress_flows"
    __table_args__ = (
        UniqueConstraint("hour", "node", "source_ip", "domain"),
        Index("ix_egress_flows_hour", "hour"),
    )

    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    node: Mapped[str] = mapped_column(String(100))
    source_ip: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(200))  # a container, "host" or an IP
    domain: Mapped[str] = mapped_column(String(300))
    requests: Mapped[int] = mapped_column(Integer, default=0)
    blocked: Mapped[int] = mapped_column(Integer, default=0)
    bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    first_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEvent(UUIDPk, Base):
    """Who, through which agent, to what, by which decision: no prompts.

    Only ``execution_id`` is a foreign key; other identifiers are plain,
    so the audit log survives any cleanup. UPDATE/DELETE are forbidden by a trigger.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_occurred_at", "occurred_at"),
        Index("ix_audit_events_execution_id", "execution_id"),
        Index("ix_audit_events_decision", "decision"),
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    event_type: Mapped[AuditEventType] = mapped_column(
        enum_column(AuditEventType, "audit_event_type")
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("executions.id"))
    span_id: Mapped[str | None] = mapped_column(String(16))
    actor_principal_id: Mapped[uuid.UUID | None]
    effective_principal_id: Mapped[uuid.UUID | None]
    agent_id: Mapped[uuid.UUID | None]
    agent_revision_id: Mapped[uuid.UUID | None]
    runtime_type: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(200))
    tool: Mapped[str | None] = mapped_column(String(100))
    data_asset_id: Mapped[uuid.UUID | None]
    data_class: Mapped[DataClass | None] = mapped_column(enum_column(DataClass, "data_class"))
    decision: Mapped[Decision | None] = mapped_column(enum_column(Decision, "decision"))
    reason: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    control_level: Mapped[ControlLevel | None] = mapped_column(
        enum_column(ControlLevel, "control_level")
    )
    parent_execution_id: Mapped[uuid.UUID | None]
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
