"""ModelGateway is the only point that calls an LLM: Model Resource, PDP ``call_model`` with the
context
class, provider, usage and cost in the attempt, an ``llm`` span with the prompt, audit without it.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.cluster.placements import card_allows, target_of
from yorath.control.journal import AuditEventType, Decision, GenAI, Journal, SpanKind
from yorath.control.journal.events import llm_attributes
from yorath.control.model import Execution, ExecutionStatus, ModelResource
from yorath.enforcement import entities as E
from yorath.enforcement.authorize import enforce
from yorath.enforcement.gateway.context import ExecutionContext
from yorath.enforcement.gateway.errors import GatewayDenied, GatewayError
from yorath.enforcement.gateway.slots import Lease, SlotRouter
from yorath.enforcement.gateway.tools import principal_entity
from yorath.enforcement.pdp import AuthzRequest, EntityRef, PolicyDecisionPoint
from yorath.runtime.llm import ChatChunk, ChatProvider, ChatRequest, ProviderError, ToolCall, Usage

log = logging.getLogger(__name__)

ProviderFactory = Callable[[ModelResource, Lease], Awaitable[ChatProvider]]

RETRIES = 1
TRANSIENT = ("runner has unexpectedly stopped", "runner process no longer running", "503")


def transient(exc: ProviderError) -> bool:
    """A provider error after which the same request will almost certainly succeed."""
    text = str(exc).lower()
    return any(marker in text for marker in TRANSIENT)


def cost_of(model: ModelResource, usage: Usage) -> Decimal:
    inp = Decimal(usage.input_tokens or 0) * (model.input_price_per_mtok or Decimal(0))
    out = Decimal(usage.output_tokens or 0) * (model.output_price_per_mtok or Decimal(0))
    return (inp + out) / Decimal(1_000_000)


async def context_window(session: AsyncSession, name: str, default: int) -> int:
    """Model Resource context window for the loop budget; an unknown model gives ``default`` (chat
    will deny it)."""
    # a pointer mirrors its target's window (placements.sync_alias): no join needed
    window = await session.scalar(
        select(ModelResource.context_length).where(ModelResource.name == name)
    )
    return window or default


class ModelGateway:
    def __init__(
        self,
        pdp: PolicyDecisionPoint,
        journal: Journal,
        providers: ProviderFactory,
        slots: SlotRouter,
        *,
        timeout_seconds: float = 300.0,
        first_token_seconds: float = 120.0,
        max_output_tokens: int = 2048,
        retry_delay_seconds: float = 3.0,
    ) -> None:
        self._pdp = pdp
        self._journal = journal
        self._providers = providers
        self._slots = slots
        self._timeout = timeout_seconds
        self._first_token = first_token_seconds
        self._max_output = max_output_tokens
        self._retry_delay = retry_delay_seconds

    async def resolve(
        self, ctx: ExecutionContext, session: AsyncSession, name: str | None
    ) -> ModelResource:
        """A model from the agent's allowed list; any other name is denied, not substituted."""
        wanted = name or ctx.card.model
        if not card_allows(ctx.card.models, wanted):
            reason = (
                f"agent {ctx.agent.name} is allowed models {', '.join(ctx.card.models)}, "
                f"requested {wanted}"
            )
            await self._journal.audit(
                AuditEventType.POLICY_DENIED,
                **ctx.audit_fields(),
                model=wanted[:200],
                decision=Decision.DENY,
                reason=reason[:2000],
            )
            raise GatewayDenied("model_not_allowed", reason, action="call_model", resource=wanted)
        row = await session.scalar(select(ModelResource).where(ModelResource.name == wanted))
        if row is None or not row.enabled:
            raise GatewayError(
                "model_not_found", f"model {wanted} is not registered: yorath model list"
            )
        return row

    async def authorize(self, ctx: ExecutionContext, model: ModelResource) -> None:
        decision = await enforce(
            self._pdp,
            self._journal,
            AuthzRequest.build(
                E.principal_ref(ctx.effective_principal),
                "call_model",
                EntityRef(type="Model", id=model.name),
                agent=ctx.agent.name,
                context_class=ctx.context_class.rank,
            ),
            [principal_entity(ctx), E.agent_entity(ctx.agent, ctx.card), E.model_entity(model)],
            model=model.model_id,
            data_class=ctx.context_class,
            **ctx.audit_fields(),
        )
        if not decision.allowed:
            raise GatewayDenied(
                "model_denied",
                f"call to model {model.name} with data of class {ctx.context_class.value} "
                f"blocked: {decision.reason_text}",
                action="call_model",
                resource=model.name,
            )

    async def chat(
        self, ctx: ExecutionContext, session: AsyncSession, request: ChatRequest
    ) -> AsyncIterator[ChatChunk]:
        """Chunk stream; the last one carries full ``tool_calls`` and usage. A denial comes before
        the provider and before the queue.

        A local model is called only when one of its model servers has a free slot; until then
        the call waits in the model's queue (a ``queued`` chunk tells the caller). A server that
        cannot be reached is taken out of the choice and the call is repeated once on another
        placement of the same model, never on another model.
        """
        requested = await self.resolve(ctx, session, request.model)
        # ``local/default`` is a pointer: the data class is checked against the catalog model
        # behind it, and that model is what the attempt records
        model = await target_of(session, requested)
        await self.authorize(ctx, model)
        ctx.model = model  # the model actually used by the attempt
        if request.max_tokens is None:
            request = request.model_copy(update={"max_tokens": self._max_output})
        participant = str(ctx.effective_principal.id)
        call = _Call()
        placed: dict[str, Any] = {"requested": requested.name}
        try:
            for attempt in range(2):
                reservation = await self._slots.reserve(model, participant, front=attempt > 0)
                try:
                    if reservation.lease is None:
                        yield ChatChunk(queued=reservation.ahead)
                        await _set_status(ctx, session, ExecutionStatus.QUEUED)
                        lease = await reservation.wait()
                        await _set_status(ctx, session, ExecutionStatus.RUNNING)
                    else:
                        lease = reservation.lease
                    if lease.server:
                        placed["server"] = lease.server
                    if reservation.ahead or lease.waited_ms:
                        placed["queue_wait_ms"] = lease.waited_ms
                        placed["queue_ahead"] = reservation.ahead
                    try:
                        async for chunk in self._call(ctx, model, request, lease, call, placed):
                            yield chunk
                        break
                    except _ServerUnreachable as exc:
                        # the server is already out of the choice; one retry elsewhere
                        placed["failed_server"] = lease.server
                        if attempt:
                            raise GatewayError("model_error", str(exc)) from exc
                        placed["retries"] = 1
                        log.warning("model server %s: %s, retrying elsewhere", lease.server, exc)
                finally:
                    reservation.release()
        except GatewayError as exc:
            # an aborted call shows in the audit, not only as an empty span
            await self._journal.audit(
                AuditEventType.LLM_CALLED,
                **ctx.audit_fields(),
                model=model.model_id,
                data_class=ctx.context_class,
                decision=Decision.PERMIT,
                cost_usd=Decimal(0),
                details={"status": "failed", "error": exc.code, "message": str(exc)[:500]} | placed,
            )
            raise
        cost = cost_of(model, call.usage)
        ctx.add_usage(call.usage.as_dict(), cost)
        await self._journal.audit(
            AuditEventType.LLM_CALLED,
            **ctx.audit_fields(),
            model=model.model_id,
            data_class=ctx.context_class,
            decision=Decision.PERMIT,
            cost_usd=cost,
            details={
                "usage": call.usage.as_dict(),
                "finish_reason": call.finish,
                "tool_calls": len(call.calls),
            }
            | placed,
        )

    async def _call(
        self,
        ctx: ExecutionContext,
        model: ModelResource,
        request: ChatRequest,
        lease: Lease,
        call: _Call,
        placed: dict[str, Any],
    ) -> AsyncIterator[ChatChunk]:
        """One attempt on the leased server, in its own ``llm`` span. Raises
        ``_ServerUnreachable`` when a model server could not be reached before any chunk."""
        attrs = llm_attributes(
            provider=model.provider.value,
            request_model=model.model_id,
            input_messages=[m.openai() for m in request.messages] if ctx.record_content else None,
        )
        if not ctx.record_content:
            attrs["yorath.messages"] = len(request.messages)
        attrs["yorath.tools"] = [t["function"]["name"] for t in request.tools]
        attrs["yorath.data_class"] = ctx.context_class.value
        attrs["yorath.model_requested"] = placed["requested"]
        if lease.server:
            attrs["yorath.model_server"] = lease.server
        if "queue_wait_ms" in placed:
            attrs["yorath.queue_wait_ms"] = placed["queue_wait_ms"]
            attrs["yorath.queue_ahead"] = placed["queue_ahead"]
        if placed.get("failed_server"):
            attrs["yorath.failed_server"] = placed["failed_server"]
        async with self._journal.span(
            ctx.execution_id,
            f"chat {model.model_id}",
            SpanKind.LLM,
            attempt_id=ctx.attempt.id,
            agent_id=ctx.agent.id,
            model=model.model_id,
            parent_span_id=ctx.root_span_id,
            attributes=attrs,
        ) as span:
            provider = await self._providers(model, lease)
            retries = 0
            try:
                async with asyncio.timeout(self._timeout):
                    while True:
                        try:
                            # the first model event has its own deadline: a hung provider
                            # must not hold the call for the whole timeout
                            async with asyncio.timeout(self._first_token) as wait:
                                async for chunk in provider.chat(model.model_id, request):
                                    wait.reschedule(None)
                                    call.started = True
                                    call.text += chunk.text
                                    if chunk.done:
                                        call.calls, call.finish = (
                                            chunk.tool_calls,
                                            chunk.finish_reason,
                                        )
                                        call.usage = chunk.usage or Usage()
                                        if chunk.model:
                                            span.attributes[GenAI.RESPONSE_MODEL] = chunk.model
                                    yield chunk
                            break
                        except TimeoutError:
                            if not wait.expired():
                                raise  # overall timeout
                            if retries >= RETRIES:
                                raise GatewayError(
                                    "model_timeout",
                                    "model did not start answering within "
                                    f"{self._first_token:.0f} s",
                                ) from None
                            retries += 1
                            span.attributes["yorath.retries"] = retries
                            log.warning("model %s: no first token, retrying", model.model_id)
                        except ProviderError as exc:
                            # The Ollama runner was OOM-killed (the prompt cache grows to
                            # the limit): Ollama restarts it within seconds, so retry once
                            # before the first chunk.
                            if retries >= RETRIES or call.text or call.calls or not transient(exc):
                                raise
                            retries += 1
                            span.attributes["yorath.retries"] = retries
                            log.warning("model %s: %s, retry %d", model.model_id, exc, retries)
                            await asyncio.sleep(self._retry_delay)
            except GatewayError:
                raise
            except TimeoutError as exc:
                raise GatewayError(
                    "model_timeout", f"model did not answer within {self._timeout:.0f} s"
                ) from exc
            except ProviderError as exc:
                raise GatewayError("model_error", str(exc)) from exc
            except httpx.TransportError as exc:
                reason = f"{type(exc).__name__}: {exc}"
                # the connection to the model server broke: it is out of the choice until its
                # node reports again, so the calls behind this one do not try it
                self._slots.mark_failed(lease)
                if lease.server_id is not None and not call.started:
                    raise _ServerUnreachable(reason) from exc
                raise GatewayError("model_error", reason) from exc
            except Exception as exc:  # noqa: BLE001 — provider network errors
                raise GatewayError("model_error", f"{type(exc).__name__}: {exc}") from exc
            finally:
                if ctx.record_content:
                    span.attributes[GenAI.OUTPUT_MESSAGES] = [_assistant(call.text, call.calls)]
                else:
                    span.attributes["yorath.tool_calls"] = [c.name for c in call.calls]
                span.attributes.update(
                    {
                        GenAI.INPUT_TOKENS: call.usage.input_tokens,
                        GenAI.OUTPUT_TOKENS: call.usage.output_tokens,
                        "yorath.finish_reason": call.finish,
                    }
                )


class _ServerUnreachable(Exception):
    """A model server could not be reached before the first chunk: retry on another placement."""


@dataclass
class _Call:
    """What one model call has produced so far."""

    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    finish: str | None = None
    started: bool = False  # a chunk has arrived: no retry after that


async def _set_status(
    ctx: ExecutionContext, session: AsyncSession, status: ExecutionStatus
) -> None:
    """``queued`` while the call waits for a model server, back to ``running`` with a slot. A
    finished Execution is never touched."""
    if ctx.finished:
        return
    await session.execute(
        update(Execution)
        .where(
            Execution.id == ctx.execution_id,
            Execution.status.in_((ExecutionStatus.RUNNING, ExecutionStatus.QUEUED)),
        )
        .values(status=status)
    )
    await session.commit()


def _assistant(text: str, calls: list[ToolCall]) -> dict[str, Any]:
    out: dict[str, Any] = {"role": "assistant", "content": text}
    if calls:
        out["tool_calls"] = [c.openai() for c in calls]
    return out
