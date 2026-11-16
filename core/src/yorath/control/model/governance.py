"""Data Asset and Policy as standalone entities (invariants 3, 4)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yorath.db import Base, CreatedAt, UUIDPk, enum_column


class DataClass(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

    @property
    def rank(self) -> int:
        return list(DataClass).index(self)

    def __le__(self, other: object) -> bool:  # type: ignore[override]
        return isinstance(other, DataClass) and self.rank <= other.rank

    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        return isinstance(other, DataClass) and self.rank < other.rank


class AssetKind(StrEnum):
    DOCUMENTS = "documents"
    RECORDS = "records"


class DataAsset(UUIDPk, CreatedAt, Base):
    __tablename__ = "data_assets"

    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[AssetKind] = mapped_column(enum_column(AssetKind, "asset_kind"))
    data_class: Mapped[DataClass] = mapped_column(enum_column(DataClass, "data_class"))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.principal_id"))
    description: Mapped[str | None] = mapped_column(Text)
    access_rules: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    enabled: Mapped[bool] = mapped_column(default=True, server_default=true())


class Policy(UUIDPk, CreatedAt, Base):
    """Cedar policy text (decision 14); PDP is change policy-engine-cedar."""

    __tablename__ = "policies"

    name: Mapped[str] = mapped_column(String(100), unique=True)
    cedar: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True, server_default=true())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
