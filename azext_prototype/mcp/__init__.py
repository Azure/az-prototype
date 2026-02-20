"""MCP (Model Context Protocol) server integration.

Provides a handler-based plugin pattern for integrating external MCP
servers with the az prototype extension. Each handler is a Python class
that implements the MCPHandler contract and is fully responsible for
connecting to, communicating with, and translating responses from its
specific MCP server.

Public API:
    MCPClientInfo       -- Extension identity for MCP handshake
    MCPHandler          -- Abstract base class for handler implementations
    MCPHandlerConfig    -- Configuration dataclass
    MCPToolDefinition   -- Tool descriptor
    MCPToolResult       -- Tool invocation result
    MCPToolCall         -- Tool call request
    MCPRegistry         -- Handler registry (builtin/custom resolution)
    MCPManager          -- Lifecycle manager (lazy connect, routing, circuit breaker)
"""

from azext_prototype.mcp.base import (
    MCPClientInfo,
    MCPHandler,
    MCPHandlerConfig,
    MCPToolCall,
    MCPToolDefinition,
    MCPToolResult,
)
from azext_prototype.mcp.manager import MCPManager
from azext_prototype.mcp.registry import MCPRegistry

__all__ = [
    "MCPClientInfo",
    "MCPHandler",
    "MCPHandlerConfig",
    "MCPManager",
    "MCPRegistry",
    "MCPToolCall",
    "MCPToolDefinition",
    "MCPToolResult",
]
