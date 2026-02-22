"""Targeted tests for remaining coverage gaps in deploy_stage.py and custom.py."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from knack.util import CLIError

_MOD = "azext_prototype.custom"


# ======================================================================
# DeployStage — deep coverage
# ======================================================================


class TestDeployHelpersDeep:
    """Deep tests for deploy_helpers module-level functions."""

    # --- Bicep helpers ---

    def test_find_bicep_params_json(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import find_bicep_params

        (tmp_path / "main.parameters.json").write_text("{}", encoding="utf-8")
        result = find_bicep_params(tmp_path, tmp_path / "main.bicep")
        assert result is not None
        assert result.name == "main.parameters.json"

    def test_find_bicep_params_bicepparam(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import find_bicep_params

        (tmp_path / "main.bicepparam").write_text("", encoding="utf-8")
        result = find_bicep_params(tmp_path, tmp_path / "main.bicep")
        assert result is not None
        assert result.name == "main.bicepparam"

    def test_find_bicep_params_generic(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import find_bicep_params

        (tmp_path / "parameters.json").write_text("{}", encoding="utf-8")
        result = find_bicep_params(tmp_path, tmp_path / "main.bicep")
        assert result is not None
        assert result.name == "parameters.json"

    def test_find_bicep_params_none(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import find_bicep_params

        result = find_bicep_params(tmp_path, tmp_path / "main.bicep")
        assert result is None

    def test_is_subscription_scoped_true(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import is_subscription_scoped

        bicep = tmp_path / "main.bicep"
        bicep.write_text("targetScope = 'subscription'\n", encoding="utf-8")
        assert is_subscription_scoped(bicep) is True

    def test_is_subscription_scoped_false(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import is_subscription_scoped

        bicep = tmp_path / "main.bicep"
        bicep.write_text("resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {}\n", encoding="utf-8")
        assert is_subscription_scoped(bicep) is False

    def test_is_subscription_scoped_missing_file(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import is_subscription_scoped

        assert is_subscription_scoped(tmp_path / "nope.bicep") is False

    def test_get_deploy_location_from_params(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import get_deploy_location

        params = {"parameters": {"location": {"value": "westus2"}}}
        (tmp_path / "parameters.json").write_text(json.dumps(params), encoding="utf-8")
        assert get_deploy_location(tmp_path) == "westus2"

    def test_get_deploy_location_from_string(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import get_deploy_location

        params = {"location": "centralus"}
        (tmp_path / "parameters.json").write_text(json.dumps(params), encoding="utf-8")
        assert get_deploy_location(tmp_path) == "centralus"

    def test_get_deploy_location_none(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import get_deploy_location

        assert get_deploy_location(tmp_path) is None

    def test_get_deploy_location_invalid_json(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import get_deploy_location

        (tmp_path / "parameters.json").write_text("not json", encoding="utf-8")
        assert get_deploy_location(tmp_path) is None

    # --- check_az_login ---

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run")
    def test_check_az_login_true(self, mock_run):
        from azext_prototype.stages.deploy_helpers import check_az_login

        mock_run.return_value = MagicMock(returncode=0)
        assert check_az_login() is True

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run")
    def test_check_az_login_false(self, mock_run):
        from azext_prototype.stages.deploy_helpers import check_az_login

        mock_run.return_value = MagicMock(returncode=1)
        assert check_az_login() is False

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run", side_effect=FileNotFoundError)
    def test_check_az_login_no_az(self, mock_run):
        from azext_prototype.stages.deploy_helpers import check_az_login

        assert check_az_login() is False

    # --- get_current_subscription ---

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run")
    def test_get_current_subscription(self, mock_run):
        from azext_prototype.stages.deploy_helpers import get_current_subscription

        mock_run.return_value = MagicMock(returncode=0, stdout="sub-abc-123\n")
        assert get_current_subscription() == "sub-abc-123"

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run", side_effect=FileNotFoundError)
    def test_get_current_subscription_error(self, mock_run):
        from azext_prototype.stages.deploy_helpers import get_current_subscription

        assert get_current_subscription() == ""

    # --- deploy_terraform ---

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run")
    def test_deploy_terraform_success(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_terraform

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = deploy_terraform(tmp_path, "sub-123")
        assert result["status"] == "deployed"
        assert result["tool"] == "terraform"

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run")
    def test_deploy_terraform_failure(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_terraform

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),  # init
            MagicMock(returncode=1, stdout="", stderr="init failed"),  # plan
        ]
        result = deploy_terraform(tmp_path, "sub-123")
        assert result["status"] == "failed"
        assert "init failed" in result["error"]

    # --- deploy_bicep ---

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run")
    def test_deploy_bicep_success(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_bicep

        (tmp_path / "main.bicep").write_text("resource rg {}", encoding="utf-8")
        mock_run.return_value = MagicMock(returncode=0, stdout='{"outputs":{}}', stderr="")

        result = deploy_bicep(tmp_path, "sub-123", "my-rg")
        assert result["status"] == "deployed"
        assert result["scope"] == "resourceGroup"

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run")
    def test_deploy_bicep_subscription_scope(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_bicep

        (tmp_path / "main.bicep").write_text("targetScope = 'subscription'\n", encoding="utf-8")
        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")

        result = deploy_bicep(tmp_path, "sub-123", "")
        assert result["status"] == "deployed"
        assert result["scope"] == "subscription"

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run")
    def test_deploy_bicep_failure(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_bicep

        (tmp_path / "main.bicep").write_text("resource rg {}", encoding="utf-8")
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="deployment error")

        result = deploy_bicep(tmp_path, "sub-123", "my-rg")
        assert result["status"] == "failed"

    def test_deploy_bicep_no_files(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_bicep

        result = deploy_bicep(tmp_path, "sub-123", "rg")
        assert result["status"] == "skipped"

    def test_deploy_bicep_no_rg_for_rg_scope(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_bicep

        (tmp_path / "main.bicep").write_text("resource rg {}", encoding="utf-8")
        result = deploy_bicep(tmp_path, "sub-123", "")
        assert result["status"] == "failed"
        assert "Resource group required" in result["error"]

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run")
    def test_deploy_bicep_fallback_file(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_bicep

        # No main.bicep, but another.bicep exists
        (tmp_path / "network.bicep").write_text("resource vnet {}", encoding="utf-8")
        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")

        result = deploy_bicep(tmp_path, "sub-123", "rg")
        assert result["status"] == "deployed"
        assert result["template"] == "network.bicep"

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run")
    def test_deploy_bicep_with_params(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_bicep

        (tmp_path / "main.bicep").write_text("resource x {}", encoding="utf-8")
        (tmp_path / "main.parameters.json").write_text("{}", encoding="utf-8")
        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")

        result = deploy_bicep(tmp_path, "sub-123", "rg")
        assert result["status"] == "deployed"
        # Verify parameters were passed
        call_args = mock_run.call_args[0][0]
        assert "--parameters" in call_args

    # --- whatif_bicep ---

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run")
    def test_whatif_bicep_success(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import whatif_bicep

        (tmp_path / "main.bicep").write_text("resource x {}", encoding="utf-8")
        mock_run.return_value = MagicMock(returncode=0, stdout="Changes:\n  +Create", stderr="")

        result = whatif_bicep(tmp_path, "sub-123", "rg")
        assert result["status"] == "previewed"
        assert "Changes" in result["output"]

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run")
    def test_whatif_bicep_error(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import whatif_bicep

        (tmp_path / "main.bicep").write_text("resource x {}", encoding="utf-8")
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="auth error")

        result = whatif_bicep(tmp_path, "sub-123", "rg")
        assert result["error"] == "auth error"

    def test_whatif_bicep_no_files(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import whatif_bicep

        result = whatif_bicep(tmp_path, "sub-123", "rg")
        assert result["status"] == "skipped"

    def test_whatif_bicep_no_rg(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import whatif_bicep

        (tmp_path / "main.bicep").write_text("resource x {}", encoding="utf-8")
        result = whatif_bicep(tmp_path, "sub-123", "")
        assert result["status"] == "skipped"
        assert "Resource group" in result["reason"]

    # --- deploy_app_stage ---

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run")
    def test_deploy_app_stage_with_deploy_script(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_app_stage

        (tmp_path / "deploy.sh").write_text("#!/bin/bash\necho ok", encoding="utf-8")
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        result = deploy_app_stage(tmp_path, "sub", "rg")
        assert result["status"] == "deployed"
        assert result["method"] == "deploy_script"

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run")
    def test_deploy_app_stage_script_failure(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_app_stage

        (tmp_path / "deploy.sh").write_text("#!/bin/bash\nexit 1", encoding="utf-8")
        mock_run.return_value = MagicMock(returncode=1, stderr="script error", stdout="")

        result = deploy_app_stage(tmp_path, "sub", "rg")
        assert result["status"] == "failed"

    @patch("azext_prototype.stages.deploy_helpers.subprocess.run")
    def test_deploy_app_stage_sub_apps(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_app_stage

        api = tmp_path / "api"
        api.mkdir()
        (api / "deploy.sh").write_text("#!/bin/bash\necho api", encoding="utf-8")
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        result = deploy_app_stage(tmp_path, "sub", "rg")
        assert result["status"] == "deployed"
        assert "api" in result["apps"]

    def test_deploy_app_stage_no_scripts(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_app_stage

        result = deploy_app_stage(tmp_path, "sub", "rg")
        assert result["status"] == "skipped"


# ======================================================================
# Custom.py — deeper coverage
# ======================================================================


class TestPrototypeDesign:
    """Test the design command."""

    @patch(f"{_MOD}._prepare_command")
    @patch(f"{_MOD}._check_guards")
    def test_design_interactive(self, mock_guards, mock_prep, project_with_config, mock_ai_provider):
        from azext_prototype.custom import prototype_design
        from azext_prototype.ai.provider import AIResponse

        mock_ai_provider.chat.return_value = AIResponse(content="Architecture plan", model="gpt-4o")

        mock_registry = MagicMock()
        architect = MagicMock()
        architect.name = "cloud-architect"
        architect.execute.return_value = AIResponse(content="# Architecture\nDesign", model="gpt-4o")
        mock_registry.find_by_capability.return_value = [architect]

        mock_ctx = MagicMock()
        mock_ctx.project_dir = str(project_with_config)
        mock_prep.return_value = (str(project_with_config), MagicMock(), mock_registry, mock_ctx)

        cmd = MagicMock()
        # Discovery now happens inside DesignStage.execute — mock it
        with patch("azext_prototype.stages.design_stage.DiscoverySession") as MockDS:
            from azext_prototype.stages.discovery import DiscoveryResult
            MockDS.return_value.run.return_value = DiscoveryResult(
                requirements="User wants an API",
                conversation=[],
                policy_overrides=[],
                exchange_count=2,
            )
            result = prototype_design(cmd, json_output=True)
        # Design stage calls execute which returns a dict
        assert isinstance(result, dict)

    @patch(f"{_MOD}._prepare_command")
    @patch(f"{_MOD}._check_guards")
    def test_design_with_context(self, mock_guards, mock_prep, project_with_config, mock_ai_provider):
        from azext_prototype.custom import prototype_design
        from azext_prototype.ai.provider import AIResponse

        mock_registry = MagicMock()
        architect = MagicMock()
        architect.name = "cloud-architect"
        architect.execute.return_value = AIResponse(content="# Architecture", model="gpt-4o")
        mock_registry.find_by_capability.return_value = [architect]

        mock_ctx = MagicMock()
        mock_ctx.project_dir = str(project_with_config)
        mock_prep.return_value = (str(project_with_config), MagicMock(), mock_registry, mock_ctx)

        cmd = MagicMock()
        with patch("azext_prototype.stages.design_stage.DiscoverySession") as MockDS:
            from azext_prototype.stages.discovery import DiscoveryResult
            MockDS.return_value.run.return_value = DiscoveryResult(
                requirements="Build an API with Cosmos DB",
                conversation=[],
                policy_overrides=[],
                exchange_count=2,
            )
            result = prototype_design(cmd, context="Build an API with Cosmos DB", json_output=True)
        assert isinstance(result, dict)


class TestPrototypeGenerateDocs:
    """Test generate docs and speckit commands."""

    @patch(f"{_MOD}._get_project_dir")
    def test_generate_docs(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_generate_docs

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        result = prototype_generate_docs(cmd, json_output=True)
        assert result["status"] == "generated"
        assert len(result["documents"]) >= 1
        docs_dir = project_with_config / "docs"
        assert docs_dir.is_dir()

    @patch(f"{_MOD}._get_project_dir")
    def test_generate_docs_custom_path(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_generate_docs

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        custom_dir = project_with_config / "custom_docs"
        result = prototype_generate_docs(cmd, path=str(custom_dir), json_output=True)
        assert result["output_dir"] == str(custom_dir)
        assert custom_dir.is_dir()

    @patch(f"{_MOD}._get_project_dir")
    def test_generate_speckit(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_generate_speckit

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        result = prototype_generate_speckit(cmd, json_output=True)
        assert result["status"] == "generated"
        # Speckit should include manifest
        speckit_dir = project_with_config / "concept" / ".specify"
        assert (speckit_dir / "manifest.json").exists()


class TestPrototypeAgentList:
    """Test agent list command."""

    @patch(f"{_MOD}._get_project_dir")
    def test_agent_list(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_list

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        result = prototype_agent_list(cmd, json_output=True)
        assert len(result) >= 8

    @patch(f"{_MOD}._get_project_dir")
    def test_agent_list_no_builtin(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_list

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        result = prototype_agent_list(cmd, show_builtin=False, json_output=True)
        # Should filter out built-in agents
        assert isinstance(result, list)


class TestPrototypeAgentShow:
    """Test agent show command."""

    @patch(f"{_MOD}._get_project_dir")
    def test_agent_show_existing(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_show

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        result = prototype_agent_show(cmd, name="cloud-architect", json_output=True)
        assert result["name"] == "cloud-architect"

    @patch(f"{_MOD}._get_project_dir")
    def test_agent_show_not_found(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_show

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        with pytest.raises(CLIError, match="not found"):
            prototype_agent_show(cmd, name="nonexistent-agent")

    def test_agent_show_missing_name_raises(self):
        from azext_prototype.custom import prototype_agent_show

        cmd = MagicMock()
        with pytest.raises(CLIError, match="--name"):
            prototype_agent_show(cmd, name=None)


class TestPrototypeAgentAddExtended:
    """Extended tests for agent add modes."""

    @patch(f"{_MOD}._get_project_dir")
    def test_agent_add_default_template(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_add

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        # Interactive mode: provide input for all prompts
        inputs = ["My agent", "general", "develop", "", "You are a test agent.", "END", ""]
        with patch("builtins.input", side_effect=inputs):
            result = prototype_agent_add(cmd, name="my-agent", json_output=True)
        assert result["status"] == "added"
        assert result["name"] == "my-agent"

    @patch(f"{_MOD}._get_project_dir")
    def test_agent_add_from_definition(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_add

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        result = prototype_agent_add(cmd, name="custom-arch", definition="example_custom_agent", json_output=True)
        assert result["status"] == "added"

    @patch(f"{_MOD}._get_project_dir")
    def test_agent_add_duplicate_raises(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_add

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        prototype_agent_add(cmd, name="dupe-agent", definition="cloud_architect")
        with pytest.raises(CLIError, match="already exists"):
            prototype_agent_add(cmd, name="dupe-agent", definition="cloud_architect")

    def test_agent_add_file_and_definition_raises(self):
        from azext_prototype.custom import prototype_agent_add

        cmd = MagicMock()
        with pytest.raises(CLIError, match="mutually exclusive"):
            prototype_agent_add(cmd, name="x", file="x.yaml", definition="bicep_agent")

    @patch(f"{_MOD}._get_project_dir")
    def test_agent_add_missing_file_raises(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_add

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()
        with pytest.raises(CLIError, match="not found"):
            prototype_agent_add(cmd, name="x", file="/nonexistent/file.yaml")


class TestResolveDefinition:
    """Test _resolve_definition helper."""

    def test_resolve_known(self):
        from azext_prototype.custom import _resolve_definition

        defs_dir = Path(__file__).resolve().parent.parent / "azext_prototype" / "agents" / "builtin" / "definitions"
        result = _resolve_definition(defs_dir, "example_custom_agent")
        assert result.exists()

    def test_resolve_unknown_raises(self, tmp_path):
        from azext_prototype.custom import _resolve_definition

        with pytest.raises(CLIError, match="Unknown definition"):
            _resolve_definition(tmp_path, "nonexistent_agent")


class TestCopyYamlWithName:
    """Test _copy_yaml_with_name helper."""

    def test_copies_and_renames(self, tmp_path):
        from azext_prototype.custom import _copy_yaml_with_name

        src = tmp_path / "source.yaml"
        src.write_text("name: original\ndescription: test\n", encoding="utf-8")
        dest = tmp_path / "dest.yaml"

        _copy_yaml_with_name(src, dest, "new-name")
        content = dest.read_text(encoding="utf-8")
        assert "new-name" in content
        assert "original" not in content


class TestPrototypeDeployOutputsExtended:
    """Additional deploy outputs tests."""

    @patch(f"{_MOD}._get_project_dir")
    def test_outputs_with_stored_data(self, mock_dir, project_with_build):
        from azext_prototype.custom import prototype_deploy_outputs

        mock_dir.return_value = str(project_with_build)
        state_dir = project_with_build / ".prototype" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "deploy_outputs.json").write_text(
            json.dumps({"rg_name": {"value": "test-rg"}}), encoding="utf-8"
        )
        cmd = MagicMock()
        result = prototype_deploy_outputs(cmd, json_output=True)
        assert isinstance(result, dict)
