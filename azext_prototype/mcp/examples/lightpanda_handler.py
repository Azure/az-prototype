"""Example MCP handler: Lightpanda headless browser integration.

Lightpanda is an AI-native headless browser built for automation --
11x faster and 9x less memory than Chrome headless. This handler
connects to Lightpanda's hosted MCP endpoint so AI agents can
navigate web pages, extract content, and validate URLs during
build/deploy stages.

This file demonstrates how to build an MCP server handler for the
az prototype extension. To create your own:

1. Subclass MCPHandler
2. Implement connect(), list_tools(), call_tool(), disconnect()
3. Drop the file into .prototype/mcp/ (or mcp/builtin/ for built-ins)

The handler is fully responsible for transport, auth, and protocol.
The extension provides config, console (for user messages), logger,
and project config (read-only).

Config example (prototype.yaml):
    mcp:
      servers:
        - name: lightpanda
          stages: ["build", "deploy"]
          agents: ["qa-engineer", "app-developer"]
          settings:
            url: "https://mcp.pipedream.net/v2"
            # api_key goes in prototype.secrets.yaml

Secrets example (prototype.secrets.yaml):
    mcp:
      servers:
        - name: lightpanda
          settings:
            api_key: "lpd_xxxxxxxxxxxx"
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

import requests

from azext_prototype.mcp.base import (
    MCPHandler,
    MCPHandlerConfig,
    MCPToolDefinition,
    MCPToolResult,
)


class LightpandaHandler(MCPHandler):
    """Handler for Lightpanda hosted MCP server.

    Communicates via HTTP (JSON-RPC over HTTPS POST requests).
    Lightpanda's hosted endpoint provides headless browser tools
    that agents can use to fetch documentation, validate URLs,
    and extract web content during code generation.
    """

    name = "lightpanda"
    description = "Lightpanda headless browser -- web navigation and content extraction"

    def __init__(self, config: MCPHandlerConfig, **kwargs):
        super().__init__(config, **kwargs)
        self._session: requests.Session | None = None
        self._tools: list[MCPToolDefinition] = []
        self._request_id = 0
        self._lock = threading.Lock()
        self._mcp_session_url: str | None = None

    # ------------------------------------------------------------------ #
    # Contract implementation
    # ------------------------------------------------------------------ #

    def connect(self) -> None:
        """Connect to Lightpanda's hosted MCP endpoint."""
        settings = self.config.settings
        base_url = settings.get("url", "https://mcp.pipedream.net/v2")
        api_key = settings.get("api_key", "")

        self.logger.info("Connecting to Lightpanda MCP at %s", base_url)
        self._bubble_message("Connecting to Lightpanda browser...")

        # Create HTTP session with auth headers
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        if api_key:
            self._session.headers["Authorization"] = f"Bearer {api_key}"

        self._mcp_session_url = base_url

        # MCP initialize handshake over HTTP
        init_result = self._send_jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": self.client_info.to_dict(),
        })

        if not init_result:
            self._bubble_warning("Lightpanda MCP initialization failed")
            return

        # Send initialized notification
        self._send_notification("notifications/initialized", {})

        # Discover available tools
        tools_result = self._send_jsonrpc("tools/list", {})
        if tools_result and "tools" in tools_result:
            self._tools = [
                MCPToolDefinition(
                    name=t["name"],
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}),
                    handler_name=self.name,
                )
                for t in tools_result["tools"]
            ]

        self._connected = True
        self._bubble_message(f"Connected ({len(self._tools)} tools available)")
        self.logger.info("Lightpanda connected with %d tools", len(self._tools))

    def list_tools(self) -> list[MCPToolDefinition]:
        """Return discovered Lightpanda tools."""
        return list(self._tools)

    def call_tool(self, name: str, arguments: dict) -> MCPToolResult:
        """Invoke a Lightpanda tool via JSON-RPC over HTTP."""
        if not self._connected or not self._session:
            return MCPToolResult(
                content="", is_error=True,
                error_message="Not connected to Lightpanda",
            )

        self.logger.debug("Calling tool %s with args %s", name, arguments)

        for attempt in range(self.config.max_retries + 1):
            try:
                result = self._send_jsonrpc("tools/call", {
                    "name": name,
                    "arguments": arguments,
                })

                if result is None:
                    if attempt < self.config.max_retries:
                        continue
                    return MCPToolResult(
                        content="", is_error=True,
                        error_message="No response from Lightpanda",
                    )

                # MCP tool results contain a content array
                content_parts = result.get("content", [])
                text_parts = [
                    p.get("text", "") for p in content_parts
                    if p.get("type") == "text"
                ]
                content = "\n".join(text_parts)

                # Respect max_result_bytes
                if len(content) > self.config.max_result_bytes:
                    content = content[:self.config.max_result_bytes] + "\n...(truncated)"

                return MCPToolResult(
                    content=content,
                    metadata={"handler": self.name, "attempt": attempt + 1},
                )

            except requests.Timeout:
                self.logger.warning("Tool call timed out (attempt %d)", attempt + 1)
                if attempt == self.config.max_retries:
                    return MCPToolResult(
                        content="", is_error=True,
                        error_message=f"Timeout after {attempt + 1} attempts",
                    )
            except Exception as exc:
                self.logger.warning("Tool call failed (attempt %d): %s", attempt + 1, exc)
                if attempt == self.config.max_retries:
                    return MCPToolResult(
                        content="", is_error=True,
                        error_message=f"Failed after {attempt + 1} attempts: {exc}",
                    )

        return MCPToolResult(content="", is_error=True, error_message="Unexpected error")

    def disconnect(self) -> None:
        """Close the HTTP session."""
        if self._session:
            self.logger.info("Disconnecting from Lightpanda MCP")
            self._session.close()
            self._session = None
        self._connected = False
        self._tools = []

    # ------------------------------------------------------------------ #
    # Internal -- JSON-RPC over HTTP
    # ------------------------------------------------------------------ #

    def _send_jsonrpc(self, method: str, params: dict) -> dict | None:
        """Send a JSON-RPC request via HTTP POST."""
        if not self._session or not self._mcp_session_url:
            return None

        with self._lock:
            self._request_id += 1
            request_body = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params,
            }

            try:
                resp = self._session.post(
                    self._mcp_session_url,
                    json=request_body,
                    timeout=self.config.timeout,
                )
                resp.raise_for_status()

                data = resp.json()
                if "error" in data:
                    self.logger.warning("JSON-RPC error: %s", data["error"])
                    return None

                return data.get("result")

            except requests.RequestException as exc:
                self.logger.error("HTTP request failed: %s", exc)
                return None
            except (json.JSONDecodeError, ValueError) as exc:
                self.logger.error("Failed to parse response: %s", exc)
                return None

    def _send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self._session or not self._mcp_session_url:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        try:
            self._session.post(
                self._mcp_session_url,
                json=notification,
                timeout=5,
            )
        except requests.RequestException:
            pass


# Required: tells the loader which class to instantiate
MCP_HANDLER_CLASS = LightpandaHandler
