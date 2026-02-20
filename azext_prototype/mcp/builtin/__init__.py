"""Built-in MCP handlers that ship with the extension.

Currently empty -- add built-in handlers here as they are developed.
Follow the same pattern as agents/builtin/__init__.py.
"""

from azext_prototype.mcp.registry import MCPRegistry


def register_all_builtin_mcp(registry: MCPRegistry) -> None:
    """Register all built-in MCP handlers into the registry.

    Currently no built-in handlers are shipped. This function exists
    as the extension point for adding them in the future.
    """
    pass
