"""MCP handler contract and data model.

Defines the abstract base class that all MCP server integrations must
implement, plus the dataclasses used to pass configuration, tool
definitions, and tool results between the extension and handlers.

Each handler is fully responsible for connecting to its specific MCP
server (transport, auth, protocol handshake), translating between the
extension data model and the MCP server protocol, and error handling.
The extension provides config, console, logger, and project config.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from azext_prototype.ui.console import Console


# -------------------------------------------------------------------- #
# Data model
# -------------------------------------------------------------------- #


@dataclass
class MCPClientInfo:
    """MCP client identity sent during the ``initialize`` handshake.

    Centralises the extension name and version so individual handlers
    never hard-code them.  Access via ``self.client_info`` on any
    :class:`MCPHandler` instance.
    """

    name: str = "az-prototype"
    version: str = "1.0.0"

    def to_dict(self) -> dict[str, str]:
        """Serialise for the MCP ``initialize`` request."""
        return {"name": self.name, "version": self.version}


@dataclass
class MCPHandlerConfig:
    """Configuration passed to a handler from prototype.yaml."""

    name: str
    stages: list[str] | None = None  # None = all stages
    agents: list[str] | None = None  # None = all agents
    enabled: bool = True
    timeout: int = 30  # Default seconds per tool call
    max_retries: int = 2
    max_result_bytes: int = 8192  # Truncate results exceeding this
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPToolDefinition:
    """A tool exposed by a handler."""

    name: str
    description: str
    input_schema: dict  # JSON Schema for arguments
    handler_name: str  # Which handler owns this tool


@dataclass
class MCPToolResult:
    """Result from a tool invocation."""

    content: str
    is_error: bool = False
    error_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPToolCall:
    """A tool call request (from AI response or proactive code)."""

    id: str
    name: str
    arguments: dict[str, Any]


# -------------------------------------------------------------------- #
# Abstract contract
# -------------------------------------------------------------------- #


class MCPHandler(ABC):
    """Contract for MCP server integrations.

    Each handler is fully responsible for:
    - Connecting to its specific MCP server (transport, auth, protocol)
    - Translating between extension data model and MCP server protocol
    - Error handling, retries, and timeouts internally

    The extension provides via base class:
    - self.config       -- MCPHandlerConfig with handler-specific settings
    - self.client_info  -- MCPClientInfo (extension identity for MCP handshake)
    - self.console      -- Console for bubbling messages to the user
    - self.logger       -- Python logger (mcp.<handler_name>)
    - self.project_config -- Full project config dict (read-only)
    """

    name: str = ""
    description: str = ""

    config: MCPHandlerConfig
    client_info: MCPClientInfo
    console: "Console | None"
    project_config: dict
    logger: logging.Logger

    def __init__(
        self,
        config: MCPHandlerConfig,
        *,
        console: "Console | None" = None,
        project_config: dict | None = None,
    ):
        self.name = self.name or config.name
        self.config = config
        self.client_info = MCPClientInfo()
        self.console = console
        self.project_config = project_config or {}
        self.logger = logging.getLogger(f"mcp.{self.name}")
        self._connected = False

    # ------------------------------------------------------------------ #
    # Abstract methods — handlers MUST implement
    # ------------------------------------------------------------------ #

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to the MCP server.

        Called lazily on first tool call (not at startup).
        Handler is responsible for transport choice, auth handshake,
        and tool discovery. Must set self._connected = True on success.
        """

    @abstractmethod
    def list_tools(self) -> list[MCPToolDefinition]:
        """Return available tools. Called after connect()."""

    @abstractmethod
    def call_tool(self, name: str, arguments: dict) -> MCPToolResult:
        """Invoke a tool and return the result.

        Handler is responsible for timeout, retry, and error handling.
        Must never raise -- return MCPToolResult(is_error=True) on failure.
        """

    @abstractmethod
    def disconnect(self) -> None:
        """Clean shutdown. Safe to call multiple times."""

    # ------------------------------------------------------------------ #
    # Optional overrides
    # ------------------------------------------------------------------ #

    def health_check(self) -> bool:
        """Check if connection is healthy. Override for custom checks."""
        return self._connected

    # ------------------------------------------------------------------ #
    # Provided by base (handlers don't override)
    # ------------------------------------------------------------------ #

    def matches_scope(self, stage: str | None, agent: str | None) -> bool:
        """Check if this handler is available for the given stage+agent."""
        if not self.config.enabled:
            return False
        if self.config.stages is not None and stage is not None:
            if stage not in self.config.stages and "all" not in self.config.stages:
                return False
        if self.config.agents is not None and agent is not None:
            if agent not in self.config.agents:
                return False
        return True

    def _bubble_message(self, message: str) -> None:
        """Print a message to the user via Console."""
        if self.console:
            self.console.print_dim(f"[{self.name}] {message}")

    def _bubble_warning(self, message: str) -> None:
        """Print a warning to the user via Console."""
        if self.console:
            self.console.print_warning(f"[{self.name}] {message}")
