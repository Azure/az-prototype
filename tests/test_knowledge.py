"""Tests for azext_prototype.knowledge — KnowledgeLoader and agent integration."""

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from azext_prototype.knowledge import KnowledgeLoader, DEFAULT_TOKEN_BUDGET, _CHARS_PER_TOKEN


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def knowledge_dir(tmp_path):
    """Create a minimal knowledge directory for testing."""
    kd = tmp_path / "knowledge"
    kd.mkdir()

    # Subdirectories
    (kd / "services").mkdir()
    (kd / "tools").mkdir()
    (kd / "languages").mkdir()
    (kd / "roles").mkdir()

    # constraints.md
    (kd / "constraints.md").write_text(
        "# Shared Constraints\n\n- Always use managed identity\n- Tag all resources\n",
        encoding="utf-8",
    )

    # service-registry.yaml
    registry = {
        "cosmos-db": {
            "display_name": "Azure Cosmos DB",
            "resource_provider": "Microsoft.DocumentDB/databaseAccounts",
            "rbac_roles": [{"name": "Cosmos DB Data Contributor"}],
        },
        "key-vault": {
            "display_name": "Azure Key Vault",
            "resource_provider": "Microsoft.KeyVault/vaults",
        },
    }
    (kd / "service-registry.yaml").write_text(
        yaml.dump(registry, default_flow_style=False), encoding="utf-8",
    )

    # Service files
    (kd / "services" / "cosmos-db.md").write_text(
        "# Cosmos DB\n\nUse Cosmos DB for NoSQL.\n", encoding="utf-8",
    )
    (kd / "services" / "key-vault.md").write_text(
        "# Key Vault\n\nUse Key Vault for secrets.\n", encoding="utf-8",
    )

    # Tool files
    (kd / "tools" / "terraform.md").write_text(
        "# Terraform Patterns\n\nUse azurerm provider.\n", encoding="utf-8",
    )
    (kd / "tools" / "bicep.md").write_text(
        "# Bicep Patterns\n\nUse modules.\n", encoding="utf-8",
    )

    # Language files
    (kd / "languages" / "python.md").write_text(
        "# Python Patterns\n\nUse FastAPI.\n", encoding="utf-8",
    )
    (kd / "languages" / "auth-patterns.md").write_text(
        "# Auth Patterns\n\nUse DefaultAzureCredential.\n", encoding="utf-8",
    )

    # Role files
    (kd / "roles" / "architect.md").write_text(
        "# Architect Role\n\nDesign Azure architectures.\n", encoding="utf-8",
    )
    (kd / "roles" / "infrastructure.md").write_text(
        "# Infrastructure Role\n\nGenerate IaC code.\n", encoding="utf-8",
    )
    (kd / "roles" / "developer.md").write_text(
        "# Developer Role\n\nWrite application code.\n", encoding="utf-8",
    )
    (kd / "roles" / "analyst.md").write_text(
        "# Analyst Role\n\nGather requirements.\n", encoding="utf-8",
    )

    return kd


@pytest.fixture
def loader(knowledge_dir):
    """Create a KnowledgeLoader pointing to the test knowledge directory."""
    return KnowledgeLoader(knowledge_dir=knowledge_dir)


# ------------------------------------------------------------------
# KnowledgeLoader — individual loaders
# ------------------------------------------------------------------

class TestKnowledgeLoaderIndividual:
    """Test individual load methods."""

    def test_load_service(self, loader):
        text = loader.load_service("cosmos-db")
        assert "Cosmos DB" in text
        assert "NoSQL" in text

    def test_load_service_missing(self, loader):
        assert loader.load_service("nonexistent") == ""

    def test_load_tool(self, loader):
        text = loader.load_tool("terraform")
        assert "azurerm" in text

    def test_load_tool_missing(self, loader):
        assert loader.load_tool("pulumi") == ""

    def test_load_language(self, loader):
        text = loader.load_language("python")
        assert "FastAPI" in text

    def test_load_language_missing(self, loader):
        assert loader.load_language("java") == ""

    def test_load_role(self, loader):
        text = loader.load_role("architect")
        assert "Architect" in text

    def test_load_role_missing(self, loader):
        assert loader.load_role("devops") == ""

    def test_load_constraints(self, loader):
        text = loader.load_constraints()
        assert "managed identity" in text

    def test_load_service_registry_full(self, loader):
        registry = loader.load_service_registry()
        assert "cosmos-db" in registry
        assert "key-vault" in registry

    def test_load_service_registry_single(self, loader):
        entry = loader.load_service_registry("cosmos-db")
        assert entry["display_name"] == "Azure Cosmos DB"

    def test_load_service_registry_missing(self, loader):
        assert loader.load_service_registry("nonexistent") == {}


# ------------------------------------------------------------------
# KnowledgeLoader — list methods
# ------------------------------------------------------------------

class TestKnowledgeLoaderList:
    """Test list methods for introspection."""

    def test_list_services(self, loader):
        services = loader.list_services()
        assert "cosmos-db" in services
        assert "key-vault" in services

    def test_list_tools(self, loader):
        tools = loader.list_tools()
        assert "terraform" in tools
        assert "bicep" in tools

    def test_list_languages(self, loader):
        languages = loader.list_languages()
        assert "python" in languages
        assert "auth-patterns" in languages

    def test_list_roles(self, loader):
        roles = loader.list_roles()
        assert "architect" in roles
        assert "infrastructure" in roles
        assert "developer" in roles
        assert "analyst" in roles

    def test_list_missing_subdir(self, tmp_path):
        loader = KnowledgeLoader(knowledge_dir=tmp_path)
        assert loader.list_services() == []


# ------------------------------------------------------------------
# KnowledgeLoader — compose_context
# ------------------------------------------------------------------

class TestKnowledgeLoaderCompose:
    """Test context composition."""

    def test_compose_with_role(self, loader):
        ctx = loader.compose_context(role="architect")
        assert "ROLE: architect" in ctx
        assert "SHARED CONSTRAINTS" in ctx

    def test_compose_with_tool(self, loader):
        ctx = loader.compose_context(tool="terraform")
        assert "TOOL PATTERNS: terraform" in ctx

    def test_compose_with_language(self, loader):
        ctx = loader.compose_context(language="python")
        assert "LANGUAGE PATTERNS: python" in ctx
        # Auth patterns should be auto-included
        assert "AUTH PATTERNS (cross-language)" in ctx

    def test_compose_auth_patterns_not_doubled(self, loader):
        """When language IS auth-patterns, don't include it twice."""
        ctx = loader.compose_context(language="auth-patterns")
        assert "LANGUAGE PATTERNS: auth-patterns" in ctx
        assert "AUTH PATTERNS (cross-language)" not in ctx

    def test_compose_with_services(self, loader):
        ctx = loader.compose_context(services=["cosmos-db", "key-vault"])
        assert "SERVICE: cosmos-db" in ctx
        assert "SERVICE: key-vault" in ctx

    def test_compose_with_service_registry(self, loader):
        ctx = loader.compose_context(
            services=["cosmos-db"],
            include_service_registry=True,
        )
        assert "SERVICE REGISTRY DATA" in ctx
        assert "Azure Cosmos DB" in ctx

    def test_compose_no_constraints(self, loader):
        ctx = loader.compose_context(role="architect", include_constraints=False)
        assert "SHARED CONSTRAINTS" not in ctx
        assert "ROLE: architect" in ctx

    def test_compose_empty_returns_empty(self, loader):
        ctx = loader.compose_context(include_constraints=False)
        assert ctx == ""

    def test_compose_priority_order(self, loader):
        """Role should appear before constraints before tool before services."""
        ctx = loader.compose_context(
            role="architect",
            tool="terraform",
            services=["cosmos-db"],
        )
        role_pos = ctx.index("ROLE: architect")
        constraints_pos = ctx.index("SHARED CONSTRAINTS")
        tool_pos = ctx.index("TOOL PATTERNS: terraform")
        service_pos = ctx.index("SERVICE: cosmos-db")

        assert role_pos < constraints_pos < tool_pos < service_pos

    def test_compose_missing_files_skipped(self, loader):
        """Missing files should be silently skipped."""
        ctx = loader.compose_context(
            role="nonexistent",
            tool="nonexistent",
            services=["nonexistent"],
        )
        # Only constraints should be present (they exist)
        assert "SHARED CONSTRAINTS" in ctx
        assert "ROLE" not in ctx

    def test_compose_full_stack(self, loader):
        """All dimensions composed together."""
        ctx = loader.compose_context(
            role="infrastructure",
            tool="terraform",
            language="python",
            services=["cosmos-db", "key-vault"],
            include_service_registry=True,
        )
        assert "ROLE: infrastructure" in ctx
        assert "SHARED CONSTRAINTS" in ctx
        assert "TOOL PATTERNS: terraform" in ctx
        assert "LANGUAGE PATTERNS: python" in ctx
        assert "AUTH PATTERNS (cross-language)" in ctx
        assert "SERVICE: cosmos-db" in ctx
        assert "SERVICE: key-vault" in ctx
        assert "SERVICE REGISTRY DATA" in ctx


# ------------------------------------------------------------------
# KnowledgeLoader — token budget
# ------------------------------------------------------------------

class TestKnowledgeLoaderBudget:
    """Test token budget enforcement."""

    def test_default_budget(self):
        assert DEFAULT_TOKEN_BUDGET == 10_000
        assert _CHARS_PER_TOKEN == 4

    def test_estimate_tokens(self):
        assert KnowledgeLoader.estimate_tokens("a" * 400) == 100

    def test_budget_truncation(self, knowledge_dir):
        """With a tiny budget, lower-priority content should be truncated."""
        # Create a loader with a very small budget (20 tokens = 80 chars)
        loader = KnowledgeLoader(knowledge_dir=knowledge_dir, token_budget=20)
        ctx = loader.compose_context(
            role="architect",
            tool="terraform",
            services=["cosmos-db"],
        )
        # Should have some content but be truncated
        assert len(ctx) > 0
        # With only 80 chars budget, services should not fit fully
        assert len(ctx) <= 200  # Allow some overhead for truncation message


# ------------------------------------------------------------------
# KnowledgeLoader — real knowledge directory
# ------------------------------------------------------------------

class TestKnowledgeLoaderReal:
    """Test against the actual knowledge/ directory shipped with the package."""

    def test_real_services_exist(self):
        loader = KnowledgeLoader()
        services = loader.list_services()
        # Should have at least 10 services
        assert len(services) >= 10
        assert "cosmos-db" in services
        assert "key-vault" in services

    def test_real_tools_exist(self):
        loader = KnowledgeLoader()
        tools = loader.list_tools()
        assert "terraform" in tools
        assert "bicep" in tools
        assert "deploy-scripts" in tools

    def test_real_languages_exist(self):
        loader = KnowledgeLoader()
        languages = loader.list_languages()
        assert "python" in languages
        assert "csharp" in languages
        assert "nodejs" in languages
        assert "auth-patterns" in languages

    def test_real_roles_exist(self):
        loader = KnowledgeLoader()
        roles = loader.list_roles()
        assert "architect" in roles
        assert "infrastructure" in roles
        assert "developer" in roles
        assert "analyst" in roles

    def test_real_constraints_not_empty(self):
        loader = KnowledgeLoader()
        assert len(loader.load_constraints()) > 100

    def test_real_service_registry_not_empty(self):
        loader = KnowledgeLoader()
        registry = loader.load_service_registry()
        assert len(registry) >= 10

    def test_real_compose_fits_budget(self):
        """Full composition should fit within the default token budget."""
        loader = KnowledgeLoader()
        ctx = loader.compose_context(
            role="infrastructure",
            tool="terraform",
            language="python",
            services=["cosmos-db", "key-vault", "app-service"],
        )
        tokens = loader.estimate_tokens(ctx)
        assert tokens <= DEFAULT_TOKEN_BUDGET


# ------------------------------------------------------------------
# BaseAgent — knowledge injection
# ------------------------------------------------------------------

class TestBaseAgentKnowledge:
    """Test that BaseAgent.get_system_messages() injects knowledge."""

    def test_no_knowledge_by_default(self):
        from azext_prototype.agents.base import BaseAgent

        agent = BaseAgent(
            name="test",
            description="test agent",
        )
        agent._governance_aware = False
        messages = agent.get_system_messages()
        # No knowledge attributes set, no knowledge message
        for m in messages:
            assert "ROLE:" not in m.content
            assert "TOOL PATTERNS:" not in m.content

    def test_knowledge_injected_when_role_set(self, knowledge_dir):
        from azext_prototype.agents.base import BaseAgent

        agent = BaseAgent(
            name="test",
            description="test agent",
            system_prompt="You are a test agent.",
        )
        agent._governance_aware = False
        agent._knowledge_role = "architect"

        with patch(
            "azext_prototype.knowledge._KNOWLEDGE_DIR", knowledge_dir,
        ):
            messages = agent.get_system_messages()

        # Should have system_prompt + knowledge
        assert len(messages) >= 2
        knowledge_msg = messages[-1]
        assert "ROLE: architect" in knowledge_msg.content

    def test_knowledge_injected_when_tools_set(self, knowledge_dir):
        from azext_prototype.agents.base import BaseAgent

        agent = BaseAgent(name="test", description="test")
        agent._governance_aware = False
        agent._knowledge_tools = ["terraform"]

        with patch(
            "azext_prototype.knowledge._KNOWLEDGE_DIR", knowledge_dir,
        ):
            messages = agent.get_system_messages()

        knowledge_msg = messages[-1]
        assert "TOOL PATTERNS: terraform" in knowledge_msg.content

    def test_knowledge_error_does_not_break_agent(self):
        from azext_prototype.agents.base import BaseAgent

        agent = BaseAgent(name="test", description="test")
        agent._governance_aware = False
        agent._knowledge_role = "architect"

        with patch(
            "azext_prototype.knowledge.KnowledgeLoader",
            side_effect=Exception("boom"),
        ):
            # Should not raise — knowledge errors are caught
            messages = agent.get_system_messages()
            # Should still return basic messages without knowledge
            assert isinstance(messages, list)


# ------------------------------------------------------------------
# Builtin agents — knowledge declarations
# ------------------------------------------------------------------

class TestBuiltinAgentKnowledge:
    """Test that builtin agents have correct knowledge declarations."""

    def test_cloud_architect_knowledge(self):
        from azext_prototype.agents.builtin.cloud_architect import CloudArchitectAgent

        agent = CloudArchitectAgent()
        assert agent._knowledge_role == "architect"
        assert agent._knowledge_tools is None
        assert agent._knowledge_languages is None

    def test_terraform_agent_knowledge(self):
        from azext_prototype.agents.builtin.terraform_agent import TerraformAgent

        agent = TerraformAgent()
        assert agent._knowledge_role == "infrastructure"
        assert agent._knowledge_tools == ["terraform"]
        assert agent._knowledge_languages is None

    def test_bicep_agent_knowledge(self):
        from azext_prototype.agents.builtin.bicep_agent import BicepAgent

        agent = BicepAgent()
        assert agent._knowledge_role == "infrastructure"
        assert agent._knowledge_tools == ["bicep"]
        assert agent._knowledge_languages is None

    def test_app_developer_knowledge(self):
        from azext_prototype.agents.builtin.app_developer import AppDeveloperAgent

        agent = AppDeveloperAgent()
        assert agent._knowledge_role == "developer"
        assert agent._knowledge_tools is None
        assert agent._knowledge_languages is None

    def test_biz_analyst_knowledge(self):
        from azext_prototype.agents.builtin.biz_analyst import BizAnalystAgent

        agent = BizAnalystAgent()
        assert agent._knowledge_role == "analyst"
        assert agent._knowledge_tools is None
        assert agent._knowledge_languages is None

    def test_qa_engineer_no_knowledge(self):
        from azext_prototype.agents.builtin.qa_engineer import QAEngineerAgent

        agent = QAEngineerAgent()
        assert agent._knowledge_role is None
        assert agent._knowledge_tools is None
        assert agent._knowledge_languages is None

    def test_cost_analyst_no_knowledge(self):
        from azext_prototype.agents.builtin.cost_analyst import CostAnalystAgent

        agent = CostAnalystAgent()
        assert agent._knowledge_role is None
        assert agent._knowledge_tools is None
        assert agent._knowledge_languages is None

    def test_project_manager_no_knowledge(self):
        from azext_prototype.agents.builtin.project_manager import ProjectManagerAgent

        agent = ProjectManagerAgent()
        assert agent._knowledge_role is None
        assert agent._knowledge_tools is None
        assert agent._knowledge_languages is None

    def test_doc_agent_no_knowledge(self):
        from azext_prototype.agents.builtin.doc_agent import DocumentationAgent

        agent = DocumentationAgent()
        assert agent._knowledge_role is None
        assert agent._knowledge_tools is None
        assert agent._knowledge_languages is None
