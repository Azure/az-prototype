"""Tests for Phase 4: Agent Enhancements.

Covers:
- SecurityReviewerAgent (4.1)
- MonitoringAgent (4.2)
- AgentContract / coordination (4.3)
- Parallel execution (4.4)
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from azext_prototype.agents.base import (
    AgentCapability,
    AgentContext,
    AgentContract,
    BaseAgent,
)
from azext_prototype.agents.registry import AgentRegistry
from azext_prototype.ai.provider import AIMessage, AIResponse


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture(autouse=True)
def _no_telemetry_network():
    with patch("azext_prototype.telemetry._send_envelope"):
        yield


@pytest.fixture
def mock_ai():
    provider = MagicMock()
    provider.chat.return_value = AIResponse(
        content="mock response",
        model="test-model",
        usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    )
    provider.default_model = "test-model"
    return provider


@pytest.fixture
def mock_context(mock_ai, tmp_path):
    return AgentContext(
        project_config={
            "project": {
                "name": "test-project",
                "location": "eastus",
                "iac_tool": "terraform",
                "environment": "dev",
            }
        },
        project_dir=str(tmp_path),
        ai_provider=mock_ai,
    )


# ======================================================================
# 4.1 SecurityReviewerAgent
# ======================================================================


class TestSecurityReviewerAgent:
    """Test the security-reviewer built-in agent."""

    def test_instantiation(self):
        from azext_prototype.agents.builtin.security_reviewer import SecurityReviewerAgent

        agent = SecurityReviewerAgent()
        assert agent.name == "security-reviewer"
        assert AgentCapability.SECURITY_REVIEW in agent.capabilities
        assert AgentCapability.ANALYZE in agent.capabilities

    def test_temperature(self):
        from azext_prototype.agents.builtin.security_reviewer import SecurityReviewerAgent

        agent = SecurityReviewerAgent()
        assert agent._temperature == 0.1

    def test_knowledge_role(self):
        from azext_prototype.agents.builtin.security_reviewer import SecurityReviewerAgent

        agent = SecurityReviewerAgent()
        assert agent._knowledge_role == "security-reviewer"

    def test_include_templates_false(self):
        from azext_prototype.agents.builtin.security_reviewer import SecurityReviewerAgent

        agent = SecurityReviewerAgent()
        assert agent._include_templates is False

    def test_keywords_cover_security_topics(self):
        from azext_prototype.agents.builtin.security_reviewer import SecurityReviewerAgent

        agent = SecurityReviewerAgent()
        for kw in ["security", "rbac", "encryption", "secret", "firewall"]:
            assert kw in agent._keywords

    def test_can_handle_security_task(self):
        from azext_prototype.agents.builtin.security_reviewer import SecurityReviewerAgent

        agent = SecurityReviewerAgent()
        score = agent.can_handle("Review the security of the terraform code")
        assert score > 0.3

    def test_can_handle_unrelated_task(self):
        from azext_prototype.agents.builtin.security_reviewer import SecurityReviewerAgent

        agent = SecurityReviewerAgent()
        score = agent.can_handle("Generate a backlog of user stories")
        assert score <= 0.5

    def test_execute_basic(self, mock_context):
        from azext_prototype.agents.builtin.security_reviewer import SecurityReviewerAgent

        agent = SecurityReviewerAgent()
        with patch.object(agent, "_get_governance_text", return_value=""):
            with patch.object(agent, "_get_knowledge_text", return_value=""):
                result = agent.execute(mock_context, "Review this terraform code")

        assert result.content == "mock response"
        mock_context.ai_provider.chat.assert_called_once()
        messages = mock_context.ai_provider.chat.call_args[0][0]
        # Should include system prompt + constraints + project context + user task
        assert any("security reviewer" in m.content.lower() for m in messages if isinstance(m.content, str))
        assert any("IaC Tool: terraform" in m.content for m in messages if isinstance(m.content, str))

    def test_execute_with_architecture_artifact(self, mock_context):
        from azext_prototype.agents.builtin.security_reviewer import SecurityReviewerAgent

        mock_context.add_artifact("architecture", "App Service + Cosmos DB + Key Vault")
        agent = SecurityReviewerAgent()
        with patch.object(agent, "_get_governance_text", return_value=""):
            with patch.object(agent, "_get_knowledge_text", return_value=""):
                agent.execute(mock_context, "Review security")

        messages = mock_context.ai_provider.chat.call_args[0][0]
        arch_messages = [m for m in messages if isinstance(m.content, str) and "ARCHITECTURE CONTEXT" in m.content]
        assert len(arch_messages) == 1

    def test_contract(self):
        from azext_prototype.agents.builtin.security_reviewer import SecurityReviewerAgent

        agent = SecurityReviewerAgent()
        contract = agent.get_contract()
        assert "architecture" in contract.inputs
        assert "iac_code" in contract.inputs
        assert "security_findings" in contract.outputs
        assert "terraform-agent" in contract.delegates_to


# ======================================================================
# 4.2 MonitoringAgent
# ======================================================================


class TestMonitoringAgent:
    """Test the monitoring-agent built-in agent."""

    def test_instantiation(self):
        from azext_prototype.agents.builtin.monitoring_agent import MonitoringAgent

        agent = MonitoringAgent()
        assert agent.name == "monitoring-agent"
        assert AgentCapability.MONITORING in agent.capabilities
        assert AgentCapability.ANALYZE in agent.capabilities

    def test_temperature(self):
        from azext_prototype.agents.builtin.monitoring_agent import MonitoringAgent

        agent = MonitoringAgent()
        assert agent._temperature == 0.2

    def test_knowledge_role(self):
        from azext_prototype.agents.builtin.monitoring_agent import MonitoringAgent

        agent = MonitoringAgent()
        assert agent._knowledge_role == "monitoring"

    def test_keywords_cover_monitoring_topics(self):
        from azext_prototype.agents.builtin.monitoring_agent import MonitoringAgent

        agent = MonitoringAgent()
        for kw in ["monitor", "alert", "diagnostic", "app insights", "log analytics"]:
            assert kw in agent._keywords

    def test_can_handle_monitoring_task(self):
        from azext_prototype.agents.builtin.monitoring_agent import MonitoringAgent

        agent = MonitoringAgent()
        score = agent.can_handle("Generate monitoring alerts and diagnostics")
        assert score > 0.3

    def test_execute_basic(self, mock_context):
        from azext_prototype.agents.builtin.monitoring_agent import MonitoringAgent

        agent = MonitoringAgent()
        with patch.object(agent, "_get_governance_text", return_value=""):
            with patch.object(agent, "_get_knowledge_text", return_value=""):
                result = agent.execute(mock_context, "Generate monitoring config")

        assert result.content == "mock response"
        messages = mock_context.ai_provider.chat.call_args[0][0]
        assert any("monitoring specialist" in m.content.lower() for m in messages if isinstance(m.content, str))
        assert any("IaC Tool: terraform" in m.content for m in messages if isinstance(m.content, str))

    def test_execute_with_artifacts(self, mock_context):
        from azext_prototype.agents.builtin.monitoring_agent import MonitoringAgent

        mock_context.add_artifact("architecture", "App Service architecture")
        mock_context.add_artifact("deployment_plan", "3 stages: infra, data, apps")
        agent = MonitoringAgent()
        with patch.object(agent, "_get_governance_text", return_value=""):
            with patch.object(agent, "_get_knowledge_text", return_value=""):
                agent.execute(mock_context, "Generate monitoring")

        messages = mock_context.ai_provider.chat.call_args[0][0]
        assert any("ARCHITECTURE CONTEXT" in m.content for m in messages if isinstance(m.content, str))
        assert any("DEPLOYMENT PLAN" in m.content for m in messages if isinstance(m.content, str))

    def test_contract(self):
        from azext_prototype.agents.builtin.monitoring_agent import MonitoringAgent

        agent = MonitoringAgent()
        contract = agent.get_contract()
        assert "architecture" in contract.inputs
        assert "deployment_plan" in contract.inputs
        assert "monitoring_config" in contract.outputs


# ======================================================================
# 4.1 + 4.2: Registry integration
# ======================================================================


class TestNewAgentsInRegistry:
    """Test that new agents register and resolve correctly."""

    def test_security_reviewer_registered(self, populated_registry):
        assert "security-reviewer" in populated_registry
        agent = populated_registry.get("security-reviewer")
        assert agent.name == "security-reviewer"

    def test_monitoring_agent_registered(self, populated_registry):
        assert "monitoring-agent" in populated_registry
        agent = populated_registry.get("monitoring-agent")
        assert agent.name == "monitoring-agent"

    def test_all_builtin_agents_registered(self, populated_registry):
        expected = [
            "cloud-architect", "terraform-agent", "bicep-agent",
            "app-developer", "doc-agent", "qa-engineer",
            "biz-analyst", "cost-analyst", "project-manager",
            "security-reviewer", "monitoring-agent",
        ]
        for name in expected:
            assert name in populated_registry, f"Built-in agent '{name}' not registered"

    def test_builtin_count(self, populated_registry):
        assert len(populated_registry) == 11

    def test_security_review_capability(self, populated_registry):
        agents = populated_registry.find_by_capability(AgentCapability.SECURITY_REVIEW)
        assert len(agents) >= 1
        assert agents[0].name == "security-reviewer"

    def test_monitoring_capability(self, populated_registry):
        agents = populated_registry.find_by_capability(AgentCapability.MONITORING)
        assert len(agents) >= 1
        assert agents[0].name == "monitoring-agent"

    def test_find_best_for_security_task(self, populated_registry):
        best = populated_registry.find_best_for_task(
            "Review the security of the generated terraform code for RBAC issues"
        )
        assert best is not None
        assert best.name == "security-reviewer"

    def test_find_best_for_monitoring_task(self, populated_registry):
        best = populated_registry.find_best_for_task(
            "Generate monitoring alerts and diagnostic settings for all resources"
        )
        assert best is not None
        assert best.name == "monitoring-agent"


# ======================================================================
# 4.3: AgentContract
# ======================================================================


class TestAgentContract:
    """Test AgentContract dataclass and integration."""

    def test_default_contract(self):
        contract = AgentContract()
        assert contract.inputs == []
        assert contract.outputs == []
        assert contract.delegates_to == []

    def test_contract_with_values(self):
        contract = AgentContract(
            inputs=["architecture"],
            outputs=["iac_code"],
            delegates_to=["app-developer"],
        )
        assert contract.inputs == ["architecture"]
        assert contract.outputs == ["iac_code"]
        assert contract.delegates_to == ["app-developer"]

    def test_base_agent_get_contract_default(self):
        """Agent without _contract returns empty contract."""
        agent = BaseAgent(name="test", description="test")
        contract = agent.get_contract()
        assert contract.inputs == []
        assert contract.outputs == []

    def test_base_agent_get_contract_set(self):
        """Agent with _contract returns it."""
        agent = BaseAgent(name="test", description="test")
        agent._contract = AgentContract(
            inputs=["requirements"],
            outputs=["architecture"],
        )
        contract = agent.get_contract()
        assert contract.inputs == ["requirements"]
        assert contract.outputs == ["architecture"]

    def test_to_dict_without_contract(self):
        agent = BaseAgent(
            name="test", description="test",
            capabilities=[AgentCapability.DEVELOP],
        )
        d = agent.to_dict()
        assert "contract" not in d

    def test_to_dict_with_contract(self):
        agent = BaseAgent(
            name="test", description="test",
            capabilities=[AgentCapability.DEVELOP],
        )
        agent._contract = AgentContract(
            inputs=["architecture"],
            outputs=["iac_code"],
            delegates_to=["app-developer"],
        )
        d = agent.to_dict()
        assert "contract" in d
        assert d["contract"]["inputs"] == ["architecture"]
        assert d["contract"]["outputs"] == ["iac_code"]
        assert d["contract"]["delegates_to"] == ["app-developer"]


class TestAllAgentContracts:
    """Verify all builtin agents have contracts set."""

    def test_all_agents_have_contracts(self, populated_registry):
        for agent in populated_registry.list_all():
            contract = agent.get_contract()
            assert isinstance(contract, AgentContract), (
                f"Agent '{agent.name}' does not return an AgentContract"
            )

    def test_architect_contract(self, populated_registry):
        agent = populated_registry.get("cloud-architect")
        c = agent.get_contract()
        assert "requirements" in c.inputs
        assert "architecture" in c.outputs
        assert "deployment_plan" in c.outputs

    def test_terraform_contract(self, populated_registry):
        agent = populated_registry.get("terraform-agent")
        c = agent.get_contract()
        assert "architecture" in c.inputs
        assert "iac_code" in c.outputs

    def test_bicep_contract(self, populated_registry):
        agent = populated_registry.get("bicep-agent")
        c = agent.get_contract()
        assert "architecture" in c.inputs
        assert "iac_code" in c.outputs

    def test_biz_analyst_contract(self, populated_registry):
        agent = populated_registry.get("biz-analyst")
        c = agent.get_contract()
        assert c.inputs == []
        assert "requirements" in c.outputs
        assert "scope" in c.outputs

    def test_qa_engineer_contract(self, populated_registry):
        agent = populated_registry.get("qa-engineer")
        c = agent.get_contract()
        assert "qa_diagnosis" in c.outputs
        assert len(c.delegates_to) > 0

    def test_project_manager_contract(self, populated_registry):
        agent = populated_registry.get("project-manager")
        c = agent.get_contract()
        assert "requirements" in c.inputs
        assert "backlog_items" in c.outputs

    def test_doc_agent_contract(self, populated_registry):
        agent = populated_registry.get("doc-agent")
        c = agent.get_contract()
        assert "architecture" in c.inputs
        assert "documentation" in c.outputs

    def test_cost_analyst_contract(self, populated_registry):
        agent = populated_registry.get("cost-analyst")
        c = agent.get_contract()
        assert "architecture" in c.inputs
        assert "cost_estimate" in c.outputs

    def test_app_developer_contract(self, populated_registry):
        agent = populated_registry.get("app-developer")
        c = agent.get_contract()
        assert "architecture" in c.inputs
        assert "app_code" in c.outputs


# ======================================================================
# 4.3: Orchestrator contract validation
# ======================================================================


class TestOrchestratorContractValidation:
    """Test AgentOrchestrator.check_contracts()."""

    def test_no_warnings_when_artifacts_available(self, populated_registry, mock_context):
        from azext_prototype.agents.orchestrator import AgentOrchestrator, AgentTask, TeamPlan

        mock_context.add_artifact("requirements", "some requirements")
        orch = AgentOrchestrator(populated_registry, mock_context)
        plan = TeamPlan(
            objective="Design architecture",
            tasks=[AgentTask(description="Design", assigned_agent="cloud-architect")],
        )
        warnings = orch.check_contracts(plan)
        assert len(warnings) == 0

    def test_warnings_for_missing_inputs(self, populated_registry, mock_context):
        from azext_prototype.agents.orchestrator import AgentOrchestrator, AgentTask, TeamPlan

        orch = AgentOrchestrator(populated_registry, mock_context)
        plan = TeamPlan(
            objective="Design architecture",
            tasks=[AgentTask(description="Design", assigned_agent="cloud-architect")],
        )
        warnings = orch.check_contracts(plan)
        assert len(warnings) > 0
        assert any("requirements" in w for w in warnings)

    def test_chain_satisfies_downstream(self, populated_registry, mock_context):
        from azext_prototype.agents.orchestrator import AgentOrchestrator, AgentTask, TeamPlan

        orch = AgentOrchestrator(populated_registry, mock_context)
        plan = TeamPlan(
            objective="Full build",
            tasks=[
                AgentTask(description="Gather requirements", assigned_agent="biz-analyst"),
                AgentTask(description="Design architecture", assigned_agent="cloud-architect"),
                AgentTask(description="Generate terraform", assigned_agent="terraform-agent"),
            ],
        )
        warnings = orch.check_contracts(plan)
        # biz-analyst has no required inputs
        # cloud-architect needs requirements (produced by biz-analyst)
        # terraform needs architecture (produced by cloud-architect)
        assert len(warnings) == 0

    def test_unassigned_tasks_skipped(self, populated_registry, mock_context):
        from azext_prototype.agents.orchestrator import AgentOrchestrator, AgentTask, TeamPlan

        orch = AgentOrchestrator(populated_registry, mock_context)
        plan = TeamPlan(
            objective="test",
            tasks=[AgentTask(description="do something")],
        )
        warnings = orch.check_contracts(plan)
        assert len(warnings) == 0

    def test_empty_plan(self, populated_registry, mock_context):
        from azext_prototype.agents.orchestrator import AgentOrchestrator, TeamPlan

        orch = AgentOrchestrator(populated_registry, mock_context)
        plan = TeamPlan(objective="nothing")
        warnings = orch.check_contracts(plan)
        assert warnings == []


# ======================================================================
# 4.4: Parallel execution
# ======================================================================


class TestParallelExecution:
    """Test AgentOrchestrator.execute_plan_parallel()."""

    def _make_agent(self, name, inputs=None, outputs=None, delay=0):
        """Create a stub agent with contract and optional delay."""
        agent = MagicMock(spec=BaseAgent)
        agent.name = name
        agent.description = f"Test agent {name}"
        agent.capabilities = [AgentCapability.DEVELOP]
        contract = AgentContract(
            inputs=inputs or [],
            outputs=outputs or [],
        )
        agent.get_contract.return_value = contract
        agent.can_handle.return_value = 0.5

        def delayed_execute(context, task):
            if delay:
                time.sleep(delay)
            return AIResponse(content=f"result from {name}", model="test")

        agent.execute.side_effect = delayed_execute
        return agent

    def _make_registry(self, agents):
        reg = AgentRegistry()
        for agent in agents:
            reg.register_builtin(agent)
        return reg

    def test_parallel_independent_tasks(self, mock_context):
        from azext_prototype.agents.orchestrator import AgentOrchestrator, AgentTask, TeamPlan

        a1 = self._make_agent("agent-a", outputs=["out_a"], delay=0.05)
        a2 = self._make_agent("agent-b", outputs=["out_b"], delay=0.05)
        registry = self._make_registry([a1, a2])

        orch = AgentOrchestrator(registry, mock_context)
        plan = TeamPlan(
            objective="parallel test",
            tasks=[
                AgentTask(description="task a", assigned_agent="agent-a"),
                AgentTask(description="task b", assigned_agent="agent-b"),
            ],
        )

        start = time.time()
        results = orch.execute_plan_parallel(plan, max_workers=2)
        elapsed = time.time() - start

        # Both should complete
        assert results[0].status == "completed"
        assert results[1].status == "completed"
        # Should run in parallel (< 2x the delay)
        assert elapsed < 0.15

    def test_sequential_dependent_tasks(self, mock_context):
        from azext_prototype.agents.orchestrator import AgentOrchestrator, AgentTask, TeamPlan

        a1 = self._make_agent("producer", outputs=["artifact_x"])
        a2 = self._make_agent("consumer", inputs=["artifact_x"])
        registry = self._make_registry([a1, a2])

        orch = AgentOrchestrator(registry, mock_context)
        plan = TeamPlan(
            objective="dependency test",
            tasks=[
                AgentTask(description="produce", assigned_agent="producer"),
                AgentTask(description="consume", assigned_agent="consumer"),
            ],
        )
        results = orch.execute_plan_parallel(plan, max_workers=2)
        assert results[0].status == "completed"
        assert results[1].status == "completed"

        # Producer must execute before consumer (due to dependency)
        producer_idx = next(
            i for i, e in enumerate(orch.execution_log)
            if e.get("agent") == "producer"
        )
        consumer_idx = next(
            i for i, e in enumerate(orch.execution_log)
            if e.get("agent") == "consumer"
        )
        assert producer_idx < consumer_idx

    def test_empty_plan(self, mock_context):
        from azext_prototype.agents.orchestrator import AgentOrchestrator, TeamPlan

        registry = AgentRegistry()
        orch = AgentOrchestrator(registry, mock_context)
        plan = TeamPlan(objective="empty")
        results = orch.execute_plan_parallel(plan)
        assert results == []

    def test_single_task(self, mock_context):
        from azext_prototype.agents.orchestrator import AgentOrchestrator, AgentTask, TeamPlan

        a1 = self._make_agent("solo", outputs=["result"])
        registry = self._make_registry([a1])

        orch = AgentOrchestrator(registry, mock_context)
        plan = TeamPlan(
            objective="solo",
            tasks=[AgentTask(description="solo task", assigned_agent="solo")],
        )
        results = orch.execute_plan_parallel(plan)
        assert len(results) == 1
        assert results[0].status == "completed"

    def test_failed_task_in_parallel(self, mock_context):
        from azext_prototype.agents.orchestrator import AgentOrchestrator, AgentTask, TeamPlan

        a1 = self._make_agent("good", outputs=["out_a"])
        a2 = self._make_agent("bad", outputs=["out_b"])
        a2.execute.side_effect = RuntimeError("boom")
        registry = self._make_registry([a1, a2])

        orch = AgentOrchestrator(registry, mock_context)
        plan = TeamPlan(
            objective="failure test",
            tasks=[
                AgentTask(description="good task", assigned_agent="good"),
                AgentTask(description="bad task", assigned_agent="bad"),
            ],
        )
        results = orch.execute_plan_parallel(plan, max_workers=2)
        # Good task completes, bad task fails
        statuses = {r.assigned_agent: r.status for r in results}
        assert statuses["good"] == "completed"
        assert statuses["bad"] == "failed"

    def test_three_stage_pipeline(self, mock_context):
        """A -> B -> C dependency chain executes in order."""
        from azext_prototype.agents.orchestrator import AgentOrchestrator, AgentTask, TeamPlan

        a = self._make_agent("stage-a", outputs=["data_a"])
        b = self._make_agent("stage-b", inputs=["data_a"], outputs=["data_b"])
        c = self._make_agent("stage-c", inputs=["data_b"])
        registry = self._make_registry([a, b, c])

        orch = AgentOrchestrator(registry, mock_context)
        plan = TeamPlan(
            objective="pipeline",
            tasks=[
                AgentTask(description="step a", assigned_agent="stage-a"),
                AgentTask(description="step b", assigned_agent="stage-b"),
                AgentTask(description="step c", assigned_agent="stage-c"),
            ],
        )
        results = orch.execute_plan_parallel(plan, max_workers=4)
        assert all(r.status == "completed" for r in results)

    def test_diamond_dependency(self, mock_context):
        """A -> B, A -> C, B+C -> D."""
        from azext_prototype.agents.orchestrator import AgentOrchestrator, AgentTask, TeamPlan

        a = self._make_agent("root", outputs=["data_root"])
        b = self._make_agent("left", inputs=["data_root"], outputs=["data_left"])
        c = self._make_agent("right", inputs=["data_root"], outputs=["data_right"])
        d = self._make_agent("merge", inputs=["data_left", "data_right"])
        registry = self._make_registry([a, b, c, d])

        orch = AgentOrchestrator(registry, mock_context)
        plan = TeamPlan(
            objective="diamond",
            tasks=[
                AgentTask(description="root", assigned_agent="root"),
                AgentTask(description="left", assigned_agent="left"),
                AgentTask(description="right", assigned_agent="right"),
                AgentTask(description="merge", assigned_agent="merge"),
            ],
        )
        results = orch.execute_plan_parallel(plan, max_workers=4)
        assert all(r.status == "completed" for r in results)


# ======================================================================
# Knowledge role templates exist for new agents
# ======================================================================


class TestKnowledgeRoleTemplates:
    """Verify knowledge role templates for new agents exist and load."""

    def test_security_reviewer_role_loads(self):
        from azext_prototype.knowledge import KnowledgeLoader

        loader = KnowledgeLoader()
        content = loader.load_role("security-reviewer")
        assert content
        assert "Security Reviewer" in content

    def test_monitoring_role_loads(self):
        from azext_prototype.knowledge import KnowledgeLoader

        loader = KnowledgeLoader()
        content = loader.load_role("monitoring")
        assert content
        assert "Monitoring" in content

    def test_security_reviewer_compose_context(self):
        from azext_prototype.knowledge import KnowledgeLoader

        loader = KnowledgeLoader()
        ctx = loader.compose_context(role="security-reviewer", include_constraints=True)
        assert ctx
        assert len(ctx) > 100

    def test_monitoring_compose_context(self):
        from azext_prototype.knowledge import KnowledgeLoader

        loader = KnowledgeLoader()
        ctx = loader.compose_context(role="monitoring", include_constraints=True)
        assert ctx
        assert len(ctx) > 100
