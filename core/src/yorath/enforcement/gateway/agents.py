"""AgentGateway is the inter-agent route (§4, decision 7): PDP ``invoke_agent``, a child Execution
with the same principal and the inherited label, run by the same loop; a denial shows in the
chain."""

import re
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yorath.control.journal import AuditEventType, Decision, Journal, SpanKind
from yorath.control.journal.events import agent_attributes
from yorath.control.model import ExecutionStatus, ExecutionTrigger, Outcome, OutcomeKind
from yorath.distribution.manifest import AgentLoopSettings
from yorath.enforcement import entities as E
from yorath.enforcement.authorize import enforce
from yorath.enforcement.gateway.context import (
    ContextRegistry,
    ExecutionContext,
    finish,
    load_agent,
    open_context,
)
from yorath.enforcement.gateway.errors import GatewayError
from yorath.enforcement.gateway.models import ModelGateway, context_window
from yorath.enforcement.gateway.tools import ToolGateway, principal_entity
from yorath.enforcement.pdp import AuthzRequest, EntityRef, PolicyDecisionPoint
from yorath.runtime.tools.types import Citation, ToolResult

CITATION_RE = re.compile(r"\[(\d+)\]")
INTERRUPTED = "parent Execution interrupted"


def shift_citations(text: str, offset: int) -> str:
    return CITATION_RE.sub(lambda m: f"[{int(m.group(1)) + offset}]", text)


class AgentGateway:
    def __init__(
        self,
        pdp: PolicyDecisionPoint,
        journal: Journal,
        sessions: async_sessionmaker[AsyncSession],
        contexts: ContextRegistry,
        tools: ToolGateway,
        models: ModelGateway,
        *,
        settings: AgentLoopSettings,
        agents_dir: Path,
        num_ctx: int,
    ) -> None:
        self._pdp = pdp
        self._journal = journal
        self._sessions = sessions
        self._contexts = contexts
        self._tools = tools
        self._models = models
        self._settings = settings
        self._agents_dir = agents_dir
        self._num_ctx = num_ctx

    async def invoke(
        self, ctx: ExecutionContext, target: str, question: str, offset: int = 0
    ) -> ToolResult:
        async with self._sessions() as session:
            try:
                agent, rev, card, body = await load_agent(session, target, self._agents_dir)
            except GatewayError as exc:
                return ToolResult(content=f"Agent {target} is unavailable: {exc}", is_error=True)
            entities: list[dict[str, Any]] = [
                principal_entity(ctx),
                E.agent_entity(ctx.agent, ctx.card),
            ]
            if agent.id != ctx.agent.id:
                entities.append(E.agent_entity(agent, card))
            decision = await enforce(
                self._pdp,
                self._journal,
                AuthzRequest.build(
                    E.principal_ref(ctx.effective_principal),
                    "invoke_agent",
                    EntityRef(type="Agent", id=target),
                    agent=ctx.agent.name,
                    context_class=ctx.context_class.rank,
                ),
                entities,
                tool="ask_agent",
                data_class=ctx.context_class,
                **ctx.audit_fields(),
            )
            child = await open_context(
                session,
                self._journal,
                agent=agent,
                revision=rev,
                card=card,
                body=body,
                actor=ctx.actor,
                effective_principal=ctx.effective_principal,
                trigger=ExecutionTrigger.AGENT_CALL,
                parent=ctx.execution,
            )
            child.raise_class(ctx.max_data_class_seen)  # the label follows the data
            if not decision.allowed:
                reason = (
                    f"call to agent {target} with data of class {ctx.context_class.value} "
                    f"blocked: {decision.reason_text}"
                )
                await finish(child, session, self._journal, ExecutionStatus.DENIED, error=reason)
                return ToolResult(
                    content=reason,
                    is_error=True,
                    meta={"denied": True, "reasons": decision.reasons},
                )
            await self._journal.audit(
                AuditEventType.AGENT_INVOKED,
                **ctx.audit_fields(),
                tool="ask_agent",
                data_class=ctx.context_class,
                decision=Decision.PERMIT,
                reason=decision.reason_text,
                details={"target": target, "child_execution_id": str(child.execution_id)},
            )
            return await self._run(child, session, question, offset)

    async def _run(
        self, child: ExecutionContext, session: AsyncSession, question: str, offset: int
    ) -> ToolResult:
        from yorath.runtime.agent_loop import Failure, Final, LoopDeps, run

        window = await context_window(session, child.card.model, self._num_ctx)
        deps = LoopDeps(
            tools=self._tools, models=self._models, settings=self._settings, num_ctx=window
        )
        self._contexts.add(child)
        status, error = ExecutionStatus.CANCELLED, INTERRUPTED
        final: Final | None = None
        try:
            async with self._journal.span(
                child.execution_id,
                f"execution {child.agent.name}",
                SpanKind.EXECUTION,
                attempt_id=child.attempt.id,
                agent_id=child.agent.id,
                attributes=agent_attributes(child.agent.name),
            ) as root:
                child.root_span_id = root.span_id
                async for event in run(child, session, deps, question):
                    if isinstance(event, Final):
                        final = event
                        status, error = ExecutionStatus.SUCCEEDED, ""
                    elif isinstance(event, Failure):
                        status, error = event.status, event.message
            if final is not None:
                session.add(
                    Outcome(
                        execution_id=child.execution_id,
                        kind=OutcomeKind.ANSWER,
                        data={"kind": "answer", "text": final.text, "citations": []},
                    )
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 — the child Execution outcome is not lost
            status, error = ExecutionStatus.FAILED, str(exc)
        finally:
            self._contexts.remove(child.execution_id)
            await finish(child, session, self._journal, status, error=error or None)
        if final is None:
            return ToolResult(
                content=f"Agent {child.agent.name} did not answer: {error}", is_error=True
            )
        citations = [
            Citation.model_validate({**c.model_dump(), "n": c.n + offset})
            for c in final.all_citations
        ]
        return ToolResult(
            content=f"Answer from agent {child.agent.name}: {shift_citations(final.text, offset)}",
            data_class=child.max_data_class_seen,
            citations=citations,
            meta={"hits": len(citations)},
        )
