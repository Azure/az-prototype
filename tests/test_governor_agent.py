"""Tests for GovernorAgent — brief(), review(), execute() methods."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from azext_prototype.agents.base import AgentCapability, AgentContext
from azext_prototype.agents.builtin.governor_agent import GovernorAgent
from azext_prototype.ai.provider import AIResponse

# ======================================================================
# Helpers
# ======================================================================


def _make_context(project_dir: str, ai_provider=None) -> AgentContext:
    """Create a minimal AgentContext for governor tests."""
    return AgentContext(
        project_config={"project": {"name": "test"}, "ai": {"provider": "github-models"}},
        project_dir=project_dir,
        ai_provider=ai_provider,
    )


# ======================================================================
# GovernorAgent construction
# ======================================================================


class TestGovernorAgentInit:

    def test_agent_name_and_capabilities(self):
        agent = GovernorAgent()
        assert agent.name == "governor"
        assert AgentCapability.GOVERNANCE in agent.capabilities

    def test_agent_not_governance_aware(self):
        """Governor should not recurse into itself."""
        agent = GovernorAgent()
        assert agent._governance_aware is False

    def test_agent_does_not_include_templates_or_standards(self):
        agent = GovernorAgent()
        assert agent._include_templates is False
        assert agent._include_standards is False

    def test_contract_declares_inputs_and_outputs(self):
        agent = GovernorAgent()
        contract = agent.get_contract()
        assert "task_description" in contract.inputs
        assert "generated_output" in contract.inputs
        assert "policy_brief" in contract.outputs
        assert "policy_violations" in contract.outputs

    def test_can_handle_governance_keywords(self):
        agent = GovernorAgent()
        score = agent.can_handle("review governance policy violations")
        assert score > 0.0

    def test_system_prompt_set(self):
        agent = GovernorAgent()
        assert "governance reviewer" in agent.system_prompt.lower()


# ======================================================================
# brief() tests
# ======================================================================


class TestGovernorBrief:

    @patch("azext_prototype.governance.governor.brief")
    def test_brief_returns_string_with_policy_rules(self, mock_brief, tmp_path):
        mock_brief.return_value = "## Policy Brief\n- RULE-001: Use managed identity\n- RULE-002: Encrypt at rest"

        agent = GovernorAgent()
        ctx = _make_context(str(tmp_path))
        result = agent.brief(ctx, "Generate terraform for key-vault", agent_name="terraform-agent")

        assert isinstance(result, str)
        assert "RULE-001" in result
        mock_brief.assert_called_once_with(
            project_dir=str(tmp_path),
            task_description="Generate terraform for key-vault",
            agent_name="terraform-agent",
            top_k=10,
        )

    @patch("azext_prototype.governance.governor.brief")
    def test_brief_with_empty_project_dir(self, mock_brief, tmp_path):
        mock_brief.return_value = ""

        agent = GovernorAgent()
        ctx = _make_context("")
        result = agent.brief(ctx, "some task")

        assert result == ""
        mock_brief.assert_called_once_with(
            project_dir="",
            task_description="some task",
            agent_name="",
            top_k=10,
        )

    @patch("azext_prototype.governance.governor.brief")
    def test_brief_custom_top_k(self, mock_brief, tmp_path):
        mock_brief.return_value = "rules"

        agent = GovernorAgent()
        ctx = _make_context(str(tmp_path))
        result = agent.brief(ctx, "task", top_k=5)

        assert result == "rules"
        mock_brief.assert_called_once_with(
            project_dir=str(tmp_path),
            task_description="task",
            agent_name="",
            top_k=5,
        )

    @patch("azext_prototype.governance.governor.brief")
    def test_brief_passes_agent_name(self, mock_brief, tmp_path):
        mock_brief.return_value = "brief text"

        agent = GovernorAgent()
        ctx = _make_context(str(tmp_path))
        agent.brief(ctx, "task desc", agent_name="bicep-agent")

        mock_brief.assert_called_once_with(
            project_dir=str(tmp_path),
            task_description="task desc",
            agent_name="bicep-agent",
            top_k=10,
        )


# ======================================================================
# review() tests
# ======================================================================


class TestGovernorReview:

    def test_review_no_ai_provider_returns_empty_list(self, tmp_path):
        agent = GovernorAgent()
        ctx = _make_context(str(tmp_path), ai_provider=None)

        result = agent.review(ctx, "some generated code")

        assert result == []

    @patch("azext_prototype.governance.governor.review")
    def test_review_with_mock_ai_provider(self, mock_review, tmp_path):
        mock_review.return_value = ["[RULE-001] Missing managed identity", "[RULE-002] No encryption at rest"]

        provider = MagicMock()
        provider.provider_name = "github-models"
        agent = GovernorAgent()
        ctx = _make_context(str(tmp_path), ai_provider=provider)

        result = agent.review(ctx, "code with access_key = ...", max_workers=3)

        assert len(result) == 2
        assert "RULE-001" in result[0]
        assert "RULE-002" in result[1]
        mock_review.assert_called_once_with(
            project_dir=str(tmp_path),
            output_text="code with access_key = ...",
            ai_provider=provider,
            max_workers=3,
        )

    @patch("azext_prototype.governance.governor.review")
    def test_review_no_violations(self, mock_review, tmp_path):
        mock_review.return_value = []

        provider = MagicMock()
        agent = GovernorAgent()
        ctx = _make_context(str(tmp_path), ai_provider=provider)

        result = agent.review(ctx, "clean code")

        assert result == []

    @patch("azext_prototype.governance.governor.review")
    def test_review_default_max_workers(self, mock_review, tmp_path):
        mock_review.return_value = []

        provider = MagicMock()
        agent = GovernorAgent()
        ctx = _make_context(str(tmp_path), ai_provider=provider)

        agent.review(ctx, "code")

        mock_review.assert_called_once_with(
            project_dir=str(tmp_path),
            output_text="code",
            ai_provider=provider,
            max_workers=2,
        )


# ======================================================================
# execute() tests
# ======================================================================


class TestGovernorExecute:

    @patch("azext_prototype.governance.governor.review")
    def test_execute_returns_violations(self, mock_review, tmp_path):
        mock_review.return_value = [
            "[SEC-001] Connection string detected",
            "[SEC-002] No resource lock",
        ]

        provider = MagicMock()
        agent = GovernorAgent()
        ctx = _make_context(str(tmp_path), ai_provider=provider)

        result = agent.execute(ctx, "resource code with connection_string")

        assert isinstance(result, AIResponse)
        assert "Governance Violations Found" in result.content
        assert "[SEC-001]" in result.content
        assert "[SEC-002]" in result.content
        assert result.model == "governor"

    @patch("azext_prototype.governance.governor.review")
    def test_execute_no_violations(self, mock_review, tmp_path):
        mock_review.return_value = []

        provider = MagicMock()
        agent = GovernorAgent()
        ctx = _make_context(str(tmp_path), ai_provider=provider)

        result = agent.execute(ctx, "clean terraform code")

        assert isinstance(result, AIResponse)
        assert "No governance violations found" in result.content
        assert result.model == "governor"

    def test_execute_no_ai_provider_returns_clean(self, tmp_path):
        """With no AI provider, review returns [] so execute reports no violations."""
        agent = GovernorAgent()
        ctx = _make_context(str(tmp_path), ai_provider=None)

        result = agent.execute(ctx, "some code")

        assert isinstance(result, AIResponse)
        assert "No governance violations found" in result.content

    @patch("azext_prototype.governance.governor.review")
    def test_execute_single_violation(self, mock_review, tmp_path):
        mock_review.return_value = ["[NET-001] Public endpoint exposed"]

        provider = MagicMock()
        agent = GovernorAgent()
        ctx = _make_context(str(tmp_path), ai_provider=provider)

        result = agent.execute(ctx, "networking code")

        assert "Governance Violations Found" in result.content
        assert "[NET-001]" in result.content
        assert result.usage == {}
