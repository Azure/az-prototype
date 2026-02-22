"""Tests for backlog generation — BacklogState, BacklogSession, push helpers, scope injection.

Keeps the new backlog tests separate from test_custom.py to prevent file bloat.
"""

import json
import os

import pytest
import yaml
from unittest.mock import MagicMock, patch, call

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
        state.set_items([
            {"epic": "Infra", "title": "VNet Setup", "effort": "M", "tasks": ["T1"]},
            {"epic": "App", "title": "API Gateway", "effort": "L", "tasks": ["T2"]},
        ])

        summary = state.format_backlog_summary()
        assert "2 item(s)" in summary
        assert "VNet Setup" in summary
        assert "API Gateway" in summary
        assert "Infra" in summary
        assert "App" in summary

    def test_format_item_detail(self, tmp_project):
        from azext_prototype.stages.backlog_state import BacklogState

        state = BacklogState(str(tmp_project))
        state.set_items([{
            "epic": "Infra",
            "title": "VNet Setup",
            "description": "Configure virtual network",
            "acceptance_criteria": ["AC1: VNet created"],
            "tasks": ["Create VNet", "Create Subnets"],
            "effort": "M",
        }])

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
                "myorg", "myrepo",
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
                "myorg", "myrepo",
                {"title": "VNet"},
            )
            assert "error" in result
            assert "authentication" in result["error"]

    def test_push_devops_feature_success(self):
        from azext_prototype.stages.backlog_push import push_devops_feature

        devops_response = json.dumps({
            "id": 123,
            "_links": {"html": {"href": "https://dev.azure.com/org/proj/_workitems/edit/123"}},
        })
        with patch("azext_prototype.stages.backlog_push.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=devops_response,
            )

            result = push_devops_feature(
                "myorg", "myproj",
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


# ======================================================================
# BacklogSession Tests
# ======================================================================

class TestBacklogSession:
    """Test the interactive backlog session."""

    def _make_session(self, project_dir, mock_ai_provider, items_response="[]"):
        from azext_prototype.stages.backlog_session import BacklogSession
        from azext_prototype.stages.backlog_state import BacklogState
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.ai.provider import AIResponse

        mock_ai_provider.chat.return_value = AIResponse(
            content=items_response, model="test",
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
            ctx, registry, backlog_state=backlog_state,
        )
        return session, backlog_state

    def test_generate_from_ai(self, tmp_project, mock_ai_provider):
        items_json = json.dumps([
            {"epic": "Infra", "title": "VNet", "effort": "M", "tasks": ["T1"],
             "description": "d", "acceptance_criteria": ["AC1"]},
        ])
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
        items_json = json.dumps([
            {"epic": "Infra", "title": "VNet", "effort": "M"},
        ])
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
        items_json = json.dumps([
            {"epic": "Infra", "title": "VNet", "description": "Configure virtual network",
             "effort": "M", "acceptance_criteria": ["AC1"], "tasks": ["T1"]},
        ])
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
        items_json = json.dumps([
            {"epic": "Infra", "title": "VNet", "effort": "M",
             "description": "d", "acceptance_criteria": ["AC1"], "tasks": ["T1"]},
        ])
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
        from azext_prototype.stages.backlog_state import BacklogState
        from azext_prototype.ai.provider import AIResponse

        state = BacklogState(str(tmp_project))
        state.set_items([{"epic": "Old", "title": "Old Item", "effort": "S"}])
        state.set_context_hash("arch")

        new_items_json = json.dumps([
            {"epic": "New", "title": "New Item", "effort": "M"},
        ])

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
        items_json = json.dumps([
            {"epic": "A", "title": "Item1", "effort": "S"},
            {"epic": "A", "title": "Item2", "effort": "M"},
        ])
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
        from azext_prototype.custom import _generate_templates
        from pathlib import Path

        output_dir = Path(str(project_with_config)) / "test_docs"
        project_dir = str(project_with_config)
        project_config = {"project": {"name": "test"}}

        # console is imported locally inside _generate_templates
        with patch("azext_prototype.ui.console.console") as mock_console:
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
        from azext_prototype.custom import prototype_generate_backlog
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry

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
        from azext_prototype.custom import prototype_generate_backlog
        from azext_prototype.stages.backlog_state import BacklogState
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry

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
        from azext_prototype.custom import prototype_generate_backlog
        from azext_prototype.ai.provider import AIResponse

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
                    items_generated=1, items_pushed=0,
                )

                result = prototype_generate_backlog(
                    cmd, provider="github", org="o", project="p", json_output=True,
                )

        assert result["status"] == "generated"
        assert result["items_generated"] == 1

    @patch(f"{_CUSTOM_MODULE}._check_requirements")
    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_backlog_cancelled_returns_cancelled(self, mock_dir, mock_check_req, project_with_design, mock_ai_provider):
        from azext_prototype.custom import prototype_generate_backlog
        from azext_prototype.ai.provider import AIResponse

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
                    cmd, provider="github", org="o", project="p", json_output=True,
                )

        assert result["status"] == "cancelled"


# ======================================================================
# /add enrichment tests (Phase 9)
# ======================================================================

class TestAddEnrichment:
    """Test that /add uses PM agent to enrich items."""

    def _make_session(self, tmp_project, pm_response=None, pm_raises=False):
        from azext_prototype.stages.backlog_session import BacklogSession
        from azext_prototype.stages.backlog_state import BacklogState
        from azext_prototype.agents.base import AgentCapability, AgentContext
        from azext_prototype.ai.provider import AIResponse

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
                content=pm_response, model="gpt-4o",
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )
        else:
            ctx.ai_provider.chat.return_value = AIResponse(
                content=json.dumps({
                    "epic": "API",
                    "title": "Add rate limiting",
                    "description": "Implement API rate limiting for all endpoints",
                    "acceptance_criteria": ["AC1: Rate limit headers returned", "AC2: 429 status on exceed"],
                    "tasks": ["Add middleware", "Configure limits", "Add tests"],
                    "effort": "L",
                }),
                model="gpt-4o",
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )

        registry = MagicMock()
        from azext_prototype.agents.base import AgentCapability
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
        from azext_prototype.stages.backlog_session import BacklogSession
        from azext_prototype.stages.backlog_state import BacklogState
        from azext_prototype.agents.base import AgentContext

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
