"""Tests for backlog_session.py — branch coverage for cache vs regeneration,
quick mode vs interactive, item enrichment, push routing (GitHub vs DevOps),
review loop, /add command handling, _parse_items, _mutate_items, _save_backlog_md,
and slash commands.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from azext_prototype.agents.base import AgentCapability, AgentContext

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def backlog_context(project_with_design, sample_config):
    provider = MagicMock()
    provider.provider_name = "github-models"
    provider.default_model = "gpt-4o"
    provider.chat.return_value = MagicMock(
        content='[{"epic": "API", "title": "Build REST API", "description": "desc", '
        '"acceptance_criteria": ["AC1"], "tasks": [{"title": "T1", "done": false}], '
        '"effort": "M", "status": "todo"}]',
        model="test",
        usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
    )
    return AgentContext(
        project_config=sample_config,
        project_dir=str(project_with_design),
        ai_provider=provider,
    )


@pytest.fixture
def backlog_registry():
    registry = MagicMock()

    mock_pm = MagicMock()
    mock_pm.name = "project-manager"
    mock_pm.get_system_messages.return_value = []
    mock_pm._temperature = 0.3
    mock_pm._max_tokens = 8192

    mock_qa = MagicMock()
    mock_qa.name = "qa-engineer"

    def find_by_cap(cap):
        mapping = {
            AgentCapability.BACKLOG_GENERATION: [mock_pm],
            AgentCapability.QA: [mock_qa],
        }
        return mapping.get(cap, [])

    registry.find_by_capability.side_effect = find_by_cap
    return registry


def _make_session(ctx, registry, items_response=None):
    from azext_prototype.stages.backlog_session import BacklogSession

    session = BacklogSession(ctx, registry)

    # Override the AI response if specified AFTER session is created
    if items_response is not None:
        ctx.ai_provider.chat.return_value = MagicMock(
            content=items_response,
            model="test",
            usage={"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
        )

    return session


# ------------------------------------------------------------------
# BacklogResult
# ------------------------------------------------------------------


class TestBacklogResult:
    def test_defaults(self):
        from azext_prototype.stages.backlog_session import BacklogResult

        result = BacklogResult()
        assert result.items_generated == 0
        assert result.items_pushed == 0
        assert result.items_failed == 0
        assert result.push_urls == []
        assert result.cancelled is False

    def test_with_values(self):
        from azext_prototype.stages.backlog_session import BacklogResult

        result = BacklogResult(
            items_generated=5,
            items_pushed=3,
            items_failed=1,
            push_urls=["https://github.com/issues/1"],
            cancelled=False,
        )
        assert result.items_generated == 5
        assert len(result.push_urls) == 1


# ------------------------------------------------------------------
# _parse_items
# ------------------------------------------------------------------


class TestParseItems:
    def test_valid_json_array(self):
        from azext_prototype.stages.backlog_session import BacklogSession

        items = BacklogSession._parse_items('[{"title": "A"}, {"title": "B"}]')
        assert len(items) == 2
        assert items[0]["title"] == "A"

    def test_json_with_fences(self):
        from azext_prototype.stages.backlog_session import BacklogSession

        items = BacklogSession._parse_items('```json\n[{"title": "X"}]\n```')
        assert len(items) == 1
        assert items[0]["title"] == "X"

    def test_invalid_json_returns_empty(self):
        from azext_prototype.stages.backlog_session import BacklogSession

        items = BacklogSession._parse_items("not json at all")
        assert items == []

    def test_json_object_not_array_returns_empty(self):
        from azext_prototype.stages.backlog_session import BacklogSession

        items = BacklogSession._parse_items('{"title": "single"}')
        assert items == []


# ------------------------------------------------------------------
# Run — cached items path
# ------------------------------------------------------------------


class TestRunCachedItems:
    def test_cached_items_skip_generation(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)

        # Pre-populate cached items
        session._backlog_state._state["items"] = [
            {"epic": "API", "title": "Build API", "status": "todo"},
        ]
        session._backlog_state._state["context_hash"] = ""
        session._backlog_state.matches_context = MagicMock(return_value=True)

        output = []
        result = session.run(
            design_context="arch",
            input_fn=lambda p: "done",
            print_fn=lambda m: output.append(m),
        )
        assert result.items_generated == 1
        assert not result.cancelled


# ------------------------------------------------------------------
# Run — generation path
# ------------------------------------------------------------------


class TestRunGeneration:
    def test_generation_creates_items(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)

        output = []
        result = session.run(
            design_context="Build an API with Cosmos DB",
            input_fn=lambda p: "done",
            print_fn=lambda m: output.append(m),
        )
        assert result.items_generated >= 1
        assert not result.cancelled

    def test_no_pm_agent_cancels(self, backlog_context):
        registry = MagicMock()
        registry.find_by_capability.return_value = []

        from azext_prototype.stages.backlog_session import BacklogSession

        session = BacklogSession(backlog_context, registry)

        result = session.run(
            design_context="test",
            input_fn=lambda p: "done",
            print_fn=lambda m: None,
        )
        assert result.cancelled is True

    def test_no_ai_provider_cancels(self, backlog_context, backlog_registry):
        backlog_context.ai_provider = None

        from azext_prototype.stages.backlog_session import BacklogSession

        session = BacklogSession(backlog_context, backlog_registry)

        result = session.run(
            design_context="test",
            input_fn=lambda p: "done",
            print_fn=lambda m: None,
        )
        assert result.cancelled is True

    def test_empty_ai_response_cancels(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry, items_response="not json")

        result = session.run(
            design_context="test",
            input_fn=lambda p: "done",
            print_fn=lambda m: None,
        )
        assert result.cancelled is True


# ------------------------------------------------------------------
# Run — quick mode
# ------------------------------------------------------------------


class TestRunQuickMode:
    @patch("azext_prototype.stages.backlog_session.check_gh_auth", return_value=True)
    @patch("azext_prototype.stages.backlog_session.push_github_issue", return_value={"url": "https://gh/1"})
    def test_quick_mode_confirm_pushes(self, mock_push, mock_auth, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)

        result = session.run(
            design_context="test",
            provider="github",
            org="myorg",
            project="myrepo",
            quick=True,
            input_fn=lambda p: "y",
            print_fn=lambda m: None,
        )
        assert result.items_pushed >= 1

    def test_quick_mode_cancel(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)

        result = session.run(
            design_context="test",
            quick=True,
            input_fn=lambda p: "n",
            print_fn=lambda m: None,
        )
        assert result.cancelled is True

    def test_quick_mode_eof(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)

        def raise_eof(p):
            raise EOFError

        result = session.run(
            design_context="test",
            quick=True,
            input_fn=raise_eof,
            print_fn=lambda m: None,
        )
        assert result.cancelled is True


# ------------------------------------------------------------------
# Interactive review loop
# ------------------------------------------------------------------


class TestInteractiveLoop:
    def test_quit_in_loop(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)

        inputs = iter(["quit"])
        result = session.run(
            design_context="test",
            input_fn=lambda p: next(inputs),
            print_fn=lambda m: None,
        )
        assert result.cancelled is True

    def test_slash_quit(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)

        inputs = iter(["/quit"])
        result = session.run(
            design_context="test",
            input_fn=lambda p: next(inputs),
            print_fn=lambda m: None,
        )
        assert result.cancelled is True

    def test_eof_in_loop(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)

        call_count = [0]

        def input_fn(p):
            call_count[0] += 1
            if call_count[0] > 1:
                raise EOFError
            return "not a command"

        # Override to return a parseable mutation
        backlog_context.ai_provider.chat.side_effect = [
            # Initial generation
            MagicMock(
                content='[{"epic": "A", "title": "T1"}]',
                model="test",
                usage={},
            ),
            # Mutation call
            MagicMock(
                content='[{"epic": "A", "title": "T1 updated"}]',
                model="test",
                usage={},
            ),
        ]

        result = session.run(
            design_context="test",
            input_fn=input_fn,
            print_fn=lambda m: None,
        )
        assert result.cancelled is True

    def test_empty_input_ignored(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)

        inputs = iter(["", "", "done"])
        result = session.run(
            design_context="test",
            input_fn=lambda p: next(inputs),
            print_fn=lambda m: None,
        )
        assert not result.cancelled


# ------------------------------------------------------------------
# Slash commands
# ------------------------------------------------------------------


class TestSlashCommands:
    def _run_with_commands(self, ctx, registry, commands):
        session = _make_session(ctx, registry)
        inputs = iter(commands + ["done"])
        output = []
        session.run(
            design_context="test",
            input_fn=lambda p: next(inputs),
            print_fn=lambda m: output.append(m),
        )
        return output

    def test_list_command(self, backlog_context, backlog_registry):
        output = self._run_with_commands(backlog_context, backlog_registry, ["/list"])
        # Should have printed the backlog summary
        assert any("Backlog" in str(m) or "item" in str(m).lower() for m in output)

    def test_show_valid_index(self, backlog_context, backlog_registry):
        output = self._run_with_commands(backlog_context, backlog_registry, ["/show 1"])
        # Should show item details
        assert len(output) > 0

    def test_show_invalid_arg(self, backlog_context, backlog_registry):
        output = self._run_with_commands(backlog_context, backlog_registry, ["/show"])
        assert any("Usage" in str(m) for m in output)

    def test_remove_valid_index(self, backlog_context, backlog_registry):
        output = self._run_with_commands(backlog_context, backlog_registry, ["/remove 1"])
        assert any("Removed" in str(m) for m in output)

    def test_remove_invalid_arg(self, backlog_context, backlog_registry):
        output = self._run_with_commands(backlog_context, backlog_registry, ["/remove"])
        assert any("Usage" in str(m) for m in output)

    def test_help_command(self, backlog_context, backlog_registry):
        output = self._run_with_commands(backlog_context, backlog_registry, ["/help"])
        assert any("Available commands" in str(m) for m in output)

    def test_status_command(self, backlog_context, backlog_registry):
        output = self._run_with_commands(backlog_context, backlog_registry, ["/status"])
        assert len(output) > 0

    def test_preview_command(self, backlog_context, backlog_registry):
        output = self._run_with_commands(
            backlog_context,
            backlog_registry,
            ["/preview"],
        )
        assert len(output) > 0


# ------------------------------------------------------------------
# _push_all — provider routing
# ------------------------------------------------------------------


class TestPushAll:
    def _set_items_with_status(self, session, items, statuses=None):
        """Helper to properly set items with matching push_status and push_results arrays."""
        session._backlog_state._state["items"] = items
        n = len(items)
        session._backlog_state._state["push_status"] = statuses or ["pending"] * n
        session._backlog_state._state["push_results"] = [None] * n

    @patch("azext_prototype.stages.backlog_session.check_gh_auth", return_value=False)
    def test_github_auth_failure(self, mock_auth, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)
        self._set_items_with_status(session, [{"title": "A", "status": "todo"}])

        output = []
        result = session._push_all("github", "org", "repo", lambda m: output.append(m), False)
        assert result.cancelled is True

    @patch("azext_prototype.stages.backlog_session.check_devops_ext", return_value=False)
    def test_devops_ext_not_available(self, mock_ext, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)
        self._set_items_with_status(session, [{"title": "A", "status": "todo"}])

        output = []
        result = session._push_all("devops", "org", "project", lambda m: output.append(m), False)
        assert result.cancelled is True

    @patch("azext_prototype.stages.backlog_session.check_gh_auth", return_value=True)
    @patch("azext_prototype.stages.backlog_session.push_github_issue", return_value={"url": "https://gh/1"})
    def test_github_push_success(self, mock_push, mock_auth, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)
        self._set_items_with_status(session, [{"title": "A", "status": "todo"}])

        output = []
        result = session._push_all("github", "org", "repo", lambda m: output.append(m), False)
        assert result.items_pushed == 1
        assert "https://gh/1" in result.push_urls

    @patch("azext_prototype.stages.backlog_session.check_gh_auth", return_value=True)
    @patch("azext_prototype.stages.backlog_session.push_github_issue", return_value={"error": "rate limited"})
    def test_github_push_failure(self, mock_push, mock_auth, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)
        self._set_items_with_status(session, [{"title": "A", "status": "todo"}])

        output = []
        result = session._push_all("github", "org", "repo", lambda m: output.append(m), False)
        assert result.items_failed == 1

    @patch("azext_prototype.stages.backlog_session.check_devops_ext", return_value=True)
    @patch("azext_prototype.stages.backlog_session.push_devops_feature")
    @patch("azext_prototype.stages.backlog_session.push_devops_story")
    @patch("azext_prototype.stages.backlog_session.push_devops_task")
    def test_devops_push_with_children(
        self, mock_task, mock_story, mock_feature, mock_ext, backlog_context, backlog_registry
    ):
        mock_feature.return_value = {"url": "https://devops/1", "id": "1"}
        mock_story.return_value = {"url": "https://devops/s1", "id": "2"}
        mock_task.return_value = {"url": "https://devops/t1"}

        session = _make_session(backlog_context, backlog_registry)
        items = [
            {
                "title": "Feature A",
                "status": "todo",
                "children": [
                    {
                        "title": "Story 1",
                        "tasks": [{"title": "Task 1", "done": False}],
                    }
                ],
            }
        ]
        self._set_items_with_status(session, items)

        output = []
        result = session._push_all("devops", "org", "proj", lambda m: output.append(m), False)
        assert result.items_pushed == 1
        mock_story.assert_called_once()
        mock_task.assert_called_once()

    def test_no_pending_items(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)
        self._set_items_with_status(session, [{"title": "A", "status": "pushed"}], statuses=["pushed"])

        output = []
        result = session._push_all("github", "org", "repo", lambda m: output.append(m), False)
        # items_pushed reflects historical pushed count (1 already pushed)
        assert result.items_pushed == 1
        assert any("No pending" in str(m) for m in output)


# ------------------------------------------------------------------
# _enrich_new_item
# ------------------------------------------------------------------


class TestEnrichNewItem:
    def test_no_pm_agent_returns_bare(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)
        session._pm_agent = None

        item = session._enrich_new_item("Build rate limiter")
        assert item["title"] == "Build rate limiter"
        assert item["epic"] == "Added"

    def test_no_ai_provider_returns_bare(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)
        session._context.ai_provider = None

        item = session._enrich_new_item("Build rate limiter")
        assert item["title"] == "Build rate limiter"

    def test_successful_enrichment(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)

        backlog_context.ai_provider.chat.return_value = MagicMock(
            content='{"epic": "Performance", "title": "API Rate Limiter", '
            '"description": "Implement rate limiting", '
            '"acceptance_criteria": ["Limit 100 req/s"], '
            '"tasks": ["Add middleware"], "effort": "M"}',
            model="test",
            usage={},
        )

        item = session._enrich_new_item("Build rate limiter")
        assert item["title"] == "API Rate Limiter"
        assert item["epic"] == "Performance"

    def test_enrichment_failure_falls_back(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)

        backlog_context.ai_provider.chat.side_effect = Exception("AI failed")

        item = session._enrich_new_item("Build rate limiter")
        assert item["title"] == "Build rate limiter"
        assert item["epic"] == "Added"

    def test_enrichment_with_fenced_json(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)

        backlog_context.ai_provider.chat.return_value = MagicMock(
            content='```json\n{"epic": "Infra", "title": "Add CDN"}\n```',
            model="test",
            usage={},
        )

        item = session._enrich_new_item("Add CDN")
        assert item["title"] == "Add CDN"
        assert item["epic"] == "Infra"


# ------------------------------------------------------------------
# _mutate_items
# ------------------------------------------------------------------


class TestMutateItems:
    def test_successful_mutation(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)
        session._backlog_state._state["items"] = [{"title": "Old title"}]

        backlog_context.ai_provider.chat.return_value = MagicMock(
            content='[{"title": "Updated title"}]',
            model="test",
            usage={},
        )

        result = session._mutate_items("Change title to Updated title", "design context")
        assert result is not None
        assert result[0]["title"] == "Updated title"

    def test_no_pm_agent_returns_none(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)
        session._pm_agent = None

        result = session._mutate_items("Change title", "ctx")
        assert result is None

    def test_no_ai_provider_returns_none(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)
        session._context.ai_provider = None

        result = session._mutate_items("Change title", "ctx")
        assert result is None


# ------------------------------------------------------------------
# _save_backlog_md
# ------------------------------------------------------------------


class TestSaveBacklogMd:
    def test_saves_markdown(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)
        session._backlog_state._state["items"] = [
            {"epic": "API", "title": "Build endpoints", "effort": "M", "description": "REST API"},
        ]

        output = []
        session._save_backlog_md(lambda m: output.append(m))

        md_path = Path(backlog_context.project_dir) / "concept" / "docs" / "BACKLOG.md"
        assert md_path.exists()
        content = md_path.read_text()
        assert "Build endpoints" in content

    def test_empty_items_prints_message(self, backlog_context, backlog_registry):
        session = _make_session(backlog_context, backlog_registry)
        session._backlog_state._state["items"] = []

        output = []
        session._save_backlog_md(lambda m: output.append(m))
        assert any("No items" in str(m) for m in output)
