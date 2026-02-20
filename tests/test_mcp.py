"""Tests for MCP handler contract, registry, manager, and loader."""

import json
import os
import threading
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from azext_prototype.mcp.base import (
    MCPHandler,
    MCPHandlerConfig,
    MCPToolCall,
    MCPToolDefinition,
    MCPToolResult,
)
from azext_prototype.mcp.manager import MCPManager
from azext_prototype.mcp.registry import MCPRegistry
from azext_prototype.mcp.loader import load_mcp_handler, load_handlers_from_directory


# -------------------------------------------------------------------- #
# Concrete test handler (in-process, no real MCP server)
# -------------------------------------------------------------------- #


class EchoHandler(MCPHandler):
    """Test handler that echoes tool arguments back."""

    name = "echo"
    description = "Test echo handler"

    def __init__(self, config, **kwargs):
        super().__init__(config, **kwargs)
        self._tools = [
            MCPToolDefinition(
                name="echo",
                description="Echoes input back",
                input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
                handler_name="echo",
            ),
            MCPToolDefinition(
                name="reverse",
                description="Reverses input text",
                input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
                handler_name="echo",
            ),
        ]

    def connect(self):
        self._connected = True

    def list_tools(self):
        return list(self._tools)

    def call_tool(self, name, arguments):
        text = arguments.get("text", "")
        if name == "echo":
            return MCPToolResult(content=text, metadata={"handler": "echo"})
        if name == "reverse":
            return MCPToolResult(content=text[::-1], metadata={"handler": "echo"})
        return MCPToolResult(content="", is_error=True, error_message=f"Unknown tool: {name}")

    def disconnect(self):
        self._connected = False


class FailingHandler(MCPHandler):
    """Test handler that always fails."""

    name = "failing"
    description = "Always fails"

    def connect(self):
        self._connected = True

    def list_tools(self):
        return [
            MCPToolDefinition(
                name="fail_tool",
                description="Always fails",
                input_schema={},
                handler_name="failing",
            )
        ]

    def call_tool(self, name, arguments):
        return MCPToolResult(content="", is_error=True, error_message="Intentional failure")

    def disconnect(self):
        self._connected = False


class ConnectFailHandler(MCPHandler):
    """Test handler that fails to connect."""

    name = "connect-fail"
    description = "Fails on connect"

    def connect(self):
        raise ConnectionError("Cannot reach server")

    def list_tools(self):
        return []

    def call_tool(self, name, arguments):
        return MCPToolResult(content="", is_error=True, error_message="Not connected")

    def disconnect(self):
        self._connected = False


# -------------------------------------------------------------------- #
# Fixtures
# -------------------------------------------------------------------- #


@pytest.fixture
def echo_config():
    return MCPHandlerConfig(name="echo")


@pytest.fixture
def echo_handler(echo_config):
    return EchoHandler(echo_config)


@pytest.fixture
def scoped_config():
    return MCPHandlerConfig(
        name="scoped",
        stages=["build", "deploy"],
        agents=["terraform-agent", "qa-engineer"],
    )


@pytest.fixture
def scoped_handler(scoped_config):
    return EchoHandler(scoped_config)


@pytest.fixture
def registry_with_handlers(echo_config):
    registry = MCPRegistry()
    registry.register_builtin(EchoHandler(echo_config))
    return registry


# ================================================================== #
# MCPHandlerConfig tests
# ================================================================== #


class TestMCPHandlerConfig:
    def test_defaults(self):
        config = MCPHandlerConfig(name="test")
        assert config.name == "test"
        assert config.stages is None
        assert config.agents is None
        assert config.enabled is True
        assert config.timeout == 30
        assert config.max_retries == 2
        assert config.max_result_bytes == 8192
        assert config.settings == {}

    def test_custom_values(self):
        config = MCPHandlerConfig(
            name="custom",
            stages=["build"],
            agents=["terraform-agent"],
            timeout=60,
            settings={"url": "https://example.com"},
        )
        assert config.stages == ["build"]
        assert config.agents == ["terraform-agent"]
        assert config.timeout == 60
        assert config.settings["url"] == "https://example.com"


# ================================================================== #
# MCPToolDefinition tests
# ================================================================== #


class TestMCPToolDefinition:
    def test_creation(self):
        tool = MCPToolDefinition(
            name="fetch_page",
            description="Fetch a web page",
            input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
            handler_name="lightpanda",
        )
        assert tool.name == "fetch_page"
        assert tool.handler_name == "lightpanda"


# ================================================================== #
# MCPToolResult tests
# ================================================================== #


class TestMCPToolResult:
    def test_success(self):
        result = MCPToolResult(content="hello", metadata={"handler": "echo"})
        assert not result.is_error
        assert result.content == "hello"

    def test_error(self):
        result = MCPToolResult(content="", is_error=True, error_message="boom")
        assert result.is_error
        assert result.error_message == "boom"


# ================================================================== #
# MCPToolCall tests
# ================================================================== #


class TestMCPToolCall:
    def test_creation(self):
        call = MCPToolCall(id="call_1", name="echo", arguments={"text": "hi"})
        assert call.id == "call_1"
        assert call.name == "echo"


# ================================================================== #
# MCPHandler (contract) tests
# ================================================================== #


class TestMCPHandler:
    def test_connect_disconnect(self, echo_handler):
        assert not echo_handler._connected
        echo_handler.connect()
        assert echo_handler._connected
        echo_handler.disconnect()
        assert not echo_handler._connected

    def test_list_tools(self, echo_handler):
        echo_handler.connect()
        tools = echo_handler.list_tools()
        assert len(tools) == 2
        assert tools[0].name == "echo"
        assert tools[1].name == "reverse"

    def test_call_tool(self, echo_handler):
        echo_handler.connect()
        result = echo_handler.call_tool("echo", {"text": "hello world"})
        assert result.content == "hello world"
        assert not result.is_error

    def test_call_tool_reverse(self, echo_handler):
        echo_handler.connect()
        result = echo_handler.call_tool("reverse", {"text": "abc"})
        assert result.content == "cba"

    def test_call_tool_unknown(self, echo_handler):
        echo_handler.connect()
        result = echo_handler.call_tool("nonexistent", {})
        assert result.is_error

    def test_health_check(self, echo_handler):
        assert not echo_handler.health_check()
        echo_handler.connect()
        assert echo_handler.health_check()

    def test_matches_scope_no_filters(self, echo_handler):
        """No stage/agent filters = matches everything."""
        assert echo_handler.matches_scope("build", "terraform-agent")
        assert echo_handler.matches_scope(None, None)

    def test_matches_scope_stage_filter(self, scoped_handler):
        assert scoped_handler.matches_scope("build", None)
        assert scoped_handler.matches_scope("deploy", None)
        assert not scoped_handler.matches_scope("design", None)

    def test_matches_scope_agent_filter(self, scoped_handler):
        assert scoped_handler.matches_scope(None, "terraform-agent")
        assert scoped_handler.matches_scope(None, "qa-engineer")
        assert not scoped_handler.matches_scope(None, "doc-agent")

    def test_matches_scope_combined(self, scoped_handler):
        assert scoped_handler.matches_scope("build", "terraform-agent")
        assert not scoped_handler.matches_scope("design", "terraform-agent")
        assert not scoped_handler.matches_scope("build", "doc-agent")

    def test_matches_scope_disabled(self):
        config = MCPHandlerConfig(name="disabled", enabled=False)
        handler = EchoHandler(config)
        assert not handler.matches_scope(None, None)

    def test_matches_scope_all_stages(self):
        config = MCPHandlerConfig(name="all-stages", stages=["all"])
        handler = EchoHandler(config)
        assert handler.matches_scope("build", None)
        assert handler.matches_scope("design", None)

    def test_logger_name(self, echo_handler):
        assert echo_handler.logger.name == "mcp.echo"

    def test_name_from_config(self):
        """When class doesn't set name, falls back to config name."""
        class NoNameHandler(MCPHandler):
            def connect(self): pass
            def list_tools(self): return []
            def call_tool(self, name, args): return MCPToolResult(content="")
            def disconnect(self): pass

        config = MCPHandlerConfig(name="from-config")
        handler = NoNameHandler(config)
        assert handler.name == "from-config"

    def test_bubble_message_with_console(self, echo_config):
        mock_console = MagicMock()
        handler = EchoHandler(echo_config, console=mock_console)
        handler._bubble_message("testing")
        mock_console.print_dim.assert_called_once()

    def test_bubble_warning_with_console(self, echo_config):
        mock_console = MagicMock()
        handler = EchoHandler(echo_config, console=mock_console)
        handler._bubble_warning("warning test")
        mock_console.print_warning.assert_called_once()

    def test_bubble_message_no_console(self, echo_handler):
        """Should not raise when console is None."""
        echo_handler._bubble_message("no-op")
        echo_handler._bubble_warning("no-op")


# ================================================================== #
# MCPRegistry tests
# ================================================================== #


class TestMCPRegistry:
    def test_register_builtin(self, echo_config):
        registry = MCPRegistry()
        handler = EchoHandler(echo_config)
        registry.register_builtin(handler)
        assert "echo" in registry
        assert registry.get("echo") is handler

    def test_register_custom(self, echo_config):
        registry = MCPRegistry()
        handler = EchoHandler(echo_config)
        registry.register_custom(handler)
        assert "echo" in registry
        assert registry.get("echo") is handler

    def test_custom_wins_over_builtin(self, echo_config):
        registry = MCPRegistry()
        builtin = EchoHandler(echo_config)
        custom = EchoHandler(echo_config)
        registry.register_builtin(builtin)
        registry.register_custom(custom)
        assert registry.get("echo") is custom

    def test_get_not_found(self):
        registry = MCPRegistry()
        assert registry.get("nonexistent") is None

    def test_list_all(self, echo_config):
        registry = MCPRegistry()
        failing_config = MCPHandlerConfig(name="failing")
        registry.register_builtin(EchoHandler(echo_config))
        registry.register_builtin(FailingHandler(failing_config))
        assert len(registry.list_all()) == 2

    def test_list_all_deduplication(self, echo_config):
        registry = MCPRegistry()
        registry.register_builtin(EchoHandler(echo_config))
        registry.register_custom(EchoHandler(echo_config))
        assert len(registry.list_all()) == 1

    def test_len(self, echo_config):
        registry = MCPRegistry()
        assert len(registry) == 0
        registry.register_builtin(EchoHandler(echo_config))
        assert len(registry) == 1

    def test_contains(self, echo_config):
        registry = MCPRegistry()
        assert "echo" not in registry
        registry.register_builtin(EchoHandler(echo_config))
        assert "echo" in registry

    def test_get_for_scope(self):
        registry = MCPRegistry()
        # Unscoped handler (unique name via config override)
        unscoped_cfg = MCPHandlerConfig(name="unscoped")
        unscoped = EchoHandler(unscoped_cfg)
        unscoped.name = "unscoped"
        registry.register_builtin(unscoped)
        # Scoped handler (unique name via config override)
        scoped_cfg = MCPHandlerConfig(
            name="scoped",
            stages=["build"],
            agents=["terraform-agent"],
        )
        scoped = EchoHandler(scoped_cfg)
        scoped.name = "scoped"
        registry.register_builtin(scoped)

        # No filter → both
        assert len(registry.get_for_scope()) == 2

        # Stage filter
        build_handlers = registry.get_for_scope(stage="build")
        assert len(build_handlers) == 2  # unscoped + scoped

        design_handlers = registry.get_for_scope(stage="design")
        assert len(design_handlers) == 1  # only unscoped

        # Agent filter
        tf_handlers = registry.get_for_scope(agent="terraform-agent")
        assert len(tf_handlers) == 2  # unscoped + scoped

        doc_handlers = registry.get_for_scope(agent="doc-agent")
        assert len(doc_handlers) == 1  # only unscoped


# ================================================================== #
# MCPManager tests
# ================================================================== #


class TestMCPManager:
    def test_get_tools_for_scope(self, registry_with_handlers):
        manager = MCPManager(registry_with_handlers)
        tools = manager.get_tools_for_scope()
        assert len(tools) == 2
        assert tools[0].name == "echo"
        assert tools[1].name == "reverse"

    def test_lazy_connect(self, echo_config):
        registry = MCPRegistry()
        handler = EchoHandler(echo_config)
        registry.register_builtin(handler)
        manager = MCPManager(registry)

        assert not handler._connected
        manager.get_tools_for_scope()
        assert handler._connected

    def test_call_tool(self, registry_with_handlers):
        manager = MCPManager(registry_with_handlers)
        manager.get_tools_for_scope()  # Triggers connect + tool map

        result = manager.call_tool("echo", {"text": "hello"})
        assert result.content == "hello"
        assert not result.is_error

    def test_call_tool_unknown(self, registry_with_handlers):
        manager = MCPManager(registry_with_handlers)
        result = manager.call_tool("nonexistent", {})
        assert result.is_error
        assert "Unknown tool" in result.error_message

    def test_circuit_breaker(self):
        registry = MCPRegistry()
        config = MCPHandlerConfig(name="failing")
        registry.register_builtin(FailingHandler(config))
        manager = MCPManager(registry)
        manager.get_tools_for_scope()

        # First two failures
        manager.call_tool("fail_tool", {})
        manager.call_tool("fail_tool", {})
        # Third failure triggers circuit breaker
        result = manager.call_tool("fail_tool", {})
        assert result.is_error

        # After circuit break, handler is unavailable
        result = manager.call_tool("fail_tool", {})
        assert result.is_error
        assert "unavailable" in result.error_message

    def test_circuit_breaker_resets_on_success(self, echo_config):
        """Successful calls reset the error counter."""
        registry = MCPRegistry()

        class SometimesFailHandler(MCPHandler):
            name = "sometimes"

            def __init__(self, config, **kwargs):
                super().__init__(config, **kwargs)
                self.call_count = 0

            def connect(self):
                self._connected = True

            def list_tools(self):
                return [MCPToolDefinition(
                    name="maybe",
                    description="Sometimes fails",
                    input_schema={},
                    handler_name="sometimes",
                )]

            def call_tool(self, name, arguments):
                self.call_count += 1
                if self.call_count <= 2:
                    return MCPToolResult(content="", is_error=True, error_message="fail")
                return MCPToolResult(content="ok")

            def disconnect(self):
                self._connected = False

        handler = SometimesFailHandler(MCPHandlerConfig(name="sometimes"))
        registry.register_builtin(handler)
        manager = MCPManager(registry)
        manager.get_tools_for_scope()

        # Two failures
        manager.call_tool("maybe", {})
        manager.call_tool("maybe", {})
        # Third call succeeds, resets counter
        result = manager.call_tool("maybe", {})
        assert not result.is_error
        assert manager._error_counts.get("sometimes", 0) == 0

    def test_connect_failure_marks_handler_failed(self):
        registry = MCPRegistry()
        config = MCPHandlerConfig(name="connect-fail")
        registry.register_builtin(ConnectFailHandler(config))
        manager = MCPManager(registry)

        tools = manager.get_tools_for_scope()
        assert len(tools) == 0
        assert "connect-fail" in manager._failed_handlers

    def test_shutdown_all(self, echo_config):
        registry = MCPRegistry()
        handler = EchoHandler(echo_config)
        registry.register_builtin(handler)
        manager = MCPManager(registry)
        manager.get_tools_for_scope()

        assert handler._connected
        manager.shutdown_all()
        assert not handler._connected
        assert len(manager._tool_map) == 0

    def test_context_manager(self, echo_config):
        registry = MCPRegistry()
        handler = EchoHandler(echo_config)
        registry.register_builtin(handler)

        with MCPManager(registry) as manager:
            manager.get_tools_for_scope()
            assert handler._connected

        assert not handler._connected

    def test_get_tools_as_openai_schema(self, registry_with_handlers):
        manager = MCPManager(registry_with_handlers)
        schema = manager.get_tools_as_openai_schema()

        assert len(schema) == 2
        assert schema[0]["type"] == "function"
        assert schema[0]["function"]["name"] == "echo"
        assert "parameters" in schema[0]["function"]

    def test_tool_name_collision(self, echo_config):
        """First-registered handler wins on collision."""
        registry = MCPRegistry()
        handler1 = EchoHandler(MCPHandlerConfig(name="first"))
        handler1.name = "first"
        handler2 = EchoHandler(MCPHandlerConfig(name="second"))
        handler2.name = "second"
        # Override handler_name on tools to match handler names
        for t in handler1._tools:
            t.handler_name = "first"
        for t in handler2._tools:
            t.handler_name = "second"
        registry.register_builtin(handler1)
        registry.register_builtin(handler2)
        manager = MCPManager(registry)

        tools = manager.get_tools_for_scope()
        # "echo" and "reverse" registered by first, collision from second
        handler_names = {manager._tool_map[t.name] for t in tools}
        assert "first" in handler_names

    def test_scoped_tools(self):
        registry = MCPRegistry()
        build_handler = EchoHandler(MCPHandlerConfig(
            name="build-only",
            stages=["build"],
        ))
        registry.register_builtin(build_handler)
        manager = MCPManager(registry)

        # In build scope - tools available
        tools = manager.get_tools_for_scope(stage="build")
        assert len(tools) == 2

        # In design scope - no tools (handler filtered out)
        # Reset internal state for clean test
        manager2 = MCPManager(registry)
        tools2 = manager2.get_tools_for_scope(stage="design")
        assert len(tools2) == 0

    def test_thread_safety(self, echo_config):
        """Call tools from multiple threads concurrently."""
        registry = MCPRegistry()
        registry.register_builtin(EchoHandler(echo_config))
        manager = MCPManager(registry)
        manager.get_tools_for_scope()

        results = []
        errors = []

        def call_echo(idx):
            try:
                r = manager.call_tool("echo", {"text": f"thread-{idx}"})
                results.append(r)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=call_echo, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10
        assert all(not r.is_error for r in results)


# ================================================================== #
# Loader tests
# ================================================================== #


class TestLoader:
    def test_load_handler_with_mcp_handler_class(self, tmp_path):
        handler_file = tmp_path / "test_handler.py"
        handler_file.write_text(
            "from azext_prototype.mcp.base import MCPHandler, MCPHandlerConfig, "
            "MCPToolDefinition, MCPToolResult\n\n"
            "class TestHandler(MCPHandler):\n"
            "    name = 'test'\n"
            "    def connect(self): self._connected = True\n"
            "    def list_tools(self): return []\n"
            "    def call_tool(self, name, args): return MCPToolResult(content='')\n"
            "    def disconnect(self): self._connected = False\n\n"
            "MCP_HANDLER_CLASS = TestHandler\n"
        )

        config = MCPHandlerConfig(name="test")
        handler = load_mcp_handler(str(handler_file), config)
        assert handler.name == "test"
        assert isinstance(handler, MCPHandler)

    def test_load_handler_auto_discover(self, tmp_path):
        handler_file = tmp_path / "auto_handler.py"
        handler_file.write_text(
            "from azext_prototype.mcp.base import MCPHandler, MCPHandlerConfig, "
            "MCPToolDefinition, MCPToolResult\n\n"
            "class AutoHandler(MCPHandler):\n"
            "    name = 'auto'\n"
            "    def connect(self): self._connected = True\n"
            "    def list_tools(self): return []\n"
            "    def call_tool(self, name, args): return MCPToolResult(content='')\n"
            "    def disconnect(self): self._connected = False\n"
        )

        config = MCPHandlerConfig(name="auto")
        handler = load_mcp_handler(str(handler_file), config)
        assert handler.name == "auto"

    def test_load_handler_file_not_found(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            load_mcp_handler(str(tmp_path / "missing.py"), MCPHandlerConfig(name="x"))

    def test_load_handler_wrong_extension(self, tmp_path):
        txt_file = tmp_path / "handler.txt"
        txt_file.write_text("not python")
        with pytest.raises(ValueError, match=".py"):
            load_mcp_handler(str(txt_file), MCPHandlerConfig(name="x"))

    def test_load_handler_no_class(self, tmp_path):
        handler_file = tmp_path / "empty_handler.py"
        handler_file.write_text("# no handler class\nx = 42\n")
        with pytest.raises(ValueError, match="No MCPHandler subclass"):
            load_mcp_handler(str(handler_file), MCPHandlerConfig(name="x"))

    def test_load_handler_multiple_classes(self, tmp_path):
        handler_file = tmp_path / "multi_handler.py"
        handler_file.write_text(
            "from azext_prototype.mcp.base import MCPHandler, MCPToolResult\n\n"
            "class HandlerA(MCPHandler):\n"
            "    name = 'a'\n"
            "    def connect(self): pass\n"
            "    def list_tools(self): return []\n"
            "    def call_tool(self, n, a): return MCPToolResult(content='')\n"
            "    def disconnect(self): pass\n\n"
            "class HandlerB(MCPHandler):\n"
            "    name = 'b'\n"
            "    def connect(self): pass\n"
            "    def list_tools(self): return []\n"
            "    def call_tool(self, n, a): return MCPToolResult(content='')\n"
            "    def disconnect(self): pass\n"
        )
        with pytest.raises(ValueError, match="Multiple"):
            load_mcp_handler(str(handler_file), MCPHandlerConfig(name="x"))

    def test_load_handler_invalid_mcp_handler_class(self, tmp_path):
        handler_file = tmp_path / "bad_handler.py"
        handler_file.write_text("MCP_HANDLER_CLASS = 'not a class'\n")
        with pytest.raises(ValueError, match="MCPHandler subclass"):
            load_mcp_handler(str(handler_file), MCPHandlerConfig(name="x"))

    def test_load_handlers_from_directory(self, tmp_path):
        handler_dir = tmp_path / "mcp"
        handler_dir.mkdir()

        (handler_dir / "echo_handler.py").write_text(
            "from azext_prototype.mcp.base import MCPHandler, MCPHandlerConfig, "
            "MCPToolDefinition, MCPToolResult\n\n"
            "class EchoHandler(MCPHandler):\n"
            "    name = 'echo'\n"
            "    def connect(self): self._connected = True\n"
            "    def list_tools(self): return []\n"
            "    def call_tool(self, name, args): return MCPToolResult(content='')\n"
            "    def disconnect(self): self._connected = False\n\n"
            "MCP_HANDLER_CLASS = EchoHandler\n"
        )

        configs = {"echo": MCPHandlerConfig(name="echo")}
        handlers = load_handlers_from_directory(str(handler_dir), configs)
        assert len(handlers) == 1
        assert handlers[0].name == "echo"

    def test_load_handlers_missing_config(self, tmp_path):
        """Handler file without matching config is skipped."""
        handler_dir = tmp_path / "mcp"
        handler_dir.mkdir()

        (handler_dir / "orphan_handler.py").write_text(
            "from azext_prototype.mcp.base import MCPHandler, MCPToolResult\n\n"
            "class OrphanHandler(MCPHandler):\n"
            "    name = 'orphan'\n"
            "    def connect(self): pass\n"
            "    def list_tools(self): return []\n"
            "    def call_tool(self, n, a): return MCPToolResult(content='')\n"
            "    def disconnect(self): pass\n"
        )

        handlers = load_handlers_from_directory(str(handler_dir), {})
        assert len(handlers) == 0

    def test_load_handlers_nonexistent_directory(self, tmp_path):
        handlers = load_handlers_from_directory(str(tmp_path / "nope"), {})
        assert len(handlers) == 0

    def test_load_handlers_skips_underscore_files(self, tmp_path):
        handler_dir = tmp_path / "mcp"
        handler_dir.mkdir()
        (handler_dir / "__init__.py").write_text("# init")
        (handler_dir / "_private.py").write_text("# private")

        handlers = load_handlers_from_directory(str(handler_dir), {})
        assert len(handlers) == 0

    def test_load_handlers_strips_handler_suffix(self, tmp_path):
        """Filename 'lightpanda_handler.py' maps to config name 'lightpanda'."""
        handler_dir = tmp_path / "mcp"
        handler_dir.mkdir()

        (handler_dir / "lightpanda_handler.py").write_text(
            "from azext_prototype.mcp.base import MCPHandler, MCPToolResult\n\n"
            "class LP(MCPHandler):\n"
            "    name = 'lightpanda'\n"
            "    def connect(self): self._connected = True\n"
            "    def list_tools(self): return []\n"
            "    def call_tool(self, n, a): return MCPToolResult(content='')\n"
            "    def disconnect(self): pass\n\n"
            "MCP_HANDLER_CLASS = LP\n"
        )

        configs = {"lightpanda": MCPHandlerConfig(name="lightpanda")}
        handlers = load_handlers_from_directory(str(handler_dir), configs)
        assert len(handlers) == 1
        assert handlers[0].name == "lightpanda"


# ================================================================== #
# Builtin registration tests
# ================================================================== #


class TestBuiltinRegistration:
    def test_register_all_builtin_mcp(self):
        from azext_prototype.mcp.builtin import register_all_builtin_mcp

        registry = MCPRegistry()
        register_all_builtin_mcp(registry)
        # Currently empty, just verify it doesn't crash
        assert len(registry) == 0


# ================================================================== #
# Package __init__ exports tests
# ================================================================== #


class TestPackageExports:
    def test_imports(self):
        from azext_prototype.mcp import (
            MCPHandler,
            MCPHandlerConfig,
            MCPManager,
            MCPRegistry,
            MCPToolCall,
            MCPToolDefinition,
            MCPToolResult,
        )
        assert MCPHandler is not None
        assert MCPManager is not None
