"""Tests for AgentRegistry.find_agent_for_task() — priority-based routing.

Validates the CLAUDE.md governance priority chain:
1. Error → QA
2. Service + IaC → terraform/bicep
3. Scope → project-manager
4. Multiple services → cloud-architect
5. Discovery → biz-analyst
6. Docs → doc-agent
7. Cost → cost-analyst
8. Fallback → keyword scoring
9. Ultimate fallback → project-manager
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from azext_prototype.agents.base import AgentCapability, BaseAgent
from azext_prototype.agents.registry import AgentRegistry


# ======================================================================
# Helpers
# ======================================================================

def _make_agent(name: str, capabilities: list[AgentCapability], keywords: list[str] | None = None) -> BaseAgent:
    """Create a minimal BaseAgent for testing."""
    agent = MagicMock(spec=BaseAgent)
    agent.name = name
    agent.capabilities = capabilities
    agent._is_builtin = True
    agent._keywords = keywords or []
    agent._keyword_weight = 0.1

    def can_handle(task: str) -> float:
        words = task.lower().split()
        matches = sum(1 for kw in (keywords or []) if kw in words)
        return min(matches * 0.1, 1.0)

    agent.can_handle.side_effect = can_handle
    return agent


def _populated_registry() -> AgentRegistry:
    """Build a registry with all 11 built-in agent types."""
    r = AgentRegistry()

    agents = [
        _make_agent("cloud-architect", [AgentCapability.ARCHITECT, AgentCapability.COORDINATE],
                     ["architecture", "design", "multi-service"]),
        _make_agent("biz-analyst", [AgentCapability.BIZ_ANALYSIS, AgentCapability.ANALYZE],
                     ["requirements", "stakeholder", "discover"]),
        _make_agent("project-manager", [AgentCapability.BACKLOG_GENERATION, AgentCapability.COORDINATE],
                     ["scope", "backlog", "sprint", "coordinate"]),
        _make_agent("terraform-agent", [AgentCapability.TERRAFORM],
                     ["terraform", "module", "hcl"]),
        _make_agent("bicep-agent", [AgentCapability.BICEP],
                     ["bicep", "arm", "template"]),
        _make_agent("app-developer", [AgentCapability.DEVELOP],
                     ["application", "api", "code", "develop"]),
        _make_agent("qa-engineer", [AgentCapability.QA],
                     ["error", "bug", "diagnose", "troubleshoot"]),
        _make_agent("cost-analyst", [AgentCapability.COST_ANALYSIS],
                     ["cost", "pricing", "budget", "estimate"]),
        _make_agent("doc-agent", [AgentCapability.DOCUMENT],
                     ["document", "readme", "guide", "docs"]),
        _make_agent("security-reviewer", [AgentCapability.SECURITY_REVIEW],
                     ["security", "vulnerability", "scan"]),
        _make_agent("monitoring-agent", [AgentCapability.MONITORING],
                     ["monitoring", "observability", "alerts"]),
    ]

    for a in agents:
        r.register_builtin(a)

    return r


# ======================================================================
# Priority level routing tests
# ======================================================================

class TestPriorityLevelRouting:
    """Each priority level routes to the correct agent."""

    def test_error_routes_to_qa(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("Fix the deployment error")
        assert agent.name == "qa-engineer"

    def test_error_signal_fail(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("The build process will fail")
        assert agent.name == "qa-engineer"

    def test_error_signal_exception(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("Handle the exception in stage 3")
        assert agent.name == "qa-engineer"

    def test_error_signal_troubleshoot(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("Troubleshoot the Redis connection")
        assert agent.name == "qa-engineer"

    def test_single_service_terraform(self):
        r = _populated_registry()
        agent = r.find_agent_for_task(
            "Generate the key vault module",
            services=["key-vault"],
            iac_tool="terraform",
        )
        assert agent.name == "terraform-agent"

    def test_single_service_bicep(self):
        r = _populated_registry()
        agent = r.find_agent_for_task(
            "Generate the key vault template",
            services=["key-vault"],
            iac_tool="bicep",
        )
        assert agent.name == "bicep-agent"

    def test_two_services_with_iac(self):
        r = _populated_registry()
        agent = r.find_agent_for_task(
            "Generate modules",
            services=["key-vault", "storage"],
            iac_tool="terraform",
        )
        assert agent.name == "terraform-agent"

    def test_scope_routes_to_pm(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("Update the scope for sprint 2")
        assert agent.name == "project-manager"

    def test_backlog_routes_to_pm(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("Create backlog items for the API")
        assert agent.name == "project-manager"

    def test_multiple_services_routes_to_architect(self):
        r = _populated_registry()
        agent = r.find_agent_for_task(
            "Configure networking",
            services=["vnet", "subnet", "nsg"],
        )
        assert agent.name == "cloud-architect"

    def test_discovery_routes_to_biz_analyst(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("Discover the user requirements")
        assert agent.name == "biz-analyst"

    def test_requirements_routes_to_biz_analyst(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("Gather requirements from stakeholder")
        assert agent.name == "biz-analyst"

    def test_docs_routes_to_doc_agent(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("Generate the readme documentation")
        assert agent.name == "doc-agent"

    def test_cost_routes_to_cost_analyst(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("Estimate the cost of this deployment")
        assert agent.name == "cost-analyst"

    def test_pricing_routes_to_cost_analyst(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("Check pricing for App Service")
        assert agent.name == "cost-analyst"


# ======================================================================
# Priority ordering tests
# ======================================================================

class TestPriorityOrdering:
    """Error signals should take precedence over other signals."""

    def test_error_beats_docs(self):
        r = _populated_registry()
        # Has both error and docs signals
        agent = r.find_agent_for_task("Fix the error in the documentation guide")
        assert agent.name == "qa-engineer"

    def test_error_beats_cost(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("Diagnose the cost estimation error")
        assert agent.name == "qa-engineer"

    def test_error_beats_scope(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("The scope validation has a bug")
        assert agent.name == "qa-engineer"

    def test_error_beats_iac(self):
        r = _populated_registry()
        agent = r.find_agent_for_task(
            "Diagnose the terraform error",
            services=["key-vault"],
            iac_tool="terraform",
        )
        assert agent.name == "qa-engineer"

    def test_iac_beats_scope(self):
        r = _populated_registry()
        agent = r.find_agent_for_task(
            "Generate the key vault module for the sprint",
            services=["key-vault"],
            iac_tool="terraform",
        )
        assert agent.name == "terraform-agent"

    def test_scope_beats_docs(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("Document the scope changes for the sprint")
        # scope signal present, routes to PM
        assert agent.name == "project-manager"


# ======================================================================
# Explicit task_type override tests
# ======================================================================

class TestExplicitTaskType:
    """Explicit task_type parameter overrides keyword detection."""

    def test_task_type_error(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("Update the docs", task_type="error")
        assert agent.name == "qa-engineer"

    def test_task_type_scope(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("Fix the error", task_type="scope")
        # task_type=scope but words have "error" → error wins (priority 1 vs 3)
        assert agent.name == "qa-engineer"

    def test_task_type_docs(self):
        r = _populated_registry()
        # No error/scope words, task_type=docs
        agent = r.find_agent_for_task("Generate output files", task_type="docs")
        assert agent.name == "doc-agent"

    def test_task_type_cost(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("Analyze this deployment", task_type="cost")
        assert agent.name == "cost-analyst"

    def test_task_type_discovery(self):
        r = _populated_registry()
        agent = r.find_agent_for_task("Analyze this app", task_type="discovery")
        assert agent.name == "biz-analyst"


# ======================================================================
# Services parameter tests
# ======================================================================

class TestServicesParameter:
    """Service list drives single-service vs multi-service routing."""

    def test_no_services_skips_iac(self):
        r = _populated_registry()
        agent = r.find_agent_for_task(
            "Generate infrastructure",
            iac_tool="terraform",
        )
        # No services, skips step 2 → falls through
        assert agent.name != "terraform-agent"

    def test_three_services_routes_to_architect(self):
        r = _populated_registry()
        agent = r.find_agent_for_task(
            "Configure the deployment",
            services=["app", "db", "cache"],
        )
        assert agent.name == "cloud-architect"

    def test_empty_services_list(self):
        r = _populated_registry()
        agent = r.find_agent_for_task(
            "Generate infrastructure",
            services=[],
            iac_tool="terraform",
        )
        # Empty services, skips step 2
        assert agent.name != "terraform-agent"

    def test_one_service_no_iac_tool(self):
        r = _populated_registry()
        agent = r.find_agent_for_task(
            "Deploy the key vault",
            services=["key-vault"],
        )
        # No iac_tool, skips step 2
        assert agent.name != "terraform-agent"


# ======================================================================
# Fallback tests
# ======================================================================

class TestFallback:
    """Test fallback paths when no priority signal matches."""

    def test_keyword_scoring_fallback(self):
        r = _populated_registry()
        # No priority signals, but "application" and "api" keywords should match app-developer
        agent = r.find_agent_for_task("Build the application API endpoint")
        assert agent is not None
        assert agent.name == "app-developer"

    def test_ultimate_fallback_to_pm(self):
        r = _populated_registry()
        # Make all agents score 0
        for a in r.list_all():
            a.can_handle.side_effect = lambda t: 0.0

        agent = r.find_agent_for_task("Do something completely generic")
        assert agent.name == "project-manager"

    def test_empty_registry_returns_none(self):
        r = AgentRegistry()
        agent = r.find_agent_for_task("Do something")
        assert agent is None


# ======================================================================
# Custom/override agent tests
# ======================================================================

class TestCustomOverrideAgents:
    """Custom and override agents are still respected."""

    def test_custom_qa_replaces_builtin(self):
        r = _populated_registry()
        custom_qa = _make_agent("qa-engineer", [AgentCapability.QA])
        r.register_custom(custom_qa)

        agent = r.find_agent_for_task("Fix the error")
        assert agent is custom_qa

    def test_override_architect_replaces_builtin(self):
        r = _populated_registry()
        override = _make_agent("cloud-architect", [AgentCapability.ARCHITECT])
        r.register_override(override)

        agent = r.find_agent_for_task(
            "Configure networking",
            services=["a", "b", "c"],
        )
        assert agent is override

    def test_custom_agent_with_new_capability(self):
        r = _populated_registry()
        custom = _make_agent("custom-agent", [AgentCapability.QA])
        r.register_custom(custom)

        agent = r.find_agent_for_task("Diagnose the crash")
        # Custom agent has QA capability and was registered, may be first
        assert agent.name in ("qa-engineer", "custom-agent")


# ======================================================================
# find_best_for_task regression tests
# ======================================================================

class TestFindBestForTaskRegression:
    """Ensure find_best_for_task() is unchanged."""

    def test_keyword_scoring_unchanged(self):
        r = _populated_registry()
        agent = r.find_best_for_task("terraform module generation")
        assert agent is not None
        assert agent.name == "terraform-agent"

    def test_no_match_returns_none(self):
        r = _populated_registry()
        for a in r.list_all():
            a.can_handle.side_effect = lambda t: 0.0

        agent = r.find_best_for_task("something totally generic")
        assert agent is None

    def test_highest_scorer_wins(self):
        r = _populated_registry()
        # Security keywords should match security-reviewer
        agent = r.find_best_for_task("run security scan for vulnerability")
        assert agent is not None
        assert agent.name == "security-reviewer"


# ======================================================================
# Orchestrator auto-assign integration tests
# ======================================================================

class TestOrchestratorAutoAssign:
    """Test that orchestrator auto-assignment uses find_agent_for_task."""

    def test_auto_assign_uses_priority_chain(self):
        from azext_prototype.agents.orchestrator import AgentOrchestrator, AgentTask

        r = _populated_registry()
        ctx = MagicMock()
        ctx.ai_provider = MagicMock()
        ctx.conversation_history = []
        ctx.artifacts = {}
        ctx.shared_state = {}

        # Make the agent execute return a mock response
        qa = r.get("qa-engineer")
        qa.execute.return_value = MagicMock(content="Diagnosed")

        orchestrator = AgentOrchestrator(r, ctx)
        task = AgentTask(description="Fix the error in deployment")

        orchestrator._execute_task(task)

        assert task.assigned_agent == "qa-engineer"
        assert task.status == "completed"

    def test_auto_assign_no_match_fails(self):
        from azext_prototype.agents.orchestrator import AgentOrchestrator, AgentTask

        r = AgentRegistry()  # empty
        ctx = MagicMock()

        orchestrator = AgentOrchestrator(r, ctx)
        task = AgentTask(description="Do something")

        orchestrator._execute_task(task)

        assert task.status == "failed"

    def test_auto_assign_doc_task(self):
        from azext_prototype.agents.orchestrator import AgentOrchestrator, AgentTask

        r = _populated_registry()
        ctx = MagicMock()
        ctx.ai_provider = MagicMock()
        ctx.conversation_history = []
        ctx.artifacts = {}
        ctx.shared_state = {}

        doc = r.get("doc-agent")
        doc.execute.return_value = MagicMock(content="Done")

        orchestrator = AgentOrchestrator(r, ctx)
        task = AgentTask(description="Generate the project documentation readme")

        orchestrator._execute_task(task)

        assert task.assigned_agent == "doc-agent"
