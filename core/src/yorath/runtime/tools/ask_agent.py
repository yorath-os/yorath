"""``ask_agent`` as an MCP server: rights and execution are in AgentGateway, the tool only passes
the call."""

import uuid

from mcp.server.mcpserver import Context, MCPServer

from yorath.runtime.tools.registry import EXECUTION_META, ToolDeps
from yorath.runtime.tools.types import ToolResult


def build_ask_agent(deps: ToolDeps) -> MCPServer:
    server = MCPServer("yorath-ask-agent")

    @server.tool(name="ask_agent")
    async def ask_agent(agent: str, question: str, ctx: Context) -> ToolResult:
        """Pass a question to another agent, for example research-agent for an internet search.
        Call it only if the user explicitly asks to search the internet."""
        meta = ctx.request_context.meta or {}
        raw = meta.get(EXECUTION_META)
        exec_ctx = deps.contexts(uuid.UUID(str(raw))) if raw else None
        if exec_ctx is None or deps.invoke_agent is None:
            return ToolResult(content="Tool called outside an Execution.", is_error=True)
        offset = int(str(meta.get("citation_offset", 0)))
        return await deps.invoke_agent(exec_ctx, agent, question, offset)

    return server
