"""``/data/*``: sources and documents, owner only."""

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select

from yorath.control import demo
from yorath.control.agents.loader import AgentCard
from yorath.control.data import service
from yorath.control.data.schemas import (
    AssetCreate,
    AssetOut,
    AssetUpdate,
    DocumentDetail,
    DocumentOut,
)
from yorath.control.data.service import DataError
from yorath.control.demo import DemoResult
from yorath.control.identity.deps import Db, JournalDep, Owner
from yorath.control.model import (
    Agent,
    AgentMode,
    AgentRevision,
    DataAsset,
    DataClass,
    Document,
    DocumentStatus,
    Hosting,
)
from yorath.runtime.rag.indexer import IndexError_
from yorath.runtime.rag.search import search

router = APIRouter(prefix="/data", tags=["data"])

ERROR_STATUS = {
    "name_taken": 409,
    "already_enabled": 409,
    "duplicate": 409,
    "too_large": 413,
    "empty": 422,
    "unsupported": 415,
    "class_below_asset": 422,
}


def _raise(exc: DataError) -> None:
    raise HTTPException(
        ERROR_STATUS.get(exc.code, 422), detail={"code": exc.code, "message": str(exc), **exc.extra}
    ) from exc


async def _asset_out(db: Db, assets: list[DataAsset]) -> list[AssetOut]:
    counts = await service.document_counts(db)
    rows = await service.record_counts(db)
    return [
        AssetOut.model_validate(a).model_copy(
            update={"documents": counts.get(a.id, 0), "records": rows.get(a.id, 0)}
        )
        for a in assets
    ]


@router.get("/assets", response_model=list[AssetOut])
async def list_assets(owner: Owner, db: Db):
    await service.ensure_default_asset(db, owner)
    return await _asset_out(
        db, list((await db.scalars(select(DataAsset).order_by(DataAsset.name))).all())
    )


@router.post("/assets", response_model=AssetOut, status_code=201)
async def create_asset(data: AssetCreate, owner: Owner, db: Db):
    try:
        asset = await service.create_asset(db, data, owner)
    except DataError as exc:
        _raise(exc)
    return (await _asset_out(db, [asset]))[0]


@router.patch("/assets/{asset_id}", response_model=AssetOut)
async def update_asset(asset_id: uuid.UUID, data: AssetUpdate, _: Owner, db: Db):
    asset = await db.get(DataAsset, asset_id)
    if asset is None:
        raise HTTPException(404, detail="asset_not_found")
    return (await _asset_out(db, [await service.update_asset(db, asset, data)]))[0]


@router.post("/assets/{asset_id}/documents", response_model=DocumentOut, status_code=201)
async def upload_document(
    asset_id: uuid.UUID,
    owner: Owner,
    db: Db,
    journal: JournalDep,
    request: Request,
    file: Annotated[UploadFile, File()],
    data_class: Annotated[DataClass | None, Form()] = None,
):
    asset = await db.get(DataAsset, asset_id)
    if asset is None:
        raise HTTPException(404, detail="asset_not_found")
    settings = request.app.state.settings
    try:
        doc = await service.upload_document(
            db,
            journal,
            asset,
            filename=file.filename or "upload",
            data=await file.read(),
            data_class=data_class,
            actor=owner,
            documents_dir=settings.documents_dir,
            max_bytes=settings.max_upload_mb * 1024 * 1024,
        )
    except DataError as exc:
        _raise(exc)
    if doc.status is DocumentStatus.PARSED and getattr(request.app.state, "indexer", None):
        request.app.state.indexer.schedule(doc.id)
    return doc


@router.post("/assets/{asset_id}/records", status_code=201)
async def upload_records(
    asset_id: uuid.UUID,
    owner: Owner,
    db: Db,
    journal: JournalDep,
    request: Request,
    file: Annotated[UploadFile, File()],
) -> dict[str, int]:
    asset = await db.get(DataAsset, asset_id)
    if asset is None:
        raise HTTPException(404, detail="asset_not_found")
    limit = request.app.state.settings.max_upload_mb * 1024 * 1024
    data = await file.read()
    if len(data) > limit:
        raise HTTPException(413, detail={"code": "too_large", "message": "the file is too large"})
    try:
        rows = await service.upload_records(
            db, journal, asset, filename=file.filename or "upload.csv", data=data, actor=owner
        )
    except DataError as exc:
        _raise(exc)
    return {"rows": rows}


@router.post("/demo-hr", status_code=201)
async def enable_demo_hr(owner: Owner, db: Db, journal: JournalDep, request: Request) -> DemoResult:
    """Passwords are shown here once: a repeated call is rejected."""
    settings = request.app.state.settings
    try:
        result, doc = await demo.enable(
            db, journal, owner=owner, documents_dir=settings.documents_dir,
            max_bytes=settings.max_upload_mb * 1024 * 1024,
        )  # fmt: skip
    except DataError as exc:
        _raise(exc)
    if doc and doc.status is DocumentStatus.PARSED and getattr(request.app.state, "indexer", None):
        request.app.state.indexer.schedule(doc.id)
    return result


@router.post("/documents/{document_id}/index")
async def reindex_document(document_id: uuid.UUID, _: Owner, db: Db, request: Request) -> dict:
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, detail="document_not_found")
    try:
        chunks = await request.app.state.indexer.run(document_id)
    except IndexError_ as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — the cause is already in the document status
        raise HTTPException(502, detail=f"indexing_failed: {exc}") from exc
    return {"document_id": str(document_id), "chunks": chunks}


def _search_agent() -> tuple[Agent, AgentCard]:
    """Synthetic agent for the owner's debug search: allowed to all sources."""
    card = AgentCard(
        name="owner-search", mode=AgentMode.DELEGATED, max_data_class=DataClass.RESTRICTED
    )
    agent = Agent(name="owner-search", display_name="owner-search", hosting=Hosting.MANAGED,
                  mode=AgentMode.DELEGATED, runtime_type_id=uuid.uuid7())  # fmt: skip
    return agent, card


@router.get("/search")
async def search_documents(
    q: str, owner: Owner, db: Db, request: Request, agent: str | None = None
) -> dict:
    """Debug search: documents as the owner sees them, through an agent or unrestricted."""
    if agent:
        row = await db.scalar(select(Agent).where(Agent.name == agent))
        if row is None:
            raise HTTPException(404, detail="agent_not_found")
        rev = await db.scalar(
            select(AgentRevision)
            .where(AgentRevision.agent_id == row.id)
            .order_by(AgentRevision.revision_no.desc())
            .limit(1)
        )
        if rev is None:
            raise HTTPException(404, detail="agent_not_found")
        card = AgentCard.model_validate(rev.front_matter)
    else:
        row, card = _search_agent()
        row.name = "owner-search"
    if not agent:
        assets = (await db.scalars(select(DataAsset.name).where(DataAsset.enabled.is_(True)))).all()
        card = card.model_copy(update={"data": list(assets)})
    state = request.app.state
    model = await _embeddings_model(db)
    embed = state.indexer._embed_factory(model)  # noqa: SLF001
    result = await search(
        db, state.pdp, embed, q, principal=owner, agent=row, card=card, params=state.manifest.rag
    )
    return {
        "hits": [
            {
                "document_id": str(h.document_id),
                "asset": h.asset_name,
                "filename": h.filename,
                "heading_path": h.heading_path,
                "data_class": h.data_class.value,
                "score": round(h.score, 5),
                "text": h.text,
            }
            for h in result.hits
        ],
        "max_data_class": result.max_data_class.value if result.max_data_class else None,
        "assets": [
            {
                "name": d.name,
                "data_class": d.data_class.value,
                "allowed": d.allowed,
                "reason": d.reason_text,
            }
            for d in result.decisions
        ],
    }


async def _embeddings_model(db: Db) -> str:
    from yorath.runtime.rag.indexer import embeddings_model

    try:
        return await embeddings_model(db)
    except IndexError_ as exc:
        raise HTTPException(409, detail=str(exc)) from exc


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(_: Owner, db: Db, asset_id: uuid.UUID | None = None):
    q = select(Document).order_by(Document.created_at.desc())
    if asset_id:
        q = q.where(Document.asset_id == asset_id)
    return (await db.scalars(q)).all()


@router.get("/documents/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: uuid.UUID, _: Owner, db: Db):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, detail="document_not_found")
    out = DocumentDetail.model_validate(doc)
    out.text_preview = (doc.text or "")[:2000] or None
    out.text_chars = len(doc.text or "")
    return out


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID, owner: Owner, db: Db, journal: JournalDep, request: Request
) -> None:
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, detail="document_not_found")
    await service.delete_document(
        db, journal, doc, actor=owner, documents_dir=request.app.state.settings.documents_dir
    )
