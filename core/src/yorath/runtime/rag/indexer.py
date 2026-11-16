"""Document indexing: chunks → embeddings → chunks; indexing/indexed/failed statuses; background."""

import asyncio
import logging
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yorath.control.model import Chunk, DataAsset, Document, DocumentStatus, ModelResource
from yorath.distribution.manifest import RagSettings
from yorath.runtime.rag.chunking import chunk_document
from yorath.runtime.rag.embeddings import EmbeddingClient

log = logging.getLogger(__name__)

EMBEDDINGS_RESOURCE = "local/embeddings"


class IndexError_(RuntimeError):
    pass


async def embeddings_model(session: AsyncSession) -> str:
    row = await session.scalar(
        select(ModelResource).where(ModelResource.name == EMBEDDINGS_RESOURCE)
    )
    if row is None or not row.enabled:
        raise IndexError_(
            "embeddings model is not registered: complete the Model step of the setup wizard"
        )
    return row.model_id


async def index_document(
    session: AsyncSession, embed: EmbeddingClient, document_id: uuid.UUID, params: RagSettings
) -> int:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise IndexError_("document not found")
    if doc.text is None:
        raise IndexError_("document is not parsed")
    asset = await session.get(DataAsset, doc.asset_id)
    doc.status = DocumentStatus.INDEXING
    await session.commit()
    try:
        pieces = chunk_document(
            doc.filename,
            doc.text,
            chunk_chars=params.chunk_chars,
            overlap=params.chunk_overlap,
            pages=doc.pages,
        )
        vectors = await embed.embed([p.text for p in pieces]) if pieces else []
        if any(len(v) != params.embedding_dim for v in vectors):
            raise IndexError_(
                f"embedding dimension {len(vectors[0])} != {params.embedding_dim} from the manifest"
            )
        await session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        session.add_all(
            Chunk(
                document_id=doc.id,
                asset_id=asset.id if asset else doc.asset_id,
                ordinal=i,
                heading_path=p.heading_path,
                text=p.text,
                data_class=doc.data_class,
                embedding=v,
            )
            for i, (p, v) in enumerate(zip(pieces, vectors, strict=True))
        )
        doc.status, doc.error = DocumentStatus.INDEXED, None
        await session.commit()
        return len(pieces)
    except Exception as exc:  # noqa: BLE001 — any indexing error is honestly visible in the status
        await session.rollback()
        doc = await session.get(Document, document_id)
        if doc is not None:
            doc.status, doc.error = DocumentStatus.FAILED, f"indexing: {exc}"[:2000]
            await session.commit()
        raise


class Indexer:
    """Background indexing tasks in the core process (queue and workers are Later)."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        embed_factory,
        params: RagSettings,
    ) -> None:
        self._sessions = sessions
        self._embed_factory = embed_factory  # (model_id) -> EmbeddingClient
        self._params = params
        self._tasks: set[asyncio.Task[None]] = set()

    async def run(self, document_id: uuid.UUID) -> int:
        async with self._sessions() as s:
            model = await embeddings_model(s)
            return await index_document(s, self._embed_factory(model), document_id, self._params)

    def schedule(self, document_id: uuid.UUID) -> asyncio.Task[None]:
        async def job() -> None:
            try:
                n = await self.run(document_id)
                log.info("indexed document %s: %d chunks", document_id, n)
            except Exception:  # noqa: BLE001
                log.exception("indexing of %s failed", document_id)

        task = asyncio.create_task(job())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def schedule_pending(self) -> list[uuid.UUID]:
        """Documents parsed but never indexed: uploaded before the embeddings model was registered
        (the HR demo before the wizard's model step, an upload during a pull). Indexing them is
        what registering that model, or a core start, owes them."""
        async with self._sessions() as s:
            try:
                await embeddings_model(s)
            except IndexError_:
                return []  # still no model: they wait for it
            ids = list(
                (
                    await s.scalars(
                        select(Document.id).where(Document.status == DocumentStatus.PARSED)
                    )
                ).all()
            )
        for document_id in ids:
            self.schedule(document_id)
        return ids

    async def wait_all(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
