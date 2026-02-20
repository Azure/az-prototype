"""MCP manager -- lifecycle, tool routing, and circuit breaker.

The MCPManager is the primary interface for the rest of the extension
to interact with MCP handlers.  It provides:

- Lazy connection: handlers connect on first tool access, not at startup
- Tool routing: maps tool names to handlers and dispatches calls
- Circuit breaker: marks handlers as failed after consecutive errors
- OpenAI schema conversion: formats tools for AI provider consumption
- Context manager: clean shutdown when session ends
"""

from __future__ import annotations

import logging
import threading
from typing import Any, TYPE_CHECKING

from azext_prototype.mcp.base import MCPToolDefinition, MCPToolResult
from azext_prototype.mcp.registry import MCPRegistry

if TYPE_CHECKING:
    from azext_prototype.ui.console import Console

logger = logging.getLogger(__name__)

# Circuit breaker: mark handler as failed after this many consecutive errors
_CIRCUIT_BREAKER_THRESHOLD = 3


class MCPManager:
    """Lifecycle manager for MCP handlers.

    Owns lazy connection, tool routing, circuit breaker, and shutdown.
    Thread-safe for concurrent tool calls from parallel agent execution.
    """

    def __init__(
        self,
        registry: MCPRegistry,
        console: "Console | None" = None,
    ):
        self._registry = registry
        self._console = console
        self._tool_map: dict[str, str] = {}  # tool_name -> handler_name
        self._connected_handlers: set[str] = set()
        self._failed_handlers: set[str] = set()
        self._error_counts: dict[str, int] = {}  # handler_name -> consecutive errors
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Tool discovery
    # ------------------------------------------------------------------ #

    def get_tools_for_scope(
        self,
        stage: str | None = None,
        agent: str | None = None,
    ) -> list[MCPToolDefinition]:
        """Get tools from scoped handlers. Lazily connects on first access."""
        handlers = self._registry.get_for_scope(stage, agent)
        tools: list[MCPToolDefinition] = []

        for handler in handlers:
            if handler.name in self._failed_handlers:
                continue

            # Lazy connect
            if handler.name not in self._connected_handlers:
                self._ensure_connected(handler)
                if handler.name in self._failed_handlers:
                    continue

            handler_tools = handler.list_tools()
            for tool in handler_tools:
                if tool.name in self._tool_map:
                    existing_handler = self._tool_map[tool.name]
                    if existing_handler != handler.name:
                        logger.warning(
                            "Tool name collision: '%s' already registered by '%s', "
                            "ignoring from '%s'",
                            tool.name, existing_handler, handler.name,
                        )
                        continue
                self._tool_map[tool.name] = handler.name
                tools.append(tool)

        return tools

    def get_tools_as_openai_schema(
        self,
        stage: str | None = None,
        agent: str | None = None,
    ) -> list[dict]:
        """Convert scoped tools to OpenAI function-calling format."""
        tools = self.get_tools_for_scope(stage, agent)
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema or {"type": "object", "properties": {}},
                },
            }
            for tool in tools
        ]

    # ------------------------------------------------------------------ #
    # Tool invocation
    # ------------------------------------------------------------------ #

    def call_tool(self, tool_name: str, arguments: dict) -> MCPToolResult:
        """Route a tool call to the correct handler. Thread-safe."""
        handler_name = self._tool_map.get(tool_name)
        if not handler_name:
            return MCPToolResult(
                content="",
                is_error=True,
                error_message=f"Unknown tool: {tool_name}",
            )

        handler = self._registry.get(handler_name)
        if handler is None or handler_name in self._failed_handlers:
            return MCPToolResult(
                content="",
                is_error=True,
                error_message=f"Handler '{handler_name}' is unavailable",
            )

        result = handler.call_tool(tool_name, arguments)

        # Circuit breaker tracking
        with self._lock:
            if result.is_error:
                count = self._error_counts.get(handler_name, 0) + 1
                self._error_counts[handler_name] = count
                if count >= _CIRCUIT_BREAKER_THRESHOLD:
                    self._failed_handlers.add(handler_name)
                    logger.warning(
                        "Circuit breaker tripped for handler '%s' after %d errors",
                        handler_name, count,
                    )
                    if self._console:
                        self._console.print_warning(
                            f"MCP handler '{handler_name}' disabled after "
                            f"{count} consecutive failures"
                        )
            else:
                self._error_counts[handler_name] = 0

        return result

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def shutdown_all(self) -> None:
        """Disconnect all connected handlers."""
        for handler in self._registry.list_all():
            if handler.name in self._connected_handlers:
                try:
                    handler.disconnect()
                except Exception as exc:
                    logger.warning(
                        "Error disconnecting handler '%s': %s",
                        handler.name, exc,
                    )
        self._connected_handlers.clear()
        self._tool_map.clear()
        self._error_counts.clear()
        self._failed_handlers.clear()

    def __enter__(self) -> MCPManager:
        return self

    def __exit__(self, *args: Any) -> None:
        self.shutdown_all()

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _ensure_connected(self, handler: Any) -> None:
        """Connect a handler, marking it as failed on error."""
        with self._lock:
            if handler.name in self._connected_handlers:
                return
            try:
                handler.connect()
                if handler._connected:
                    self._connected_handlers.add(handler.name)
                else:
                    self._failed_handlers.add(handler.name)
                    logger.warning(
                        "Handler '%s' connect() completed but _connected is False",
                        handler.name,
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to connect MCP handler '%s': %s",
                    handler.name, exc,
                )
                self._failed_handlers.add(handler.name)
                if self._console:
                    self._console.print_warning(
                        f"MCP handler '{handler.name}' failed to connect: {exc}"
                    )
