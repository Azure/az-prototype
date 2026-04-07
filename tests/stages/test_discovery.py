"""Tests for discovery.py — branch coverage for section header extraction,
slash command routing, opening message construction, vision content array
building, topic detection, context change handling, conversation state
management, parse_sections, and the main run loop.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from azext_prototype.agents.base import AgentCapability, AgentContext

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def discovery_context(project_with_config, sample_config):
    provider = MagicMock()
    provider.provider_name = "github-models"
    provider.default_model = "gpt-4o"
    provider.chat.return_value = MagicMock(
        content="I understand. Let me ask some questions.",
        model="test",
        usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
    )
    return AgentContext(
        project_config=sample_config,
        project_dir=str(project_with_config),
        ai_provider=provider,
    )


@pytest.fixture
def discovery_registry():
    registry = MagicMock()

    mock_biz = MagicMock()
    mock_biz.name = "biz-analyst"
    mock_biz.get_system_messages.return_value = []
    mock_biz._temperature = 0.7
    mock_biz._max_tokens = 4096

    mock_architect = MagicMock()
    mock_architect.name = "cloud-architect"

    mock_qa = MagicMock()
    mock_qa.name = "qa-engineer"

    def find_by_cap(cap):
        mapping = {
            AgentCapability.BIZ_ANALYSIS: [mock_biz],
            AgentCapability.ARCHITECT: [mock_architect],
            AgentCapability.QA: [mock_qa],
        }
        return mapping.get(cap, [])

    registry.find_by_capability.side_effect = find_by_cap
    return registry


# ------------------------------------------------------------------
# extract_section_headers
# ------------------------------------------------------------------


class TestExtractSectionHeaders:
    def test_extracts_h2_headings(self):
        from azext_prototype.stages.discovery import extract_section_headers

        text = "## Authentication\nContent\n## Data Storage\nContent"
        headers = extract_section_headers(text)
        assert len(headers) == 2
        assert headers[0] == ("Authentication", 2)
        assert headers[1] == ("Data Storage", 2)

    def test_filters_skip_headings(self):
        from azext_prototype.stages.discovery import extract_section_headers

        text = "## Summary\nContent\n## Next Steps\nContent\n## Real Topic\nContent"
        headers = extract_section_headers(text)
        assert len(headers) == 1
        assert headers[0][0] == "Real Topic"

    def test_filters_short_headings(self):
        from azext_prototype.stages.discovery import extract_section_headers

        text = "## AB\nContent\n## Long Enough\nContent"
        headers = extract_section_headers(text)
        assert len(headers) == 1
        assert headers[0][0] == "Long Enough"

    def test_deduplicates_headings(self):
        from azext_prototype.stages.discovery import extract_section_headers

        text = "## Auth\nContent\n## Auth\nDuplicate"
        headers = extract_section_headers(text)
        assert len(headers) == 1

    def test_bold_headings(self):
        from azext_prototype.stages.discovery import extract_section_headers

        text = "**Authentication Model**\nContent\n**Data Layer**\nContent"
        headers = extract_section_headers(text)
        assert len(headers) == 2
        assert headers[0][0] == "Authentication Model"

    def test_h3_headings_excluded(self):
        from azext_prototype.stages.discovery import extract_section_headers

        text = "## Top Level\nContent\n### Sub Section\nContent"
        headers = extract_section_headers(text)
        assert len(headers) == 1
        assert headers[0][0] == "Top Level"

    def test_empty_text(self):
        from azext_prototype.stages.discovery import extract_section_headers

        assert extract_section_headers("") == []

    def test_no_headings(self):
        from azext_prototype.stages.discovery import extract_section_headers

        assert extract_section_headers("Just plain text with no headings.") == []


# ------------------------------------------------------------------
# parse_sections
# ------------------------------------------------------------------


class TestParseSections:
    def test_basic_sections(self):
        from azext_prototype.stages.discovery import parse_sections

        text = "Intro text\n\n## Section A\nContent A\n\n## Section B\nContent B"
        preamble, sections = parse_sections(text)
        assert "Intro text" in preamble
        assert len(sections) == 2
        assert sections[0].heading == "Section A"
        assert sections[1].heading == "Section B"

    def test_no_sections_returns_full_text(self):
        from azext_prototype.stages.discovery import parse_sections

        text = "Just a paragraph without headings."
        preamble, sections = parse_sections(text)
        assert preamble == text
        assert sections == []

    def test_skip_headings_filtered(self):
        from azext_prototype.stages.discovery import parse_sections

        text = "## Summary\nSkip this\n## Real Section\nKeep this"
        preamble, sections = parse_sections(text)
        assert len(sections) == 1
        assert sections[0].heading == "Real Section"

    def test_task_id_generated(self):
        from azext_prototype.stages.discovery import parse_sections

        text = "## Data Storage\nContent"
        _, sections = parse_sections(text)
        assert len(sections) == 1
        assert sections[0].task_id == "design-section-data-storage"

    def test_h3_folded_into_parent(self):
        from azext_prototype.stages.discovery import parse_sections

        text = "## Parent\nP content\n### Child\nC content\n## Another\nA content"
        _, sections = parse_sections(text)
        assert len(sections) == 2
        assert "Child" in sections[0].content
        assert sections[1].heading == "Another"

    def test_bold_heading_sections(self):
        from azext_prototype.stages.discovery import parse_sections

        text = "Preamble\n\n**Security Model**\nSecurity content\n\n**Deployment**\nDeploy content"
        preamble, sections = parse_sections(text)
        assert len(sections) == 2
        assert sections[0].heading == "Security Model"

    def test_only_h3_returns_no_sections(self):
        from azext_prototype.stages.discovery import parse_sections

        text = "### Sub Section\nContent"
        preamble, sections = parse_sections(text)
        assert sections == []
        assert "Sub Section" in preamble


# ------------------------------------------------------------------
# _build_opening
# ------------------------------------------------------------------


class TestBuildOpening:
    def _make_session(self, ctx, registry):
        from azext_prototype.stages.discovery import DiscoverySession

        return DiscoverySession(ctx, registry)

    def test_no_context_no_artifacts(self, discovery_context, discovery_registry):
        session = self._make_session(discovery_context, discovery_registry)
        opening = session._build_opening("", "", "")
        assert isinstance(opening, str)
        assert "Azure prototype" in opening

    def test_seed_context_only(self, discovery_context, discovery_registry):
        session = self._make_session(discovery_context, discovery_registry)
        opening = session._build_opening("Build an API", "", "")
        assert "Build an API" in opening

    def test_artifacts_only(self, discovery_context, discovery_registry):
        session = self._make_session(discovery_context, discovery_registry)
        opening = session._build_opening("", "Requirements doc content", "")
        assert "requirement documents" in opening.lower()

    def test_seed_and_artifacts(self, discovery_context, discovery_registry):
        session = self._make_session(discovery_context, discovery_registry)
        opening = session._build_opening("Build an API", "Doc content", "")
        assert "Build an API" in opening
        assert "Doc content" in opening

    def test_existing_context_included(self, discovery_context, discovery_registry):
        session = self._make_session(discovery_context, discovery_registry)
        opening = session._build_opening("New info", "", "Previous session learnings")
        assert "Previous session learnings" in opening
        assert "conflicts" in opening.lower()

    def test_images_produce_multimodal(self, discovery_context, discovery_registry):
        session = self._make_session(discovery_context, discovery_registry)
        images = [{"filename": "test.png", "data": "abc123", "mime": "image/png"}]
        opening = session._build_opening("Context", "", "", images=images)
        assert isinstance(opening, list)
        assert opening[0]["type"] == "text"
        assert opening[1]["type"] == "image_url"
        assert "abc123" in opening[1]["image_url"]["url"]

    def test_no_context_with_existing_only(self, discovery_context, discovery_registry):
        session = self._make_session(discovery_context, discovery_registry)
        opening = session._build_opening("", "", "existing")
        assert "existing" in opening

    def test_multiple_images(self, discovery_context, discovery_registry):
        session = self._make_session(discovery_context, discovery_registry)
        images = [
            {"filename": "a.png", "data": "aaa", "mime": "image/png"},
            {"filename": "b.jpg", "data": "bbb", "mime": "image/jpeg"},
        ]
        opening = session._build_opening("", "artifacts", "", images=images)
        assert isinstance(opening, list)
        assert len(opening) == 3  # text + 2 images


# ------------------------------------------------------------------
# DiscoveryResult
# ------------------------------------------------------------------


class TestDiscoveryResult:
    def test_default_not_cancelled(self):
        from azext_prototype.stages.discovery import DiscoveryResult

        result = DiscoveryResult(
            requirements="reqs",
            conversation=[],
            policy_overrides=[],
            exchange_count=5,
        )
        assert result.cancelled is False
        assert result.exchange_count == 5

    def test_cancelled_result(self):
        from azext_prototype.stages.discovery import DiscoveryResult

        result = DiscoveryResult(
            requirements="",
            conversation=[],
            policy_overrides=[],
            exchange_count=0,
            cancelled=True,
        )
        assert result.cancelled is True


# ------------------------------------------------------------------
# Session run — no biz-agent fallback
# ------------------------------------------------------------------


class TestDiscoverySessionNoBizAgent:
    def test_no_biz_agent_prompts_user(self, discovery_context):
        from azext_prototype.stages.discovery import DiscoverySession

        registry = MagicMock()
        registry.find_by_capability.return_value = []

        session = DiscoverySession(discovery_context, registry)
        result = session.run(
            seed_context="test",
            input_fn=lambda p: "my requirements",
            print_fn=lambda m: None,
        )
        assert result.requirements == "my requirements"
        assert result.exchange_count == 0

    def test_no_biz_agent_eof(self, discovery_context):
        from azext_prototype.stages.discovery import DiscoverySession

        registry = MagicMock()
        registry.find_by_capability.return_value = []

        session = DiscoverySession(discovery_context, registry)

        def raise_eof(p):
            raise EOFError

        result = session.run(
            seed_context="",
            input_fn=raise_eof,
            print_fn=lambda m: None,
        )
        assert result.requirements == ""


# ------------------------------------------------------------------
# Session run — quit/done in main loop
# ------------------------------------------------------------------


class TestDiscoverySessionMainLoop:
    def _make_session(self, ctx, registry):
        from azext_prototype.stages.discovery import DiscoverySession

        return DiscoverySession(ctx, registry)

    def test_quit_returns_cancelled(self, discovery_context, discovery_registry):
        session = self._make_session(discovery_context, discovery_registry)

        # First response from AI has no sections (plain text), so we enter free-form loop
        discovery_context.ai_provider.chat.return_value = MagicMock(
            content="What would you like to build?",
            model="test",
            usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
        )

        inputs = iter(["quit"])
        result = session.run(
            seed_context="Build an API",
            input_fn=lambda p: next(inputs),
            print_fn=lambda m: None,
        )
        assert result.cancelled is True

    def test_done_produces_summary(self, discovery_context, discovery_registry):
        session = self._make_session(discovery_context, discovery_registry)

        call_count = [0]

        def mock_chat(messages, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(
                    content="What would you like to build?",
                    model="test",
                    usage={"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
                )
            # Summary call
            return MagicMock(
                content="## Requirements Summary\nBuild an API with auth.",
                model="test",
                usage={"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
            )

        discovery_context.ai_provider.chat.side_effect = mock_chat

        inputs = iter(["done"])
        result = session.run(
            seed_context="Build an API",
            input_fn=lambda p: next(inputs),
            print_fn=lambda m: None,
        )
        assert result.cancelled is False
        assert result.requirements  # Should have summary text

    def test_eof_in_main_loop_ends_session(self, discovery_context, discovery_registry):
        session = self._make_session(discovery_context, discovery_registry)

        discovery_context.ai_provider.chat.return_value = MagicMock(
            content="Plain response no sections",
            model="test",
            usage={"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
        )

        def raise_eof(p):
            raise EOFError

        result = session.run(
            seed_context="test",
            input_fn=raise_eof,
            print_fn=lambda m: None,
        )
        # Should produce a summary (not cancelled)
        assert result is not None

    def test_slash_command_help(self, discovery_context, discovery_registry):
        session = self._make_session(discovery_context, discovery_registry)

        discovery_context.ai_provider.chat.return_value = MagicMock(
            content="What would you like to build?",
            model="test",
            usage={"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
        )

        inputs = iter(["/help", "done"])
        result = session.run(
            seed_context="test",
            input_fn=lambda p: next(inputs),
            print_fn=lambda m: None,
        )
        assert result is not None
        assert not result.cancelled


# ------------------------------------------------------------------
# _chat — vision fallback
# ------------------------------------------------------------------


class TestChatVisionFallback:
    def test_vision_failure_degrades_to_text(self, discovery_context, discovery_registry):
        from azext_prototype.stages.discovery import DiscoverySession

        session = DiscoverySession(discovery_context, discovery_registry)

        call_count = [0]

        def mock_chat(messages, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Vision not supported")
            return MagicMock(
                content="Text fallback response",
                model="test",
                usage={"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
            )

        discovery_context.ai_provider.chat.side_effect = mock_chat

        content = [
            {"type": "text", "text": "Review these files"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]
        response = session._chat(content)
        assert response == "Text fallback response"
        # Should have fallen back to text-only
        assert call_count[0] == 2


# ------------------------------------------------------------------
# _chat_lightweight
# ------------------------------------------------------------------


class TestChatLightweight:
    def test_returns_ai_content(self, discovery_context, discovery_registry):
        from azext_prototype.stages.discovery import DiscoverySession

        session = DiscoverySession(discovery_context, discovery_registry)

        discovery_context.ai_provider.chat.return_value = MagicMock(
            content="Lightweight response",
            model="test",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )

        result = session._chat_lightweight("classify this text")
        assert result == "Lightweight response"

    def test_does_not_append_to_messages(self, discovery_context, discovery_registry):
        from azext_prototype.stages.discovery import DiscoverySession

        session = DiscoverySession(discovery_context, discovery_registry)

        discovery_context.ai_provider.chat.return_value = MagicMock(
            content="response",
            model="test",
            usage={},
        )

        initial_len = len(session._messages)
        session._chat_lightweight("test")
        assert len(session._messages) == initial_len


# ------------------------------------------------------------------
# _handle_incremental_context
# ------------------------------------------------------------------


class TestHandleIncrementalContext:
    def _make_session(self, ctx, registry):
        from azext_prototype.stages.discovery import DiscoverySession

        return DiscoverySession(ctx, registry)

    def test_no_new_topics_records_decision(self, discovery_context, discovery_registry):
        session = self._make_session(discovery_context, discovery_registry)

        discovery_context.ai_provider.chat.return_value = MagicMock(
            content="[NO_NEW_TOPICS]",
            model="test",
            usage={},
        )

        result = session._handle_incremental_context("Use Redis for caching", "", None, lambda m: None, False, None)
        assert result is False

    def test_new_topics_added(self, discovery_context, discovery_registry):
        session = self._make_session(discovery_context, discovery_registry)

        discovery_context.ai_provider.chat.return_value = MagicMock(
            content="## Caching Strategy\nHow should Redis be configured?\n\n## Performance\nWhat SLA is needed?",
            model="test",
            usage={},
        )

        result = session._handle_incremental_context("Add Redis caching", "", None, lambda m: None, False, None)
        assert result is True
