"""MCP handler registry -- builtin/custom resolution.

Follows the same pattern as AgentRegistry: custom handlers win over
builtin handlers when both exist with the same name.
"""

from __future__ import annotations

import logging

from azext_prototype.mcp.base import MCPHandler

logger = logging.getLogger(__name__)


class MCPRegistry:
    """Central registry for MCP handlers (builtin and custom).

    Resolution order when looking up a handler by name:
      1. Custom handlers (loaded from .prototype/mcp/)
      2. Built-in handlers (ship with the extension)
    """

    def __init__(self):
        self._builtin: dict[str, MCPHandler] = {}
        self._custom: dict[str, MCPHandler] = {}

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #

    def register_builtin(self, handler: MCPHandler) -> None:
        """Register a built-in handler (ships with extension)."""
        logger.debug("Registering built-in MCP handler: %s", handler.name)
        self._builtin[handler.name] = handler

    def register_custom(self, handler: MCPHandler) -> None:
        """Register a custom handler (loaded from project directory)."""
        logger.info("Custom MCP handler registered: %s", handler.name)
        self._custom[handler.name] = handler

    # ------------------------------------------------------------------ #
    # Resolution
    # ------------------------------------------------------------------ #

    def get(self, name: str) -> MCPHandler | None:
        """Resolve a handler by name (custom > builtin).

        Returns None if no handler with that name exists.
        """
        if name in self._custom:
            return self._custom[name]
        if name in self._builtin:
            return self._builtin[name]
        return None

    def list_all(self) -> list[MCPHandler]:
        """List all resolved handlers (custom wins over builtin)."""
        resolved: dict[str, MCPHandler] = {}
        for name, handler in self._builtin.items():
            resolved[name] = handler
        for name, handler in self._custom.items():
            resolved[name] = handler
        return list(resolved.values())

    def get_for_scope(
        self,
        stage: str | None = None,
        agent: str | None = None,
    ) -> list[MCPHandler]:
        """Return handlers matching the given stage+agent scope."""
        return [
            h for h in self.list_all()
            if h.matches_scope(stage, agent)
        ]

    def __len__(self) -> int:
        return len(self.list_all())

    def __contains__(self, name: str) -> bool:
        return name in self._custom or name in self._builtin
