"""Documents inside a data source (invariant 3, §7): a file on disk, metadata and text in the DB."""

import uuid
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from yorath.control.model.governance import DataAsset, DataClass
from yorath.db import Base, CreatedAt, UUIDPk, enum_column


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSED = "parsed"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"


class Document(UUIDPk, CreatedAt, Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("asset_id", "sha256"),)

    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_assets.id"))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    data_class: Mapped[DataClass] = mapped_column(enum_column(DataClass, "data_class"))
    status: Mapped[DocumentStatus] = mapped_column(
        enum_column(DocumentStatus, "document_status"), default=DocumentStatus.UPLOADED
    )
    error: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text, comment="extracted text for chunking")
    pages: Mapped[int | None]
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.principal_id"))

    asset: Mapped[DataAsset] = relationship()


class DataRecord(UUIDPk, CreatedAt, Base):
    """A row of a structured source: rule fields pulled out, the whole row in ``data``."""

    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint("asset_id", "key"),
        Index("ix_records_asset_label", "asset_id", "label"),
    )

    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_assets.id"))
    key: Mapped[str] = mapped_column(String(100), comment="employee_id from the file")
    label: Mapped[str] = mapped_column(String(300), comment="full_name")
    manager_key: Mapped[str | None] = mapped_column(String(100))
    department: Mapped[str | None] = mapped_column(String(100))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    data_class: Mapped[DataClass] = mapped_column(enum_column(DataClass, "data_class"))


EMBEDDING_DIM = 768  # embeddinggemma-2:570m; must match rag.embedding_dim in the manifest
FTS_CONFIG = "russian"


class Chunk(UUIDPk, CreatedAt, Base):
    """Document chunk: text prefixed with the heading path, embedding, full text, document class."""

    __tablename__ = "chunks"
    __table_args__ = (
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_chunks_asset_class", "asset_id", "data_class"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_assets.id"))
    ordinal: Mapped[int]
    heading_path: Mapped[str] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text)
    data_class: Mapped[DataClass] = mapped_column(enum_column(DataClass, "data_class"))
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed(f"to_tsvector('{FTS_CONFIG}', text)", persisted=True)
    )
