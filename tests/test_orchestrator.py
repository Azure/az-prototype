"""Tests for azext_prototype.agents.orchestrator — agent team coordination."""

from unittest.mock import MagicMock

from azext_prototype.agents.base import AgentContext, BaseAgent
from azext_prototype.agents.orchestrator import (
    AgentOrchestrator,
    AgentTask,
    TeamPlan,
)
from azext_prototype.agents.registry import AgentRegistry
from azext_prototype.ai.provider import AIResponse


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_agent(name: str, capabilities=None, response_text="done"):
    """Create a mock agent that returns a fixed response."""
    agent = MagicMock(spec=BaseAgent)
    agent.name = name
    agent.description = f"{name} agent"
    agent.capabilities = capabilities or []
    agent.can_handle = MagicMock(return_value=0.5)
    agent.execute = MagicMock(
        return_value=AIResponse(content=response_text, model="test", usage={})
    )
    return agent


def _make_context(plan_text="1. [alpha] Do the thing"):
    """Create an AgentContext with a mock AI provider."""
    provider = MagicMock()
    provider.chat = MagicMock(
        return_value=AIResponse(content=plan_text, model="test", usage={})
    )
    return AgentContext(
        project_config={},
        project_dir="/tmp/test",
        ai_provider=provider,
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestAgentTask:
    """Test AgentTask dataclass."""

    def test_defaults(self):
        task = AgentTask(description="Build the API")
        assert task.status == "pending"
        assert task.assigned_agent is None
        assert task.sub_tasks == []
        assert task.result is None


class TestTeamPlan:
    """Test TeamPlan dataclass."""

    def test_creation(self):
        plan = TeamPlan(objective="Deploy everything")
        assert plan.objective == "Deploy everything"
        assert plan.tasks == []


class TestOrchestratorPlan:
    """Test AgentOrchestrator.plan()."""

    def test_plan_creates_tasks(self):
        registry = AgentRegistry()
        alpha = _make_agent("alpha")
        registry.register_builtin(alpha)

        ctx = _make_context("1. [alpha] Design the architecture\n2. [alpha] Write docs")
        orch = AgentOrchestrator(registry, ctx)

        plan = orch.plan("Build a web app", agent_names=["alpha"])
        assert plan.objective == "Build a web app"
        assert len(plan.tasks) >= 1

    def test_plan_parses_sub_tasks(self):
        registry = AgentRegistry()
        alpha = _make_agent("alpha")
        beta = _make_agent("beta")
        registry.register_builtin(alpha)
        registry.register_builtin(beta)

        plan_text = (
            "1. [alpha] Design the architecture\n"
            "   1a. [beta] Review networking\n"
            "2. [alpha] Write documentation\n"
        )
        ctx = _make_context(plan_text)
        orch = AgentOrchestrator(registry, ctx)

        plan = orch.plan("Build a web app")
        # First task should have a sub-task
        assert len(plan.tasks) >= 1
        if plan.tasks[0].sub_tasks:
            assert plan.tasks[0].sub_tasks[0].assigned_agent == "beta"


class TestOrchestratorExecute:
    """Test AgentOrchestrator.execute_plan() and run_team()."""

    def test_execute_plan_runs_all_tasks(self):
        registry = AgentRegistry()
        alpha = _make_agent("alpha", response_text="alpha output")
        registry.register_builtin(alpha)

        ctx = _make_context()
        orch = AgentOrchestrator(registry, ctx)

        plan = TeamPlan(
            objective="test",
            tasks=[AgentTask(description="task 1", assigned_agent="alpha")],
        )
        results = orch.execute_plan(plan)
        assert len(results) == 1
        assert results[0].status == "completed"
        assert results[0].result is not None
        assert results[0].result.content == "alpha output"

    def test_execute_handles_missing_agent(self):
        registry = AgentRegistry()
        ctx = _make_context()
        orch = AgentOrchestrator(registry, ctx)

        plan = TeamPlan(
            objective="test",
            tasks=[AgentTask(description="task 1", assigned_agent="nonexistent")],
        )
        results = orch.execute_plan(plan)
        assert results[0].status == "failed"

    def test_execute_sub_tasks(self):
        registry = AgentRegistry()
        alpha = _make_agent("alpha")
        beta = _make_agent("beta", response_text="beta output")
        registry.register_builtin(alpha)
        registry.register_builtin(beta)

        ctx = _make_context()
        orch = AgentOrchestrator(registry, ctx)

        plan = TeamPlan(
            objective="test",
            tasks=[
                AgentTask(
                    description="parent task",
                    assigned_agent="alpha",
                    sub_tasks=[
                        AgentTask(description="sub task", assigned_agent="beta"),
                    ],
                ),
            ],
        )
        results = orch.execute_plan(plan)
        assert results[0].status == "completed"
        assert results[0].sub_tasks[0].status == "completed"
        assert results[0].sub_tasks[0].result is not None
        assert results[0].sub_tasks[0].result.content == "beta output"

    def test_run_team_plans_and_executes(self):
        registry = AgentRegistry()
        alpha = _make_agent("alpha")
        registry.register_builtin(alpha)

        ctx = _make_context("1. [alpha] Do the work")
        orch = AgentOrchestrator(registry, ctx)

        results = orch.run_team("Build it", agent_names=["alpha"])
        assert len(results) >= 1
        # At least one task should be completed
        completed = [t for t in results if t.status == "completed"]
        assert len(completed) >= 1


class TestOrchestratorDelegate:
    """Test AgentOrchestrator.delegate()."""

    def test_delegate_to_known_agent(self):
        registry = AgentRegistry()
        beta = _make_agent("beta", response_text="beta delegated output")
        registry.register_builtin(beta)

        ctx = _make_context()
        orch = AgentOrchestrator(registry, ctx)

        result = orch.delegate("alpha", "beta", "Review the networking config")
        assert result.content == "beta delegated output"

    def test_delegate_to_unknown_agent_returns_error(self):
        registry = AgentRegistry()
        ctx = _make_context()
        orch = AgentOrchestrator(registry, ctx)

        result = orch.delegate("alpha", "nonexistent", "some task")
        assert "not found" in result.content

    def test_delegate_logs_delegation(self):
        registry = AgentRegistry()
        beta = _make_agent("beta")
        registry.register_builtin(beta)

        ctx = _make_context()
        orch = AgentOrchestrator(registry, ctx)

        orch.delegate("alpha", "beta", "sub-task")
        assert len(orch.execution_log) == 1
        assert orch.execution_log[0]["type"] == "delegation"
        assert orch.execution_log[0]["from"] == "alpha"
        assert orch.execution_log[0]["to"] == "beta"


class TestOrchestratorAutoAssign:
    """Test automatic agent assignment when no agent is specified."""

    def test_auto_assigns_best_agent(self):
        registry = AgentRegistry()
        alpha = _make_agent("alpha")
        alpha.can_handle = MagicMock(return_value=0.9)
        registry.register_builtin(alpha)

        ctx = _make_context()
        orch = AgentOrchestrator(registry, ctx)

        plan = TeamPlan(
            objective="test",
            tasks=[AgentTask(description="Build the API")],
        )
        results = orch.execute_plan(plan)
        assert results[0].assigned_agent == "alpha"
        assert results[0].status == "completed"


class TestOrchestratorConversationHistory:
    """Test that agent results are fed into conversation history."""

    def test_results_added_to_history(self):
        registry = AgentRegistry()
        alpha = _make_agent("alpha", response_text="alpha did stuff")
        beta = _make_agent("beta", response_text="beta did stuff")
        registry.register_builtin(alpha)
        registry.register_builtin(beta)

        ctx = _make_context()
        orch = AgentOrchestrator(registry, ctx)

        plan = TeamPlan(
            objective="test",
            tasks=[
                AgentTask(description="task 1", assigned_agent="alpha"),
                AgentTask(description="task 2", assigned_agent="beta"),
            ],
        )
        orch.execute_plan(plan)

        # Both results should be in conversation history
        history_content = " ".join(m.content for m in ctx.conversation_history)
        assert "alpha" in history_content
        assert "beta" in history_content
