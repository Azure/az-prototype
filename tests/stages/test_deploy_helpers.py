"""Tests for deploy_helpers — error handling paths.

Covers:
- Azure CLI command execution with error handling (subprocess errors, FileNotFoundError, stderr parsing)
- Terraform secret variable scanning (suffix detection, default value overriding, deduplication)
- Secret resolution with generation (reuse existing, generate new, config update)
- Az CLI path resolution (Windows .cmd variant, fallback ordering)
- build_deploy_env construction
- check_az_login / get_current_subscription / get_current_tenant
- login_service_principal / set_deployment_context
- DeploymentOutputCapture: terraform/bicep capture, accessors, env vars
- find_bicep_params / is_subscription_scoped / get_deploy_location
"""

import json
import os
import subprocess
from unittest.mock import MagicMock, patch

# ======================================================================
# _find_az / _az — Az CLI path resolution
# ======================================================================


class TestFindAz:
    """Test _find_az fallback chain."""

    def test_shutil_which_found(self):
        """When shutil.which finds az, return that path."""
        from azext_prototype.stages import deploy_helpers

        # Clear module cache
        deploy_helpers._AZ = None
        with patch("shutil.which", return_value="/usr/bin/az"):
            result = deploy_helpers._find_az()
        assert result == "/usr/bin/az"

    def test_falls_back_to_python_bin_dir(self, tmp_path):
        """When shutil.which returns None, check Python's bin dir."""
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        fake_az = tmp_path / "az"
        fake_az.touch()

        with (
            patch("shutil.which", return_value=None),
            patch.object(deploy_helpers.sys, "executable", str(tmp_path / "python")),
            patch("os.path.isfile") as mock_isfile,
        ):
            # First call: az candidate check → True
            # Second call (would be .cmd check) should not be reached
            mock_isfile.side_effect = lambda p: p == str(tmp_path / "az")
            result = deploy_helpers._find_az()
        assert result == str(tmp_path / "az")

    def test_falls_back_to_windows_cmd(self, tmp_path):
        """When az is not found but az.cmd exists, return .cmd path."""
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with (
            patch("shutil.which", return_value=None),
            patch.object(deploy_helpers.sys, "executable", str(tmp_path / "python")),
            patch("os.path.isfile") as mock_isfile,
        ):
            # First call: az candidate → False, second call: az.cmd → True
            def isfile_side(p):
                return p.endswith(".cmd")

            mock_isfile.side_effect = isfile_side
            result = deploy_helpers._find_az()
        assert result.endswith(".cmd")

    def test_final_fallback_bare_az(self, tmp_path):
        """When nothing else works, return bare 'az'."""
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with (
            patch("shutil.which", return_value=None),
            patch.object(deploy_helpers.sys, "executable", str(tmp_path / "python")),
            patch("os.path.isfile", return_value=False),
        ):
            result = deploy_helpers._find_az()
        assert result == "az"

    def test_az_caches_result(self):
        """_az() caches on first call."""
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with patch.object(deploy_helpers, "_find_az", return_value="/cached/az") as mock_find:
            val1 = deploy_helpers._az()
            val2 = deploy_helpers._az()
        assert val1 == "/cached/az"
        assert val2 == "/cached/az"
        mock_find.assert_called_once()
        # Clean up
        deploy_helpers._AZ = None


# ======================================================================
# build_deploy_env
# ======================================================================


class TestBuildDeployEnv:
    """Test build_deploy_env merges OS environ with Azure auth vars."""

    def test_all_params_set(self):
        from azext_prototype.stages.deploy_helpers import build_deploy_env

        env = build_deploy_env(
            subscription="sub-123",
            tenant="tenant-abc",
            client_id="cid",
            client_secret="csec",
        )
        assert env["ARM_SUBSCRIPTION_ID"] == "sub-123"
        assert env["TF_VAR_subscription_id"] == "sub-123"
        assert env["SUBSCRIPTION_ID"] == "sub-123"
        assert env["ARM_TENANT_ID"] == "tenant-abc"
        assert env["ARM_CLIENT_ID"] == "cid"
        assert env["ARM_CLIENT_SECRET"] == "csec"

    def test_none_params_skipped(self):
        from azext_prototype.stages.deploy_helpers import build_deploy_env

        env = build_deploy_env(subscription="sub-only")
        assert env["ARM_SUBSCRIPTION_ID"] == "sub-only"
        assert "ARM_TENANT_ID" not in env or env.get("ARM_TENANT_ID") == os.environ.get("ARM_TENANT_ID")

    def test_includes_os_environ(self):
        from azext_prototype.stages.deploy_helpers import build_deploy_env

        env = build_deploy_env()
        # Should contain at least PATH from os.environ
        assert "PATH" in env


# ======================================================================
# Terraform Secret Variable Scanning
# ======================================================================


class TestScanTfSecretVariables:
    """Test scan_tf_secret_variables with suffix detection, defaults, dedup."""

    def test_detects_secret_suffix(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import scan_tf_secret_variables

        tf_file = tmp_path / "main.tf"
        tf_file.write_text(
            """
variable "db_password" {
  type = string
}
""",
            encoding="utf-8",
        )
        result = scan_tf_secret_variables(tmp_path)
        assert "db_password" in result

    def test_detects_secret_suffix_underscore(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import scan_tf_secret_variables

        tf_file = tmp_path / "main.tf"
        tf_file.write_text(
            """
variable "api_secret" {
  type = string
}
""",
            encoding="utf-8",
        )
        result = scan_tf_secret_variables(tmp_path)
        assert "api_secret" in result

    def test_skips_non_secret_variable(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import scan_tf_secret_variables

        tf_file = tmp_path / "main.tf"
        tf_file.write_text(
            """
variable "resource_group_name" {
  type = string
}
""",
            encoding="utf-8",
        )
        result = scan_tf_secret_variables(tmp_path)
        assert result == []

    def test_skips_known_auth_variables(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import scan_tf_secret_variables

        tf_file = tmp_path / "main.tf"
        tf_file.write_text(
            """
variable "client_secret" {
  type = string
}
""",
            encoding="utf-8",
        )
        result = scan_tf_secret_variables(tmp_path)
        assert "client_secret" not in result

    def test_skips_variable_with_non_empty_default(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import scan_tf_secret_variables

        tf_file = tmp_path / "main.tf"
        tf_file.write_text(
            """
variable "db_password" {
  type    = string
  default = "predefined-value"
}
""",
            encoding="utf-8",
        )
        result = scan_tf_secret_variables(tmp_path)
        assert result == []

    def test_includes_variable_with_empty_default(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import scan_tf_secret_variables

        tf_file = tmp_path / "main.tf"
        tf_file.write_text(
            """
variable "db_password" {
  type    = string
  default = ""
}
""",
            encoding="utf-8",
        )
        result = scan_tf_secret_variables(tmp_path)
        assert "db_password" in result

    def test_deduplicates_across_files(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import scan_tf_secret_variables

        (tmp_path / "a.tf").write_text(
            'variable "db_password" {\n  type = string\n}\n',
            encoding="utf-8",
        )
        (tmp_path / "b.tf").write_text(
            'variable "db_password" {\n  type = string\n}\n',
            encoding="utf-8",
        )
        result = scan_tf_secret_variables(tmp_path)
        assert result.count("db_password") == 1

    def test_handles_unreadable_file(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import scan_tf_secret_variables

        tf_file = tmp_path / "main.tf"
        tf_file.write_text("some content", encoding="utf-8")
        # Make unreadable (best-effort; may not work on all platforms)
        with patch("pathlib.Path.read_text", side_effect=OSError("permission denied")):
            result = scan_tf_secret_variables(tmp_path)
        assert result == []

    def test_no_tf_files(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import scan_tf_secret_variables

        result = scan_tf_secret_variables(tmp_path)
        assert result == []


# ======================================================================
# resolve_stage_secrets
# ======================================================================


class TestResolveStageSecrets:
    """Test secret resolution: reuse existing, generate new, config update."""

    def test_no_secrets_needed(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import resolve_stage_secrets

        (tmp_path / "main.tf").write_text(
            'variable "name" {\n  type = string\n}\n',
            encoding="utf-8",
        )
        config = MagicMock()
        result = resolve_stage_secrets(tmp_path, config)
        assert result == {}

    def test_generates_new_secret(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import resolve_stage_secrets

        (tmp_path / "main.tf").write_text(
            'variable "db_password" {\n  type = string\n}\n',
            encoding="utf-8",
        )
        config = MagicMock()
        config.get.return_value = {}
        result = resolve_stage_secrets(tmp_path, config)
        assert "TF_VAR_db_password" in result
        assert len(result["TF_VAR_db_password"]) == 64  # 32 bytes hex
        config.set.assert_called_once()

    def test_reuses_existing_secret(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import resolve_stage_secrets

        (tmp_path / "main.tf").write_text(
            'variable "db_password" {\n  type = string\n}\n',
            encoding="utf-8",
        )
        config = MagicMock()
        config.get.return_value = {"db_password": "existing-secret-value"}
        result = resolve_stage_secrets(tmp_path, config)
        assert result["TF_VAR_db_password"] == "existing-secret-value"
        config.set.assert_not_called()

    def test_stored_not_dict_generates_new(self, tmp_path):
        """If stored secrets is a non-dict, treat as missing."""
        from azext_prototype.stages.deploy_helpers import resolve_stage_secrets

        (tmp_path / "main.tf").write_text(
            'variable "db_password" {\n  type = string\n}\n',
            encoding="utf-8",
        )
        config = MagicMock()
        config.get.return_value = "not-a-dict"
        result = resolve_stage_secrets(tmp_path, config)
        assert "TF_VAR_db_password" in result
        config.set.assert_called_once()


# ======================================================================
# check_az_login / get_current_subscription / get_current_tenant
# ======================================================================


class TestAzCliCommands:
    """Test Azure CLI command wrappers with error handling."""

    def test_check_az_login_success(self):
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with patch("subprocess.run") as mock_run, patch.object(deploy_helpers, "_find_az", return_value="az"):
            mock_run.return_value = MagicMock(returncode=0)
            deploy_helpers._AZ = None
            result = deploy_helpers.check_az_login()
        assert result is True

    def test_check_az_login_failure(self):
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with patch("subprocess.run") as mock_run, patch.object(deploy_helpers, "_find_az", return_value="az"):
            mock_run.return_value = MagicMock(returncode=1)
            deploy_helpers._AZ = None
            result = deploy_helpers.check_az_login()
        assert result is False

    def test_check_az_login_file_not_found(self):
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch.object(deploy_helpers, "_find_az", return_value="az"),
        ):
            deploy_helpers._AZ = None
            result = deploy_helpers.check_az_login()
        assert result is False

    def test_get_current_subscription_success(self):
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with patch("subprocess.run") as mock_run, patch.object(deploy_helpers, "_find_az", return_value="az"):
            mock_run.return_value = MagicMock(returncode=0, stdout="sub-id-123\n")
            deploy_helpers._AZ = None
            result = deploy_helpers.get_current_subscription()
        assert result == "sub-id-123"

    def test_get_current_subscription_error(self):
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with (
            patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "az")),
            patch.object(deploy_helpers, "_find_az", return_value="az"),
        ):
            deploy_helpers._AZ = None
            result = deploy_helpers.get_current_subscription()
        assert result == ""

    def test_get_current_subscription_file_not_found(self):
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch.object(deploy_helpers, "_find_az", return_value="az"),
        ):
            deploy_helpers._AZ = None
            result = deploy_helpers.get_current_subscription()
        assert result == ""

    def test_get_current_tenant_success(self):
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with patch("subprocess.run") as mock_run, patch.object(deploy_helpers, "_find_az", return_value="az"):
            mock_run.return_value = MagicMock(returncode=0, stdout="tenant-abc\n")
            deploy_helpers._AZ = None
            result = deploy_helpers.get_current_tenant()
        assert result == "tenant-abc"

    def test_get_current_tenant_error(self):
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch.object(deploy_helpers, "_find_az", return_value="az"),
        ):
            deploy_helpers._AZ = None
            result = deploy_helpers.get_current_tenant()
        assert result == ""


# ======================================================================
# login_service_principal
# ======================================================================


class TestLoginServicePrincipal:
    """Test service principal login with error paths."""

    def test_login_success(self):
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with (
            patch("subprocess.run") as mock_run,
            patch.object(deploy_helpers, "_find_az", return_value="az"),
            patch.object(deploy_helpers, "get_current_subscription", return_value="sub-after"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            deploy_helpers._AZ = None
            result = deploy_helpers.login_service_principal("cid", "csec", "tid")
        assert result["status"] == "ok"
        assert result["subscription"] == "sub-after"

    def test_login_failure_returncode(self):
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with patch("subprocess.run") as mock_run, patch.object(deploy_helpers, "_find_az", return_value="az"):
            mock_run.return_value = MagicMock(returncode=1, stderr="auth failed", stdout="")
            deploy_helpers._AZ = None
            result = deploy_helpers.login_service_principal("cid", "csec", "tid")
        assert result["status"] == "failed"
        assert "auth failed" in result["error"]

    def test_login_file_not_found(self):
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch.object(deploy_helpers, "_find_az", return_value="az"),
        ):
            deploy_helpers._AZ = None
            result = deploy_helpers.login_service_principal("cid", "csec", "tid")
        assert result["status"] == "failed"
        assert "not found" in result["error"]


# ======================================================================
# set_deployment_context
# ======================================================================


class TestSetDeploymentContext:
    """Test set_deployment_context with tenant and error paths."""

    def test_success_without_tenant(self):
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with patch("subprocess.run") as mock_run, patch.object(deploy_helpers, "_find_az", return_value="az"):
            mock_run.return_value = MagicMock(returncode=0)
            deploy_helpers._AZ = None
            result = deploy_helpers.set_deployment_context("sub-123")
        assert result["status"] == "ok"

    def test_success_with_tenant(self):
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with patch("subprocess.run") as mock_run, patch.object(deploy_helpers, "_find_az", return_value="az"):
            mock_run.return_value = MagicMock(returncode=0)
            deploy_helpers._AZ = None
            result = deploy_helpers.set_deployment_context("sub-123", tenant="tid")
        assert result["status"] == "ok"
        # Verify --tenant flag was passed
        call_args = mock_run.call_args[0][0]
        assert "--tenant" in call_args
        assert "tid" in call_args

    def test_failure_returncode(self):
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with patch("subprocess.run") as mock_run, patch.object(deploy_helpers, "_find_az", return_value="az"):
            mock_run.return_value = MagicMock(returncode=1, stderr="bad sub", stdout="")
            deploy_helpers._AZ = None
            result = deploy_helpers.set_deployment_context("bad-sub")
        assert result["status"] == "failed"
        assert "bad sub" in result["error"]

    def test_file_not_found(self):
        from azext_prototype.stages import deploy_helpers

        deploy_helpers._AZ = None
        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch.object(deploy_helpers, "_find_az", return_value="az"),
        ):
            deploy_helpers._AZ = None
            result = deploy_helpers.set_deployment_context("sub-123")
        assert result["status"] == "failed"
        assert "not found" in result["error"]


# ======================================================================
# find_bicep_params / is_subscription_scoped / get_deploy_location
# ======================================================================


class TestBicepDiscovery:
    """Test Bicep template/parameter file discovery helpers."""

    def test_find_bicep_params_parameters_json(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import find_bicep_params

        (tmp_path / "main.parameters.json").touch()
        result = find_bicep_params(tmp_path, tmp_path / "main.bicep")
        assert result == tmp_path / "main.parameters.json"

    def test_find_bicep_params_bicepparam(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import find_bicep_params

        (tmp_path / "main.bicepparam").touch()
        result = find_bicep_params(tmp_path, tmp_path / "main.bicep")
        assert result == tmp_path / "main.bicepparam"

    def test_find_bicep_params_generic(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import find_bicep_params

        (tmp_path / "parameters.json").touch()
        result = find_bicep_params(tmp_path, tmp_path / "main.bicep")
        assert result == tmp_path / "parameters.json"

    def test_find_bicep_params_none(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import find_bicep_params

        result = find_bicep_params(tmp_path, tmp_path / "main.bicep")
        assert result is None

    def test_find_bicep_params_priority(self, tmp_path):
        """parameters.json beats bicepparam, stem.parameters.json beats both."""
        from azext_prototype.stages.deploy_helpers import find_bicep_params

        (tmp_path / "main.parameters.json").touch()
        (tmp_path / "main.bicepparam").touch()
        (tmp_path / "parameters.json").touch()
        result = find_bicep_params(tmp_path, tmp_path / "main.bicep")
        assert result == tmp_path / "main.parameters.json"

    def test_is_subscription_scoped_true(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import is_subscription_scoped

        bicep_file = tmp_path / "main.bicep"
        bicep_file.write_text("targetScope = 'subscription'\n\nresource rg ...", encoding="utf-8")
        assert is_subscription_scoped(bicep_file) is True

    def test_is_subscription_scoped_false(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import is_subscription_scoped

        bicep_file = tmp_path / "main.bicep"
        bicep_file.write_text("resource kv 'Microsoft.KeyVault/vaults@...'", encoding="utf-8")
        assert is_subscription_scoped(bicep_file) is False

    def test_is_subscription_scoped_unreadable(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import is_subscription_scoped

        bicep_file = tmp_path / "missing.bicep"
        assert is_subscription_scoped(bicep_file) is False

    def test_get_deploy_location_from_params(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import get_deploy_location

        params = {"parameters": {"location": {"value": "westus2"}}}
        (tmp_path / "parameters.json").write_text(json.dumps(params), encoding="utf-8")
        assert get_deploy_location(tmp_path) == "westus2"

    def test_get_deploy_location_string_value(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import get_deploy_location

        params = {"location": "eastus"}
        (tmp_path / "parameters.json").write_text(json.dumps(params), encoding="utf-8")
        assert get_deploy_location(tmp_path) == "eastus"

    def test_get_deploy_location_none(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import get_deploy_location

        assert get_deploy_location(tmp_path) is None

    def test_get_deploy_location_bad_json(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import get_deploy_location

        (tmp_path / "parameters.json").write_text("not json", encoding="utf-8")
        assert get_deploy_location(tmp_path) is None


# ======================================================================
# DeploymentOutputCapture
# ======================================================================


class TestDeploymentOutputCapture:
    """Test capture, accessors, and env var generation."""

    def test_capture_terraform(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import DeploymentOutputCapture

        cap = DeploymentOutputCapture(str(tmp_path))
        tf_output = json.dumps(
            {
                "endpoint": {"value": "https://app.azurewebsites.net", "type": "string"},
                "key": {"value": "abc123", "type": "string"},
            }
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=tf_output)
            result = cap.capture_terraform(tmp_path / "infra")
        assert result["endpoint"] == "https://app.azurewebsites.net"
        assert result["key"] == "abc123"

    def test_capture_terraform_failure(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import DeploymentOutputCapture

        cap = DeploymentOutputCapture(str(tmp_path))
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = cap.capture_terraform(tmp_path / "infra")
        assert result == {}

    def test_capture_bicep(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import DeploymentOutputCapture

        cap = DeploymentOutputCapture(str(tmp_path))
        bicep_output = json.dumps(
            {
                "properties": {
                    "outputs": {
                        "storageEndpoint": {"value": "https://storage.blob.core.windows.net", "type": "string"},
                    }
                }
            }
        )
        result = cap.capture_bicep(bicep_output)
        assert result["storageEndpoint"] == "https://storage.blob.core.windows.net"

    def test_capture_bicep_bad_json(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import DeploymentOutputCapture

        cap = DeploymentOutputCapture(str(tmp_path))
        result = cap.capture_bicep("not json")
        assert result == {}

    def test_get_across_providers(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import DeploymentOutputCapture

        cap = DeploymentOutputCapture(str(tmp_path))
        cap._outputs = {
            "terraform": {"key1": "val1"},
            "bicep": {"key2": "val2"},
        }
        assert cap.get("key1") == "val1"
        assert cap.get("key2") == "val2"
        assert cap.get("missing", "default") == "default"

    def test_get_all(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import DeploymentOutputCapture

        cap = DeploymentOutputCapture(str(tmp_path))
        cap._outputs = {"terraform": {"a": 1}}
        all_outputs = cap.get_all()
        assert all_outputs == {"terraform": {"a": 1}}
        # Verify it's a copy
        all_outputs["extra"] = True
        assert "extra" not in cap._outputs

    def test_to_env_vars(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import DeploymentOutputCapture

        cap = DeploymentOutputCapture(str(tmp_path))
        cap._outputs = {
            "terraform": {"endpoint": "https://app.com"},
            "bicep": {"storage_key": "secret"},
        }
        env = cap.to_env_vars()
        assert env["PROTOTYPE_ENDPOINT"] == "https://app.com"
        assert env["PROTOTYPE_STORAGE_KEY"] == "secret"

    def test_flatten_outputs_plain_values(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import DeploymentOutputCapture

        flat = DeploymentOutputCapture._flatten_outputs({"simple": "value"})
        assert flat["simple"] == "value"

    def test_load_existing(self, tmp_path):
        """Test that existing outputs file is loaded on construction."""
        from azext_prototype.stages.deploy_helpers import DeploymentOutputCapture

        output_dir = tmp_path / ".prototype" / "state"
        output_dir.mkdir(parents=True)
        (output_dir / "deployment_outputs.json").write_text(
            json.dumps({"terraform": {"x": "y"}}),
            encoding="utf-8",
        )
        cap = DeploymentOutputCapture(str(tmp_path))
        assert cap.get("x") == "y"

    def test_load_bad_json(self, tmp_path):
        """Bad JSON in existing outputs file falls back to empty dict."""
        from azext_prototype.stages.deploy_helpers import DeploymentOutputCapture

        output_dir = tmp_path / ".prototype" / "state"
        output_dir.mkdir(parents=True)
        (output_dir / "deployment_outputs.json").write_text("not json", encoding="utf-8")
        cap = DeploymentOutputCapture(str(tmp_path))
        assert cap._outputs == {}

# --- Additional imports from merged flat test ---
from azext_prototype.stages.deploy_helpers import DEPLOY_ENV_MAPPING, DeploymentOutputCapture, DeployScriptGenerator, RollbackManager, build_deploy_env, resolve_stage_secrets, scan_tf_secret_variables
from pathlib import Path


class TestDeployScriptGenerator:
    """Test deploy script generation."""

    def test_generate_webapp_script(self, tmp_path):
        app_dir = tmp_path / "my-api"
        app_dir.mkdir()

        script = DeployScriptGenerator.generate(
            app_dir=app_dir,
            app_name="my-api",
            deploy_type="webapp",
            resource_group="rg-test",
        )

        assert "#!/usr/bin/env bash" in script
        assert "my-api" in script
        assert "az webapp deploy" in script
        assert (app_dir / "deploy.sh").exists()

    def test_generate_container_app_script(self, tmp_path):
        app_dir = tmp_path / "my-app"
        app_dir.mkdir()

        script = DeployScriptGenerator.generate(
            app_dir=app_dir,
            app_name="my-app",
            deploy_type="container_app",
            resource_group="rg-test",
            registry="myregistry.azurecr.io",
        )

        assert "az acr build" in script
        assert "az containerapp update" in script
        assert "myregistry.azurecr.io" in script

    def test_generate_function_script(self, tmp_path):
        app_dir = tmp_path / "my-func"
        app_dir.mkdir()

        script = DeployScriptGenerator.generate(
            app_dir=app_dir,
            app_name="my-func",
            deploy_type="function",
            resource_group="rg-test",
        )

        assert "func azure functionapp publish" in script
        assert "my-func" in script


class TestRollbackManager:
    """Test rollback tracking and instructions."""

    def test_snapshot_before_deploy(self, tmp_project):
        mgr = RollbackManager(str(tmp_project))
        snapshot = mgr.snapshot_before_deploy("infra", "terraform")

        assert snapshot["scope"] == "infra"
        assert snapshot["iac_tool"] == "terraform"
        assert "timestamp" in snapshot

    def test_multiple_snapshots(self, tmp_project):
        mgr = RollbackManager(str(tmp_project))
        mgr.snapshot_before_deploy("infra", "terraform")
        mgr.snapshot_before_deploy("apps", "terraform")

        latest = mgr.get_last_snapshot()
        assert latest["scope"] == "apps"

    def test_rollback_instructions_terraform(self, tmp_project):
        mgr = RollbackManager(str(tmp_project))
        mgr.snapshot_before_deploy("infra", "terraform")

        instructions = mgr.get_rollback_instructions()
        assert any("terraform" in line.lower() for line in instructions)

    def test_rollback_instructions_bicep(self, tmp_project):
        mgr = RollbackManager(str(tmp_project))
        mgr.snapshot_before_deploy("infra", "bicep")

        instructions = mgr.get_rollback_instructions()
        assert any("bicep" in line.lower() or "deployment" in line.lower() for line in instructions)

    def test_no_snapshots(self, tmp_project):
        mgr = RollbackManager(str(tmp_project))
        assert mgr.get_last_snapshot() is None

        instructions = mgr.get_rollback_instructions()
        assert len(instructions) >= 1  # Should have "nothing to roll back" message

    def test_persistence(self, tmp_project):
        mgr1 = RollbackManager(str(tmp_project))
        mgr1.snapshot_before_deploy("infra", "terraform")

        mgr2 = RollbackManager(str(tmp_project))
        assert mgr2.get_last_snapshot() is not None
        assert mgr2.get_last_snapshot()["scope"] == "infra"


class TestDeployEnvMapping:
    """Tests for DEPLOY_ENV_MAPPING and build_deploy_env()."""

    def test_mapping_covers_all_params(self):
        """Every build_deploy_env parameter has a mapping entry."""
        assert "subscription" in DEPLOY_ENV_MAPPING
        assert "tenant" in DEPLOY_ENV_MAPPING
        assert "client_id" in DEPLOY_ENV_MAPPING
        assert "client_secret" in DEPLOY_ENV_MAPPING

    def test_mapping_includes_tf_var(self):
        """Each param maps to at least one TF_VAR_* entry."""
        for param, keys in DEPLOY_ENV_MAPPING.items():
            tf_vars = [k for k in keys if k.startswith("TF_VAR_")]
            assert tf_vars, f"{param} has no TF_VAR_* mapping"

    def test_mapping_includes_arm(self):
        """Each param maps to at least one ARM_* entry."""
        for param, keys in DEPLOY_ENV_MAPPING.items():
            arm_vars = [k for k in keys if k.startswith("ARM_")]
            assert arm_vars, f"{param} has no ARM_* mapping"

    def test_all_fields(self):
        env = build_deploy_env("sub-123", "tenant-456", "client-id", "secret")
        # ARM vars
        assert env["ARM_SUBSCRIPTION_ID"] == "sub-123"
        assert env["ARM_TENANT_ID"] == "tenant-456"
        assert env["ARM_CLIENT_ID"] == "client-id"
        assert env["ARM_CLIENT_SECRET"] == "secret"
        # TF_VAR vars (auto-resolve HCL variables)
        assert env["TF_VAR_subscription_id"] == "sub-123"
        assert env["TF_VAR_tenant_id"] == "tenant-456"
        assert env["TF_VAR_client_id"] == "client-id"
        assert env["TF_VAR_client_secret"] == "secret"
        # Legacy
        assert env["SUBSCRIPTION_ID"] == "sub-123"

    def test_subscription_only(self):
        env = build_deploy_env("sub-123")
        assert env["ARM_SUBSCRIPTION_ID"] == "sub-123"
        assert env["TF_VAR_subscription_id"] == "sub-123"
        assert env["SUBSCRIPTION_ID"] == "sub-123"
        assert "ARM_TENANT_ID" not in env
        assert "TF_VAR_tenant_id" not in env
        assert "ARM_CLIENT_ID" not in env

    def test_inherits_os_environ(self):
        env = build_deploy_env("sub-123")
        # PATH should be inherited from os.environ
        assert "PATH" in env

    def test_empty(self):
        env = build_deploy_env()
        assert "ARM_SUBSCRIPTION_ID" not in env
        assert "TF_VAR_subscription_id" not in env
        assert "ARM_TENANT_ID" not in env
        # Should still have os.environ entries
        assert "PATH" in env


class TestDeployEnvPassing:
    """Tests that verify env is passed through to subprocess calls."""

    @patch("subprocess.run")
    def test_deploy_terraform_passes_env(self, mock_run):
        from azext_prototype.stages.deploy_helpers import deploy_terraform

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        test_env = build_deploy_env("sub-123", "tenant-456")

        deploy_terraform(Path("/tmp/fake"), "sub-123", env=test_env)

        # All subprocess.run calls should receive env=test_env
        for c in mock_run.call_args_list:
            assert c.kwargs.get("env") is test_env

    @patch("subprocess.run")
    def test_deploy_bicep_adds_tenant_flag(self, mock_run):
        from azext_prototype.stages.deploy_helpers import deploy_bicep

        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
        infra_dir = Path("/tmp/fake")
        test_env = build_deploy_env("sub-123", "tenant-456")

        # Create a mock bicep file
        with patch.object(Path, "exists", return_value=True), patch.object(Path, "glob", return_value=[]), patch(
            "azext_prototype.stages.deploy_helpers.find_bicep_params", return_value=None
        ), patch("azext_prototype.stages.deploy_helpers.is_subscription_scoped", return_value=False):
            deploy_bicep(infra_dir, "sub-123", "my-rg", env=test_env)

        # Verify --tenant was added to the command
        cmd = mock_run.call_args[0][0]
        assert "--tenant" in cmd
        assert "tenant-456" in cmd
        assert mock_run.call_args.kwargs.get("env") is test_env

    @patch("subprocess.run")
    def test_deploy_app_stage_merges_env(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_app_stage

        stage_dir = tmp_path / "app"
        stage_dir.mkdir()
        deploy_sh = stage_dir / "deploy.sh"
        deploy_sh.write_text("#!/bin/bash\necho ok")

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        test_env = build_deploy_env("sub-123", "tenant-456", "cid", "csecret")

        deploy_app_stage(stage_dir, "sub-123", "my-rg", env=test_env)

        passed_env = mock_run.call_args.kwargs.get("env")
        assert passed_env is not None
        assert passed_env["ARM_SUBSCRIPTION_ID"] == "sub-123"
        assert passed_env["ARM_TENANT_ID"] == "tenant-456"
        assert passed_env["SUBSCRIPTION_ID"] == "sub-123"
        assert passed_env["RESOURCE_GROUP"] == "my-rg"

    @patch("subprocess.run")
    def test_deploy_app_sub_dirs_receive_env(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_app_stage

        stage_dir = tmp_path / "apps"
        stage_dir.mkdir()
        sub_app = stage_dir / "api"
        sub_app.mkdir()
        (sub_app / "deploy.sh").write_text("#!/bin/bash\necho ok")

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        test_env = build_deploy_env("sub-123", "tenant-456")

        deploy_app_stage(stage_dir, "sub-123", "my-rg", env=test_env)

        passed_env = mock_run.call_args.kwargs.get("env")
        assert passed_env is not None
        assert passed_env["ARM_SUBSCRIPTION_ID"] == "sub-123"
        assert passed_env["ARM_TENANT_ID"] == "tenant-456"
        assert passed_env["RESOURCE_GROUP"] == "my-rg"

    @patch("subprocess.run")
    def test_rollback_terraform_passes_env(self, mock_run):
        from azext_prototype.stages.deploy_helpers import rollback_terraform

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        test_env = build_deploy_env("sub-123", "tenant-456")

        rollback_terraform(Path("/tmp/fake"), env=test_env)

        assert mock_run.call_args.kwargs.get("env") is test_env

    @patch("subprocess.run")
    def test_plan_terraform_passes_env(self, mock_run):
        from azext_prototype.stages.deploy_helpers import plan_terraform

        mock_run.return_value = MagicMock(returncode=0, stdout="Plan: 1 to add", stderr="")
        test_env = build_deploy_env("sub-123")

        plan_terraform(Path("/tmp/fake"), "sub-123", env=test_env)

        for c in mock_run.call_args_list:
            assert c.kwargs.get("env") is test_env

    @patch("subprocess.run")
    def test_rollback_bicep_adds_tenant_flag(self, mock_run):
        from azext_prototype.stages.deploy_helpers import rollback_bicep

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        test_env = build_deploy_env("sub-123", "tenant-456")

        rollback_bicep(Path("/tmp/fake"), "sub-123", "my-rg", env=test_env)

        cmd = mock_run.call_args[0][0]
        assert "--tenant" in cmd
        assert "tenant-456" in cmd
        assert mock_run.call_args.kwargs.get("env") is test_env

    @patch("subprocess.run")
    def test_whatif_bicep_adds_tenant_flag(self, mock_run):
        from azext_prototype.stages.deploy_helpers import whatif_bicep

        mock_run.return_value = MagicMock(returncode=0, stdout="What-if output", stderr="")
        test_env = build_deploy_env("sub-123", "tenant-789")

        with patch.object(Path, "exists", return_value=True), patch.object(Path, "glob", return_value=[]), patch(
            "azext_prototype.stages.deploy_helpers.find_bicep_params", return_value=None
        ), patch("azext_prototype.stages.deploy_helpers.is_subscription_scoped", return_value=False):
            whatif_bicep(Path("/tmp/fake"), "sub-123", "my-rg", env=test_env)

        cmd = mock_run.call_args[0][0]
        assert "--tenant" in cmd
        assert "tenant-789" in cmd

    @patch("subprocess.run")
    def test_deploy_terraform_no_env_still_works(self, mock_run):
        """Verify backward compat — env defaults to None."""
        from azext_prototype.stages.deploy_helpers import deploy_terraform

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        deploy_terraform(Path("/tmp/fake"), "sub-123")

        # env=None is passed (default), which means subprocess inherits os.environ
        for c in mock_run.call_args_list:
            assert c.kwargs.get("env") is None


class TestSecretVariableScanning:
    """Tests for scan_tf_secret_variables()."""

    def test_scan_finds_secret_suffix(self, tmp_path):
        tf = tmp_path / "main.tf"
        tf.write_text('variable "graph_client_secret" {}\n')
        result = scan_tf_secret_variables(tmp_path)
        assert "graph_client_secret" in result

    def test_scan_finds_password_suffix(self, tmp_path):
        tf = tmp_path / "main.tf"
        tf.write_text('variable "admin_password" {\n  type = string\n}\n')
        result = scan_tf_secret_variables(tmp_path)
        assert "admin_password" in result

    def test_scan_ignores_known_vars(self, tmp_path):
        tf = tmp_path / "main.tf"
        tf.write_text('variable "client_secret" {}\n')
        result = scan_tf_secret_variables(tmp_path)
        assert "client_secret" not in result

    def test_scan_ignores_non_secret_vars(self, tmp_path):
        tf = tmp_path / "main.tf"
        tf.write_text('variable "location" {}\nvariable "resource_group_name" {}\n')
        result = scan_tf_secret_variables(tmp_path)
        assert result == []

    def test_scan_ignores_vars_with_default(self, tmp_path):
        tf = tmp_path / "main.tf"
        tf.write_text('variable "api_secret" {\n  default = "preset-value"\n}\n')
        result = scan_tf_secret_variables(tmp_path)
        assert result == []

    def test_scan_multiple_files(self, tmp_path):
        (tmp_path / "main.tf").write_text('variable "graph_client_secret" {}\n')
        (tmp_path / "variables.tf").write_text('variable "db_password" {}\n')
        result = scan_tf_secret_variables(tmp_path)
        assert "graph_client_secret" in result
        assert "db_password" in result

    def test_scan_empty_dir(self, tmp_path):
        result = scan_tf_secret_variables(tmp_path)
        assert result == []
