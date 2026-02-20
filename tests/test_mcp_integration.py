"""Integration tests for MCP + Agent tool call loop.

Tests the end-to-end flow: agent gets MCP tools → AI requests tool calls
→ agent invokes via MCPManager → feeds results back → AI responds.
"""

import json
from unittest.mock import MagicMock, patch, call

import pytest

from azext_prototype.agents.base import AgentContext, BaseAgent
from azext_prototype.ai.provider import AIMessage, AIResponse, ToolCall
from azext_prototype.mcp.base import (
    MCPHandler,
    MCPHandlerConfig,
    MCPToolDefinition,
    MCPToolResult,
)
from azext_prototype.mcp.manager import MCPManager
from azext_prototype.mcp.registry import MCPRegistry


# -------------------------------------------------------------------- #
# Test handler
# -------------------------------------------------------------------- #


class MockMCPHandler(MCPHandler):
    """In-process handler for integration tests."""

    name = "mock-mcp"
    description = "Mock MCP handler for testing"

    def __init__(self, config, **kwargs):
        super().__init__(config, **kwargs)
        self._tools = [
            MCPToolDefinition(
                name="get_weather",
                description="Get current weather for a location",
                input_schema={
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"],
                },
                handler_name="mock-mcp",
            ),
            MCPToolDefinition(
                name="search_docs",
                description="Search documentation",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
                handler_name="mock-mcp",
            ),
        ]
        self._responses = {
            "get_weather": "Sunny, 72F",
            "search_docs": "Azure App Service supports Python 3.12",
        }

    def connect(self):
        self._connected = True

    def list_tools(self):
        return list(self._tools)

    def call_tool(self, name, arguments):
        content = self._responses.get(name, f"Unknown tool: {name}")
        return MCPToolResult(content=content, metadata={"handler": self.name})

    def disconnect(self):
        self._connected = False


# -------------------------------------------------------------------- #
# Fixtures
# -------------------------------------------------------------------- #


@pytest.fixture
def mock_mcp_manager():
    """Create an MCPManager with the mock handler."""
    registry = MCPRegistry()
    config = MCPHandlerConfig(name="mock-mcp")
    handler = MockMCPHandler(config)
    registry.register_builtin(handler)
    return MCPManager(registry)


@pytest.fixture
def agent_context_with_mcp(project_with_config, sample_config, mock_mcp_manager):
    """AgentContext with MCP manager and mock AI provider."""
    provider = MagicMock()
    provider.provider_name = "github-models"
    provider.default_model = "gpt-4o"

    return AgentContext(
        project_config=sample_config,
        project_dir=str(project_with_config),
        ai_provider=provider,
        mcp_manager=mock_mcp_manager,
    )


# ================================================================== #
# Agent + MCP tool call loop tests
# ================================================================== #


class TestAgentMCPToolCallLoop:
    """Test the tool call loop in BaseAgent.execute()."""

    def test_no_mcp_manager_skips_tools(self, mock_agent_context):
        """When mcp_manager is None, no tools are passed to AI."""
        agent = BaseAgent(
            name="test-agent",
            description="Test agent",
        )
        agent._governance_aware = False

        mock_agent_context.ai_provider.chat.return_value = AIResponse(
            content="Hello", model="gpt-4o", usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

        response = agent.execute(mock_agent_context, "say hello")
        assert response.content == "Hello"

        # Verify tools was None
        call_kwargs = mock_agent_context.ai_provider.chat.call_args
        assert call_kwargs.kwargs.get("tools") is None or call_kwargs[1].get("tools") is None

    def test_mcp_tools_passed_to_ai(self, agent_context_with_mcp):
        """MCP tools are passed to the AI provider as OpenAI function schema."""
        agent = BaseAgent(
            name="test-agent",
            description="Test agent",
        )
        agent._governance_aware = False

        # AI responds without tool calls
        agent_context_with_mcp.ai_provider.chat.return_value = AIResponse(
            content="No tools needed",
            model="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

        response = agent.execute(agent_context_with_mcp, "simple question")
        assert response.content == "No tools needed"

        # Verify tools were passed
        call_kwargs = agent_context_with_mcp.ai_provider.chat.call_args
        tools = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools")
        assert tools is not None
        assert len(tools) == 2
        assert tools[0]["function"]["name"] == "get_weather"

    def test_single_tool_call_loop(self, agent_context_with_mcp):
        """AI requests one tool call, agent invokes it, AI responds with result."""
        agent = BaseAgent(
            name="test-agent",
            description="Test agent",
        )
        agent._governance_aware = False

        # First call: AI requests a tool call
        first_response = AIResponse(
            content="",
            model="gpt-4o",
            usage={"prompt_tokens": 50, "completion_tokens": 10},
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(id="call_1", name="get_weather", arguments='{"location": "Seattle"}'),
            ],
        )

        # Second call: AI responds with final content
        second_response = AIResponse(
            content="The weather in Seattle is Sunny, 72F.",
            model="gpt-4o",
            usage={"prompt_tokens": 80, "completion_tokens": 20},
            finish_reason="stop",
        )

        agent_context_with_mcp.ai_provider.chat.side_effect = [first_response, second_response]

        response = agent.execute(agent_context_with_mcp, "What's the weather in Seattle?")

        assert response.content == "The weather in Seattle is Sunny, 72F."
        assert agent_context_with_mcp.ai_provider.chat.call_count == 2

        # Verify tool result was fed back
        second_call = agent_context_with_mcp.ai_provider.chat.call_args_list[1]
        messages = second_call.args[0] if second_call.args else second_call.kwargs.get("messages")
        tool_messages = [m for m in messages if m.role == "tool"]
        assert len(tool_messages) == 1
        assert tool_messages[0].content == "Sunny, 72F"
        assert tool_messages[0].tool_call_id == "call_1"

    def test_multiple_tool_calls_single_turn(self, agent_context_with_mcp):
        """AI requests multiple tool calls in one response."""
        agent = BaseAgent(
            name="test-agent",
            description="Test agent",
        )
        agent._governance_aware = False

        # AI requests two tools at once
        first_response = AIResponse(
            content="",
            model="gpt-4o",
            usage={"prompt_tokens": 50, "completion_tokens": 10},
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(id="call_1", name="get_weather", arguments='{"location": "NYC"}'),
                ToolCall(id="call_2", name="search_docs", arguments='{"query": "app service"}'),
            ],
        )

        second_response = AIResponse(
            content="NYC weather is Sunny and Azure App Service supports Python 3.12.",
            model="gpt-4o",
            usage={"prompt_tokens": 100, "completion_tokens": 30},
            finish_reason="stop",
        )

        agent_context_with_mcp.ai_provider.chat.side_effect = [first_response, second_response]

        response = agent.execute(agent_context_with_mcp, "Weather and docs?")
        assert "Sunny" in response.content
        assert "Python 3.12" in response.content

        # Verify both tool results were fed back
        second_call = agent_context_with_mcp.ai_provider.chat.call_args_list[1]
        messages = second_call.args[0] if second_call.args else second_call.kwargs.get("messages")
        tool_messages = [m for m in messages if m.role == "tool"]
        assert len(tool_messages) == 2

    def test_multi_turn_tool_calls(self, agent_context_with_mcp):
        """AI makes tool calls across multiple turns."""
        agent = BaseAgent(
            name="test-agent",
            description="Test agent",
        )
        agent._governance_aware = False

        # Turn 1: AI requests first tool
        turn1 = AIResponse(
            content="",
            model="gpt-4o",
            usage={"prompt_tokens": 50, "completion_tokens": 5},
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(id="call_1", name="get_weather", arguments='{"location": "LA"}'),
            ],
        )

        # Turn 2: AI requests second tool based on first result
        turn2 = AIResponse(
            content="",
            model="gpt-4o",
            usage={"prompt_tokens": 80, "completion_tokens": 5},
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(id="call_2", name="search_docs", arguments='{"query": "deploy sunny"}'),
            ],
        )

        # Turn 3: Final response
        turn3 = AIResponse(
            content="LA is sunny. Here are the deploy docs.",
            model="gpt-4o",
            usage={"prompt_tokens": 100, "completion_tokens": 20},
            finish_reason="stop",
        )

        agent_context_with_mcp.ai_provider.chat.side_effect = [turn1, turn2, turn3]

        response = agent.execute(agent_context_with_mcp, "Weather and deploy?")
        assert agent_context_with_mcp.ai_provider.chat.call_count == 3
        assert "sunny" in response.content.lower()

    def test_max_iterations_enforced(self, agent_context_with_mcp):
        """Tool call loop stops after max iterations."""
        agent = BaseAgent(
            name="test-agent",
            description="Test agent",
        )
        agent._governance_aware = False
        agent._max_tool_iterations = 3

        # AI keeps requesting tools forever
        infinite_response = AIResponse(
            content="need more",
            model="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(id="call_x", name="get_weather", arguments='{"location": "mars"}'),
            ],
        )

        agent_context_with_mcp.ai_provider.chat.return_value = infinite_response

        response = agent.execute(agent_context_with_mcp, "infinite loop")

        # Should stop after max_tool_iterations + 1 (initial + 3 loop)
        assert agent_context_with_mcp.ai_provider.chat.call_count == 4  # 1 initial + 3 loop

    def test_tool_call_with_invalid_json_arguments(self, agent_context_with_mcp):
        """Gracefully handles invalid JSON in tool call arguments."""
        agent = BaseAgent(
            name="test-agent",
            description="Test agent",
        )
        agent._governance_aware = False

        first_response = AIResponse(
            content="",
            model="gpt-4o",
            usage={"prompt_tokens": 50, "completion_tokens": 10},
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(id="call_1", name="get_weather", arguments="not valid json"),
            ],
        )

        second_response = AIResponse(
            content="Handled the bad args",
            model="gpt-4o",
            usage={"prompt_tokens": 80, "completion_tokens": 20},
            finish_reason="stop",
        )

        agent_context_with_mcp.ai_provider.chat.side_effect = [first_response, second_response]

        # Should not raise
        response = agent.execute(agent_context_with_mcp, "test")
        assert response.content == "Handled the bad args"

    def test_tool_call_error_result(self, agent_context_with_mcp):
        """Tool errors are fed back to the AI as error messages."""
        agent = BaseAgent(
            name="test-agent",
            description="Test agent",
        )
        agent._governance_aware = False

        first_response = AIResponse(
            content="",
            model="gpt-4o",
            usage={"prompt_tokens": 50, "completion_tokens": 10},
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(id="call_1", name="nonexistent_tool", arguments="{}"),
            ],
        )

        second_response = AIResponse(
            content="Tool failed, but I can still help",
            model="gpt-4o",
            usage={"prompt_tokens": 80, "completion_tokens": 20},
            finish_reason="stop",
        )

        agent_context_with_mcp.ai_provider.chat.side_effect = [first_response, second_response]

        response = agent.execute(agent_context_with_mcp, "use unknown tool")
        assert response.content == "Tool failed, but I can still help"

        # Verify error was in the tool message
        second_call = agent_context_with_mcp.ai_provider.chat.call_args_list[1]
        messages = second_call.args[0] if second_call.args else second_call.kwargs.get("messages")
        tool_messages = [m for m in messages if m.role == "tool"]
        assert len(tool_messages) == 1
        assert "Error:" in tool_messages[0].content

    def test_usage_merged_across_turns(self, agent_context_with_mcp):
        """Token usage is accumulated across all turns."""
        agent = BaseAgent(
            name="test-agent",
            description="Test agent",
        )
        agent._governance_aware = False

        first_response = AIResponse(
            content="",
            model="gpt-4o",
            usage={"prompt_tokens": 100, "completion_tokens": 10},
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(id="call_1", name="get_weather", arguments='{"location": "SF"}'),
            ],
        )

        second_response = AIResponse(
            content="SF is foggy",
            model="gpt-4o",
            usage={"prompt_tokens": 200, "completion_tokens": 30},
            finish_reason="stop",
        )

        agent_context_with_mcp.ai_provider.chat.side_effect = [first_response, second_response]

        response = agent.execute(agent_context_with_mcp, "weather")
        assert response.usage["prompt_tokens"] == 300  # 100 + 200
        assert response.usage["completion_tokens"] == 40  # 10 + 30

    def test_enable_mcp_tools_false(self, agent_context_with_mcp):
        """Agent with _enable_mcp_tools=False doesn't use MCP tools."""
        agent = BaseAgent(
            name="no-mcp-agent",
            description="Agent without MCP",
        )
        agent._enable_mcp_tools = False
        agent._governance_aware = False

        agent_context_with_mcp.ai_provider.chat.return_value = AIResponse(
            content="No tools used",
            model="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

        response = agent.execute(agent_context_with_mcp, "test")
        assert response.content == "No tools used"

        call_kwargs = agent_context_with_mcp.ai_provider.chat.call_args
        tools = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools")
        assert tools is None


# ================================================================== #
# AI Provider tool calling dataclass tests
# ================================================================== #


class TestToolCallDataclasses:
    def test_tool_call_dataclass(self):
        tc = ToolCall(id="call_1", name="test", arguments='{"key": "value"}')
        assert tc.id == "call_1"
        assert tc.name == "test"
        assert tc.arguments == '{"key": "value"}'

    def test_ai_message_with_tool_calls(self):
        tc = ToolCall(id="call_1", name="get_weather", arguments="{}")
        msg = AIMessage(
            role="assistant",
            content="",
            tool_calls=[tc],
        )
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "get_weather"

    def test_ai_message_tool_result(self):
        msg = AIMessage(
            role="tool",
            content="Sunny, 72F",
            tool_call_id="call_1",
        )
        assert msg.role == "tool"
        assert msg.tool_call_id == "call_1"

    def test_ai_response_with_tool_calls(self):
        tc = ToolCall(id="call_1", name="search", arguments="{}")
        resp = AIResponse(
            content="",
            model="gpt-4o",
            usage={},
            finish_reason="tool_calls",
            tool_calls=[tc],
        )
        assert resp.tool_calls is not None
        assert resp.finish_reason == "tool_calls"

    def test_ai_response_backward_compat(self):
        """Existing code that doesn't use tool_calls still works."""
        resp = AIResponse(
            content="Hello",
            model="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        assert resp.tool_calls is None
        assert resp.finish_reason == "stop"

    def test_ai_message_backward_compat(self):
        """Existing code that doesn't use tool fields still works."""
        msg = AIMessage(role="user", content="hello")
        assert msg.tool_calls is None
        assert msg.tool_call_id is None


# ================================================================== #
# Config integration tests
# ================================================================== #


class TestMCPConfig:
    def test_default_config_includes_mcp(self):
        from azext_prototype.config import DEFAULT_CONFIG

        assert "mcp" in DEFAULT_CONFIG
        assert "servers" in DEFAULT_CONFIG["mcp"]
        assert "custom_dir" in DEFAULT_CONFIG["mcp"]
        assert DEFAULT_CONFIG["mcp"]["servers"] == []
        assert DEFAULT_CONFIG["mcp"]["custom_dir"] == ".prototype/mcp/"

    def test_mcp_servers_in_secret_prefixes(self):
        from azext_prototype.config import SECRET_KEY_PREFIXES

        assert "mcp.servers" in SECRET_KEY_PREFIXES


# ================================================================== #
# AgentContext mcp_manager field tests
# ================================================================== #


class TestAgentContextMCPManager:
    def test_default_none(self, project_with_config, sample_config):
        ctx = AgentContext(
            project_config=sample_config,
            project_dir=str(project_with_config),
            ai_provider=None,
        )
        assert ctx.mcp_manager is None

    def test_with_manager(self, project_with_config, sample_config, mock_mcp_manager):
        ctx = AgentContext(
            project_config=sample_config,
            project_dir=str(project_with_config),
            ai_provider=None,
            mcp_manager=mock_mcp_manager,
        )
        assert ctx.mcp_manager is mock_mcp_manager


# ================================================================== #
# Custom.py _build_mcp_manager tests
# ================================================================== #


class TestBuildMCPManager:
    def test_returns_none_when_no_servers(self, project_with_config):
        """No MCP servers configured → returns None."""
        from azext_prototype.config import ProjectConfig

        config = ProjectConfig(str(project_with_config))
        config.load()

        from azext_prototype.custom import _build_mcp_manager
        result = _build_mcp_manager(config, str(project_with_config))
        assert result is None

    def test_returns_manager_with_custom_handler(self, project_with_config):
        """Custom handler file + config → MCPManager returned."""
        import yaml

        # Write MCP config to prototype.yaml
        config_path = project_with_config / "prototype.yaml"
        with open(config_path) as f:
            config_data = yaml.safe_load(f)

        # Config name "echotest" must match filename "echotest_handler.py"
        # after stripping the _handler suffix
        config_data["mcp"] = {
            "servers": [
                {
                    "name": "echotest",
                    "settings": {"url": "http://localhost:9999"},
                }
            ],
            "custom_dir": ".prototype/mcp/",
        }

        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        # Create custom handler file — "echotest_handler.py" → name "echotest"
        mcp_dir = project_with_config / ".prototype" / "mcp"
        mcp_dir.mkdir(parents=True, exist_ok=True)

        (mcp_dir / "echotest_handler.py").write_text(
            "from azext_prototype.mcp.base import MCPHandler, MCPToolResult, MCPToolDefinition\n\n"
            "class TestH(MCPHandler):\n"
            "    def connect(self): self._connected = True\n"
            "    def list_tools(self): return []\n"
            "    def call_tool(self, n, a): return MCPToolResult(content='')\n"
            "    def disconnect(self): pass\n\n"
            "MCP_HANDLER_CLASS = TestH\n"
        )

        from azext_prototype.config import ProjectConfig
        config = ProjectConfig(str(project_with_config))
        config.load()

        from azext_prototype.custom import _build_mcp_manager
        manager = _build_mcp_manager(config, str(project_with_config))
        assert manager is not None
