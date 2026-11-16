"""Calling a connected agent through Yorath (design §6): chat and ``agent/<name>`` in ``/v1``.

Yorath passes the history to the agent endpoint (OpenAI Chat Completions, streamed) with the
endpoint token from
the secret store. The agent's own memory and tools are its business (``gateway`` level);
model calls the agent makes along the way come to ``/v1`` with its key as separate Executions.
"""

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yorath.control.agents.connected import address, secret_name
from yorath.control.journal import AuditEventType, SpanKind
from yorath.control.journal.events import agent_attributes
from yorath.control.model import (
    AgentRevision,
    Deployment,
    ExecutionStatus,
    Node,
    Outcome,
    OutcomeKind,
)
from yorath.distribution.manifest import ConnectedAgentSpec
from yorath.enforcement.gateway import NODE_GATEWAY_PORT, ExecutionContext
from yorath.enforcement.secrets import SecretStore
from yorath.runtime.agent_api import AgentResult
from yorath.runtime.chat.service import ChatDeps

TIMEOUT = httpx.Timeout(300, connect=5)  # OpenClaw: 14k prompt tokens for a simple question
KEPT_ROLES = ("user", "assistant")


def entry_url(spec: ConnectedAgentSpec, node_ip: str | None = None) -> str:
    """Agent endpoint: on the main node directly in the agents network; on a worker node through its
    gateway in the tunnel."""
    if node_ip:
        return f"http://{node_ip}:{NODE_GATEWAY_PORT}/agents/{address(spec)}:{spec.port}{spec.api_path}"
    return f"http://{address(spec)}:{spec.port}{spec.api_path}"


async def agent_node_ip(session: AsyncSession, agent_id: Any) -> str | None:
    """Tunnel address of the agent's node; an agent on the main node gives ``None``. An unavailable
    node is an error."""
    deployment = await session.scalar(
        select(Deployment)
        .join(AgentRevision, AgentRevision.id == Deployment.agent_revision_id)
        .where(AgentRevision.agent_id == agent_id)
        .order_by(Deployment.created_at.desc())
        .limit(1)
    )
    if deployment is None or deployment.node_id is None:
        return None
    node = await session.get(Node, deployment.node_id)
    if node is None or node.status != "active" or not node.overlay_ip:
        raise _Unavailable(f"node of agent {node.name if node else '?'} is unavailable")
    return node.overlay_ip


def agent_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only ``user`` and ``assistant``: the agent has its own instructions, the client ``system`` is
    dropped."""
    return [
        {"role": m["role"], "content": m.get("content") or ""}
        for m in messages
        if m.get("role") in KEPT_ROLES
    ]


async def _chunks(response: httpx.Response) -> AsyncGenerator[str]:
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            event = json.loads(data)
        except ValueError:
            continue
        if "error" in event:
            raise httpx.HTTPError(str(event["error"]))
        for choice in event.get("choices") or []:
            text = (choice.get("delta") or {}).get("content")
            if text:
                yield text


async def run_connected(
    ctx: ExecutionContext,
    session: AsyncSession,
    deps: ChatDeps,
    http: httpx.AsyncClient,
    store: SecretStore,
    spec: ConnectedAgentSpec,
    messages: list[dict[str, Any]],
) -> AsyncGenerator[str | AgentResult]:
    """Pieces of the agent answer, ending with exactly one ``AgentResult``; Outcome as for the
    Yorath loop."""
    result, text = AgentResult(), ""
    token = await store.get(secret_name(ctx.agent.name, "entry_token"))
    try:
        async with deps.journal.span(
            ctx.execution_id,
            f"invoke_agent {ctx.agent.name}",
            SpanKind.EXECUTION,
            attempt_id=ctx.attempt.id,
            agent_id=ctx.agent.id,
            attributes=agent_attributes(ctx.agent.name, **{"yorath.control_level": "gateway"}),
        ) as root:
            ctx.root_span_id = root.span_id
            if token is None:
                raise _Unavailable(f"agent {ctx.agent.name} is not connected: no endpoint token")
            url = entry_url(spec, await agent_node_ip(session, ctx.agent.id))
            body = {"model": spec.model_name, "stream": True, "messages": agent_messages(messages)}
            try:
                async with http.stream(
                    "POST", url, json=body, timeout=TIMEOUT,
                    headers={"Authorization": f"Bearer {token}"},
                ) as response:  # fmt: skip
                    if response.status_code >= 400:
                        await response.aread()
                        raise _Unavailable(
                            f"agent endpoint answered {response.status_code}: {response.text[:200]}"
                        )
                    async for piece in _chunks(response):
                        text += piece
                        yield piece
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise _Unavailable(f"agent {ctx.agent.name} is not running or unavailable") from exc
            root.attributes["yorath.answer.chars"] = len(text)
        result.text = text
    except _Unavailable as exc:
        result.status, result.error = ExecutionStatus.FAILED, str(exc)
        result.code = "agent_unavailable"
    except httpx.HTTPError as exc:
        result.status, result.error, result.code = ExecutionStatus.FAILED, str(exc), "agent_error"
    if result.status is ExecutionStatus.SUCCEEDED:
        session.add(
            Outcome(
                execution_id=ctx.execution_id,
                kind=OutcomeKind.ANSWER,
                data={"kind": "answer", "text": result.text, "citations": []},
            )
        )
        await session.commit()
        await deps.journal.audit(
            AuditEventType.OUTCOME_RECORDED, **ctx.audit_fields(), details={"kind": "answer"}
        )
    yield result


class _Unavailable(Exception):
    pass
