"""Tests for azext_prototype.agents — registry, loader, base."""

from unittest.mock import MagicMock

import pytest
import yaml
from knack.util import CLIError

from azext_prototype.agents.base import AgentCapability, BaseAgent
from azext_prototype.agents.registry import AgentRegistry
from azext_prototype.agents.loader import (
    load_agents_from_directory,
    load_python_agent,
    load_yaml_agent,
)
from azext_prototype.ai.provider import AIResponse


# --- A concrete agent for testing ---

class StubAgent(BaseAgent):
    """Minimal agent for testing."""

    def __init__(self, name="stub", capabilities=None):
        super().__init__(
            name=name,
            description="A stub agent for tests",
            capabilities=capabilities or [AgentCapability.DEVELOP],
        )

    def execute(self, context, task):
        return AIResponse(content="stub response", model="test")


class TestAgentRegistry:
    """Test agent registry resolution order."""

    def test_register_builtin(self):
        reg = AgentRegistry()
        agent = StubAgent("cloud-architect")
        reg.register_builtin(agent)

        assert "cloud-architect" in reg
        assert reg.get("cloud-architect") is agent

    def test_custom_overrides_builtin(self):
        reg = AgentRegistry()
        builtin = StubAgent("cloud-architect")
        custom = StubAgent("cloud-architect")
        custom._is_builtin = False

        reg.register_builtin(builtin)
        reg.register_custom(custom)

        resolved = reg.get("cloud-architect")
        assert resolved is custom

    def test_override_overrides_builtin(self):
        reg = AgentRegistry()
        builtin = StubAgent("cloud-architect")
        override = StubAgent("cloud-architect")

        reg.register_builtin(builtin)
        reg.register_override(override)

        resolved = reg.get("cloud-architect")
        assert resolved is override

    def test_custom_overrides_override(self):
        reg = AgentRegistry()
        builtin = StubAgent("cloud-architect")
        override = StubAgent("cloud-architect")
        custom = StubAgent("cloud-architect")

        reg.register_builtin(builtin)
        reg.register_override(override)
        reg.register_custom(custom)

        resolved = reg.get("cloud-architect")
        assert resolved is custom

    def test_get_missing_raises(self):
        reg = AgentRegistry()
        with pytest.raises(CLIError, match="not found"):
            reg.get("nonexistent")

    def test_find_by_capability(self):
        reg = AgentRegistry()
        arch = StubAgent("architect", [AgentCapability.ARCHITECT])
        dev = StubAgent("developer", [AgentCapability.DEVELOP])

        reg.register_builtin(arch)
        reg.register_builtin(dev)

        results = reg.find_by_capability(AgentCapability.ARCHITECT)
        assert len(results) == 1
        assert results[0].name == "architect"

    def test_remove_custom(self):
        reg = AgentRegistry()
        agent = StubAgent("my-agent")
        reg.register_custom(agent)

        assert reg.remove_custom("my-agent") is True
        assert "my-agent" not in reg

    def test_remove_custom_nonexistent(self):
        reg = AgentRegistry()
        assert reg.remove_custom("nope") is False

    def test_list_all(self):
        reg = AgentRegistry()
        reg.register_builtin(StubAgent("a"))
        reg.register_builtin(StubAgent("b"))

        assert len(reg.list_all()) == 2

    def test_list_names(self):
        reg = AgentRegistry()
        reg.register_builtin(StubAgent("alpha"))
        reg.register_builtin(StubAgent("beta"))

        names = reg.list_names()
        assert "alpha" in names
        assert "beta" in names

    def test_len(self):
        reg = AgentRegistry()
        reg.register_builtin(StubAgent("a"))
        assert len(reg) == 1

    def test_list_all_detailed(self):
        reg = AgentRegistry()
        reg.register_builtin(StubAgent("builtin-agent"))
        reg.register_custom(StubAgent("custom-agent"))

        detailed = reg.list_all_detailed()
        sources = {d["name"]: d["source"] for d in detailed}
        assert sources["builtin-agent"] == "builtin"
        assert sources["custom-agent"] == "custom"


class TestYAMLAgentLoader:
    """Test loading agents from YAML definitions."""

    def test_load_valid_yaml(self, tmp_path):
        definition = {
            "name": "test-agent",
            "description": "A test agent",
            "capabilities": ["develop"],
            "system_prompt": "You are a test agent.",
            "constraints": ["Only write tests."],
        }
        yaml_file = tmp_path / "test-agent.yaml"
        with open(yaml_file, "w") as f:
            yaml.dump(definition, f)

        agent = load_yaml_agent(str(yaml_file))
        assert agent.name == "test-agent"
        assert AgentCapability.DEVELOP in agent.capabilities
        assert not agent.is_builtin

    def test_load_yaml_missing_name_raises(self, tmp_path):
        definition = {"description": "No name"}
        yaml_file = tmp_path / "bad.yaml"
        with open(yaml_file, "w") as f:
            yaml.dump(definition, f)

        with pytest.raises(CLIError, match="must include 'name'"):
            load_yaml_agent(str(yaml_file))

    def test_load_yaml_file_not_found(self):
        with pytest.raises(CLIError, match="not found"):
            load_yaml_agent("/nonexistent/agent.yaml")

    def test_load_yaml_wrong_extension(self, tmp_path):
        txt_file = tmp_path / "agent.txt"
        txt_file.write_text("not yaml")

        with pytest.raises(CLIError, match="Expected .yaml"):
            load_yaml_agent(str(txt_file))

    def test_load_agents_from_directory(self, tmp_path):
        for i in range(3):
            definition = {"name": f"agent-{i}", "description": f"Agent {i}"}
            with open(tmp_path / f"agent_{i}.yaml", "w") as f:
                yaml.dump(definition, f)

        agents = load_agents_from_directory(str(tmp_path))
        assert len(agents) == 3

    def test_load_agents_from_empty_directory(self, tmp_path):
        agents = load_agents_from_directory(str(tmp_path))
        assert agents == []

    def test_load_agents_from_nonexistent_directory(self):
        agents = load_agents_from_directory("/nonexistent/dir")
        assert agents == []


class TestPythonAgentLoader:
    """Test loading agents from Python files."""

    def test_load_python_agent_with_agent_class(self, tmp_path):
        code = '''
from azext_prototype.agents.base import BaseAgent, AgentCapability
from azext_prototype.ai.provider import AIResponse

class MyAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="my-py-agent", description="Python agent",
                         capabilities=[AgentCapability.DEVELOP])
    def execute(self, context, task):
        return AIResponse(content="ok", model="test")

AGENT_CLASS = MyAgent
'''
        py_file = tmp_path / "my_agent.py"
        py_file.write_text(code)

        agent = load_python_agent(str(py_file))
        assert agent.name == "my-py-agent"

    def test_load_python_file_not_found(self):
        with pytest.raises(CLIError, match="not found"):
            load_python_agent("/nonexistent/agent.py")

    def test_load_python_wrong_extension(self, tmp_path):
        txt_file = tmp_path / "agent.txt"
        txt_file.write_text("not python")

        with pytest.raises(CLIError, match="Expected .py"):
            load_python_agent(str(txt_file))


class TestBuiltinRegistry:
    """Test that all built-in agents register successfully."""

    def test_all_builtin_agents_registered(self, populated_registry):
        expected = [
            "cloud-architect", "terraform-agent", "bicep-agent",
            "app-developer", "doc-agent", "qa-engineer",
            "biz-analyst", "cost-analyst", "project-manager",
            "security-reviewer", "monitoring-agent",
        ]
        for name in expected:
            assert name in populated_registry, f"Built-in agent '{name}' not registered"

    def test_all_builtin_agents_have_capabilities(self, populated_registry):
        for agent in populated_registry.list_all():
            assert len(agent.capabilities) > 0, f"Agent '{agent.name}' has no capabilities"

    def test_architect_capability_exists(self, populated_registry):
        archs = populated_registry.find_by_capability(AgentCapability.ARCHITECT)
        assert len(archs) >= 1

    def test_backlog_generation_capability_exists(self, populated_registry):
        agents = populated_registry.find_by_capability(AgentCapability.BACKLOG_GENERATION)
        assert len(agents) >= 1
        assert agents[0].name == "project-manager"


class TestProjectManagerAgent:
    """Test the project-manager built-in agent."""

    def test_instantiation(self):
        from azext_prototype.agents.builtin.project_manager import ProjectManagerAgent

        agent = ProjectManagerAgent()
        assert agent.name == "project-manager"
        assert AgentCapability.BACKLOG_GENERATION in agent.capabilities
        assert AgentCapability.ANALYZE in agent.capabilities
        assert agent._temperature == 0.4
        assert agent._max_tokens == 8192
        assert agent.is_builtin

    def test_can_handle_backlog_keywords(self):
        from azext_prototype.agents.builtin.project_manager import ProjectManagerAgent

        agent = ProjectManagerAgent()
        assert agent.can_handle("generate a backlog") > 0.3
        assert agent.can_handle("create user stories") > 0.3
        assert agent.can_handle("github issues for sprint") > 0.3
        assert agent.can_handle("devops work items") > 0.3

    def test_can_handle_unrelated_low_score(self):
        from azext_prototype.agents.builtin.project_manager import ProjectManagerAgent

        agent = ProjectManagerAgent()
        score = agent.can_handle("deploy kubernetes cluster")
        assert score <= 0.5

    def test_execute_github_provider(self, mock_agent_context):
        from azext_prototype.agents.builtin.project_manager import ProjectManagerAgent

        agent = ProjectManagerAgent()
        mock_agent_context.shared_state["backlog_provider"] = "github"

        # First call returns structured JSON, second call returns formatted output
        mock_agent_context.ai_provider.chat.side_effect = [
            AIResponse(
                content='[{"epic": "Infrastructure", "title": "Setup VNet", '
                '"description": "Create virtual network", '
                '"acceptance_criteria": ["VNet created"], '
                '"tasks": ["Define CIDR"], "effort": "M"}]',
                model="test",
            ),
            AIResponse(content="## Backlog\n- [ ] Setup VNet", model="test"),
        ]

        result = agent.execute(mock_agent_context, "Test architecture")
        assert result.content == "## Backlog\n- [ ] Setup VNet"
        assert mock_agent_context.ai_provider.chat.call_count == 2

    def test_execute_devops_provider(self, mock_agent_context):
        from azext_prototype.agents.builtin.project_manager import ProjectManagerAgent

        agent = ProjectManagerAgent()
        mock_agent_context.shared_state["backlog_provider"] = "devops"

        mock_agent_context.ai_provider.chat.side_effect = [
            AIResponse(
                content='[{"epic": "Data Layer", "title": "Setup DB", '
                '"description": "Provision database", '
                '"acceptance_criteria": ["DB online"], '
                '"tasks": ["Create schema"], "effort": "L"}]',
                model="test",
            ),
            AIResponse(content="### User Story: Setup DB", model="test"),
        ]

        result = agent.execute(mock_agent_context, "Test architecture")
        assert "Setup DB" in result.content

    def test_execute_defaults_to_github(self, mock_agent_context):
        from azext_prototype.agents.builtin.project_manager import ProjectManagerAgent

        agent = ProjectManagerAgent()
        # No backlog_provider set in shared_state — should default to github

        mock_agent_context.ai_provider.chat.side_effect = [
            AIResponse(content="[]", model="test"),
            AIResponse(content="Empty backlog", model="test"),
        ]

        agent.execute(mock_agent_context, "architecture")
        # Verify the format call mentions "github"
        second_call_messages = mock_agent_context.ai_provider.chat.call_args_list[1][0][0]
        user_msg = [m for m in second_call_messages if m.role == "user"][-1]
        assert "github" in user_msg.content.lower()

    def test_parse_items_valid_json(self):
        from azext_prototype.agents.builtin.project_manager import ProjectManagerAgent

        items = ProjectManagerAgent._parse_items(
            '[{"title": "Test", "tasks": []}]'
        )
        assert len(items) == 1
        assert items[0]["title"] == "Test"

    def test_parse_items_with_markdown_fences(self):
        from azext_prototype.agents.builtin.project_manager import ProjectManagerAgent

        items = ProjectManagerAgent._parse_items(
            '```json\n[{"title": "Fenced"}]\n```'
        )
        assert len(items) == 1
        assert items[0]["title"] == "Fenced"

    def test_parse_items_invalid_json_fallback(self):
        from azext_prototype.agents.builtin.project_manager import ProjectManagerAgent

        items = ProjectManagerAgent._parse_items("This is not JSON at all")
        assert len(items) == 1
        assert items[0]["title"] == "Backlog"
        assert "not JSON" in items[0]["description"]

    def test_parse_items_empty_array(self):
        from azext_prototype.agents.builtin.project_manager import ProjectManagerAgent

        items = ProjectManagerAgent._parse_items("[]")
        assert items == []

    def test_to_dict(self):
        from azext_prototype.agents.builtin.project_manager import ProjectManagerAgent

        agent = ProjectManagerAgent()
        d = agent.to_dict()
        assert d["name"] == "project-manager"
        assert "backlog_generation" in d["capabilities"]
        assert d["is_builtin"] is True

    def test_constraints_present(self):
        from azext_prototype.agents.builtin.project_manager import ProjectManagerAgent

        agent = ProjectManagerAgent()
        assert len(agent.constraints) >= 4
        constraint_text = " ".join(agent.constraints).lower()
        assert "acceptance criteria" in constraint_text
        assert "description" in constraint_text


# ======================================================================
# AzureRM version injection tests
# ======================================================================


class TestAzureApiVersionInjection:
    """Verify agents inject Azure API version into system messages."""

    def test_terraform_agent_injects_azure_api_version(self):
        from azext_prototype.agents.builtin.terraform_agent import TerraformAgent

        agent = TerraformAgent()
        messages = agent.get_system_messages()
        contents = [m.content for m in messages if isinstance(m.content, str)]
        joined = "\n".join(contents)
        assert "AZURE API VERSION: 2025-06-01" in joined
        assert "azapi" in joined
        assert "learn.microsoft.com" in joined

    def test_terraform_agent_injects_azapi_provider_version(self):
        from azext_prototype.agents.builtin.terraform_agent import TerraformAgent

        agent = TerraformAgent()
        messages = agent.get_system_messages()
        contents = [m.content for m in messages if isinstance(m.content, str)]
        joined = "\n".join(contents)
        assert "AZAPI PROVIDER VERSION: 2.8.0" in joined
        assert '~> 2.8.0' in joined

    def test_terraform_agent_constraint_says_pinned(self):
        from azext_prototype.agents.builtin.terraform_agent import TerraformAgent

        agent = TerraformAgent()
        constraint_text = " ".join(agent.constraints).lower()
        assert "pinned" in constraint_text

    def test_qa_agent_injects_azure_api_version(self):
        from azext_prototype.agents.builtin.qa_engineer import QAEngineerAgent

        agent = QAEngineerAgent()
        messages = agent.get_system_messages()
        contents = [m.content for m in messages if isinstance(m.content, str)]
        joined = "\n".join(contents)
        assert "AZURE API VERSION: 2025-06-01" in joined
        assert "learn.microsoft.com" in joined

    def test_bicep_agent_injects_azure_api_version(self):
        from azext_prototype.agents.builtin.bicep_agent import BicepAgent

        agent = BicepAgent()
        messages = agent.get_system_messages()
        contents = [m.content for m in messages if isinstance(m.content, str)]
        joined = "\n".join(contents)
        assert "AZURE API VERSION: 2025-06-01" in joined
        assert "learn.microsoft.com" in joined
        assert "deployment-language-bicep" in joined

    def test_cloud_architect_injects_azure_api_version_for_terraform(self):
        from azext_prototype.agents.builtin.cloud_architect import CloudArchitectAgent
        from azext_prototype.agents.base import AgentContext

        agent = CloudArchitectAgent()
        provider = MagicMock()
        provider.chat.return_value = AIResponse(content="test", model="test", usage={})
        context = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus", "iac_tool": "terraform"}},
            project_dir="/tmp/test",
            ai_provider=provider,
        )
        agent.execute(context, "Design an architecture")
        call_args = provider.chat.call_args
        messages = call_args[0][0]
        contents = [m.content for m in messages if isinstance(m.content, str)]
        joined = "\n".join(contents)
        assert "AZURE API VERSION: 2025-06-01" in joined
        assert "deployment-language-terraform" in joined

    def test_cloud_architect_injects_azure_api_version_for_bicep(self):
        from azext_prototype.agents.builtin.cloud_architect import CloudArchitectAgent
        from azext_prototype.agents.base import AgentContext

        agent = CloudArchitectAgent()
        provider = MagicMock()
        provider.chat.return_value = AIResponse(content="test", model="test", usage={})
        context = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus", "iac_tool": "bicep"}},
            project_dir="/tmp/test",
            ai_provider=provider,
        )
        agent.execute(context, "Design an architecture")
        call_args = provider.chat.call_args
        messages = call_args[0][0]
        contents = [m.content for m in messages if isinstance(m.content, str)]
        joined = "\n".join(contents)
        assert "AZURE API VERSION: 2025-06-01" in joined
        assert "deployment-language-bicep" in joined
