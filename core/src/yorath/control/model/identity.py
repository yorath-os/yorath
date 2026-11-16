"""Principal (decision 27): people and service accounts are kinds of one entity.

An Execution references ``principals`` with a real foreign key in both modes;
the Cedar entity type comes from ``kind``. The agent owner is ``agents.owner_id``;
it plays no part in Execution authorization (invariant "Principal ≠ Agent identity").
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from yorath.db import Base, CreatedAt, UUIDPk, enum_column


class PrincipalKind(StrEnum):
    USER = "user"
    SERVICE = "service"


class Principal(UUIDPk, CreatedAt, Base):
    __tablename__ = "principals"

    kind: Mapped[PrincipalKind] = mapped_column(enum_column(PrincipalKind, "principal_kind"))
    display_name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())

    __mapper_args__ = {"polymorphic_on": "kind"}


class User(Principal):
    """Local account (decision 11): role and attributes for Cedar and row rules."""

    __tablename__ = "users"

    principal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("principals.id"), primary_key=True)
    login: Mapped[str] = mapped_column(String(100), unique=True)
    provider: Mapped[str] = mapped_column(String(32), default="local", server_default="local")
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50), default="member", server_default="member")
    department: Mapped[str | None] = mapped_column(String(100))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.principal_id"))
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default="{}",
        comment="extensible attributes: assigned_departments",
    )

    manager: Mapped[User | None] = relationship(
        remote_side=[principal_id], foreign_keys=[manager_id]
    )

    __mapper_args__ = {
        "polymorphic_identity": PrincipalKind.USER,
        "inherit_condition": principal_id == Principal.id,
    }


class ServiceAccount(Principal):
    """Service account of an autonomous agent: no password and no department attributes."""

    __tablename__ = "service_accounts"

    principal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("principals.id"), primary_key=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.principal_id"))
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), unique=True)

    owner: Mapped[User] = relationship(foreign_keys=[owner_user_id])

    __mapper_args__ = {
        "polymorphic_identity": PrincipalKind.SERVICE,
        "inherit_condition": principal_id == Principal.id,
    }


class UserSession(UUIDPk, CreatedAt, Base):
    """Server-side login session: only the token sha256 is stored; revoke sets ``revoked_at``."""

    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.principal_id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKey(UUIDPk, CreatedAt, Base):
    """A user's persistent API key for the model endpoint: stores sha256 and a visible prefix."""

    __tablename__ = "api_keys"
    __table_args__ = (Index("ix_api_keys_user", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.principal_id"))
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # key narrowed to one agent (change agent-api): only ``agent/<name>``, not the bare model
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    # model: a user key; agent: calls agent/<name>; agent_self: the model key of the connected
    # agent itself (change connected-agents), issued only on connection
    scope: Mapped[str] = mapped_column(String(20), default="model", server_default="model")
    # models the key may call (change field-fixes-0-7): None — every chat model of the catalog,
    # or a list of names where ``local/*`` covers every local chat model
    models: Mapped[list[str] | None] = mapped_column(JSONB)
