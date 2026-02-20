"""Tests for azext_prototype.stages.discovery — organic multi-turn conversation."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from azext_prototype.agents.base import AgentCapability, AgentContext
from azext_prototype.ai.provider import AIMessage, AIResponse
from azext_prototype.stages.discovery import (
    DiscoverySession,
    DiscoveryResult,
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
def mock_agent_context():
    ctx = AgentContext(
        project_config={"project": {"name": "test", "location": "eastus"}},
        project_dir="/tmp/test",
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
            "## Architecture\nMock output"
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
            "## Architecture\nShould not appear"
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
            "## Architecture\nDesign with overrides"
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
        self, mock_agent_context, mock_registry, mock_biz_agent, capsys,
    ):
        """/why with no argument should show usage hint, not crash."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What would you like to build?"),
            _make_response("## Summary\nNothing yet."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["/why", "done"])

        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        captured = capsys.readouterr()
        assert "Usage" in captured.out or "/why" in captured.out

    def test_why_with_matching_query(
        self, mock_agent_context, mock_registry, mock_biz_agent, capsys,
    ):
        """/why should find exchanges mentioning the queried topic."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What would you like to build?"),
            _make_response("Managed identity is the recommended auth approach."),
            _make_response("## Summary\nAll confirmed."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["Use managed identity for auth", "/why managed identity", "done"])

        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        captured = capsys.readouterr()
        assert "Exchange" in captured.out

    def test_why_no_matches(
        self, mock_agent_context, mock_registry, mock_biz_agent, capsys,
    ):
        """/why with no matching history should show 'no exchanges found'."""
        mock_agent_context.ai_provider.chat.side_effect = [
            _make_response("What would you like to build?"),
            _make_response("## Summary\nNothing yet."),
        ]

        session = DiscoverySession(mock_agent_context, mock_registry)
        inputs = iter(["/why kubernetes", "done"])

        session.run(
            input_fn=lambda _: next(inputs),
            print_fn=lambda x: None,
        )

        captured = capsys.readouterr()
        assert "No exchanges found" in captured.out


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
