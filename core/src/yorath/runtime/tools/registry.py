"""Tool registry: name → MCP server. Built-in servers are created by factories with ``ToolDeps``.

Registration does not give the tool to agents: access is decided by the agent header and the PDP in
the Gateway.
An external server (stdio / Streamable HTTP) registers with an SDK transport through the same method
(Later).
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yorath.control.journal import Journal
from yorath.control.model import DataClass
from yorath.distribution.manifest import RagSettings
from yorath.enforcement.pdp import PolicyDecisionPoint
from yorath.enforcement.secrets import SecretStore
from yorath.runtime.rag.embeddings import EmbeddingClient
from yorath.runtime.tools.types import ToolContext, ToolResult

EXECUTION_META = "execution_id"  # MCP request ``meta`` key the tool uses to find its context


@dataclass
class ToolDeps:
    sessions: async_sessionmaker[AsyncSession]
    pdp: PolicyDecisionPoint
    journal: Journal
    embed_factory: Callable[[str], EmbeddingClient]
    rag: RagSettings
    contexts: Callable[[uuid.UUID], ToolContext | None]
    http: httpx.AsyncClient
    secrets: SecretStore
    tavily_url: str
    agent_timeout_seconds: float
    invoke_agent: Callable[[Any, str, str, int], Awaitable[ToolResult]] | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._servers: dict[str, MCPServer] = {}
        self._ceilings: dict[str, DataClass] = {}
        self._timeouts: dict[str, float] = {}

    def register(
        self,
        name: str,
        server: MCPServer,
        max_class: DataClass = DataClass.RESTRICTED,
        timeout_seconds: float | None = None,
    ) -> None:
        self._servers[name] = server
        self._ceilings[name] = max_class
        if timeout_seconds is not None:
            self._timeouts[name] = timeout_seconds

    def timeout(self, name: str) -> float | None:
        """The tool's own timeout; ``None`` means the shared one from the manifest."""
        return self._timeouts.get(name)

    def max_class(self, name: str) -> DataClass:
        """Context class ceiling: a tool that sends data outside stays below Confidential."""
        return self._ceilings.get(name, DataClass.RESTRICTED)

    def get(self, name: str) -> MCPServer | None:
        return self._servers.get(name)

    @property
    def names(self) -> list[str]:
        return sorted(self._servers)


@dataclass
class _Builtin:
    name: str
    factory: Callable[[ToolDeps], MCPServer]
    max_class: DataClass = DataClass.RESTRICTED
    long_running: bool = False


def _builtins() -> list[_Builtin]:
    from yorath.runtime.tools.ask_agent import build_ask_agent
    from yorath.runtime.tools.query_records import build_query_records
    from yorath.runtime.tools.search_docs import build_search_docs
    from yorath.runtime.tools.web_search import build_web_search

    return [
        _Builtin("search_docs", build_search_docs),
        _Builtin("query_records", build_query_records),
        _Builtin("web_search", build_web_search, DataClass.INTERNAL),
        # a route's class ceiling comes from the agent-to-agent policy and the recipient's ceiling
        _Builtin("ask_agent", build_ask_agent, long_running=True),
    ]


def build_registry(deps: ToolDeps) -> ToolRegistry:
    """Built-in core tools. Later changes add their factories here."""
    registry = ToolRegistry()
    for item in _builtins():
        timeout = deps.agent_timeout_seconds if item.long_running else None
        registry.register(item.name, item.factory(deps), item.max_class, timeout)
    return registry
