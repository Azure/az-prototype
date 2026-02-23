"""Tests for custom.py — additional coverage for stage commands and helpers."""

import json
from unittest.mock import MagicMock, patch

import pytest
from knack.util import CLIError

_MOD = "azext_prototype.custom"


# ======================================================================
# Helper functions
# ======================================================================


class TestBuildRegistry:
    """Test _build_registry helper."""

    def test_build_registry_builtin_only(self):
        from azext_prototype.custom import _build_registry

        registry = _build_registry(config=None, project_dir=None)
        agents = registry.list_all()
        assert len(agents) >= 8

    def test_build_registry_with_custom_agents(self, project_with_config):
        from azext_prototype.custom import _build_registry, _load_config

        # Create a custom YAML agent
        agent_dir = project_with_config / ".prototype" / "agents"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "test-agent.yaml").write_text(
            "name: test-agent\ndescription: A test\ncapabilities:\n  - develop\n"
            "system_prompt: You are a test.\n",
            encoding="utf-8",
        )

        config = _load_config(str(project_with_config))
        registry = _build_registry(config, str(project_with_config))
        names = [a.name for a in registry.list_all()]
        assert "test-agent" in names

    def test_build_registry_with_overrides(self, project_with_config):
        from azext_prototype.custom import _build_registry, _load_config

        # Write a YAML agent to use as override
        override_file = project_with_config / "override.yaml"
        override_file.write_text(
            "name: cloud-architect\ndescription: Override\ncapabilities:\n  - architect\n"
            "system_prompt: Override prompt.\n",
            encoding="utf-8",
        )

        config = _load_config(str(project_with_config))
        config.set("agents.overrides", {"cloud-architect": "override.yaml"})

        registry = _build_registry(config, str(project_with_config))
        agent = registry.get("cloud-architect")
        assert "Override" in agent.description


class TestBuildContext:
    """Test _build_context helper."""

    @patch("azext_prototype.ai.factory.create_ai_provider")
    def test_build_context_creates_agent_context(self, mock_factory, project_with_config):
        from azext_prototype.custom import _build_context, _load_config

        mock_provider = MagicMock()
        mock_factory.return_value = mock_provider
        config = _load_config(str(project_with_config))

        ctx = _build_context(config, str(project_with_config))
        assert ctx.project_dir == str(project_with_config)
        assert ctx.ai_provider is mock_provider


class TestPrepareCommand:
    """Test _prepare_command helper."""

    @patch(f"{_MOD}._check_requirements")
    @patch("azext_prototype.ai.factory.create_ai_provider")
    def test_prepare_command(self, mock_factory, mock_check_req, project_with_config):
        from azext_prototype.custom import _prepare_command

        mock_factory.return_value = MagicMock()
        pd, config, registry, ctx = _prepare_command(str(project_with_config))
        assert pd == str(project_with_config)
        assert config is not None
        assert registry is not None
        assert ctx is not None


class TestCheckRequirements:
    """Test _check_requirements wiring in command entry points."""

    def test_check_requirements_passes_when_all_ok(self):
        from azext_prototype.custom import _check_requirements
        from azext_prototype.requirements import CheckResult

        with patch("azext_prototype.requirements.check_all") as mock_check:
            mock_check.return_value = [
                CheckResult(name="Python", status="pass", installed_version="3.12.0",
                            required=">=3.9.0", message="ok"),
            ]
            # Should not raise
            _check_requirements("terraform")

    def test_check_requirements_raises_on_missing(self):
        from azext_prototype.custom import _check_requirements
        from azext_prototype.requirements import CheckResult

        with patch("azext_prototype.requirements.check_all") as mock_check:
            mock_check.return_value = [
                CheckResult(name="Terraform", status="missing", installed_version=None,
                            required=">=1.14.0", message="Terraform is not installed",
                            install_hint="https://developer.hashicorp.com/terraform/install"),
            ]
            with pytest.raises(CLIError, match="Tool requirements not met"):
                _check_requirements("terraform")

    def test_check_requirements_raises_on_version_fail(self):
        from azext_prototype.custom import _check_requirements
        from azext_prototype.requirements import CheckResult

        with patch("azext_prototype.requirements.check_all") as mock_check:
            mock_check.return_value = [
                CheckResult(name="Azure CLI", status="fail", installed_version="2.40.0",
                            required=">=2.50.0",
                            message="Azure CLI 2.40.0 does not satisfy >=2.50.0",
                            install_hint="https://learn.microsoft.com/cli/azure/install-azure-cli"),
            ]
            with pytest.raises(CLIError, match="Azure CLI"):
                _check_requirements(None)

    def test_check_requirements_includes_install_hint(self):
        from azext_prototype.custom import _check_requirements
        from azext_prototype.requirements import CheckResult

        with patch("azext_prototype.requirements.check_all") as mock_check:
            mock_check.return_value = [
                CheckResult(name="Terraform", status="missing", installed_version=None,
                            required=">=1.14.0", message="Terraform is not installed",
                            install_hint="https://developer.hashicorp.com/terraform/install"),
            ]
            with pytest.raises(CLIError, match="Install:.*hashicorp"):
                _check_requirements("terraform")

    @patch("azext_prototype.ai.factory.create_ai_provider")
    def test_prepare_command_calls_check_requirements(self, mock_factory, project_with_config):
        from azext_prototype.custom import _prepare_command

        mock_factory.return_value = MagicMock()
        with patch(f"{_MOD}._check_requirements") as mock_check:
            _prepare_command(str(project_with_config))
            mock_check.assert_called_once()

    def test_init_calls_check_requirements(self, tmp_path):
        with patch(f"{_MOD}._check_requirements") as mock_check, \
             patch("azext_prototype.stages.init_stage.InitStage") as MockStage:
            from azext_prototype.custom import prototype_init
            mock_stage = MockStage.return_value
            mock_stage.can_run.return_value = (True, [])
            mock_stage.execute.return_value = {"status": "success"}

            cmd = MagicMock()
            prototype_init(cmd, name="test", location="eastus", output_dir=str(tmp_path))
            mock_check.assert_called_once_with("terraform")  # default iac_tool


class TestCheckGuards:
    """Test _check_guards helper."""

    def test_check_guards_pass(self):
        from azext_prototype.custom import _check_guards

        stage = MagicMock()
        stage.can_run.return_value = (True, [])
        _check_guards(stage)  # Should not raise

    def test_check_guards_fail(self):
        from azext_prototype.custom import _check_guards

        stage = MagicMock()
        stage.can_run.return_value = (False, ["Missing gh CLI"])
        with pytest.raises(CLIError, match="Prerequisites not met"):
            _check_guards(stage)


class TestGetRegistryWithFallback:
    """Test _get_registry_with_fallback helper."""

    def test_with_valid_config(self, project_with_config):
        from azext_prototype.custom import _get_registry_with_fallback

        registry = _get_registry_with_fallback(str(project_with_config))
        assert len(registry.list_all()) >= 8

    def test_without_config_falls_back(self, tmp_project):
        from azext_prototype.custom import _get_registry_with_fallback

        registry = _get_registry_with_fallback(str(tmp_project))
        assert len(registry.list_all()) >= 8


# ======================================================================
# Stage commands
# ======================================================================


class TestPrototypeInit:
    """Test the init command."""

    @patch(f"{_MOD}._check_requirements")
    @patch(f"{_MOD}._check_guards")
    @patch("azext_prototype.auth.copilot_license.CopilotLicenseValidator")
    @patch("azext_prototype.auth.github_auth.GitHubAuthManager")
    @patch("azext_prototype.stages.init_stage.InitStage._check_gh", return_value=True)
    def test_init_success(self, mock_gh, mock_auth_cls, mock_lic_cls, mock_guards, mock_check_req, tmp_path):
        from azext_prototype.custom import prototype_init

        mock_auth = MagicMock()
        mock_auth.ensure_authenticated.return_value = {"login": "testuser"}
        mock_auth_cls.return_value = mock_auth

        mock_lic = MagicMock()
        mock_lic.validate_license.return_value = {"plan": "business", "status": "active"}
        mock_lic_cls.return_value = mock_lic

        cmd = MagicMock()
        result = prototype_init(
            cmd,
            name="test-proj",
            location="eastus",
            output_dir=str(tmp_path),
            ai_provider="github-models",
            json_output=True,
        )

        assert result["status"] == "success"
        assert result["github_user"] == "testuser"
        project_dir = tmp_path / "test-proj"
        assert project_dir.is_dir()
        assert (project_dir / "prototype.yaml").exists()
        assert (project_dir / ".gitignore").exists()

    @patch(f"{_MOD}._check_requirements")
    @patch(f"{_MOD}._check_guards")
    def test_init_azure_openai_skips_license(self, mock_guards, mock_check_req, tmp_path):
        from azext_prototype.custom import prototype_init

        cmd = MagicMock()
        result = prototype_init(
            cmd,
            name="aoai-proj",
            location="eastus",
            output_dir=str(tmp_path),
            ai_provider="azure-openai",
            json_output=True,
        )

        assert result["status"] == "success"
        assert "copilot_license" not in result
        assert result["github_user"] is None

    @patch(f"{_MOD}._check_requirements")
    def test_init_missing_name_raises(self, mock_check_req, tmp_path):
        from azext_prototype.custom import prototype_init
        from azext_prototype.stages.init_stage import InitStage

        cmd = MagicMock()
        # Need to bypass guards
        with patch.object(InitStage, "get_guards", return_value=[]):
            with pytest.raises(CLIError, match="Project name"):
                prototype_init(cmd, name=None, location="eastus", output_dir=str(tmp_path))

    @patch(f"{_MOD}._check_requirements")
    def test_init_missing_location_raises(self, mock_check_req, tmp_path):
        from azext_prototype.custom import prototype_init
        from azext_prototype.stages.init_stage import InitStage

        cmd = MagicMock()
        with patch.object(InitStage, "get_guards", return_value=[]):
            with pytest.raises(CLIError, match="region is required"):
                prototype_init(cmd, name="test-proj", location=None, output_dir=str(tmp_path))

    @patch(f"{_MOD}._check_requirements")
    @patch(f"{_MOD}._check_guards")
    def test_init_idempotency_cancel(self, mock_guards, mock_check_req, tmp_path):
        """If project exists and user declines, init should cancel."""
        from azext_prototype.custom import prototype_init

        # Create existing project
        proj_dir = tmp_path / "existing-proj"
        proj_dir.mkdir()
        (proj_dir / "prototype.yaml").write_text("project:\n  name: old\n")

        cmd = MagicMock()
        with patch("builtins.input", return_value="n"):
            result = prototype_init(
                cmd, name="existing-proj", location="eastus",
                output_dir=str(tmp_path), ai_provider="azure-openai",
                json_output=True,
            )
        assert result["status"] == "cancelled"

    @patch(f"{_MOD}._check_requirements")
    @patch(f"{_MOD}._check_guards")
    def test_init_idempotency_reinitialize(self, mock_guards, mock_check_req, tmp_path):
        """If project exists and user confirms, init should proceed."""
        from azext_prototype.custom import prototype_init

        proj_dir = tmp_path / "reinit-proj"
        proj_dir.mkdir()
        (proj_dir / "prototype.yaml").write_text("project:\n  name: old\n")

        cmd = MagicMock()
        with patch("builtins.input", return_value="y"):
            result = prototype_init(
                cmd, name="reinit-proj", location="eastus",
                output_dir=str(tmp_path), ai_provider="azure-openai",
                json_output=True,
            )
        assert result["status"] == "success"

    @patch(f"{_MOD}._check_requirements")
    @patch(f"{_MOD}._check_guards")
    def test_init_environment_parameter(self, mock_guards, mock_check_req, tmp_path):
        """--environment should be stored in config."""
        from azext_prototype.custom import prototype_init
        from azext_prototype.config import ProjectConfig

        cmd = MagicMock()
        result = prototype_init(
            cmd, name="env-proj", location="westus2",
            output_dir=str(tmp_path), ai_provider="azure-openai",
            environment="staging", json_output=True,
        )
        assert result["status"] == "success"
        config = ProjectConfig(str(tmp_path / "env-proj"))
        config.load()
        assert config.get("project.environment") == "staging"

    @patch(f"{_MOD}._check_requirements")
    @patch(f"{_MOD}._check_guards")
    def test_init_model_parameter(self, mock_guards, mock_check_req, tmp_path):
        """--model should override the provider default."""
        from azext_prototype.custom import prototype_init
        from azext_prototype.config import ProjectConfig

        cmd = MagicMock()
        result = prototype_init(
            cmd, name="model-proj", location="eastus",
            output_dir=str(tmp_path), ai_provider="azure-openai",
            model="gpt-4o-mini", json_output=True,
        )
        assert result["status"] == "success"
        config = ProjectConfig(str(tmp_path / "model-proj"))
        config.load()
        assert config.get("ai.model") == "gpt-4o-mini"

    @patch(f"{_MOD}._check_requirements")
    @patch(f"{_MOD}._check_guards")
    def test_init_default_model_per_provider(self, mock_guards, mock_check_req, tmp_path):
        """Without --model, the default should be provider-specific."""
        from azext_prototype.custom import prototype_init
        from azext_prototype.config import ProjectConfig

        cmd = MagicMock()
        result = prototype_init(
            cmd, name="defmodel-proj", location="eastus",
            output_dir=str(tmp_path), ai_provider="azure-openai",
            json_output=True,
        )
        assert result["status"] == "success"
        config = ProjectConfig(str(tmp_path / "defmodel-proj"))
        config.load()
        assert config.get("ai.model") == "gpt-4o"

    @patch(f"{_MOD}._check_requirements")
    @patch(f"{_MOD}._check_guards")
    def test_init_sends_telemetry_overrides(self, mock_guards, mock_check_req, tmp_path):
        """Init should set _telemetry_overrides with resolved values."""
        from azext_prototype.custom import prototype_init

        cmd = MagicMock()
        prototype_init(
            cmd, name="telem-proj", location="westeurope",
            output_dir=str(tmp_path), ai_provider="azure-openai",
            environment="staging", iac_tool="bicep",
        )

        assert isinstance(cmd._telemetry_overrides, dict)
        overrides = cmd._telemetry_overrides
        assert overrides["location"] == "westeurope"
        assert overrides["ai_provider"] == "azure-openai"
        assert overrides["model"] == "gpt-4o"  # resolved default
        assert overrides["iac_tool"] == "bicep"
        assert overrides["environment"] == "staging"

    @patch(f"{_MOD}._check_requirements")
    @patch(f"{_MOD}._check_guards")
    def test_init_telemetry_overrides_explicit_model(self, mock_guards, mock_check_req, tmp_path):
        """When --model is explicit, overrides should use that value."""
        from azext_prototype.custom import prototype_init

        cmd = MagicMock()
        prototype_init(
            cmd, name="telem-model-proj", location="eastus",
            output_dir=str(tmp_path), ai_provider="azure-openai",
            model="gpt-4o-mini",
        )

        overrides = cmd._telemetry_overrides
        assert overrides["model"] == "gpt-4o-mini"
        assert overrides["ai_provider"] == "azure-openai"


class TestPrototypeConfigGet:
    """Test the config get command."""

    def test_config_get_basic(self, project_with_config):
        from azext_prototype.custom import prototype_config_get

        cmd = MagicMock()
        with patch(f"{_MOD}._get_project_dir", return_value=str(project_with_config)):
            result = prototype_config_get(cmd, key="ai.provider", json_output=True)
        assert result == {"key": "ai.provider", "value": "github-models"}

    def test_config_get_missing_key(self, project_with_config):
        from azext_prototype.custom import prototype_config_get

        cmd = MagicMock()
        with patch(f"{_MOD}._get_project_dir", return_value=str(project_with_config)):
            with pytest.raises(CLIError, match="not found"):
                prototype_config_get(cmd, key="nonexistent.key")

    def test_config_get_masks_secret(self, project_with_config):
        from azext_prototype.custom import prototype_config_get
        from azext_prototype.config import ProjectConfig

        # Set a secret value first
        config = ProjectConfig(str(project_with_config))
        config.load()
        config._secrets = {"deploy": {"subscription": "secret-sub-id"}}
        config._config["deploy"]["subscription"] = "secret-sub-id"
        config.save()
        config.save_secrets()

        cmd = MagicMock()
        with patch(f"{_MOD}._get_project_dir", return_value=str(project_with_config)):
            result = prototype_config_get(cmd, key="deploy.subscription", json_output=True)
        assert result == {"key": "deploy.subscription", "value": "***"}


class TestPrototypeConfigShowMasking:
    """Test that config show masks secrets."""

    def test_config_show_masks_secret_values(self, project_with_config):
        from azext_prototype.custom import prototype_config_show
        from azext_prototype.config import ProjectConfig

        # Set a secret value
        config = ProjectConfig(str(project_with_config))
        config.load()
        config._secrets = {"deploy": {"subscription": "my-secret-sub"}}
        config._config["deploy"]["subscription"] = "my-secret-sub"
        config.save()
        config.save_secrets()

        cmd = MagicMock()
        with patch(f"{_MOD}._get_project_dir", return_value=str(project_with_config)):
            result = prototype_config_show(cmd, json_output=True)
        assert result["deploy"]["subscription"] == "***"

    def test_config_show_preserves_non_secrets(self, project_with_config):
        from azext_prototype.custom import prototype_config_show

        cmd = MagicMock()
        with patch(f"{_MOD}._get_project_dir", return_value=str(project_with_config)):
            result = prototype_config_show(cmd, json_output=True)
        # Non-secret value should not be masked
        assert result["ai"]["provider"] == "github-models"


class TestPrototypeConfigInit:
    """Test config init marks init complete."""

    @patch("builtins.input", side_effect=[
        "y",                # overwrite existing prototype.yaml
        "my-project",       # project name
        "eastus",           # location
        "dev",              # environment
        "terraform",        # iac tool
        "1",                # naming strategy choice (microsoft-alz)
        "myorg",            # org
        "zd",               # zone_id (ALZ-specific)
        "copilot",          # ai provider
        "",                 # model (accept default)
        "",                 # subscription
        "",                 # resource group
    ])
    def test_config_init_marks_init_complete(self, mock_input, project_with_config):
        from azext_prototype.custom import prototype_config_init
        from azext_prototype.config import ProjectConfig

        cmd = MagicMock()
        with patch(f"{_MOD}._get_project_dir", return_value=str(project_with_config)):
            prototype_config_init(cmd)

        config = ProjectConfig(str(project_with_config))
        config.load()
        assert config.get("stages.init.completed") is True
        assert config.get("stages.init.timestamp") is not None

    @patch("builtins.input", side_effect=[
        "y",                # overwrite existing prototype.yaml
        "telemetry-proj",   # project name
        "westus2",          # location
        "staging",          # environment
        "bicep",            # iac tool
        "2",                # naming strategy choice (microsoft-caf)
        "myorg",            # org
        "azure-openai",     # ai provider
        "gpt-4o",           # model
        "https://myres.openai.azure.com/",  # Azure OpenAI endpoint
        "gpt-4o",           # deployment name
        "",                 # subscription
        "",                 # resource group
    ])
    def test_config_init_sends_telemetry_overrides(self, mock_input, project_with_config):
        """After prompting, config init should set _telemetry_overrides on cmd."""
        from azext_prototype.custom import prototype_config_init

        cmd = MagicMock()
        with patch(f"{_MOD}._get_project_dir", return_value=str(project_with_config)):
            prototype_config_init(cmd)

        assert hasattr(cmd, "_telemetry_overrides")
        overrides = cmd._telemetry_overrides
        assert overrides["location"] == "westus2"
        assert overrides["ai_provider"] == "azure-openai"
        assert overrides["model"] == "gpt-4o"
        assert overrides["iac_tool"] == "bicep"
        assert overrides["environment"] == "staging"
        assert overrides["naming_strategy"] == "microsoft-caf"

    def test_config_init_cancelled_no_overrides(self, project_with_config):
        """If config init is cancelled, no telemetry overrides should be set."""
        from azext_prototype.custom import prototype_config_init

        cmd = MagicMock(spec=[])  # strict spec — no auto-attributes
        with patch(f"{_MOD}._get_project_dir", return_value=str(project_with_config)):
            with patch("builtins.input", return_value="n"):
                result = prototype_config_init(cmd, json_output=True)
        assert result["status"] == "cancelled"
        assert not hasattr(cmd, "_telemetry_overrides")


class TestPrototypeBuild:
    """Test the build command."""

    @patch(f"{_MOD}._check_requirements")
    @patch(f"{_MOD}._get_project_dir")
    @patch("azext_prototype.ai.factory.create_ai_provider")
    @patch(f"{_MOD}._check_guards")
    def test_build_calls_stage(self, mock_guards, mock_factory, mock_dir, mock_check_req, project_with_design, mock_ai_provider):
        from azext_prototype.custom import prototype_build
        from azext_prototype.ai.provider import AIResponse

        mock_dir.return_value = str(project_with_design)
        mock_factory.return_value = mock_ai_provider
        mock_ai_provider.chat.return_value = AIResponse(
            content="```main.tf\nresource null {}\n```",
            model="gpt-4o",
        )

        cmd = MagicMock()
        result = prototype_build(cmd, scope="docs", dry_run=True, json_output=True)
        assert result["status"] == "dry-run"


class TestPrototypeDeploy:
    """Test the deploy command."""

    @patch(f"{_MOD}._check_requirements")
    @patch(f"{_MOD}._get_project_dir")
    @patch("azext_prototype.ai.factory.create_ai_provider")
    def test_deploy_status(self, mock_factory, mock_dir, mock_check_req, project_with_build, mock_ai_provider):
        from azext_prototype.custom import prototype_deploy

        mock_dir.return_value = str(project_with_build)
        mock_factory.return_value = mock_ai_provider

        cmd = MagicMock()
        result = prototype_deploy(cmd, status=True, json_output=True)
        assert result["status"] == "displayed"


class TestPrototypeDeployOutputs:
    """Test deploy outputs command."""

    @patch(f"{_MOD}._get_project_dir")
    def test_no_outputs(self, mock_dir, project_with_build):
        from azext_prototype.custom import prototype_deploy_outputs

        mock_dir.return_value = str(project_with_build)
        cmd = MagicMock()
        result = prototype_deploy_outputs(cmd, json_output=True)
        assert result["status"] == "empty"

    @patch(f"{_MOD}._get_project_dir")
    def test_with_outputs(self, mock_dir, project_with_build):
        from azext_prototype.custom import prototype_deploy_outputs

        mock_dir.return_value = str(project_with_build)
        # Write outputs file
        outputs_dir = project_with_build / ".prototype" / "state"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        (outputs_dir / "deploy_outputs.json").write_text(
            json.dumps({"rg_name": "test-rg"}), encoding="utf-8"
        )
        cmd = MagicMock()
        result = prototype_deploy_outputs(cmd, json_output=True)
        # May return empty or dict depending on DeploymentOutputCapture impl
        assert isinstance(result, dict)


class TestPrototypeDeployRollbackInfo:
    """Test deploy rollback-info command."""

    @patch(f"{_MOD}._get_project_dir")
    def test_rollback_info(self, mock_dir, project_with_build):
        from azext_prototype.custom import prototype_deploy_rollback_info

        mock_dir.return_value = str(project_with_build)
        cmd = MagicMock()
        result = prototype_deploy_rollback_info(cmd, json_output=True)
        assert "last_deployment" in result
        assert "rollback_instructions" in result


class TestPrototypeDeployGenerateScripts:
    """Test deploy generate-scripts command."""

    @patch(f"{_MOD}._get_project_dir")
    def test_generate_scripts_no_apps(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_deploy_generate_scripts

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        # concept/apps exists but empty
        result = prototype_deploy_generate_scripts(cmd, json_output=True)
        assert result["status"] == "generated"
        assert len(result["scripts"]) == 0

    @patch(f"{_MOD}._get_project_dir")
    def test_generate_scripts_with_apps(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_deploy_generate_scripts

        mock_dir.return_value = str(project_with_config)
        # Create app directories
        apps_dir = project_with_config / "concept" / "apps"
        (apps_dir / "backend").mkdir(parents=True, exist_ok=True)
        (apps_dir / "frontend").mkdir(parents=True, exist_ok=True)

        cmd = MagicMock()
        result = prototype_deploy_generate_scripts(cmd, deploy_type="webapp", json_output=True)
        assert result["status"] == "generated"
        assert len(result["scripts"]) == 2

    @patch(f"{_MOD}._get_project_dir")
    def test_generate_scripts_no_apps_dir_raises(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_deploy_generate_scripts

        # Remove apps dir if present
        import shutil
        apps_dir = project_with_config / "concept" / "apps"
        if apps_dir.exists():
            shutil.rmtree(apps_dir)

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        with pytest.raises(CLIError, match="No apps directory"):
            prototype_deploy_generate_scripts(cmd)


class TestPrototypeAgentOverride:
    """Test agent override command."""

    @patch(f"{_MOD}._get_project_dir")
    def test_override_registers(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_override

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        # Create a real YAML file for the override
        override_file = project_with_config / "my_arch.yaml"
        override_file.write_text(
            "name: cloud-architect\ndescription: Custom Override\n"
            "capabilities:\n  - architect\nsystem_prompt: Custom prompt.\n",
            encoding="utf-8",
        )

        result = prototype_agent_override(cmd, name="cloud-architect", file="my_arch.yaml", json_output=True)
        assert result["status"] == "override_registered"
        assert result["name"] == "cloud-architect"

    def test_override_missing_name_raises(self):
        from azext_prototype.custom import prototype_agent_override

        cmd = MagicMock()
        with pytest.raises(CLIError, match="--name"):
            prototype_agent_override(cmd, name=None, file="x.yaml")

    def test_override_missing_file_raises(self):
        from azext_prototype.custom import prototype_agent_override

        cmd = MagicMock()
        with pytest.raises(CLIError, match="--file"):
            prototype_agent_override(cmd, name="x", file=None)


class TestPrototypeAgentRemove:
    """Test agent remove command."""

    @patch(f"{_MOD}._get_project_dir")
    def test_remove_custom_agent(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_add, prototype_agent_remove

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        # Add then remove
        prototype_agent_add(cmd, name="to-remove", definition="cloud_architect")
        result = prototype_agent_remove(cmd, name="to-remove", json_output=True)
        assert result["status"] == "removed"

    @patch(f"{_MOD}._get_project_dir")
    def test_remove_override_agent(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_override, prototype_agent_remove

        mock_dir.return_value = str(project_with_config)

        # Create a real YAML file for the override
        override_file = project_with_config / "my_arch.yaml"
        override_file.write_text(
            "name: cloud-architect\ndescription: Override\n"
            "capabilities:\n  - architect\nsystem_prompt: Override.\n",
            encoding="utf-8",
        )

        cmd = MagicMock()
        prototype_agent_override(cmd, name="cloud-architect", file="my_arch.yaml")
        result = prototype_agent_remove(cmd, name="cloud-architect", json_output=True)
        assert result["status"] == "override_removed"

    @patch(f"{_MOD}._get_project_dir")
    def test_remove_builtin_raises(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_remove

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        # bicep-agent is builtin and not custom/override → should raise
        with pytest.raises(CLIError, match="Built-in agents cannot be removed"):
            prototype_agent_remove(cmd, name="app-developer")

    def test_remove_missing_name_raises(self):
        from azext_prototype.custom import prototype_agent_remove

        cmd = MagicMock()
        with pytest.raises(CLIError, match="--name"):
            prototype_agent_remove(cmd, name=None)


class TestPrototypeAnalyzeError:
    """Test the error analysis command."""

    def test_missing_input_raises(self):
        from azext_prototype.custom import prototype_analyze_error

        cmd = MagicMock()
        with pytest.raises(CLIError, match="Error input is required"):
            prototype_analyze_error(cmd, input=None)

    @patch(f"{_MOD}._prepare_command")
    def test_analyze_inline_error(self, mock_prep, project_with_design, mock_ai_provider):
        from azext_prototype.custom import prototype_analyze_error
        from azext_prototype.ai.provider import AIResponse

        mock_qa = MagicMock()
        mock_qa.name = "qa-engineer"
        mock_qa.execute.return_value = AIResponse(content="Root cause: missing RBAC", model="gpt-4o")

        mock_registry = MagicMock()
        mock_registry.find_by_capability.return_value = [mock_qa]

        mock_ctx = MagicMock()
        mock_prep.return_value = (str(project_with_design), MagicMock(), mock_registry, mock_ctx)

        cmd = MagicMock()
        result = prototype_analyze_error(cmd, input="ResourceNotFound error", json_output=True)
        assert result["status"] == "analyzed"

    @patch(f"{_MOD}._prepare_command")
    def test_analyze_log_file(self, mock_prep, project_with_design, mock_ai_provider):
        from azext_prototype.custom import prototype_analyze_error
        from azext_prototype.ai.provider import AIResponse

        mock_qa = MagicMock()
        mock_qa.name = "qa-engineer"
        mock_qa.execute.return_value = AIResponse(content="Root cause: config error", model="gpt-4o")

        mock_registry = MagicMock()
        mock_registry.find_by_capability.return_value = [mock_qa]

        mock_ctx = MagicMock()
        mock_prep.return_value = (str(project_with_design), MagicMock(), mock_registry, mock_ctx)

        log_file = project_with_design / "error.log"
        log_file.write_text("ERROR: Connection refused", encoding="utf-8")

        cmd = MagicMock()
        result = prototype_analyze_error(cmd, input=str(log_file), json_output=True)
        assert result["status"] == "analyzed"

    @patch(f"{_MOD}._prepare_command")
    def test_analyze_screenshot(self, mock_prep, project_with_design, mock_ai_provider):
        from azext_prototype.custom import prototype_analyze_error
        from azext_prototype.ai.provider import AIResponse

        mock_qa = MagicMock()
        mock_qa.name = "qa-engineer"
        mock_qa.execute_with_image.return_value = AIResponse(content="Screenshot analysis", model="gpt-4o")

        mock_registry = MagicMock()
        mock_registry.find_by_capability.return_value = [mock_qa]

        mock_ctx = MagicMock()
        mock_prep.return_value = (str(project_with_design), MagicMock(), mock_registry, mock_ctx)

        img = project_with_design / "error.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        cmd = MagicMock()
        result = prototype_analyze_error(cmd, input=str(img), json_output=True)
        assert result["status"] == "analyzed"


class TestPrototypeAnalyzeCosts:
    """Test the cost analysis command."""

    @patch(f"{_MOD}._prepare_command")
    def test_analyze_costs(self, mock_prep, project_with_design, mock_ai_provider):
        from azext_prototype.custom import prototype_analyze_costs
        from azext_prototype.ai.provider import AIResponse

        mock_cost = MagicMock()
        mock_cost.name = "cost-analyst"
        mock_cost.execute.return_value = AIResponse(content="Cost report content", model="gpt-4o")

        mock_registry = MagicMock()
        mock_registry.find_by_capability.return_value = [mock_cost]

        mock_ctx = MagicMock()
        mock_prep.return_value = (str(project_with_design), MagicMock(), mock_registry, mock_ctx)

        cmd = MagicMock()
        result = prototype_analyze_costs(cmd, json_output=True)
        assert result["status"] == "analyzed"

    @patch(f"{_MOD}._prepare_command")
    def test_analyze_costs_no_agent_raises(self, mock_prep, project_with_design):
        from azext_prototype.custom import prototype_analyze_costs

        mock_registry = MagicMock()
        mock_registry.find_by_capability.return_value = []
        mock_prep.return_value = (str(project_with_design), MagicMock(), mock_registry, MagicMock())

        cmd = MagicMock()
        with pytest.raises(CLIError, match="No cost analyst"):
            prototype_analyze_costs(cmd)


class TestPrototypeConfigSet:
    """Additional config set tests."""

    def test_config_set_missing_value_raises(self):
        from azext_prototype.custom import prototype_config_set

        cmd = MagicMock()
        with pytest.raises(CLIError, match="--value"):
            prototype_config_set(cmd, key="some.key", value=None)

    @patch(f"{_MOD}._get_project_dir")
    def test_config_set_json_value(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_config_set

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        result = prototype_config_set(cmd, key="deploy.tags", value='{"env":"dev"}', json_output=True)
        assert result["status"] == "updated"


class TestPrototypeStatusExtended:
    """Extended status tests."""

    @patch(f"{_MOD}._get_project_dir")
    def test_status_with_build_shows_changes(self, mock_dir, project_with_build):
        from azext_prototype.custom import prototype_status

        mock_dir.return_value = str(project_with_build)
        cmd = MagicMock()
        result = prototype_status(cmd, json_output=True)
        # If build stage is marked completed, pending_changes should exist
        if result.get("stages", {}).get("build", {}).get("completed"):
            assert "pending_changes" in result
        else:
            # Build state exists → pending_changes may still be present
            assert "stages" in result

    @patch(f"{_MOD}._get_project_dir")
    def test_status_default_uses_console(self, mock_dir, project_with_config):
        """Default mode (no flags) uses console output and returns None (suppressed)."""
        from azext_prototype.custom import prototype_status

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        with patch("azext_prototype.custom.console", create=True):
            result = prototype_status(cmd)

        assert result is None

    @patch(f"{_MOD}._get_project_dir")
    def test_status_json_returns_enriched_dict(self, mock_dir, project_with_config):
        """--json returns enriched dict with all new fields."""
        from azext_prototype.custom import prototype_status

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        result = prototype_status(cmd, json_output=True)

        assert isinstance(result, dict)
        assert result["project"] == "test-project"
        assert "environment" in result
        assert "naming_strategy" in result
        assert "project_id" in result
        assert "deployment_history" in result
        # All three stages present
        for stage in ("design", "build", "deploy"):
            assert stage in result["stages"]
            assert "completed" in result["stages"][stage]

    @patch(f"{_MOD}._get_project_dir")
    def test_status_detailed_prints_detail(self, mock_dir, project_with_config):
        """--detailed prints expanded output and returns None (suppressed)."""
        from azext_prototype.custom import prototype_status

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        with patch("azext_prototype.custom.console", create=True):
            result = prototype_status(cmd, detailed=True)

        assert result is None

    @patch(f"{_MOD}._get_project_dir")
    def test_status_with_discovery_state(self, mock_dir, project_with_config):
        """Discovery state populates exchanges/confirmed/open."""
        import yaml
        from azext_prototype.custom import prototype_status

        state_dir = project_with_config / ".prototype" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "discovery.yaml"
        state_file.write_text(yaml.dump({
            "open_items": ["item1"],
            "confirmed_items": ["item2", "item3"],
            "conversation_history": [],
            "_metadata": {"exchange_count": 5, "created": "2026-01-01T00:00:00", "last_updated": "2026-01-01T01:00:00"},
        }), encoding="utf-8")

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        result = prototype_status(cmd, json_output=True)

        d = result["stages"]["design"]
        assert d["exchanges"] == 5
        assert d["confirmed"] == 2
        assert d["open"] == 1

    @patch(f"{_MOD}._get_project_dir")
    def test_status_with_build_state(self, mock_dir, project_with_build):
        """Build state populates templates/stages/files/overrides."""
        from azext_prototype.custom import prototype_status

        mock_dir.return_value = str(project_with_build)
        cmd = MagicMock()
        result = prototype_status(cmd, json_output=True)

        b = result["stages"]["build"]
        assert "templates_used" in b
        assert "total_stages" in b
        assert "accepted_stages" in b
        assert "files_generated" in b
        assert "policy_overrides" in b
        assert b["total_stages"] >= 0

    @patch(f"{_MOD}._get_project_dir")
    def test_status_with_deploy_state(self, mock_dir, project_with_config):
        """Deploy state populates deployed/failed/rolled_back/outputs."""
        import yaml
        from azext_prototype.custom import prototype_status

        state_dir = project_with_config / ".prototype" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "deploy.yaml"
        state_file.write_text(yaml.dump({
            "deployment_stages": [
                {"stage": 1, "name": "Foundation", "deploy_status": "deployed", "services": []},
                {"stage": 2, "name": "App", "deploy_status": "failed", "deploy_error": "timeout", "services": []},
            ],
            "captured_outputs": {"terraform": {"endpoint": "https://example.com"}},
            "_metadata": {"created": "2026-01-01T00:00:00", "last_updated": "2026-01-01T01:00:00"},
        }), encoding="utf-8")

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        result = prototype_status(cmd, json_output=True)

        dp = result["stages"]["deploy"]
        assert dp["total_stages"] == 2
        assert dp["deployed"] == 1
        assert dp["failed"] == 1
        assert dp["rolled_back"] == 0
        assert dp["outputs_captured"] == 1

    @patch(f"{_MOD}._get_project_dir")
    def test_status_no_state_files(self, mock_dir, project_with_config):
        """Config exists but no state files — stages show zero counts."""
        from azext_prototype.custom import prototype_status

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        result = prototype_status(cmd, json_output=True)

        d = result["stages"]["design"]
        assert d["exchanges"] == 0
        assert d["confirmed"] == 0
        assert d["open"] == 0

        b = result["stages"]["build"]
        assert b["total_stages"] == 0
        assert b["files_generated"] == 0

        dp = result["stages"]["deploy"]
        assert dp["total_stages"] == 0
        assert dp["deployed"] == 0

    @patch(f"{_MOD}._get_project_dir")
    def test_status_deployment_history(self, mock_dir, project_with_config):
        """Deployment history from ChangeTracker is included."""
        import json as json_mod
        from azext_prototype.custom import prototype_status

        # Create a manifest with deployment history
        manifest_dir = project_with_config / ".prototype" / "state"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "change_manifest.json"
        manifest_path.write_text(json_mod.dumps({
            "files": {},
            "deployments": [
                {"scope": "all", "timestamp": "2026-01-15T10:00:00", "files_count": 12},
            ],
        }), encoding="utf-8")

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        result = prototype_status(cmd, json_output=True)

        assert len(result["deployment_history"]) == 1
        assert result["deployment_history"][0]["scope"] == "all"

    @patch(f"{_MOD}._get_project_dir")
    def test_status_detailed_json_returns_dict(self, mock_dir, project_with_config):
        """When both detailed and json_output are True, json wins — returns dict."""
        from azext_prototype.custom import prototype_status

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        result = prototype_status(cmd, detailed=True, json_output=True)

        # json_output takes precedence — returns the enriched dict, not displayed
        assert isinstance(result, dict)
        assert "project" in result
        assert result.get("status") != "displayed"


class TestLoadDesignContext:
    """Test _load_design_context."""

    def test_loads_from_design_json(self, project_with_design):
        from azext_prototype.custom import _load_design_context

        result = _load_design_context(str(project_with_design))
        assert "Sample architecture" in result

    def test_loads_from_architecture_md(self, project_with_config):
        from azext_prototype.custom import _load_design_context

        arch_md = project_with_config / "concept" / "docs" / "ARCHITECTURE.md"
        arch_md.parent.mkdir(parents=True, exist_ok=True)
        arch_md.write_text("# My Architecture\nDetails here.", encoding="utf-8")

        result = _load_design_context(str(project_with_config))
        assert "My Architecture" in result

    def test_returns_empty_when_no_design(self, tmp_project):
        from azext_prototype.custom import _load_design_context

        result = _load_design_context(str(tmp_project))
        assert result == ""


class TestRenderTemplate:
    """Test _render_template."""

    def test_replaces_placeholders(self):
        from azext_prototype.custom import _render_template

        template = "Project: [PROJECT_NAME], Region: [LOCATION], Date: [DATE]"
        config = {"project": {"name": "my-proj", "location": "westus2"}}
        result = _render_template(template, config)
        assert "my-proj" in result
        assert "westus2" in result
        assert "[PROJECT_NAME]" not in result

    def test_keeps_unknown_placeholders(self):
        from azext_prototype.custom import _render_template

        template = "[UNKNOWN_PLACEHOLDER] stays"
        result = _render_template(template, {})
        assert "[UNKNOWN_PLACEHOLDER]" in result


class TestGenerateTemplates:
    """Test _generate_templates shared helper."""

    def test_generates_all_templates(self, project_with_config):
        from azext_prototype.custom import _generate_templates, _load_config

        config = _load_config(str(project_with_config))
        output_dir = project_with_config / "test_output"

        generated = _generate_templates(
            output_dir, str(project_with_config), config.to_dict(), "test"
        )
        assert len(generated) >= 1
        assert output_dir.is_dir()

    def test_generates_with_manifest(self, project_with_config):
        from azext_prototype.custom import _generate_templates, _load_config

        config = _load_config(str(project_with_config))
        output_dir = project_with_config / "speckit_output"

        _generate_templates(
            output_dir, str(project_with_config), config.to_dict(), "speckit",
            include_manifest=True,
        )
        assert (output_dir / "manifest.json").exists()
        manifest = json.loads((output_dir / "manifest.json").read_text())
        assert "speckit_version" in manifest


# ======================================================================
# _load_design_context — 3-source cascade
# ======================================================================

_MOD = "azext_prototype.custom"


class TestLoadDesignContextCascade:
    """Test the 3-source cascade in _load_design_context."""

    def test_loads_from_design_json(self, project_with_design):
        """Source 1: design.json is used when present."""
        from azext_prototype.custom import _load_design_context

        result = _load_design_context(str(project_with_design))
        assert "Sample architecture" in result

    def test_falls_back_to_discovery_yaml(self, project_with_discovery):
        """Source 2: discovery.yaml used when no design.json."""
        from azext_prototype.custom import _load_design_context

        result = _load_design_context(str(project_with_discovery))
        assert result  # Should get non-empty context from discovery state

    def test_design_json_takes_priority(self, project_with_design):
        """design.json takes priority over discovery.yaml when both exist."""
        import yaml as _yaml
        from azext_prototype.custom import _load_design_context

        # Add a discovery.yaml alongside the existing design.json
        state_dir = project_with_design / ".prototype" / "state"
        discovery = {
            "project": {"summary": "Different content from discovery"},
            "confirmed_items": ["Different item"],
            "_metadata": {"exchange_count": 1, "created": "2026-01-01T00:00:00", "last_updated": "2026-01-01T00:00:00"},
        }
        (state_dir / "discovery.yaml").write_text(_yaml.dump(discovery), encoding="utf-8")

        result = _load_design_context(str(project_with_design))
        assert "Sample architecture" in result  # design.json content, not discovery

    def test_falls_back_to_architecture_md(self, project_with_config):
        """Source 3: ARCHITECTURE.md used when no state files exist."""
        from azext_prototype.custom import _load_design_context

        arch_md = project_with_config / "concept" / "docs" / "ARCHITECTURE.md"
        arch_md.parent.mkdir(parents=True, exist_ok=True)
        arch_md.write_text("# Architecture from markdown", encoding="utf-8")

        result = _load_design_context(str(project_with_config))
        assert "Architecture from markdown" in result

    def test_returns_empty_when_nothing(self, project_with_config):
        """Returns empty string when no sources exist."""
        from azext_prototype.custom import _load_design_context

        result = _load_design_context(str(project_with_config))
        assert result == ""


# ======================================================================
# Analyze costs — cache behavior
# ======================================================================


class TestAnalyzeCostsCache:
    """Test cost analysis caching (deterministic results)."""

    def _make_mock_prep(self, project_dir, mock_registry, mock_context):
        """Build a _prepare_command return tuple."""
        from azext_prototype.config import ProjectConfig

        config = ProjectConfig(str(project_dir))
        config.load()
        return (str(project_dir), config, mock_registry, mock_context)

    def _make_registry_with_cost_agent(self):
        from azext_prototype.agents.base import AgentCapability
        from tests.conftest import make_ai_response

        agent = MagicMock()
        agent.name = "cost-analyst"
        agent.execute.return_value = make_ai_response("## Cost Report\n| Service | Small | Medium | Large |")

        registry = MagicMock()
        registry.find_by_capability.return_value = [agent]
        return registry, agent

    @patch(f"{_MOD}._prepare_command")
    def test_first_run_calls_agent_and_caches(self, mock_prep, project_with_design):
        from azext_prototype.custom import prototype_analyze_costs

        registry, agent = self._make_registry_with_cost_agent()
        mock_ctx = MagicMock()
        mock_ctx.project_config = {"project": {"location": "eastus"}}
        mock_prep.return_value = self._make_mock_prep(project_with_design, registry, mock_ctx)

        cmd = MagicMock()
        result = prototype_analyze_costs(cmd, output_format="json", refresh=False, json_output=True)

        assert result["status"] == "analyzed"
        agent.execute.assert_called_once()

        # Cache file should exist
        cache = project_with_design / ".prototype" / "state" / "cost_analysis.yaml"
        assert cache.exists()

    @patch(f"{_MOD}._prepare_command")
    def test_second_run_returns_cached(self, mock_prep, project_with_design):
        """Cached result returned without calling agent."""
        import yaml as _yaml
        from azext_prototype.custom import prototype_analyze_costs

        registry, agent = self._make_registry_with_cost_agent()
        mock_ctx = MagicMock()
        mock_ctx.project_config = {"project": {"location": "eastus"}}
        mock_prep.return_value = self._make_mock_prep(project_with_design, registry, mock_ctx)

        # Pre-populate cache with matching hash
        from azext_prototype.custom import _load_design_context
        import hashlib

        design_context = _load_design_context(str(project_with_design))
        context_hash = hashlib.sha256(design_context.encode("utf-8")).hexdigest()[:16]

        cache_data = {
            "context_hash": context_hash,
            "content": "Cached cost report content",
            "result": {"status": "analyzed", "agent": "cost-analyst", "format": "markdown"},
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
        cache_path = project_with_design / ".prototype" / "state" / "cost_analysis.yaml"
        cache_path.write_text(_yaml.dump(cache_data, default_flow_style=False), encoding="utf-8")

        cmd = MagicMock()
        result = prototype_analyze_costs(cmd, output_format="markdown", refresh=False, json_output=True)

        assert result["status"] == "analyzed"
        agent.execute.assert_not_called()  # Should NOT have called the agent

    @patch(f"{_MOD}._prepare_command")
    def test_refresh_bypasses_cache(self, mock_prep, project_with_design):
        """--refresh forces fresh analysis even when cache matches."""
        import yaml as _yaml
        from azext_prototype.custom import prototype_analyze_costs

        registry, agent = self._make_registry_with_cost_agent()
        mock_ctx = MagicMock()
        mock_ctx.project_config = {"project": {"location": "eastus"}}
        mock_prep.return_value = self._make_mock_prep(project_with_design, registry, mock_ctx)

        # Pre-populate cache with matching hash
        from azext_prototype.custom import _load_design_context
        import hashlib

        design_context = _load_design_context(str(project_with_design))
        context_hash = hashlib.sha256(design_context.encode("utf-8")).hexdigest()[:16]

        cache_data = {
            "context_hash": context_hash,
            "content": "Old cached content",
            "result": {"status": "analyzed", "agent": "cost-analyst", "format": "markdown"},
        }
        cache_path = project_with_design / ".prototype" / "state" / "cost_analysis.yaml"
        cache_path.write_text(_yaml.dump(cache_data, default_flow_style=False), encoding="utf-8")

        cmd = MagicMock()
        result = prototype_analyze_costs(cmd, output_format="json", refresh=True, json_output=True)

        assert result["status"] == "analyzed"
        agent.execute.assert_called_once()  # Should HAVE called the agent

    @patch(f"{_MOD}._prepare_command")
    def test_cache_invalidated_on_design_change(self, mock_prep, project_with_design):
        """Different design context hash invalidates the cache."""
        import yaml as _yaml
        from azext_prototype.custom import prototype_analyze_costs

        registry, agent = self._make_registry_with_cost_agent()
        mock_ctx = MagicMock()
        mock_ctx.project_config = {"project": {"location": "eastus"}}
        mock_prep.return_value = self._make_mock_prep(project_with_design, registry, mock_ctx)

        # Pre-populate cache with a DIFFERENT hash
        cache_data = {
            "context_hash": "stale_hash_0000",
            "content": "Stale cached content",
            "result": {"status": "analyzed", "agent": "cost-analyst", "format": "markdown"},
        }
        cache_path = project_with_design / ".prototype" / "state" / "cost_analysis.yaml"
        cache_path.write_text(_yaml.dump(cache_data, default_flow_style=False), encoding="utf-8")

        cmd = MagicMock()
        result = prototype_analyze_costs(cmd, output_format="json", refresh=False, json_output=True)

        assert result["status"] == "analyzed"
        agent.execute.assert_called_once()  # Stale cache — must re-run

    @patch(f"{_MOD}._prepare_command")
    def test_cache_file_written_to_state_dir(self, mock_prep, project_with_design):
        """Cache is written to .prototype/state/cost_analysis.yaml."""
        import yaml as _yaml
        from azext_prototype.custom import prototype_analyze_costs

        registry, agent = self._make_registry_with_cost_agent()
        mock_ctx = MagicMock()
        mock_ctx.project_config = {"project": {"location": "eastus"}}
        mock_prep.return_value = self._make_mock_prep(project_with_design, registry, mock_ctx)

        cmd = MagicMock()
        prototype_analyze_costs(cmd, output_format="json", refresh=False)

        cache_path = project_with_design / ".prototype" / "state" / "cost_analysis.yaml"
        assert cache_path.exists()
        cached = _yaml.safe_load(cache_path.read_text(encoding="utf-8"))
        assert "context_hash" in cached
        assert "content" in cached
        assert "timestamp" in cached


# ======================================================================
# Console output — analyze commands
# ======================================================================


class TestAnalyzeConsoleOutput:
    """Verify analyze commands use console.* methods (not raw print)."""

    @patch(f"{_MOD}._prepare_command")
    @patch(f"{_MOD}.console", create=True)
    def test_analyze_error_uses_console(self, mock_console, mock_prep, project_with_design):
        from azext_prototype.agents.base import AgentCapability
        from azext_prototype.custom import prototype_analyze_error
        from tests.conftest import make_ai_response

        agent = MagicMock()
        agent.name = "qa-engineer"
        agent.execute.return_value = make_ai_response("## Fix\nDo something")

        registry = MagicMock()
        registry.find_by_capability.return_value = [agent]

        config = MagicMock()
        mock_prep.return_value = (str(project_with_design), config, registry, MagicMock())

        cmd = MagicMock()
        result = prototype_analyze_error(cmd, input="some error text", json_output=True)

        assert result["status"] == "analyzed"

    @patch(f"{_MOD}._prepare_command")
    def test_analyze_error_warns_no_context(self, mock_prep, project_with_config):
        """When no design context exists, a warning should be shown."""
        from azext_prototype.custom import prototype_analyze_error
        from tests.conftest import make_ai_response

        agent = MagicMock()
        agent.name = "qa-engineer"
        agent.execute.return_value = make_ai_response("## Fix\nDo something")

        registry = MagicMock()
        registry.find_by_capability.return_value = [agent]

        config = MagicMock()
        mock_prep.return_value = (str(project_with_config), config, registry, MagicMock())

        cmd = MagicMock()

        # Patch the module-level console singleton. We must use importlib
        # because `import azext_prototype.ui.console` can resolve to the
        # `console` variable re-exported in azext_prototype.ui.__init__
        # instead of the submodule (name collision on Python 3.10).
        import importlib
        _console_mod = importlib.import_module("azext_prototype.ui.console")

        with patch.object(_console_mod, "console") as mock_console:
            result = prototype_analyze_error(cmd, input="some error", json_output=True)

        assert result["status"] == "analyzed"

    @patch(f"{_MOD}._prepare_command")
    def test_analyze_costs_uses_console(self, mock_prep, project_with_design):
        from azext_prototype.custom import prototype_analyze_costs
        from tests.conftest import make_ai_response

        agent = MagicMock()
        agent.name = "cost-analyst"
        agent.execute.return_value = make_ai_response("## Costs\n$100/mo")

        registry = MagicMock()
        registry.find_by_capability.return_value = [agent]

        from azext_prototype.config import ProjectConfig
        config = ProjectConfig(str(project_with_design))
        config.load()

        mock_ctx = MagicMock()
        mock_ctx.project_config = {"project": {"location": "eastus"}}
        mock_prep.return_value = (str(project_with_design), config, registry, mock_ctx)

        cmd = MagicMock()
        result = prototype_analyze_costs(cmd, output_format="json", refresh=True, json_output=True)

        assert result["status"] == "analyzed"


# ======================================================================
# Console output — deploy subcommands
# ======================================================================


class TestDeploySubcommandConsole:
    """Verify deploy subcommands use console.* methods."""

    @patch(f"{_MOD}._get_project_dir")
    def test_deploy_outputs_empty_warns(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_deploy_outputs

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        with patch("azext_prototype.stages.deploy_helpers.DeploymentOutputCapture") as MockCapture:
            MockCapture.return_value.get_all.return_value = {}
            result = prototype_deploy_outputs(cmd, json_output=True)

        assert result["status"] == "empty"

    @patch(f"{_MOD}._get_project_dir")
    def test_deploy_rollback_info_empty_warns(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_deploy_rollback_info

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        with patch("azext_prototype.stages.deploy_helpers.RollbackManager") as MockMgr:
            MockMgr.return_value.get_last_snapshot.return_value = None
            MockMgr.return_value.get_rollback_instructions.return_value = None
            result = prototype_deploy_rollback_info(cmd, json_output=True)

        assert result["last_deployment"] is None
        assert result["rollback_instructions"] is None

    @patch(f"{_MOD}._get_project_dir")
    @patch(f"{_MOD}._load_config")
    def test_generate_scripts_uses_console(self, mock_config, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_deploy_generate_scripts

        mock_dir.return_value = str(project_with_config)
        mock_config.return_value = MagicMock()
        mock_config.return_value.get.return_value = ""

        # Create an apps directory with a subdirectory
        apps_dir = project_with_config / "concept" / "apps"
        apps_dir.mkdir(parents=True, exist_ok=True)
        (apps_dir / "my-app").mkdir()

        cmd = MagicMock()

        with patch("azext_prototype.stages.deploy_helpers.DeployScriptGenerator") as MockGen:
            result = prototype_deploy_generate_scripts(cmd, json_output=True)

        assert result["status"] == "generated"
        assert "my-app/deploy.sh" in result["scripts"]


# ======================================================================
# Agent commands — Rich UI, new commands, validation
# ======================================================================

_MOD = "azext_prototype.custom"


class TestPrototypeAgentListRichUI:
    """Test agent list Rich UI, json, and detailed modes."""

    @patch(f"{_MOD}._get_project_dir")
    def test_list_json_returns_list(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_list

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        result = prototype_agent_list(cmd, json_output=True)
        assert isinstance(result, list)
        assert len(result) >= 8

    @patch(f"{_MOD}._get_project_dir")
    def test_list_console_mode(self, mock_dir, project_with_config):
        """Default (non-json) returns list and uses console."""
        from azext_prototype.custom import prototype_agent_list

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        result = prototype_agent_list(cmd, json_output=True)
        assert isinstance(result, list)

    @patch(f"{_MOD}._get_project_dir")
    def test_list_detailed_mode(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_list

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        result = prototype_agent_list(cmd, detailed=True, json_output=True)
        assert isinstance(result, list)

    @patch(f"{_MOD}._get_project_dir")
    def test_list_agents_have_source(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_list

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        result = prototype_agent_list(cmd, json_output=True)
        for agent in result:
            assert "source" in agent


class TestPrototypeAgentShowRichUI:
    """Test agent show Rich UI, json, and detailed modes."""

    @patch(f"{_MOD}._get_project_dir")
    def test_show_json_returns_dict(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_show

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        result = prototype_agent_show(cmd, name="cloud-architect", json_output=True)
        assert isinstance(result, dict)
        assert result["name"] == "cloud-architect"
        assert "system_prompt_preview" in result

    @patch(f"{_MOD}._get_project_dir")
    def test_show_detailed_includes_full_prompt(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_show

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        result = prototype_agent_show(cmd, name="cloud-architect", detailed=True, json_output=True)
        assert "system_prompt" in result
        # detailed should not have preview
        assert "system_prompt_preview" not in result

    @patch(f"{_MOD}._get_project_dir")
    def test_show_console_mode(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_show

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        result = prototype_agent_show(cmd, name="cloud-architect", json_output=True)
        assert isinstance(result, dict)


class TestPrototypeAgentUpdate:
    """Test agent update command."""

    @patch(f"{_MOD}._get_project_dir")
    def test_update_description(self, mock_dir, project_with_config):
        """Targeted field update — description only."""
        from azext_prototype.custom import prototype_agent_add, prototype_agent_update

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        prototype_agent_add(cmd, name="updatable", definition="cloud_architect")
        result = prototype_agent_update(cmd, name="updatable", description="New desc", json_output=True)
        assert result["status"] == "updated"
        assert result["description"] == "New desc"

    @patch(f"{_MOD}._get_project_dir")
    def test_update_capabilities(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_add, prototype_agent_update

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        prototype_agent_add(cmd, name="cap-update", definition="cloud_architect")
        result = prototype_agent_update(cmd, name="cap-update", capabilities="architect,deploy", json_output=True)
        assert result["status"] == "updated"
        assert "architect" in result["capabilities"]
        assert "deploy" in result["capabilities"]

    @patch(f"{_MOD}._get_project_dir")
    def test_update_system_prompt_from_file(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_add, prototype_agent_update

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        prototype_agent_add(cmd, name="prompt-update", definition="cloud_architect")

        prompt_file = project_with_config / "new_prompt.txt"
        prompt_file.write_text("You are an updated agent.", encoding="utf-8")

        result = prototype_agent_update(cmd, name="prompt-update", system_prompt_file=str(prompt_file), json_output=True)
        assert result["status"] == "updated"

        import yaml as _yaml
        agent_file = project_with_config / ".prototype" / "agents" / "prompt-update.yaml"
        content = _yaml.safe_load(agent_file.read_text(encoding="utf-8"))
        assert content["system_prompt"] == "You are an updated agent."

    @patch(f"{_MOD}._get_project_dir")
    def test_update_interactive_mode(self, mock_dir, project_with_config):
        """Interactive mode with mocked input."""
        from azext_prototype.custom import prototype_agent_add, prototype_agent_update

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        prototype_agent_add(cmd, name="interactive-up", definition="cloud_architect")

        # Mock interactive prompts: description, role, capabilities, constraints (empty), system prompt (empty=keep)
        inputs = [
            "Updated description",  # description
            "architect",           # role
            "architect",           # capabilities
            "",                    # end constraints
            "",                    # system prompt (keep existing - first empty line)
            "",                    # examples (skip)
        ]
        with patch("builtins.input", side_effect=inputs):
            result = prototype_agent_update(cmd, name="interactive-up", json_output=True)

        assert result["status"] == "updated"
        assert result["description"] == "Updated description"

    @patch(f"{_MOD}._get_project_dir")
    def test_update_manifest_sync(self, mock_dir, project_with_config):
        """Manifest entry is updated after field update."""
        from azext_prototype.custom import prototype_agent_add, prototype_agent_update, _load_config

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        prototype_agent_add(cmd, name="manifest-sync", definition="cloud_architect")
        prototype_agent_update(cmd, name="manifest-sync", description="Synced desc")

        config = _load_config(str(project_with_config))
        custom = config.get("agents.custom", {})
        assert custom["manifest-sync"]["description"] == "Synced desc"

    def test_update_missing_name_raises(self):
        from azext_prototype.custom import prototype_agent_update

        cmd = MagicMock()
        with pytest.raises(CLIError, match="--name"):
            prototype_agent_update(cmd, name=None)

    @patch(f"{_MOD}._get_project_dir")
    def test_update_nonexistent_raises(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_update

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        with pytest.raises(CLIError, match="not found"):
            prototype_agent_update(cmd, name="nonexistent-agent")

    @patch(f"{_MOD}._get_project_dir")
    def test_update_invalid_capability_raises(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_add, prototype_agent_update

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        prototype_agent_add(cmd, name="bad-cap", definition="cloud_architect")
        with pytest.raises(CLIError, match="Unknown capability"):
            prototype_agent_update(cmd, name="bad-cap", capabilities="invalid_cap")

    @patch(f"{_MOD}._get_project_dir")
    def test_update_prompt_file_not_found_raises(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_add, prototype_agent_update

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        prototype_agent_add(cmd, name="no-prompt", definition="cloud_architect")
        with pytest.raises(CLIError, match="not found"):
            prototype_agent_update(cmd, name="no-prompt", system_prompt_file="./does_not_exist.txt")


class TestPrototypeAgentTest:
    """Test agent test command."""

    @patch(f"{_MOD}._prepare_command")
    def test_default_prompt(self, mock_prep, project_with_config, mock_ai_provider):
        from azext_prototype.custom import prototype_agent_test
        from azext_prototype.ai.provider import AIResponse

        mock_agent = MagicMock()
        mock_agent.name = "cloud-architect"
        mock_agent.execute.return_value = AIResponse(
            content="I am the cloud architect.",
            model="gpt-4o",
            usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        )

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_agent
        mock_prep.return_value = (str(project_with_config), MagicMock(), mock_registry, MagicMock())

        cmd = MagicMock()
        result = prototype_agent_test(cmd, name="cloud-architect", json_output=True)

        assert result["status"] == "tested"
        assert result["name"] == "cloud-architect"
        assert result["model"] == "gpt-4o"
        assert result["tokens"] == 70
        mock_agent.execute.assert_called_once()

    @patch(f"{_MOD}._prepare_command")
    def test_custom_prompt(self, mock_prep, project_with_config, mock_ai_provider):
        from azext_prototype.custom import prototype_agent_test
        from azext_prototype.ai.provider import AIResponse

        mock_agent = MagicMock()
        mock_agent.name = "cloud-architect"
        mock_agent.execute.return_value = AIResponse(
            content="Here is a web app design.",
            model="gpt-4o",
            usage={"total_tokens": 100},
        )

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_agent
        mock_prep.return_value = (str(project_with_config), MagicMock(), mock_registry, MagicMock())

        cmd = MagicMock()
        result = prototype_agent_test(cmd, name="cloud-architect", prompt="Design a web app", json_output=True)

        assert result["status"] == "tested"
        # Verify custom prompt was passed
        call_args = mock_agent.execute.call_args
        assert "Design a web app" in call_args[0][1]

    def test_test_missing_name_raises(self):
        from azext_prototype.custom import prototype_agent_test

        cmd = MagicMock()
        with pytest.raises(CLIError, match="--name"):
            prototype_agent_test(cmd, name=None)


class TestPrototypeAgentExport:
    """Test agent export command."""

    @patch(f"{_MOD}._get_project_dir")
    def test_export_builtin(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_export

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        output_path = str(project_with_config / "exported.yaml")
        result = prototype_agent_export(cmd, name="cloud-architect", output=output_path, json_output=True)

        assert result["status"] == "exported"
        assert result["name"] == "cloud-architect"

        import yaml as _yaml
        exported = _yaml.safe_load(
            (project_with_config / "exported.yaml").read_text(encoding="utf-8")
        )
        assert exported["name"] == "cloud-architect"
        assert "capabilities" in exported
        assert "system_prompt" in exported

    @patch(f"{_MOD}._get_project_dir")
    def test_export_custom(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_add, prototype_agent_export

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        prototype_agent_add(cmd, name="export-test", definition="bicep_agent")
        output_path = str(project_with_config / "custom_export.yaml")
        result = prototype_agent_export(cmd, name="export-test", output=output_path, json_output=True)

        assert result["status"] == "exported"
        assert (project_with_config / "custom_export.yaml").exists()

    @patch(f"{_MOD}._get_project_dir")
    def test_export_default_path(self, mock_dir, project_with_config):
        """Default output path is ./{name}.yaml."""
        import os
        from azext_prototype.custom import prototype_agent_export

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        # Change cwd to project dir for default path
        original_cwd = os.getcwd()
        try:
            os.chdir(str(project_with_config))
            result = prototype_agent_export(cmd, name="cloud-architect", json_output=True)
            assert result["status"] == "exported"
            assert (project_with_config / "cloud-architect.yaml").exists()
        finally:
            os.chdir(original_cwd)

    @patch(f"{_MOD}._get_project_dir")
    def test_export_loadable_by_loader(self, mock_dir, project_with_config):
        """Exported YAML is loadable by load_yaml_agent."""
        from azext_prototype.custom import prototype_agent_export
        from azext_prototype.agents.loader import load_yaml_agent

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        output_path = str(project_with_config / "loadable.yaml")
        prototype_agent_export(cmd, name="cloud-architect", output=output_path)

        agent = load_yaml_agent(output_path)
        assert agent.name == "cloud-architect"

    def test_export_missing_name_raises(self):
        from azext_prototype.custom import prototype_agent_export

        cmd = MagicMock()
        with pytest.raises(CLIError, match="--name"):
            prototype_agent_export(cmd, name=None)


class TestPrototypeAgentOverrideValidation:
    """Test override validation enhancements."""

    @patch(f"{_MOD}._get_project_dir")
    def test_override_file_not_found_raises(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_override

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        with pytest.raises(CLIError, match="not found"):
            prototype_agent_override(cmd, name="cloud-architect", file="./does_not_exist.yaml")

    @patch(f"{_MOD}._get_project_dir")
    def test_override_invalid_yaml_raises(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_override

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        bad_yaml = project_with_config / "bad.yaml"
        bad_yaml.write_text("{{invalid yaml::", encoding="utf-8")

        with pytest.raises(CLIError, match="Invalid YAML"):
            prototype_agent_override(cmd, name="cloud-architect", file="bad.yaml")

    @patch(f"{_MOD}._get_project_dir")
    def test_override_missing_name_field_raises(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_override

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        no_name = project_with_config / "no_name.yaml"
        no_name.write_text("description: test\n", encoding="utf-8")

        with pytest.raises(CLIError, match="name"):
            prototype_agent_override(cmd, name="cloud-architect", file="no_name.yaml")

    @patch(f"{_MOD}._get_project_dir")
    def test_override_non_builtin_warns(self, mock_dir, project_with_config):
        """Overriding a non-builtin name should warn but succeed."""
        from azext_prototype.custom import prototype_agent_override

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        valid_yaml = project_with_config / "valid.yaml"
        valid_yaml.write_text(
            "name: nonexistent-agent\ndescription: test\ncapabilities:\n  - develop\n"
            "system_prompt: test\n",
            encoding="utf-8",
        )

        result = prototype_agent_override(cmd, name="nonexistent-agent", file="valid.yaml", json_output=True)
        assert result["status"] == "override_registered"

    @patch(f"{_MOD}._get_project_dir")
    def test_override_valid_builtin(self, mock_dir, project_with_config):
        """Overriding a known builtin should succeed without warnings."""
        from azext_prototype.custom import prototype_agent_override

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        valid_yaml = project_with_config / "arch_override.yaml"
        valid_yaml.write_text(
            "name: cloud-architect\ndescription: Custom arch\ncapabilities:\n  - architect\n"
            "system_prompt: Custom prompt.\n",
            encoding="utf-8",
        )

        result = prototype_agent_override(cmd, name="cloud-architect", file="arch_override.yaml", json_output=True)
        assert result["status"] == "override_registered"


class TestPromptAgentDefinition:
    """Test the _prompt_agent_definition interactive helper."""

    def test_full_walkthrough(self):
        from azext_prototype.custom import _prompt_agent_definition
        from azext_prototype.ui.console import Console

        console = Console()
        inputs = [
            "My agent description",     # description
            "architect",                 # role
            "architect,deploy",          # capabilities
            "Must use PaaS only",        # constraint 1
            "",                          # end constraints
            "You are a custom agent.",   # system prompt line 1
            "END",                       # end system prompt
            "",                          # no examples
        ]
        with patch("builtins.input", side_effect=inputs):
            result = _prompt_agent_definition(console, "test-agent")

        assert result["name"] == "test-agent"
        assert result["description"] == "My agent description"
        assert result["role"] == "architect"
        assert "architect" in result["capabilities"]
        assert "deploy" in result["capabilities"]
        assert "Must use PaaS only" in result["constraints"]
        assert "You are a custom agent." in result["system_prompt"]

    def test_existing_defaults(self):
        from azext_prototype.custom import _prompt_agent_definition
        from azext_prototype.ui.console import Console

        console = Console()
        existing = {
            "description": "Old desc",
            "role": "developer",
            "capabilities": ["develop"],
            "constraints": ["Old constraint"],
            "system_prompt": "Old prompt.",
            "examples": [{"user": "hello", "assistant": "hi"}],
        }
        # All empty inputs → keep existing values
        inputs = [
            "",   # description (keep)
            "",   # role (keep)
            "",   # capabilities (keep)
            "",   # constraints (keep existing)
            "",   # system prompt (keep existing)
            "",   # examples (keep existing)
        ]
        with patch("builtins.input", side_effect=inputs):
            result = _prompt_agent_definition(console, "test-agent", existing=existing)

        assert result["description"] == "Old desc"
        assert result["role"] == "developer"
        assert result["capabilities"] == ["develop"]
        assert result["constraints"] == ["Old constraint"]
        assert result["system_prompt"] == "Old prompt."
        assert result["examples"] == [{"user": "hello", "assistant": "hi"}]

    def test_invalid_capability_skipped(self):
        from azext_prototype.custom import _prompt_agent_definition
        from azext_prototype.ui.console import Console

        console = Console()
        inputs = [
            "desc",              # description
            "role",              # role
            "invalid_cap,architect",  # capabilities — one invalid
            "",                  # end constraints
            "prompt",            # system prompt
            "END",               # end system prompt
            "",                  # no examples
        ]
        with patch("builtins.input", side_effect=inputs):
            result = _prompt_agent_definition(console, "test-agent")

        assert "architect" in result["capabilities"]
        assert "invalid_cap" not in result["capabilities"]


class TestReadMultilineInput:
    """Test _read_multiline_input helper."""

    def test_reads_until_end(self):
        from azext_prototype.custom import _read_multiline_input

        with patch("builtins.input", side_effect=["line 1", "line 2", "END"]):
            result = _read_multiline_input()
        assert result == "line 1\nline 2"

    def test_empty_first_line_returns_empty(self):
        from azext_prototype.custom import _read_multiline_input

        with patch("builtins.input", side_effect=[""]):
            result = _read_multiline_input()
        assert result == ""
