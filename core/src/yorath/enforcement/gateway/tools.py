"""ToolGateway is the tool PEP: header ∩ registry, PDP ``use_tool``, span, audit, label."""

import asyncio
import json
import logging
from typing import Any, cast

from mcp import Client
from mcp.types import RequestParamsMeta
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.journal import AuditEventType, Decision, Journal, SpanKind, SpanStatus
from yorath.control.journal.events import tool_attributes
from yorath.control.model import Execution
from yorath.enforcement import entities as E
from yorath.enforcement.authorize import enforce
from yorath.enforcement.gateway.context import ExecutionContext
from yorath.enforcement.pdp import AuthzRequest, EntityRef, PolicyDecisionPoint
from yorath.runtime.tools import ToolRegistry, ToolResult
from yorath.runtime.tools.registry import EXECUTION_META

log = logging.getLogger(__name__)


def principal_entity(ctx: ExecutionContext) -> dict[str, Any]:
    from yorath.control.model import ServiceAccount, User

    p = ctx.effective_principal
    if isinstance(p, User):
        return E.user_entity(p)
    if isinstance(p, ServiceAccount):
        return E.service_account_entity(p, ctx.agent.name)
    raise TypeError(f"unknown principal kind {p.kind}")


class ToolGateway:
    def __init__(
        self,
        registry: ToolRegistry,
        pdp: PolicyDecisionPoint,
        journal: Journal,
        *,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._registry = registry
        self._pdp = pdp
        self._journal = journal
        self._timeout = timeout_seconds

    def allowed_names(self, ctx: ExecutionContext) -> list[str]:
        """Tools from the header that exist in the registry; missing ones are remembered in the
        context."""
        from yorath.control.agents.service import normalize_tools

        wanted = [t.name for t in normalize_tools(ctx.card.tools)]
        missing = [n for n in wanted if self._registry.get(n) is None]
        if missing:
            ctx.unavailable_tools = sorted(set(ctx.unavailable_tools) | set(missing))
        return [n for n in wanted if n not in missing]

    async def list_tools(self, ctx: ExecutionContext) -> list[dict[str, Any]]:
        """Description and argument schema from MCP ``tools/list``, in the OpenAI ``tools``
        shape."""
        out: list[dict[str, Any]] = []
        for name in self.allowed_names(ctx):
            server = self._registry.get(name)
            assert server is not None
            async with Client(server) as client:
                listed = await client.list_tools()
            for tool in listed.tools:
                if tool.name != name:
                    continue
                schema = dict(tool.input_schema)
                schema.pop("title", None)
                out.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": " ".join((tool.description or "").split()),
                            "parameters": schema,
                        },
                    }
                )
        return out

    async def call_tool(
        self,
        ctx: ExecutionContext,
        session: AsyncSession,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        meta: dict[str, Any] | None = None,
    ) -> ToolResult:
        """PDP → MCP → class label → span and audit. A denial or an error reaches the model as a
        result."""
        arguments = arguments or {}
        audit = ctx.audit_fields()
        server = self._registry.get(name)
        if server is None:  # not registered: not a policy decision, the tool is just absent
            return ToolResult(content=f"Tool {name} is unavailable.", is_error=True)
        decision = await enforce(
            self._pdp,
            self._journal,
            AuthzRequest.build(
                E.principal_ref(ctx.effective_principal),
                "use_tool",
                EntityRef(type="Tool", id=name),
                agent=ctx.agent.name,
                context_class=ctx.context_class.rank,
            ),
            [
                principal_entity(ctx),
                E.agent_entity(ctx.agent, ctx.card),
                E.tool_entity(name, self._registry.max_class(name)),
            ],
            tool=name,
            data_class=ctx.context_class,
            **audit,
        )
        if not decision.allowed:
            return ToolResult(
                content=f"Tool {name} is denied by policy: {decision.reason_text}.",
                is_error=True,
                meta={"denied": True, "reasons": decision.reasons},
            )
        async with self._journal.span(
            ctx.execution_id,
            name,
            SpanKind.TOOL,
            attempt_id=ctx.attempt.id,
            agent_id=ctx.agent.id,
            parent_span_id=ctx.root_span_id,
            attributes=tool_attributes(name, **{"yorath.tool.arguments": arguments}),
        ) as span:
            result = await self._invoke(server, name, arguments, ctx, meta)
            raised = ctx.raise_class(result.data_class)
            span.attributes.update(
                {
                    "yorath.tool.is_error": result.is_error,
                    "yorath.tool.data_class": result.data_class.value
                    if result.data_class
                    else None,
                    "yorath.assets.allowed": result.assets_allowed,
                    "yorath.assets.denied": result.assets_denied,
                    "yorath.hits": result.meta.get("hits"),
                    "yorath.max_data_class": ctx.context_class.value,
                }
            )
            if result.is_error:
                span.status = SpanStatus.ERROR  # Journal keeps an error set in advance
        if raised:
            ctx.execution.max_data_class_seen = ctx.max_data_class_seen
            execution = await session.get(Execution, ctx.execution_id)
            if execution is not None:
                execution.max_data_class_seen = ctx.max_data_class_seen
                await session.commit()
        await self._journal.audit(
            AuditEventType.TOOL_CALLED,
            **audit,
            tool=name,
            data_class=result.data_class,
            decision=Decision.PERMIT,
            reason=decision.reason_text,
            details={
                "is_error": result.is_error,
                "hits": result.meta.get("hits"),
                "assets_allowed": result.assets_allowed,
                "assets_denied": result.assets_denied,
            },
        )
        return result

    async def _invoke(
        self,
        server: Any,
        name: str,
        arguments: dict[str, Any],
        ctx: ExecutionContext,
        meta: dict[str, Any] | None,
    ) -> ToolResult:
        request_meta = {EXECUTION_META: str(ctx.execution_id), **(meta or {})}
        timeout = self._registry.timeout(name) or self._timeout
        try:
            async with asyncio.timeout(timeout), Client(server) as client:
                raw = await client.call_tool(
                    name, arguments, meta=cast(RequestParamsMeta, request_meta)
                )
        except TimeoutError:
            return ToolResult(
                content=f"Tool {name} did not answer within {timeout:.0f} s.", is_error=True
            )
        except Exception as exc:  # noqa: BLE001 — a tool error does not bring down the Execution
            log.exception("tool %s failed", name)
            return ToolResult(content=f"Tool {name} error: {exc}", is_error=True)
        if raw.structured_content:
            try:
                return ToolResult.model_validate(raw.structured_content)
            except Exception:  # noqa: BLE001 — external server without our result shape
                pass
        text = "\n".join(getattr(c, "text", "") for c in raw.content if hasattr(c, "text"))
        if raw.is_error:
            return ToolResult(content=text or f"Tool {name} error.", is_error=True)
        return ToolResult(content=text or json.dumps(raw.structured_content, ensure_ascii=False))
