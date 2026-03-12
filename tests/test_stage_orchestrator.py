"""Tests for azext_prototype.ui.stage_orchestrator — stage detection and tree population."""

from unittest.mock import MagicMock, call, patch

from azext_prototype.ui.stage_orchestrator import StageOrchestrator, detect_stage
from azext_prototype.ui.task_model import TaskStatus


# ------------------------------------------------------------------
# detect_stage
# ------------------------------------------------------------------


class TestDetectStage:
    """Test detect_stage() based on state files."""

    def test_init_only(self, tmp_path):
        """No state files → init."""
        (tmp_path / ".prototype" / "state").mkdir(parents=True)
        assert detect_stage(str(tmp_path)) == "init"

    def test_discovery_yaml(self, tmp_path):
        state = tmp_path / ".prototype" / "state"
        state.mkdir(parents=True)
        (state / "discovery.yaml").write_text("exchanges: []")
        assert detect_stage(str(tmp_path)) == "design"

    def test_design_json(self, tmp_path):
        state = tmp_path / ".prototype" / "state"
        state.mkdir(parents=True)
        (state / "design.json").write_text("{}")
        assert detect_stage(str(tmp_path)) == "design"

    def test_build_yaml(self, tmp_path):
        state = tmp_path / ".prototype" / "state"
        state.mkdir(parents=True)
        (state / "build.yaml").write_text("deployment_stages: []")
        assert detect_stage(str(tmp_path)) == "build"

    def test_deploy_yaml(self, tmp_path):
        state = tmp_path / ".prototype" / "state"
        state.mkdir(parents=True)
        (state / "deploy.yaml").write_text("deployment_stages: []")
        assert detect_stage(str(tmp_path)) == "deploy"

    def test_deploy_takes_precedence(self, tmp_path):
        """All state files present → deploy wins."""
        state = tmp_path / ".prototype" / "state"
        state.mkdir(parents=True)
        (state / "discovery.yaml").write_text("")
        (state / "build.yaml").write_text("")
        (state / "deploy.yaml").write_text("")
        assert detect_stage(str(tmp_path)) == "deploy"


# ------------------------------------------------------------------
# Task tree population
# ------------------------------------------------------------------


class TestPopulateFromState:
    """Test that _populate_from_state marks correct stages."""

    def _make_orchestrator(self, tmp_path):
        app = MagicMock()
        adapter = MagicMock()
        # Track update_task calls
        adapter.update_task = MagicMock()
        return StageOrchestrator(app, adapter, str(tmp_path)), adapter

    def test_init_only_marks_design_pending(self, tmp_path):
        """When only init is done, design/build/deploy should NOT be completed."""
        (tmp_path / ".prototype" / "state").mkdir(parents=True)
        orch, adapter = self._make_orchestrator(tmp_path)

        orch._populate_from_state("init")

        # Design, build, deploy should not be marked as completed
        completed_calls = [
            c for c in adapter.update_task.call_args_list if c == call("design", TaskStatus.COMPLETED)
        ]
        assert len(completed_calls) == 0

    def test_design_done_marks_design_completed(self, tmp_path):
        """When design is done, design should be completed but not build/deploy."""
        state = tmp_path / ".prototype" / "state"
        state.mkdir(parents=True)
        (state / "discovery.yaml").write_text("exchanges: []")
        orch, adapter = self._make_orchestrator(tmp_path)

        orch._populate_from_state("design")

        completed_calls = [c for c in adapter.update_task.call_args_list if c[0][1] == TaskStatus.COMPLETED]
        completed_names = [c[0][0] for c in completed_calls]
        assert "design" in completed_names
        assert "build" not in completed_names
        assert "deploy" not in completed_names

    def test_build_done_marks_design_and_build_completed(self, tmp_path):
        state = tmp_path / ".prototype" / "state"
        state.mkdir(parents=True)
        (state / "build.yaml").write_text("deployment_stages: []")
        orch, adapter = self._make_orchestrator(tmp_path)

        orch._populate_from_state("build")

        completed_calls = [c for c in adapter.update_task.call_args_list if c[0][1] == TaskStatus.COMPLETED]
        completed_names = [c[0][0] for c in completed_calls]
        assert "design" in completed_names
        assert "build" in completed_names
        assert "deploy" not in completed_names


class TestRunStageStatus:
    """Test that run() correctly marks target stage as in-progress."""

    def _make_orchestrator(self, tmp_path):
        app = MagicMock()
        adapter = MagicMock()
        adapter.update_task = MagicMock()
        adapter.print_fn = MagicMock()
        adapter.input_fn = MagicMock(return_value="quit")
        return StageOrchestrator(app, adapter, str(tmp_path)), adapter

    @patch("azext_prototype.ui.stage_orchestrator.detect_stage", return_value="init")
    def test_design_stage_marked_in_progress_when_init_only(self, mock_detect, tmp_path):
        """When launching design from init state, design should be IN_PROGRESS not COMPLETED."""
        (tmp_path / ".prototype" / "state").mkdir(parents=True)
        orch, adapter = self._make_orchestrator(tmp_path)

        orch.run(start_stage="design")

        # Design should be marked IN_PROGRESS (not COMPLETED)
        in_progress_calls = [
            c for c in adapter.update_task.call_args_list if c == call("design", TaskStatus.IN_PROGRESS)
        ]
        assert len(in_progress_calls) >= 1

        # Design should NOT be marked COMPLETED before it runs
        completed_calls = [
            c for c in adapter.update_task.call_args_list if c == call("design", TaskStatus.COMPLETED)
        ]
        assert len(completed_calls) == 0

    @patch("azext_prototype.ui.stage_orchestrator.detect_stage", return_value="design")
    def test_design_stage_completed_when_already_done(self, mock_detect, tmp_path):
        """When design is already done and we re-enter, it should show as COMPLETED."""
        state = tmp_path / ".prototype" / "state"
        state.mkdir(parents=True)
        (state / "discovery.yaml").write_text("exchanges: []")
        orch, adapter = self._make_orchestrator(tmp_path)

        orch.run(start_stage="design")

        # Design should be marked COMPLETED (detected == start_stage)
        completed_calls = [
            c for c in adapter.update_task.call_args_list if c == call("design", TaskStatus.COMPLETED)
        ]
        assert len(completed_calls) >= 1


class TestStageGuard:
    """Test that skipping stages is blocked."""

    def _make_orchestrator(self, tmp_path):
        app = MagicMock()
        adapter = MagicMock()
        adapter.update_task = MagicMock()
        adapter.print_fn = MagicMock()
        adapter.input_fn = MagicMock(return_value="quit")
        return StageOrchestrator(app, adapter, str(tmp_path)), adapter

    @patch("azext_prototype.ui.stage_orchestrator.detect_stage", return_value="init")
    def test_skip_to_deploy_blocked(self, mock_detect, tmp_path):
        """Cannot skip from init to deploy — should fall back to design."""
        (tmp_path / ".prototype" / "state").mkdir(parents=True)
        orch, adapter = self._make_orchestrator(tmp_path)

        orch.run(start_stage="deploy")

        # Should print a warning about skipping
        printed = " ".join(str(c) for c in adapter.print_fn.call_args_list)
        assert "Cannot skip" in printed

        # Design (the next valid stage) should be marked IN_PROGRESS, not deploy
        in_progress_calls = [
            c for c in adapter.update_task.call_args_list if c == call("design", TaskStatus.IN_PROGRESS)
        ]
        assert len(in_progress_calls) >= 1

        # Deploy should NOT be marked IN_PROGRESS
        deploy_in_progress = [
            c for c in adapter.update_task.call_args_list if c == call("deploy", TaskStatus.IN_PROGRESS)
        ]
        assert len(deploy_in_progress) == 0

    @patch("azext_prototype.ui.stage_orchestrator.detect_stage", return_value="init")
    def test_skip_to_build_blocked(self, mock_detect, tmp_path):
        """Cannot skip from init to build — should fall back to design."""
        (tmp_path / ".prototype" / "state").mkdir(parents=True)
        orch, adapter = self._make_orchestrator(tmp_path)

        orch.run(start_stage="build")

        printed = " ".join(str(c) for c in adapter.print_fn.call_args_list)
        assert "Cannot skip" in printed

    @patch("azext_prototype.ui.stage_orchestrator.detect_stage", return_value="design")
    def test_skip_to_deploy_from_design_blocked(self, mock_detect, tmp_path):
        """Cannot skip from design to deploy — should fall back to build."""
        (tmp_path / ".prototype" / "state").mkdir(parents=True)
        orch, adapter = self._make_orchestrator(tmp_path)

        orch.run(start_stage="deploy")

        printed = " ".join(str(c) for c in adapter.print_fn.call_args_list)
        assert "Cannot skip" in printed

        build_in_progress = [
            c for c in adapter.update_task.call_args_list if c == call("build", TaskStatus.IN_PROGRESS)
        ]
        assert len(build_in_progress) >= 1

    @patch("azext_prototype.ui.stage_orchestrator.detect_stage", return_value="design")
    def test_next_stage_allowed(self, mock_detect, tmp_path):
        """design → build is valid (next stage), should NOT show skip warning."""
        (tmp_path / ".prototype" / "state").mkdir(parents=True)
        orch, adapter = self._make_orchestrator(tmp_path)

        orch.run(start_stage="build")

        printed = " ".join(str(c) for c in adapter.print_fn.call_args_list)
        assert "Cannot skip" not in printed

    @patch("azext_prototype.ui.stage_orchestrator.detect_stage", return_value="build")
    def test_rerun_completed_stage_allowed(self, mock_detect, tmp_path):
        """Re-running design (already completed) should NOT show skip warning."""
        (tmp_path / ".prototype" / "state").mkdir(parents=True)
        orch, adapter = self._make_orchestrator(tmp_path)

        orch.run(start_stage="design")

        printed = " ".join(str(c) for c in adapter.print_fn.call_args_list)
        assert "Cannot skip" not in printed
