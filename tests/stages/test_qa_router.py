from __future__ import annotations

"""Tests for route_error_to_qa() — QA error routing.

Tier 2: Conditional branches with multiple paths.

Covers:
- QA agent None -> early return
- agent_context None -> early return
- agent_context.ai_provider None -> early return
- QA agent executes successfully -> diagnosed=True
- QA agent returns empty content -> diagnosed=False
- QA agent returns None -> diagnosed=False
- QA agent raises exception -> diagnosed=False
- Token tracking when tracker provided
- Token tracking exception swallowed
- Knowledge contribution fire-and-forget (success + failure)
- Blocker recording when QA can't diagnose + escalation tracker present
- Blocker recording exception swallowed
- Error text truncation (max_error_chars)
- Display text truncation (max_display_chars)
- None/empty error handling
"""

from unittest.mock import MagicMock, patch

import pytest

from azext_prototype.stages.qa_router import route_error_to_qa

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def qa_agent():
    """Mock QA agent with successful response."""
    agent = MagicMock()
    agent.execute.return_value = MagicMock(content="Root cause: misconfigured SKU. Fix: use B1.")
    return agent


@pytest.fixture
def agent_context():
    """Mock AgentContext with AI provider."""
    ctx = MagicMock()
    ctx.ai_provider = MagicMock()
    return ctx


@pytest.fixture
def token_tracker():
    """Mock TokenTracker."""
    return MagicMock()


@pytest.fixture
def escalation_tracker():
    """Mock EscalationTracker."""
    return MagicMock()


# ------------------------------------------------------------------
# Early returns — no QA agent / no context / no provider
# ------------------------------------------------------------------


class TestEarlyReturns:
    def test_qa_agent_none(self, agent_context):
        result = route_error_to_qa(
            "Error occurred",
            "Build Stage 3",
            qa_agent=None,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
        )
        assert result["diagnosed"] is False
        assert "Error occurred" in result["content"]
        assert result["response"] is None

    def test_agent_context_none(self, qa_agent):
        result = route_error_to_qa(
            "Error",
            "Build Stage 1",
            qa_agent=qa_agent,
            agent_context=None,
            token_tracker=None,
            print_fn=MagicMock(),
        )
        assert result["diagnosed"] is False
        assert result["response"] is None

    def test_ai_provider_none(self, qa_agent):
        ctx = MagicMock()
        ctx.ai_provider = None
        result = route_error_to_qa(
            "Error",
            "Deploy",
            qa_agent=qa_agent,
            agent_context=ctx,
            token_tracker=None,
            print_fn=MagicMock(),
        )
        assert result["diagnosed"] is False

    def test_all_none(self):
        result = route_error_to_qa(
            "Error",
            "context",
            qa_agent=None,
            agent_context=None,
            token_tracker=None,
            print_fn=MagicMock(),
        )
        assert result["diagnosed"] is False


# ------------------------------------------------------------------
# Successful QA diagnosis
# ------------------------------------------------------------------


class TestSuccessfulDiagnosis:
    def test_diagnosed_true(self, qa_agent, agent_context):
        result = route_error_to_qa(
            "Terraform apply failed",
            "Build Stage 2",
            qa_agent=qa_agent,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
        )
        assert result["diagnosed"] is True
        assert "Root cause" in result["content"]
        assert result["response"] is not None

    def test_prints_qa_diagnosis(self, qa_agent, agent_context):
        printed = []
        route_error_to_qa(
            "Error",
            "Build",
            qa_agent=qa_agent,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=printed.append,
        )
        assert any("QA Diagnosis" in msg for msg in printed)

    def test_exception_error_converted_to_string(self, qa_agent, agent_context):
        route_error_to_qa(
            RuntimeError("connection timeout"),
            "Deploy Stage 1",
            qa_agent=qa_agent,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
        )
        task_arg = qa_agent.execute.call_args[0][1]
        assert "connection timeout" in task_arg

    def test_services_kwarg_forwarded(self, qa_agent, agent_context):
        with patch("azext_prototype.stages.qa_router._submit_knowledge") as mock_submit:
            route_error_to_qa(
                "Error",
                "Build",
                qa_agent=qa_agent,
                agent_context=agent_context,
                token_tracker=None,
                print_fn=MagicMock(),
                services=["key-vault", "cosmos-db"],
            )
            mock_submit.assert_called_once()
            _, _, services_arg, _ = mock_submit.call_args[0]
            assert services_arg == ["key-vault", "cosmos-db"]


# ------------------------------------------------------------------
# QA agent failures
# ------------------------------------------------------------------


class TestQAFailures:
    def test_qa_agent_raises_exception(self, agent_context):
        bad_agent = MagicMock()
        bad_agent.execute.side_effect = RuntimeError("model overloaded")
        result = route_error_to_qa(
            "Error",
            "Build",
            qa_agent=bad_agent,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
        )
        assert result["diagnosed"] is False
        assert result["response"] is None

    def test_qa_returns_none(self, agent_context):
        agent = MagicMock()
        agent.execute.return_value = None
        result = route_error_to_qa(
            "Error",
            "Build",
            qa_agent=agent,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
        )
        assert result["diagnosed"] is False

    def test_qa_returns_empty_content(self, agent_context):
        agent = MagicMock()
        agent.execute.return_value = MagicMock(content="")
        result = route_error_to_qa(
            "Error",
            "Build",
            qa_agent=agent,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
        )
        assert result["diagnosed"] is False
        assert result["response"] is not None

    def test_qa_returns_none_content(self, agent_context):
        agent = MagicMock()
        agent.execute.return_value = MagicMock(content=None)
        result = route_error_to_qa(
            "Error",
            "Build",
            qa_agent=agent,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
        )
        assert result["diagnosed"] is False


# ------------------------------------------------------------------
# Token tracking
# ------------------------------------------------------------------


class TestTokenTracking:
    def test_tokens_recorded_on_success(self, qa_agent, agent_context, token_tracker):
        route_error_to_qa(
            "Error",
            "Build",
            qa_agent=qa_agent,
            agent_context=agent_context,
            token_tracker=token_tracker,
            print_fn=MagicMock(),
        )
        token_tracker.record.assert_called_once_with(qa_agent.execute.return_value)

    def test_no_token_tracker_no_error(self, qa_agent, agent_context):
        # Should not raise even when token_tracker is None
        result = route_error_to_qa(
            "Error",
            "Build",
            qa_agent=qa_agent,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
        )
        assert result["diagnosed"] is True

    def test_token_tracker_exception_swallowed(self, qa_agent, agent_context):
        bad_tracker = MagicMock()
        bad_tracker.record.side_effect = RuntimeError("tracker broken")
        result = route_error_to_qa(
            "Error",
            "Build",
            qa_agent=qa_agent,
            agent_context=agent_context,
            token_tracker=bad_tracker,
            print_fn=MagicMock(),
        )
        assert result["diagnosed"] is True  # Should still succeed

    def test_tokens_not_recorded_when_response_is_none(self, agent_context, token_tracker):
        agent = MagicMock()
        agent.execute.return_value = None
        route_error_to_qa(
            "Error",
            "Build",
            qa_agent=agent,
            agent_context=agent_context,
            token_tracker=token_tracker,
            print_fn=MagicMock(),
        )
        token_tracker.record.assert_not_called()


# ------------------------------------------------------------------
# Knowledge contribution (fire-and-forget)
# ------------------------------------------------------------------


class TestKnowledgeContribution:
    def test_knowledge_submitted_on_success(self, qa_agent, agent_context):
        with patch("azext_prototype.stages.qa_router._submit_knowledge") as mock_submit:
            route_error_to_qa(
                "Error",
                "Build Stage 3",
                qa_agent=qa_agent,
                agent_context=agent_context,
                token_tracker=None,
                print_fn=MagicMock(),
                services=["cosmos-db"],
            )
            mock_submit.assert_called_once()

    def test_knowledge_exception_swallowed(self, qa_agent, agent_context):
        with patch(
            "azext_prototype.stages.qa_router._submit_knowledge",
            side_effect=RuntimeError("GitHub down"),
        ):
            result = route_error_to_qa(
                "Error",
                "Build",
                qa_agent=qa_agent,
                agent_context=agent_context,
                token_tracker=None,
                print_fn=MagicMock(),
            )
            # Should still return successfully
            assert result["diagnosed"] is True


# ------------------------------------------------------------------
# Blocker recording (escalation tracker)
# ------------------------------------------------------------------


class TestBlockerRecording:
    def test_blocker_recorded_when_qa_cant_diagnose(self, agent_context, escalation_tracker):
        agent = MagicMock()
        agent.execute.return_value = MagicMock(content="")

        route_error_to_qa(
            "Deployment failed: quota exceeded",
            "Deploy Stage 1",
            qa_agent=agent,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
            escalation_tracker=escalation_tracker,
            source_agent="terraform-agent",
            source_stage="deploy",
        )
        escalation_tracker.record_blocker.assert_called_once()
        call_args = escalation_tracker.record_blocker.call_args
        assert call_args[0][0] == "Deploy Stage 1"
        assert "quota exceeded" in call_args[0][1]
        assert call_args[1]["source_agent"] == "terraform-agent"
        assert call_args[1]["source_stage"] == "deploy"

    def test_default_source_agent_is_qa_engineer(self, agent_context, escalation_tracker):
        agent = MagicMock()
        agent.execute.return_value = MagicMock(content="")

        route_error_to_qa(
            "Error",
            "Build",
            qa_agent=agent,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
            escalation_tracker=escalation_tracker,
        )
        call_args = escalation_tracker.record_blocker.call_args
        assert call_args[1]["source_agent"] == "qa-engineer"

    def test_no_blocker_when_no_tracker(self, agent_context):
        agent = MagicMock()
        agent.execute.return_value = MagicMock(content="")
        # Should not raise
        result = route_error_to_qa(
            "Error",
            "Build",
            qa_agent=agent,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
            escalation_tracker=None,
        )
        assert result["diagnosed"] is False

    def test_blocker_not_recorded_on_success(self, qa_agent, agent_context, escalation_tracker):
        route_error_to_qa(
            "Error",
            "Build",
            qa_agent=qa_agent,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
            escalation_tracker=escalation_tracker,
        )
        escalation_tracker.record_blocker.assert_not_called()

    def test_blocker_recording_exception_swallowed(self, agent_context):
        agent = MagicMock()
        agent.execute.return_value = MagicMock(content="")

        bad_tracker = MagicMock()
        bad_tracker.record_blocker.side_effect = RuntimeError("disk full")

        result = route_error_to_qa(
            "Error",
            "Build",
            qa_agent=agent,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
            escalation_tracker=bad_tracker,
        )
        assert result["diagnosed"] is False  # Still returns gracefully


# ------------------------------------------------------------------
# Error text handling
# ------------------------------------------------------------------


class TestErrorTextHandling:
    def test_error_text_truncated(self, agent_context):
        long_error = "x" * 5000
        result = route_error_to_qa(
            long_error,
            "Build",
            qa_agent=None,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
            max_error_chars=100,
        )
        assert len(result["content"]) == 100

    def test_none_error_becomes_unknown(self, agent_context):
        result = route_error_to_qa(
            None,
            "Build",
            qa_agent=None,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
        )
        assert result["content"] == "Unknown error"

    def test_empty_string_error_becomes_unknown(self, agent_context):
        result = route_error_to_qa(
            "",
            "Build",
            qa_agent=None,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
        )
        assert result["content"] == "Unknown error"

    def test_display_truncated(self, agent_context):
        long_content = "R" * 3000
        agent = MagicMock()
        agent.execute.return_value = MagicMock(content=long_content)
        printed = []
        route_error_to_qa(
            "Error",
            "Build",
            qa_agent=agent,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=printed.append,
            max_display_chars=500,
        )
        # The displayed content should be truncated
        display_lines = [p for p in printed if p and "QA Diagnosis" not in p and p.strip()]
        if display_lines:
            assert len(display_lines[0]) <= 500

    def test_custom_max_error_chars(self, qa_agent, agent_context):
        long_error = "E" * 5000
        route_error_to_qa(
            long_error,
            "Build",
            qa_agent=qa_agent,
            agent_context=agent_context,
            token_tracker=None,
            print_fn=MagicMock(),
            max_error_chars=50,
        )
        task_arg = qa_agent.execute.call_args[0][1]
        # The error text in the task should be truncated to 50 chars
        assert "E" * 50 in task_arg
        assert "E" * 51 not in task_arg


# --- Additional imports from merged flat test ---
from azext_prototype.agents.base import AgentCapability
from azext_prototype.agents.base import AgentContext
from azext_prototype.ai.provider import AIResponse
from azext_prototype.stages.backlog_session import BacklogSession
from azext_prototype.stages.backlog_state import BacklogState
from azext_prototype.stages.build_session import BuildSession
from azext_prototype.stages.build_state import BuildState
from azext_prototype.stages.deploy_session import DeploySession
from azext_prototype.stages.deploy_state import DeployState
from azext_prototype.stages.discovery import DiscoverySession
import json


# ======================================================================


def _make_response(content: str = "Root cause: X. Fix: do Y.") -> AIResponse:
    return AIResponse(content=content, model="gpt-4o", usage={})

def _make_qa_agent(response: AIResponse | None = None, raises: Exception | None = None):
    agent = MagicMock()
    agent.name = "qa-engineer"
    if raises:
        agent.execute.side_effect = raises
    else:
        agent.execute.return_value = response or _make_response()
    return agent

def _make_context():
    return AgentContext(
        project_config={"project": {"name": "test"}},
        project_dir="/tmp/test",
        ai_provider=MagicMock(),
    )

def _make_tracker():
    tracker = MagicMock()
    return tracker

# ======================================================================


class TestRouteErrorToQA:
    """Tests for route_error_to_qa()."""

    def test_qa_agent_available_diagnoses_error(self):
        qa = _make_qa_agent()
        ctx = _make_context()
        tracker = _make_tracker()
        printed = []

        result = route_error_to_qa(
            "Something broke",
            "Build Stage 1",
            qa,
            ctx,
            tracker,
            printed.append,
        )

        assert result["diagnosed"] is True
        assert result["content"] == "Root cause: X. Fix: do Y."
        assert result["response"] is not None
        qa.execute.assert_called_once()
        tracker.record.assert_called_once()

    def test_qa_agent_none_returns_graceful_fallback(self):
        ctx = _make_context()
        printed = []

        result = route_error_to_qa(
            "Something broke",
            "Build Stage 1",
            None,
            ctx,
            None,
            printed.append,
        )

        assert result["diagnosed"] is False
        assert result["content"] == "Something broke"
        assert result["response"] is None
        assert len(printed) == 0  # no output when undiagnosed

    def test_string_error_input(self):
        qa = _make_qa_agent()
        ctx = _make_context()
        printed = []

        result = route_error_to_qa(
            "Connection refused",
            "Deploy Stage 2",
            qa,
            ctx,
            None,
            printed.append,
        )

        assert result["diagnosed"] is True
        assert "Connection refused" in qa.execute.call_args[0][1]

    def test_exception_error_input(self):
        qa = _make_qa_agent()
        ctx = _make_context()
        printed = []

        result = route_error_to_qa(
            ValueError("bad value"),
            "Build Stage 3",
            qa,
            ctx,
            None,
            printed.append,
        )

        assert result["diagnosed"] is True
        assert "bad value" in qa.execute.call_args[0][1]

    def test_long_error_truncated_at_max_chars(self):
        qa = _make_qa_agent()
        ctx = _make_context()
        printed = []

        long_error = "x" * 5000

        result = route_error_to_qa(
            long_error,
            "Build Stage 1",
            qa,
            ctx,
            None,
            printed.append,
            max_error_chars=100,
        )

        assert result["diagnosed"] is True
        task_text = qa.execute.call_args[0][1]
        # The error in the task should be truncated
        assert "x" * 100 in task_text
        assert "x" * 5000 not in task_text

    def test_qa_agent_raises_returns_undiagnosed(self):
        qa = _make_qa_agent(raises=RuntimeError("QA crashed"))
        ctx = _make_context()
        printed = []

        result = route_error_to_qa(
            "Original error",
            "Build Stage 1",
            qa,
            ctx,
            None,
            printed.append,
        )

        assert result["diagnosed"] is False
        assert result["content"] == "Original error"
        assert result["response"] is None

    def test_token_tracker_records_response(self):
        qa = _make_qa_agent()
        ctx = _make_context()
        tracker = _make_tracker()

        route_error_to_qa(
            "error",
            "context",
            qa,
            ctx,
            tracker,
            lambda m: None,
        )

        tracker.record.assert_called_once()

    def test_token_tracker_none_does_not_crash(self):
        qa = _make_qa_agent()
        ctx = _make_context()

        result = route_error_to_qa(
            "error",
            "context",
            qa,
            ctx,
            None,
            lambda m: None,
        )

        assert result["diagnosed"] is True

    def test_print_fn_called_with_diagnosis(self):
        qa = _make_qa_agent(_make_response("Fix: restart the service"))
        ctx = _make_context()
        printed = []

        route_error_to_qa(
            "error",
            "context",
            qa,
            ctx,
            None,
            printed.append,
        )

        assert any("QA Diagnosis" in p for p in printed)
        assert any("Fix: restart the service" in p for p in printed)

    def test_display_truncated_at_max_display_chars(self):
        long_response = "a" * 3000
        qa = _make_qa_agent(_make_response(long_response))
        ctx = _make_context()
        printed = []

        route_error_to_qa(
            "error",
            "context",
            qa,
            ctx,
            None,
            printed.append,
            max_display_chars=500,
        )

        # One of the printed lines should be truncated
        display_lines = [p for p in printed if "a" in p]
        assert any(len(p) <= 500 for p in display_lines)

    def test_no_ai_provider_returns_undiagnosed(self):
        qa = _make_qa_agent()
        ctx = _make_context()
        ctx.ai_provider = None
        printed = []

        result = route_error_to_qa(
            "error",
            "context",
            qa,
            ctx,
            None,
            printed.append,
        )

        assert result["diagnosed"] is False

    def test_empty_error_uses_unknown(self):
        qa = _make_qa_agent()
        ctx = _make_context()

        result = route_error_to_qa(
            "",
            "context",
            qa,
            ctx,
            None,
            lambda m: None,
        )

        assert result["diagnosed"] is True
        # Should have used "Unknown error"
        task_text = qa.execute.call_args[0][1]
        assert "Unknown error" in task_text

    def test_none_error_uses_unknown(self):
        qa = _make_qa_agent()
        ctx = _make_context()

        result = route_error_to_qa(
            None,
            "context",
            qa,
            ctx,
            None,
            lambda m: None,
        )

        assert result["diagnosed"] is True
        task_text = qa.execute.call_args[0][1]
        assert "Unknown error" in task_text

    def test_qa_returns_empty_content(self):
        qa = _make_qa_agent(_make_response(""))
        ctx = _make_context()
        printed = []

        result = route_error_to_qa(
            "error",
            "context",
            qa,
            ctx,
            None,
            printed.append,
        )

        assert result["diagnosed"] is False

    @patch("azext_prototype.stages.qa_router._submit_knowledge")
    def test_knowledge_contribution_attempted(self, mock_submit):
        qa = _make_qa_agent()
        ctx = _make_context()

        route_error_to_qa(
            "error",
            "Build Stage 1",
            qa,
            ctx,
            None,
            lambda m: None,
            services=["key-vault"],
        )

        mock_submit.assert_called_once()
        args = mock_submit.call_args[0]
        assert args[0] == "Root cause: X. Fix: do Y."
        assert args[1] == "Build Stage 1"
        assert args[2] == ["key-vault"]

    @patch("azext_prototype.stages.qa_router._submit_knowledge", side_effect=Exception("boom"))
    def test_knowledge_failure_swallowed(self, mock_submit):
        qa = _make_qa_agent()
        ctx = _make_context()

        # Should not raise
        result = route_error_to_qa(
            "error",
            "context",
            qa,
            ctx,
            None,
            lambda m: None,
            services=["svc"],
        )

        assert result["diagnosed"] is True

    def test_services_none_no_knowledge_submitted(self):
        qa = _make_qa_agent()
        ctx = _make_context()

        with patch("azext_prototype.stages.qa_router._submit_knowledge") as mock_submit:
            route_error_to_qa(
                "error",
                "context",
                qa,
                ctx,
                None,
                lambda m: None,
            )

            mock_submit.assert_called_once()
            # services should be None
            assert mock_submit.call_args[0][2] is None

    def test_context_label_in_task_prompt(self):
        qa = _make_qa_agent()
        ctx = _make_context()

        route_error_to_qa(
            "error",
            "Deploy Stage 5: Redis Cache",
            qa,
            ctx,
            None,
            lambda m: None,
        )

        task_text = qa.execute.call_args[0][1]
        assert "Deploy Stage 5: Redis Cache" in task_text

    def test_token_tracker_record_failure_swallowed(self):
        qa = _make_qa_agent()
        ctx = _make_context()
        tracker = MagicMock()
        tracker.record.side_effect = Exception("tracker boom")

        # Should not raise
        result = route_error_to_qa(
            "error",
            "context",
            qa,
            ctx,
            tracker,
            lambda m: None,
        )

        assert result["diagnosed"] is True

# ======================================================================


class TestBuildSessionQARouting:
    """Test that build session routes errors through qa_router."""

    def _make_session(self, tmp_project, qa_agent=None, response=None):
        from azext_prototype.stages.build_session import BuildSession
        from azext_prototype.stages.build_state import BuildState

        ctx = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )

        registry = MagicMock()

        # IaC agent that fails
        iac_agent = MagicMock()
        iac_agent.name = "terraform-agent"
        if response is not None:
            iac_agent.execute.return_value = response
        else:
            iac_agent.execute.side_effect = RuntimeError("AI exploded")

        doc_agent = MagicMock()
        doc_agent.name = "doc-agent"
        doc_agent.execute.return_value = _make_response("# Docs")

        qa = qa_agent or _make_qa_agent()

        def find_by_cap(cap):
            from azext_prototype.agents.base import AgentCapability

            if cap == AgentCapability.TERRAFORM:
                return [iac_agent]
            if cap == AgentCapability.QA:
                return [qa]
            if cap == AgentCapability.DOCUMENT:
                return [doc_agent]
            if cap == AgentCapability.ARCHITECT:
                return []
            return []

        registry.find_by_capability.side_effect = find_by_cap

        build_state = BuildState(str(tmp_project))
        build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "category": "infra",
                    "dir": "concept/infra/terraform/stage-1-foundation",
                    "services": [{"name": "key-vault", "computed_name": "kv-1", "resource_type": "", "sku": ""}],
                    "status": "pending",
                    "files": [],
                },
            ]
        )

        with patch("azext_prototype.stages.build_session.ProjectConfig") as mock_config:
            mock_config.return_value.load.return_value = None
            mock_config.return_value.get.side_effect = lambda k, d=None: {
                "project.iac_tool": "terraform",
                "project.name": "test",
            }.get(k, d)
            mock_config.return_value.to_dict.return_value = {
                "naming": {"strategy": "simple"},
                "project": {"name": "test"},
            }
            session = BuildSession(ctx, registry, build_state=build_state)

        return session, qa

    @patch("azext_prototype.stages.qa_router._submit_knowledge")
    def test_stage_generation_failure_routes_to_qa(self, mock_knowledge, tmp_project):
        session, qa = self._make_session(tmp_project)
        printed = []

        session.run(
            design={"architecture": "Simple web app"},
            input_fn=lambda p: "done",
            print_fn=printed.append,
        )

        qa.execute.assert_called()
        assert any("QA Diagnosis" in p for p in printed)

    @patch("azext_prototype.stages.qa_router._submit_knowledge")
    def test_empty_response_routes_to_qa(self, mock_knowledge, tmp_project):
        empty_resp = AIResponse(content="", model="gpt-4o", usage={})
        session, qa = self._make_session(tmp_project, response=empty_resp)
        printed = []

        session.run(
            design={"architecture": "Simple web app"},
            input_fn=lambda p: "done",
            print_fn=printed.append,
        )

        # QA should be called for empty response
        qa.execute.assert_called()

# ======================================================================


class TestDiscoveryQARouting:
    """Test that discovery routes non-vision errors through qa_router."""

    @patch("azext_prototype.stages.qa_router._submit_knowledge")
    def test_non_vision_error_routes_to_qa(self, mock_knowledge, tmp_project):
        from azext_prototype.stages.discovery import DiscoverySession

        ctx = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )

        biz_agent = MagicMock()
        biz_agent.name = "biz-analyst"
        biz_agent.capabilities = []
        biz_agent._temperature = 0.5
        biz_agent._max_tokens = 8192
        biz_agent.get_system_messages.return_value = []

        qa = _make_qa_agent()

        registry = MagicMock()

        from azext_prototype.agents.base import AgentCapability

        def find_by_cap(cap):
            if cap == AgentCapability.BIZ_ANALYSIS:
                return [biz_agent]
            if cap == AgentCapability.QA:
                return [qa]
            return []

        registry.find_by_capability.side_effect = find_by_cap

        ctx.ai_provider.chat.side_effect = RuntimeError("API error")

        session = DiscoverySession(ctx, registry)

        with pytest.raises(RuntimeError, match="API error"):
            session.run(
                seed_context="test",
                input_fn=lambda p: "done",
                print_fn=lambda m: None,
            )

        # QA should have been called for the error diagnosis
        qa.execute.assert_called_once()

# ======================================================================


class TestBacklogQARouting:
    """Test that backlog session routes errors through qa_router."""

    def _make_session(self, tmp_project, items_response="[]"):
        from azext_prototype.stages.backlog_session import BacklogSession
        from azext_prototype.stages.backlog_state import BacklogState

        ctx = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )

        pm = MagicMock()
        pm.name = "project-manager"
        pm.get_system_messages.return_value = []
        qa = _make_qa_agent()

        registry = MagicMock()
        from azext_prototype.agents.base import AgentCapability

        def find_by_cap(cap):
            if cap == AgentCapability.BACKLOG_GENERATION:
                return [pm]
            if cap == AgentCapability.QA:
                return [qa]
            return []

        registry.find_by_capability.side_effect = find_by_cap

        ctx.ai_provider.chat.return_value = AIResponse(
            content=items_response,
            model="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

        session = BacklogSession(ctx, registry, backlog_state=BacklogState(str(tmp_project)))
        return session, qa, ctx

    @patch("azext_prototype.stages.qa_router._submit_knowledge")
    def test_empty_parse_triggers_qa(self, mock_knowledge, tmp_project):
        session, qa, ctx = self._make_session(tmp_project, items_response="not valid json at all")
        printed = []

        result = session.run(
            design_context="web app architecture",
            input_fn=lambda p: "done",
            print_fn=printed.append,
        )

        qa.execute.assert_called()
        assert result.cancelled is True

    @patch("azext_prototype.stages.qa_router._submit_knowledge")
    @patch("azext_prototype.stages.backlog_session.check_gh_auth", return_value=True)
    @patch("azext_prototype.stages.backlog_session.push_github_issue")
    def test_push_error_triggers_qa(self, mock_push, mock_auth, mock_knowledge, tmp_project):
        import json

        items = [{"epic": "Infra", "title": "Setup VNet", "description": "Create VNet", "tasks": [], "effort": "M"}]
        session, qa, ctx = self._make_session(tmp_project, items_response=json.dumps(items))

        mock_push.return_value = {"error": "gh: auth required"}

        printed = []
        session.run(
            design_context="web app",
            provider="github",
            org="myorg",
            project="myrepo",
            quick=True,
            input_fn=lambda p: "y",
            print_fn=printed.append,
        )

        qa.execute.assert_called()

# ======================================================================


class TestDeploySessionRefactoredQA:
    """Test that refactored deploy session still works correctly."""

    def test_handle_deploy_failure_uses_qa_router(self, tmp_project):
        from azext_prototype.stages.deploy_session import DeploySession
        from azext_prototype.stages.deploy_state import DeployState

        ctx = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )

        qa = _make_qa_agent(_make_response("Root cause: missing permissions"))
        registry = MagicMock()
        from azext_prototype.agents.base import AgentCapability

        def find_by_cap(cap):
            if cap == AgentCapability.QA:
                return [qa]
            return []

        registry.find_by_capability.side_effect = find_by_cap

        with patch("azext_prototype.stages.deploy_session.ProjectConfig") as mock_config:
            mock_config.return_value.load.return_value = None
            mock_config.return_value.get.side_effect = lambda k, d=None: {
                "project.iac_tool": "terraform",
            }.get(k, d)
            session = DeploySession(ctx, registry, deploy_state=DeployState(str(tmp_project)))

        printed = []
        stage = {"stage": 1, "name": "Foundation", "services": [{"name": "rg"}]}
        result = {"error": "Deployment failed: access denied"}

        session._handle_deploy_failure(
            stage,
            result,
            False,
            printed.append,
            lambda p: "",
        )

        qa.execute.assert_called_once()
        assert any("QA Diagnosis" in p for p in printed)
        assert any("missing permissions" in p for p in printed)
        assert any("Options:" in p for p in printed)

    def test_handle_deploy_failure_no_qa_shows_error(self, tmp_project):
        from azext_prototype.stages.deploy_session import DeploySession
        from azext_prototype.stages.deploy_state import DeployState

        ctx = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )

        registry = MagicMock()
        registry.find_by_capability.return_value = []

        with patch("azext_prototype.stages.deploy_session.ProjectConfig") as mock_config:
            mock_config.return_value.load.return_value = None
            mock_config.return_value.get.side_effect = lambda k, d=None: {
                "project.iac_tool": "terraform",
            }.get(k, d)
            session = DeploySession(ctx, registry, deploy_state=DeployState(str(tmp_project)))

        printed = []
        stage = {"stage": 1, "name": "Foundation", "services": []}
        result = {"error": "access denied"}

        session._handle_deploy_failure(
            stage,
            result,
            False,
            printed.append,
            lambda p: "",
        )

        assert any("Error:" in p for p in printed)
        assert any("Options:" in p for p in printed)
