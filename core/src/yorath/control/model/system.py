"""Infrastructure tables: encrypted secrets and system settings. Not §9 entities."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from yorath.db import Base, CreatedAt, UUIDPk, utcnow


class Secret(UUIDPk, CreatedAt, Base):
    """The value is encrypted with YORATH_SECRET_KEY (Fernet); plaintext is never stored."""

    __tablename__ = "secrets"

    name: Mapped[str] = mapped_column(String(100), unique=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SystemSetting(Base):
    """Key → JSON: wizard flags and other settings changed from the UI."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


async def get_setting(session: AsyncSession, key: str, default: Any = None) -> Any:
    row = await session.scalar(select(SystemSetting).where(SystemSetting.key == key))
    return default if row is None else row.value


async def set_setting(session: AsyncSession, key: str, value: Any) -> None:
    row = await session.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if row is None:
        session.add(SystemSetting(key=key, value=value))
    else:
        row.value, row.updated_at = value, utcnow()
    await session.commit()
