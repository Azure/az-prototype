"""Tests for DeployStage — routing logic, state transitions, guard conditions.

Covers:
- Guard conditions (project_initialized, build_complete, az_logged_in)
- Routing: --status, --reset, --dry-run, --stage N, interactive
- State transitions between modes
- _result_to_dict conversion
"""

from unittest.mock import MagicMock, patch

import pytest

from azext_prototype.stages.base import StageState

# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def deploy_stage():
    from azext_prototype.stages.deploy_stage import DeployStage

    return DeployStage()


@pytest.fixture
def agent_context(project_with_build, sample_config):
    from azext_prototype.agents.base import AgentContext

    provider = MagicMock()
    provider.provider_name = "github-models"
    provider.chat.return_value = MagicMock(content="ok", model="test", usage={})
    return AgentContext(
        project_config=sample_config,
        project_dir=str(project_with_build),
        ai_provider=provider,
    )


@pytest.fixture
def registry():
    return MagicMock()


# ======================================================================
# Guard validation
# ======================================================================


class TestDeployStageGuards:
    """Test deploy stage prerequisites."""

    def test_guards_return_three_guards(self, deploy_stage):
        guards = deploy_stage.get_guards()
        assert len(guards) == 3
        names = [g.name for g in guards]
        assert "project_initialized" in names
        assert "build_complete" in names
        assert "az_logged_in" in names

    def test_all_guards_pass(self, deploy_stage, project_with_build, monkeypatch):
        monkeypatch.chdir(project_with_build)
        with patch("azext_prototype.stages.deploy_helpers.check_az_login", return_value=True):
            # Reload guards with the patched function
            from azext_prototype.stages.deploy_stage import DeployStage

            stage = DeployStage()
            can_run, failures = stage.can_run()
        assert can_run is True
        assert failures == []

    def test_missing_project_yaml(self, deploy_stage, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        can_run, failures = deploy_stage.can_run()
        assert can_run is False
        assert any("init" in f.lower() or "prototype" in f.lower() for f in failures)

    def test_missing_build_state(self, deploy_stage, project_with_config, monkeypatch):
        monkeypatch.chdir(project_with_config)
        can_run, failures = deploy_stage.can_run()
        assert can_run is False
        assert any("build" in f.lower() for f in failures)

    def test_not_logged_in(self, deploy_stage, project_with_build, monkeypatch):
        monkeypatch.chdir(project_with_build)
        with patch("azext_prototype.stages.deploy_stage.check_az_login", return_value=False):
            from azext_prototype.stages.deploy_stage import DeployStage

            stage = DeployStage()
            can_run, failures = stage.can_run()
        assert can_run is False
        assert any("login" in f.lower() for f in failures)


# ======================================================================
# --status routing
# ======================================================================


class TestDeployStageStatusRoute:
    """Test --status shows current progress without starting session."""

    def test_status_route(self, deploy_stage, agent_context, registry):
        with patch("azext_prototype.stages.deploy_stage.DeployState") as mock_ds, patch(
            "azext_prototype.stages.deploy_stage.default_console"
        ) as mock_console:
            mock_ds.return_value.load.return_value = None
            mock_ds.return_value.format_stage_status.return_value = "Stage status output"
            result = deploy_stage.execute(agent_context, registry, status=True)
        assert result["status"] == "status_displayed"
        assert deploy_stage.state == StageState.COMPLETED
        mock_console.print_info.assert_called_once()


# ======================================================================
# --reset routing
# ======================================================================


class TestDeployStageResetRoute:
    """Test --reset clears deploy state."""

    def test_reset_route(self, deploy_stage, agent_context, registry):
        with patch("azext_prototype.stages.deploy_stage.DeployState") as mock_ds, patch(
            "azext_prototype.stages.deploy_stage.default_console"
        ):
            result = deploy_stage.execute(agent_context, registry, reset=True)
        assert result["status"] == "reset"
        assert deploy_stage.state == StageState.COMPLETED
        mock_ds.return_value.reset.assert_called_once()


# ======================================================================
# --dry-run routing
# ======================================================================


class TestDeployStageDryRunRoute:
    """Test --dry-run delegates to session.run_dry_run()."""

    def test_dry_run_route(self, deploy_stage, agent_context, registry):
        mock_result = MagicMock()
        mock_result.failed_stages = []
        mock_result.cancelled = False
        mock_result.deployed_stages = []
        mock_result.rolled_back_stages = []
        mock_result.captured_outputs = {}

        with patch("azext_prototype.stages.deploy_stage.DeploySession") as mock_session_cls:
            mock_session_cls.return_value.run_dry_run.return_value = mock_result
            result = deploy_stage.execute(agent_context, registry, dry_run=True, subscription="sub-123")
        assert result["status"] == "success"
        assert result["mode"] == "dry-run"
        assert deploy_stage.state == StageState.COMPLETED

    def test_dry_run_with_stage(self, deploy_stage, agent_context, registry):
        mock_result = MagicMock()
        mock_result.failed_stages = []
        mock_result.cancelled = False
        mock_result.deployed_stages = []
        mock_result.rolled_back_stages = []
        mock_result.captured_outputs = {}

        with patch("azext_prototype.stages.deploy_stage.DeploySession") as mock_session_cls:
            mock_session_cls.return_value.run_dry_run.return_value = mock_result
            result = deploy_stage.execute(agent_context, registry, dry_run=True, stage=2)
        assert result["mode"] == "dry-run"
        mock_session_cls.return_value.run_dry_run.assert_called_once()


# ======================================================================
# --stage N routing
# ======================================================================


class TestDeployStageSingleStageRoute:
    """Test --stage N delegates to session.run_single_stage()."""

    def test_single_stage_success(self, deploy_stage, agent_context, registry):
        mock_result = MagicMock()
        mock_result.failed_stages = []
        mock_result.cancelled = False
        mock_result.deployed_stages = ["stage-1"]
        mock_result.rolled_back_stages = []
        mock_result.captured_outputs = {}

        with patch("azext_prototype.stages.deploy_stage.DeploySession") as mock_session_cls:
            mock_session_cls.return_value.run_single_stage.return_value = mock_result
            result = deploy_stage.execute(agent_context, registry, stage=1, subscription="sub-123")
        assert result["mode"] == "single_stage"
        assert deploy_stage.state == StageState.COMPLETED

    def test_single_stage_failure(self, deploy_stage, agent_context, registry):
        mock_result = MagicMock()
        mock_result.failed_stages = ["stage-1"]
        mock_result.cancelled = False
        mock_result.deployed_stages = []
        mock_result.rolled_back_stages = []
        mock_result.captured_outputs = {}

        with patch("azext_prototype.stages.deploy_stage.DeploySession") as mock_session_cls:
            mock_session_cls.return_value.run_single_stage.return_value = mock_result
            result = deploy_stage.execute(agent_context, registry, stage=1)
        assert result["status"] == "partial_failure"
        assert deploy_stage.state == StageState.FAILED


# ======================================================================
# Interactive (default) routing
# ======================================================================


class TestDeployStageInteractiveRoute:
    """Test default interactive mode delegates to session.run()."""

    def test_interactive_success(self, deploy_stage, agent_context, registry):
        mock_result = MagicMock()
        mock_result.failed_stages = []
        mock_result.cancelled = False
        mock_result.deployed_stages = ["stage-1", "stage-2"]
        mock_result.rolled_back_stages = []
        mock_result.captured_outputs = {"terraform": {"key": "val"}}

        with patch("azext_prototype.stages.deploy_stage.DeploySession") as mock_session_cls:
            mock_session_cls.return_value.run.return_value = mock_result
            result = deploy_stage.execute(agent_context, registry)
        assert result["status"] == "success"
        assert result["mode"] == "interactive"
        assert result["deployed"] == 2
        assert deploy_stage.state == StageState.COMPLETED

    def test_interactive_cancelled(self, deploy_stage, agent_context, registry):
        mock_result = MagicMock()
        mock_result.cancelled = True
        mock_result.failed_stages = []
        mock_result.deployed_stages = []
        mock_result.rolled_back_stages = []
        mock_result.captured_outputs = {}

        with patch("azext_prototype.stages.deploy_stage.DeploySession") as mock_session_cls:
            mock_session_cls.return_value.run.return_value = mock_result
            result = deploy_stage.execute(agent_context, registry)
        assert result["status"] == "cancelled"
        assert deploy_stage.state == StageState.COMPLETED

    def test_interactive_partial_failure(self, deploy_stage, agent_context, registry):
        mock_result = MagicMock()
        mock_result.cancelled = False
        mock_result.failed_stages = ["stage-2"]
        mock_result.deployed_stages = ["stage-1"]
        mock_result.rolled_back_stages = []
        mock_result.captured_outputs = {}

        with patch("azext_prototype.stages.deploy_stage.DeploySession") as mock_session_cls:
            mock_session_cls.return_value.run.return_value = mock_result
            result = deploy_stage.execute(agent_context, registry)
        assert result["status"] == "partial_failure"
        assert deploy_stage.state == StageState.FAILED


# ======================================================================
# _result_to_dict
# ======================================================================


class TestResultToDict:
    """Test the result-to-dict conversion helper."""

    def test_success(self):
        from azext_prototype.stages.deploy_stage import _result_to_dict

        result = MagicMock()
        result.failed_stages = []
        result.cancelled = False
        result.deployed_stages = ["a", "b"]
        result.rolled_back_stages = []
        result.captured_outputs = {"tf": {"x": 1}}
        d = _result_to_dict(result, "test")
        assert d["status"] == "success"
        assert d["mode"] == "test"
        assert d["deployed"] == 2
        assert d["failed"] == 0

    def test_partial_failure(self):
        from azext_prototype.stages.deploy_stage import _result_to_dict

        result = MagicMock()
        result.failed_stages = ["x"]
        result.cancelled = False
        result.deployed_stages = ["a"]
        result.rolled_back_stages = ["b"]
        result.captured_outputs = {}
        d = _result_to_dict(result, "interactive")
        assert d["status"] == "partial_failure"
        assert d["rolled_back"] == 1

    def test_cancelled(self):
        from azext_prototype.stages.deploy_stage import _result_to_dict

        result = MagicMock()
        result.failed_stages = []
        result.cancelled = True
        result.deployed_stages = []
        result.rolled_back_stages = []
        result.captured_outputs = {}
        d = _result_to_dict(result, "interactive")
        assert d["status"] == "cancelled"
