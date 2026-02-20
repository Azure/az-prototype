"""Tests for azext_prototype.agents.governance — governance-aware agent system.

Tests the GovernanceContext bridge, BaseAgent governance integration,
and post-response validation across all built-in agents.
"""

import pytest
from unittest.mock import MagicMock, patch

from azext_prototype.agents.base import BaseAgent, AgentCapability, AgentContext
from azext_prototype.agents.governance import GovernanceContext, reset_caches
from azext_prototype.ai.provider import AIResponse
from azext_prototype.governance.policies import PolicyEngine
from azext_prototype.templates.registry import TemplateRegistry


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture(autouse=True)
def _clean_governance_caches():
    """Reset module-level singleton caches before each test."""
    reset_caches()
    yield
    reset_caches()


@pytest.fixture
def policy_engine():
    """Return a real PolicyEngine loaded from shipped policies."""
    engine = PolicyEngine()
    engine.load()
    return engine


@pytest.fixture
def template_registry():
    """Return a real TemplateRegistry loaded from shipped templates."""
    reg = TemplateRegistry()
    reg.load()
    return reg


@pytest.fixture
def governance_ctx(policy_engine, template_registry):
    """Pre-wired GovernanceContext."""
    return GovernanceContext(
        policy_engine=policy_engine,
        template_registry=template_registry,
    )



@pytest.fixture
def mock_agent_context(tmp_path, mock_ai_provider):
    """Minimal AgentContext for governance tests."""
    return AgentContext(
        project_config={"project": {"name": "test"}},
        project_dir=str(tmp_path),
        ai_provider=mock_ai_provider,
    )


# ------------------------------------------------------------------ #
# GovernanceContext — unit tests
# ------------------------------------------------------------------ #

class TestGovernanceContext:
    """Test GovernanceContext formatting and validation."""

    def test_format_policies_returns_non_empty(self, governance_ctx):
        """Policies for cloud-architect should include at least some rules."""
        text = governance_ctx.format_policies("cloud-architect")
        assert "Governance Policies" in text
        assert "MUST" in text or "SHOULD" in text

    def test_format_policies_with_services_filter(self, governance_ctx):
        text = governance_ctx.format_policies("cloud-architect", services=["key_vault"])
        # Should still produce output (may be a subset)
        assert isinstance(text, str)

    def test_format_templates_returns_non_empty(self, governance_ctx):
        text = governance_ctx.format_templates()
        assert "Workload Templates" in text

    def test_format_templates_with_category(self, governance_ctx):
        text = governance_ctx.format_templates(category="web")
        # May or may not have templates in 'web' — just ensure it doesn't crash
        assert isinstance(text, str)

    def test_format_all_includes_policies_and_templates(self, governance_ctx):
        text = governance_ctx.format_all("cloud-architect", include_templates=True)
        assert "Governance Policies" in text
        assert "Workload Templates" in text

    def test_format_all_without_templates(self, governance_ctx):
        text = governance_ctx.format_all("cloud-architect", include_templates=False)
        assert "Governance Policies" in text
        assert "Workload Templates" not in text

    def test_format_all_for_unknown_agent(self, governance_ctx):
        """An unrecognised agent name should still return text (policies apply broadly)."""
        text = governance_ctx.format_all("nonexistent-agent")
        # Some policies have no applies_to filter, so they apply to everyone
        assert isinstance(text, str)

    def test_check_response_clean(self, governance_ctx):
        """A clean response should produce zero warnings."""
        warnings = governance_ctx.check_response_for_violations(
            "cloud-architect",
            "Use Azure Key Vault with RBAC and managed identity.",
        )
        assert warnings == []

    def test_check_response_detects_credentials(self, governance_ctx):
        """Credential patterns trigger a warning."""
        warnings = governance_ctx.check_response_for_violations(
            "cloud-architect",
            'connection_string = "Server=mydb;Password=oops"',
        )
        assert any("credential" in w.lower() or "secret" in w.lower() for w in warnings)

    def test_check_response_detects_access_key(self, governance_ctx):
        warnings = governance_ctx.check_response_for_violations(
            "cloud-architect",
            "Use the storage account access_key to authenticate.",
        )
        assert len(warnings) > 0

    def test_check_response_detects_client_secret(self, governance_ctx):
        warnings = governance_ctx.check_response_for_violations(
            "bicep-agent",
            "Set the client_secret parameter in the application registration.",
        )
        assert len(warnings) > 0

    def test_check_response_detects_password_assignment(self, governance_ctx):
        warnings = governance_ctx.check_response_for_violations(
            "terraform-agent",
            'password = "hunter2"',
        )
        assert len(warnings) > 0

    def test_default_singletons_are_lazily_created(self):
        """When no engine/registry injected, GovernanceContext creates singletons."""
        ctx = GovernanceContext()
        # The singleton should be usable
        text = ctx.format_policies("cloud-architect")
        assert isinstance(text, str)

    def test_reset_caches(self):
        """reset_caches() should clear singletons."""
        # Trigger lazy init
        _ = GovernanceContext()
        reset_caches()
        # After reset, next GovernanceContext should re-create them
        ctx2 = GovernanceContext()
        text = ctx2.format_policies("cloud-architect")
        assert isinstance(text, str)


# ------------------------------------------------------------------ #
# BaseAgent governance integration
# ------------------------------------------------------------------ #

class _GovernanceStub(BaseAgent):
    """Minimal agent for governance integration tests."""

    def __init__(self, name="test-gov", governance_aware=True, include_templates=True):
        super().__init__(
            name=name,
            description="Test governance integration",
            capabilities=[AgentCapability.DEVELOP],
            system_prompt="You are a test agent.",
        )
        self._governance_aware = governance_aware
        self._include_templates = include_templates


class TestBaseAgentGovernanceIntegration:
    """Test that BaseAgent properly injects governance context."""

    def test_system_messages_include_governance(self, governance_ctx):
        agent = _GovernanceStub()
        messages = agent.get_system_messages()

        # Should have: system prompt, constraints (empty), governance
        governance_msgs = [m for m in messages if "Governance" in m.content or "Workload" in m.content]
        assert len(governance_msgs) >= 1

    def test_system_messages_skip_governance_when_disabled(self):
        agent = _GovernanceStub(governance_aware=False)
        messages = agent.get_system_messages()

        governance_msgs = [m for m in messages if "Governance" in m.content]
        assert governance_msgs == []

    def test_system_messages_skip_templates_when_disabled(self, governance_ctx):
        agent = _GovernanceStub(include_templates=False)
        messages = agent.get_system_messages()

        template_msgs = [m for m in messages if "Workload Templates" in m.content]
        assert template_msgs == []

    def test_validate_response_returns_empty_for_clean(self, governance_ctx):
        agent = _GovernanceStub()
        warnings = agent.validate_response("Use managed identity with Key Vault RBAC.")
        assert warnings == []

    def test_validate_response_returns_warnings_for_credentials(self, governance_ctx):
        agent = _GovernanceStub()
        warnings = agent.validate_response('connectionString = "Server=x;Password=y"')
        assert len(warnings) > 0

    def test_validate_response_skipped_when_not_aware(self):
        agent = _GovernanceStub(governance_aware=False)
        warnings = agent.validate_response("connection_string = bad")
        assert warnings == []

    def test_execute_appends_governance_warnings(self, mock_agent_context, governance_ctx):
        """When AI returns problematic content, warnings are appended."""
        agent = _GovernanceStub()
        mock_agent_context.ai_provider.chat.return_value = AIResponse(
            content='Use connection_string = "Server=abc;Password=oops"',
            model="test",
        )

        result = agent.execute(mock_agent_context, "Generate config")
        assert "Governance warnings" in result.content or "governance" in result.content.lower()

    def test_execute_no_warnings_for_clean_response(self, mock_agent_context, governance_ctx):
        """A clean response should not have governance warnings appended."""
        agent = _GovernanceStub()
        mock_agent_context.ai_provider.chat.return_value = AIResponse(
            content="Use managed identity and Key Vault references.",
            model="test",
        )

        result = agent.execute(mock_agent_context, "Generate config")
        assert "Governance warnings" not in result.content

    def test_governance_error_does_not_break_execute(self, mock_agent_context):
        """If GovernanceContext fails, execute() still returns."""
        agent = _GovernanceStub()
        # Force governance to fail by patching
        with patch(
            "azext_prototype.agents.governance.GovernanceContext.check_response_for_violations",
            side_effect=RuntimeError("boom"),
        ):
            result = agent.execute(mock_agent_context, "do stuff")
        # Should still get the AI response back
        assert result.content == "Mock AI response content"


# ------------------------------------------------------------------ #
# Built-in agents — governance flag tests
# ------------------------------------------------------------------ #

class TestBuiltinAgentGovernanceFlags:
    """Verify that all built-in agents have correct governance flags set."""

    @pytest.mark.parametrize(
        "agent_cls_path,expected_include_templates",
        [
            ("azext_prototype.agents.builtin.cloud_architect.CloudArchitectAgent", True),
            ("azext_prototype.agents.builtin.terraform_agent.TerraformAgent", True),
            ("azext_prototype.agents.builtin.bicep_agent.BicepAgent", True),
            ("azext_prototype.agents.builtin.app_developer.AppDeveloperAgent", True),
            ("azext_prototype.agents.builtin.cost_analyst.CostAnalystAgent", False),
            ("azext_prototype.agents.builtin.biz_analyst.BizAnalystAgent", True),
            ("azext_prototype.agents.builtin.qa_engineer.QAEngineerAgent", False),
            ("azext_prototype.agents.builtin.doc_agent.DocumentationAgent", False),
            ("azext_prototype.agents.builtin.project_manager.ProjectManagerAgent", False),
        ],
    )
    def test_include_templates_flag(self, agent_cls_path, expected_include_templates):
        import importlib

        module_path, cls_name = agent_cls_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, cls_name)
        agent = cls()
        assert agent._include_templates is expected_include_templates, (
            f"{cls_name}._include_templates should be {expected_include_templates}"
        )

    @pytest.mark.parametrize(
        "agent_cls_path",
        [
            "azext_prototype.agents.builtin.cloud_architect.CloudArchitectAgent",
            "azext_prototype.agents.builtin.terraform_agent.TerraformAgent",
            "azext_prototype.agents.builtin.bicep_agent.BicepAgent",
            "azext_prototype.agents.builtin.app_developer.AppDeveloperAgent",
            "azext_prototype.agents.builtin.cost_analyst.CostAnalystAgent",
            "azext_prototype.agents.builtin.biz_analyst.BizAnalystAgent",
            "azext_prototype.agents.builtin.qa_engineer.QAEngineerAgent",
            "azext_prototype.agents.builtin.doc_agent.DocumentationAgent",
            "azext_prototype.agents.builtin.project_manager.ProjectManagerAgent",
        ],
    )
    def test_all_agents_governance_aware(self, agent_cls_path):
        """Every built-in agent should be governance-aware by default."""
        import importlib

        module_path, cls_name = agent_cls_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, cls_name)
        agent = cls()
        assert agent._governance_aware is True, (
            f"{cls_name} should have _governance_aware = True"
        )


# ------------------------------------------------------------------ #
# Built-in agents — system messages include governance
# ------------------------------------------------------------------ #

class TestBuiltinAgentSystemMessages:
    """Verify system messages include governance context."""

    @pytest.fixture(autouse=True)
    def _setup_governance(self, policy_engine, template_registry):
        """Ensure real policies/templates are loaded in the singletons."""
        # Inject into module-level caches so agents pick them up
        import azext_prototype.agents.governance as gov_mod
        gov_mod._policy_engine = policy_engine
        gov_mod._template_registry = template_registry

    @pytest.mark.parametrize(
        "agent_cls_path,expects_templates",
        [
            ("azext_prototype.agents.builtin.cloud_architect.CloudArchitectAgent", True),
            ("azext_prototype.agents.builtin.terraform_agent.TerraformAgent", True),
            ("azext_prototype.agents.builtin.bicep_agent.BicepAgent", True),
            ("azext_prototype.agents.builtin.app_developer.AppDeveloperAgent", True),
            ("azext_prototype.agents.builtin.cost_analyst.CostAnalystAgent", False),
            ("azext_prototype.agents.builtin.biz_analyst.BizAnalystAgent", True),
        ],
    )
    def test_system_messages_contain_governance(self, agent_cls_path, expects_templates):
        import importlib

        module_path, cls_name = agent_cls_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, cls_name)
        agent = cls()

        messages = agent.get_system_messages()
        all_content = "\n".join(m.content for m in messages)

        assert "Governance Policies" in all_content, (
            f"{cls_name} system messages should include governance policies"
        )

        if expects_templates:
            assert "Workload Templates" in all_content, (
                f"{cls_name} system messages should include templates"
            )
        else:
            assert "Workload Templates" not in all_content, (
                f"{cls_name} system messages should NOT include templates"
            )


    def test_biz_analyst_gets_architectural_policies(self):
        """Biz-analyst should receive architectural-level policies and
        templates to inform discovery conversations."""
        from azext_prototype.agents.builtin.biz_analyst import BizAnalystAgent

        agent = BizAnalystAgent()
        messages = agent.get_system_messages()
        all_content = "\n".join(m.content for m in messages)

        # Should include governance policies
        assert "Governance Policies" in all_content
        # Should include templates (for template-aware discovery)
        assert "Workload Templates" in all_content
        # Spot-check a few key rules it should know about
        assert "MI-001" in all_content or "managed identity" in all_content.lower()
        assert "NET-001" in all_content or "private endpoint" in all_content.lower()
        assert "SQL-001" in all_content or "Entra authentication" in all_content

    def test_biz_analyst_validate_response_catches_anti_patterns(self):
        """Biz-analyst should detect anti-patterns in its own AI output."""
        from azext_prototype.agents.builtin.biz_analyst import BizAnalystAgent

        agent = BizAnalystAgent()
        # Recommending SQL auth with password is an anti-pattern
        warnings = agent.validate_response(
            "We recommend using SQL authentication with username/password "
            "for the database connection."
        )
        assert len(warnings) > 0


# ------------------------------------------------------------------ #
# Multi-step agents — validate_response is called
# ------------------------------------------------------------------ #

class TestMultiStepAgentGovernance:
    """Test that agents with custom execute() also validate responses."""

    @pytest.fixture(autouse=True)
    def _setup_governance(self, policy_engine, template_registry):
        import azext_prototype.agents.governance as gov_mod
        gov_mod._policy_engine = policy_engine
        gov_mod._template_registry = template_registry

    @patch("azext_prototype.agents.builtin.cost_analyst.requests.get")
    def test_cost_analyst_validates_response(self, mock_get, mock_agent_context):
        from azext_prototype.agents.builtin.cost_analyst import CostAnalystAgent

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"Items": [{"retailPrice": 0.10, "unitOfMeasure": "1 Hour", "meterName": "Standard", "currencyCode": "USD"}]}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        agent = CostAnalystAgent()

        # Step 1 returns valid JSON components, Step 2 returns a problematic report
        mock_agent_context.ai_provider.chat.side_effect = [
            AIResponse(
                content='[{"serviceName": "App Service", "armResourceType": "Microsoft.Web/sites", '
                '"skuSmall": "B1", "skuMedium": "S1", "skuLarge": "P1v2", '
                '"meterName": "Standard", "region": "eastus"}]',
                model="test",
            ),
            AIResponse(
                content='Set connection_string = "Server=db;Password=insecure"',
                model="test",
            ),
        ]

        result = agent.execute(mock_agent_context, "Estimate costs")
        assert "Governance warnings" in result.content

    @patch("azext_prototype.agents.builtin.cost_analyst.requests.get")
    def test_cost_analyst_clean_response(self, mock_get, mock_agent_context):
        from azext_prototype.agents.builtin.cost_analyst import CostAnalystAgent

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"Items": [{"retailPrice": 0.10, "unitOfMeasure": "1 Hour", "meterName": "Standard", "currencyCode": "USD"}]}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        agent = CostAnalystAgent()

        mock_agent_context.ai_provider.chat.side_effect = [
            AIResponse(
                content='[{"serviceName": "App Service", "armResourceType": "Microsoft.Web/sites", '
                '"skuSmall": "B1", "skuMedium": "S1", "skuLarge": "P1v2", '
                '"meterName": "Standard", "region": "eastus"}]',
                model="test",
            ),
            AIResponse(
                content="| Service | Small | Medium | Large |\n| App Service | $55 | $73 | $146 |",
                model="test",
            ),
        ]

        result = agent.execute(mock_agent_context, "Estimate costs")
        assert "Governance warnings" not in result.content

    def test_project_manager_validates_response(self, mock_agent_context):
        from azext_prototype.agents.builtin.project_manager import ProjectManagerAgent

        agent = ProjectManagerAgent()

        mock_agent_context.ai_provider.chat.side_effect = [
            AIResponse(
                content='[{"epic": "Infra", "title": "Setup", "description": "Create infra", '
                '"acceptance_criteria": ["Done"], "tasks": ["Do it"], "effort": "M"}]',
                model="test",
            ),
            AIResponse(
                content='Store the password = "admin123" in environment variables',
                model="test",
            ),
        ]

        result = agent.execute(mock_agent_context, "Generate backlog")
        assert "Governance warnings" in result.content

    def test_cloud_architect_validates_response(self, mock_agent_context):
        from azext_prototype.agents.builtin.cloud_architect import CloudArchitectAgent

        agent = CloudArchitectAgent()

        mock_agent_context.ai_provider.chat.return_value = AIResponse(
            content='Use account_key for storage access',
            model="test",
        )

        result = agent.execute(mock_agent_context, "Design architecture")
        assert "Governance warnings" in result.content


# ------------------------------------------------------------------ #
# Credential detection patterns — exhaustive
# ------------------------------------------------------------------ #

class TestCredentialDetection:
    """Test all credential patterns are detected."""

    @pytest.fixture(autouse=True)
    def _setup_governance(self, policy_engine, template_registry):
        import azext_prototype.agents.governance as gov_mod
        gov_mod._policy_engine = policy_engine
        gov_mod._template_registry = template_registry

    @pytest.mark.parametrize(
        "pattern",
        [
            "connection_string",
            "connectionstring",
            "access_key",
            "accesskey",
            "account_key",
            "accountkey",
            "shared_access_key",
            "client_secret",
            'password="bad"',
            "password='bad'",
            "password = foo",
        ],
    )
    def test_credential_pattern_detected(self, pattern, governance_ctx):
        warnings = governance_ctx.check_response_for_violations(
            "cloud-architect", f"Use {pattern} for auth"
        )
        assert any(
            "credential" in w.lower() or "secret" in w.lower() or "managed identity" in w.lower()
            for w in warnings
        ), f"Pattern '{pattern}' should be detected as credential"


# ------------------------------------------------------------------ #
# GovernanceContext — edge cases
# ------------------------------------------------------------------ #

class TestGovernanceEdgeCases:
    """Edge case tests for robustness."""

    def test_format_all_empty_agent_name(self, governance_ctx):
        text = governance_ctx.format_all("")
        assert isinstance(text, str)

    def test_check_violations_empty_response(self, governance_ctx):
        warnings = governance_ctx.check_response_for_violations("cloud-architect", "")
        assert warnings == []

    def test_check_violations_very_long_response(self, governance_ctx):
        # Should not crash on large input
        long_text = "safe content " * 10000
        warnings = governance_ctx.check_response_for_violations("cloud-architect", long_text)
        assert isinstance(warnings, list)

    def test_custom_policy_engine_injection(self):
        """GovernanceContext accepts injected engine/registry."""
        engine = MagicMock(spec=PolicyEngine)
        engine.format_for_prompt.return_value = "Custom policies"
        engine.resolve.return_value = []

        registry = MagicMock(spec=TemplateRegistry)
        registry.format_for_prompt.return_value = "Custom templates"

        ctx = GovernanceContext(policy_engine=engine, template_registry=registry)
        text = ctx.format_all("any-agent")
        assert "Custom policies" in text
        assert "Custom templates" in text

    def test_custom_injection_skips_templates(self):
        engine = MagicMock(spec=PolicyEngine)
        engine.format_for_prompt.return_value = "Rules"
        engine.resolve.return_value = []

        registry = MagicMock(spec=TemplateRegistry)
        registry.format_for_prompt.return_value = "Templates"

        ctx = GovernanceContext(policy_engine=engine, template_registry=registry)
        text = ctx.format_all("any-agent", include_templates=False)
        assert "Rules" in text
        assert "Templates" not in text


# ------------------------------------------------------------------ #
# Standards integration — system messages include design standards
# ------------------------------------------------------------------ #

class TestBuiltinAgentStandardsFlags:
    """Verify that built-in agents have correct _include_standards flags."""

    @pytest.fixture(autouse=True)
    def _setup_governance(self, policy_engine, template_registry):
        import azext_prototype.agents.governance as gov_mod
        gov_mod._policy_engine = policy_engine
        gov_mod._template_registry = template_registry

    @pytest.mark.parametrize(
        "agent_cls_path,expects_standards",
        [
            ("azext_prototype.agents.builtin.cloud_architect.CloudArchitectAgent", True),
            ("azext_prototype.agents.builtin.terraform_agent.TerraformAgent", True),
            ("azext_prototype.agents.builtin.bicep_agent.BicepAgent", True),
            ("azext_prototype.agents.builtin.app_developer.AppDeveloperAgent", True),
            ("azext_prototype.agents.builtin.security_reviewer.SecurityReviewerAgent", True),
            ("azext_prototype.agents.builtin.monitoring_agent.MonitoringAgent", True),
            ("azext_prototype.agents.builtin.cost_analyst.CostAnalystAgent", False),
            ("azext_prototype.agents.builtin.qa_engineer.QAEngineerAgent", False),
            ("azext_prototype.agents.builtin.doc_agent.DocumentationAgent", False),
            ("azext_prototype.agents.builtin.project_manager.ProjectManagerAgent", False),
            ("azext_prototype.agents.builtin.biz_analyst.BizAnalystAgent", False),
        ],
    )
    def test_include_standards_flag(self, agent_cls_path, expects_standards):
        import importlib

        module_path, cls_name = agent_cls_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, cls_name)
        agent = cls()
        assert agent._include_standards is expects_standards, (
            f"{cls_name}._include_standards should be {expects_standards}"
        )

    @pytest.mark.parametrize(
        "agent_cls_path",
        [
            "azext_prototype.agents.builtin.cloud_architect.CloudArchitectAgent",
            "azext_prototype.agents.builtin.terraform_agent.TerraformAgent",
            "azext_prototype.agents.builtin.bicep_agent.BicepAgent",
            "azext_prototype.agents.builtin.app_developer.AppDeveloperAgent",
        ],
    )
    def test_system_messages_include_standards(self, agent_cls_path):
        """Code-generating agents should have Design Standards in system messages."""
        import importlib

        module_path, cls_name = agent_cls_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, cls_name)
        agent = cls()

        messages = agent.get_system_messages()
        all_content = "\n".join(m.content for m in messages)
        assert "Design Standards" in all_content, (
            f"{cls_name} system messages should include Design Standards"
        )

    @pytest.mark.parametrize(
        "agent_cls_path",
        [
            "azext_prototype.agents.builtin.cost_analyst.CostAnalystAgent",
            "azext_prototype.agents.builtin.qa_engineer.QAEngineerAgent",
            "azext_prototype.agents.builtin.doc_agent.DocumentationAgent",
            "azext_prototype.agents.builtin.project_manager.ProjectManagerAgent",
            "azext_prototype.agents.builtin.biz_analyst.BizAnalystAgent",
        ],
    )
    def test_system_messages_exclude_standards(self, agent_cls_path):
        """Non-generating agents should NOT have Design Standards in system messages."""
        import importlib

        module_path, cls_name = agent_cls_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, cls_name)
        agent = cls()

        messages = agent.get_system_messages()
        all_content = "\n".join(m.content for m in messages)
        assert "Design Standards" not in all_content, (
            f"{cls_name} system messages should NOT include Design Standards"
        )

    def test_terraform_agent_sees_tf_standards(self):
        """Terraform agent should see TF-001 module structure standard."""
        from azext_prototype.agents.builtin.terraform_agent import TerraformAgent

        agent = TerraformAgent()
        messages = agent.get_system_messages()
        all_content = "\n".join(m.content for m in messages)
        assert "TF-001" in all_content or "Standard File Layout" in all_content

    def test_bicep_agent_sees_bcp_standards(self):
        """Bicep agent should see BCP-001 module structure standard."""
        from azext_prototype.agents.builtin.bicep_agent import BicepAgent

        agent = BicepAgent()
        messages = agent.get_system_messages()
        all_content = "\n".join(m.content for m in messages)
        assert "BCP-001" in all_content or "Standard File Layout" in all_content

    def test_app_developer_sees_python_standards(self):
        """App developer should see PY-001 DefaultAzureCredential standard."""
        from azext_prototype.agents.builtin.app_developer import AppDeveloperAgent

        agent = AppDeveloperAgent()
        messages = agent.get_system_messages()
        all_content = "\n".join(m.content for m in messages)
        assert "PY-001" in all_content or "DefaultAzureCredential" in all_content
