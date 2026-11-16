"""Hybrid search with an access filter before the query: the PDP decides per source, SQL per
class."""

from sqlalchemy import Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.agents.loader import AgentCard
from yorath.control.model import (
    Agent,
    AssetKind,
    Chunk,
    DataAsset,
    DataClass,
    Document,
    Principal,
    ServiceAccount,
    User,
)
from yorath.distribution.manifest import RagSettings
from yorath.enforcement import entities as E
from yorath.enforcement.pdp import AuthzRequest, EntityRef, PolicyDecisionPoint
from yorath.runtime.rag.embeddings import EmbeddingClient
from yorath.runtime.rag.fusion import rrf
from yorath.runtime.rag.rerank import NoopReranker, Reranker
from yorath.runtime.rag.search_types import AssetDecision, Hit, SearchResult


def fts_query(config: str, query: str):
    """OR query over lexemes (like BM25 in the spike): websearch_to_tsquery requires all words at
    once."""
    plain = func.cast(func.plainto_tsquery(config, query), Text)
    return func.to_tsquery(config, func.replace(plain, "&", "|"))


def classes_up_to(ceiling: DataClass) -> list[DataClass]:
    return [c for c in DataClass if c.rank <= ceiling.rank]


async def principal_entity(session: AsyncSession, principal: Principal, agent_name: str) -> dict:
    if isinstance(principal, User):
        return E.user_entity(principal)
    if isinstance(principal, ServiceAccount):
        return E.service_account_entity(principal, agent_name)
    raise TypeError(f"unknown principal kind {principal.kind}")


async def decide_assets(
    session: AsyncSession,
    pdp: PolicyDecisionPoint,
    principal: Principal,
    agent: Agent,
    card: AgentCard,
) -> list[AssetDecision]:
    """A `read_doc` decision for every enabled source of kind documents."""
    assets = (
        await session.scalars(
            select(DataAsset)
            .where(DataAsset.kind == AssetKind.DOCUMENTS, DataAsset.enabled.is_(True))
            .order_by(DataAsset.name)
        )
    ).all()
    base = [await principal_entity(session, principal, agent.name), E.agent_entity(agent, card)]
    entities = base + [E.data_asset_entity(a) for a in assets]
    decisions: list[AssetDecision] = []
    for a in assets:
        req = AuthzRequest.build(
            E.principal_ref(principal),
            "read_doc",
            EntityRef(type="DataAsset", id=a.name),
            agent=agent.name,
            context_class=a.data_class.rank,
        )
        d = pdp.authorize(req, entities)
        decisions.append(
            AssetDecision(a.id, a.name, a.data_class, d.allowed, d.reasons, d.reason_text)
        )
    return decisions


async def search(
    session: AsyncSession,
    pdp: PolicyDecisionPoint,
    embed: EmbeddingClient,
    query: str,
    *,
    principal: Principal,
    agent: Agent,
    card: AgentCard,
    params: RagSettings,
    reranker: Reranker | None = None,
) -> SearchResult:
    decisions = await decide_assets(session, pdp, principal, agent, card)
    allowed_ids = [d.asset_id for d in decisions if d.allowed]
    if not allowed_ids or not query.strip():
        return SearchResult(hits=[], decisions=decisions)
    allowed_classes = classes_up_to(card.max_data_class)
    base = (
        select(Chunk, Document.filename, DataAsset.name)
        .join(Document, Document.id == Chunk.document_id)
        .join(DataAsset, DataAsset.id == Chunk.asset_id)
        .where(Chunk.asset_id.in_(allowed_ids), Chunk.data_class.in_(allowed_classes))
    )

    (qvec,) = await embed.embed([query])
    distance = Chunk.embedding.cosine_distance(qvec).label("distance")
    scored = base.add_columns(distance)
    vec_rows = (await session.execute(scored.order_by(distance).limit(params.candidates))).all()
    tsq = fts_query(params.fts_config, query)
    fts_rows = (
        await session.execute(
            scored.where(Chunk.tsv.op("@@")(tsq))
            .order_by(func.ts_rank(Chunk.tsv, tsq).desc())
            .limit(params.candidates)
        )
    ).all()

    # Relevance is the cosine to the question (calibrated on the spike corpus: on topic ≥ 0.75,
    # off topic ≤ 0.66); full text affects only the order: a nearest neighbor and an OR lexeme
    # match always exist.
    by_id = {
        row[0].id: row
        for row in [*vec_rows, *fts_rows]
        if 1.0 - float(row[3]) >= params.min_similarity
    }
    order = [
        cid
        for cid in rrf([[r[0].id for r in vec_rows], [r[0].id for r in fts_rows]], k=params.rrf_k)
        if cid in by_id
    ]
    hits: list[Hit] = []
    for rank, chunk_id in enumerate(order[: params.top_k]):
        chunk, filename, asset_name, _ = by_id[chunk_id]
        hits.append(
            Hit(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                asset_id=chunk.asset_id,
                asset_name=asset_name,
                filename=filename,
                heading_path=chunk.heading_path,
                text=chunk.text,
                data_class=chunk.data_class,
                score=1.0 / (params.rrf_k + rank + 1),
            )
        )
    hits = await (reranker or NoopReranker()).rerank(query, hits)
    max_class = max((h.data_class for h in hits), key=lambda c: c.rank, default=None)
    return SearchResult(
        hits=hits, decisions=decisions, max_data_class=max_class, query_embedding=qvec
    )
