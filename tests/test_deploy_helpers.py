"""Tests for azext_prototype.stages.deploy_helpers."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from azext_prototype.stages.deploy_helpers import (
    DEPLOY_ENV_MAPPING,
    DeploymentOutputCapture,
    DeployScriptGenerator,
    RollbackManager,
    build_deploy_env,
    resolve_stage_secrets,
    scan_tf_secret_variables,
)


class TestDeploymentOutputCapture:
    """Test output capture and environment variable generation."""

    def test_capture_and_retrieve(self, tmp_project):
        capture = DeploymentOutputCapture(str(tmp_project))

        # Simulate Bicep outputs
        bicep_output = json.dumps({
            "properties": {
                "outputs": {
                    "resource_group_name": {"type": "string", "value": "zd-rg-api-dev-eus"},
                    "storage_account_name": {"type": "string", "value": "stzddatadeveus"},
                }
            }
        })
        capture.capture_bicep(bicep_output)

        assert capture.get("resource_group_name") == "zd-rg-api-dev-eus"
        assert capture.get("storage_account_name") == "stzddatadeveus"
        assert capture.get("nonexistent", "fallback") == "fallback"

    def test_to_env_vars(self, tmp_project):
        capture = DeploymentOutputCapture(str(tmp_project))

        bicep_output = json.dumps({
            "properties": {
                "outputs": {
                    "resource_group_name": {"type": "string", "value": "rg-test"},
                    "app_url": {"type": "string", "value": "https://myapp.azurewebsites.net"},
                }
            }
        })
        capture.capture_bicep(bicep_output)

        env_vars = capture.to_env_vars()
        assert env_vars["PROTOTYPE_RESOURCE_GROUP_NAME"] == "rg-test"
        assert env_vars["PROTOTYPE_APP_URL"] == "https://myapp.azurewebsites.net"

    def test_persistence(self, tmp_project):
        # Write
        capture1 = DeploymentOutputCapture(str(tmp_project))
        capture1._outputs["terraform"] = {"foo": "bar"}
        capture1._save()

        # Read
        capture2 = DeploymentOutputCapture(str(tmp_project))
        assert capture2.get("foo") == "bar"

    def test_get_all(self, tmp_project):
        capture = DeploymentOutputCapture(str(tmp_project))
        assert isinstance(capture.get_all(), dict)

    def test_invalid_bicep_output(self, tmp_project):
        capture = DeploymentOutputCapture(str(tmp_project))
        result = capture.capture_bicep("not-json")
        assert result == {}


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
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "glob", return_value=[]), \
             patch("azext_prototype.stages.deploy_helpers.find_bicep_params", return_value=None), \
             patch("azext_prototype.stages.deploy_helpers.is_subscription_scoped", return_value=False):
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

        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "glob", return_value=[]), \
             patch("azext_prototype.stages.deploy_helpers.find_bicep_params", return_value=None), \
             patch("azext_prototype.stages.deploy_helpers.is_subscription_scoped", return_value=False):
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


class TestResolveStageSecrets:
    """Tests for resolve_stage_secrets()."""

    def _make_config(self, tmp_project):
        from azext_prototype.config import ProjectConfig

        config = ProjectConfig(str(tmp_project))
        config.create_default()
        return config

    def test_generates_new_secret(self, tmp_path, tmp_project):
        (tmp_path / "main.tf").write_text('variable "graph_client_secret" {}\n')
        config = self._make_config(tmp_project)

        result = resolve_stage_secrets(tmp_path, config)
        assert "TF_VAR_graph_client_secret" in result
        assert len(result["TF_VAR_graph_client_secret"]) == 64  # token_hex(32)

    def test_reuses_existing_secret(self, tmp_path, tmp_project):
        (tmp_path / "main.tf").write_text('variable "graph_client_secret" {}\n')
        config = self._make_config(tmp_project)
        config.set("deploy.generated_secrets.graph_client_secret", "reused-value")

        result = resolve_stage_secrets(tmp_path, config)
        assert result["TF_VAR_graph_client_secret"] == "reused-value"

    def test_persists_generated_secret(self, tmp_path, tmp_project):
        (tmp_path / "main.tf").write_text('variable "app_password" {}\n')
        config = self._make_config(tmp_project)

        resolve_stage_secrets(tmp_path, config)

        stored = config.get("deploy.generated_secrets.app_password")
        assert stored is not None
        assert len(stored) == 64

    def test_multiple_secrets(self, tmp_path, tmp_project):
        (tmp_path / "main.tf").write_text(
            'variable "graph_client_secret" {}\nvariable "admin_password" {}\n'
        )
        config = self._make_config(tmp_project)

        result = resolve_stage_secrets(tmp_path, config)
        assert "TF_VAR_graph_client_secret" in result
        assert "TF_VAR_admin_password" in result

    def test_no_secrets_needed(self, tmp_path, tmp_project):
        (tmp_path / "main.tf").write_text('variable "location" {}\n')
        config = self._make_config(tmp_project)

        result = resolve_stage_secrets(tmp_path, config)
        assert result == {}
