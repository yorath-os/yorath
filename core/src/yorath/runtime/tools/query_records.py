"""``query_records`` as an MCP server: candidates from the table, PDP on every row before output.

For an employee, you see everything or nothing. A forbidden row is indistinguishable from a missing
one: otherwise a denial would confirm that the person exists. Dropped rows show only as a count in
the audit.
"""

import re
import uuid
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from yorath.control.journal import AuditEventType, Decision
from yorath.control.model import AssetKind, DataAsset, DataClass, DataRecord, User
from yorath.enforcement import entities as E
from yorath.enforcement.pdp import AuthzRequest, EntityRef
from yorath.runtime.rag.search import principal_entity
from yorath.runtime.tools.registry import EXECUTION_META, ToolDeps
from yorath.runtime.tools.types import Citation, ToolContext, ToolResult

MAX_CANDIDATES = 200
MAX_ROWS = 20
NOT_FOUND = "No records match the query."
POSSESSIVE = re.compile(r"['\u2019]s?$")


def name_tokens(name: str) -> list[str]:
    """Lowercased name words without the possessive: "Miller's" finds "Miller"."""
    return [t for w in name.lower().split() if (t := POSSESSIVE.sub("", w))]


def has_word(label: InstrumentedAttribute[str], token: str) -> ColumnElement[bool]:
    """``token`` is a whole word of ``label``, case-insensitive: a first name or a surname."""
    low = func.lower(label)
    return or_(
        low == token,
        low.startswith(token + " ", autoescape=True),
        low.endswith(" " + token, autoescape=True),
        low.contains(" " + token + " ", autoescape=True),
    )


async def candidates(
    session: AsyncSession, assets: list[DataAsset], name: str, department: str
) -> list[DataRecord]:
    query = select(DataRecord).where(DataRecord.asset_id.in_([a.id for a in assets]))
    for token in name_tokens(name):
        query = query.where(has_word(DataRecord.label, token))
    if department.strip():
        query = query.where(func.lower(DataRecord.department) == department.strip().lower())
    return list(
        (await session.scalars(query.order_by(DataRecord.label).limit(MAX_CANDIDATES))).all()
    )


async def user_ids(session: AsyncSession) -> dict[str, uuid.UUID]:
    """Links a row to a user account: ``users.attributes.employee_id``."""
    rows = (await session.scalars(select(User))).all()
    return {str(u.attributes["employee_id"]): u.id for u in rows if u.attributes.get("employee_id")}


def render(rows: list[DataRecord], start: int) -> str:
    def line(n: int, r: DataRecord) -> str:
        fields = "; ".join(f"{k}: {v}" for k, v in r.data.items())
        return f"[{n}] {r.label}: {fields}"

    body = "\n".join(line(i, r) for i, r in enumerate(rows, start=start))
    return f"People found: {len(rows)}\n{body}"


async def allowed_rows(
    session: AsyncSession, deps: ToolDeps, ctx: ToolContext, name: str, department: str
) -> tuple[list[DataRecord], dict[uuid.UUID, DataAsset], dict[uuid.UUID, int]]:
    assets = (
        await session.scalars(
            select(DataAsset).where(
                DataAsset.kind == AssetKind.RECORDS, DataAsset.enabled.is_(True)
            )
        )
    ).all()
    by_id = {a.id: a for a in assets}
    denied: dict[uuid.UUID, int] = {}
    if not assets:
        return [], by_id, denied
    rows = await candidates(session, list(assets), name, department)
    users = await user_ids(session)
    base: list[dict[str, Any]] = [
        await principal_entity(session, ctx.effective_principal, ctx.agent.name),
        E.agent_entity(ctx.agent, ctx.card),
        *[E.data_asset_entity(a) for a in assets],
    ]
    allowed = []
    for row in rows:
        asset = by_id[row.asset_id]
        entity = E.record_entity(
            asset.name,
            row.id,
            row.data_class,
            employee_id=users.get(row.key),
            department=row.department,
            manager_id=users.get(row.manager_key or ""),
        )
        decision = deps.pdp.authorize(
            AuthzRequest.build(
                E.principal_ref(ctx.effective_principal),
                "query_record",
                EntityRef(type="Record", id=str(row.id)),
                agent=ctx.agent.name,
                context_class=row.data_class.rank,
            ),
            [*base, entity],
        )
        if decision.allowed:
            allowed.append(row)
        else:
            denied[row.asset_id] = denied.get(row.asset_id, 0) + 1
    return allowed, by_id, denied


def build_query_records(deps: ToolDeps) -> MCPServer:
    server = MCPServer("yorath-query-records")

    @server.tool(name="query_records")
    async def query_records(ctx: Context, name: str = "", department: str = "") -> ToolResult:
        """Data on specific employees from tables: vacation days left, dates. name is a surname
        or first name, department is the department. For rules and policies use search_docs."""
        meta = ctx.request_context.meta or {}
        raw = meta.get(EXECUTION_META)
        exec_ctx = deps.contexts(uuid.UUID(str(raw))) if raw else None
        if exec_ctx is None:
            return ToolResult(content="Tool called outside an Execution.", is_error=True)
        start = int(str(meta.get("citation_offset", 0))) + 1
        async with deps.sessions() as session:
            rows, assets, denied = await allowed_rows(session, deps, exec_ctx, name, department)
        rows = rows[:MAX_ROWS]
        audit = exec_ctx.audit_fields()
        counts: dict[uuid.UUID, int] = {}
        for row in rows:
            counts[row.asset_id] = counts.get(row.asset_id, 0) + 1
        for asset_id, count in counts.items():
            await deps.journal.audit(
                AuditEventType.DATA_ACCESSED,
                **audit,
                tool="query_records",
                data_asset_id=asset_id,
                data_class=assets[asset_id].data_class,
                decision=Decision.PERMIT,
                details={"rows": count},
            )
        for asset_id, count in denied.items():
            await deps.journal.audit(
                AuditEventType.POLICY_DENIED,
                **audit,
                tool="query_records",
                data_asset_id=asset_id,
                data_class=assets[asset_id].data_class,
                decision=Decision.DENY,
                reason="no permission for rows of the source",
                details={"rows_denied": count},
            )
        if not rows:
            return ToolResult(content=NOT_FOUND, meta={"hits": 0})
        top: DataClass = max((r.data_class for r in rows), key=lambda c: c.rank)
        return ToolResult(
            content=render(rows, start),
            data_class=top,
            citations=[
                Citation(
                    n=i,
                    document_id="",
                    filename=assets[r.asset_id].name,
                    heading_path=r.label,
                    asset=assets[r.asset_id].name,
                    data_class=r.data_class.value,
                )
                for i, r in enumerate(rows, start=start)
            ],
            meta={"hits": len(rows)},
        )

    return server
