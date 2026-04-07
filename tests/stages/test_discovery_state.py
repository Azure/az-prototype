"""Tests for DiscoveryState — legacy migration, exchange handling, state persistence.

Covers:
- Legacy state migration (topics, open_items, confirmed_items → unified items)
- update_from_exchange with str vs list content
- Image stripping during persistence (multi-modal content arrays)
- Item management (add, resolve, mark, append, dedup)
- Format methods (open_items, confirmed_items, status_summary, as_context)
- Conversation summary extraction
- Search history
- Topic at exchange
- Artifact inventory
- Context hash
"""

from pathlib import Path

import pytest
import yaml

from azext_prototype.stages.discovery_state import (
    DiscoveryState,
    TrackedItem,
    _default_discovery_state,
)

# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def disco_state(tmp_project):
    ds = DiscoveryState(str(tmp_project))
    return ds


@pytest.fixture
def disco_state_with_items(disco_state):
    """State with some items pre-loaded."""
    disco_state._state["items"] = [
        {
            "heading": "Auth approach",
            "detail": "How to authenticate?",
            "kind": "topic",
            "status": "pending",
            "answer_exchange": None,
        },
        {
            "heading": "DB choice",
            "detail": "Which database?",
            "kind": "decision",
            "status": "confirmed",
            "answer_exchange": 2,
        },
        {"heading": "Hosting", "detail": "Where to host?", "kind": "topic", "status": "answered", "answer_exchange": 3},
    ]
    disco_state._loaded = True
    return disco_state


# ======================================================================
# Legacy migration
# ======================================================================


class TestLegacyMigration:
    """Test _migrate_legacy_state converts old fields to unified items."""

    def test_migrate_topics(self, disco_state):
        """Old 'topics' key migrates to items with kind=topic."""
        disco_state._state["topics"] = [
            {"heading": "Auth", "questions": "How to authenticate?", "status": "pending"},
        ]
        disco_state._state["items"] = []
        disco_state._migrate_legacy_state()
        assert len(disco_state._state["items"]) == 1
        assert disco_state._state["items"][0]["kind"] == "topic"
        assert disco_state._state["items"][0]["detail"] == "How to authenticate?"
        assert "topics" not in disco_state._state

    def test_migrate_open_items(self, disco_state):
        """Old 'open_items' list migrates to decision items with pending status."""
        disco_state._state["open_items"] = ["Which region?", "Which SKU?"]
        disco_state._state["items"] = []
        disco_state._migrate_legacy_state()
        assert len(disco_state._state["items"]) == 2
        assert all(i["kind"] == "decision" for i in disco_state._state["items"])
        assert all(i["status"] == "pending" for i in disco_state._state["items"])
        assert "open_items" not in disco_state._state

    def test_migrate_confirmed_items(self, disco_state):
        """Old 'confirmed_items' list migrates to confirmed decision items."""
        disco_state._state["confirmed_items"] = ["Use PaaS"]
        disco_state._state["items"] = []
        disco_state._migrate_legacy_state()
        assert len(disco_state._state["items"]) == 1
        assert disco_state._state["items"][0]["status"] == "confirmed"
        assert "confirmed_items" not in disco_state._state

    def test_migrate_deduplicates(self, disco_state):
        """Migration deduplicates by heading (case-insensitive)."""
        disco_state._state["topics"] = [
            {"heading": "Auth", "questions": "q", "status": "pending"},
        ]
        disco_state._state["open_items"] = ["Auth"]  # Same heading
        disco_state._state["items"] = []
        disco_state._migrate_legacy_state()
        assert len(disco_state._state["items"]) == 1

    def test_migrate_empty_legacy_keys_cleaned(self, disco_state):
        """Empty legacy keys are removed even if they have no items."""
        disco_state._state["topics"] = []
        disco_state._state["open_items"] = []
        disco_state._state["confirmed_items"] = []
        disco_state._migrate_legacy_state()
        assert "topics" not in disco_state._state
        assert "open_items" not in disco_state._state
        assert "confirmed_items" not in disco_state._state

    def test_no_legacy_keys_no_op(self, disco_state):
        """When no legacy keys exist, migration is a no-op."""
        original_items = list(disco_state._state["items"])
        disco_state._migrate_legacy_state()
        assert disco_state._state["items"] == original_items

    def test_post_load_calls_migrate(self, disco_state, tmp_path):
        """Loading state from disk triggers migration."""
        state_dir = Path(str(tmp_path)) / "test-project" / ".prototype" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        legacy_state = _default_discovery_state()
        legacy_state["topics"] = [
            {"heading": "Legacy Topic", "questions": "q", "status": "pending"},
        ]
        state_file = state_dir / "discovery.yaml"
        with open(state_file, "w", encoding="utf-8") as f:
            yaml.dump(legacy_state, f)

        ds = DiscoveryState(str(tmp_path / "test-project"))
        ds.load()
        assert len(ds._state["items"]) == 1
        assert ds._state["items"][0]["heading"] == "Legacy Topic"
        assert "topics" not in ds._state


# ======================================================================
# update_from_exchange
# ======================================================================


class TestUpdateFromExchange:
    """Test exchange recording with str and list content."""

    def test_string_input(self, disco_state):
        disco_state.update_from_exchange("Hello", "Hi there!", 1)
        history = disco_state._state["conversation_history"]
        assert len(history) == 1
        assert history[0]["user"] == "Hello"
        assert history[0]["assistant"] == "Hi there!"
        assert history[0]["exchange"] == 1

    def test_list_input_text_only(self, disco_state):
        """Multi-modal content with only text parts."""
        content = [
            {"type": "text", "text": "Part 1"},
            {"type": "text", "text": "Part 2"},
        ]
        disco_state.update_from_exchange(content, "Response", 1)
        history = disco_state._state["conversation_history"]
        assert "Part 1" in history[0]["user"]
        assert "Part 2" in history[0]["user"]

    def test_list_input_with_images_stripped(self, disco_state):
        """Multi-modal content with images — base64 data replaced with placeholder."""
        content = [
            {"type": "text", "text": "See this diagram"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,ABC123..."}},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,DEF456..."}},
        ]
        disco_state.update_from_exchange(content, "I see the diagram", 1)
        history = disco_state._state["conversation_history"]
        assert "[2 image(s) attached]" in history[0]["user"]
        assert "base64" not in history[0]["user"]

    def test_exchange_count_updated(self, disco_state):
        disco_state.update_from_exchange("Q1", "A1", 1)
        disco_state.update_from_exchange("Q2", "A2", 2)
        assert disco_state._state["_metadata"]["exchange_count"] == 2

    def test_list_input_with_no_text(self, disco_state):
        """Multi-modal content with only images."""
        content = [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,ABC"}},
        ]
        disco_state.update_from_exchange(content, "I see", 1)
        history = disco_state._state["conversation_history"]
        assert "[1 image(s) attached]" in history[0]["user"]


# ======================================================================
# Item management
# ======================================================================


class TestItemManagement:
    """Test add, resolve, mark, append, dedup operations."""

    def test_add_open_item(self, disco_state):
        disco_state.add_open_item("Which region?")
        items = disco_state._state["items"]
        assert len(items) == 1
        assert items[0]["heading"] == "Which region?"
        assert items[0]["status"] == "pending"
        assert items[0]["kind"] == "decision"

    def test_add_open_item_dedup(self, disco_state):
        disco_state.add_open_item("Which region?")
        disco_state.add_open_item("Which region?")
        assert len(disco_state._state["items"]) == 1

    def test_resolve_item_by_heading(self, disco_state):
        disco_state.add_open_item("Which region?")
        disco_state.resolve_item("Which region?")
        assert disco_state._state["items"][0]["status"] == "confirmed"

    def test_resolve_item_by_confirmed_text(self, disco_state):
        disco_state.add_open_item("Which region?")
        disco_state.resolve_item("different", confirmed_text="Which region?")
        assert disco_state._state["items"][0]["status"] == "confirmed"

    def test_resolve_creates_if_not_found(self, disco_state):
        disco_state.resolve_item("nonexistent", confirmed_text="New decision")
        assert len(disco_state._state["items"]) == 1
        assert disco_state._state["items"][0]["status"] == "confirmed"

    def test_resolve_no_match_no_text_no_op(self, disco_state):
        disco_state.resolve_item("nonexistent")
        assert len(disco_state._state["items"]) == 0

    def test_add_confirmed_decision(self, disco_state):
        disco_state.add_confirmed_decision("Use PaaS services")
        assert "Use PaaS services" in disco_state._state["decisions"]

    def test_add_confirmed_decision_dedup(self, disco_state):
        disco_state.add_confirmed_decision("Use PaaS")
        disco_state.add_confirmed_decision("Use PaaS")
        assert disco_state._state["decisions"].count("Use PaaS") == 1

    def test_set_items(self, disco_state):
        items = [
            TrackedItem(heading="T1", detail="D1", kind="topic", status="pending", answer_exchange=None),
        ]
        disco_state.set_items(items)
        assert len(disco_state._state["items"]) == 1

    def test_append_items_dedup(self, disco_state):
        disco_state.set_items(
            [TrackedItem(heading="T1", detail="D1", kind="topic", status="pending", answer_exchange=None)]
        )
        disco_state.append_items(
            [
                TrackedItem(heading="T1", detail="D1", kind="topic", status="pending", answer_exchange=None),
                TrackedItem(heading="T2", detail="D2", kind="topic", status="pending", answer_exchange=None),
            ]
        )
        assert len(disco_state._state["items"]) == 2

    def test_mark_item(self, disco_state_with_items):
        disco_state_with_items.mark_item("Auth approach", "answered", exchange=5)
        item = disco_state_with_items._state["items"][0]
        assert item["status"] == "answered"
        assert item["answer_exchange"] == 5

    def test_first_pending_index(self, disco_state_with_items):
        idx = disco_state_with_items.first_pending_index()
        assert idx == 0  # Auth approach is pending

    def test_first_pending_index_by_kind(self, disco_state_with_items):
        # Add a pending decision
        disco_state_with_items._state["items"].append(
            {"heading": "D1", "detail": "d", "kind": "decision", "status": "pending", "answer_exchange": None}
        )
        idx = disco_state_with_items.first_pending_index(kind="decision")
        assert idx == 3

    def test_first_pending_index_none(self, disco_state):
        assert disco_state.first_pending_index() is None


# ======================================================================
# Item properties
# ======================================================================


class TestItemProperties:
    """Test item accessor properties."""

    def test_open_count(self, disco_state_with_items):
        assert disco_state_with_items.open_count == 1

    def test_confirmed_count(self, disco_state_with_items):
        assert disco_state_with_items.confirmed_count == 2  # confirmed + answered

    def test_has_items(self, disco_state_with_items):
        assert disco_state_with_items.has_items is True

    def test_has_items_empty(self, disco_state):
        assert disco_state.has_items is False

    def test_items_property(self, disco_state_with_items):
        items = disco_state_with_items.items
        assert len(items) == 3
        assert all(isinstance(i, TrackedItem) for i in items)

    def test_topic_items(self, disco_state_with_items):
        topics = disco_state_with_items.topic_items
        assert len(topics) == 2  # Auth approach + Hosting

    def test_items_by_status(self, disco_state_with_items):
        pending = disco_state_with_items.items_by_status("pending")
        assert len(pending) == 1
        assert pending[0].heading == "Auth approach"


# ======================================================================
# Backward-compat aliases
# ======================================================================


class TestBackwardCompatAliases:
    """Test old method names still work."""

    def test_topics_alias(self, disco_state_with_items):
        assert disco_state_with_items.topics == disco_state_with_items.items

    def test_has_topics_alias(self, disco_state_with_items):
        assert disco_state_with_items.has_topics == disco_state_with_items.has_items

    def test_set_topics_alias(self, disco_state):
        items = [TrackedItem(heading="X", detail="x", kind="topic", status="pending", answer_exchange=None)]
        disco_state.set_topics(items)
        assert len(disco_state._state["items"]) == 1

    def test_mark_topic_alias(self, disco_state_with_items):
        disco_state_with_items.mark_topic("Auth approach", "confirmed")
        assert disco_state_with_items._state["items"][0]["status"] == "confirmed"

    def test_first_pending_topic_index_alias(self, disco_state_with_items):
        assert disco_state_with_items.first_pending_topic_index() == disco_state_with_items.first_pending_index()


# ======================================================================
# Format methods
# ======================================================================


class TestFormatMethods:
    """Test display formatting methods."""

    def test_format_open_items_with_pending(self, disco_state_with_items):
        result = disco_state_with_items.format_open_items()
        assert "Auth approach" in result
        assert "Open items" in result

    def test_format_open_items_none_pending(self, disco_state):
        result = disco_state.format_open_items()
        assert "No open items" in result

    def test_format_confirmed_items(self, disco_state_with_items):
        result = disco_state_with_items.format_confirmed_items()
        assert "DB choice" in result
        assert "Hosting" in result

    def test_format_confirmed_items_none(self, disco_state):
        result = disco_state.format_confirmed_items()
        assert "No items confirmed" in result

    def test_format_status_summary(self, disco_state_with_items):
        result = disco_state_with_items.format_status_summary()
        assert "2 confirmed" in result
        assert "1 open" in result

    def test_format_status_summary_empty(self, disco_state):
        result = disco_state.format_status_summary()
        assert "No items tracked" in result

    def test_format_as_context_structured(self, disco_state):
        disco_state._loaded = True
        disco_state._state["project"]["summary"] = "Test project"
        disco_state._state["project"]["goals"] = ["Goal 1"]
        disco_state._state["requirements"]["functional"] = ["API support"]
        disco_state._state["constraints"] = ["PaaS only"]
        disco_state._state["decisions"] = ["Use Cosmos DB"]
        disco_state._state["architecture"]["services"] = ["cosmos-db"]
        result = disco_state.format_as_context()
        assert "Test project" in result
        assert "Goal 1" in result
        assert "API support" in result
        assert "PaaS only" in result
        assert "Use Cosmos DB" in result
        assert "cosmos-db" in result

    def test_format_as_context_falls_back_to_conversation(self, disco_state):
        """When structured fields are empty, falls back to conversation summary."""
        disco_state._loaded = True
        disco_state._state["conversation_history"] = [
            {
                "user": "Tell me about the project",
                "assistant": "## Project Summary\nThis is a test project.\n## Confirmed Functional Requirements\n- API",
            }
        ]
        result = disco_state.format_as_context()
        assert "Project Summary" in result

    def test_format_as_context_not_loaded(self, disco_state):
        result = disco_state.format_as_context()
        assert result == ""


# ======================================================================
# Merge learnings
# ======================================================================


class TestMergeLearnings:
    """Test merge_learnings integrates structured data."""

    def test_merge_project_summary(self, disco_state):
        disco_state.merge_learnings({"project": {"summary": "New summary"}})
        assert disco_state._state["project"]["summary"] == "New summary"

    def test_merge_goals(self, disco_state):
        disco_state.merge_learnings({"project": {"goals": ["G1", "G2"]}})
        assert disco_state._state["project"]["goals"] == ["G1", "G2"]

    def test_merge_requirements(self, disco_state):
        disco_state.merge_learnings({"requirements": {"functional": ["R1"], "non_functional": ["NF1"]}})
        assert disco_state._state["requirements"]["functional"] == ["R1"]
        assert disco_state._state["requirements"]["non_functional"] == ["NF1"]

    def test_merge_deduplicates(self, disco_state):
        disco_state.merge_learnings({"constraints": ["C1"]})
        disco_state.merge_learnings({"constraints": ["C1", "C2"]})
        assert disco_state._state["constraints"] == ["C1", "C2"]

    def test_merge_open_items_creates_decisions(self, disco_state):
        disco_state.merge_learnings({"open_items": ["Choose DB"]})
        assert len(disco_state._state["items"]) == 1
        assert disco_state._state["items"][0]["kind"] == "decision"

    def test_merge_resolved_items(self, disco_state):
        disco_state.add_open_item("Choose DB")
        disco_state.merge_learnings({"resolved_items": ["Choose DB"]})
        assert disco_state._state["items"][0]["status"] == "confirmed"

    def test_merge_scope(self, disco_state):
        disco_state.merge_learnings({"scope": {"in_scope": ["API"], "deferred": ["ML"]}})
        assert "API" in disco_state._state["scope"]["in_scope"]
        assert "ML" in disco_state._state["scope"]["deferred"]

    def test_merge_architecture(self, disco_state):
        disco_state.merge_learnings({"architecture": {"services": ["cosmos-db"], "data_flow": "API -> DB"}})
        assert "cosmos-db" in disco_state._state["architecture"]["services"]
        assert disco_state._state["architecture"]["data_flow"] == "API -> DB"


# ======================================================================
# Search history
# ======================================================================


class TestSearchHistory:
    """Test conversation history search."""

    def test_search_finds_match(self, disco_state):
        disco_state._state["conversation_history"] = [
            {"user": "Tell me about Cosmos DB", "assistant": "It is a NoSQL database"},
            {"user": "What about SQL?", "assistant": "Relational database"},
        ]
        results = disco_state.search_history("cosmos")
        assert len(results) == 1

    def test_search_no_match(self, disco_state):
        disco_state._state["conversation_history"] = [
            {"user": "Hello", "assistant": "Hi"},
        ]
        results = disco_state.search_history("cosmos")
        assert len(results) == 0


# ======================================================================
# topic_at_exchange
# ======================================================================


class TestTopicAtExchange:
    """Test finding which topic was discussed at a given exchange."""

    def test_finds_topic_at_exchange(self, disco_state_with_items):
        # DB choice answered at exchange 2, Hosting at 3
        result = disco_state_with_items.topic_at_exchange(2)
        assert result == "DB choice"

    def test_returns_none_no_answered_items(self, disco_state):
        assert disco_state.topic_at_exchange(1) is None

    def test_returns_none_past_all_exchanges(self, disco_state_with_items):
        # Exchange 10 is past all answered items
        result = disco_state_with_items.topic_at_exchange(10)
        assert result is None


# ======================================================================
# Artifact inventory
# ======================================================================


class TestArtifactInventory:
    """Test artifact hash tracking."""

    def test_update_and_get(self, disco_state):
        disco_state.update_artifact_inventory({"/path/to/file.txt": "abc123"})
        hashes = disco_state.get_artifact_hashes()
        assert hashes["/path/to/file.txt"] == "abc123"

    def test_additive_updates(self, disco_state):
        disco_state.update_artifact_inventory({"/a": "hash1"})
        disco_state.update_artifact_inventory({"/b": "hash2"})
        hashes = disco_state.get_artifact_hashes()
        assert "/a" in hashes
        assert "/b" in hashes


# ======================================================================
# Context hash
# ======================================================================


class TestContextHash:
    """Test context hash for change detection."""

    def test_update_and_get(self, disco_state):
        disco_state.update_context_hash("abc123")
        assert disco_state.get_context_hash() == "abc123"

    def test_default_empty(self, disco_state):
        assert disco_state.get_context_hash() == ""


# ======================================================================
# Reset
# ======================================================================


class TestReset:
    """Test state reset."""

    def test_reset_clears_state(self, disco_state_with_items):
        disco_state_with_items.reset()
        assert disco_state_with_items._state["items"] == []
        assert disco_state_with_items._loaded is False


# ======================================================================
# TrackedItem dataclass
# ======================================================================


class TestTrackedItem:
    """Test TrackedItem serialization."""

    def test_to_dict(self):
        item = TrackedItem(heading="H", detail="D", kind="topic", status="pending", answer_exchange=None)
        d = item.to_dict()
        assert d["heading"] == "H"
        assert d["kind"] == "topic"

    def test_from_dict(self):
        d = {"heading": "H", "detail": "D", "kind": "decision", "status": "confirmed", "answer_exchange": 3}
        item = TrackedItem.from_dict(d)
        assert item.heading == "H"
        assert item.answer_exchange == 3

    def test_from_dict_legacy_questions_key(self):
        """Old format used 'questions' instead of 'detail'."""
        d = {"heading": "H", "questions": "Q?", "status": "pending"}
        item = TrackedItem.from_dict(d)
        assert item.detail == "Q?"



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
