"""MCP (Model Context Protocol) tool integration."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool


class MCPToolWrapper(Tool):
    """Wraps an MCP tool so the agent can call it like any other tool."""

    def __init__(self, session: Any, tool_def: Any) -> None:
        self._session = session
        self._tool_def = tool_def
        self._name: str = tool_def.name
        self._description: str = tool_def.description or ""
        input_schema = tool_def.inputSchema if hasattr(tool_def, "inputSchema") else {}
        self._parameters: dict[str, Any] = input_schema or {
            "type": "object",
            "properties": {},
        }

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> str:
        from mcp.types import CallToolResult

        result: CallToolResult = await self._session.call_tool(self._name, kwargs)
        parts: list[str] = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.append(json.dumps(block.model_dump(), ensure_ascii=False))
        return "\n".join(parts) if parts else "(empty)"


async def connect_mcp_servers(
    mcp_servers: dict[str, Any],
    exit_stack: Any,
) -> list[MCPToolWrapper]:
    """Connect to all configured MCP servers and return wrapped tools.

    Args:
        mcp_servers: Mapping of server name to MCPServerConfig.
        exit_stack: An AsyncExitStack for managing server lifecycles.

    Returns:
        List of MCPToolWrapper instances ready for registration.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.sse import sse_client

    tools: list[MCPToolWrapper] = []

    for name, cfg in mcp_servers.items():
        try:
            if cfg.command:
                params = StdioServerParameters(
                    command=cfg.command,
                    args=list(cfg.args),
                    env=dict(cfg.env) if cfg.env else None,
                )
                transport = await exit_stack.enter_async_context(stdio_client(params))
            elif cfg.url:
                transport = await exit_stack.enter_async_context(sse_client(cfg.url))
            else:
                logger.warning(f"MCP server '{name}': no command or url configured, skipping")
                continue

            read_stream, write_stream = transport
            session: ClientSession = await exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()

            server_tools = await session.list_tools()
            for t in server_tools.tools:
                tools.append(MCPToolWrapper(session, t))

            logger.info(f"MCP server '{name}' connected — {len(server_tools.tools)} tools")

        except Exception as e:
            logger.error(f"MCP server '{name}' failed to connect: {e}")

    return tools
