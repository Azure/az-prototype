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
