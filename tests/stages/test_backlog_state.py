"""Tests for BacklogState — item management, push tracking, context hash.

Covers:
- Item management (set_items, mark_item_pushed, mark_item_failed)
- Push status arrays synchronized with items
- Pending/pushed/failed item queries
- Context hash for cache invalidation
- matches_context validation
- Conversation tracking (update_from_exchange)
- Formatting (backlog summary, item detail)
- State persistence (load, save, reset)
"""

import pytest

from azext_prototype.stages.backlog_state import BacklogState, _default_backlog_state

# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def backlog_state(tmp_project):
    return BacklogState(str(tmp_project))


@pytest.fixture
def backlog_state_with_items(backlog_state):
    """Backlog state with sample items set."""
    items = [
        {
            "epic": "Infrastructure",
            "title": "Setup VNet",
            "description": "Configure virtual network",
            "effort": "M",
            "acceptance_criteria": ["AC1", "AC2"],
            "tasks": ["T1"],
        },
        {
            "epic": "Infrastructure",
            "title": "Setup Key Vault",
            "description": "Create KV for secrets",
            "effort": "S",
        },
        {
            "epic": "Application",
            "title": "Build API",
            "description": "REST API on Container Apps",
            "effort": "L",
            "children": [
                {"title": "Create Dockerfile", "effort": "S"},
                {"title": "Add health check", "effort": "S"},
            ],
        },
    ]
    backlog_state.set_items(items)
    return backlog_state


# ======================================================================
# Item management
# ======================================================================


class TestItemManagement:
    """Test set_items and push status arrays."""

    def test_set_items_stores_items(self, backlog_state):
        items = [{"title": "A"}, {"title": "B"}]
        backlog_state.set_items(items)
        assert len(backlog_state._state["items"]) == 2

    def test_set_items_resets_push_status(self, backlog_state):
        backlog_state.set_items([{"title": "A"}, {"title": "B"}, {"title": "C"}])
        assert backlog_state._state["push_status"] == ["pending", "pending", "pending"]
        assert backlog_state._state["push_results"] == [None, None, None]

    def test_set_items_replaces_previous(self, backlog_state):
        backlog_state.set_items([{"title": "A"}])
        backlog_state.set_items([{"title": "X"}, {"title": "Y"}])
        assert len(backlog_state._state["items"]) == 2
        assert len(backlog_state._state["push_status"]) == 2


# ======================================================================
# Push status tracking
# ======================================================================


class TestPushStatusTracking:
    """Test mark_item_pushed and mark_item_failed."""

    def test_mark_pushed(self, backlog_state_with_items):
        backlog_state_with_items.mark_item_pushed(0, "https://github.com/issues/1")
        assert backlog_state_with_items._state["push_status"][0] == "pushed"
        assert backlog_state_with_items._state["push_results"][0] == "https://github.com/issues/1"
        assert backlog_state_with_items._state["_metadata"]["last_pushed"] is not None

    def test_mark_failed(self, backlog_state_with_items):
        backlog_state_with_items.mark_item_failed(1, "auth error")
        assert backlog_state_with_items._state["push_status"][1] == "failed"
        assert "auth error" in backlog_state_with_items._state["push_results"][1]

    def test_mark_out_of_range_no_error(self, backlog_state_with_items):
        """Marking an out-of-range index is a no-op."""
        backlog_state_with_items.mark_item_pushed(99, "url")
        backlog_state_with_items.mark_item_failed(99, "err")
        # No crash, no change
        assert all(s == "pending" for s in backlog_state_with_items._state["push_status"])


# ======================================================================
# Item queries
# ======================================================================


class TestItemQueries:
    """Test pending/pushed/failed item queries."""

    def test_get_pending_items_all_pending(self, backlog_state_with_items):
        pending = backlog_state_with_items.get_pending_items()
        assert len(pending) == 3
        assert all(isinstance(p, tuple) and len(p) == 2 for p in pending)

    def test_get_pending_items_after_push(self, backlog_state_with_items):
        backlog_state_with_items.mark_item_pushed(0, "url")
        pending = backlog_state_with_items.get_pending_items()
        assert len(pending) == 2
        assert all(p[0] != 0 for p in pending)

    def test_get_pushed_items(self, backlog_state_with_items):
        backlog_state_with_items.mark_item_pushed(0, "url")
        backlog_state_with_items.mark_item_pushed(2, "url2")
        pushed = backlog_state_with_items.get_pushed_items()
        assert len(pushed) == 2

    def test_get_failed_items(self, backlog_state_with_items):
        backlog_state_with_items.mark_item_failed(1, "err")
        failed = backlog_state_with_items.get_failed_items()
        assert len(failed) == 1
        assert failed[0][0] == 1

    def test_get_pending_with_missing_status(self, backlog_state):
        """Items beyond push_status array length are treated as pending."""
        backlog_state._state["items"] = [{"title": "A"}, {"title": "B"}]
        backlog_state._state["push_status"] = ["pushed"]  # Only 1 status for 2 items
        pending = backlog_state.get_pending_items()
        assert len(pending) == 1
        assert pending[0][0] == 1


# ======================================================================
# Context hash for cache invalidation
# ======================================================================


class TestContextHash:
    """Test context hash computation and matching."""

    def test_set_context_hash(self, backlog_state):
        backlog_state.set_context_hash("design context text")
        h = backlog_state._state["context_hash"]
        assert len(h) == 16  # truncated sha256

    def test_matches_context_true(self, backlog_state):
        backlog_state.set_context_hash("design context")
        assert backlog_state.matches_context("design context") is True

    def test_matches_context_false(self, backlog_state):
        backlog_state.set_context_hash("design context v1")
        assert backlog_state.matches_context("design context v2") is False

    def test_matches_context_with_scope(self, backlog_state):
        scope = {"in_scope": ["API"], "out_of_scope": ["ML"]}
        backlog_state.set_context_hash("ctx", scope=scope)
        assert backlog_state.matches_context("ctx", scope=scope) is True
        assert backlog_state.matches_context("ctx", scope={"in_scope": ["Different"]}) is False

    def test_matches_context_no_hash_set(self, backlog_state):
        assert backlog_state.matches_context("anything") is False


# ======================================================================
# Conversation tracking
# ======================================================================


class TestConversationTracking:
    """Test exchange recording."""

    def test_update_from_exchange(self, backlog_state):
        backlog_state.update_from_exchange("Add more stories", "Here are 3 more stories", 1)
        history = backlog_state._state["conversation_history"]
        assert len(history) == 1
        assert history[0]["exchange"] == 1
        assert history[0]["user"] == "Add more stories"

    def test_multiple_exchanges(self, backlog_state):
        backlog_state.update_from_exchange("Q1", "A1", 1)
        backlog_state.update_from_exchange("Q2", "A2", 2)
        assert len(backlog_state._state["conversation_history"]) == 2


# ======================================================================
# Formatting
# ======================================================================


class TestFormatting:
    """Test backlog summary and item detail formatting."""

    def test_format_summary_empty(self, backlog_state):
        result = backlog_state.format_backlog_summary()
        assert "No backlog items" in result

    def test_format_summary_with_items(self, backlog_state_with_items):
        result = backlog_state_with_items.format_backlog_summary()
        assert "3 item(s)" in result
        assert "Infrastructure" in result
        assert "Application" in result
        assert "3 pending" in result

    def test_format_summary_with_pushed(self, backlog_state_with_items):
        backlog_state_with_items.mark_item_pushed(0, "url")
        result = backlog_state_with_items.format_backlog_summary()
        assert "1 pushed" in result
        assert "2 pending" in result

    def test_format_summary_with_children(self, backlog_state_with_items):
        result = backlog_state_with_items.format_backlog_summary()
        assert "2 stories" in result  # Item 3 has children

    def test_format_summary_with_provider(self, backlog_state_with_items):
        backlog_state_with_items._state["provider"] = "github"
        backlog_state_with_items._state["org"] = "myorg"
        backlog_state_with_items._state["project"] = "myproject"
        result = backlog_state_with_items.format_backlog_summary()
        assert "github" in result
        assert "myorg/myproject" in result

    def test_format_item_detail(self, backlog_state_with_items):
        result = backlog_state_with_items.format_item_detail(0)
        assert "Setup VNet" in result
        assert "Configure virtual network" in result
        assert "AC1" in result
        assert "T1" in result

    def test_format_item_detail_with_children(self, backlog_state_with_items):
        result = backlog_state_with_items.format_item_detail(2)
        assert "Build API" in result
        assert "Children (2)" in result
        assert "Create Dockerfile" in result

    def test_format_item_detail_with_push_status(self, backlog_state_with_items):
        backlog_state_with_items.mark_item_pushed(0, "https://github.com/issues/1")
        result = backlog_state_with_items.format_item_detail(0)
        assert "pushed" in result
        assert "https://github.com/issues/1" in result

    def test_format_item_detail_out_of_range(self, backlog_state_with_items):
        result = backlog_state_with_items.format_item_detail(99)
        assert "not found" in result

    def test_format_item_detail_negative_index(self, backlog_state_with_items):
        result = backlog_state_with_items.format_item_detail(-1)
        assert "not found" in result


# ======================================================================
# State persistence
# ======================================================================


class TestStatePersistence:
    """Test load, save, reset via BaseState."""

    def test_save_and_load(self, backlog_state):
        backlog_state.set_items([{"title": "Persist me"}])
        backlog_state.save()

        new_state = BacklogState(backlog_state._project_dir)
        new_state.load()
        assert len(new_state._state["items"]) == 1
        assert new_state._state["items"][0]["title"] == "Persist me"

    def test_reset(self, backlog_state_with_items):
        backlog_state_with_items.reset()
        assert backlog_state_with_items._state["items"] == []
        assert backlog_state_with_items._state["push_status"] == []

    def test_exists_false_initially(self, backlog_state):
        assert backlog_state.exists is False

    def test_exists_after_save(self, backlog_state):
        backlog_state.save()
        assert backlog_state.exists is True

    def test_default_state_structure(self):
        state = _default_backlog_state()
        assert "items" in state
        assert "provider" in state
        assert "push_status" in state
        assert "context_hash" in state
        assert "_metadata" in state
