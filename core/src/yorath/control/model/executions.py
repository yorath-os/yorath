"""Execution ≠ attempt ≠ Outcome (decisions 26, 29; spike runtime-adapter-contract §3.4, §4)."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from yorath.control.model.governance import DataClass
from yorath.db import Base, CreatedAt, UUIDPk, enum_column


class ExecutionTrigger(StrEnum):
    USER_MESSAGE = "user_message"
    AGENT_CALL = "agent_call"
    EVENT = "event"
    SCHEDULE = "schedule"
    API = "api"  # an external client call through the model endpoint


class ExecutionStatus(StrEnum):
    CREATED = "created"
    DENIED = "denied"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CONFIG_ERROR = "config_error"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset(
    {
        ExecutionStatus.DENIED,
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMEOUT,
        ExecutionStatus.CONFIG_ERROR,
        ExecutionStatus.CANCELLED,
    }
)


class Execution(UUIDPk, CreatedAt, Base):
    """One business task. The Actor is the agent revision; both principals are foreign keys."""

    __tablename__ = "executions"

    agent_revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_revisions.id"))
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deployments.id"))
    initiated_by_principal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("principals.id"))
    effective_principal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("principals.id"))
    parent_execution_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("executions.id"))
    trigger: Mapped[ExecutionTrigger] = mapped_column(
        enum_column(ExecutionTrigger, "execution_trigger")
    )
    status: Mapped[ExecutionStatus] = mapped_column(
        enum_column(ExecutionStatus, "execution_status"), default=ExecutionStatus.CREATED
    )
    max_data_class_seen: Mapped[DataClass | None] = mapped_column(
        enum_column(DataClass, "data_class")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # the API key the call came with (/v1): a person has several: Hermes, Codex, a bot
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("api_keys.id"))

    parent: Mapped[Execution | None] = relationship(remote_side="Execution.id")
    attempts: Mapped[list[ExecutionAttempt]] = relationship(
        back_populates="execution", order_by="ExecutionAttempt.attempt_no"
    )
    outcomes: Mapped[list[Outcome]] = relationship(back_populates="execution")


class AttemptStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CONFIG_ERROR = "config_error"
    CANCELLED = "cancelled"


class ExecutionAttempt(UUIDPk, CreatedAt, Base):
    """One attempt on one executor. Exactly one in the MVP; the schema allows several."""

    __tablename__ = "execution_attempts"
    __table_args__ = (UniqueConstraint("execution_id", "attempt_no"),)

    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("executions.id"))
    attempt_no: Mapped[int] = mapped_column(default=1, server_default="1")
    instance_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("instances.id"))
    runtime_installation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runtime_installations.id")
    )
    model_resource_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_resources.id"))
    model: Mapped[str | None] = mapped_column(String(200), comment="actual model")
    runtime_session_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[AttemptStatus] = mapped_column(
        enum_column(AttemptStatus, "attempt_status"), default=AttemptStatus.RUNNING
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_code: Mapped[int | None]
    error_message: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cost_estimated: Mapped[bool] = mapped_column(default=False, server_default=false())

    execution: Mapped[Execution] = relationship(back_populates="attempts")


class OutcomeKind(StrEnum):
    PULL_REQUEST = "pull_request"
    ANSWER = "answer"


class OutcomeStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MERGED = "merged"


class Outcome(UUIDPk, CreatedAt, Base):
    """A confirmed result; "Completed" on an Execution does not mean an Outcome."""

    __tablename__ = "outcomes"

    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("executions.id"))
    kind: Mapped[OutcomeKind] = mapped_column(enum_column(OutcomeKind, "outcome_kind"))
    status: Mapped[OutcomeStatus] = mapped_column(
        enum_column(OutcomeStatus, "outcome_status"), default=OutcomeStatus.PENDING
    )
    ref: Mapped[str | None] = mapped_column(String(500), comment="PR URL, answer id")
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.principal_id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    useful: Mapped[bool | None]
    rated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.principal_id"))

    execution: Mapped[Execution] = relationship(back_populates="outcomes")


class PullRequestOutcome(BaseModel):
    """``outcomes.data`` for ``kind = pull_request``: from the workspace, not the model text."""

    kind: Literal["pull_request"] = "pull_request"
    repository: str
    branch: str
    commits: list[str] = []
    changed_files: list[str] = []
    pr_number: int | None = None
    tests_passed: bool | None = None


class AnswerOutcome(BaseModel):
    """Shape of ``outcomes.data`` for ``kind = answer``."""

    kind: Literal["answer"] = "answer"
    text: str
    citations: list[str] = []


class ExecutionToken(UUIDPk, CreatedAt, Base):
    """Sandbox runtime token for the Model Gateway: sha256 only; revoke sets ``revoked_at``."""

    __tablename__ = "execution_tokens"
    __table_args__ = (Index("ix_execution_tokens_execution", "execution_id"),)

    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("executions.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
