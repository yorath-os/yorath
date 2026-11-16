"""Policies as data: the ``policies`` table and the built-in default set (package files)."""

import hashlib
from dataclasses import dataclass, field
from importlib import resources

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.model import Policy
from yorath.db import utcnow

BUILTIN_PREFIX = "builtin:"


def builtin_policies() -> dict[str, str]:
    """{file name without extension: Cedar text} from ``yorath/enforcement/policies``."""
    folder = resources.files("yorath.enforcement") / "policies"
    return {
        p.name.removesuffix(".cedar"): p.read_text(encoding="utf-8")
        for p in sorted(folder.iterdir(), key=lambda p: p.name)
        if p.name.endswith(".cedar")
    }


def combine(parts: dict[str, str]) -> str:
    return "\n\n".join(
        f"// ===== {name} =====\n{text.strip()}" for name, text in sorted(parts.items())
    )


def _sha(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


async def load_policy_text(session: AsyncSession) -> str | None:
    """Combined text of enabled policies; None for an empty table (the core uses the built-in
    set)."""
    rows = (await session.scalars(select(Policy).order_by(Policy.name))).all()
    if not rows:
        return None
    return combine({r.name: r.cedar for r in rows if r.enabled})


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


async def sync_default_policies(session: AsyncSession) -> SyncReport:
    """Creates rows of the built-in set; updates only those the owner has not edited."""
    report = SyncReport()
    existing = {p.name: p for p in (await session.scalars(select(Policy))).all()}
    for name, text in builtin_policies().items():
        marker = BUILTIN_PREFIX + _sha(text)
        row = existing.get(name)
        if row is None:
            session.add(Policy(name=name, cedar=text, description=marker))
            report.created.append(name)
            continue
        if row.description == marker:
            continue
        builtin_hash_unchanged = (row.description or "").startswith(BUILTIN_PREFIX) and _sha(
            row.cedar
        ) == (row.description or "")[len(BUILTIN_PREFIX) :]
        if builtin_hash_unchanged:
            row.cedar, row.description, row.updated_at = text, marker, utcnow()
            report.updated.append(name)
        else:
            report.warnings.append(
                f"{name}: text edited by the owner, built-in version not applied"
            )
    await session.commit()
    return report
