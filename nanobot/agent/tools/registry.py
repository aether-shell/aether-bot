"""Tool registry for dynamic tool management."""

import json
import time
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        logger.debug(f"Tool registry: registered '{tool.name}' ({len(self._tools)} total)")

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)
        logger.debug(f"Tool registry: unregistered '{name}' ({len(self._tools)} total)")

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        definitions = [tool.to_schema() for tool in self._tools.values()]
        names: list[str] = []
        for definition in definitions:
            if not isinstance(definition, dict):
                continue
            if definition.get("type") == "function" and isinstance(definition.get("function"), dict):
                names.append(str(definition["function"].get("name") or "unknown"))
            else:
                names.append(str(definition.get("name") or "unknown"))
        logger.debug(
            f"Tool registry: exporting {len(definitions)} tool definitions names={names}"
        )
        return definitions

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """
        Execute a tool by name with given parameters.

        Args:
            name: Tool name.
            params: Tool parameters.

        Returns:
            Tool execution result as string.

        Raises:
            KeyError: If tool not found.
        """
        tool = self._tools.get(name)
        if not tool:
            logger.warning(f"Tool registry: unknown tool '{name}'")
            return f"Error: Tool '{name}' not found"

        started = time.monotonic()
        params_preview = json.dumps(params, ensure_ascii=False)
        if len(params_preview) > 200:
            params_preview = params_preview[:200] + "...(truncated)"
        logger.debug(f"Tool registry: executing '{name}' with params={params_preview}")

        try:
            errors = tool.validate_params(params)
            if errors:
                logger.warning(
                    f"Tool registry: validation failed for '{name}' errors={'; '.join(errors)}"
                )
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            result = await tool.execute(**params)
            elapsed = time.monotonic() - started
            logger.debug(
                f"Tool registry: '{name}' completed in {elapsed:.3f}s "
                f"result_chars={len(result)}"
            )
            return result
        except Exception as e:
            elapsed = time.monotonic() - started
            logger.exception(f"Tool registry: '{name}' failed after {elapsed:.3f}s: {e}")
            return f"Error executing {name}: {str(e)}"

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
