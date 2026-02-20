"""Tests for deploy_stage.py, build_stage.py, and init_stage.py — full coverage."""

import json
from unittest.mock import MagicMock, patch

import pytest
from knack.util import CLIError

from azext_prototype.ai.provider import AIResponse
from azext_prototype.stages.build_session import BuildResult


# ======================================================================
# DeployStage
# ======================================================================


class TestDeployStageExecution:
    """Test DeployStage orchestration and deploy_helpers functions."""

    def _make_stage(self):
        from azext_prototype.stages.deploy_stage import DeployStage
        return DeployStage()

    def test_deploy_guards(self):
        stage = self._make_stage()
        guards = stage.get_guards()
        names = [g.name for g in guards]
        assert "project_initialized" in names
        assert "build_complete" in names
        assert "az_logged_in" in names

    @patch("subprocess.run")
    def test_check_az_login_true(self, mock_run):
        from azext_prototype.stages.deploy_helpers import check_az_login
        mock_run.return_value = MagicMock(returncode=0)
        assert check_az_login() is True

    @patch("subprocess.run")
    def test_check_az_login_false(self, mock_run):
        from azext_prototype.stages.deploy_helpers import check_az_login
        mock_run.return_value = MagicMock(returncode=1)
        assert check_az_login() is False

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_check_az_login_not_installed(self, mock_run):
        from azext_prototype.stages.deploy_helpers import check_az_login
        assert check_az_login() is False

    @patch("subprocess.run")
    def test_get_current_subscription(self, mock_run):
        from azext_prototype.stages.deploy_helpers import get_current_subscription
        mock_run.return_value = MagicMock(returncode=0, stdout="abc-123\n")
        result = get_current_subscription()
        assert result == "abc-123"

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_get_current_subscription_not_installed(self, mock_run):
        from azext_prototype.stages.deploy_helpers import get_current_subscription
        assert get_current_subscription() == ""

    @patch("subprocess.run")
    def test_deploy_terraform_success(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_terraform
        infra_dir = tmp_path / "tf"
        infra_dir.mkdir()
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = deploy_terraform(infra_dir, "sub-123")
        assert result["status"] == "deployed"

    @patch("subprocess.run")
    def test_deploy_terraform_failure(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_terraform
        infra_dir = tmp_path / "tf"
        infra_dir.mkdir()
        mock_run.return_value = MagicMock(returncode=1, stderr="init failed", stdout="")

        result = deploy_terraform(infra_dir, "sub-123")
        assert result["status"] == "failed"

    @patch("subprocess.run")
    def test_deploy_bicep_failure(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_bicep
        (tmp_path / "main.bicep").write_text("resource x 'y' = {}", encoding="utf-8")
        mock_run.return_value = MagicMock(returncode=1, stderr="Deployment failed", stdout="")

        result = deploy_bicep(tmp_path, "sub-123", "my-rg")
        assert result["status"] == "failed"

    def test_deploy_app_stage_with_deploy_script(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_app_stage
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "deploy.sh").write_text("echo deployed", encoding="utf-8")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = deploy_app_stage(app_dir, "sub-123", "my-rg")
            assert result["status"] == "deployed"

    def test_deploy_app_stage_sub_apps(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_app_stage
        stage_dir = tmp_path / "stage"
        stage_dir.mkdir()
        backend = stage_dir / "backend"
        backend.mkdir()
        (backend / "deploy.sh").write_text("echo ok", encoding="utf-8")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = deploy_app_stage(stage_dir, "sub-123", "my-rg")
            assert result["status"] == "deployed"
            assert "backend" in result["apps"]

    def test_deploy_app_stage_no_scripts(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_app_stage
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result = deploy_app_stage(empty_dir, "sub-123", "my-rg")
        assert result["status"] == "skipped"

    @patch("subprocess.run")
    def test_whatif_bicep_no_files(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import whatif_bicep
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result = whatif_bicep(empty_dir, "sub-123", "my-rg")
        assert result["status"] == "skipped"

    @patch("subprocess.run")
    def test_whatif_bicep_no_rg_skips(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import whatif_bicep
        (tmp_path / "main.bicep").write_text("resource x 'y' = {}", encoding="utf-8")
        result = whatif_bicep(tmp_path, "sub-123", "")
        assert result["status"] == "skipped"

    def test_get_deploy_location_main_params(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import get_deploy_location
        (tmp_path / "main.parameters.json").write_text(
            '{"parameters": {"location": {"value": "northeurope"}}}', encoding="utf-8"
        )
        result = get_deploy_location(tmp_path)
        assert result == "northeurope"

    def test_get_deploy_location_string_value(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import get_deploy_location
        (tmp_path / "parameters.json").write_text(
            '{"location": "uksouth"}', encoding="utf-8"
        )
        result = get_deploy_location(tmp_path)
        assert result == "uksouth"

    def test_execute_status(self, project_with_build, mock_agent_context, populated_registry):
        """Deploy with --status shows state and returns."""
        stage = self._make_stage()
        stage.get_guards = lambda: []
        mock_agent_context.project_dir = str(project_with_build)

        result = stage.execute(
            mock_agent_context, populated_registry,
            status=True,
        )
        assert result["status"] == "status_displayed"

    def test_execute_reset(self, project_with_build, mock_agent_context, populated_registry):
        """Deploy with --reset clears state and returns."""
        stage = self._make_stage()
        stage.get_guards = lambda: []
        mock_agent_context.project_dir = str(project_with_build)

        result = stage.execute(
            mock_agent_context, populated_registry,
            reset=True,
        )
        assert result["status"] == "reset"


# ======================================================================
# BuildStage
# ======================================================================


class TestBuildStageExecution:
    """Test BuildStage methods."""

    def _make_stage(self):
        from azext_prototype.stages.build_stage import BuildStage
        return BuildStage()

    def test_build_guards(self):
        stage = self._make_stage()
        guards = stage.get_guards()
        names = [g.name for g in guards]
        assert "project_initialized" in names
        assert "discovery_complete" in names
        assert "design_complete" in names

    def test_load_design(self, project_with_design):
        stage = self._make_stage()
        design = stage._load_design(str(project_with_design))
        assert "architecture" in design

    def test_load_design_missing(self, tmp_project):
        stage = self._make_stage()
        result = stage._load_design(str(tmp_project))
        assert result == {}

    def test_execute_no_design_raises(self, project_with_config, mock_agent_context, populated_registry):
        stage = self._make_stage()
        stage.get_guards = lambda: []
        mock_agent_context.project_dir = str(project_with_config)

        with pytest.raises(CLIError, match="No architecture design"):
            stage.execute(mock_agent_context, populated_registry)

    def test_execute_dry_run(self, project_with_design, mock_agent_context, populated_registry):
        stage = self._make_stage()
        stage.get_guards = lambda: []
        mock_agent_context.project_dir = str(project_with_design)
        mock_agent_context.ai_provider.chat.return_value = AIResponse(
            content="Generated code", model="gpt-4o"
        )

        result = stage.execute(
            mock_agent_context, populated_registry, scope="docs", dry_run=True
        )
        assert result["status"] == "dry-run"

    def test_execute_all_scopes_dry_run(self, project_with_design, mock_agent_context, populated_registry):
        stage = self._make_stage()
        stage.get_guards = lambda: []
        mock_agent_context.project_dir = str(project_with_design)

        result = stage.execute(
            mock_agent_context, populated_registry, scope="all", dry_run=True
        )
        assert result["status"] == "dry-run"
        assert result["scope"] == "all"

    @patch("azext_prototype.stages.build_stage.BuildSession")
    def test_execute_interactive_delegates_to_session(
        self, mock_session_cls, project_with_design, mock_agent_context, populated_registry
    ):
        stage = self._make_stage()
        stage.get_guards = lambda: []
        mock_agent_context.project_dir = str(project_with_design)

        mock_result = BuildResult(
            files_generated=["main.tf"],
            deployment_stages=[{"stage": 1, "name": "Foundation"}],
            policy_overrides=[],
            resources=[{"resourceType": "Microsoft.Compute/virtualMachines", "sku": "Standard_B2s"}],
            review_accepted=True,
            cancelled=False,
        )
        mock_session_cls.return_value.run.return_value = mock_result

        result = stage.execute(
            mock_agent_context, populated_registry, scope="all", dry_run=False
        )
        assert result["status"] == "success"
        assert result["scope"] == "all"
        assert result["files_generated"] == ["main.tf"]
        mock_session_cls.return_value.run.assert_called_once()


# ======================================================================
# InitStage
# ======================================================================


class TestInitStageExecution:
    """Test InitStage methods."""

    def _make_stage(self):
        from azext_prototype.stages.init_stage import InitStage
        return InitStage()

    def test_init_guards(self):
        """Init has no unconditional guards; gh check is conditional inside execute()."""
        stage = self._make_stage()
        guards = stage.get_guards()
        assert len(guards) == 0

    @patch("subprocess.run")
    def test_check_gh_true(self, mock_run):
        stage = self._make_stage()
        mock_run.return_value = MagicMock(returncode=0)
        assert stage._check_gh() is True

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_check_gh_false(self, mock_run):
        stage = self._make_stage()
        assert stage._check_gh() is False

    def test_create_scaffold(self, tmp_path):
        stage = self._make_stage()
        project_dir = tmp_path / "my-project"
        stage._create_scaffold(project_dir)

        assert (project_dir / "concept" / "apps").is_dir()
        assert (project_dir / "concept" / "infra" / "terraform").is_dir()
        assert (project_dir / "concept" / "infra" / "bicep").is_dir()
        assert (project_dir / "concept" / "db" / "sql").is_dir()
        assert (project_dir / "concept" / "db" / "cosmos").is_dir()
        assert (project_dir / "concept" / "db" / "databricks").is_dir()
        assert (project_dir / "concept" / "db" / "fabric").is_dir()
        assert (project_dir / "concept" / "docs").is_dir()
        assert (project_dir / ".prototype" / "agents").is_dir()

    def test_create_gitignore(self, tmp_path):
        stage = self._make_stage()
        stage._create_gitignore(tmp_path)
        gi = tmp_path / ".gitignore"
        assert gi.exists()
        content = gi.read_text()
        assert ".terraform/" in content
        assert "__pycache__/" in content

    def test_create_gitignore_no_overwrite(self, tmp_path):
        stage = self._make_stage()
        gi = tmp_path / ".gitignore"
        gi.write_text("custom content", encoding="utf-8")
        stage._create_gitignore(tmp_path)
        assert gi.read_text() == "custom content"

    @patch("azext_prototype.auth.copilot_license.CopilotLicenseValidator")
    @patch("azext_prototype.auth.github_auth.GitHubAuthManager")
    @patch("azext_prototype.stages.init_stage.InitStage._check_gh", return_value=True)
    def test_execute_full(self, mock_gh, mock_auth_cls, mock_lic_cls, tmp_path):
        stage = self._make_stage()
        stage.get_guards = lambda: []

        mock_auth = MagicMock()
        mock_auth.ensure_authenticated.return_value = {"login": "devuser"}
        mock_auth_cls.return_value = mock_auth
        mock_lic = MagicMock()
        mock_lic.validate_license.return_value = {"plan": "business"}
        mock_lic_cls.return_value = mock_lic

        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry

        ctx = AgentContext(project_config={}, project_dir=str(tmp_path), ai_provider=None)
        registry = AgentRegistry()

        result = stage.execute(
            ctx, registry,
            name="test-proj", location="westus2", iac_tool="bicep",
            ai_provider="github-models", output_dir=str(tmp_path),
        )
        assert result["status"] == "success"
        assert (tmp_path / "test-proj" / "prototype.yaml").exists()

    @patch("azext_prototype.auth.copilot_license.CopilotLicenseValidator")
    @patch("azext_prototype.auth.github_auth.GitHubAuthManager")
    @patch("azext_prototype.stages.init_stage.InitStage._check_gh", return_value=True)
    def test_execute_license_failure_continues(self, mock_gh, mock_auth_cls, mock_lic_cls, tmp_path):
        """License validation failure should warn but continue."""
        stage = self._make_stage()
        stage.get_guards = lambda: []

        mock_auth = MagicMock()
        mock_auth.ensure_authenticated.return_value = {"login": "devuser"}
        mock_auth_cls.return_value = mock_auth
        mock_lic = MagicMock()
        mock_lic.validate_license.side_effect = CLIError("No license")
        mock_lic_cls.return_value = mock_lic

        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry

        ctx = AgentContext(project_config={}, project_dir=str(tmp_path), ai_provider=None)
        registry = AgentRegistry()

        result = stage.execute(
            ctx, registry,
            name="lic-test", location="eastus", ai_provider="github-models",
            output_dir=str(tmp_path),
        )
        assert result["status"] == "success"
        assert result["copilot_license"]["status"] == "unverified"

    def test_execute_no_name_raises(self, tmp_path):
        stage = self._make_stage()
        stage.get_guards = lambda: []

        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry

        ctx = AgentContext(project_config={}, project_dir=str(tmp_path), ai_provider=None)
        registry = AgentRegistry()

        with pytest.raises(CLIError, match="Project name"):
            stage.execute(ctx, registry, name="", output_dir=str(tmp_path))

    def test_execute_no_location_raises(self, tmp_path):
        stage = self._make_stage()
        stage.get_guards = lambda: []

        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry

        ctx = AgentContext(project_config={}, project_dir=str(tmp_path), ai_provider=None)
        registry = AgentRegistry()

        with pytest.raises(CLIError, match="region is required"):
            stage.execute(
                ctx, registry,
                name="test-proj", location=None, output_dir=str(tmp_path),
            )

    def test_execute_azure_openai_skips_auth(self, tmp_path):
        """azure-openai provider should skip GitHub auth entirely."""
        stage = self._make_stage()
        stage.get_guards = lambda: []

        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry

        ctx = AgentContext(project_config={}, project_dir=str(tmp_path), ai_provider=None)
        registry = AgentRegistry()

        result = stage.execute(
            ctx, registry,
            name="aoai-test", location="eastus", ai_provider="azure-openai",
            output_dir=str(tmp_path),
        )
        assert result["status"] == "success"
        assert result["github_user"] is None
        assert "copilot_license" not in result

    def test_execute_environment_stored(self, tmp_path):
        """--environment should be persisted in config."""
        stage = self._make_stage()
        stage.get_guards = lambda: []

        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.config import ProjectConfig

        ctx = AgentContext(project_config={}, project_dir=str(tmp_path), ai_provider=None)
        registry = AgentRegistry()

        stage.execute(
            ctx, registry,
            name="env-test", location="westus2", ai_provider="azure-openai",
            environment="prod", output_dir=str(tmp_path),
        )
        config = ProjectConfig(str(tmp_path / "env-test"))
        config.load()
        assert config.get("project.environment") == "prod"

    def test_execute_model_override(self, tmp_path):
        """Explicit --model should override provider default."""
        stage = self._make_stage()
        stage.get_guards = lambda: []

        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.config import ProjectConfig

        ctx = AgentContext(project_config={}, project_dir=str(tmp_path), ai_provider=None)
        registry = AgentRegistry()

        stage.execute(
            ctx, registry,
            name="model-test", location="eastus", ai_provider="azure-openai",
            model="gpt-4o-mini", output_dir=str(tmp_path),
        )
        config = ProjectConfig(str(tmp_path / "model-test"))
        config.load()
        assert config.get("ai.model") == "gpt-4o-mini"

    def test_execute_idempotency_cancel(self, tmp_path):
        """Existing project + user declining should cancel."""
        stage = self._make_stage()
        stage.get_guards = lambda: []

        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry

        # Pre-create project directory with config
        proj = tmp_path / "idem-test"
        proj.mkdir()
        (proj / "prototype.yaml").write_text("project:\n  name: old\n")

        ctx = AgentContext(project_config={}, project_dir=str(tmp_path), ai_provider=None)
        registry = AgentRegistry()

        with patch("builtins.input", return_value="n"):
            result = stage.execute(
                ctx, registry,
                name="idem-test", location="eastus", ai_provider="azure-openai",
                output_dir=str(tmp_path),
            )
        assert result["status"] == "cancelled"

    def test_execute_marks_init_complete(self, tmp_path):
        """Init stage should set stages.init.completed and timestamp."""
        stage = self._make_stage()
        stage.get_guards = lambda: []

        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.config import ProjectConfig

        ctx = AgentContext(project_config={}, project_dir=str(tmp_path), ai_provider=None)
        registry = AgentRegistry()

        stage.execute(
            ctx, registry,
            name="complete-test", location="eastus", ai_provider="azure-openai",
            output_dir=str(tmp_path),
        )
        config = ProjectConfig(str(tmp_path / "complete-test"))
        config.load()
        assert config.get("stages.init.completed") is True
        assert config.get("stages.init.timestamp") is not None
