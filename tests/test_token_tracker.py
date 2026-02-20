"""Tests for TokenTracker utility and session integrations."""

from unittest.mock import MagicMock, patch

import pytest

from azext_prototype.ai.provider import AIResponse
from azext_prototype.ai.token_tracker import TokenTracker, _CONTEXT_WINDOWS


# -------------------------------------------------------------------- #
# TokenTracker — unit tests
# -------------------------------------------------------------------- #

class TestTokenTrackerBasics:
    """Core record/accumulate behaviour."""

    def test_initial_state(self):
        t = TokenTracker()
        assert t.this_turn == 0
        assert t.session_total == 0
        assert t.turn_count == 0
        assert t.model == ""
        assert t.budget_pct is None

    def test_record_single_turn(self):
        t = TokenTracker()
        resp = AIResponse(
            content="hello", model="gpt-4o",
            usage={"prompt_tokens": 100, "completion_tokens": 50},
        )
        t.record(resp)
        assert t.this_turn == 150
        assert t.session_total == 150
        assert t.session_prompt_total == 100
        assert t.turn_count == 1
        assert t.model == "gpt-4o"

    def test_record_multiple_turns_accumulates(self):
        t = TokenTracker()
        for i in range(3):
            resp = AIResponse(
                content=f"turn {i}", model="gpt-4o",
                usage={"prompt_tokens": 100, "completion_tokens": 50},
            )
            t.record(resp)

        # this_turn reflects only the last
        assert t.this_turn == 150
        # session accumulates all three
        assert t.session_total == 450
        assert t.session_prompt_total == 300
        assert t.turn_count == 3

    def test_record_empty_usage(self):
        t = TokenTracker()
        resp = AIResponse(content="hi", model="gpt-4o", usage={})
        t.record(resp)
        assert t.this_turn == 0
        assert t.session_total == 0
        assert t.turn_count == 1

    def test_record_no_usage_attr(self):
        """Duck-typed: works with objects that have usage=None."""
        t = TokenTracker()
        mock = MagicMock()
        mock.usage = None
        mock.model = "test-model"
        t.record(mock)
        assert t.this_turn == 0
        assert t.model == "test-model"

    def test_record_no_model(self):
        t = TokenTracker()
        resp = AIResponse(content="hi", model="", usage={"prompt_tokens": 10, "completion_tokens": 5})
        t.record(resp)
        assert t.model == ""

    def test_model_updates_on_each_turn(self):
        t = TokenTracker()
        t.record(AIResponse(content="a", model="gpt-4o", usage={}))
        assert t.model == "gpt-4o"
        t.record(AIResponse(content="b", model="gpt-4o-mini", usage={}))
        assert t.model == "gpt-4o-mini"

    def test_model_not_overwritten_with_empty(self):
        t = TokenTracker()
        t.record(AIResponse(content="a", model="gpt-4o", usage={}))
        t.record(AIResponse(content="b", model="", usage={}))
        assert t.model == "gpt-4o"


class TestTokenTrackerBudget:
    """Context-window budget percentage."""

    def test_budget_known_model_exact(self):
        t = TokenTracker()
        t.record(AIResponse(
            content="x", model="gpt-4o",
            usage={"prompt_tokens": 64000, "completion_tokens": 100},
        ))
        pct = t.budget_pct
        assert pct is not None
        assert abs(pct - 50.0) < 0.1  # 64000 / 128000 = 50%

    def test_budget_known_model_substring(self):
        """Model names with date suffixes should still match."""
        t = TokenTracker()
        t.record(AIResponse(
            content="x", model="gpt-4o-2024-05-13",
            usage={"prompt_tokens": 12800, "completion_tokens": 0},
        ))
        pct = t.budget_pct
        assert pct is not None
        assert abs(pct - 10.0) < 0.1

    def test_budget_unknown_model(self):
        t = TokenTracker()
        t.record(AIResponse(
            content="x", model="my-custom-model",
            usage={"prompt_tokens": 500, "completion_tokens": 50},
        ))
        assert t.budget_pct is None

    def test_budget_zero_prompt_tokens(self):
        t = TokenTracker()
        t.record(AIResponse(
            content="x", model="gpt-4o",
            usage={"prompt_tokens": 0, "completion_tokens": 50},
        ))
        assert t.budget_pct is None

    def test_budget_accumulates_across_turns(self):
        t = TokenTracker()
        for _ in range(4):
            t.record(AIResponse(
                content="x", model="gpt-4o",
                usage={"prompt_tokens": 16000, "completion_tokens": 100},
            ))
        pct = t.budget_pct
        assert pct is not None
        assert abs(pct - 50.0) < 0.1  # 64000 / 128000 = 50%


class TestTokenTrackerFormat:
    """format_status() output."""

    def test_format_empty(self):
        t = TokenTracker()
        assert t.format_status() == ""

    def test_format_without_budget(self):
        t = TokenTracker()
        t.record(AIResponse(
            content="x", model="unknown-model",
            usage={"prompt_tokens": 1000, "completion_tokens": 847},
        ))
        status = t.format_status()
        assert "1,847 tokens this turn" in status
        assert "1,847 session" in status
        assert "%" not in status

    def test_format_with_budget(self):
        t = TokenTracker()
        t.record(AIResponse(
            content="x", model="gpt-4o",
            usage={"prompt_tokens": 79360, "completion_tokens": 640},
        ))
        status = t.format_status()
        assert "80,000 tokens this turn" in status
        assert "80,000 session" in status
        assert "~62%" in status  # 79360 / 128000 ≈ 62%

    def test_format_multi_turn(self):
        t = TokenTracker()
        t.record(AIResponse(
            content="a", model="gpt-4o",
            usage={"prompt_tokens": 5000, "completion_tokens": 340},
        ))
        t.record(AIResponse(
            content="b", model="gpt-4o",
            usage={"prompt_tokens": 7000, "completion_tokens": 500},
        ))
        status = t.format_status()
        # this_turn = 7500, session = 12840
        assert "7,500 tokens this turn" in status
        assert "12,840 session" in status

    def test_format_uses_middle_dot(self):
        t = TokenTracker()
        t.record(AIResponse(
            content="x", model="unknown",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        ))
        assert "\u00b7" in t.format_status()


class TestTokenTrackerToDict:
    """Serialisation."""

    def test_to_dict_structure(self):
        t = TokenTracker()
        t.record(AIResponse(
            content="x", model="gpt-4o",
            usage={"prompt_tokens": 100, "completion_tokens": 50},
        ))
        d = t.to_dict()
        assert d["this_turn"]["prompt"] == 100
        assert d["this_turn"]["completion"] == 50
        assert d["session"]["prompt"] == 100
        assert d["session"]["completion"] == 50
        assert d["turn_count"] == 1
        assert d["model"] == "gpt-4o"


class TestContextWindowLookup:
    """_CONTEXT_WINDOWS coverage."""

    def test_all_models_have_positive_windows(self):
        for model, window in _CONTEXT_WINDOWS.items():
            assert window > 0, f"{model} has invalid window {window}"

    def test_gpt4_small_window(self):
        t = TokenTracker()
        t.record(AIResponse(
            content="x", model="gpt-4",
            usage={"prompt_tokens": 4096, "completion_tokens": 0},
        ))
        pct = t.budget_pct
        assert pct is not None
        assert abs(pct - 50.0) < 0.1  # 4096 / 8192 = 50%


# -------------------------------------------------------------------- #
# Console.print_token_status — unit tests
# -------------------------------------------------------------------- #

class TestConsoleTokenStatus:
    """Console.print_token_status renders right-justified muted text."""

    def test_print_token_status_nonempty(self):
        from azext_prototype.ui.console import Console

        c = Console()
        # Capture output via the underlying Rich console
        with patch.object(c._console, "print") as mock_print:
            c.print_token_status("100 tokens this turn")
            mock_print.assert_called_once()
            call_args = mock_print.call_args
            output = call_args[0][0]
            assert "100 tokens this turn" in output
            assert "[muted]" in output

    def test_print_token_status_empty(self):
        from azext_prototype.ui.console import Console

        c = Console()
        with patch.object(c._console, "print") as mock_print:
            c.print_token_status("")
            mock_print.assert_not_called()


# -------------------------------------------------------------------- #
# DiscoveryPrompt — combined status line
# -------------------------------------------------------------------- #

class TestDiscoveryPromptCombinedStatus:
    """Prompt shows open items in the bordered area; token status is shown
    above the border via ``print_token_status()`` — not inside the prompt."""

    def test_open_count_shown_token_status_excluded(self):
        from azext_prototype.ui.console import Console, DiscoveryPrompt

        c = Console()
        prompt = DiscoveryPrompt(c)

        with patch.object(prompt._session, "prompt", return_value="test"), \
             patch.object(c._console, "print") as mock_print:
            prompt.prompt(
                "> ",
                open_count=3,
                status_text="150 tokens this turn \u00b7 150 session",
            )
            calls = [str(call) for call in mock_print.call_args_list]
            # Open items should appear in the prompt area
            open_calls = [c for c in calls if "Open items: 3" in c]
            assert len(open_calls) >= 1
            # Token status should NOT appear inside the prompt area
            token_calls = [c for c in calls if "tokens" in c]
            assert len(token_calls) == 0, f"Token status should not be in prompt: {calls}"

    def test_open_count_only(self):
        from azext_prototype.ui.console import Console, DiscoveryPrompt

        c = Console()
        prompt = DiscoveryPrompt(c)

        with patch.object(prompt._session, "prompt", return_value="test"), \
             patch.object(c._console, "print") as mock_print:
            prompt.prompt("> ", open_count=3, status_text="")
            calls = [str(call) for call in mock_print.call_args_list]
            open_calls = [c for c in calls if "Open items: 3" in c]
            assert len(open_calls) >= 1

    def test_no_status_when_zero_open_and_no_text(self):
        from azext_prototype.ui.console import Console, DiscoveryPrompt

        c = Console()
        prompt = DiscoveryPrompt(c)

        with patch.object(prompt._session, "prompt", return_value="test"), \
             patch.object(c._console, "print") as mock_print:
            prompt.prompt("> ", open_count=0, status_text="")
            calls = [str(call) for call in mock_print.call_args_list]
            status_calls = [c for c in calls if "Open items" in c]
            assert len(status_calls) == 0


# -------------------------------------------------------------------- #
# DiscoverySession — token tracking integration
# -------------------------------------------------------------------- #

class TestDiscoverySessionTokenTracking:
    """DiscoverySession records token usage and displays status."""

    def _make_session(self, tmp_path, ai_content="Mock response"):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.discovery import DiscoverySession

        mock_agent = MagicMock()
        mock_agent.name = "biz-analyst"
        mock_agent._temperature = 0.7
        mock_agent._max_tokens = 8192
        mock_agent.get_system_messages.return_value = []

        mock_provider = MagicMock()
        mock_provider.chat.return_value = AIResponse(
            content=ai_content, model="gpt-4o",
            usage={"prompt_tokens": 500, "completion_tokens": 200, "total_tokens": 700},
        )

        context = AgentContext(
            project_config={},
            project_dir=str(tmp_path),
            ai_provider=mock_provider,
        )

        registry = MagicMock(spec=AgentRegistry)
        from azext_prototype.agents.base import AgentCapability
        def find_by(cap):
            if cap == AgentCapability.BIZ_ANALYSIS:
                return [mock_agent]
            return []
        registry.find_by_capability.side_effect = find_by

        session = DiscoverySession(context, registry)
        return session, mock_provider

    def test_token_tracker_exists(self, tmp_path):
        session, _ = self._make_session(tmp_path)
        assert hasattr(session, "_token_tracker")
        assert isinstance(session._token_tracker, TokenTracker)

    def test_chat_records_usage(self, tmp_path):
        session, _ = self._make_session(tmp_path)
        outputs = []
        result = session.run(
            seed_context="Build a web app",
            input_fn=lambda p: "done",
            print_fn=lambda m: outputs.append(m),
        )
        # Opening chat + summary chat = 2 turns
        assert session._token_tracker.turn_count >= 1
        assert session._token_tracker.session_total > 0

    def test_token_status_displayed_styled(self, tmp_path):
        """In styled mode, print_token_status is called after AI responses."""
        session, _ = self._make_session(tmp_path)

        with patch.object(session._console, "print_token_status") as mock_status, \
             patch.object(session._console, "print_agent_response"), \
             patch.object(session._console, "print"), \
             patch.object(session._console, "print_info"), \
             patch.object(session._prompt, "prompt", return_value="done"), \
             patch.object(session._console, "spinner", return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock())):
            session.run(seed_context="Build a web app")
            assert mock_status.call_count >= 1

    def test_token_status_not_displayed_non_styled(self, tmp_path):
        """In non-styled mode (test I/O), print_token_status is not called."""
        session, _ = self._make_session(tmp_path)

        with patch.object(session._console, "print_token_status") as mock_status:
            session.run(
                seed_context="Build a web app",
                input_fn=lambda p: "done",
                print_fn=lambda m: None,
            )
            mock_status.assert_not_called()


# -------------------------------------------------------------------- #
# BuildSession — token tracking integration
# -------------------------------------------------------------------- #

class TestBuildSessionTokenTracking:
    """BuildSession records token usage across agent.execute() calls."""

    def _make_session(self, tmp_path):
        from azext_prototype.agents.base import AgentCapability, AgentContext
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.build_session import BuildSession

        # Mock agents
        mock_architect = MagicMock()
        mock_architect.name = "cloud-architect"
        mock_architect.execute.return_value = AIResponse(
            content='{"stages": [{"stage": 1, "name": "Foundation", "category": "infra", "dir": "concept/infra/terraform/stage-1", "services": [], "status": "pending", "files": []}]}',
            model="gpt-4o",
            usage={"prompt_tokens": 1000, "completion_tokens": 500},
        )

        mock_iac = MagicMock()
        mock_iac.name = "terraform-agent"
        mock_iac.execute.return_value = AIResponse(
            content="# main.tf\nresource \"azurerm_resource_group\" \"rg\" {}",
            model="gpt-4o",
            usage={"prompt_tokens": 800, "completion_tokens": 300},
        )

        mock_provider = MagicMock()
        context = AgentContext(
            project_config={"project": {"name": "test", "iac_tool": "terraform"}},
            project_dir=str(tmp_path),
            ai_provider=mock_provider,
        )

        # Write minimal config
        import yaml
        config_path = tmp_path / "prototype.yaml"
        config_path.write_text(yaml.dump({
            "project": {"name": "test", "iac_tool": "terraform", "location": "eastus", "environment": "dev"},
            "naming": {"strategy": "simple"},
            "ai": {"provider": "github-models"},
        }))

        registry = MagicMock(spec=AgentRegistry)
        def find_by(cap):
            if cap == AgentCapability.ARCHITECT:
                return [mock_architect]
            if cap == AgentCapability.TERRAFORM:
                return [mock_iac]
            return []
        registry.find_by_capability.side_effect = find_by

        session = BuildSession(context, registry)
        return session

    def test_token_tracker_exists(self, tmp_path):
        session = self._make_session(tmp_path)
        assert hasattr(session, "_token_tracker")
        assert isinstance(session._token_tracker, TokenTracker)

    def test_tracks_deployment_plan_derivation(self, tmp_path):
        session = self._make_session(tmp_path)
        outputs = []
        result = session.run(
            design={"architecture": "Build a web app with App Service"},
            input_fn=lambda p: "done",
            print_fn=lambda m: outputs.append(m),
        )
        # At minimum, architect was called for deployment plan
        assert session._token_tracker.turn_count >= 1
        assert session._token_tracker.session_total > 0


# -------------------------------------------------------------------- #
# DeploySession — token tracking integration
# -------------------------------------------------------------------- #

class TestDeploySessionTokenTracking:
    """DeploySession has a token tracker."""

    def test_token_tracker_exists(self, tmp_path):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        context = AgentContext(
            project_config={},
            project_dir=str(tmp_path),
            ai_provider=MagicMock(),
        )
        import yaml
        (tmp_path / "prototype.yaml").write_text(yaml.dump({
            "project": {"name": "test", "iac_tool": "terraform", "location": "eastus"},
            "naming": {"strategy": "simple"},
            "ai": {"provider": "github-models"},
        }))

        registry = MagicMock(spec=AgentRegistry)
        registry.find_by_capability.return_value = []

        session = DeploySession(context, registry)
        assert hasattr(session, "_token_tracker")
        assert isinstance(session._token_tracker, TokenTracker)


# -------------------------------------------------------------------- #
# BacklogSession — token tracking integration
# -------------------------------------------------------------------- #

class TestBacklogSessionTokenTracking:
    """BacklogSession records token usage from AI calls."""

    def _make_session(self, tmp_path, items_response="[]"):
        from azext_prototype.agents.base import AgentCapability, AgentContext
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.backlog_session import BacklogSession

        mock_agent = MagicMock()
        mock_agent.name = "project-manager"
        mock_agent.get_system_messages.return_value = []

        mock_provider = MagicMock()
        mock_provider.chat.return_value = AIResponse(
            content=items_response, model="gpt-4o",
            usage={"prompt_tokens": 2000, "completion_tokens": 1000, "total_tokens": 3000},
        )

        context = AgentContext(
            project_config={},
            project_dir=str(tmp_path),
            ai_provider=mock_provider,
        )

        registry = MagicMock(spec=AgentRegistry)
        def find_by(cap):
            if cap == AgentCapability.BACKLOG_GENERATION:
                return [mock_agent]
            return []
        registry.find_by_capability.side_effect = find_by

        session = BacklogSession(context, registry)
        # Override mock AFTER session creation (conftest pattern)
        mock_provider.chat.return_value = AIResponse(
            content=items_response, model="gpt-4o",
            usage={"prompt_tokens": 2000, "completion_tokens": 1000, "total_tokens": 3000},
        )
        return session, mock_provider

    def test_token_tracker_exists(self, tmp_path):
        session, _ = self._make_session(tmp_path)
        assert hasattr(session, "_token_tracker")
        assert isinstance(session._token_tracker, TokenTracker)

    def test_tracks_generation(self, tmp_path):
        items = '[{"epic": "Core", "title": "Test", "description": "desc", "acceptance_criteria": [], "tasks": [], "effort": "S"}]'
        session, provider = self._make_session(tmp_path, items_response=items)
        outputs = []
        result = session.run(
            design_context="Build a web app",
            input_fn=lambda p: "done",
            print_fn=lambda m: outputs.append(m),
        )
        assert session._token_tracker.turn_count >= 1
        assert session._token_tracker.session_total == 3000
