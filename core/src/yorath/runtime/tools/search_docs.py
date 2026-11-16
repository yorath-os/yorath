"""``search_docs`` as an MCP server on top of hybrid search with an access filter before output."""

import uuid

from mcp.server.mcpserver import Context, MCPServer

from yorath.control.journal import AuditEventType, Decision
from yorath.runtime.rag.indexer import IndexError_, embeddings_model
from yorath.runtime.rag.search import search
from yorath.runtime.rag.search_types import Hit
from yorath.runtime.tools.registry import EXECUTION_META, ToolDeps
from yorath.runtime.tools.types import Citation, ToolResult

NO_HITS = "No passages match the query: the available documents do not have the answer."


def citation(n: int, h: Hit) -> Citation:
    return Citation(
        n=n,
        document_id=str(h.document_id),
        filename=h.filename,
        heading_path=h.heading_path,
        asset=h.asset_name,
        data_class=h.data_class.value,
    )


def render_fragments(hits: list[Hit], start: int = 1) -> str:
    return "\n\n".join(
        f"[{i}] {h.filename} › {h.heading_path}: {h.text}" for i, h in enumerate(hits, start=start)
    )


def build_search_docs(deps: ToolDeps) -> MCPServer:
    server = MCPServer("yorath-search-docs")

    @server.tool(name="search_docs")
    async def search_docs(query: str, ctx: Context) -> ToolResult:
        """Search the team's internal documents. Returns numbered passages [n] with the file
        and section; cite them by number. If there are no passages, the documents do not have the
        answer."""
        meta = ctx.request_context.meta or {}
        raw = meta.get(EXECUTION_META)
        exec_ctx = deps.contexts(uuid.UUID(str(raw))) if raw else None
        if exec_ctx is None:
            return ToolResult(content="Tool called outside an Execution.", is_error=True)
        start = int(str(meta.get("citation_offset", 0))) + 1
        async with deps.sessions() as session:
            try:
                embed = deps.embed_factory(await embeddings_model(session))
            except IndexError_ as exc:
                return ToolResult(content=f"Search is unavailable: {exc}", is_error=True)
            result = await search(
                session,
                deps.pdp,
                embed,
                query,
                principal=exec_ctx.effective_principal,
                agent=exec_ctx.agent,
                card=exec_ctx.card,
                params=deps.rag,
            )
        base = exec_ctx.audit_fields()
        for d in result.decisions:
            await deps.journal.audit(
                AuditEventType.DATA_ACCESSED if d.allowed else AuditEventType.POLICY_DENIED,
                **base,
                tool="search_docs",
                data_asset_id=d.asset_id,
                data_class=d.data_class,
                decision=Decision.PERMIT if d.allowed else Decision.DENY,
                reason=d.reason_text,
                details={"reasons": d.reasons},
            )
        hits = result.hits
        return ToolResult(
            content=render_fragments(hits, start) if hits else NO_HITS,
            data_class=result.max_data_class,
            citations=[citation(i, h) for i, h in enumerate(hits, start=start)],
            assets_allowed=result.allowed_assets,
            assets_denied=[d.name for d in result.denied],
            meta={"hits": len(hits), "query": query},
        )

    return server
