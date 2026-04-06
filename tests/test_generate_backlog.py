"""Tests for backlog generation — BacklogState, BacklogSession, push helpers, scope injection.

Keeps the new backlog tests separate from test_custom.py to prevent file bloat.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
import yaml
from knack.util import CLIError

_CUSTOM_MODULE = "azext_prototype.custom"


# ======================================================================
# BacklogState Tests
# ======================================================================


class TestBacklogState:
    """Test BacklogState YAML persistence."""

    def test_default_structure(self, tmp_project):
        from azext_prototype.stages.backlog_state import BacklogState

        state = BacklogState(str(tmp_project))
        assert state.state["items"] == []
        assert state.state["provider"] == ""
        assert state.state["push_status"] == []
        assert state.state["context_hash"] == ""
        assert state.state["conversation_history"] == []

    def test_save_and_load_round_trip(self, tmp_project):
        from azext_prototype.stages.backlog_state import BacklogState

        state = BacklogState(str(tmp_project))
        state._state["provider"] = "github"
        state._state["org"] = "myorg"
        state._state["project"] = "myrepo"
        state.save()

        assert state.exists

        state2 = BacklogState(str(tmp_project))
        loaded = state2.load()
        assert loaded["provider"] == "github"
        assert loaded["org"] == "myorg"
        assert loaded["project"] == "myrepo"
        assert loaded["_metadata"]["created"] is not None

    def test_set_items(self, tmp_project):
        from azext_prototype.stages.backlog_state import BacklogState

        state = BacklogState(str(tmp_project))
        items = [
            {"epic": "Infra", "title": "VNet", "effort": "M", "tasks": []},
            {"epic": "Infra", "title": "KeyVault", "effort": "S", "tasks": []},
        ]
        state.set_items(items)

        assert len(state.state["items"]) == 2
        assert state.state["push_status"] == ["pending", "pending"]
        assert state.state["push_results"] == [None, None]
        assert state.exists

    def test_mark_item_pushed(self, tmp_project):
        from azext_prototype.stages.backlog_state import BacklogState

        state = BacklogState(str(tmp_project))
        state.set_items([{"title": "Item1"}, {"title": "Item2"}])

        state.mark_item_pushed(0, "https://github.com/org/repo/issues/1")

        assert state.state["push_status"][0] == "pushed"
        assert state.state["push_results"][0] == "https://github.com/org/repo/issues/1"
        assert state.state["_metadata"]["last_pushed"] is not None

    def test_mark_item_failed(self, tmp_project):
        from azext_prototype.stages.backlog_state import BacklogState

        state = BacklogState(str(tmp_project))
        state.set_items([{"title": "Item1"}])

        state.mark_item_failed(0, "gh: command failed")

        assert state.state["push_status"][0] == "failed"
        assert "gh: command failed" in state.state["push_results"][0]

    def test_get_pending_items(self, tmp_project):
        from azext_prototype.stages.backlog_state import BacklogState

        state = BacklogState(str(tmp_project))
        state.set_items([{"title": "A"}, {"title": "B"}, {"title": "C"}])
        state.mark_item_pushed(1, "url")

        pending = state.get_pending_items()
        assert len(pending) == 2
        assert pending[0][0] == 0  # idx
        assert pending[1][0] == 2

    def test_get_pushed_items(self, tmp_project):
        from azext_prototype.stages.backlog_state import BacklogState

        state = BacklogState(str(tmp_project))
        state.set_items([{"title": "A"}, {"title": "B"}])
        state.mark_item_pushed(0, "url1")
        state.mark_item_pushed(1, "url2")

        pushed = state.get_pushed_items()
        assert len(pushed) == 2

    def test_context_hash(self, tmp_project):
        from azext_prototype.stages.backlog_state import BacklogState

        state = BacklogState(str(tmp_project))
        context = "Some architecture design"
        scope = {"in_scope": ["API"], "out_of_scope": [], "deferred": []}

        state.set_context_hash(context, scope)
        assert state.matches_context(context, scope)
        assert not state.matches_context("Different context", scope)

    def test_format_backlog_summary(self, tmp_project):
        from azext_prototype.stages.backlog_state import BacklogState

        state = BacklogState(str(tmp_project))
        state.set_items(
            [
                {"epic": "Infra", "title": "VNet Setup", "effort": "M", "tasks": ["T1"]},
                {"epic": "App", "title": "API Gateway", "effort": "L", "tasks": ["T2"]},
            ]
        )

        summary = state.format_backlog_summary()
        assert "2 item(s)" in summary
        assert "VNet Setup" in summary
        assert "API Gateway" in summary
        assert "Infra" in summary
        assert "App" in summary

    def test_format_item_detail(self, tmp_project):
        from azext_prototype.stages.backlog_state import BacklogState

        state = BacklogState(str(tmp_project))
        state.set_items(
            [
                {
                    "epic": "Infra",
                    "title": "VNet Setup",
                    "description": "Configure virtual network",
                    "acceptance_criteria": ["AC1: VNet created"],
                    "tasks": ["Create VNet", "Create Subnets"],
                    "effort": "M",
                }
            ]
        )

        detail = state.format_item_detail(0)
        assert "VNet Setup" in detail
        assert "Configure virtual network" in detail
        assert "AC1: VNet created" in detail
        assert "Create VNet" in detail

    def test_reset(self, tmp_project):
        from azext_prototype.stages.backlog_state import BacklogState

        state = BacklogState(str(tmp_project))
        state.set_items([{"title": "Item1"}])
        assert len(state.state["items"]) == 1

        state.reset()
        assert state.state["items"] == []
        assert state.exists  # File still exists (reset saves)

    def test_update_from_exchange(self, tmp_project):
        from azext_prototype.stages.backlog_state import BacklogState

        state = BacklogState(str(tmp_project))
        state.update_from_exchange("add a story", "Added", 1)

        assert len(state.state["conversation_history"]) == 1
        assert state.state["conversation_history"][0]["user"] == "add a story"


# ======================================================================
# Backlog Push Helper Tests
# ======================================================================


class TestBacklogPushHelpers:
    """Test GitHub/DevOps push helper functions."""

    def test_check_gh_auth_pass(self):
        from azext_prototype.stages.backlog_push import check_gh_auth

        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert check_gh_auth() is True

    def test_check_gh_auth_fail(self):
        from azext_prototype.stages.backlog_push import check_gh_auth

        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert check_gh_auth() is False

    def test_check_gh_auth_not_installed(self):
        from azext_prototype.stages.backlog_push import check_gh_auth

        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError
            assert check_gh_auth() is False

    def test_check_devops_ext_pass(self):
        from azext_prototype.stages.backlog_push import check_devops_ext

        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert check_devops_ext() is True

    def test_check_devops_ext_fail(self):
        from azext_prototype.stages.backlog_push import check_devops_ext

        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert check_devops_ext() is False

    def test_format_github_body(self):
        from azext_prototype.stages.backlog_push import format_github_body

        item = {
            "epic": "Infra",
            "title": "VNet Setup",
            "description": "Configure VNet",
            "acceptance_criteria": ["VNet exists", "Subnets configured"],
            "tasks": ["Create VNet", "Create Subnets"],
            "effort": "M",
        }
        body = format_github_body(item)
        assert "## Description" in body
        assert "Configure VNet" in body
        assert "## Acceptance Criteria" in body
        assert "- [ ] Create VNet" in body
        assert "`effort/M`" in body
        assert "`infra`" in body

    def test_format_devops_description(self):
        from azext_prototype.stages.backlog_push import format_devops_description

        item = {
            "description": "Configure VNet",
            "acceptance_criteria": ["VNet exists"],
            "tasks": ["Create VNet"],
            "effort": "M",
        }
        desc = format_devops_description(item)
        assert "<p>Configure VNet</p>" in desc
        assert "<li>VNet exists</li>" in desc
        assert "<li>Create VNet</li>" in desc
        assert "Effort" in desc

    def test_push_github_issue_success(self):
        from azext_prototype.stages.backlog_push import push_github_issue

        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/myorg/myrepo/issues/42\n",
            )

            result = push_github_issue(
                "myorg",
                "myrepo",
                {"epic": "Infra", "title": "VNet", "description": "desc", "effort": "M"},
            )
            assert result["url"] == "https://github.com/myorg/myrepo/issues/42"
            assert result["number"] == "42"

    def test_push_github_issue_failure(self):
        from azext_prototype.stages.backlog_push import push_github_issue

        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stderr="authentication failed",
                stdout="",
            )

            result = push_github_issue(
                "myorg",
                "myrepo",
                {"title": "VNet"},
            )
            assert "error" in result
            assert "authentication" in result["error"]

    def test_push_devops_feature_success(self):
        from azext_prototype.stages.backlog_push import push_devops_feature

        devops_response = json.dumps(
            {
                "id": 123,
                "_links": {"html": {"href": "https://dev.azure.com/org/proj/_workitems/edit/123"}},
            }
        )
        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=devops_response,
            )

            result = push_devops_feature(
                "myorg",
                "myproj",
                {"title": "VNet", "description": "desc"},
            )
            assert result["id"] == 123
            assert "dev.azure.com" in result["url"]

    def test_push_devops_feature_failure(self):
        from azext_prototype.stages.backlog_push import push_devops_feature

        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stderr="project not found",
                stdout="",
            )

            result = push_devops_feature("myorg", "myproj", {"title": "VNet"})
            assert "error" in result

    # --- Lines 48-49: check_devops_ext FileNotFoundError ---

    def test_check_devops_ext_not_installed(self):
        from azext_prototype.stages.backlog_push import check_devops_ext

        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError
            assert check_devops_ext() is False

    # --- Lines 83-84: format_github_body with dict tasks ---

    def test_format_github_body_dict_tasks(self):
        from azext_prototype.stages.backlog_push import format_github_body

        item = {
            "description": "desc",
            "tasks": [
                {"title": "Done task", "done": True},
                {"title": "Open task", "done": False},
            ],
        }
        body = format_github_body(item)
        assert "- [x] Done task" in body
        assert "- [ ] Open task" in body

    # --- Lines 92-114: format_github_body with children ---

    def test_format_github_body_children(self):
        from azext_prototype.stages.backlog_push import format_github_body

        item = {
            "description": "Parent",
            "children": [
                {
                    "title": "Child Story",
                    "effort": "S",
                    "description": "Child desc",
                    "acceptance_criteria": ["AC1"],
                    "tasks": [
                        {"title": "Sub done", "done": True},
                        "Sub open",
                    ],
                },
            ],
        }
        body = format_github_body(item)
        assert "## Stories" in body
        assert "### Child Story [S]" in body
        assert "Child desc" in body
        assert "1. AC1" in body
        assert "- [x] Sub done" in body
        assert "- [ ] Sub open" in body

    # --- Lines 150-153: format_devops_description with dict tasks ---

    def test_format_devops_description_dict_tasks(self):
        from azext_prototype.stages.backlog_push import format_devops_description

        item = {
            "tasks": [
                {"title": "Done", "done": True},
                {"title": "Open", "done": False},
            ],
        }
        desc = format_devops_description(item)
        assert "&#9745; Done" in desc
        assert "&#9744; Open" in desc

    # --- Lines 230-231: push_github_issue FileNotFoundError ---

    def test_push_github_issue_not_installed(self):
        from azext_prototype.stages.backlog_push import push_github_issue

        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError
            result = push_github_issue("org", "repo", {"title": "T"})
            assert "error" in result
            assert "gh CLI not found" in result["error"]

    # --- Lines 261, 280: push_devops_story / push_devops_task ---

    def test_push_devops_story_success(self):
        from azext_prototype.stages.backlog_push import push_devops_story

        resp = json.dumps(
            {
                "id": 200,
                "_links": {"html": {"href": "https://dev.azure.com/o/p/_workitems/edit/200"}},
            }
        )
        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=resp)
            result = push_devops_story("o", "p", {"title": "Story"}, parent_id=100)
            assert result["id"] == 200

    def test_push_devops_task_success(self):
        from azext_prototype.stages.backlog_push import push_devops_task

        resp = json.dumps(
            {
                "id": 300,
                "_links": {"html": {"href": "https://dev.azure.com/o/p/_workitems/edit/300"}},
            }
        )
        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=resp)
            result = push_devops_task("o", "p", {"title": "Task"}, parent_id=200)
            assert result["id"] == 300

    # --- Line 326: _push_devops_work_item with epic (area path) ---

    def test_push_devops_feature_with_epic_area(self):
        from azext_prototype.stages.backlog_push import push_devops_feature

        resp = json.dumps(
            {
                "id": 10,
                "_links": {"html": {"href": "https://dev.azure.com/o/p/_workitems/edit/10"}},
            }
        )
        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=resp)
            result = push_devops_feature("o", "p", {"title": "T", "epic": "Infra"})
            assert result["id"] == 10
            cmd_args = mock_run.call_args[0][0]
            assert "--area" in cmd_args
            assert "p\\Infra" in cmd_args

    # --- Line 350: url fallback to data["url"] ---

    def test_push_devops_url_fallback(self):
        from azext_prototype.stages.backlog_push import push_devops_feature

        resp = json.dumps({"id": 50, "url": "https://fallback-url", "_links": {}})
        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=resp)
            result = push_devops_feature("o", "p", {"title": "T"})
            assert result["url"] == "https://fallback-url"

    # --- Line 354: parent linking path ---

    def test_push_devops_story_calls_link_parent(self):
        from azext_prototype.stages.backlog_push import push_devops_story

        resp = json.dumps(
            {
                "id": 77,
                "_links": {"html": {"href": "https://dev.azure.com/o/p/_workitems/edit/77"}},
            }
        )
        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=resp)
            result = push_devops_story("o", "p", {"title": "S"}, parent_id=10)
            assert result["id"] == 77
            # Second call should be the _link_parent relation add
            assert mock_run.call_count == 2
            link_cmd = mock_run.call_args_list[1][0][0]
            assert "relation" in link_cmd
            assert "parent" in link_cmd

    # --- Lines 357-358: JSONDecodeError fallback ---

    def test_push_devops_json_decode_error(self):
        from azext_prototype.stages.backlog_push import push_devops_feature

        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not-json-output")
            result = push_devops_feature("o", "p", {"title": "T"})
            assert result["url"] == ""
            assert result["id"] == "not-json-output"

    # --- Lines 360-361: _push_devops_work_item FileNotFoundError ---

    def test_push_devops_feature_not_installed(self):
        from azext_prototype.stages.backlog_push import push_devops_feature

        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError
            result = push_devops_feature("o", "p", {"title": "T"})
            assert "error" in result
            assert "az CLI not found" in result["error"]

    # --- Lines 366-388: _link_parent error handling ---

    def test_link_parent_file_not_found(self):
        from azext_prototype.stages.backlog_push import _link_parent

        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError
            # Should not raise — just logs a warning
            _link_parent("o", "p", 10, 5)

    def test_link_parent_subprocess_error(self):
        import subprocess as sp

        from azext_prototype.stages.backlog_push import _link_parent

        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.side_effect = sp.SubprocessError("fail")
            _link_parent("o", "p", 10, 5)


# ======================================================================
# BacklogSession Tests
# ======================================================================


class TestBacklogSession:
    """Test the interactive backlog session."""

    def _make_session(self, project_dir, mock_ai_provider, items_response="[]"):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.stages.backlog_session import BacklogSession
        from azext_prototype.stages.backlog_state import BacklogState

        mock_ai_provider.chat.return_value = AIResponse(
            content=items_response,
            model="test",
        )

        registry = AgentRegistry()
        register_all_builtin(registry)

        ctx = AgentContext(
            project_config={"project": {"name": "test"}},
            project_dir=str(project_dir),
            ai_provider=mock_ai_provider,
        )

        backlog_state = BacklogState(str(project_dir))
        session = BacklogSession(
            ctx,
            registry,
            backlog_state=backlog_state,
        )
        return session, backlog_state

    def test_generate_from_ai(self, tmp_project, mock_ai_provider):
        items_json = json.dumps(
            [
                {
                    "epic": "Infra",
                    "title": "VNet",
                    "effort": "M",
                    "tasks": ["T1"],
                    "description": "d",
                    "acceptance_criteria": ["AC1"],
                },
            ]
        )
        session, state = self._make_session(tmp_project, mock_ai_provider, items_json)

        output = []
        result = session.run(
            design_context="Sample arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: "done",
            print_fn=output.append,
        )

        assert result.items_generated == 1
        assert not result.cancelled

    def test_resume_from_state(self, tmp_project, mock_ai_provider):
        from azext_prototype.stages.backlog_state import BacklogState

        # Pre-populate state
        state = BacklogState(str(tmp_project))
        state.set_items([{"epic": "Pre", "title": "Existing", "effort": "S"}])
        state.set_context_hash("Sample arch")

        session, _ = self._make_session(tmp_project, mock_ai_provider)
        session._backlog_state = state

        output = []
        result = session.run(
            design_context="Sample arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: "done",
            print_fn=output.append,
        )

        assert result.items_generated == 1
        # Should have used cached items
        joined = "\n".join(output)
        assert "cached" in joined.lower() or "resumed" in joined.lower()

    def test_slash_list(self, tmp_project, mock_ai_provider):
        items_json = json.dumps(
            [
                {"epic": "Infra", "title": "VNet", "effort": "M"},
            ]
        )
        session, state = self._make_session(tmp_project, mock_ai_provider, items_json)

        inputs = iter(["/list", "done"])
        output = []
        session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: next(inputs),
            print_fn=output.append,
        )
        joined = "\n".join(output)
        assert "VNet" in joined

    def test_slash_show(self, tmp_project, mock_ai_provider):
        items_json = json.dumps(
            [
                {
                    "epic": "Infra",
                    "title": "VNet",
                    "description": "Configure virtual network",
                    "effort": "M",
                    "acceptance_criteria": ["AC1"],
                    "tasks": ["T1"],
                },
            ]
        )
        session, state = self._make_session(tmp_project, mock_ai_provider, items_json)

        inputs = iter(["/show 1", "done"])
        output = []
        session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: next(inputs),
            print_fn=output.append,
        )
        joined = "\n".join(output)
        assert "Configure virtual network" in joined

    def test_slash_save(self, tmp_project, mock_ai_provider):
        items_json = json.dumps(
            [
                {
                    "epic": "Infra",
                    "title": "VNet",
                    "effort": "M",
                    "description": "d",
                    "acceptance_criteria": ["AC1"],
                    "tasks": ["T1"],
                },
            ]
        )
        session, state = self._make_session(tmp_project, mock_ai_provider, items_json)

        inputs = iter(["/save", "done"])
        output = []
        session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: next(inputs),
            print_fn=output.append,
        )

        backlog_md = tmp_project / "concept" / "docs" / "BACKLOG.md"
        assert backlog_md.exists()
        content = backlog_md.read_text()
        assert "VNet" in content

    def test_slash_quit(self, tmp_project, mock_ai_provider):
        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state = self._make_session(tmp_project, mock_ai_provider, items_json)

        output = []
        result = session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: "/quit",
            print_fn=output.append,
        )
        assert result.cancelled

    def test_eof_cancels_session(self, tmp_project, mock_ai_provider):
        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state = self._make_session(tmp_project, mock_ai_provider, items_json)

        def eof_input(p):
            raise EOFError

        output = []
        result = session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=eof_input,
            print_fn=output.append,
        )
        assert result.cancelled

    def test_quick_mode_cancel(self, tmp_project, mock_ai_provider):
        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state = self._make_session(tmp_project, mock_ai_provider, items_json)

        output = []
        result = session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            quick=True,
            input_fn=lambda p: "n",
            print_fn=output.append,
        )
        assert result.cancelled or result.items_pushed == 0

    def test_refresh_forces_regeneration(self, tmp_project, mock_ai_provider):
        """Even with cached state, --refresh forces new AI generation."""
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.stages.backlog_state import BacklogState

        state = BacklogState(str(tmp_project))
        state.set_items([{"epic": "Old", "title": "Old Item", "effort": "S"}])
        state.set_context_hash("arch")

        new_items_json = json.dumps(
            [
                {"epic": "New", "title": "New Item", "effort": "M"},
            ]
        )

        # Create session first, THEN override the mock return value
        # (_make_session defaults items_response="[]" which overwrites the mock)
        session, _ = self._make_session(tmp_project, mock_ai_provider)
        session._backlog_state = state
        mock_ai_provider.chat.return_value = AIResponse(content=new_items_json, model="t")

        output = []
        result = session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            refresh=True,
            input_fn=lambda p: "done",
            print_fn=output.append,
        )

        assert result.items_generated == 1
        assert state.state["items"][0]["title"] == "New Item"

    def test_slash_remove(self, tmp_project, mock_ai_provider):
        items_json = json.dumps(
            [
                {"epic": "A", "title": "Item1", "effort": "S"},
                {"epic": "A", "title": "Item2", "effort": "M"},
            ]
        )
        session, state = self._make_session(tmp_project, mock_ai_provider, items_json)

        inputs = iter(["/remove 1", "done"])
        output = []
        session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: next(inputs),
            print_fn=output.append,
        )
        assert len(state.state["items"]) == 1
        assert state.state["items"][0]["title"] == "Item2"


# ======================================================================
# Scope Injection Tests
# ======================================================================


class TestScopeInjection:
    """Test scope loading and injection into backlog generation."""

    def test_load_scope_from_discovery(self, tmp_project):
        from azext_prototype.custom import _load_discovery_scope

        # Create discovery state with scope
        state_dir = tmp_project / ".prototype" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        discovery_data = {
            "scope": {
                "in_scope": ["API Gateway", "Database"],
                "out_of_scope": ["Mobile app"],
                "deferred": ["Analytics dashboard"],
            },
            "project": {"summary": ""},
            "requirements": {"functional": [], "non_functional": []},
        }
        with open(state_dir / "discovery.yaml", "w") as f:
            yaml.dump(discovery_data, f)

        scope = _load_discovery_scope(str(tmp_project))
        assert scope is not None
        assert "API Gateway" in scope["in_scope"]
        assert "Mobile app" in scope["out_of_scope"]
        assert "Analytics dashboard" in scope["deferred"]

    def test_load_scope_no_discovery(self, tmp_project):
        from azext_prototype.custom import _load_discovery_scope

        scope = _load_discovery_scope(str(tmp_project))
        assert scope is None

    def test_load_scope_empty_scope(self, tmp_project):
        from azext_prototype.custom import _load_discovery_scope

        state_dir = tmp_project / ".prototype" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        discovery_data = {
            "scope": {"in_scope": [], "out_of_scope": [], "deferred": []},
            "project": {"summary": ""},
            "requirements": {"functional": [], "non_functional": []},
        }
        with open(state_dir / "discovery.yaml", "w") as f:
            yaml.dump(discovery_data, f)

        scope = _load_discovery_scope(str(tmp_project))
        assert scope is None


# ======================================================================
# AI-Populated Templates Tests
# ======================================================================


class TestAIPopulatedTemplates:
    """Test AI-populated doc/speckit templates."""

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_generate_docs_static_fallback(self, mock_dir, project_with_config):
        """Without design context, uses static template rendering."""
        from azext_prototype.custom import prototype_generate_docs

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        out_dir = str(project_with_config / "docs")
        result = prototype_generate_docs(cmd, path=out_dir, json_output=True)
        assert result["status"] == "generated"

        docs_path = project_with_config / "docs"
        assert docs_path.is_dir()

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_generate_speckit_with_manifest(self, mock_dir, project_with_config):
        """Speckit includes manifest.json."""
        from azext_prototype.custom import prototype_generate_speckit

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        out_dir = str(project_with_config / "concept" / ".specify")
        prototype_generate_speckit(cmd, path=out_dir)

        manifest_path = project_with_config / "concept" / ".specify" / "manifest.json"
        assert manifest_path.exists()

        with open(manifest_path) as f:
            manifest = json.load(f)
        assert "templates" in manifest

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_generate_docs_with_design_context(self, mock_dir, project_with_design, mock_ai_provider):
        """When design context exists, doc-agent is attempted for population."""
        from azext_prototype.custom import prototype_generate_docs

        mock_dir.return_value = str(project_with_design)
        cmd = MagicMock()

        # AI provider factory is imported locally inside prototype_generate_docs
        with patch("azext_prototype.ai.factory.create_ai_provider") as mock_factory:
            mock_factory.return_value = mock_ai_provider

            out_dir = str(project_with_design / "docs")
            result = prototype_generate_docs(cmd, path=out_dir, json_output=True)

        assert result["status"] == "generated"

    def test_generate_templates_uses_rich_ui(self, project_with_config):
        """_generate_templates uses console.print_file_list instead of print()."""
        from pathlib import Path

        from azext_prototype.custom import _generate_templates

        output_dir = Path(str(project_with_config)) / "test_docs"
        project_dir = str(project_with_config)
        project_config = {"project": {"name": "test"}}

        # Patch the module-level console singleton. We must use importlib
        # because `import azext_prototype.ui.console` can resolve to the
        # `console` variable re-exported in azext_prototype.ui.__init__
        # instead of the submodule (name collision on Python 3.10).
        import importlib

        _console_mod = importlib.import_module("azext_prototype.ui.console")

        with patch.object(_console_mod, "console") as mock_console:
            generated = _generate_templates(output_dir, project_dir, project_config, "docs")

        # Should use console.print_file_list instead of bare print()
        mock_console.print_file_list.assert_called_once()
        mock_console.print_dim.assert_called_once()
        assert len(generated) >= 1


# ======================================================================
# Command-level Integration Tests
# ======================================================================


class TestBacklogCommandIntegration:
    """Test the prototype_generate_backlog command with new session delegation."""

    @patch(f"{_CUSTOM_MODULE}._prepare_command")
    def test_backlog_status_no_state(self, mock_prepare, project_with_config, mock_ai_provider):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.custom import prototype_generate_backlog

        ctx = AgentContext(
            project_config={"project": {"name": "test"}},
            project_dir=str(project_with_config),
            ai_provider=mock_ai_provider,
        )
        from azext_prototype.config import ProjectConfig

        config = ProjectConfig(str(project_with_config))
        mock_prepare.return_value = (str(project_with_config), config, AgentRegistry(), ctx)
        cmd = MagicMock()

        result = prototype_generate_backlog(cmd, status=True, json_output=True)
        assert result["status"] == "displayed"

    @patch(f"{_CUSTOM_MODULE}._prepare_command")
    def test_backlog_status_with_state(self, mock_prepare, project_with_design, mock_ai_provider):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.custom import prototype_generate_backlog
        from azext_prototype.stages.backlog_state import BacklogState

        ctx = AgentContext(
            project_config={"project": {"name": "test"}},
            project_dir=str(project_with_design),
            ai_provider=mock_ai_provider,
        )
        from azext_prototype.config import ProjectConfig

        config = ProjectConfig(str(project_with_design))
        mock_prepare.return_value = (str(project_with_design), config, AgentRegistry(), ctx)
        cmd = MagicMock()

        # Create backlog state
        state = BacklogState(str(project_with_design))
        state.set_items([{"epic": "Infra", "title": "VNet", "effort": "M"}])

        result = prototype_generate_backlog(cmd, status=True, json_output=True)
        assert result["status"] == "displayed"

    @patch(f"{_CUSTOM_MODULE}._check_requirements")
    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_backlog_invalid_provider_raises(self, mock_dir, mock_check_req, project_with_design, mock_ai_provider):
        from azext_prototype.custom import prototype_generate_backlog

        mock_dir.return_value = str(project_with_design)
        cmd = MagicMock()

        with patch(f"{_CUSTOM_MODULE}._build_context") as mock_ctx:
            from azext_prototype.agents.base import AgentContext

            ctx = AgentContext(
                project_config={"project": {"name": "test"}},
                project_dir=str(project_with_design),
                ai_provider=mock_ai_provider,
            )
            mock_ctx.return_value = ctx

            with pytest.raises(CLIError, match="Unsupported backlog provider"):
                prototype_generate_backlog(cmd, provider="jira", org="x", project="y")

    @patch(f"{_CUSTOM_MODULE}._check_requirements")
    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_backlog_no_design_raises(self, mock_dir, mock_check_req, project_with_config, mock_ai_provider):
        from azext_prototype.custom import prototype_generate_backlog

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        with patch(f"{_CUSTOM_MODULE}._build_context") as mock_ctx:
            from azext_prototype.agents.base import AgentContext

            ctx = AgentContext(
                project_config={"project": {"name": "test"}},
                project_dir=str(project_with_config),
                ai_provider=mock_ai_provider,
            )
            mock_ctx.return_value = ctx

            with pytest.raises(CLIError, match="No architecture design found"):
                prototype_generate_backlog(cmd, provider="github", org="x", project="y")

    @patch(f"{_CUSTOM_MODULE}._check_requirements")
    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_backlog_delegates_to_session(self, mock_dir, mock_check_req, project_with_design, mock_ai_provider):
        """The command delegates to BacklogSession.run()."""
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.custom import prototype_generate_backlog

        mock_dir.return_value = str(project_with_design)
        cmd = MagicMock()

        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        mock_ai_provider.chat.return_value = AIResponse(content=items_json, model="t")

        with patch(f"{_CUSTOM_MODULE}._build_context") as mock_ctx:
            from azext_prototype.agents.base import AgentContext

            ctx = AgentContext(
                project_config={"project": {"name": "test"}},
                project_dir=str(project_with_design),
                ai_provider=mock_ai_provider,
            )
            mock_ctx.return_value = ctx

            with patch("azext_prototype.stages.backlog_session.BacklogSession.run") as mock_run:
                from azext_prototype.stages.backlog_session import BacklogResult

                mock_run.return_value = BacklogResult(
                    items_generated=1,
                    items_pushed=0,
                )

                result = prototype_generate_backlog(
                    cmd,
                    provider="github",
                    org="o",
                    project="p",
                    json_output=True,
                )

        assert result["status"] == "generated"
        assert result["items_generated"] == 1

    @patch(f"{_CUSTOM_MODULE}._check_requirements")
    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_backlog_cancelled_returns_cancelled(self, mock_dir, mock_check_req, project_with_design, mock_ai_provider):
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.custom import prototype_generate_backlog

        mock_dir.return_value = str(project_with_design)
        cmd = MagicMock()

        mock_ai_provider.chat.return_value = AIResponse(content="[]", model="t")

        with patch(f"{_CUSTOM_MODULE}._build_context") as mock_ctx:
            from azext_prototype.agents.base import AgentContext

            ctx = AgentContext(
                project_config={"project": {"name": "test"}},
                project_dir=str(project_with_design),
                ai_provider=mock_ai_provider,
            )
            mock_ctx.return_value = ctx

            with patch("azext_prototype.stages.backlog_session.BacklogSession.run") as mock_run:
                from azext_prototype.stages.backlog_session import BacklogResult

                mock_run.return_value = BacklogResult(cancelled=True)

                result = prototype_generate_backlog(
                    cmd,
                    provider="github",
                    org="o",
                    project="p",
                    json_output=True,
                )

        assert result["status"] == "cancelled"


# ======================================================================
# /add enrichment tests (Phase 9)
# ======================================================================


class TestAddEnrichment:
    """Test that /add uses PM agent to enrich items."""

    def _make_session(self, tmp_project, pm_response=None, pm_raises=False):
        from azext_prototype.agents.base import AgentCapability, AgentContext
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.stages.backlog_session import BacklogSession
        from azext_prototype.stages.backlog_state import BacklogState

        ctx = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )

        pm = MagicMock()
        pm.name = "project-manager"
        pm.get_system_messages.return_value = []

        if pm_raises:
            ctx.ai_provider.chat.side_effect = RuntimeError("AI error")
        elif pm_response:
            ctx.ai_provider.chat.return_value = AIResponse(
                content=pm_response,
                model="gpt-4o",
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )
        else:
            ctx.ai_provider.chat.return_value = AIResponse(
                content=json.dumps(
                    {
                        "epic": "API",
                        "title": "Add rate limiting",
                        "description": "Implement API rate limiting for all endpoints",
                        "acceptance_criteria": ["AC1: Rate limit headers returned", "AC2: 429 status on exceed"],
                        "tasks": ["Add middleware", "Configure limits", "Add tests"],
                        "effort": "L",
                    }
                ),
                model="gpt-4o",
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )

        registry = MagicMock()

        def find_by_cap(cap):
            if cap == AgentCapability.BACKLOG_GENERATION:
                return [pm]
            if cap == AgentCapability.QA:
                return []
            return []

        registry.find_by_capability.side_effect = find_by_cap

        state = BacklogState(str(tmp_project))
        state.set_items([{"title": "Existing"}])

        session = BacklogSession(ctx, registry, backlog_state=state)
        return session, pm, state

    def test_add_enriched_via_pm(self, tmp_project):
        session, pm, state = self._make_session(tmp_project)

        result = session._enrich_new_item("Add rate limiting to the API")

        assert result["title"] == "Add rate limiting"
        assert result["epic"] == "API"
        assert len(result["acceptance_criteria"]) == 2
        assert len(result["tasks"]) == 3
        assert result["effort"] == "L"

    def test_add_pm_failure_falls_back_to_bare(self, tmp_project):
        session, pm, state = self._make_session(tmp_project, pm_raises=True)

        result = session._enrich_new_item("Add rate limiting")

        assert result["title"] == "Add rate limiting"
        assert result["epic"] == "Added"
        assert result["acceptance_criteria"] == []

    def test_add_pm_invalid_json_falls_back(self, tmp_project):
        session, pm, state = self._make_session(
            tmp_project,
            pm_response="Sure, here's a rate limiting story with details...",
        )

        result = session._enrich_new_item("Add rate limiting")

        assert result["title"] == "Add rate limiting"
        assert result["epic"] == "Added"

    def test_add_no_pm_agent_uses_bare(self, tmp_project):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.stages.backlog_session import BacklogSession
        from azext_prototype.stages.backlog_state import BacklogState

        ctx = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )

        registry = MagicMock()
        registry.find_by_capability.return_value = []

        session = BacklogSession(ctx, registry, backlog_state=BacklogState(str(tmp_project)))

        result = session._enrich_new_item("Add rate limiting")

        assert result["title"] == "Add rate limiting"
        assert result["epic"] == "Added"

    def test_add_enriched_missing_fields_get_defaults(self, tmp_project):
        session, pm, state = self._make_session(
            tmp_project,
            pm_response=json.dumps({"title": "Rate Limiting", "effort": "S"}),
        )

        result = session._enrich_new_item("Add rate limiting")

        assert result["title"] == "Rate Limiting"
        assert result["epic"] == "Added"  # defaulted
        assert result["acceptance_criteria"] == []  # defaulted
        assert result["tasks"] == []  # defaulted
        assert result["effort"] == "S"


# ======================================================================
# BacklogSession Coverage — additional tests for uncovered lines
# ======================================================================

_SESSION_MODULE = "azext_prototype.stages.backlog_session"


class TestBacklogSessionCoverage:
    """Additional tests to cover uncovered lines in backlog_session.py."""

    def _make_session(
        self,
        project_dir,
        mock_ai_provider=None,
        items_response="[]",
        *,
        with_qa=True,
    ):
        from azext_prototype.agents.base import AgentCapability, AgentContext
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.stages.backlog_session import BacklogSession
        from azext_prototype.stages.backlog_state import BacklogState

        if mock_ai_provider is None:
            mock_ai_provider = MagicMock()

        mock_ai_provider.chat.return_value = AIResponse(
            content=items_response,
            model="test",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

        pm = MagicMock()
        pm.name = "project-manager"
        pm.get_system_messages.return_value = []

        qa = MagicMock()
        qa.name = "qa-engineer"

        registry = MagicMock(spec=AgentRegistry)

        def find_by_cap(cap):
            if cap == AgentCapability.BACKLOG_GENERATION:
                return [pm]
            if cap == AgentCapability.QA:
                return [qa] if with_qa else []
            return []

        registry.find_by_capability.side_effect = find_by_cap

        ctx = AgentContext(
            project_config={"project": {"name": "test"}},
            project_dir=str(project_dir),
            ai_provider=mock_ai_provider,
        )

        backlog_state = BacklogState(str(project_dir))
        session = BacklogSession(ctx, registry, backlog_state=backlog_state)
        return session, backlog_state, mock_ai_provider

    # ----------------------------------------------------------
    # Line 151: escalation tracker load
    # ----------------------------------------------------------

    def test_escalation_tracker_loaded_when_exists(self, tmp_project):
        """When escalation.yaml exists, __init__ loads it (line 151)."""
        import yaml as _yaml

        esc_path = tmp_project / ".prototype" / "state" / "escalation.yaml"
        esc_path.parent.mkdir(parents=True, exist_ok=True)
        esc_path.write_text(_yaml.dump({"entries": [], "active_count": 0}))

        session, _, _ = self._make_session(tmp_project)
        # If it loaded without error, the path is covered
        assert session._escalation_tracker is not None

    # ----------------------------------------------------------
    # Lines 227-228: no PM agent
    # ----------------------------------------------------------

    def test_run_no_pm_agent_returns_cancelled(self, tmp_project):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.stages.backlog_session import BacklogSession
        from azext_prototype.stages.backlog_state import BacklogState

        ctx = AgentContext(
            project_config={"project": {"name": "test"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )
        registry = MagicMock()
        registry.find_by_capability.return_value = []

        session = BacklogSession(ctx, registry, backlog_state=BacklogState(str(tmp_project)))

        output = []
        result = session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: "done",
            print_fn=output.append,
        )
        assert result.cancelled
        joined = "\n".join(output)
        assert "No project-manager agent" in joined

    # ----------------------------------------------------------
    # Lines 231-232: no AI provider
    # ----------------------------------------------------------

    def test_run_no_ai_provider_returns_cancelled(self, tmp_project):
        from azext_prototype.agents.base import AgentCapability, AgentContext
        from azext_prototype.stages.backlog_session import BacklogSession
        from azext_prototype.stages.backlog_state import BacklogState

        ctx = AgentContext(
            project_config={"project": {"name": "test"}},
            project_dir=str(tmp_project),
            ai_provider=None,
        )

        pm = MagicMock()
        pm.name = "project-manager"
        registry = MagicMock()

        def find_by_cap(cap):
            if cap == AgentCapability.BACKLOG_GENERATION:
                return [pm]
            return []

        registry.find_by_capability.side_effect = find_by_cap

        session = BacklogSession(ctx, registry, backlog_state=BacklogState(str(tmp_project)))

        output = []
        result = session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: "done",
            print_fn=output.append,
        )
        assert result.cancelled
        joined = "\n".join(output)
        assert "No AI provider" in joined

    # ----------------------------------------------------------
    # Lines 297: empty input skip in interactive loop
    # ----------------------------------------------------------

    def test_empty_input_skipped(self, tmp_project):
        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        inputs = iter(["", "done"])
        output = []
        result = session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: next(inputs),
            print_fn=output.append,
        )
        assert not result.cancelled
        assert result.items_generated == 1

    # ----------------------------------------------------------
    # Lines 328-364: intent classification to command + NL mutate
    # ----------------------------------------------------------

    def test_intent_command_routes_to_slash(self, tmp_project):
        """Natural language classified as COMMAND is routed (lines 328-342)."""
        from azext_prototype.stages.intent import IntentKind, IntentResult

        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        # Mock the intent classifier to return a COMMAND
        session._intent_classifier = MagicMock()
        session._intent_classifier.classify.return_value = IntentResult(
            kind=IntentKind.COMMAND,
            command="/list",
            args="",
            original_input="show all items",
            confidence=0.9,
        )

        inputs = iter(["show all items", "done"])
        output = []
        session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: next(inputs),
            print_fn=output.append,
        )
        # /list should have been handled -- items are listed
        joined = "\n".join(output)
        assert "B" in joined

    def test_intent_command_push_breaks_loop(self, tmp_project):
        """Intent push that succeeds returns 'pushed' (line 340-341)."""
        from azext_prototype.stages.intent import IntentKind, IntentResult

        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        session._intent_classifier = MagicMock()
        session._intent_classifier.classify.return_value = IntentResult(
            kind=IntentKind.COMMAND,
            command="/push",
            args="",
            original_input="push items",
            confidence=0.9,
        )

        with patch(f"{_SESSION_MODULE}.check_gh_auth", return_value=True), patch(
            f"{_SESSION_MODULE}.push_github_issue"
        ) as mock_push:
            mock_push.return_value = {"url": "https://github.com/o/p/issues/1"}

            output = []
            result = session.run(
                design_context="arch",
                provider="github",
                org="o",
                project="p",
                input_fn=lambda p: "push items",
                print_fn=output.append,
            )
        assert result.items_pushed == 1

    def test_natural_language_mutate_items(self, tmp_project):
        """NL CONVERSATIONAL triggers _mutate_items (lines 344-364)."""
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.stages.intent import IntentKind, IntentResult

        items_json = json.dumps([{"epic": "A", "title": "Original", "effort": "S"}])
        session, state, ai = self._make_session(tmp_project, items_response=items_json)

        updated_json = json.dumps([{"epic": "A", "title": "Updated", "effort": "M"}])

        session._intent_classifier = MagicMock()
        session._intent_classifier.classify.return_value = IntentResult(
            kind=IntentKind.CONVERSATIONAL,
            original_input="change title to Updated",
        )

        call_count = [0]

        def side_effect_chat(msgs, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return AIResponse(
                    content=items_json,
                    model="t",
                    usage={"prompt_tokens": 10, "completion_tokens": 5},
                )
            else:
                return AIResponse(
                    content=updated_json,
                    model="t",
                    usage={"prompt_tokens": 10, "completion_tokens": 5},
                )

        ai.chat.side_effect = side_effect_chat

        inputs = iter(["change title to Updated", "done"])
        output = []
        session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: next(inputs),
            print_fn=output.append,
        )
        assert state.state["items"][0]["title"] == "Updated"

    def test_natural_language_mutate_returns_none(self, tmp_project):
        """When _mutate_items returns None, user sees error (line 362)."""
        from azext_prototype.stages.intent import IntentKind, IntentResult

        items_json = json.dumps([{"epic": "A", "title": "T", "effort": "S"}])
        session, state, ai = self._make_session(tmp_project, items_response=items_json)

        session._intent_classifier = MagicMock()
        session._intent_classifier.classify.return_value = IntentResult(
            kind=IntentKind.CONVERSATIONAL,
            original_input="do something weird",
        )

        # Force _mutate_items to return None (the path that shows the error)
        session._mutate_items = MagicMock(return_value=None)

        inputs = iter(["do something weird", "done"])
        output = []
        session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: next(inputs),
            print_fn=output.append,
        )
        joined = "\n".join(output)
        assert "Could not update" in joined

    # ----------------------------------------------------------
    # Lines 374, 378: report phase with push_urls
    # ----------------------------------------------------------

    def test_report_collects_push_urls(self, tmp_project):
        """Report phase extracts urls from push_results (lines 374-378)."""
        session, state, _ = self._make_session(tmp_project)

        state.set_items([{"epic": "A", "title": "B", "effort": "S"}])
        state.mark_item_pushed(0, "https://github.com/o/p/issues/1")
        state.set_context_hash("arch")
        session._backlog_state = state

        output = []
        result = session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: "done",
            print_fn=output.append,
        )
        assert result.items_pushed == 1
        assert "https://github.com/o/p/issues/1" in result.push_urls

    # ----------------------------------------------------------
    # Lines 407, 410-411: quick mode EOF
    # ----------------------------------------------------------

    def test_quick_mode_eof_cancels(self, tmp_project):
        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        def eof_input(p):
            raise EOFError

        output = []
        result = session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            quick=True,
            input_fn=eof_input,
            print_fn=output.append,
        )
        assert result.cancelled

    def test_quick_mode_confirm_push(self, tmp_project):
        """Quick mode confirm=yes triggers push (line 417)."""
        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        with patch(f"{_SESSION_MODULE}.check_gh_auth", return_value=True), patch(
            f"{_SESSION_MODULE}.push_github_issue"
        ) as mock_push:
            mock_push.return_value = {"url": "https://github.com/o/p/issues/1"}

            output = []
            result = session.run(
                design_context="arch",
                provider="github",
                org="o",
                project="p",
                quick=True,
                input_fn=lambda p: "y",
                print_fn=output.append,
            )
        assert result.items_pushed == 1

    # ----------------------------------------------------------
    # Lines 440-448, 494, 504: scope text + devops provider
    # ----------------------------------------------------------

    def test_generate_items_with_full_scope(self, tmp_project):
        """Scope in/out/deferred all present (lines 440-448, 494)."""
        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state, ai = self._make_session(tmp_project, items_response=items_json)

        scope = {
            "in_scope": ["API Gateway"],
            "out_of_scope": ["Mobile app"],
            "deferred": ["Analytics"],
        }

        output = []
        session.run(
            design_context="arch",
            scope=scope,
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: "done",
            print_fn=output.append,
        )

        call_args = ai.chat.call_args
        messages = call_args[0][0]
        content = messages[-1].content
        assert "In Scope" in content
        assert "API Gateway" in content
        assert "Out of Scope" in content
        assert "Mobile app" in content
        assert "Deferred" in content
        assert "Analytics" in content

    def test_generate_items_devops_format(self, tmp_project):
        """DevOps provider uses hierarchical JSON schema (line 504)."""
        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state, ai = self._make_session(tmp_project, items_response=items_json)

        output = []
        session.run(
            design_context="arch",
            provider="devops",
            org="o",
            project="p",
            input_fn=lambda p: "done",
            print_fn=output.append,
        )

        call_args = ai.chat.call_args
        messages = call_args[0][0]
        content = messages[-1].content
        assert "Azure DevOps hierarchy" in content
        assert "children" in content

    # ----------------------------------------------------------
    # Lines 571-599: _mutate_items
    # ----------------------------------------------------------

    def test_mutate_items_no_pm_returns_none(self, tmp_project):
        """_mutate_items returns None when no PM agent (line 571)."""
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.stages.backlog_session import BacklogSession
        from azext_prototype.stages.backlog_state import BacklogState

        ctx = AgentContext(
            project_config={"project": {"name": "test"}},
            project_dir=str(tmp_project),
            ai_provider=None,
        )
        registry = MagicMock()
        registry.find_by_capability.return_value = []

        session = BacklogSession(ctx, registry, backlog_state=BacklogState(str(tmp_project)))

        result = session._mutate_items("add an item", "arch")
        assert result is None

    def test_mutate_items_success(self, tmp_project):
        """_mutate_items calls AI and parses JSON (lines 571-599)."""
        from azext_prototype.ai.provider import AIResponse

        updated = [{"epic": "A", "title": "Updated", "effort": "M"}]
        session, state, ai = self._make_session(tmp_project)
        state.set_items([{"epic": "A", "title": "Old", "effort": "S"}])

        ai.chat.return_value = AIResponse(
            content=json.dumps(updated),
            model="t",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

        result = session._mutate_items("rename to Updated", "arch")
        assert result is not None
        assert result[0]["title"] == "Updated"

    # ----------------------------------------------------------
    # Lines 606-608: _parse_items with markdown fences
    # ----------------------------------------------------------

    def test_parse_items_markdown_fences(self):
        from azext_prototype.stages.backlog_session import BacklogSession

        raw = '```json\n[{"title": "A"}]\n```'
        items = BacklogSession._parse_items(raw)
        assert len(items) == 1
        assert items[0]["title"] == "A"

    def test_parse_items_bad_json_returns_empty(self):
        from azext_prototype.stages.backlog_session import BacklogSession

        items = BacklogSession._parse_items("this is not json")
        assert items == []

    # ----------------------------------------------------------
    # Lines 634-637: push_all no pending items
    # ----------------------------------------------------------

    def test_push_all_no_pending(self, tmp_project):
        """_push_all with no pending items returns early (lines 634-637)."""
        session, state, _ = self._make_session(tmp_project)

        state.set_items([{"epic": "A", "title": "B", "effort": "S"}])
        state.mark_item_pushed(0, "url")

        output = []
        result = session._push_all("github", "o", "p", output.append, False)
        assert result.items_pushed == 1
        joined = "\n".join(output)
        assert "No pending" in joined

    # ----------------------------------------------------------
    # Lines 645-653: push auth check fails
    # ----------------------------------------------------------

    def test_push_all_github_no_auth(self, tmp_project):
        session, state, _ = self._make_session(tmp_project)
        state.set_items([{"title": "A"}])

        with patch(f"{_SESSION_MODULE}.check_gh_auth", return_value=False):
            output = []
            result = session._push_all("github", "o", "p", output.append, False)
        assert result.cancelled
        joined = "\n".join(output)
        assert "not authenticated" in joined.lower()

    def test_push_all_devops_no_ext(self, tmp_project):
        """DevOps push fails when extension missing (lines 651-656)."""
        session, state, _ = self._make_session(tmp_project)
        state.set_items([{"title": "A"}])

        with patch(f"{_SESSION_MODULE}.check_devops_ext", return_value=False):
            output = []
            result = session._push_all("devops", "o", "p", output.append, False)
        assert result.cancelled
        joined = "\n".join(output)
        assert "not available" in joined.lower()

    # ----------------------------------------------------------
    # Lines 672, 687-714: push devops feature with children
    # ----------------------------------------------------------

    def test_push_all_devops_with_children_and_tasks(self, tmp_project):
        """DevOps push: Feature -> Stories -> Tasks (lines 687-714)."""
        session, state, _ = self._make_session(tmp_project)
        state.set_items(
            [
                {
                    "title": "Feature1",
                    "children": [
                        {
                            "title": "Story1",
                            "tasks": [
                                {"title": "Task1", "done": False},
                                {"title": "Task2", "done": True},
                            ],
                        },
                    ],
                }
            ]
        )

        with patch(f"{_SESSION_MODULE}.check_devops_ext", return_value=True), patch(
            f"{_SESSION_MODULE}.push_devops_feature"
        ) as mock_feat, patch(f"{_SESSION_MODULE}.push_devops_story") as mock_story, patch(
            f"{_SESSION_MODULE}.push_devops_task"
        ) as mock_task:
            mock_feat.return_value = {
                "id": 100,
                "url": "https://dev.azure.com/o/p/_workitems/100",
            }
            mock_story.return_value = {
                "id": 101,
                "url": "https://dev.azure.com/o/p/_workitems/101",
            }
            mock_task.return_value = {"id": 102, "url": ""}

            output = []
            result = session._push_all("devops", "o", "p", output.append, False)

        assert result.items_pushed == 1
        assert len(result.push_urls) == 2  # feature + story
        mock_story.assert_called_once()
        # Only Task1 (done=False) should be pushed
        mock_task.assert_called_once()
        task_arg = mock_task.call_args[0][2]
        assert task_arg["title"] == "Task1"

    def test_push_all_item_error_routes_to_qa(self, tmp_project):
        """Push failure routes to QA (lines 674-685)."""
        session, state, _ = self._make_session(tmp_project)
        state.set_items([{"title": "FailItem"}])

        with patch(f"{_SESSION_MODULE}.check_gh_auth", return_value=True), patch(
            f"{_SESSION_MODULE}.push_github_issue"
        ) as mock_push, patch(f"{_SESSION_MODULE}.route_error_to_qa") as mock_qa:
            mock_push.return_value = {"error": "auth failed"}

            output = []
            result = session._push_all("github", "o", "p", output.append, False)

        assert result.items_failed == 1
        mock_qa.assert_called_once()

    # ----------------------------------------------------------
    # Lines 737-779: _push_single
    # ----------------------------------------------------------

    def test_push_single_invalid_index(self, tmp_project):
        """_push_single with out-of-range index (lines 738-740)."""
        session, state, _ = self._make_session(tmp_project)
        state.set_items([{"title": "Only"}])

        output = []
        session._push_single(5, "github", "o", "p", output.append, False)
        joined = "\n".join(output)
        assert "not found" in joined.lower()

    def test_push_single_github_success(self, tmp_project):
        """_push_single pushes a single github issue (lines 742-757)."""
        session, state, _ = self._make_session(tmp_project)
        state.set_items([{"title": "Item1"}])

        with patch(f"{_SESSION_MODULE}.push_github_issue") as mock_push:
            mock_push.return_value = {"url": "https://github.com/o/p/issues/1"}

            output = []
            session._push_single(0, "github", "o", "p", output.append, False)

        assert state.state["push_status"][0] == "pushed"
        joined = "\n".join(output)
        assert "github.com" in joined

    def test_push_single_error(self, tmp_project):
        """_push_single error marks item failed (lines 751-753)."""
        session, state, _ = self._make_session(tmp_project)
        state.set_items([{"title": "Item1"}])

        with patch(f"{_SESSION_MODULE}.push_github_issue") as mock_push:
            mock_push.return_value = {"error": "not found"}

            output = []
            session._push_single(0, "github", "o", "p", output.append, False)

        assert state.state["push_status"][0] == "failed"

    def test_push_single_devops_with_children(self, tmp_project):
        """_push_single devops creates children + tasks (lines 759-779)."""
        session, state, _ = self._make_session(tmp_project)
        state.set_items(
            [
                {
                    "title": "Feature",
                    "children": [
                        {
                            "title": "Story",
                            "tasks": [{"title": "Task", "done": False}],
                        }
                    ],
                }
            ]
        )

        with patch(f"{_SESSION_MODULE}.push_devops_feature") as mock_feat, patch(
            f"{_SESSION_MODULE}.push_devops_story"
        ) as mock_story, patch(f"{_SESSION_MODULE}.push_devops_task") as mock_task:
            mock_feat.return_value = {"id": 10, "url": "http://f"}
            mock_story.return_value = {"id": 11, "url": "http://s"}
            mock_task.return_value = {"id": 12, "url": ""}

            output = []
            session._push_single(0, "devops", "o", "p", output.append, False)

        mock_story.assert_called_once()
        mock_task.assert_called_once()

    # ----------------------------------------------------------
    # Lines 812, 815-829: slash commands /show, /add
    # ----------------------------------------------------------

    def test_slash_show_no_arg(self, tmp_project):
        """/show without number prints usage (line 812)."""
        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        inputs = iter(["/show", "done"])
        output = []
        session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: next(inputs),
            print_fn=output.append,
        )
        joined = "\n".join(output)
        assert "Usage: /show N" in joined

    def test_slash_add_with_description(self, tmp_project):
        """/add prompts for description and enriches (lines 815-829)."""
        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        inputs = iter(["/add", "New item description", "done"])
        output = []
        session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: next(inputs),
            print_fn=output.append,
        )
        assert len(state.state["items"]) == 2
        joined = "\n".join(output)
        assert "Added item 2" in joined

    def test_slash_add_eof(self, tmp_project):
        """/add with EOF during description input (lines 821-822)."""
        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        call_count = [0]

        def eof_on_second(p):
            call_count[0] += 1
            if call_count[0] == 1:
                return "/add"
            elif call_count[0] == 2:
                raise EOFError
            return "done"

        output = []
        session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=eof_on_second,
            print_fn=output.append,
        )
        # Items unchanged -- the add was cancelled
        assert len(state.state["items"]) == 1

    # ----------------------------------------------------------
    # Lines 840-842: /remove edge cases
    # ----------------------------------------------------------

    def test_slash_remove_invalid_arg(self, tmp_project):
        """/remove without number prints usage (lines 841-842)."""
        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        inputs = iter(["/remove", "done"])
        output = []
        session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: next(inputs),
            print_fn=output.append,
        )
        joined = "\n".join(output)
        assert "Usage: /remove N" in joined

    def test_slash_remove_out_of_range(self, tmp_project):
        """/remove with index out of range (line 840)."""
        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        inputs = iter(["/remove 99", "done"])
        output = []
        session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: next(inputs),
            print_fn=output.append,
        )
        joined = "\n".join(output)
        assert "not found" in joined.lower()

    # ----------------------------------------------------------
    # Lines 845-857: /preview command
    # ----------------------------------------------------------

    def test_slash_preview_github(self, tmp_project):
        items_json = json.dumps(
            [
                {"epic": "Infra", "title": "VNet", "effort": "M"},
                {"epic": "App", "title": "API", "effort": "L"},
            ]
        )
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        inputs = iter(["/preview", "done"])
        output = []
        session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: next(inputs),
            print_fn=output.append,
        )
        joined = "\n".join(output)
        assert "GitHub Issues" in joined
        assert "[Infra] VNet" in joined
        assert "[App] API" in joined

    def test_slash_preview_devops(self, tmp_project):
        """/preview for devops provider (no epic prefix, line 856)."""
        items_json = json.dumps([{"title": "Feature1", "effort": "M"}])
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        inputs = iter(["/preview", "done"])
        output = []
        session.run(
            design_context="arch",
            provider="devops",
            org="o",
            project="p",
            input_fn=lambda p: next(inputs),
            print_fn=output.append,
        )
        joined = "\n".join(output)
        assert "DevOps Work Items" in joined
        assert "Feature1" in joined

    # ----------------------------------------------------------
    # Lines 862-907: /push, /status, /help
    # ----------------------------------------------------------

    def test_slash_push_single(self, tmp_project):
        """/push N pushes single item (lines 862-865)."""
        items_json = json.dumps(
            [
                {"epic": "A", "title": "Item1", "effort": "S"},
                {"epic": "A", "title": "Item2", "effort": "M"},
            ]
        )
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        with patch(f"{_SESSION_MODULE}.push_github_issue") as mock_push:
            mock_push.return_value = {"url": "https://github.com/o/p/issues/1"}

            inputs = iter(["/push 1", "done"])
            output = []
            session.run(
                design_context="arch",
                provider="github",
                org="o",
                project="p",
                input_fn=lambda p: next(inputs),
                print_fn=output.append,
            )

        assert state.state["push_status"][0] == "pushed"
        assert state.state["push_status"][1] == "pending"

    def test_slash_push_all_breaks_on_success(self, tmp_project):
        """/push (all) breaks loop on success (line 868-869)."""
        items_json = json.dumps([{"epic": "A", "title": "Item1", "effort": "S"}])
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        with patch(f"{_SESSION_MODULE}.check_gh_auth", return_value=True), patch(
            f"{_SESSION_MODULE}.push_github_issue"
        ) as mock_push:
            mock_push.return_value = {"url": "https://github.com/o/p/issues/1"}

            output = []
            result = session.run(
                design_context="arch",
                provider="github",
                org="o",
                project="p",
                input_fn=lambda p: "/push",
                print_fn=output.append,
            )

        assert result.items_pushed == 1

    def test_slash_status(self, tmp_project):
        """Show push status per item (lines 871-880)."""
        session, state, _ = self._make_session(tmp_project)

        state.set_items(
            [
                {"epic": "A", "title": "Item1", "effort": "S"},
                {"epic": "A", "title": "Item2", "effort": "M"},
            ]
        )
        state.mark_item_pushed(0, "url")
        state.set_context_hash("arch")
        session._backlog_state = state

        inputs = iter(["/status", "done"])
        output = []
        session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: next(inputs),
            print_fn=output.append,
        )
        joined = "\n".join(output)
        assert "pushed" in joined
        assert "pending" in joined

    def test_slash_help(self, tmp_project):
        """Display help text (lines 882-907)."""
        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        inputs = iter(["/help", "done"])
        output = []
        session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
            input_fn=lambda p: next(inputs),
            print_fn=output.append,
        )
        joined = "\n".join(output)
        assert "/list" in joined
        assert "/push" in joined
        assert "/remove" in joined
        assert "/preview" in joined
        assert "/status" in joined
        assert "natural language" in joined.lower()

    # ----------------------------------------------------------
    # Lines 961-963: enrich with markdown fences
    # ----------------------------------------------------------

    def test_enrich_strips_markdown_fences(self, tmp_project):
        from azext_prototype.ai.provider import AIResponse

        item_json = json.dumps({"title": "Rate Limiting", "effort": "L"})
        fenced = f"```json\n{item_json}\n```"

        session, state, ai = self._make_session(tmp_project)
        ai.chat.return_value = AIResponse(
            content=fenced,
            model="t",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

        result = session._enrich_new_item("Rate limiting")
        assert result["title"] == "Rate Limiting"
        assert result["effort"] == "L"

    # ----------------------------------------------------------
    # Lines 987-988: _save_backlog_md with no items
    # ----------------------------------------------------------

    def test_save_backlog_md_no_items(self, tmp_project):
        session, state, _ = self._make_session(tmp_project)
        state.set_items([])

        output = []
        session._save_backlog_md(output.append)
        joined = "\n".join(output)
        assert "No items to save" in joined

    # ----------------------------------------------------------
    # Lines 1020-1021: save with dict tasks
    # ----------------------------------------------------------

    def test_save_backlog_md_dict_tasks(self, tmp_project):
        session, state, _ = self._make_session(tmp_project)
        state.set_items(
            [
                {
                    "epic": "Infra",
                    "title": "VNet",
                    "description": "Configure VNet",
                    "effort": "M",
                    "acceptance_criteria": ["AC1"],
                    "tasks": [
                        {"title": "Create VNet", "done": True},
                        {"title": "Create Subnets", "done": False},
                    ],
                }
            ]
        )

        output = []
        session._save_backlog_md(output.append)

        md_path = tmp_project / "concept" / "docs" / "BACKLOG.md"
        assert md_path.exists()
        content = md_path.read_text()
        assert "- [x] Create VNet" in content
        assert "- [ ] Create Subnets" in content

    # ----------------------------------------------------------
    # Lines 1055, 1067-1069: _get_production_items
    # ----------------------------------------------------------

    def test_get_production_items_no_services(self, tmp_project):
        """Returns empty string when no services (line 1055)."""
        from azext_prototype.stages.discovery_state import DiscoveryState

        ds = DiscoveryState(str(tmp_project))
        ds._state["architecture"] = {"services": []}
        ds.save()

        session, _, _ = self._make_session(tmp_project)
        result = session._get_production_items()
        assert result == ""

    def test_get_production_items_exception(self, tmp_project):
        """Returns empty string on exception (lines 1067-1069)."""
        session, _, _ = self._make_session(tmp_project)

        with patch(
            "azext_prototype.stages.discovery_state.DiscoveryState.load",
            side_effect=Exception("boom"),
        ):
            result = session._get_production_items()
        assert result == ""

    # ----------------------------------------------------------
    # Lines 1075-1076, 1078-1082: _maybe_spinner
    # ----------------------------------------------------------

    def test_maybe_spinner_with_status_fn(self, tmp_project):
        """_maybe_spinner with status_fn calls start/end (1078-1082)."""
        session, _, _ = self._make_session(tmp_project)

        calls = []

        def status_fn(msg, phase):
            calls.append((msg, phase))

        with session._maybe_spinner("Working...", False, status_fn=status_fn):
            pass

        assert ("Working...", "start") in calls
        assert ("Working...", "end") in calls

    def test_maybe_spinner_plain_noop(self, tmp_project):
        """_maybe_spinner with no styling and no status_fn is a no-op."""
        session, _, _ = self._make_session(tmp_project)

        with session._maybe_spinner("msg", False):
            pass

    # ----------------------------------------------------------
    # Line 324: slash command push breaks interactive loop
    # ----------------------------------------------------------

    def test_slash_command_push_breaks_loop(self, tmp_project):
        """When /push returns 'pushed', the loop breaks (line 324)."""
        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        with patch(f"{_SESSION_MODULE}.check_gh_auth", return_value=True), patch(
            f"{_SESSION_MODULE}.push_github_issue"
        ) as mock_push:
            mock_push.return_value = {"url": "https://github.com/o/p/issues/1"}

            output = []
            result = session.run(
                design_context="arch",
                provider="github",
                org="o",
                project="p",
                input_fn=lambda p: "/push",
                print_fn=output.append,
            )

        assert result.items_pushed == 1
        assert not result.cancelled

    # ----------------------------------------------------------
    # Line 672: push_all devops feature direct call
    # ----------------------------------------------------------

    def test_push_all_devops_feature_direct(self, tmp_project):
        """_push_all with devops calls push_devops_feature (line 672)."""
        session, state, _ = self._make_session(tmp_project)
        state.set_items([{"title": "F1"}])

        with patch(f"{_SESSION_MODULE}.check_devops_ext", return_value=True), patch(
            f"{_SESSION_MODULE}.push_devops_feature"
        ) as mock_feat:
            mock_feat.return_value = {
                "id": 1,
                "url": "https://dev.azure.com/o/p/1",
            }

            output = []
            result = session._push_all("devops", "o", "p", output.append, False)

        assert result.items_pushed == 1
        mock_feat.assert_called_once()

    # ----------------------------------------------------------
    # Line 283: styled prompt (test use_styled=True paths
    # via console mock)
    # ----------------------------------------------------------

    def test_use_styled_calls_prompt(self, tmp_project):
        """With use_styled=True, prompt is used (line 283)."""
        items_json = json.dumps([{"epic": "A", "title": "B", "effort": "S"}])
        session, state, _ = self._make_session(tmp_project, items_response=items_json)

        # Mock the prompt to return "done"
        session._prompt = MagicMock()
        session._prompt.prompt.return_value = "done"

        # Run without input_fn/print_fn (use_styled=True)
        # But we need to suppress real console output
        session._console = MagicMock()
        session._console.print = MagicMock()
        session._console.spinner = MagicMock()
        session._console.spinner.return_value.__enter__ = MagicMock()
        session._console.spinner.return_value.__exit__ = MagicMock(return_value=False)

        result = session.run(
            design_context="arch",
            provider="github",
            org="o",
            project="p",
        )
        session._prompt.prompt.assert_called()
        assert result.items_generated == 1
