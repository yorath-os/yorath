"""Sources and documents: upload, class rule, duplicates, parsing, deletion, audit."""

import csv
import hashlib
import io
import uuid
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.data.parsers import CONTENT_TYPES, ParseError, extension, parse
from yorath.control.data.schemas import AssetCreate, AssetUpdate
from yorath.control.journal import AuditEventType, Journal
from yorath.control.model import (
    AssetKind,
    DataAsset,
    DataClass,
    DataRecord,
    Document,
    DocumentStatus,
    User,
)
from yorath.db import uuid7

DEFAULT_ASSET = AssetCreate(
    name="team-docs",
    description="Team documents (created by the wizard)",
    data_class=DataClass.INTERNAL,
    access_rules={"roles": ["owner", "member"], "departments": []},  # type: ignore[arg-type]
)


class DataError(Exception):
    def __init__(self, code: str, message: str, **extra: object) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra


async def create_asset(session: AsyncSession, data: AssetCreate, owner: User | None) -> DataAsset:
    if await session.scalar(select(DataAsset).where(DataAsset.name == data.name)):
        raise DataError("name_taken", "a source with this name already exists")
    asset = DataAsset(
        name=data.name,
        kind=data.kind,
        description=data.description,
        data_class=data.data_class,
        access_rules=data.access_rules.model_dump(),
        owner_id=owner.id if owner else None,
    )
    session.add(asset)
    await session.commit()
    return asset


async def update_asset(session: AsyncSession, asset: DataAsset, data: AssetUpdate) -> DataAsset:
    changes = data.model_dump(exclude_unset=True)
    if "access_rules" in changes and data.access_rules is not None:
        changes["access_rules"] = data.access_rules.model_dump()
    for key, value in changes.items():
        setattr(asset, key, value)
    await session.commit()
    return asset


async def ensure_default_asset(session: AsyncSession, owner: User) -> DataAsset:
    existing = await session.scalar(select(DataAsset).where(DataAsset.name == DEFAULT_ASSET.name))
    return existing or await create_asset(session, DEFAULT_ASSET, owner)


async def document_counts(session: AsyncSession) -> dict[uuid.UUID, int]:
    rows = await session.execute(
        select(Document.asset_id, func.count()).group_by(Document.asset_id)
    )
    return {asset_id: count for asset_id, count in rows.all()}


REQUIRED_COLUMNS = ("employee_id", "full_name", "department", "manager_id")


async def record_counts(session: AsyncSession) -> dict[uuid.UUID, int]:
    rows = await session.execute(
        select(DataRecord.asset_id, func.count()).group_by(DataRecord.asset_id)
    )
    return {asset_id: count for asset_id, count in rows.all()}


def parse_records(data: bytes) -> list[dict[str, str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DataError("invalid_csv", "the file is not UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise DataError("invalid_csv", f"missing columns: {', '.join(missing)}", columns=missing)
    rows = [dict(r) for r in reader]
    if not rows:
        raise DataError("empty", "the table has no rows")
    return rows


async def upload_records(
    session: AsyncSession,
    journal: Journal | None,
    asset: DataAsset,
    *,
    filename: str,
    data: bytes,
    actor: User,
) -> int:
    """Re-uploading replaces the rows: the table is a snapshot, not a change log."""
    if asset.kind is not AssetKind.RECORDS:
        raise DataError("wrong_kind", "the source is not structured")
    rows = parse_records(data)
    await session.execute(delete(DataRecord).where(DataRecord.asset_id == asset.id))
    session.add_all(
        DataRecord(
            asset_id=asset.id,
            key=r["employee_id"],
            label=r["full_name"],
            manager_key=r["manager_id"] or None,
            department=r["department"] or None,
            data=r,
            data_class=asset.data_class,
        )
        for r in rows
    )
    await session.commit()
    if journal:
        await journal.audit(
            AuditEventType.DOCUMENT_UPLOADED,
            actor_principal_id=actor.id,
            data_asset_id=asset.id,
            data_class=asset.data_class,
            details={"filename": filename, "rows": len(rows)},
        )
    return len(rows)


def _file_path(documents_dir: Path, doc_id: uuid.UUID, filename: str) -> Path:
    return documents_dir / f"{doc_id}{extension(filename)}"


async def upload_document(
    session: AsyncSession,
    journal: Journal | None,
    asset: DataAsset,
    *,
    filename: str,
    data: bytes,
    data_class: DataClass | None,
    actor: User,
    documents_dir: Path,
    max_bytes: int,
) -> Document:
    if asset.kind is not AssetKind.DOCUMENTS:
        raise DataError("wrong_kind", "the source accepts only tables")
    if len(data) > max_bytes:
        raise DataError("too_large", f"the file is larger than {max_bytes // (1024 * 1024)} MB")
    if len(data) == 0:
        raise DataError("empty", "the file is empty")
    ext = extension(filename)
    if ext not in CONTENT_TYPES:
        raise DataError("unsupported", f"format {ext or 'without an extension'} is not supported")
    cls = data_class or asset.data_class
    if cls.rank < asset.data_class.rank:
        raise DataError(
            "class_below_asset",
            f"document class {cls.value} is below the source class {asset.data_class.value}",
        )
    sha = hashlib.sha256(data).hexdigest()
    existing = await session.scalar(
        select(Document).where(Document.asset_id == asset.id, Document.sha256 == sha)
    )
    if existing:
        raise DataError("duplicate", "this file is already uploaded", document_id=str(existing.id))

    doc = Document(
        id=uuid7(),  # the column default fires only on flush, but the file path is needed now
        asset_id=asset.id,
        filename=Path(filename).name[:255],
        content_type=CONTENT_TYPES[ext],
        size_bytes=len(data),
        sha256=sha,
        data_class=cls,
        uploaded_by=actor.id,
    )
    documents_dir.mkdir(parents=True, exist_ok=True)
    path = _file_path(documents_dir, doc.id, doc.filename)
    path.write_bytes(data)
    try:
        parsed = parse(path, doc.filename)
        doc.text, doc.pages, doc.status = parsed.text, parsed.pages, DocumentStatus.PARSED
        doc.error = "; ".join(parsed.warnings) or None
    except ParseError as exc:
        doc.status, doc.error = DocumentStatus.FAILED, str(exc)
    session.add(doc)
    await session.commit()
    if journal:
        await journal.audit(
            AuditEventType.DOCUMENT_UPLOADED,
            actor_principal_id=actor.id,
            data_asset_id=asset.id,
            data_class=cls,
            details={
                "document_id": str(doc.id),
                "filename": doc.filename,
                "sha256": sha,
                "status": doc.status.value,
            },
        )
    return doc


async def delete_document(
    session: AsyncSession,
    journal: Journal | None,
    doc: Document,
    *,
    actor: User,
    documents_dir: Path,
) -> None:
    path = _file_path(documents_dir, doc.id, doc.filename)
    path.unlink(missing_ok=True)
    info = {"document_id": str(doc.id), "filename": doc.filename, "sha256": doc.sha256}
    asset_id, cls = doc.asset_id, doc.data_class
    await session.delete(doc)
    await session.commit()
    if journal:
        await journal.audit(
            AuditEventType.DOCUMENT_DELETED,
            actor_principal_id=actor.id,
            data_asset_id=asset_id,
            data_class=cls,
            details=info,
        )
