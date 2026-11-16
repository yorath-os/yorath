"""Gateway is the single PEP (§6, decision 21): Execution context, tools, models.

``install(app)`` builds the Gateway from ``app.state`` (sessions, journal, pdp, http, indexer,
manifest, settings); called from lifespan and from tests that set the state directly.
"""

from typing import Any

from yorath.control.cluster.placements import NODE_GATEWAY_PORT
from yorath.control.model import ModelResource
from yorath.enforcement.gateway.context import (
    ContextRegistry,
    ExecutionContext,
    finish,
    load_agent,
    open_context,
    sweep_unfinished,
)
from yorath.enforcement.gateway.errors import GatewayDenied, GatewayError
from yorath.enforcement.gateway.models import ModelGateway, ProviderFactory
from yorath.enforcement.gateway.slots import Lease, SlotRouter
from yorath.enforcement.gateway.tools import ToolGateway


def install(app: Any) -> None:
    from yorath.runtime.tools import ToolDeps, build_registry

    state = app.state
    contexts = ContextRegistry()
    deps = ToolDeps(
        sessions=state.sessions,
        pdp=state.pdp,
        journal=state.journal,
        embed_factory=state.indexer._embed_factory,  # noqa: SLF001
        rag=state.manifest.rag,
        contexts=contexts.get,
        http=state.http,
        secrets=state.secrets,
        tavily_url=state.settings.tavily_url,
        agent_timeout_seconds=state.manifest.agent_loop.timeout_seconds,
    )
    state.contexts = contexts
    state.tools = build_registry(deps)
    state.tool_gateway = ToolGateway(
        state.tools,
        state.pdp,
        state.journal,
        timeout_seconds=state.manifest.gateway.tool_timeout_seconds,
    )
    state.model_slots = SlotRouter(
        state.sessions,
        lambda: str(state.settings.ollama_url),
        max_waiting=state.manifest.gateway.model_queue_max,
        wait_seconds=state.manifest.gateway.model_queue_wait_seconds,
    )
    state.model_gateway = ModelGateway(
        state.pdp,
        state.journal,
        _provider_factory(state),
        state.model_slots,
        timeout_seconds=state.manifest.gateway.model_timeout_seconds,
        first_token_seconds=state.manifest.gateway.model_first_token_seconds,
        max_output_tokens=state.manifest.gateway.max_output_tokens,
        retry_delay_seconds=state.manifest.gateway.retry_delay_seconds,
    )
    from yorath.enforcement.gateway.agents import AgentGateway

    state.agent_gateway = AgentGateway(
        state.pdp,
        state.journal,
        state.sessions,
        contexts,
        state.tool_gateway,
        state.model_gateway,
        settings=state.manifest.agent_loop,
        agents_dir=state.settings.agents_dir,
        num_ctx=state.manifest.ollama.context_length,
    )
    deps.invoke_agent = state.agent_gateway.invoke


def _provider_factory(state: Any) -> ProviderFactory:
    """Provider by ``ModelResource.provider``; the OpenRouter key comes only from the secret
    store."""
    from yorath.control.model import ModelProvider
    from yorath.control.setup.openrouter import SECRET_NAME
    from yorath.enforcement.secrets import SecretStoreUnavailable
    from yorath.runtime.llm import ChatProvider, OllamaChat, OpenRouterChat

    async def factory(model: ModelResource, lease: Lease) -> ChatProvider:
        if model.provider is ModelProvider.OLLAMA:
            assert lease.url is not None  # SlotRouter.lease gives every Ollama call a server
            http = getattr(state, "http_models", None) or state.http
            return OllamaChat(http, lease.url, num_ctx=model.context_length)
        try:
            key = await state.secrets.get(SECRET_NAME)
        except SecretStoreUnavailable:
            key = None
        if not key:
            raise GatewayError("openrouter_key_missing", "OpenRouter key is not set (wizard step)")
        return OpenRouterChat(state.http, state.settings.openrouter_url, key)

    return factory


__all__ = [
    "NODE_GATEWAY_PORT",
    "ContextRegistry",
    "ExecutionContext",
    "GatewayDenied",
    "GatewayError",
    "ModelGateway",
    "ToolGateway",
    "finish",
    "install",
    "load_agent",
    "open_context",
    "sweep_unfinished",
]
