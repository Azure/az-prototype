"""Tests for the TUI stage orchestrator.

Covers ``detect_stage()``, ``StageOrchestrator`` init/run/state-population,
and the per-stage runner methods (_run_design, _run_build, _run_deploy).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest
import yaml

from azext_prototype.ui.stage_orchestrator import StageOrchestrator, detect_stage
from azext_prototype.ui.task_model import TaskStatus
from azext_prototype.ui.tui_adapter import ShutdownRequested

# -------------------------------------------------------------------- #
# detect_stage()
# -------------------------------------------------------------------- #


class TestDetectStage:
    """Test stage detection from state files."""

    def test_no_state_files_returns_init(self, tmp_project):
        assert detect_stage(str(tmp_project)) == "init"

    def test_discovery_yaml_returns_design(self, tmp_project):
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "discovery.yaml").write_text("project:\n  summary: test\n")
        assert detect_stage(str(tmp_project)) == "design"

    def test_design_json_returns_design(self, tmp_project):
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "design.json").write_text(json.dumps({"architecture": "test"}))
        assert detect_stage(str(tmp_project)) == "design"

    def test_build_yaml_returns_build(self, tmp_project):
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "discovery.yaml").write_text("project:\n  summary: test\n")
        (state_dir / "build.yaml").write_text("iac_tool: terraform\n")
        assert detect_stage(str(tmp_project)) == "build"

    def test_deploy_yaml_returns_deploy(self, tmp_project):
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "discovery.yaml").write_text("project:\n  summary: test\n")
        (state_dir / "build.yaml").write_text("iac_tool: terraform\n")
        (state_dir / "deploy.yaml").write_text("iac_tool: terraform\n")
        assert detect_stage(str(tmp_project)) == "deploy"

    def test_deploy_without_lower_files(self, tmp_project):
        """deploy.yaml alone is enough to detect deploy stage."""
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "deploy.yaml").write_text("iac_tool: terraform\n")
        assert detect_stage(str(tmp_project)) == "deploy"

    def test_missing_state_dir_returns_init(self, tmp_path):
        """Non-existent .prototype/state/ dir should not raise."""
        project_dir = tmp_path / "empty-project"
        project_dir.mkdir()
        assert detect_stage(str(project_dir)) == "init"

    def test_deploy_takes_precedence_over_build(self, tmp_project):
        """All state files present -> deploy wins (highest priority)."""
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "discovery.yaml").write_text("")
        (state_dir / "build.yaml").write_text("")
        (state_dir / "deploy.yaml").write_text("")
        assert detect_stage(str(tmp_project)) == "deploy"

    def test_build_takes_precedence_over_design(self, tmp_project):
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "discovery.yaml").write_text("")
        (state_dir / "build.yaml").write_text("")
        assert detect_stage(str(tmp_project)) == "build"


# -------------------------------------------------------------------- #
# Helpers -- shared mock setup
# -------------------------------------------------------------------- #


def _make_adapter():
    """Return a MagicMock that satisfies the TUIAdapter interface."""
    adapter = MagicMock()
    adapter.input_fn = MagicMock(return_value="quit")
    adapter.print_fn = MagicMock()
    adapter.status_fn = MagicMock()
    adapter.print_token_status = MagicMock()
    adapter.update_task = MagicMock()
    adapter.add_task = MagicMock()
    adapter.clear_tasks = MagicMock()
    adapter.section_fn = MagicMock()
    adapter.response_fn = MagicMock()
    return adapter


def _make_app():
    """Return a MagicMock that satisfies the PrototypeApp interface."""
    app = MagicMock()
    app.call_from_thread = MagicMock(side_effect=lambda fn: fn())
    app.exit = MagicMock()
    return app


def _make_orchestrator(tmp_project, adapter=None, app=None, stage_kwargs=None):
    """Build a StageOrchestrator wired to mocks."""
    adapter = adapter or _make_adapter()
    app = app or _make_app()
    orch = StageOrchestrator(
        app=app,
        adapter=adapter,
        project_dir=str(tmp_project),
        stage_kwargs=stage_kwargs,
    )
    return orch, adapter, app


# -------------------------------------------------------------------- #
# StageOrchestrator.__init__
# -------------------------------------------------------------------- #


class TestStageOrchestratorInit:
    def test_stores_adapter_and_project_dir(self, tmp_project):
        adapter = _make_adapter()
        app = _make_app()
        orch = StageOrchestrator(app=app, adapter=adapter, project_dir=str(tmp_project))
        assert orch._adapter is adapter
        assert orch._project_dir == str(tmp_project)
        assert orch._app is app

    def test_stage_kwargs_default_empty(self, tmp_project):
        orch = StageOrchestrator(app=_make_app(), adapter=_make_adapter(), project_dir=str(tmp_project))
        assert orch._stage_kwargs == {}

    def test_stage_kwargs_stored(self, tmp_project):
        kw = {"iac_tool": "terraform"}
        orch = StageOrchestrator(
            app=_make_app(),
            adapter=_make_adapter(),
            project_dir=str(tmp_project),
            stage_kwargs=kw,
        )
        assert orch._stage_kwargs == kw

    def test_none_stage_kwargs_becomes_empty_dict(self, tmp_project):
        orch = StageOrchestrator(
            app=_make_app(),
            adapter=_make_adapter(),
            project_dir=str(tmp_project),
            stage_kwargs=None,
        )
        assert orch._stage_kwargs == {}


# -------------------------------------------------------------------- #
# StageOrchestrator.run -- stage detection and guard logic
# -------------------------------------------------------------------- #


class TestStageOrchestratorRun:
    """Test the run() method's stage detection and guard behavior."""

    def test_run_with_detected_stage_init(self, tmp_project):
        """No state files -> detects init, runs command loop."""
        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.return_value = "quit"

        orch.run()

        # init should always be marked COMPLETED
        adapter.update_task.assert_any_call("init", TaskStatus.COMPLETED)

    def test_run_with_explicit_start_stage(self, tmp_project):
        """Explicit start_stage overrides detected stage."""
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "discovery.yaml").write_text("project:\n  summary: test\n")

        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.return_value = "quit"

        orch.run(start_stage="design")

        adapter.update_task.assert_any_call("init", TaskStatus.COMPLETED)

    def test_run_guard_prevents_skipping(self, tmp_project):
        """Targeting build from init state should be blocked (design skipped)."""
        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.return_value = "quit"

        orch.run(start_stage="build")

        call_args_list = [str(c) for c in adapter.print_fn.call_args_list]
        warning_printed = any("Cannot skip" in s for s in call_args_list)
        assert warning_printed, "Should warn about skipping stages"

    def test_run_guard_falls_back_to_next_allowed(self, tmp_project):
        """When guard fires, should fall back to next allowed stage."""
        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.return_value = "quit"

        orch.run(start_stage="deploy")

        # From init, next allowed is design (index 1)
        call_args_list = [str(c) for c in adapter.print_fn.call_args_list]
        resumed = any("design" in s and "Resuming" in s for s in call_args_list)
        assert resumed, "Should mention resuming at design"

    def test_run_guard_skip_from_design_to_deploy(self, tmp_project):
        """From design, targeting deploy should skip-warn and fall back to build."""
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "discovery.yaml").write_text("project:\n  summary: test\n")

        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.return_value = "quit"

        orch.run(start_stage="deploy")

        call_args_list = [str(c) for c in adapter.print_fn.call_args_list]
        assert any("Cannot skip" in s for s in call_args_list)
        assert any("build" in s and "Resuming" in s for s in call_args_list)

    def test_run_can_target_next_stage(self, tmp_project):
        """From init (detected), targeting design (next) should be allowed."""
        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.return_value = "quit"

        orch.run(start_stage="design")

        call_args_list = [str(c) for c in adapter.print_fn.call_args_list]
        assert not any("Cannot skip" in s for s in call_args_list)

    def test_run_rerun_earlier_stage_marks_downstream_pending(self, tmp_project):
        """Re-running design when build is detected should mark build+deploy as PENDING."""
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "discovery.yaml").write_text("project:\n  summary: test\n")
        (state_dir / "build.yaml").write_text("iac_tool: terraform\n")

        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.return_value = "quit"

        orch.run(start_stage="design")

        adapter.update_task.assert_any_call("build", TaskStatus.PENDING)
        adapter.update_task.assert_any_call("deploy", TaskStatus.PENDING)

    def test_run_rerun_earlier_stage_allowed(self, tmp_project):
        """Re-running design when build is detected should NOT show skip warning."""
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "discovery.yaml").write_text("project:\n  summary: test\n")
        (state_dir / "build.yaml").write_text("iac_tool: terraform\n")

        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.return_value = "quit"

        orch.run(start_stage="design")

        call_args_list = [str(c) for c in adapter.print_fn.call_args_list]
        assert not any("Cannot skip" in s for s in call_args_list)

    def test_run_shutdown_requested_is_caught(self, tmp_project):
        """ShutdownRequested raised during command_loop should be caught."""
        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.side_effect = ShutdownRequested()

        # Should not raise
        orch.run()

    def test_run_auto_runs_design_when_stage_kwargs(self, tmp_project):
        """With stage_kwargs and start_stage=design, should auto-run design."""
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "discovery.yaml").write_text("project:\n  summary: test\n")

        orch, adapter, app = _make_orchestrator(tmp_project, stage_kwargs={"iac_tool": "terraform"})
        adapter.input_fn.return_value = "quit"

        with patch.object(orch, "_run_design") as mock_design:
            orch.run(start_stage="design")
            mock_design.assert_called_once_with(iac_tool="terraform")

    def test_run_auto_runs_build_when_stage_kwargs(self, tmp_project):
        """With stage_kwargs and start_stage=build, should auto-run build."""
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "discovery.yaml").write_text("project:\n  summary: test\n")
        (state_dir / "build.yaml").write_text("iac_tool: terraform\n")

        orch, adapter, app = _make_orchestrator(tmp_project, stage_kwargs={"iac_tool": "terraform"})
        adapter.input_fn.return_value = "quit"

        with patch.object(orch, "_run_build") as mock_build:
            orch.run(start_stage="build")
            mock_build.assert_called_once_with(iac_tool="terraform")

    def test_run_auto_runs_deploy_when_stage_kwargs(self, tmp_project):
        """With stage_kwargs and start_stage=deploy, should auto-run deploy."""
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "discovery.yaml").write_text("project:\n  summary: test\n")
        (state_dir / "build.yaml").write_text("iac_tool: terraform\n")
        (state_dir / "deploy.yaml").write_text("iac_tool: terraform\n")

        orch, adapter, app = _make_orchestrator(tmp_project, stage_kwargs={"subscription": "sub-123"})
        adapter.input_fn.return_value = "quit"

        with patch.object(orch, "_run_deploy") as mock_deploy:
            orch.run(start_stage="deploy")
            mock_deploy.assert_called_once_with(subscription="sub-123")

    def test_run_no_auto_run_without_start_stage(self, tmp_project):
        """stage_kwargs alone (no start_stage) should NOT auto-run."""
        orch, adapter, app = _make_orchestrator(tmp_project, stage_kwargs={"iac_tool": "terraform"})
        adapter.input_fn.return_value = "quit"

        with patch.object(orch, "_run_design") as mock_d, patch.object(orch, "_run_build") as mock_b, patch.object(
            orch, "_run_deploy"
        ) as mock_dep:
            orch.run()
            mock_d.assert_not_called()
            mock_b.assert_not_called()
            mock_dep.assert_not_called()

    def test_run_marks_target_in_progress_when_not_detected(self, tmp_project):
        """When start_stage differs from detected, target gets IN_PROGRESS."""
        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.return_value = "quit"

        orch.run(start_stage="design")

        adapter.update_task.assert_any_call("design", TaskStatus.IN_PROGRESS)

    def test_run_does_not_mark_target_in_progress_when_same_as_detected(self, tmp_project):
        """When start_stage == detected, should not get extra IN_PROGRESS call."""
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "discovery.yaml").write_text("project:\n  summary: test\n")

        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.return_value = "quit"

        orch.run(start_stage="design")

        # detected == "design", start_stage == "design" -> current == detected
        # The IN_PROGRESS call from line 108 should NOT happen
        in_progress_calls = [
            c for c in adapter.update_task.call_args_list if c == call("design", TaskStatus.IN_PROGRESS)
        ]
        # It should only get COMPLETED from _populate_from_state, not IN_PROGRESS
        assert len(in_progress_calls) == 0

    def test_guard_uses_singular_has_for_one_skipped(self, tmp_project):
        """When one stage is skipped, message should use 'has' not 'have'."""
        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.return_value = "quit"

        orch.run(start_stage="build")  # skips design

        call_args_list = [str(c) for c in adapter.print_fn.call_args_list]
        assert any("has" in s and "not been completed" in s for s in call_args_list)

    def test_guard_uses_plural_have_for_multiple_skipped(self, tmp_project):
        """When multiple stages skipped, message should use 'have'."""
        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.return_value = "quit"

        orch.run(start_stage="deploy")  # skips design + build

        call_args_list = [str(c) for c in adapter.print_fn.call_args_list]
        assert any("have" in s and "not been completed" in s for s in call_args_list)


# -------------------------------------------------------------------- #
# _populate_from_state
# -------------------------------------------------------------------- #


class TestPopulateFromState:
    def test_marks_stages_up_to_current(self, tmp_project):
        orch, adapter, _ = _make_orchestrator(tmp_project)

        orch._populate_from_state("build")

        adapter.update_task.assert_any_call("design", TaskStatus.COMPLETED)
        adapter.update_task.assert_any_call("build", TaskStatus.COMPLETED)

    def test_init_stage_skipped_in_loop(self, tmp_project):
        """Init is never updated in the loop (already marked externally)."""
        orch, adapter, _ = _make_orchestrator(tmp_project)

        orch._populate_from_state("design")

        update_calls = adapter.update_task.call_args_list
        init_calls = [c for c in update_calls if c[0][0] == "init"]
        assert len(init_calls) == 0

    def test_deploy_marks_all_stages(self, tmp_project):
        orch, adapter, _ = _make_orchestrator(tmp_project)

        orch._populate_from_state("deploy")

        adapter.update_task.assert_any_call("design", TaskStatus.COMPLETED)
        adapter.update_task.assert_any_call("build", TaskStatus.COMPLETED)
        adapter.update_task.assert_any_call("deploy", TaskStatus.COMPLETED)

    def test_init_marks_nothing(self, tmp_project):
        """When current_stage is init, no stages should be marked COMPLETED."""
        orch, adapter, _ = _make_orchestrator(tmp_project)

        orch._populate_from_state("init")

        completed_calls = [c for c in adapter.update_task.call_args_list if c[0][1] == TaskStatus.COMPLETED]
        assert len(completed_calls) == 0


# -------------------------------------------------------------------- #
# _populate_design_subtasks
# -------------------------------------------------------------------- #


class TestPopulateDesignSubtasks:
    def test_no_discovery_state(self, tmp_project):
        """No discovery.yaml -> no subtasks added."""
        orch, adapter, _ = _make_orchestrator(tmp_project)

        orch._populate_design_subtasks()

        adapter.add_task.assert_not_called()

    def test_with_confirmed_items(self, tmp_project):
        state_dir = tmp_project / ".prototype" / "state"
        state = {
            "items": [
                {"heading": "Use CosmosDB", "status": "confirmed"},
                {"heading": "Use AKS", "status": "confirmed"},
                {"heading": "Auth method", "status": "pending"},
            ]
        }
        (state_dir / "discovery.yaml").write_text(yaml.dump(state))

        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_design_subtasks()

        adapter.add_task.assert_any_call("design", "design-confirmed", "Confirmed requirements (2)")
        adapter.update_task.assert_any_call("design-confirmed", TaskStatus.COMPLETED)

    def test_with_open_items(self, tmp_project):
        state_dir = tmp_project / ".prototype" / "state"
        state = {
            "items": [
                {"heading": "Auth method", "status": "pending"},
            ]
        }
        (state_dir / "discovery.yaml").write_text(yaml.dump(state))

        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_design_subtasks()

        adapter.add_task.assert_any_call("design", "design-open", "Open items (1)")
        adapter.update_task.assert_any_call("design-open", TaskStatus.PENDING)

    def test_with_architecture_output(self, tmp_project):
        """design.json present -> architecture subtask added."""
        state_dir = tmp_project / ".prototype" / "state"
        state = {"items": [{"heading": "Use AKS", "status": "confirmed"}]}
        (state_dir / "discovery.yaml").write_text(yaml.dump(state))
        (state_dir / "design.json").write_text(json.dumps({"architecture": "test"}))

        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_design_subtasks()

        adapter.add_task.assert_any_call("design", "design-arch", "Architecture document")
        adapter.update_task.assert_any_call("design-arch", TaskStatus.COMPLETED)

    def test_no_confirmed_no_open_no_subtasks(self, tmp_project):
        """Discovery exists but has zero items -> no confirmed/open subtasks."""
        state_dir = tmp_project / ".prototype" / "state"
        state = {"items": []}
        (state_dir / "discovery.yaml").write_text(yaml.dump(state))

        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_design_subtasks()

        # No confirmed or open subtasks should be added
        add_calls = [str(c) for c in adapter.add_task.call_args_list]
        assert not any("design-confirmed" in s for s in add_calls)
        assert not any("design-open" in s for s in add_calls)

    def test_answered_items_count_as_confirmed(self, tmp_project):
        """Items with status 'answered' should count toward confirmed total."""
        state_dir = tmp_project / ".prototype" / "state"
        state = {
            "items": [
                {"heading": "DB choice", "status": "answered"},
            ]
        }
        (state_dir / "discovery.yaml").write_text(yaml.dump(state))

        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_design_subtasks()

        adapter.add_task.assert_any_call("design", "design-confirmed", "Confirmed requirements (1)")

    def test_exception_does_not_propagate(self, tmp_project):
        """Errors loading state should be caught (not propagate)."""
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "discovery.yaml").write_text("invalid: yaml: : :\n  bad")

        orch, adapter, _ = _make_orchestrator(tmp_project)
        # Should not raise
        orch._populate_design_subtasks()


# -------------------------------------------------------------------- #
# _populate_build_subtasks
# -------------------------------------------------------------------- #


class TestPopulateBuildSubtasks:
    def test_no_build_state(self, tmp_project):
        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_build_subtasks()
        adapter.add_task.assert_not_called()

    def test_with_build_stages(self, tmp_project):
        state_dir = tmp_project / ".prototype" / "state"
        build_state = {
            "deployment_stages": [
                {"stage": 1, "name": "Foundation", "status": "generated"},
                {"stage": 2, "name": "Application", "status": "in_progress"},
                {"stage": 3, "name": "Database", "status": "pending"},
            ]
        }
        (state_dir / "build.yaml").write_text(yaml.dump(build_state))

        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_build_subtasks()

        adapter.add_task.assert_any_call("build", "build-stage-1", "Stage 1: Foundation")
        adapter.add_task.assert_any_call("build", "build-stage-2", "Stage 2: Application")
        adapter.add_task.assert_any_call("build", "build-stage-3", "Stage 3: Database")

        adapter.update_task.assert_any_call("build-stage-1", TaskStatus.COMPLETED)
        adapter.update_task.assert_any_call("build-stage-2", TaskStatus.IN_PROGRESS)

    def test_accepted_status_is_completed(self, tmp_project):
        state_dir = tmp_project / ".prototype" / "state"
        build_state = {
            "deployment_stages": [
                {"stage": 1, "name": "Foundation", "status": "accepted"},
            ]
        }
        (state_dir / "build.yaml").write_text(yaml.dump(build_state))

        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_build_subtasks()

        adapter.update_task.assert_any_call("build-stage-1", TaskStatus.COMPLETED)

    def test_missing_name_uses_fallback(self, tmp_project):
        """Stage with no 'name' key should use 'Stage N' fallback."""
        state_dir = tmp_project / ".prototype" / "state"
        build_state = {
            "deployment_stages": [
                {"stage": 5, "status": "pending"},
            ]
        }
        (state_dir / "build.yaml").write_text(yaml.dump(build_state))

        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_build_subtasks()

        adapter.add_task.assert_any_call("build", "build-stage-5", "Stage 5: Stage 5")

    def test_exception_does_not_propagate(self, tmp_project):
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "build.yaml").write_text("invalid: yaml: : :\n  bad")

        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_build_subtasks()


# -------------------------------------------------------------------- #
# _populate_deploy_subtasks
# -------------------------------------------------------------------- #


class TestPopulateDeploySubtasks:
    def test_no_deploy_state(self, tmp_project):
        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_deploy_subtasks()
        adapter.add_task.assert_not_called()

    def test_with_deploy_stages(self, tmp_project):
        state_dir = tmp_project / ".prototype" / "state"
        deploy_state = {
            "deployment_stages": [
                {"stage": 1, "name": "Foundation", "deploy_status": "deployed"},
                {"stage": 2, "name": "Application", "deploy_status": "deploying"},
                {"stage": 3, "name": "Database", "deploy_status": "failed"},
                {"stage": 4, "name": "Monitoring", "deploy_status": "pending"},
            ]
        }
        (state_dir / "deploy.yaml").write_text(yaml.dump(deploy_state))

        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_deploy_subtasks()

        adapter.add_task.assert_any_call("deploy", "deploy-stage-1", "Stage 1: Foundation")
        adapter.add_task.assert_any_call("deploy", "deploy-stage-2", "Stage 2: Application")
        adapter.add_task.assert_any_call("deploy", "deploy-stage-3", "Stage 3: Database")
        adapter.add_task.assert_any_call("deploy", "deploy-stage-4", "Stage 4: Monitoring")

        adapter.update_task.assert_any_call("deploy-stage-1", TaskStatus.COMPLETED)
        adapter.update_task.assert_any_call("deploy-stage-2", TaskStatus.IN_PROGRESS)
        adapter.update_task.assert_any_call("deploy-stage-3", TaskStatus.FAILED)

    def test_in_progress_status(self, tmp_project):
        state_dir = tmp_project / ".prototype" / "state"
        deploy_state = {
            "deployment_stages": [
                {"stage": 1, "name": "Stage", "deploy_status": "in_progress"},
            ]
        }
        (state_dir / "deploy.yaml").write_text(yaml.dump(deploy_state))

        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_deploy_subtasks()
        adapter.update_task.assert_any_call("deploy-stage-1", TaskStatus.IN_PROGRESS)

    def test_remediating_status(self, tmp_project):
        state_dir = tmp_project / ".prototype" / "state"
        deploy_state = {
            "deployment_stages": [
                {"stage": 1, "name": "Stage", "deploy_status": "remediating"},
            ]
        }
        (state_dir / "deploy.yaml").write_text(yaml.dump(deploy_state))

        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_deploy_subtasks()
        adapter.update_task.assert_any_call("deploy-stage-1", TaskStatus.IN_PROGRESS)

    def test_rolled_back_status(self, tmp_project):
        state_dir = tmp_project / ".prototype" / "state"
        deploy_state = {
            "deployment_stages": [
                {"stage": 1, "name": "Stage", "deploy_status": "rolled_back"},
            ]
        }
        (state_dir / "deploy.yaml").write_text(yaml.dump(deploy_state))

        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_deploy_subtasks()
        adapter.update_task.assert_any_call("deploy-stage-1", TaskStatus.FAILED)

    def test_missing_name_uses_fallback(self, tmp_project):
        state_dir = tmp_project / ".prototype" / "state"
        deploy_state = {
            "deployment_stages": [
                {"stage": 3, "deploy_status": "deployed"},
            ]
        }
        (state_dir / "deploy.yaml").write_text(yaml.dump(deploy_state))

        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_deploy_subtasks()
        adapter.add_task.assert_any_call("deploy", "deploy-stage-3", "Stage 3: Stage 3")

    def test_exception_does_not_propagate(self, tmp_project):
        state_dir = tmp_project / ".prototype" / "state"
        (state_dir / "deploy.yaml").write_text("invalid: yaml: : :\n  bad")

        orch, adapter, _ = _make_orchestrator(tmp_project)
        orch._populate_deploy_subtasks()


# -------------------------------------------------------------------- #
# _show_welcome
# -------------------------------------------------------------------- #


class TestShowWelcome:
    def test_displays_stage_name(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        orch._show_welcome("design")

        call_args = [str(c) for c in adapter.print_fn.call_args_list]
        assert any("design" in s for s in call_args)

    def test_displays_project_name(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        orch._show_welcome("init")

        call_args = [str(c) for c in adapter.print_fn.call_args_list]
        assert any("test-project" in s for s in call_args)

    def test_displays_location(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        orch._show_welcome("init")

        call_args = [str(c) for c in adapter.print_fn.call_args_list]
        assert any("eastus" in s for s in call_args)

    def test_displays_ai_provider(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        orch._show_welcome("init")

        call_args = [str(c) for c in adapter.print_fn.call_args_list]
        assert any("github-models" in s for s in call_args)

    def test_exception_fallback(self, tmp_project):
        """When config cannot be loaded, should still print stage."""
        orch, adapter, _ = _make_orchestrator(tmp_project)

        orch._show_welcome("build")

        call_args = [str(c) for c in adapter.print_fn.call_args_list]
        assert any("build" in s for s in call_args)

    def test_summary_from_discovery_state(self, project_with_discovery):
        orch, adapter, _ = _make_orchestrator(project_with_discovery)

        orch._show_welcome("design")

        call_args = [str(c) for c in adapter.print_fn.call_args_list]
        assert any("API with Cosmos DB backend" in s for s in call_args)

    def test_summary_from_design_json(self, project_with_config):
        state_dir = project_with_config / ".prototype" / "state"
        (state_dir / "design.json").write_text(
            json.dumps({"architecture": "A serverless API using Azure Functions. It includes..."})
        )

        orch, adapter, _ = _make_orchestrator(project_with_config)
        orch._show_welcome("design")

        call_args = [str(c) for c in adapter.print_fn.call_args_list]
        assert any("serverless API" in s for s in call_args)

    def test_no_summary_prints_empty(self, project_with_config):
        """With config but no discovery/design state, summary should be absent."""
        orch, adapter, _ = _make_orchestrator(project_with_config)
        orch._show_welcome("init")

        call_args = [str(c) for c in adapter.print_fn.call_args_list]
        # Should have project name but no "Summary" line
        assert any("test-project" in s for s in call_args)


# -------------------------------------------------------------------- #
# _get_project_summary
# -------------------------------------------------------------------- #


class TestGetProjectSummary:
    def test_empty_when_no_state(self, tmp_project):
        orch, _, _ = _make_orchestrator(tmp_project)
        assert orch._get_project_summary() == ""

    def test_from_discovery(self, project_with_discovery):
        orch, _, _ = _make_orchestrator(project_with_discovery)
        result = orch._get_project_summary()
        assert "API with Cosmos DB backend" in result

    def test_from_design_json(self, project_with_config):
        state_dir = project_with_config / ".prototype" / "state"
        (state_dir / "design.json").write_text(json.dumps({"architecture": "Build a web portal. It uses React."}))
        orch, _, _ = _make_orchestrator(project_with_config)
        result = orch._get_project_summary()
        assert result == "Build a web portal."

    def test_normalizes_whitespace(self, project_with_discovery):
        state_dir = project_with_discovery / ".prototype" / "state"
        state = {"project": {"summary": "API  with   extra    spaces"}}
        (state_dir / "discovery.yaml").write_text(yaml.dump(state))

        orch, _, _ = _make_orchestrator(project_with_discovery)
        result = orch._get_project_summary()
        assert "  " not in result

    def test_empty_architecture_string(self, project_with_config):
        """design.json with empty architecture should return empty string."""
        state_dir = project_with_config / ".prototype" / "state"
        (state_dir / "design.json").write_text(json.dumps({"architecture": ""}))

        orch, _, _ = _make_orchestrator(project_with_config)
        assert orch._get_project_summary() == ""

    def test_discovery_preferred_over_design_json(self, project_with_config):
        """When both discovery.yaml and design.json exist, discovery wins."""
        state_dir = project_with_config / ".prototype" / "state"
        discovery = {"project": {"summary": "From discovery"}}
        (state_dir / "discovery.yaml").write_text(yaml.dump(discovery))
        (state_dir / "design.json").write_text(json.dumps({"architecture": "From design."}))

        orch, _, _ = _make_orchestrator(project_with_config)
        result = orch._get_project_summary()
        assert "From discovery" in result


# -------------------------------------------------------------------- #
# _command_loop
# -------------------------------------------------------------------- #


class TestCommandLoop:
    def test_quit_exits(self, tmp_project):
        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.return_value = "quit"

        orch._command_loop("init")

        app.call_from_thread.assert_called()

    def test_q_exits(self, tmp_project):
        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.return_value = "q"

        orch._command_loop("init")

    def test_exit_command(self, tmp_project):
        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.return_value = "exit"

        orch._command_loop("init")

    def test_end_command(self, tmp_project):
        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.return_value = "end"

        orch._command_loop("init")

    def test_help_prints_commands(self, tmp_project):
        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.side_effect = ["help", "quit"]

        orch._command_loop("init")

        call_args = [str(c) for c in adapter.print_fn.call_args_list]
        assert any("Commands:" in s for s in call_args)

    def test_unknown_command(self, tmp_project):
        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.side_effect = ["foobar", "quit"]

        orch._command_loop("init")

        call_args = [str(c) for c in adapter.print_fn.call_args_list]
        assert any("Unknown command" in s for s in call_args)

    def test_empty_input_continues(self, tmp_project):
        orch, adapter, app = _make_orchestrator(tmp_project)
        adapter.input_fn.side_effect = ["", "  ", "quit"]

        orch._command_loop("init")

    def test_design_command_calls_run_design(self, tmp_project):
        orch, adapter, _ = _make_orchestrator(tmp_project)
        adapter.input_fn.side_effect = ["design", "quit"]

        with patch.object(orch, "_run_design") as mock:
            orch._command_loop("init")
            mock.assert_called_once()

    def test_redesign_command_calls_run_design(self, tmp_project):
        orch, adapter, _ = _make_orchestrator(tmp_project)
        adapter.input_fn.side_effect = ["redesign", "quit"]

        with patch.object(orch, "_run_design") as mock:
            orch._command_loop("init")
            mock.assert_called_once()

    def test_build_command_calls_run_build(self, tmp_project):
        orch, adapter, _ = _make_orchestrator(tmp_project)
        adapter.input_fn.side_effect = ["build", "quit"]

        with patch.object(orch, "_run_build") as mock:
            orch._command_loop("init")
            mock.assert_called_once()

    def test_continue_command_calls_run_build(self, tmp_project):
        orch, adapter, _ = _make_orchestrator(tmp_project)
        adapter.input_fn.side_effect = ["continue", "quit"]

        with patch.object(orch, "_run_build") as mock:
            orch._command_loop("init")
            mock.assert_called_once()

    def test_deploy_command_calls_run_deploy(self, tmp_project):
        orch, adapter, _ = _make_orchestrator(tmp_project)
        adapter.input_fn.side_effect = ["deploy", "quit"]

        with patch.object(orch, "_run_deploy") as mock:
            orch._command_loop("init")
            mock.assert_called_once()

    def test_redeploy_command_calls_run_deploy(self, tmp_project):
        orch, adapter, _ = _make_orchestrator(tmp_project)
        adapter.input_fn.side_effect = ["redeploy", "quit"]

        with patch.object(orch, "_run_deploy") as mock:
            orch._command_loop("init")
            mock.assert_called_once()

    def test_case_insensitive(self, tmp_project):
        orch, adapter, _ = _make_orchestrator(tmp_project)
        adapter.input_fn.side_effect = ["DESIGN", "quit"]

        with patch.object(orch, "_run_design") as mock:
            orch._command_loop("init")
            mock.assert_called_once()


# -------------------------------------------------------------------- #
# _run_design
# -------------------------------------------------------------------- #


class TestRunDesign:
    def test_calls_stage_execute(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.return_value = {"status": "success"}

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.design_stage.DesignStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            orch._run_design()

        mock_stage.execute.assert_called_once()
        call_kwargs = mock_stage.execute.call_args[1]
        assert call_kwargs["input_fn"] is adapter.input_fn
        assert call_kwargs["print_fn"] is adapter.print_fn
        assert call_kwargs["status_fn"] is adapter.status_fn
        assert call_kwargs["section_fn"] is adapter.section_fn
        assert call_kwargs["response_fn"] is adapter.response_fn

    def test_marks_design_in_progress_and_completed(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.return_value = {"status": "success"}

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.design_stage.DesignStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            orch._run_design()

        adapter.update_task.assert_any_call("design", TaskStatus.IN_PROGRESS)
        adapter.update_task.assert_any_call("design", TaskStatus.COMPLETED)

    def test_cancelled_result_raises_shutdown(self, project_with_config):
        orch, adapter, app = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.return_value = {"status": "cancelled"}

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.design_stage.DesignStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            with pytest.raises(ShutdownRequested):
                orch._run_design()

    def test_exception_marks_failed(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        with patch.object(orch, "_prepare") as mock_prep:
            mock_prep.side_effect = RuntimeError("config load failed")

            orch._run_design()

        adapter.update_task.assert_any_call("design", TaskStatus.FAILED)

    def test_exception_prints_error(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        with patch.object(orch, "_prepare") as mock_prep:
            mock_prep.side_effect = RuntimeError("config load failed")

            orch._run_design()

        call_args = [str(c) for c in adapter.print_fn.call_args_list]
        assert any("Design stage failed" in s for s in call_args)

    def test_adds_discovery_subtask(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.return_value = {"status": "success"}

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.design_stage.DesignStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            orch._run_design()

        adapter.add_task.assert_any_call("design", "design-discovery", "Discovery")
        adapter.update_task.assert_any_call("design-discovery", TaskStatus.IN_PROGRESS)

    def test_clears_design_tasks_first(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.return_value = {"status": "success"}

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.design_stage.DesignStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            orch._run_design()

        adapter.clear_tasks.assert_called_with("design")

    def test_passes_kwargs(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.return_value = {"status": "success"}

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.design_stage.DesignStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            orch._run_design(iac_tool="terraform")

        call_kwargs = mock_stage.execute.call_args[1]
        assert call_kwargs["iac_tool"] == "terraform"

    def test_shutdown_requested_propagates(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.side_effect = ShutdownRequested()

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.design_stage.DesignStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            with pytest.raises(ShutdownRequested):
                orch._run_design()


# -------------------------------------------------------------------- #
# _run_build
# -------------------------------------------------------------------- #


class TestRunBuild:
    def test_calls_stage_execute(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.return_value = {"status": "success"}

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.build_stage.BuildStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            orch._run_build()

        mock_stage.execute.assert_called_once()

    def test_marks_build_in_progress_and_completed(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.return_value = {"status": "success"}

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.build_stage.BuildStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            orch._run_build()

        adapter.update_task.assert_any_call("build", TaskStatus.IN_PROGRESS)
        adapter.update_task.assert_any_call("build", TaskStatus.COMPLETED)

    def test_exception_marks_failed(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        with patch.object(orch, "_prepare") as mock_prep:
            mock_prep.side_effect = RuntimeError("config load failed")

            orch._run_build()

        adapter.update_task.assert_any_call("build", TaskStatus.FAILED)

    def test_exception_prints_error(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        with patch.object(orch, "_prepare") as mock_prep:
            mock_prep.side_effect = RuntimeError("config load failed")

            orch._run_build()

        call_args = [str(c) for c in adapter.print_fn.call_args_list]
        assert any("Build stage failed" in s for s in call_args)

    def test_clears_build_tasks_first(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.return_value = {"status": "success"}

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.build_stage.BuildStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            orch._run_build()

        adapter.clear_tasks.assert_called_with("build")

    def test_build_section_fn_adds_tasks(self, project_with_config):
        """The _build_section_fn closure should add build stage entries."""
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()

        def capture_section_fn(*args, **kwargs):
            section_fn = kwargs.get("section_fn")
            if section_fn:
                section_fn([("Stage 1: Foundation", 2), ("Stage 2: App", 2)])
            return {"status": "success"}

        mock_stage.execute.side_effect = capture_section_fn

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.build_stage.BuildStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            orch._run_build()

        adapter.add_task.assert_any_call("build", "build-stage-1", "Stage 1: Foundation")
        adapter.add_task.assert_any_call("build", "build-stage-2", "Stage 2: App")

    def test_build_update_fn_maps_status(self, project_with_config):
        """The _build_update_fn closure should map status strings to TaskStatus."""
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()

        def capture_update_fn(*args, **kwargs):
            update_fn = kwargs.get("update_task_fn")
            if update_fn:
                update_fn("build-stage-1", "in_progress")
                update_fn("build-stage-1", "completed")
                update_fn("build-stage-2", "unknown_status")
            return {"status": "success"}

        mock_stage.execute.side_effect = capture_update_fn

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.build_stage.BuildStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            orch._run_build()

        adapter.update_task.assert_any_call("build-stage-1", TaskStatus.IN_PROGRESS)
        adapter.update_task.assert_any_call("build-stage-1", TaskStatus.COMPLETED)
        adapter.update_task.assert_any_call("build-stage-2", TaskStatus.PENDING)

    def test_passes_kwargs(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.return_value = {"status": "success"}

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.build_stage.BuildStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            orch._run_build(iac_tool="bicep")

        call_kwargs = mock_stage.execute.call_args[1]
        assert call_kwargs["iac_tool"] == "bicep"

    def test_shutdown_requested_propagates(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.side_effect = ShutdownRequested()

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.build_stage.BuildStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            with pytest.raises(ShutdownRequested):
                orch._run_build()


# -------------------------------------------------------------------- #
# _run_deploy
# -------------------------------------------------------------------- #


class TestRunDeploy:
    def test_calls_stage_execute(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.return_value = {"status": "success"}

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.deploy_stage.DeployStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            orch._run_deploy()

        mock_stage.execute.assert_called_once()

    def test_marks_deploy_in_progress_and_completed(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.return_value = {"status": "success"}

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.deploy_stage.DeployStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            orch._run_deploy()

        adapter.update_task.assert_any_call("deploy", TaskStatus.IN_PROGRESS)
        adapter.update_task.assert_any_call("deploy", TaskStatus.COMPLETED)

    def test_exception_marks_failed(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        with patch.object(orch, "_prepare") as mock_prep:
            mock_prep.side_effect = RuntimeError("config load failed")

            orch._run_deploy()

        adapter.update_task.assert_any_call("deploy", TaskStatus.FAILED)

    def test_exception_prints_error(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        with patch.object(orch, "_prepare") as mock_prep:
            mock_prep.side_effect = RuntimeError("deploy exploded")

            orch._run_deploy()

        call_args = [str(c) for c in adapter.print_fn.call_args_list]
        assert any("Deploy stage failed" in s for s in call_args)

    def test_clears_deploy_tasks_first(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.return_value = {"status": "success"}

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.deploy_stage.DeployStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            orch._run_deploy()

        adapter.clear_tasks.assert_called_with("deploy")

    def test_passes_kwargs(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.return_value = {"status": "success"}

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.deploy_stage.DeployStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            orch._run_deploy(subscription="sub-123")

        call_kwargs = mock_stage.execute.call_args[1]
        assert call_kwargs["subscription"] == "sub-123"

    def test_shutdown_requested_propagates(self, project_with_config):
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.side_effect = ShutdownRequested()

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.deploy_stage.DeployStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            with pytest.raises(ShutdownRequested):
                orch._run_deploy()

    def test_passes_adapter_callables(self, project_with_config):
        """Deploy should pass input_fn, print_fn, status_fn to execute."""
        orch, adapter, _ = _make_orchestrator(project_with_config)

        mock_stage = MagicMock()
        mock_stage.execute.return_value = {"status": "success"}

        with patch.object(orch, "_prepare") as mock_prep, patch(
            "azext_prototype.stages.deploy_stage.DeployStage", return_value=mock_stage
        ):
            mock_prep.return_value = (str(project_with_config), MagicMock(), MagicMock(), MagicMock())

            orch._run_deploy()

        call_kwargs = mock_stage.execute.call_args[1]
        assert call_kwargs["input_fn"] is adapter.input_fn
        assert call_kwargs["print_fn"] is adapter.print_fn
        assert call_kwargs["status_fn"] is adapter.status_fn
