"""Tests for azext_prototype.stages.discovery — organic multi-turn conversation."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from azext_prototype.agents.base import AgentCapability, AgentContext
from azext_prototype.ai.provider import AIMessage, AIResponse
from azext_prototype.stages.discovery import (
    DiscoverySession,
    DiscoveryResult,
    Section,
    extract_section_headers,
    parse_sections,
    _READY_MARKER,
    _QUIT_WORDS,
    _DONE_WORDS,
)


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture
def mock_biz_agent():
    agent = MagicMock()
    agent.name = "biz-analyst"
    agent.capabilities = [AgentCapability.BIZ_ANALYSIS, AgentCapability.ANALYZE]
    agent._temperature = 0.5
    agent._max_tokens = 8192
    agent.get_system_messages.side_effect = lambda: [
        AIMessage(role="system", content="You are a biz-analyst."),
    ]
    return agent


@pytest.fixture
def mock_architect_agent():
    agent = MagicMock()
    agent.name = "cloud-architect"
    agent.capabilities = [AgentCapability.ARCHITECT, AgentCapability.COORDINATE]
    agent.constraints = [
        "All Azure services MUST use Managed Identity",
        "Follow Microsoft Well-Architected Framework principles",
        "This is a PROTOTYPE — optimize for speed and demonstration",
        "Prefer PaaS over IaaS for simplicity",
    ]
    return agent


@pytest.fixture
def mock_registry(mock_biz_agent, mock_architect_agent):
    registry = MagicMock()

    def find_by_cap(cap):
        if cap == AgentCapability.BIZ_ANALYSIS:
            return [mock_biz_agent]
        if cap == AgentCapability.ARCHITECT:
            return [mock_architect_agent]
        return []

    registry.find_by_capability.side_effect = find_by_cap
    return registry


@pytest.fixture
def mock_agent_context(tmp_path):
    ctx = AgentContext(
        project_config={"project": {"name": "test", "location": "eastus"}},
        project_dir=str(tmp_path),
        ai_provider=MagicMock(),
    )
    return ctx


def _make_response(content: str) -> AIResponse:
    """Shorthand for creating an AIResponse."""
    return AIResponse(content=content, model="gpt-4o", usage={})


# ======================================================================
# DiscoveryResult
# ======================================================================

class TestDiscoveryResult:
    def test_basic_creation(self):
        result = DiscoveryResult(
            requirements="Build a web app",
            conversation=[],
            policy_overrides=[],
            exchange_count=3,
        )
        assert result.requirements == "Build a web app"
        assert result.exchange_count == 3
        assert result.cancelled is False

    def test_cancelled(self):
        result = DiscoveryResult(
            requirements="",
            conversation=[],
            policy_overrides=[],
            exchange_count=0,
            cancelled=True,
        )
        assert result.cancelled is True


# ======================================================================
# DiscoverySession — basic conversation flow
# ======================================================================

class TestBasicConversationFlow:
    """The core contract: user and agent exchange messages naturally."""

    def test_bare_invocation_agent_speaks_first(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """With no context, the agent gets a generic opening and starts talking."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("Tell me about what you'd like to build."),
            _make_response("Interesting — a REST API for orders. What database?"),
            _make_response("## Summary\nOrders API, PostgreSQL."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["A REST API for order management", "done"])

        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        # exchange_count includes the opening exchange (1) + user reply (2)
        assert result.exchange_count == 2
        assert not result.cancelled
        # The AI was called: opening + user reply + summary
        assert mock_agent_context.ai_provider.chat.call_count == 3

    def test_with_context_agent_analyzes_and_follows_up(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """When --context is provided, it becomes the opening message."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("I see an inventory system. What about auth?"),
            _make_response("Entra ID, got it. What about scale?"),
            _make_response("50 users, read-heavy. Makes sense."),
            _make_response("## Summary\nInventory system confirmed."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["Entra ID for auth", "About 50 users", "done"])

        result = session.run(
            seed_context="Build an inventory management system",
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        # exchange_count: opening (1) + 2 user replies (2, 3)
        assert result.exchange_count == 3
        assert not result.cancelled
        # Check that the opening message was the seed context
        first_call_messages = mock_agent_context.ai_provider.chat.call_args_list[0][0][0]
        user_msgs = [m for m in first_call_messages if m.role == "user"]
        assert "inventory management" in user_msgs[0].content.lower()

    def test_with_artifacts_and_context(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """Both artifacts AND context form a combined opening message."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("I see both context and specs. Scale?"),
            _make_response("50 users, noted. Anything else?"),
            _make_response("## Summary\nAll confirmed."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["50 concurrent users", "done"])

        result = session.run(
            seed_context="Inventory system",
            artifacts="## Spec\nCRUD for products",
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        # exchange_count: opening (1) + user reply (2)
        assert result.exchange_count == 2
        first_call_messages = mock_agent_context.ai_provider.chat.call_args_list[0][0][0]
        user_msgs = [m for m in first_call_messages if m.role == "user"]
        assert "inventory" in user_msgs[0].content.lower()
        assert "CRUD" in user_msgs[0].content or "requirement documents" in user_msgs[0].content.lower()

    def test_with_only_artifacts(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """Artifacts alone — opening says 'I have documents for you'."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("Let me review... looks like a product catalog."),
            _make_response("## Summary\nProduct catalog."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        session.run(
            artifacts="## Product Catalog Spec\nCRUD endpoints",
            input_fn=lambda _: "done",
            print_fn=lambda x: None,
        )

        first_user_msg = [
            m for m in mock_agent_context.ai_provider.chat.call_args_list[0][0][0]
            if m.role == "user"
        ][0]
        assert "requirement documents" in first_user_msg.content.lower()


# ======================================================================
# Multi-turn message history
# ======================================================================

class TestMultiTurnHistory:
    """The key architectural requirement: full conversation history on every call."""

    def test_history_grows_with_each_exchange(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """Each AI call includes the full conversation history."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What are you building?"),
            _make_response("A REST API. What database?"),
            _make_response("PostgreSQL. Auth?"),
            _make_response("## Summary"),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["A REST API", "PostgreSQL", "done"])

        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        calls = mock_agent_context.ai_provider.chat.call_args_list

        # Call 0 (opening): system + 1 user message
        # Call 1 (exchange 1): system + 2 user + 1 assistant
        # Call 2 (exchange 2): system + 3 user + 2 assistant
        # Call 3 (summary):    system + 4 user + 3 assistant

        user_count_per_call = []
        for c in calls:
            messages = c[0][0]
            user_count_per_call.append(
                sum(1 for m in messages if m.role == "user")
            )

        # History should grow monotonically
        assert user_count_per_call == sorted(user_count_per_call)
        assert user_count_per_call[-1] > user_count_per_call[0]

    def test_no_meta_prompt_injection(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """User text goes to the AI unmodified — no wrapping or injection."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What are you building?"),
            _make_response("Got it."),
            _make_response("## Summary"),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["Build me a web app with React and Node.js", "done"])

        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        # The second call should contain the user's exact text
        second_call_messages = mock_agent_context.ai_provider.chat.call_args_list[1][0][0]
        user_msgs = [m.content for m in second_call_messages if m.role == "user"]
        # The user's message should appear verbatim
        assert "Build me a web app with React and Node.js" in user_msgs


# ======================================================================
# Session ending
# ======================================================================

class TestSessionEnding:
    def test_quit_cancels(self, mock_agent_context, mock_registry, mock_biz_agent):
        mock_agent_context.ai_provider.chat.return_value = _make_response("Hi!")
        session = DiscoverySession(mock_agent_context, mock_registry)

        result = session.run(
            input_fn=lambda _: "q",
            print_fn=lambda x: None,
        )
        assert result.cancelled is True
        assert result.requirements == ""

    def test_all_quit_words(self, mock_agent_context, mock_registry, mock_biz_agent):
        for word in _QUIT_WORDS:
            mock_agent_context.ai_provider.chat.return_value = _make_response("Hi!")
            session = DiscoverySession(mock_agent_context, mock_registry)
            result = session.run(
                input_fn=lambda _: word,
                print_fn=lambda x: None,
            )
            assert result.cancelled, f"'{word}' should cancel"

    def test_all_done_words(self, mock_agent_context, mock_registry, mock_biz_agent):
        for word in _DONE_WORDS:
            mock_agent_context.ai_provider.chat.side_effect = [
                _make_response("Hi!"),
                _make_response("## Summary"),
            ]
            session = DiscoverySession(mock_agent_context, mock_registry)
            result = session.run(
                input_fn=lambda _: word,
                print_fn=lambda x: None,
            )
            assert not result.cancelled, f"'{word}' should end gracefully, not cancel"

    def test_end_in_done_words(self):
        """'end' should be recognized as a done word."""
        assert "end" in _DONE_WORDS

    def test_end_word_finishes_session(self, mock_agent_context, mock_registry, mock_biz_agent):
        """Typing 'end' should complete the session (not cancel)."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("Hi! Tell me about your project."),
            _make_response("## Summary\nHere's what we discussed."),
        ]
        session = DiscoverySession(mock_agent_context, mock_registry)
        result = session.run(
            input_fn=lambda _: "end",
            print_fn=lambda x: None,
        )
        assert not result.cancelled
        assert result.exchange_count >= 1

    def test_eof_exits_gracefully(self, mock_agent_context, mock_registry, mock_biz_agent):
        mock_agent_context.ai_provider.chat.return_value = _make_response("Hi!")
        session = DiscoverySession(mock_agent_context, mock_registry)

        result = session.run(
            input_fn=lambda _: (_ for _ in ()).throw(EOFError),
            print_fn=lambda x: None,
        )
        assert result is not None

    def test_keyboard_interrupt_exits(self, mock_agent_context, mock_registry, mock_biz_agent):
        mock_agent_context.ai_provider.chat.return_value = _make_response("Hi!")
        session = DiscoverySession(mock_agent_context, mock_registry)

        result = session.run(
            input_fn=lambda _: (_ for _ in ()).throw(KeyboardInterrupt),
            print_fn=lambda x: None,
        )
        assert result is not None

    def test_empty_input_ignored(self, mock_agent_context, mock_registry, mock_biz_agent):
        """Blank lines don't count as exchanges."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What do you want to build?"),
            _make_response("A web app. Got it."),
            _make_response("## Summary"),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["", "", "Build a web app", "", "done"])

        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )
        # exchange_count: opening (1) + one real user reply (2)
        assert result.exchange_count == 2


# ======================================================================
# Agent-driven convergence via [READY] marker
# ======================================================================

class TestConvergence:
    def test_ready_marker_triggers_confirmation(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """When agent includes [READY], user is prompted to confirm."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What are you building?"),
            _make_response(f"I have a good picture now. Here's what I've got. {_READY_MARKER}"),
            _make_response("## Summary\nAll confirmed."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter([
            "A simple REST API for orders",
            "",  # Enter to accept after [READY]
        ])

        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        # exchange_count: opening (1) + user reply (2)
        assert result.exchange_count == 2
        assert not result.cancelled

    def test_ready_marker_stripped_from_display(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """The [READY] marker is never shown to the user."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What are you building?"),
            _make_response(f"I think we're done. {_READY_MARKER}"),
            _make_response("## Summary"),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        printed = []
        inputs = iter(["A web app", ""])  # exchange, then Enter to accept

        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: printed.append(x),
        )

        all_output = "\n".join(printed)
        assert _READY_MARKER not in all_output

    def test_user_can_continue_after_ready(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """User can keep typing after agent signals [READY]."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What are you building?"),
            _make_response(f"Looks complete. {_READY_MARKER}"),
            _make_response("Redis added. Anything else?"),
            _make_response("## Summary"),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter([
            "A web app",
            "Actually, also add Redis caching",  # continues after READY
            "done",
        ])

        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        # exchange_count: opening (1) + user reply (2) + continue after READY (3)
        assert result.exchange_count == 3


# ======================================================================
# No biz-analyst fallback
# ======================================================================

class TestNoBizAnalystFallback:
    def test_falls_back_to_input(self, mock_agent_context):
        registry = MagicMock()
        registry.find_by_capability.return_value = []

        session = DiscoverySession(mock_agent_context, registry)
        result = session.run(
            input_fn=lambda _: "Build a web API",
            print_fn=lambda x: None,
        )

        assert result.requirements == "Build a web API"
        assert result.exchange_count == 0


# ======================================================================
# Summary production
# ======================================================================

class TestSummaryProduction:
    def test_summary_requested_at_end(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """After conversation, a summary call is made."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What are you building?"),
            _make_response("An orders API. Makes sense."),
            _make_response("## Confirmed Requirements\n- Orders REST API\n- PostgreSQL"),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["An orders REST API with PostgreSQL", "done"])

        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        assert "orders" in result.requirements.lower() or "Orders" in result.requirements

    def test_no_summary_when_zero_exchanges(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """If user immediately types 'done', a summary is still produced
        because the opening exchange counts."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What would you like to build?"),
            _make_response("## Summary\nA web app"),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        result = session.run(
            seed_context="A web app",
            input_fn=lambda _: "done",
            print_fn=lambda x: None,
        )

        assert "web app" in result.requirements.lower()
        # 2 chat calls: opening + summary
        assert mock_agent_context.ai_provider.chat.call_count == 2


# ======================================================================
# Policy override extraction from summary
# ======================================================================

class TestPolicyOverrideExtraction:
    def test_extracts_overrides_from_summary(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """If the summary contains a 'Policy Overrides' section, parse it."""
        summary_text = (
            "## Confirmed Requirements\n"
            "- Orders API\n\n"
            "## Policy Overrides\n"
            "- managed-identity: User requires connection strings for legacy compat\n"
            "- network-isolation: Public endpoint needed for demo\n\n"
            "## Open Items\n"
            "- Timeline TBD"
        )

        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What are you building?"),
            _make_response("Got it."),
            _make_response(summary_text),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["An orders API with connection strings", "done"])

        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        assert len(result.policy_overrides) == 2
        names = [o["policy_name"] for o in result.policy_overrides]
        assert "managed-identity" in names
        assert "network-isolation" in names

    def test_no_overrides_when_section_absent(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """No Policy Overrides heading → empty list."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What are you building?"),
            _make_response("Got it."),
            _make_response("## Summary\n- Just an API\n## Open Items\n- None"),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["A web API", "done"])

        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        assert result.policy_overrides == []


# ======================================================================
# Integration with DesignStage
# ======================================================================

class TestDesignStageDiscoveryIntegration:
    """Test that DesignStage.execute() uses the DiscoverySession."""

    def test_design_stage_uses_discovery(
        self, project_with_config, mock_agent_context, populated_registry,
    ):
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()
        stage.get_guards = lambda: []

        mock_agent_context.project_dir = str(project_with_config)
        mock_agent_context.ai_provider.chat.return_value = _make_response(
            "Tell me more about your project."
        )

        inputs = iter(["Build a REST API", "PostgreSQL, 50 users", "done"])
        result = stage.execute(
            mock_agent_context,
            populated_registry,
            context="Build a simple web app",
            interactive=False,
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )
        assert result["status"] == "success"

    def test_cancelled_discovery_cancels_design(
        self, project_with_config, mock_agent_context, populated_registry,
    ):
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()
        stage.get_guards = lambda: []

        mock_agent_context.project_dir = str(project_with_config)
        mock_agent_context.ai_provider.chat.return_value = _make_response(
            "Tell me about your project."
        )

        result = stage.execute(
            mock_agent_context,
            populated_registry,
            interactive=False,
            input_fn=lambda _: "quit",
            print_fn=lambda x: None,
        )
        assert result["status"] == "cancelled"

    def test_design_stage_persists_policy_overrides(
        self, project_with_config, mock_agent_context, populated_registry,
    ):
        """Policy overrides from discovery are persisted in design state."""
        import json as _json
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()
        stage.get_guards = lambda: []

        mock_agent_context.project_dir = str(project_with_config)
        mock_agent_context.ai_provider.chat.return_value = _make_response(
            "Architecture design with overrides."
        )

        mock_result = DiscoveryResult(
            requirements="Build an API with connection strings (overridden)",
            conversation=[],
            policy_overrides=[{
                "rule_id": "managed-identity",
                "policy_name": "managed-identity",
                "description": "Legacy compat",
                "recommendation": "",
                "user_text": "Legacy compat",
            }],
            exchange_count=3,
        )

        with patch(
            "azext_prototype.stages.design_stage.DiscoverySession"
        ) as MockDS:
            MockDS.return_value.run.return_value = mock_result

            result = stage.execute(
                mock_agent_context,
                populated_registry,
                context="Build a web app",
                interactive=False,
            )

        assert result["status"] == "success"
        state_path = project_with_config / ".prototype" / "state" / "design.json"
        state = _json.loads(state_path.read_text(encoding="utf-8"))
        assert len(state.get("policy_overrides", [])) == 1
        assert state["policy_overrides"][0]["rule_id"] == "managed-identity"


# ======================================================================
# _clean helper
# ======================================================================

class TestCleanHelper:
    def test_strips_ready_marker(self):
        assert DiscoverySession._clean(f"Hello {_READY_MARKER}") == "Hello"

    def test_no_marker_passthrough(self):
        assert DiscoverySession._clean("Hello world") == "Hello world"


# ======================================================================
# _extract_overrides helper
# ======================================================================

class TestExtractOverrides:
    def test_parses_bullet_list(self):
        text = (
            "## Policy Overrides\n"
            "- managed-identity: Legacy system needs connection strings\n"
            "- network-isolation: Demo requires public access\n"
            "\n## Next Steps\n"
        )
        overrides = DiscoverySession._extract_overrides(text)
        assert len(overrides) == 2
        assert overrides[0]["policy_name"] == "managed-identity"
        assert "Legacy" in overrides[0]["description"]

    def test_empty_when_no_section(self):
        assert DiscoverySession._extract_overrides("## Summary\nJust a summary.") == []

    def test_handles_bold_names(self):
        text = (
            "## Policy Overrides\n"
            "- **MI-001**: User needs connection strings\n"
        )
        overrides = DiscoverySession._extract_overrides(text)
        assert len(overrides) == 1
        assert overrides[0]["policy_name"] == "MI-001"


# ======================================================================
# /summary slash command
# ======================================================================

class TestSummaryCommand:
    def test_summary_triggers_ai_call(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """/summary should call the AI for a mid-session summary."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What would you like to build?"),
            _make_response("Here's a summary of what we have so far."),
            _make_response("## Summary\nFinal summary."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["/summary", "done"])

        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        # 3 AI calls: opening, /summary, final summary
        assert mock_agent_context.ai_provider.chat.call_count == 3
        # /summary doesn't count as a user exchange — only the opening does
        assert result.exchange_count == 1

    def test_summary_does_not_increment_exchange_count(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """/summary is a meta-command — exchange count stays the same."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("Tell me about your project."),
            _make_response("Got it — an API."),
            _make_response("Mid-session summary: API project."),
            _make_response("## Summary\nAPI confirmed."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["I want an API", "/summary", "done"])

        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        # Opening (1) + one real user exchange (2), /summary doesn't count
        assert result.exchange_count == 2


# ======================================================================
# /restart slash command
# ======================================================================

class TestRestartCommand:
    def test_restart_clears_state_and_resets(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """/restart should reset state and re-send the opening."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What would you like to build?"),
            _make_response("Got it — a web app."),
            _make_response("Fresh start! What would you like to build?"),
            _make_response("## Summary\nFresh summary."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["A web app", "/restart", "done"])

        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        # After /restart, exchange_count resets to 1 (the new opening)
        assert result.exchange_count == 1
        # Messages were cleared and rebuilt
        assert len(session._messages) > 0

    def test_restart_clears_conversation_history(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """/restart should clear the in-memory message list."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("Tell me more."),
            _make_response("OK — a database."),
            _make_response("Starting fresh!"),
            _make_response("## Summary\nEmpty."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["Need a database", "/restart", "done"])

        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        # After restart + done, messages should only contain the
        # post-restart opening exchange + the summary exchange
        # (pre-restart messages were cleared)
        user_msgs = [m for m in session._messages if m.role == "user"]
        assert not any("database" in m.content.lower() for m in user_msgs)


# ======================================================================
# /why slash command
# ======================================================================

class TestWhyCommand:
    def test_why_no_argument_shows_usage(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """/why with no argument should show usage hint, not crash."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What would you like to build?"),
            _make_response("## Summary\nNothing yet."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["/why", "done"])
        output = []

        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=output.append,
        )

        combined = "\n".join(str(x) for x in output)
        assert "Usage" in combined or "/why" in combined

    def test_why_with_matching_query(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """/why should find exchanges mentioning the queried topic."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What would you like to build?"),
            _make_response("Managed identity is the recommended auth approach."),
            _make_response("## Summary\nAll confirmed."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["Use managed identity for auth", "/why managed identity", "done"])
        output = []

        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=output.append,
        )

        combined = "\n".join(str(x) for x in output)
        assert "Exchange" in combined

    def test_why_no_matches(
        self, mock_agent_context, mock_registry, mock_biz_agent,
    ):
        """/why with no matching history should show 'no exchanges found'."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What would you like to build?"),
            _make_response("## Summary\nNothing yet."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["/why kubernetes", "done"])
        output = []

        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=output.append,
        )

        combined = "\n".join(str(x) for x in output)
        assert "No exchanges found" in combined


# ======================================================================
# Multi-modal (images) support
# ======================================================================


class TestMultiModalOpening:
    """Test that images produce multi-modal content arrays."""

    def test_build_opening_without_images(self, mock_agent_context, mock_registry):
        session = DiscoverySession(mock_agent_context, mock_registry, console=MagicMock())
        result = session._build_opening("context", "artifacts")
        assert isinstance(result, str)
        assert "context" in result

    def test_build_opening_with_images(self, mock_agent_context, mock_registry):
        session = DiscoverySession(mock_agent_context, mock_registry, console=MagicMock())
        images = [
            {"filename": "arch.png", "data": "abc123", "mime": "image/png"},
            {"filename": "flow.jpg", "data": "def456", "mime": "image/jpeg"},
        ]
        result = session._build_opening("context", "artifacts", images=images)
        assert isinstance(result, list)
        # First element is text
        assert result[0]["type"] == "text"
        assert "context" in result[0]["text"]
        # Images follow
        assert result[1]["type"] == "image_url"
        assert "image/png" in result[1]["image_url"]["url"]
        assert result[2]["type"] == "image_url"
        assert "image/jpeg" in result[2]["image_url"]["url"]

    def test_build_opening_empty_images_returns_string(self, mock_agent_context, mock_registry):
        session = DiscoverySession(mock_agent_context, mock_registry, console=MagicMock())
        result = session._build_opening("context", "", images=[])
        assert isinstance(result, str)

    def test_chat_with_multimodal_content(self, mock_agent_context, mock_registry):
        """Multi-modal content array flows through _chat successfully."""
        mock_agent_context.ai_provider.chat.return_value = _make_response("I see the diagram.")
        session = DiscoverySession(mock_agent_context, mock_registry, console=MagicMock())

        content = [
            {"type": "text", "text": "Review this architecture"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]
        response = session._chat(content)
        assert response == "I see the diagram."
        # Verify AIMessage was constructed with list content
        call_args = mock_agent_context.ai_provider.chat.call_args
        messages = call_args[0][0]
        user_msg = [m for m in messages if m.role == "user"][-1]
        assert isinstance(user_msg.content, list)

    def test_chat_vision_fallback(self, mock_agent_context, mock_registry):
        """When multi-modal chat fails, _chat retries as text-only."""
        # First call raises, second succeeds
        mock_agent_context.ai_provider.chat.side_effect = [
            Exception("Vision not supported"),
            _make_response("Got it (text only)."),
        ]
        session = DiscoverySession(mock_agent_context, mock_registry, console=MagicMock())

        content = [
            {"type": "text", "text": "Review this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]
        response = session._chat(content)
        assert response == "Got it (text only)."
        # Provider was called twice
        assert mock_agent_context.ai_provider.chat.call_count == 2
        # Second call has string content (fallback)
        second_call = mock_agent_context.ai_provider.chat.call_args_list[1]
        messages = second_call[0][0]
        user_msg = [m for m in messages if m.role == "user"][-1]
        assert isinstance(user_msg.content, str)
        assert "[Images could not be processed" in user_msg.content

    def test_run_passes_images_to_opening(self, mock_agent_context, mock_registry):
        """The run() method passes artifact_images to _build_opening."""
        mock_agent_context.ai_provider.chat.return_value = _make_response(
            f"Got your images! {_READY_MARKER}"
        )
        session = DiscoverySession(mock_agent_context, mock_registry, console=MagicMock())
        images = [{"filename": "x.png", "data": "abc", "mime": "image/png"}]

        result = session.run(
            seed_context="test",
            artifact_images=images,
            input_fn=lambda _: "done",
            print_fn=lambda x: None,
            context_only=True,
        )
        # Verify the provider received a multi-modal message
        first_call = mock_agent_context.ai_provider.chat.call_args_list[0]
        messages = first_call[0][0]
        user_msg = [m for m in messages if m.role == "user"][0]
        assert isinstance(user_msg.content, list)


# ======================================================================
# Discovery state multi-modal persistence
# ======================================================================


class TestDiscoveryStateMultiModal:
    """Multi-modal content is persisted as text with image count."""

    def test_update_from_exchange_multimodal(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState

        state = DiscoveryState(str(tmp_path))
        state.load()
        multimodal = [
            {"type": "text", "text": "Here is my architecture"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,def"}},
        ]
        state.update_from_exchange(multimodal, "Looks good!", 1)

        history = state.state["conversation_history"]
        assert len(history) == 1
        assert "Here is my architecture" in history[0]["user"]
        assert "[2 image(s) attached]" in history[0]["user"]
        assert "base64" not in history[0]["user"]

    def test_update_from_exchange_string(self, tmp_path):
        """Regular string input still works."""
        from azext_prototype.stages.discovery_state import DiscoveryState

        state = DiscoveryState(str(tmp_path))
        state.load()
        state.update_from_exchange("plain text", "response", 1)

        history = state.state["conversation_history"]
        assert history[0]["user"] == "plain text"


# ======================================================================
# Joint analyst + architect discovery
# ======================================================================


class TestJointDiscovery:
    """Test that both biz-analyst and cloud-architect contribute to discovery."""

    def test_architect_context_injected_into_chat(
        self, mock_agent_context, mock_registry,
    ):
        """System messages should include architect constraints."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("Tell me about your project."),
            _make_response("## Project Summary\nTest project."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        session.run(
            input_fn=lambda _: "done",
            print_fn=lambda x: None,
        )

        # Check that the first AI call includes architect context
        first_call = mock_agent_context.ai_provider.chat.call_args_list[0]
        messages = first_call[0][0]
        system_msgs = [m.content for m in messages if m.role == "system"]
        combined = "\n".join(system_msgs)
        assert "Architectural Guidance" in combined
        assert "Managed Identity" in combined

    def test_architect_constraints_in_system_messages(
        self, mock_agent_context, mock_registry,
    ):
        """Architect's constraints should appear in system messages."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What are you building?"),
            _make_response("## Project Summary\nDone."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        session.run(
            input_fn=lambda _: "done",
            print_fn=lambda x: None,
        )

        first_call = mock_agent_context.ai_provider.chat.call_args_list[0]
        messages = first_call[0][0]
        system_content = "\n".join(m.content for m in messages if m.role == "system")
        assert "PaaS over IaaS" in system_content
        assert "Well-Architected Framework" in system_content

    def test_single_ai_call_per_turn(
        self, mock_agent_context, mock_registry,
    ):
        """Joint discovery still uses a single AI call per turn."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What are you building?"),
            _make_response("Got it."),
            _make_response("## Project Summary\nDone."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["A web app", "done"])
        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        # 3 calls: opening + user reply + summary — NOT doubled
        assert mock_agent_context.ai_provider.chat.call_count == 3

    def test_no_architect_still_works(
        self, mock_agent_context, mock_biz_agent,
    ):
        """Discovery works when no architect agent is available."""
        registry = MagicMock()

        def find_by_cap(cap):
            if cap == AgentCapability.BIZ_ANALYSIS:
                return [mock_biz_agent]
            return []

        registry.find_by_capability.side_effect = find_by_cap

        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What are you building?"),
            _make_response("## Project Summary\nDone."),
        ]

        session = DiscoverySession(mock_agent_context, registry)
        result = session.run(
            input_fn=lambda _: "done",
            print_fn=lambda x: None,
        )
        assert not result.cancelled
        # No architect context in messages
        first_call = mock_agent_context.ai_provider.chat.call_args_list[0]
        messages = first_call[0][0]
        system_content = "\n".join(m.content for m in messages if m.role == "system")
        assert "Architectural Guidance" not in system_content

    def test_build_architect_context_returns_empty_when_none(
        self, mock_agent_context, mock_biz_agent,
    ):
        """_build_architect_context returns '' when no architect agent."""
        registry = MagicMock()
        registry.find_by_capability.side_effect = lambda cap: (
            [mock_biz_agent] if cap == AgentCapability.BIZ_ANALYSIS else []
        )
        session = DiscoverySession(mock_agent_context, registry)
        assert session._build_architect_context() == ""


# ======================================================================
# Updated summary format
# ======================================================================


class TestUpdatedSummaryFormat:
    """Test that the summary prompt requests the exact heading format."""

    def test_summary_prompt_mentions_required_headings(
        self, mock_agent_context, mock_registry,
    ):
        """The summary prompt should mention the exact headings to use."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What are you building?"),
            _make_response("A web API. Got it."),
            _make_response("## Project Summary\nOrders API\n## Goals\n- Manage orders"),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["An orders REST API", "done"])
        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        # The summary call (last call) should mention the required headings
        summary_call = mock_agent_context.ai_provider.chat.call_args_list[-1]
        messages = summary_call[0][0]
        user_msgs = [m.content for m in messages if m.role == "user"]
        summary_prompt = user_msgs[-1]
        assert "Project Summary" in summary_prompt
        assert "Prototype Scope" in summary_prompt
        assert "Policy Overrides" in summary_prompt
        assert "In Scope" in summary_prompt

    def test_summary_prompt_asks_for_no_skipped_sections(
        self, mock_agent_context, mock_registry,
    ):
        """The summary prompt should instruct not to skip sections."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("Tell me more."),
            _make_response("## Project Summary\nTest"),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        session.run(
            input_fn=lambda _: "done",
            print_fn=lambda x: None,
        )

        summary_call = mock_agent_context.ai_provider.chat.call_args_list[-1]
        messages = summary_call[0][0]
        user_msgs = [m.content for m in messages if m.role == "user"]
        summary_prompt = user_msgs[-1]
        assert "None" in summary_prompt or "skip" in summary_prompt.lower()


# ======================================================================
# Natural Language Intent Detection — Integration
# ======================================================================


class TestNaturalLanguageIntentDiscovery:
    """Test that natural language triggers the correct slash commands."""

    def test_nl_open_items(self, mock_agent_context, mock_registry):
        """'what are the open items' should trigger the /open display."""
        # Use return_value — any call returns a valid response (no headings
        # to avoid triggering section-at-a-time gating)
        mock_agent_context.ai_provider.chat.return_value = _make_response(
            "Tell me about your project."
        )
        session = DiscoverySession(mock_agent_context, mock_registry)
        output = []
        inputs = iter(["what are the open items", "done"])
        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=output.append,
        )
        # The /open handler should have run and printed open items info
        assert any("open" in o.lower() for o in output if isinstance(o, str))

    def test_nl_status(self, mock_agent_context, mock_registry):
        """'where do we stand' should trigger the /status display."""
        mock_agent_context.ai_provider.chat.return_value = _make_response(
            "Tell me about your project."
        )
        session = DiscoverySession(mock_agent_context, mock_registry)
        output = []
        inputs = iter(["where do we stand", "done"])
        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=output.append,
        )
        assert any("status" in o.lower() or "discovery" in o.lower() for o in output if isinstance(o, str))


# ======================================================================
# extract_section_headers
# ======================================================================

class TestExtractSectionHeaders:
    """Unit tests for extract_section_headers()."""

    def test_extracts_h2_headings(self):
        text = "## Project Context & Scope\nSome text\n## Data & Content\nMore text"
        result = extract_section_headers(text)
        assert result == [("Project Context & Scope", 2), ("Data & Content", 2)]

    def test_h3_only_returns_empty(self):
        """Level-3 only responses produce no headers (subsections are not topics)."""
        text = "### Authentication\nDetails\n### Authorization\nMore details"
        result = extract_section_headers(text)
        assert result == []

    def test_mixed_h2_h3_returns_only_h2(self):
        """Level-3 subsections are filtered out — only level-2 topics returned."""
        text = "## Overview\nText\n### Sub-section\nText\n## Architecture\nText"
        result = extract_section_headers(text)
        assert result == [("Overview", 2), ("Architecture", 2)]

    def test_skips_structural_headings(self):
        text = (
            "## Project Context\nText\n"
            "## Summary\nText\n"
            "## Policy Overrides\nText\n"
            "## Next Steps\nText\n"
        )
        result = extract_section_headers(text)
        assert result == [("Project Context", 2)]

    def test_skips_policy_override_singular(self):
        text = "## Policy Override\nText"
        result = extract_section_headers(text)
        assert result == []

    def test_skips_short_headings(self):
        text = "## AB\nText\n## OK\nMore"
        result = extract_section_headers(text)
        assert result == []

    def test_empty_string(self):
        assert extract_section_headers("") == []

    def test_no_headings(self):
        text = "Just plain text without any headings at all."
        assert extract_section_headers(text) == []

    def test_h1_not_extracted(self):
        """Only ## and ### are extracted, not #."""
        text = "# Title\n## Section One\nContent"
        result = extract_section_headers(text)
        assert result == [("Section One", 2)]

    def test_strips_whitespace(self):
        text = "##   Padded Heading   \nText"
        result = extract_section_headers(text)
        assert result == [("Padded Heading", 2)]

    def test_case_insensitive_skip(self):
        text = "## SUMMARY\nText\n## NEXT STEPS\nText\n## Actual Content\nText"
        result = extract_section_headers(text)
        assert result == [("Actual Content", 2)]

    def test_bold_headings_extracted(self):
        """**Bold Heading** on its own line should be extracted as level 2."""
        text = (
            "Let me ask about your project.\n"
            "\n"
            "**Hosting & Deployment**\n"
            "How do you plan to host this?\n"
            "\n"
            "**Data Layer**\n"
            "What database will you use?"
        )
        result = extract_section_headers(text)
        assert ("Hosting & Deployment", 2) in result
        assert ("Data Layer", 2) in result

    def test_bold_inline_not_extracted(self):
        """Bold text mid-line should NOT be extracted as a heading."""
        text = "I think **this is important** for the project."
        result = extract_section_headers(text)
        assert result == []

    def test_bold_and_markdown_headings_merged(self):
        """Both ## headings and **bold headings** should be found with levels."""
        text = (
            "## Architecture Overview\n"
            "Details here.\n"
            "\n"
            "**Security Considerations**\n"
            "More details."
        )
        result = extract_section_headers(text)
        assert ("Architecture Overview", 2) in result
        assert ("Security Considerations", 2) in result

    def test_bold_headings_deduped(self):
        """Duplicate headings (same text in both formats) should appear once."""
        text = (
            "## Security\n"
            "Details.\n"
            "\n"
            "**Security**\n"
            "More details."
        )
        result = extract_section_headers(text)
        texts = [h[0] for h in result]
        assert texts.count("Security") == 1

    def test_bold_headings_skip_structural(self):
        """Bold structural headings (Summary, Next Steps) should be skipped."""
        text = "**Summary**\nText\n**Actual Topic**\nMore text"
        result = extract_section_headers(text)
        texts = [h[0] for h in result]
        assert "Summary" not in texts
        assert "Actual Topic" in texts

    def test_bold_heading_too_short(self):
        """Bold headings under 3 chars should be skipped."""
        text = "**AB**\nText"
        result = extract_section_headers(text)
        assert result == []

    def test_skip_what_ive_understood(self):
        """'What I've Understood So Far' and variants should be filtered."""
        text = (
            "## What I've Understood So Far\nStuff\n"
            "## What We've Covered\nMore stuff\n"
            "## Actual Topic\nReal content"
        )
        result = extract_section_headers(text)
        texts = [h[0] for h in result]
        assert "What I've Understood So Far" not in texts
        assert "What We've Covered" not in texts
        assert "Actual Topic" in texts

    def test_position_ordering(self):
        """Headers should be sorted by their position in the response."""
        text = (
            "**First Bold**\n"
            "Text\n"
            "## Second Markdown\n"
            "Text\n"
            "**Third Bold**\n"
            "Text"
        )
        result = extract_section_headers(text)
        assert result == [("First Bold", 2), ("Second Markdown", 2), ("Third Bold", 2)]


# ======================================================================
# section_fn callback integration
# ======================================================================

class TestSectionFnCallback:
    """Verify that section_fn is called with extracted headers during a session."""

    def test_section_fn_receives_headers(
        self, mock_agent_context, mock_registry,
    ):
        """section_fn should be called upfront with all headers from the AI response."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response(
                "## Project Context & Scope\n"
                "Let me ask about your project.\n"
                "## Data & Content\n"
                "What kind of data will you store?"
            ),
            # Summary after "done" exits the section loop
            _make_response("## Summary\nAll done."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        captured_headers = []

        def _section_fn(headers):
            captured_headers.extend(headers)

        # "done" exits from the section loop immediately
        result = session.run(
            input_fn=lambda _: "done",
            print_fn=lambda x: None,
            section_fn=_section_fn,
        )

        texts = [h[0] for h in captured_headers]
        assert "Project Context & Scope" in texts
        assert "Data & Content" in texts

    def test_section_fn_not_called_when_none(
        self, mock_agent_context, mock_registry,
    ):
        """When section_fn is None, no error should occur."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("## Some Heading\nContent"),
            _make_response("## Summary\nDone"),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        # Should not raise — section_fn defaults to None
        result = session.run(
            input_fn=lambda _: "done",
            print_fn=lambda x: None,
        )
        assert not result.cancelled


# ======================================================================
# response_fn callback integration
# ======================================================================

class TestResponseFnCallback:
    """Verify that response_fn is called with agent responses during a session."""

    def test_response_fn_receives_agent_responses(
        self, mock_agent_context, mock_registry,
    ):
        """response_fn should be called with cleaned agent responses."""
        # Use a response without ## headings so it takes the non-sectioned path
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("Let me understand your project. What are you building?"),
            _make_response("An API. Got it."),
            _make_response("Final summary."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        captured = []

        def _response_fn(content):
            captured.append(content)

        inputs = iter(["A REST API", "done"])
        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
            response_fn=_response_fn,
        )

        # response_fn should have been called for the opening and the reply
        assert len(captured) == 2
        assert "understand your project" in captured[0]
        assert "API" in captured[1]

    def test_response_fn_not_called_when_none(
        self, mock_agent_context, mock_registry,
    ):
        """When response_fn is None, print_fn should be used instead."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What are you building?"),
            _make_response("## Summary\nDone"),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        printed = []

        result = session.run(
            input_fn=lambda _: "done",
            print_fn=lambda x: printed.append(x),
        )

        # print_fn should have received the response
        assert any("building" in p.lower() for p in printed if isinstance(p, str))

    def test_response_fn_takes_precedence_over_print_fn(
        self, mock_agent_context, mock_registry,
    ):
        """response_fn should be used instead of print_fn for agent responses."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("Tell me about your project."),
            _make_response("## Summary\nDone."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        printed = []
        response_captured = []

        result = session.run(
            input_fn=lambda _: "done",
            print_fn=lambda x: printed.append(x),
            response_fn=lambda x: response_captured.append(x),
        )

        # response_fn should have the agent response
        assert len(response_captured) == 1
        assert "Tell me about your project" in response_captured[0]
        # print_fn should NOT have the agent response text
        assert not any("Tell me about your project" in p for p in printed if isinstance(p, str))


# ======================================================================
# parse_sections()
# ======================================================================

class TestParseSections:
    """Verify section parsing from AI responses."""

    def test_basic_section_splitting(self):
        text = (
            "Here's my analysis.\n\n"
            "## Authentication\n"
            "How do users sign in?\n\n"
            "## Data Layer\n"
            "What database do you prefer?"
        )
        preamble, sections = parse_sections(text)
        assert preamble == "Here's my analysis."
        assert len(sections) == 2
        assert sections[0].heading == "Authentication"
        assert sections[0].level == 2
        assert "How do users sign in?" in sections[0].content
        assert sections[1].heading == "Data Layer"
        assert "What database" in sections[1].content

    def test_preamble_only(self):
        text = "No headings here, just a plain response."
        preamble, sections = parse_sections(text)
        assert preamble == text
        assert sections == []

    def test_empty_preamble(self):
        text = "## First Topic\nQuestion here."
        preamble, sections = parse_sections(text)
        assert preamble == ""
        assert len(sections) == 1

    def test_skip_headings_filtered(self):
        text = (
            "## Authentication\nHow do users sign in?\n\n"
            "## Summary\nThis is a summary.\n\n"
            "## Next Steps\nDo this next."
        )
        _, sections = parse_sections(text)
        assert len(sections) == 1
        assert sections[0].heading == "Authentication"

    def test_task_id_generation(self):
        text = "## Data & Content\nWhat kind of data?"
        _, sections = parse_sections(text)
        assert len(sections) == 1
        assert sections[0].task_id == "design-section-data-content"

    def test_bold_headings(self):
        text = (
            "Here's what I need to know.\n\n"
            "**Authentication & Security**\n"
            "How do users log in?\n\n"
            "**Data Storage**\n"
            "What database?"
        )
        preamble, sections = parse_sections(text)
        assert len(sections) == 2
        assert sections[0].heading == "Authentication & Security"
        assert sections[0].level == 2

    def test_level_3_only_returns_empty(self):
        """Level-3 only response produces no sections (not treated as topics)."""
        text = "### Sub-topic\nDetailed question."
        _, sections = parse_sections(text)
        assert len(sections) == 0

    def test_subsections_folded_into_parent(self):
        """Level-3 subsections are folded into their parent level-2 section."""
        text = (
            "## Main Topic\nOverview.\n\n"
            "### Sub-topic\nDetail."
        )
        _, sections = parse_sections(text)
        assert len(sections) == 1
        assert sections[0].heading == "Main Topic"
        assert sections[0].level == 2
        # Subsection content is included in the parent's content
        assert "### Sub-topic" in sections[0].content
        assert "Detail." in sections[0].content

    def test_multiple_subsections_folded(self):
        """Multiple ### subsections under one ## are all included."""
        text = (
            "## Scope Boundary\nLet's clarify scope.\n\n"
            "### In Scope\n- Item A\n- Item B\n\n"
            "### Out of Scope\n- Item C\n\n"
            "## Next Topic\nQuestions here."
        )
        _, sections = parse_sections(text)
        assert len(sections) == 2
        assert sections[0].heading == "Scope Boundary"
        assert "### In Scope" in sections[0].content
        assert "### Out of Scope" in sections[0].content
        assert "Item A" in sections[0].content
        assert "Item C" in sections[0].content
        assert sections[1].heading == "Next Topic"

    def test_empty_string(self):
        preamble, sections = parse_sections("")
        assert preamble == ""
        assert sections == []

    def test_duplicate_headings_deduped(self):
        text = (
            "## Authentication\nFirst mention.\n\n"
            "## Authentication\nSecond mention."
        )
        _, sections = parse_sections(text)
        assert len(sections) == 1


# ======================================================================
# Section completion via AI "Yes" gate
# ======================================================================

class TestSectionDoneDetection:
    """Verify section completion detection via AI 'Yes' gate.

    The old heuristic-based ``_is_section_done()`` has been replaced with
    an explicit AI confirmation step.  When the AI responds with exactly
    "Yes" (case-insensitive, optional trailing period) the section is
    considered complete.
    """

    def test_continue_in_done_words(self):
        """'continue' should be accepted as a done keyword."""
        assert "continue" in _DONE_WORDS


# ======================================================================
# Section-at-a-time flow integration
# ======================================================================

class TestSectionAtATimeFlow:
    """Verify sections are shown one at a time with follow-ups."""

    def test_sections_shown_one_at_a_time(
        self, mock_agent_context, mock_registry,
    ):
        """Each section should be shown individually, collecting user input."""
        mock_agent_context.ai_provider.chat.side_effect = [
            # Initial response with 2 sections
            _make_response(
                "Great, let me explore a few areas.\n\n"
                "## Authentication\n"
                "How do users sign in?\n\n"
                "## Data Layer\n"
                "What database do you need?"
            ),
            # Follow-up for section 1 (auth) — marks section done
            _make_response("Yes"),
            # Follow-up for section 2 (data) — marks section done
            _make_response("Yes"),
            # Summary after free-form "done"
            _make_response("## Summary\nAll done."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        printed = []
        inputs = iter([
            "We use Entra ID",     # Answer for section 1
            "SQL Database",         # Answer for section 2
            "done",                 # Exit free-form loop
        ])

        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: printed.append(x),
        )
        assert not result.cancelled
        # Both sections should have been displayed
        printed_text = "\n".join(str(p) for p in printed)
        assert "Authentication" in printed_text
        assert "Data Layer" in printed_text

    def test_skip_advances_to_next_section(
        self, mock_agent_context, mock_registry,
    ):
        """Typing 'skip' should advance to the next section."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response(
                "## Auth\nHow do users sign in?\n\n"
                "## Data\nWhat database?"
            ),
            # Follow-up for data section
            _make_response("Yes"),
            # Summary
            _make_response("## Summary\nDone."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter([
            "skip",        # Skip auth section
            "Cosmos DB",   # Answer data section
            "done",        # Exit free-form
        ])

        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )
        assert not result.cancelled

    def test_done_exits_section_loop(
        self, mock_agent_context, mock_registry,
    ):
        """Typing 'done' during section loop should jump to summary."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response(
                "## Auth\nHow do users sign in?\n\n"
                "## Data\nWhat database?"
            ),
            # Summary produced after "done"
            _make_response("## Summary\nFinal summary."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        result = session.run(
            input_fn=lambda _: "done",
            print_fn=lambda x: None,
        )
        assert not result.cancelled
        assert result.requirements  # Should have summary

    def test_quit_cancels_from_section_loop(
        self, mock_agent_context, mock_registry,
    ):
        """Typing 'quit' during section loop should cancel the session."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response(
                "## Auth\nHow do users sign in?\n\n"
                "## Data\nWhat database?"
            ),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        result = session.run(
            input_fn=lambda _: "quit",
            print_fn=lambda x: None,
        )
        assert result.cancelled

    def test_follow_ups_iterate_within_section(
        self, mock_agent_context, mock_registry,
    ):
        """Multiple follow-ups within a section should work."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("## Auth\nHow do users sign in?"),
            # First follow-up — needs more info
            _make_response("What about service-to-service auth?"),
            # Second follow-up — section done
            _make_response("Yes"),
            # Summary
            _make_response("## Summary\nDone."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter([
            "Entra ID for users",          # First answer
            "Managed identity for services",  # Second answer
            "done",                          # Exit free-form
        ])

        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )
        assert not result.cancelled
        assert result.exchange_count >= 3  # opening + 2 follow-ups

    def test_update_task_fn_called(
        self, mock_agent_context, mock_registry,
    ):
        """update_task_fn should be called with in_progress and completed."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("## Auth\nHow do users sign in?"),
            _make_response("Yes"),
            _make_response("## Summary\nDone."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        task_updates = []

        def _update_task_fn(tid, status):
            task_updates.append((tid, status))

        inputs = iter(["Entra ID", "done"])
        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
            update_task_fn=_update_task_fn,
        )

        # Should have in_progress then completed for the auth section
        assert ("design-section-auth", "in_progress") in task_updates
        assert ("design-section-auth", "completed") in task_updates

    def test_no_sections_fallback(
        self, mock_agent_context, mock_registry,
    ):
        """When no sections are found, should display full response."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("Tell me what you want to build."),
            _make_response("## Summary\nDone."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        printed = []

        result = session.run(
            input_fn=lambda _: "done",
            print_fn=lambda x: printed.append(x),
        )

        assert not result.cancelled
        printed_text = "\n".join(str(p) for p in printed)
        assert "Tell me what you want to build" in printed_text

    def test_yes_gate_not_displayed(
        self, mock_agent_context, mock_registry,
    ):
        """AI 'Yes' confirmation should not be printed to the user."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("## Auth\nHow do users sign in?"),
            _make_response("Yes"),
            _make_response("## Summary\nDone."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        printed = []

        inputs = iter(["Entra ID", "continue"])
        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: printed.append(x),
        )

        printed_text = "\n".join(str(p) for p in printed)
        # The "Yes" response should not appear in output
        assert "\nYes\n" not in printed_text


# ======================================================================
# Topic persistence and re-entry
# ======================================================================


class TestTopicPersistence:
    """Topics are established once, persisted, and immutable across re-runs."""

    def test_topics_persisted_on_first_run(
        self, mock_agent_context, mock_registry, mock_biz_agent, tmp_path,
    ):
        """First run with sections should persist topics to discovery state."""
        from azext_prototype.stages.discovery_state import DiscoveryState

        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("## Auth\nHow do users sign in?\n## Data\nWhat database?"),
            _make_response("Yes"),  # Auth confirmed
            _make_response("Yes"),  # Data confirmed
            _make_response("## Summary\nDone."),
        ]

        ds = DiscoveryState(str(tmp_path))
        session = DiscoverySession(mock_agent_context, mock_registry, discovery_state=ds)
        inputs = iter(["Entra ID", "PostgreSQL", "done"])

        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        assert ds.has_topics
        topics = ds.topics
        assert len(topics) == 2
        assert topics[0].heading == "Auth"
        assert topics[1].heading == "Data"

    def test_topics_marked_answered_on_confirm(
        self, mock_agent_context, mock_registry, mock_biz_agent, tmp_path,
    ):
        """AI 'Yes' confirmation marks topic as answered."""
        from azext_prototype.stages.discovery_state import DiscoveryState

        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("## Auth\nHow do users sign in?\n## Data\nWhat database?"),
            _make_response("Yes"),  # Auth confirmed
            _make_response("Yes"),  # Data confirmed
            _make_response("## Summary\nDone."),
        ]

        ds = DiscoveryState(str(tmp_path))
        session = DiscoverySession(mock_agent_context, mock_registry, discovery_state=ds)
        inputs = iter(["Entra ID", "PostgreSQL", "done"])

        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        topics = ds.topics
        assert topics[0].status == "answered"
        assert topics[0].answer_exchange is not None
        assert topics[1].status == "answered"

    def test_topic_marked_skipped(
        self, mock_agent_context, mock_registry, mock_biz_agent, tmp_path,
    ):
        """Skipping a section marks the topic as skipped."""
        from azext_prototype.stages.discovery_state import DiscoveryState

        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("## Auth\nHow do users sign in?\n## Data\nWhat database?"),
            _make_response("Yes"),  # Data confirmed
            _make_response("## Summary\nDone."),
        ]

        ds = DiscoveryState(str(tmp_path))
        session = DiscoverySession(mock_agent_context, mock_registry, discovery_state=ds)
        inputs = iter(["skip", "PostgreSQL", "done"])

        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        topics = ds.topics
        assert topics[0].status == "skipped"
        assert topics[1].status == "answered"

    def test_topics_remain_pending_on_quit(
        self, mock_agent_context, mock_registry, mock_biz_agent, tmp_path,
    ):
        """Quitting mid-session leaves remaining topics as pending."""
        from azext_prototype.stages.discovery_state import DiscoveryState

        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("## Auth\nHow do users sign in?\n## Data\nWhat database?\n## Networking\nPublic or private?"),
            _make_response("Yes"),  # Auth confirmed
        ]

        ds = DiscoveryState(str(tmp_path))
        session = DiscoverySession(mock_agent_context, mock_registry, discovery_state=ds)
        inputs = iter(["Entra ID", "quit"])

        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        assert result.cancelled
        topics = ds.topics
        assert topics[0].status == "answered"
        assert topics[1].status == "pending"
        assert topics[2].status == "pending"


class TestTopicReentry:
    """Re-entry resumes at the first unanswered topic."""

    def test_reentry_resumes_at_first_pending(
        self, mock_agent_context, mock_registry, mock_biz_agent, tmp_path,
    ):
        """Re-run with existing topics resumes at first pending topic."""
        from azext_prototype.stages.discovery_state import DiscoveryState, Topic

        # Pre-populate state with topics (Auth answered, Data pending)
        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_topics([
            Topic(heading="Auth", detail="## Auth\nHow do users sign in?", kind="topic", status="answered", answer_exchange=2),
            Topic(heading="Data", detail="## Data\nWhat database?", kind="topic", status="pending", answer_exchange=None),
        ])
        ds.state["_metadata"]["exchange_count"] = 2
        ds.save()

        # Re-run: should skip Auth and start with Data
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("Yes"),  # Data confirmed
            _make_response("## Summary\nDone."),
        ]

        ds2 = DiscoveryState(str(tmp_path))
        session = DiscoverySession(mock_agent_context, mock_registry, discovery_state=ds2)
        inputs = iter(["PostgreSQL", "done"])

        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        assert not result.cancelled
        # Data should now be answered
        topics = ds2.topics
        assert topics[0].status == "answered"  # Auth unchanged
        assert topics[1].status == "answered"  # Data now answered

    def test_reentry_shows_progress_message(
        self, mock_agent_context, mock_registry, mock_biz_agent, tmp_path,
    ):
        """Re-entry should show a progress message."""
        from azext_prototype.stages.discovery_state import DiscoveryState, Topic

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_topics([
            Topic(heading="Auth", detail="## Auth\nHow?", kind="topic", status="answered", answer_exchange=1),
            Topic(heading="Data", detail="## Data\nWhat?", kind="topic", status="pending", answer_exchange=None),
            Topic(heading="Net", detail="## Net\nPublic?", kind="topic", status="pending", answer_exchange=None),
        ])
        ds.save()

        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("Yes"),
            _make_response("Yes"),
            _make_response("## Summary\nDone."),
        ]

        ds2 = DiscoveryState(str(tmp_path))
        session = DiscoverySession(mock_agent_context, mock_registry, discovery_state=ds2)
        printed = []
        inputs = iter(["PostgreSQL", "Public", "done"])

        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=printed.append,
        )

        combined = "\n".join(str(p) for p in printed)
        assert "1/3 topics covered" in combined

    def test_reentry_all_topics_done_falls_through(
        self, mock_agent_context, mock_registry, mock_biz_agent, tmp_path,
    ):
        """If all topics are done on re-entry, fall through to free-form loop."""
        from azext_prototype.stages.discovery_state import DiscoveryState, Topic

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_topics([
            Topic(heading="Auth", detail="## Auth\nHow?", kind="topic", status="answered", answer_exchange=1),
            Topic(heading="Data", detail="## Data\nWhat?", kind="topic", status="answered", answer_exchange=2),
        ])
        ds.save()

        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("## Summary\nDone."),  # Summary from free-form "done"
        ]

        ds2 = DiscoveryState(str(tmp_path))
        session = DiscoverySession(mock_agent_context, mock_registry, discovery_state=ds2)

        result = session.run(
            input_fn=lambda _: "done",
            print_fn=lambda x: None,
        )

        assert not result.cancelled

    def test_reentry_does_not_resend_full_history(
        self, mock_agent_context, mock_registry, mock_biz_agent, tmp_path,
    ):
        """Re-entry seeds messages with compact summary, not full history."""
        from azext_prototype.stages.discovery_state import DiscoveryState, Topic

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.state["project"]["summary"] = "An inventory API"
        ds.set_topics([
            Topic(heading="Auth", detail="## Auth\nHow?", kind="topic", status="answered", answer_exchange=1),
            Topic(heading="Data", detail="## Data\nWhat?", kind="topic", status="pending", answer_exchange=None),
        ])
        ds.state["_metadata"]["exchange_count"] = 3
        # Add large conversation history
        for i in range(20):
            ds.state["conversation_history"].append({
                "exchange": i + 1,
                "timestamp": "2026-01-01T00:00:00",
                "user": f"Long user message {i}" * 50,
                "assistant": f"Long assistant response {i}" * 50,
            })
        ds.save()

        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("Yes"),
            _make_response("## Summary\nDone."),
        ]

        ds2 = DiscoveryState(str(tmp_path))
        session = DiscoverySession(mock_agent_context, mock_registry, discovery_state=ds2)
        inputs = iter(["PostgreSQL", "done"])

        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        # The first AI call should NOT contain all 20 exchanges
        first_call = mock_agent_context.ai_provider.chat.call_args_list[0]
        messages = first_call[0][0]
        user_msgs = [m for m in messages if m.role == "user"]
        # Should have compact summary + the section follow-up prompt, not 20+ user messages
        assert len(user_msgs) <= 5

    def test_reentry_restores_exchange_count(
        self, mock_agent_context, mock_registry, mock_biz_agent, tmp_path,
    ):
        """Re-entry restores exchange count from metadata."""
        from azext_prototype.stages.discovery_state import DiscoveryState, Topic

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_topics([
            Topic(heading="Data", detail="## Data\nWhat?", kind="topic", status="pending", answer_exchange=None),
        ])
        ds.state["_metadata"]["exchange_count"] = 5
        ds.save()

        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("Yes"),
            _make_response("## Summary\nDone."),
        ]

        ds2 = DiscoveryState(str(tmp_path))
        session = DiscoverySession(mock_agent_context, mock_registry, discovery_state=ds2)
        inputs = iter(["PostgreSQL", "done"])

        result = session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        # Exchange count should continue from 5, not restart at 0
        assert result.exchange_count == 6


class TestIncrementalTopics:
    """New artifacts can add topics but not replace existing ones."""

    def test_new_artifacts_add_topics(
        self, mock_agent_context, mock_registry, mock_biz_agent, tmp_path,
    ):
        """Re-entry with new artifacts should add new topics."""
        from azext_prototype.stages.discovery_state import DiscoveryState, Topic

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_topics([
            Topic(heading="Auth", detail="## Auth\nHow?", kind="topic", status="answered", answer_exchange=1),
            Topic(heading="Data", detail="## Data\nWhat?", kind="topic", status="answered", answer_exchange=2),
        ])
        ds.save()

        # AI identifies a new topic from the new artifact
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("## Caching\nWhat caching strategy do you need?"),  # incremental context
            _make_response("Yes"),  # Caching confirmed
            _make_response("## Summary\nDone."),
        ]

        ds2 = DiscoveryState(str(tmp_path))
        session = DiscoverySession(mock_agent_context, mock_registry, discovery_state=ds2)
        inputs = iter(["Redis", "done"])

        session.run(
            seed_context="We also need caching",
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        topics = ds2.topics
        assert len(topics) == 3
        assert topics[2].heading == "Caching"

    def test_no_new_topics_marker(
        self, mock_agent_context, mock_registry, mock_biz_agent, tmp_path,
    ):
        """AI returns [NO_NEW_TOPICS] when artifacts don't warrant new topics."""
        from azext_prototype.stages.discovery_state import DiscoveryState, Topic

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_topics([
            Topic(heading="Auth", detail="## Auth\nHow?", kind="topic", status="answered", answer_exchange=1),
        ])
        ds.save()

        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("[NO_NEW_TOPICS]"),  # No new topics needed
            _make_response("What are you building?"),  # Free-form (all topics done)
            _make_response("## Summary\nDone."),
        ]

        ds2 = DiscoveryState(str(tmp_path))
        session = DiscoverySession(mock_agent_context, mock_registry, discovery_state=ds2)

        session.run(
            seed_context="Same project, just more detail",
            input_fn=lambda _: "done",
            print_fn=lambda x: None,
        )

        # Original topics unchanged
        assert len(ds2.topics) == 1
        assert ds2.topics[0].heading == "Auth"

    def test_duplicate_headings_deduplicated(
        self, mock_agent_context, mock_registry, mock_biz_agent, tmp_path,
    ):
        """append_topics should not add duplicates (case-insensitive)."""
        from azext_prototype.stages.discovery_state import DiscoveryState, Topic

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_topics([
            Topic(heading="Auth", detail="## Auth\nHow?", kind="topic", status="answered", answer_exchange=1),
        ])

        ds.append_topics([
            Topic(heading="auth", detail="## auth\nDuplicate?", kind="topic", status="pending", answer_exchange=None),
            Topic(heading="Caching", detail="## Caching\nNew topic", kind="topic", status="pending", answer_exchange=None),
        ])

        topics = ds.topics
        assert len(topics) == 2  # Auth (original) + Caching (new)
        assert topics[0].heading == "Auth"
        assert topics[1].heading == "Caching"


class TestTopicStateHelpers:
    """Unit tests for Topic dataclass and DiscoveryState topic helpers."""

    def test_topic_to_dict_roundtrip(self):
        from azext_prototype.stages.discovery_state import Topic

        t = Topic(heading="Auth", detail="How do users sign in?", kind="topic", status="answered", answer_exchange=3)
        d = t.to_dict()
        t2 = Topic.from_dict(d)
        assert t2.heading == "Auth"
        assert t2.detail == "How do users sign in?"
        assert t2.status == "answered"
        assert t2.answer_exchange == 3

    def test_topic_from_dict_defaults(self):
        from azext_prototype.stages.discovery_state import Topic

        t = Topic.from_dict({"heading": "Auth"})
        assert t.detail == ""
        assert t.status == "pending"
        assert t.answer_exchange is None

    def test_default_state_has_items_key(self):
        from azext_prototype.stages.discovery_state import _default_discovery_state

        state = _default_discovery_state()
        assert "items" in state
        assert state["items"] == []

    def test_has_topics_false_on_empty(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        assert not ds.has_topics

    def test_first_pending_topic_index(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState, Topic

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_topics([
            Topic(heading="A", detail="Q", kind="topic", status="answered", answer_exchange=1),
            Topic(heading="B", detail="Q", kind="topic", status="skipped", answer_exchange=None),
            Topic(heading="C", detail="Q", kind="topic", status="pending", answer_exchange=None),
        ])
        assert ds.first_pending_topic_index() == 2

    def test_first_pending_topic_index_none_when_all_done(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState, Topic

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_topics([
            Topic(heading="A", detail="Q", kind="topic", status="answered", answer_exchange=1),
        ])
        assert ds.first_pending_topic_index() is None

    def test_mark_topic(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState, Topic

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_topics([
            Topic(heading="Auth", detail="Q", kind="topic", status="pending", answer_exchange=None),
        ])
        ds.mark_topic("Auth", "answered", 5)
        topics = ds.topics
        assert topics[0].status == "answered"
        assert topics[0].answer_exchange == 5

    def test_backward_compat_old_yaml_without_items(self, tmp_path):
        """Old discovery.yaml without items key should get items: [] via deep_merge."""
        import yaml
        from azext_prototype.stages.discovery_state import DiscoveryState

        # Write a YAML file without items key (no topics/open_items/confirmed_items either)
        state_dir = tmp_path / ".prototype" / "state"
        state_dir.mkdir(parents=True)
        old_state = {
            "project": {"summary": "Old project", "goals": ["Goal 1"]},
            "requirements": {"functional": [], "non_functional": []},
            "constraints": [],
            "decisions": [],
            "risks": [],
            "scope": {"in_scope": [], "out_of_scope": [], "deferred": []},
            "architecture": {"services": [], "integrations": [], "data_flow": ""},
            "conversation_history": [],
            "_metadata": {"created": "2026-01-01", "last_updated": "2026-01-01", "exchange_count": 3},
        }
        with open(state_dir / "discovery.yaml", "w") as f:
            yaml.dump(old_state, f)

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        assert not ds.has_items  # Empty list = no items
        assert ds.state.get("items") == []
        # Old data preserved
        assert ds.state["project"]["summary"] == "Old project"


class TestLegacyMigration:
    """Verify old-format YAML (topics + open_items + confirmed_items) migrates on load."""

    def test_migrate_old_topics(self, tmp_path):
        """Legacy topics field is migrated into unified items."""
        import yaml
        from azext_prototype.stages.discovery_state import DiscoveryState

        state_dir = tmp_path / ".prototype" / "state"
        state_dir.mkdir(parents=True)
        old_state = {
            "project": {"summary": "", "goals": []},
            "requirements": {"functional": [], "non_functional": []},
            "constraints": [],
            "decisions": [],
            "topics": [
                {"heading": "Auth", "questions": "How do users sign in?", "status": "answered", "answer_exchange": 1},
                {"heading": "Data", "questions": "What database?", "status": "pending", "answer_exchange": None},
            ],
            "open_items": [],
            "confirmed_items": [],
            "risks": [],
            "scope": {"in_scope": [], "out_of_scope": [], "deferred": []},
            "architecture": {"services": [], "integrations": [], "data_flow": ""},
            "conversation_history": [],
            "_metadata": {"created": "2026-01-01", "last_updated": "2026-01-01", "exchange_count": 2},
        }
        with open(state_dir / "discovery.yaml", "w") as f:
            yaml.dump(old_state, f)

        ds = DiscoveryState(str(tmp_path))
        ds.load()

        assert "topics" not in ds.state
        assert "open_items" not in ds.state
        assert "confirmed_items" not in ds.state
        assert len(ds.items) == 2
        assert ds.items[0].heading == "Auth"
        assert ds.items[0].detail == "How do users sign in?"
        assert ds.items[0].kind == "topic"
        assert ds.items[0].status == "answered"
        assert ds.items[1].heading == "Data"
        assert ds.items[1].status == "pending"

    def test_migrate_old_open_and_confirmed_items(self, tmp_path):
        """Legacy open_items and confirmed_items migrate as decisions."""
        import yaml
        from azext_prototype.stages.discovery_state import DiscoveryState

        state_dir = tmp_path / ".prototype" / "state"
        state_dir.mkdir(parents=True)
        old_state = {
            "project": {"summary": "", "goals": []},
            "requirements": {"functional": [], "non_functional": []},
            "constraints": [],
            "decisions": [],
            "open_items": ["Which region?", "Auth method?"],
            "confirmed_items": ["Use PostgreSQL"],
            "risks": [],
            "scope": {"in_scope": [], "out_of_scope": [], "deferred": []},
            "architecture": {"services": [], "integrations": [], "data_flow": ""},
            "conversation_history": [],
            "_metadata": {"created": "2026-01-01", "last_updated": "2026-01-01", "exchange_count": 3},
        }
        with open(state_dir / "discovery.yaml", "w") as f:
            yaml.dump(old_state, f)

        ds = DiscoveryState(str(tmp_path))
        ds.load()

        assert "open_items" not in ds.state
        assert "confirmed_items" not in ds.state
        assert len(ds.items) == 3
        # Two pending decisions from open_items
        pending = ds.items_by_status("pending")
        assert len(pending) == 2
        assert all(i.kind == "decision" for i in pending)
        # One confirmed decision from confirmed_items
        confirmed = ds.items_by_status("confirmed")
        assert len(confirmed) == 1
        assert confirmed[0].heading == "Use PostgreSQL"

    def test_migrate_combined_topics_and_items(self, tmp_path):
        """Legacy state with both topics AND open_items merges correctly."""
        import yaml
        from azext_prototype.stages.discovery_state import DiscoveryState

        state_dir = tmp_path / ".prototype" / "state"
        state_dir.mkdir(parents=True)
        old_state = {
            "project": {"summary": "", "goals": []},
            "requirements": {"functional": [], "non_functional": []},
            "constraints": [],
            "decisions": [],
            "topics": [
                {"heading": "Auth", "questions": "How?", "status": "answered", "answer_exchange": 1},
            ],
            "open_items": ["Which region?"],
            "confirmed_items": ["Use Terraform"],
            "risks": [],
            "scope": {"in_scope": [], "out_of_scope": [], "deferred": []},
            "architecture": {"services": [], "integrations": [], "data_flow": ""},
            "conversation_history": [],
            "_metadata": {"created": "2026-01-01", "last_updated": "2026-01-01", "exchange_count": 2},
        }
        with open(state_dir / "discovery.yaml", "w") as f:
            yaml.dump(old_state, f)

        ds = DiscoveryState(str(tmp_path))
        ds.load()

        assert len(ds.items) == 3
        assert ds.items[0].kind == "topic"  # Auth
        assert ds.items[1].kind == "decision"  # Which region?
        assert ds.items[1].status == "pending"
        assert ds.items[2].kind == "decision"  # Use Terraform
        assert ds.items[2].status == "confirmed"


class TestUnifiedStatusCommands:
    """Verify /status, /open, /confirmed show data from unified items."""

    def test_status_shows_topics(self, tmp_path):
        """format_status_summary counts topics as items."""
        from azext_prototype.stages.discovery_state import DiscoveryState, Topic

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_items([
            Topic(heading="Auth", detail="Q?", kind="topic", status="answered", answer_exchange=1),
            Topic(heading="Data", detail="Q?", kind="topic", status="pending", answer_exchange=None),
            Topic(heading="Net", detail="Q?", kind="topic", status="pending", answer_exchange=None),
        ])

        assert ds.open_count == 2
        assert ds.confirmed_count == 1
        summary = ds.format_status_summary()
        assert "1 confirmed" in summary
        assert "2 open" in summary

    def test_open_items_shows_pending_topics(self, tmp_path):
        """format_open_items lists pending topics."""
        from azext_prototype.stages.discovery_state import DiscoveryState, Topic

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_items([
            Topic(heading="Auth", detail="Q?", kind="topic", status="answered", answer_exchange=1),
            Topic(heading="Data", detail="Q?", kind="topic", status="pending", answer_exchange=None),
        ])

        text = ds.format_open_items()
        assert "Data" in text
        assert "Auth" not in text
        assert "Topics:" in text

    def test_confirmed_items_shows_answered_topics(self, tmp_path):
        """format_confirmed_items lists answered topics."""
        from azext_prototype.stages.discovery_state import DiscoveryState, Topic

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_items([
            Topic(heading="Auth", detail="Q?", kind="topic", status="answered", answer_exchange=1),
            Topic(heading="Data", detail="Q?", kind="topic", status="pending", answer_exchange=None),
        ])

        text = ds.format_confirmed_items()
        assert "Auth" in text
        assert "Data" not in text

    def test_status_no_items(self, tmp_path):
        """format_status_summary with no items."""
        from azext_prototype.stages.discovery_state import DiscoveryState

        ds = DiscoveryState(str(tmp_path))
        ds.load()

        assert ds.format_status_summary() == "No items tracked yet."
        assert "No open items" in ds.format_open_items()
        assert "No items confirmed" in ds.format_confirmed_items()

    def test_mixed_kinds_in_open(self, tmp_path):
        """format_open_items groups topics and decisions separately."""
        from azext_prototype.stages.discovery_state import DiscoveryState, TrackedItem

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_items([
            TrackedItem(heading="Auth", detail="Q?", kind="topic", status="pending", answer_exchange=None),
            TrackedItem(heading="Which region?", detail="Which region?", kind="decision", status="pending", answer_exchange=None),
        ])

        text = ds.format_open_items()
        assert "Topics:" in text
        assert "Auth" in text
        assert "Decisions:" in text
        assert "Which region?" in text


class TestArtifactInventoryState:
    """Tests for artifact inventory and context hash tracking in DiscoveryState."""

    def test_default_state_has_inventory_keys(self):
        from azext_prototype.stages.discovery_state import _default_discovery_state

        state = _default_discovery_state()
        assert "artifact_inventory" in state
        assert state["artifact_inventory"] == {}
        assert "context_hash" in state
        assert state["context_hash"] == ""

    def test_artifact_inventory_roundtrip(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.update_artifact_inventory({"/abs/path/file.txt": "abc123", "/abs/path/img.png": "def456"})

        # Reload from disk
        ds2 = DiscoveryState(str(tmp_path))
        ds2.load()
        hashes = ds2.get_artifact_hashes()
        assert hashes == {"/abs/path/file.txt": "abc123", "/abs/path/img.png": "def456"}

    def test_get_artifact_hashes_flat_mapping(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.update_artifact_inventory({"/a/b.txt": "hash1", "/c/d.txt": "hash2"})

        hashes = ds.get_artifact_hashes()
        assert isinstance(hashes, dict)
        assert hashes["/a/b.txt"] == "hash1"
        assert hashes["/c/d.txt"] == "hash2"

    def test_update_artifact_inventory_is_additive(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.update_artifact_inventory({"/a/first.txt": "aaa"})
        ds.update_artifact_inventory({"/b/second.txt": "bbb"})

        hashes = ds.get_artifact_hashes()
        assert len(hashes) == 2
        assert hashes["/a/first.txt"] == "aaa"
        assert hashes["/b/second.txt"] == "bbb"

    def test_update_artifact_inventory_overwrites_hash(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.update_artifact_inventory({"/a/file.txt": "old_hash"})
        ds.update_artifact_inventory({"/a/file.txt": "new_hash"})

        assert ds.get_artifact_hashes()["/a/file.txt"] == "new_hash"

    def test_context_hash_roundtrip(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.update_context_hash("ctx_hash_abc")

        ds2 = DiscoveryState(str(tmp_path))
        ds2.load()
        assert ds2.get_context_hash() == "ctx_hash_abc"

    def test_reset_clears_inventory(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.update_artifact_inventory({"/a/file.txt": "hash1"})
        ds.update_context_hash("ctx_hash")

        ds.reset()
        assert ds.get_artifact_hashes() == {}
        assert ds.get_context_hash() == ""

    def test_legacy_state_without_inventory_loads(self, tmp_path):
        """Old discovery.yaml without inventory keys loads cleanly via _deep_merge."""
        import yaml
        from azext_prototype.stages.discovery_state import DiscoveryState

        state_dir = tmp_path / ".prototype" / "state"
        state_dir.mkdir(parents=True)
        # Write a minimal legacy state without the new keys
        legacy = {
            "project": {"summary": "test", "goals": []},
            "requirements": {"functional": [], "non_functional": []},
            "constraints": [],
            "decisions": [],
            "items": [],
            "risks": [],
            "scope": {"in_scope": [], "out_of_scope": [], "deferred": []},
            "architecture": {"services": [], "integrations": [], "data_flow": ""},
            "conversation_history": [],
            "_metadata": {"created": None, "last_updated": None, "exchange_count": 0},
        }
        with open(state_dir / "discovery.yaml", "w") as f:
            yaml.dump(legacy, f)

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        # New keys should be present with defaults
        assert ds.get_artifact_hashes() == {}
        assert ds.get_context_hash() == ""
        assert ds.state["project"]["summary"] == "test"


class TestSectionLoopSlashCommands:
    """Verify that slash commands do NOT consume inner loop iterations."""

    def test_slash_commands_do_not_advance_topic(
        self, mock_agent_context, mock_registry, mock_biz_agent, tmp_path,
    ):
        """Issuing 5+ slash commands should NOT mark a topic as answered."""
        from azext_prototype.stages.discovery_state import DiscoveryState, Topic

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_topics([
            Topic(heading="Auth", detail="## Auth\nHow?", kind="topic", status="pending", answer_exchange=None),
            Topic(heading="Data", detail="## Data\nWhat?", kind="topic", status="pending", answer_exchange=None),
        ])
        ds.save()

        # AI identifies no new topics (re-entry), then confirms section after real answer
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("Yes"),   # Auth confirmed after real answer
            _make_response("Yes"),   # Data confirmed after real answer
            _make_response("## Summary\nDone."),
        ]

        ds2 = DiscoveryState(str(tmp_path))
        # 6 slash commands first (more than old limit of 5), then a real answer, then done
        inputs = iter([
            "/status", "/open", "/confirmed", "/status", "/open", "/confirmed",
            "Use Azure AD B2C",  # Real answer for Auth
            "Use Cosmos DB",     # Real answer for Data
            "done",
        ])

        session = DiscoverySession(mock_agent_context, mock_registry, discovery_state=ds2)
        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        topics = ds2.topics
        # Auth should be answered (via real AI exchange), not prematurely
        assert topics[0].status == "answered"
        assert topics[0].answer_exchange is not None
        # Data should also be answered
        assert topics[1].status == "answered"

    def test_empty_input_does_not_advance_topic(
        self, mock_agent_context, mock_registry, mock_biz_agent, tmp_path,
    ):
        """Pressing Enter 5+ times should NOT mark a topic as answered."""
        from azext_prototype.stages.discovery_state import DiscoveryState, Topic

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_topics([
            Topic(heading="Auth", detail="## Auth\nHow?", kind="topic", status="pending", answer_exchange=None),
        ])
        ds.save()

        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("Yes"),  # Auth confirmed after real answer
            _make_response("## Summary\nDone."),
        ]

        ds2 = DiscoveryState(str(tmp_path))
        # 6 empty inputs, then a real answer, then done
        inputs = iter(["", "", "", "", "", "", "Use Azure AD", "done"])

        session = DiscoverySession(mock_agent_context, mock_registry, discovery_state=ds2)
        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        topics = ds2.topics
        assert topics[0].status == "answered"
        assert topics[0].answer_exchange is not None


class TestRestartSignal:
    """Verify /restart breaks out of section loop."""

    def test_restart_returns_signal_from_handler(self, mock_agent_context, mock_registry, mock_biz_agent, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState

        ds = DiscoveryState(str(tmp_path))
        ds.load()

        mock_agent_context.ai_provider.chat.return_value = _make_response("Welcome!")
        session = DiscoverySession(mock_agent_context, mock_registry, discovery_state=ds)
        # Set up I/O attributes that _handle_slash_command needs
        session._print = lambda x: None
        session._use_styled = False
        session._status_fn = None
        session._response_fn = None
        session._messages = []
        result = session._handle_slash_command("/restart")
        assert result == "restart"

    def test_non_restart_returns_none(self, mock_agent_context, mock_registry, mock_biz_agent):
        session = DiscoverySession(mock_agent_context, mock_registry)
        session._print = lambda x: None
        session._use_styled = False
        result = session._handle_slash_command("/status")
        assert result is None

        result = session._handle_slash_command("/open")
        assert result is None


class TestTopicAtExchange:
    """Verify topic_at_exchange() cross-references exchanges with topics."""

    def test_finds_topic_at_exchange(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState, TrackedItem

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_items([
            TrackedItem(heading="Auth", detail="Q?", kind="topic", status="answered", answer_exchange=2),
            TrackedItem(heading="Data", detail="Q?", kind="topic", status="answered", answer_exchange=4),
            TrackedItem(heading="Scale", detail="Q?", kind="topic", status="pending", answer_exchange=None),
        ])

        assert ds.topic_at_exchange(1) == "Auth"
        assert ds.topic_at_exchange(2) == "Auth"
        assert ds.topic_at_exchange(3) == "Data"
        assert ds.topic_at_exchange(4) == "Data"
        assert ds.topic_at_exchange(5) is None  # Beyond all answered topics

    def test_no_answered_topics(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState, TrackedItem

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_items([
            TrackedItem(heading="Auth", detail="Q?", kind="topic", status="pending", answer_exchange=None),
        ])

        assert ds.topic_at_exchange(1) is None

    def test_empty_state(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        assert ds.topic_at_exchange(1) is None


# ======================================================================
# _chat_lightweight edge cases
# ======================================================================


class TestChatLightweight:
    """Tests for _chat_lightweight — minimal AI call for classification tasks."""

    def test_empty_content(self, mock_agent_context, mock_registry):
        """Empty string content should still work."""
        mock_agent_context.ai_provider.chat.return_value = _make_response("[NO_NEW_TOPICS]")
        session = DiscoverySession(mock_agent_context, mock_registry)

        result = session._chat_lightweight("")
        assert result == "[NO_NEW_TOPICS]"

        # Verify it used a minimal system prompt (not the full governance payload)
        call_args = mock_agent_context.ai_provider.chat.call_args
        messages = call_args[0][0]
        system_msgs = [m for m in messages if m.role == "system"]
        assert len(system_msgs) == 1
        assert len(system_msgs[0].content) < 200  # Lightweight — not 69KB

    def test_does_not_add_to_messages(self, mock_agent_context, mock_registry):
        """_chat_lightweight is ephemeral — should NOT add to self._messages."""
        mock_agent_context.ai_provider.chat.return_value = _make_response("analysis result")
        session = DiscoverySession(mock_agent_context, mock_registry)

        initial_count = len(session._messages)
        session._chat_lightweight("classify this")
        assert len(session._messages) == initial_count

    def test_records_tokens(self, mock_agent_context, mock_registry):
        """Token usage from lightweight calls should be tracked."""
        mock_agent_context.ai_provider.chat.return_value = _make_response("result")
        session = DiscoverySession(mock_agent_context, mock_registry)

        session._chat_lightweight("test prompt")
        # TokenTracker.record was called (uses AIResponse)
        assert session._token_tracker._turn_count >= 1

    def test_uses_low_temperature(self, mock_agent_context, mock_registry):
        """Lightweight calls use temperature=0.3 for determinism."""
        mock_agent_context.ai_provider.chat.return_value = _make_response("ok")
        session = DiscoverySession(mock_agent_context, mock_registry)

        session._chat_lightweight("test")
        call_kwargs = mock_agent_context.ai_provider.chat.call_args[1]
        assert call_kwargs.get("temperature") == 0.3


# ======================================================================
# _handle_incremental_context edge cases
# ======================================================================


class TestHandleIncrementalContext:
    """Tests for _handle_incremental_context — re-entry topic detection."""

    def test_returns_false_no_topics_no_seed_context(
        self, mock_agent_context, mock_registry,
    ):
        """When AI says [NO_NEW_TOPICS] and no seed_context, returns False."""
        mock_agent_context.ai_provider.chat.return_value = _make_response("[NO_NEW_TOPICS]")
        session = DiscoverySession(mock_agent_context, mock_registry)

        result = session._handle_incremental_context(
            seed_context="",
            artifacts="some artifact text",
            artifact_images=None,
            _print=lambda x: None,
            use_styled=False,
            status_fn=None,
        )
        assert result is False

    def test_returns_false_no_topics_with_seed_context(
        self, mock_agent_context, mock_registry,
    ):
        """When AI says [NO_NEW_TOPICS] with seed_context, records decision."""
        mock_agent_context.ai_provider.chat.return_value = _make_response("[NO_NEW_TOPICS]")
        session = DiscoverySession(mock_agent_context, mock_registry)

        printed = []
        result = session._handle_incremental_context(
            seed_context="Change app name to Contoso",
            artifacts="",
            artifact_images=None,
            _print=printed.append,
            use_styled=False,
            status_fn=None,
        )
        assert result is False
        # Seed context should be recorded as a confirmed decision
        decisions = session._discovery_state.state["decisions"]
        assert "Change app name to Contoso" in decisions
        assert any("Context recorded" in p for p in printed)

    def test_returns_true_when_new_topics_found(
        self, mock_agent_context, mock_registry,
    ):
        """When AI returns new sections, topics are appended and returns True."""
        new_topics_response = (
            "## Authentication Strategy\n"
            "1. What identity provider?\n"
            "2. SSO required?\n\n"
            "## Data Residency\n"
            "1. Which region?\n"
            "2. Compliance needs?\n"
        )
        mock_agent_context.ai_provider.chat.return_value = _make_response(new_topics_response)
        session = DiscoverySession(mock_agent_context, mock_registry)

        result = session._handle_incremental_context(
            seed_context="Add GDPR compliance",
            artifacts="",
            artifact_images=None,
            _print=lambda x: None,
            use_styled=False,
            status_fn=None,
        )
        assert result is True
        # Topics should be appended to discovery state
        assert session._discovery_state.has_items

    def test_no_parseable_sections_records_decision(
        self, mock_agent_context, mock_registry,
    ):
        """When AI response has no parseable sections, seed_context is saved as decision."""
        mock_agent_context.ai_provider.chat.return_value = _make_response(
            "The new information is already covered by existing topics."
        )
        session = DiscoverySession(mock_agent_context, mock_registry)

        result = session._handle_incremental_context(
            seed_context="Use Redis for caching",
            artifacts="",
            artifact_images=None,
            _print=lambda x: None,
            use_styled=False,
            status_fn=None,
        )
        assert result is False
        decisions = session._discovery_state.state["decisions"]
        assert "Use Redis for caching" in decisions


# ======================================================================
# add_confirmed_decision deduplication
# ======================================================================


class TestAddConfirmedDecisionDedup:
    """Test that add_confirmed_decision deduplicates."""

    def test_same_decision_not_duplicated(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState

        ds = DiscoveryState(str(tmp_path))
        ds.load()

        ds.add_confirmed_decision("Use Redis for caching")
        ds.add_confirmed_decision("Use Redis for caching")
        ds.add_confirmed_decision("Use Redis for caching")

        assert ds.state["decisions"].count("Use Redis for caching") == 1

    def test_different_decisions_both_stored(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState

        ds = DiscoveryState(str(tmp_path))
        ds.load()

        ds.add_confirmed_decision("Use Redis")
        ds.add_confirmed_decision("Use PostgreSQL")

        assert "Use Redis" in ds.state["decisions"]
        assert "Use PostgreSQL" in ds.state["decisions"]
        assert len(ds.state["decisions"]) == 2

    def test_empty_string_not_stored(self, tmp_path):
        from azext_prototype.stages.discovery_state import DiscoveryState

        ds = DiscoveryState(str(tmp_path))
        ds.load()

        ds.add_confirmed_decision("")
        assert len(ds.state["decisions"]) == 0


# ======================================================================
# topic_at_exchange — overlapping exchanges
# ======================================================================


class TestTopicAtExchangeOverlapping:
    """Test topic_at_exchange with overlapping and edge case exchange ranges."""

    def test_overlapping_exchange_numbers(self, tmp_path):
        """When multiple topics have the same answer_exchange, first by sort wins."""
        from azext_prototype.stages.discovery_state import DiscoveryState, TrackedItem

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_items([
            TrackedItem(heading="Auth", detail="Q?", kind="topic", status="answered", answer_exchange=2),
            TrackedItem(heading="Data", detail="Q?", kind="topic", status="answered", answer_exchange=2),
            TrackedItem(heading="Scale", detail="Q?", kind="topic", status="answered", answer_exchange=5),
        ])

        # Exchange 2 maps to the first answered topic with answer_exchange >= 2
        result = ds.topic_at_exchange(2)
        assert result in ("Auth", "Data")  # Either is valid — both have exchange 2

    def test_exchange_between_topics(self, tmp_path):
        """Exchange number between two answer_exchanges maps to the later topic."""
        from azext_prototype.stages.discovery_state import DiscoveryState, TrackedItem

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_items([
            TrackedItem(heading="Auth", detail="Q?", kind="topic", status="answered", answer_exchange=2),
            TrackedItem(heading="Data", detail="Q?", kind="topic", status="answered", answer_exchange=5),
        ])

        # Exchange 3 is after Auth (2) but before Data (5) → Data
        assert ds.topic_at_exchange(3) == "Data"

    def test_exchange_zero(self, tmp_path):
        """Exchange 0 should return the first topic."""
        from azext_prototype.stages.discovery_state import DiscoveryState, TrackedItem

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_items([
            TrackedItem(heading="Auth", detail="Q?", kind="topic", status="answered", answer_exchange=2),
        ])

        assert ds.topic_at_exchange(0) == "Auth"

    def test_exchange_beyond_all_returns_none(self, tmp_path):
        """Exchange after all answer_exchanges returns None (free-form)."""
        from azext_prototype.stages.discovery_state import DiscoveryState, TrackedItem

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_items([
            TrackedItem(heading="Auth", detail="Q?", kind="topic", status="answered", answer_exchange=2),
            TrackedItem(heading="Data", detail="Q?", kind="topic", status="answered", answer_exchange=5),
        ])

        assert ds.topic_at_exchange(10) is None

    def test_single_topic_covers_all_earlier_exchanges(self, tmp_path):
        """A single answered topic covers all exchanges up to its answer_exchange."""
        from azext_prototype.stages.discovery_state import DiscoveryState, TrackedItem

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_items([
            TrackedItem(heading="Auth", detail="Q?", kind="topic", status="answered", answer_exchange=5),
        ])

        assert ds.topic_at_exchange(1) == "Auth"
        assert ds.topic_at_exchange(3) == "Auth"
        assert ds.topic_at_exchange(5) == "Auth"
        assert ds.topic_at_exchange(6) is None

    def test_mixed_answered_and_pending(self, tmp_path):
        """Pending topics (no answer_exchange) don't appear in results."""
        from azext_prototype.stages.discovery_state import DiscoveryState, TrackedItem

        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.set_items([
            TrackedItem(heading="Auth", detail="Q?", kind="topic", status="answered", answer_exchange=2),
            TrackedItem(heading="Pending Topic", detail="Q?", kind="topic", status="pending", answer_exchange=None),
            TrackedItem(heading="Data", detail="Q?", kind="topic", status="answered", answer_exchange=5),
        ])

        assert ds.topic_at_exchange(1) == "Auth"
        assert ds.topic_at_exchange(3) == "Data"
        assert ds.topic_at_exchange(6) is None
