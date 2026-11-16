import uuid
from dataclasses import dataclass, field

from yorath.control.model import DataClass


@dataclass
class Hit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    asset_id: uuid.UUID
    asset_name: str
    filename: str
    heading_path: str
    text: str
    data_class: DataClass
    score: float


@dataclass
class AssetDecision:
    asset_id: uuid.UUID
    name: str
    data_class: DataClass
    allowed: bool
    reasons: list[str]
    reason_text: str


@dataclass
class SearchResult:
    hits: list[Hit]
    decisions: list[AssetDecision]
    max_data_class: DataClass | None = None
    query_embedding: list[float] = field(default_factory=list, repr=False)

    @property
    def allowed_assets(self) -> list[str]:
        return [d.name for d in self.decisions if d.allowed]

    @property
    def denied(self) -> list[AssetDecision]:
        return [d for d in self.decisions if not d.allowed]
