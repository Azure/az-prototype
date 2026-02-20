"""Tests for discovery_state scope management."""

import pytest
import yaml

from azext_prototype.stages.discovery_state import DiscoveryState, _default_discovery_state


class TestDiscoveryStateScope:
    """Test the scope fields in DiscoveryState."""

    def test_default_state_has_scope(self):
        state = _default_discovery_state()
        assert "scope" in state
        assert state["scope"] == {
            "in_scope": [],
            "out_of_scope": [],
            "deferred": [],
        }

    def test_merge_learnings_with_scope(self, tmp_path):
        ds = DiscoveryState(str(tmp_path))
        ds.load()

        learnings = {
            "scope": {
                "in_scope": ["REST API", "SQL Database"],
                "out_of_scope": ["Mobile app"],
                "deferred": ["CI/CD pipeline"],
            },
        }
        ds.merge_learnings(learnings)

        assert ds.state["scope"]["in_scope"] == ["REST API", "SQL Database"]
        assert ds.state["scope"]["out_of_scope"] == ["Mobile app"]
        assert ds.state["scope"]["deferred"] == ["CI/CD pipeline"]

    def test_merge_learnings_deduplicates_scope(self, tmp_path):
        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.state["scope"]["in_scope"] = ["REST API"]

        learnings = {
            "scope": {
                "in_scope": ["REST API", "SQL Database"],
            },
        }
        ds.merge_learnings(learnings)

        assert ds.state["scope"]["in_scope"] == ["REST API", "SQL Database"]

    def test_merge_learnings_partial_scope(self, tmp_path):
        ds = DiscoveryState(str(tmp_path))
        ds.load()

        learnings = {
            "scope": {
                "in_scope": ["API endpoints"],
            },
        }
        ds.merge_learnings(learnings)

        assert ds.state["scope"]["in_scope"] == ["API endpoints"]
        assert ds.state["scope"]["out_of_scope"] == []
        assert ds.state["scope"]["deferred"] == []

    def test_merge_learnings_without_scope(self, tmp_path):
        """Learnings without scope should not break merge."""
        ds = DiscoveryState(str(tmp_path))
        ds.load()

        learnings = {
            "project": {"summary": "Test", "goals": ["Goal 1"]},
        }
        ds.merge_learnings(learnings)

        assert ds.state["scope"]["in_scope"] == []

    def test_format_as_context_includes_scope(self, tmp_path):
        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds._loaded = True
        ds.state["scope"] = {
            "in_scope": ["REST API"],
            "out_of_scope": ["Mobile app"],
            "deferred": ["CI/CD"],
        }

        context = ds.format_as_context()
        assert "## Prototype Scope" in context
        assert "### In Scope" in context
        assert "REST API" in context
        assert "### Out of Scope" in context
        assert "Mobile app" in context
        assert "### Deferred / Future Work" in context
        assert "CI/CD" in context

    def test_format_as_context_partial_scope(self, tmp_path):
        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds._loaded = True
        ds.state["scope"]["in_scope"] = ["REST API"]

        context = ds.format_as_context()
        assert "### In Scope" in context
        assert "### Out of Scope" not in context
        assert "### Deferred" not in context

    def test_format_as_context_omits_empty_scope(self, tmp_path):
        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds._loaded = True
        ds.state["project"]["summary"] = "Test project"

        context = ds.format_as_context()
        assert "Prototype Scope" not in context

    def test_format_as_context_falls_back_to_conversation(self, tmp_path):
        """When structured fields are empty, format_as_context uses conversation history."""
        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds._loaded = True
        # Structured fields are all empty (default), but conversation has content
        ds.state["conversation_history"] = [
            {"exchange": 1, "assistant": "Tell me more."},
            {
                "exchange": 2,
                "assistant": (
                    "## Project Summary\nA web app for email drafting.\n\n"
                    "## Confirmed Functional Requirements\n- Feature A\n\n"
                    "[READY]"
                ),
            },
        ]

        context = ds.format_as_context()
        assert "## Project Summary" in context
        assert "email drafting" in context
        assert "Feature A" in context
        assert "[READY]" not in context

    def test_format_as_context_prefers_structured_fields(self, tmp_path):
        """When structured fields are populated, those are used instead of conversation."""
        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds._loaded = True
        ds.state["project"]["summary"] = "Structured summary"
        ds.state["conversation_history"] = [
            {
                "exchange": 1,
                "assistant": "## Project Summary\nConversation summary.\n\n## Confirmed Functional Requirements\n- X",
            },
        ]

        context = ds.format_as_context()
        assert "Structured summary" in context
        assert "Conversation summary" not in context

    def test_extract_conversation_summary(self, tmp_path):
        """extract_conversation_summary returns last assistant message with summary headings."""
        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.state["conversation_history"] = [
            {"exchange": 1, "assistant": "Tell me more."},
            {
                "exchange": 2,
                "assistant": "## Project Summary\nA web app.\n\n[READY]",
            },
        ]

        result = ds.extract_conversation_summary()
        assert "## Project Summary" in result
        assert "[READY]" not in result

    def test_extract_conversation_summary_empty_history(self, tmp_path):
        ds = DiscoveryState(str(tmp_path))
        ds.load()

        assert ds.extract_conversation_summary() == ""

    def test_scope_persists_to_yaml(self, tmp_path):
        ds = DiscoveryState(str(tmp_path))
        ds.load()
        ds.state["scope"]["in_scope"] = ["API endpoints"]
        ds.state["scope"]["out_of_scope"] = ["Mobile app"]
        ds.save()

        ds2 = DiscoveryState(str(tmp_path))
        ds2.load()
        assert ds2.state["scope"]["in_scope"] == ["API endpoints"]
        assert ds2.state["scope"]["out_of_scope"] == ["Mobile app"]
        assert ds2.state["scope"]["deferred"] == []
