import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from yorath.control.model import AssetKind, DataClass, DocumentStatus

NAME = r"^[a-z0-9][a-z0-9-]{1,98}$"


class AccessRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: list[str] = []
    departments: list[str] = []


class AssetCreate(BaseModel):
    name: str = Field(pattern=NAME)
    description: str | None = Field(default=None, max_length=1000)
    data_class: DataClass = DataClass.INTERNAL
    kind: AssetKind = AssetKind.DOCUMENTS
    access_rules: AccessRules = AccessRules()


class AssetUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=1000)
    data_class: DataClass | None = None
    access_rules: AccessRules | None = None
    enabled: bool | None = None


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: str
    description: str | None
    data_class: DataClass
    access_rules: dict[str, Any]
    owner_id: uuid.UUID | None
    enabled: bool
    created_at: datetime
    documents: int = 0
    records: int = 0


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    asset_id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    data_class: DataClass
    status: DocumentStatus
    error: str | None
    pages: int | None
    uploaded_by: uuid.UUID | None
    created_at: datetime


class DocumentDetail(DocumentOut):
    text_preview: str | None = None
    text_chars: int = 0
