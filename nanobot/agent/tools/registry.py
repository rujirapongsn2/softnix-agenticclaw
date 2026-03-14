"""Tool registry for dynamic tool management."""

from collections.abc import Callable
from typing import Any

from nanobot.agent.tools.base import Tool


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(
        self,
        on_execute: Callable[[str, dict[str, Any], str], None] | None = None,
        on_start: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self._tools: dict[str, Tool] = {}
        self._on_execute = on_execute
        self._on_start = on_start

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """Execute a tool by name with given parameters."""
        _HINT = "\n\n[Analyze the error above and try a different approach.]"

        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"

        try:
            errors = tool.validate_params(params)
            if errors:
                result = f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + _HINT
                self._emit(name, params, result)
                return result
            self._emit_start(name, params)
            result = await tool.execute(**params)
            self._emit(name, params, result)
            if isinstance(result, str) and result.startswith("Error"):
                return result + _HINT
            return result
        except Exception as e:
            result = f"Error executing {name}: {str(e)}" + _HINT
            self._emit(name, params, result)
            return result

    def _emit(self, name: str, params: dict[str, Any], result: str) -> None:
        if not self._on_execute:
            return
        try:
            self._on_execute(name, params, result)
        except Exception:
            return

    def _emit_start(self, name: str, params: dict[str, Any]) -> None:
        if not self._on_start:
            return
        try:
            self._on_start(name, params)
        except Exception:
            return

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
