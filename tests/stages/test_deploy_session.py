"""Tests for deploy_session.py — branch coverage for dry-run layer branching,
preflight checks, stage deployment by layer, rollback ordering, output capture,
SP credential resolution, deployment context env building, and interactive loop.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from azext_prototype.agents.base import AgentCapability, AgentContext

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def deploy_context(project_with_build, sample_config):
    provider = MagicMock()
    provider.provider_name = "github-models"
    provider.default_model = "gpt-4o"
    provider.chat.return_value = MagicMock(
        content="Diagnosis: resource group missing.",
        model="test",
        usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
    )
    return AgentContext(
        project_config=sample_config,
        project_dir=str(project_with_build),
        ai_provider=provider,
    )


@pytest.fixture
def deploy_registry():
    registry = MagicMock()

    mock_qa = MagicMock()
    mock_qa.name = "qa-engineer"
    mock_qa.execute = MagicMock(return_value=MagicMock(content="Issue diagnosed.", model="test", usage={}))
    mock_qa.get_system_messages = MagicMock(return_value=[])
    mock_qa._temperature = 0.2
    mock_qa._max_tokens = 4096

    mock_tf = MagicMock()
    mock_tf.name = "terraform-agent"
    mock_tf.execute = MagicMock(return_value=MagicMock(content="Fixed.", model="test", usage={}))
    mock_tf.get_system_messages = MagicMock(return_value=[])

    mock_dev = MagicMock()
    mock_dev.name = "app-developer"
    mock_dev.execute = MagicMock(return_value=MagicMock(content="Fixed app.", model="test", usage={}))
    mock_dev.get_system_messages = MagicMock(return_value=[])

    mock_architect = MagicMock()
    mock_architect.name = "cloud-architect"
    mock_architect.execute = MagicMock(return_value=MagicMock(content="Guide fix.", model="test", usage={}))

    def find_by_cap(cap):
        mapping = {
            AgentCapability.QA: [mock_qa],
            AgentCapability.TERRAFORM: [mock_tf],
            AgentCapability.BICEP: [],
            AgentCapability.DEVELOP: [mock_dev],
            AgentCapability.ARCHITECT: [mock_architect],
        }
        return mapping.get(cap, [])

    registry.find_by_capability.side_effect = find_by_cap
    return registry


def _make_session(deploy_context, deploy_registry):
    from azext_prototype.stages.deploy_session import DeploySession

    return DeploySession(deploy_context, deploy_registry)


# ------------------------------------------------------------------
# DeployResult
# ------------------------------------------------------------------


class TestDeployResult:
    def test_defaults(self):
        from azext_prototype.stages.deploy_session import DeployResult

        result = DeployResult()
        assert result.deployed_stages == []
        assert result.failed_stages == []
        assert result.rolled_back_stages == []
        assert result.captured_outputs == {}
        assert result.cancelled is False

    def test_with_values(self):
        from azext_prototype.stages.deploy_session import DeployResult

        result = DeployResult(
            deployed_stages=[{"stage": 1}],
            captured_outputs={"key": "val"},
            cancelled=True,
        )
        assert len(result.deployed_stages) == 1
        assert result.captured_outputs["key"] == "val"
        assert result.cancelled is True


# ------------------------------------------------------------------
# _lookup_deployer_object_id
# ------------------------------------------------------------------


class TestLookupDeployerObjectId:
    @patch("azext_prototype.stages.deploy_session.subprocess.run")
    def test_user_auth_returns_oid(self, mock_run):
        from azext_prototype.stages.deploy_session import _lookup_deployer_object_id

        mock_run.return_value = MagicMock(returncode=0, stdout="abc-123-def\n")
        result = _lookup_deployer_object_id()
        assert result == "abc-123-def"

    @patch("azext_prototype.stages.deploy_session.subprocess.run")
    def test_sp_auth_uses_client_id(self, mock_run):
        from azext_prototype.stages.deploy_session import _lookup_deployer_object_id

        mock_run.return_value = MagicMock(returncode=0, stdout="sp-oid\n")
        result = _lookup_deployer_object_id(client_id="my-client")
        assert result == "sp-oid"
        # Should have called with sp show
        call_args = mock_run.call_args[0][0]
        assert "sp" in call_args
        assert "my-client" in call_args

    @patch("azext_prototype.stages.deploy_session.subprocess.run")
    def test_failure_returns_none(self, mock_run):
        from azext_prototype.stages.deploy_session import _lookup_deployer_object_id

        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = _lookup_deployer_object_id()
        assert result is None

    @patch("azext_prototype.stages.deploy_session.subprocess.run", side_effect=FileNotFoundError)
    def test_az_not_found_returns_none(self, mock_run):
        from azext_prototype.stages.deploy_session import _lookup_deployer_object_id

        result = _lookup_deployer_object_id()
        assert result is None


# ------------------------------------------------------------------
# _resolve_context
# ------------------------------------------------------------------


class TestResolveContext:
    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value=None)
    @patch("azext_prototype.stages.deploy_session.build_deploy_env", return_value={})
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123")
    def test_falls_back_to_current_subscription(self, mock_sub, mock_env, mock_oid, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)
        session._resolve_context(None, None)
        assert session._subscription == "sub-123"

    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value="oid-abc")
    @patch("azext_prototype.stages.deploy_session.build_deploy_env", return_value={})
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="")
    @patch("azext_prototype.stages.deploy_session.set_deployment_context", return_value={"status": "ok"})
    def test_sets_deployment_context_with_tenant(
        self, mock_ctx, mock_sub, mock_env, mock_oid, deploy_context, deploy_registry
    ):
        session = _make_session(deploy_context, deploy_registry)
        session._resolve_context("sub-override", "tenant-abc")
        assert session._subscription == "sub-override"
        assert session._tenant == "tenant-abc"
        assert session._deploy_env["TF_VAR_deployer_object_id"] == "oid-abc"


# ------------------------------------------------------------------
# Preflight checks
# ------------------------------------------------------------------


class TestPreflightChecks:
    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=False)
    def test_check_subscription_not_logged_in(self, mock_login, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)
        result = session._check_subscription("sub-123")
        assert result["status"] == "fail"
        assert "Not logged in" in result["message"]

    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="other-sub")
    def test_check_subscription_mismatch(self, mock_sub, mock_login, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)
        result = session._check_subscription("target-sub-1234")
        assert result["status"] == "warn"

    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="")
    def test_check_subscription_pass(self, mock_sub, mock_login, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)
        result = session._check_subscription("")
        assert result["status"] == "pass"

    @patch("azext_prototype.stages.deploy_session.get_current_tenant", return_value="other-tenant")
    def test_check_tenant_mismatch(self, mock_tenant, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)
        result = session._check_tenant("target-tenant-1234")
        assert result["status"] == "warn"

    @patch("azext_prototype.stages.deploy_session.get_current_tenant", return_value="target-tenant")
    def test_check_tenant_match(self, mock_tenant, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)
        result = session._check_tenant("target-tenant")
        assert result["status"] == "pass"

    @patch("azext_prototype.stages.deploy_session.subprocess.run")
    def test_check_iac_tool_terraform_found(self, mock_run, deploy_context, deploy_registry):
        mock_run.return_value = MagicMock(returncode=0, stdout="Terraform v1.5.0\n")
        session = _make_session(deploy_context, deploy_registry)
        result = session._check_iac_tool()
        assert result["status"] == "pass"
        assert "Terraform" in result["message"]

    @patch("azext_prototype.stages.deploy_session.subprocess.run", side_effect=FileNotFoundError)
    def test_check_iac_tool_terraform_not_found(self, mock_run, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)
        result = session._check_iac_tool()
        assert result["status"] == "fail"

    def test_check_iac_tool_bicep(self, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)
        session._iac_tool = "bicep"
        result = session._check_iac_tool()
        assert result["status"] == "pass"
        assert "Bicep" in result["name"]

    @patch("azext_prototype.stages.deploy_session.subprocess.run")
    def test_check_resource_group_exists(self, mock_run, deploy_context, deploy_registry):
        mock_run.return_value = MagicMock(returncode=0)
        session = _make_session(deploy_context, deploy_registry)
        result = session._check_resource_group("sub", "rg-test")
        assert result["status"] == "pass"

    @patch("azext_prototype.stages.deploy_session.subprocess.run")
    def test_check_resource_group_not_found(self, mock_run, deploy_context, deploy_registry):
        mock_run.return_value = MagicMock(returncode=1)
        session = _make_session(deploy_context, deploy_registry)
        result = session._check_resource_group("sub", "rg-test")
        assert result["status"] == "warn"


# ------------------------------------------------------------------
# _deploy_single_stage — layer dispatch
# ------------------------------------------------------------------


class TestDeploySingleStage:
    def _make_ready_session(self, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)
        session._subscription = "sub-123"
        session._resource_group = "rg-test"
        session._deploy_env = {}
        return session

    def test_manual_deploy_mode(self, deploy_context, deploy_registry):
        session = self._make_ready_session(deploy_context, deploy_registry)
        stage = {
            "stage": 1,
            "name": "Manual Step",
            "layer": "infra",
            "deploy_mode": "manual",
            "manual_instructions": "Run migration script",
            "dir": "concept/infra",
            "services": [],
        }
        result = session._deploy_single_stage(stage)
        assert result["status"] == "awaiting_manual"
        assert "migration" in result["instructions"]

    def test_missing_directory_skipped(self, deploy_context, deploy_registry):
        session = self._make_ready_session(deploy_context, deploy_registry)
        stage = {
            "stage": 1,
            "name": "Missing",
            "layer": "infra",
            "deploy_mode": "auto",
            "dir": "concept/infra/terraform/nonexistent",
            "services": [],
        }
        result = session._deploy_single_stage(stage)
        assert result["status"] == "skipped"

    @patch("azext_prototype.stages.deploy_session.deploy_terraform")
    @patch("azext_prototype.stages.deploy_session.resolve_stage_secrets", return_value={})
    def test_infra_layer_dispatches_terraform(self, mock_secrets, mock_deploy, deploy_context, deploy_registry):
        session = self._make_ready_session(deploy_context, deploy_registry)
        # Create stage directory
        stage_dir = Path(deploy_context.project_dir) / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)

        mock_deploy.return_value = {"status": "deployed", "deployment_output": ""}

        stage = {
            "stage": 1,
            "name": "Foundation",
            "layer": "infra",
            "deploy_mode": "auto",
            "dir": "concept/infra/terraform/stage-1",
            "services": [],
        }
        result = session._deploy_single_stage(stage)
        assert result["status"] == "deployed"
        mock_deploy.assert_called_once()

    @patch("azext_prototype.stages.deploy_session.deploy_app_stage")
    def test_app_layer_dispatches_app_deploy(self, mock_deploy, deploy_context, deploy_registry):
        session = self._make_ready_session(deploy_context, deploy_registry)
        stage_dir = Path(deploy_context.project_dir) / "concept" / "apps" / "stage-2"
        stage_dir.mkdir(parents=True, exist_ok=True)

        mock_deploy.return_value = {"status": "deployed"}

        stage = {
            "stage": 2,
            "name": "API",
            "layer": "app",
            "deploy_mode": "auto",
            "dir": "concept/apps/stage-2",
            "services": [],
        }
        result = session._deploy_single_stage(stage)
        assert result["status"] == "deployed"
        mock_deploy.assert_called_once()

    def test_docs_layer_auto_deployed(self, deploy_context, deploy_registry):
        session = self._make_ready_session(deploy_context, deploy_registry)
        stage_dir = Path(deploy_context.project_dir) / "concept" / "docs"
        stage_dir.mkdir(parents=True, exist_ok=True)

        stage = {
            "stage": 3,
            "name": "Documentation",
            "layer": "docs",
            "deploy_mode": "auto",
            "dir": "concept/docs",
            "services": [],
        }
        result = session._deploy_single_stage(stage)
        assert result["status"] == "deployed"


# ------------------------------------------------------------------
# Dry-run
# ------------------------------------------------------------------


class TestDryRun:
    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value=None)
    @patch("azext_prototype.stages.deploy_session.build_deploy_env", return_value={})
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub")
    @patch("azext_prototype.stages.deploy_session.plan_terraform", return_value={"output": "Plan: 3 to add"})
    @patch("azext_prototype.stages.deploy_session.resolve_stage_secrets", return_value={})
    def test_dry_run_terraform(
        self, mock_secrets, mock_plan, mock_sub, mock_env, mock_oid, deploy_context, deploy_registry
    ):
        session = _make_session(deploy_context, deploy_registry)
        # Create stage directories
        stage_dir = Path(deploy_context.project_dir) / "concept" / "infra" / "terraform" / "stage-1-foundation"
        stage_dir.mkdir(parents=True, exist_ok=True)

        output = []
        result = session.run_dry_run(
            subscription="sub-123",
            print_fn=lambda m: output.append(m),
        )
        assert result.cancelled is False

    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value=None)
    @patch("azext_prototype.stages.deploy_session.build_deploy_env", return_value={})
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub")
    def test_dry_run_no_build_state(self, mock_sub, mock_env, mock_oid, project_with_config, sample_config):
        from azext_prototype.stages.deploy_session import DeploySession

        # Use project WITHOUT build state
        provider = MagicMock()
        provider.provider_name = "github-models"
        provider.chat.return_value = MagicMock(content="test", model="test", usage={})
        ctx = AgentContext(
            project_config=sample_config,
            project_dir=str(project_with_config),
            ai_provider=provider,
        )
        registry = MagicMock()
        registry.find_by_capability.return_value = []

        session = DeploySession(ctx, registry)
        output = []
        result = session.run_dry_run(
            subscription="sub-123",
            print_fn=lambda m: output.append(m),
        )
        assert result.cancelled is True

    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value=None)
    @patch("azext_prototype.stages.deploy_session.build_deploy_env", return_value={})
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub")
    def test_dry_run_target_stage_not_found(self, mock_sub, mock_env, mock_oid, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)
        output = []
        result = session.run_dry_run(
            subscription="sub-123",
            target_stage=999,
            print_fn=lambda m: output.append(m),
        )
        assert result.cancelled is True


# ------------------------------------------------------------------
# Single-stage deploy
# ------------------------------------------------------------------


class TestSingleStageDeploy:
    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value=None)
    @patch("azext_prototype.stages.deploy_session.build_deploy_env", return_value={})
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub")
    def test_stage_not_found(self, mock_sub, mock_env, mock_oid, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)
        output = []
        result = session.run_single_stage(
            999,
            subscription="sub-123",
            print_fn=lambda m: output.append(m),
        )
        assert result.cancelled is True

    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value=None)
    @patch("azext_prototype.stages.deploy_session.build_deploy_env", return_value={})
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub")
    def test_no_build_state_cancels(self, mock_sub, mock_env, mock_oid, project_with_config, sample_config):
        from azext_prototype.stages.deploy_session import DeploySession

        provider = MagicMock()
        provider.provider_name = "github-models"
        provider.chat.return_value = MagicMock(content="test", model="test", usage={})
        ctx = AgentContext(
            project_config=sample_config,
            project_dir=str(project_with_config),
            ai_provider=provider,
        )
        registry = MagicMock()
        registry.find_by_capability.return_value = []

        session = DeploySession(ctx, registry)
        output = []
        result = session.run_single_stage(
            1,
            subscription="sub-123",
            print_fn=lambda m: output.append(m),
        )
        assert result.cancelled is True


# ------------------------------------------------------------------
# Run — interactive quit
# ------------------------------------------------------------------


class TestDeployRunInteractive:
    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value=None)
    @patch("azext_prototype.stages.deploy_session.build_deploy_env", return_value={})
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub")
    def test_quit_at_confirmation(self, mock_sub, mock_env, mock_oid, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)
        output = []
        result = session.run(
            subscription="sub-123",
            input_fn=lambda p: "quit",
            print_fn=lambda m: output.append(m),
        )
        assert result.cancelled is True

    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value=None)
    @patch("azext_prototype.stages.deploy_session.build_deploy_env", return_value={})
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub")
    def test_eof_at_confirmation(self, mock_sub, mock_env, mock_oid, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)

        def raise_eof(p):
            raise EOFError

        result = session.run(
            subscription="sub-123",
            input_fn=raise_eof,
            print_fn=lambda m: None,
        )
        assert result.cancelled is True


# ------------------------------------------------------------------
# _capture_stage_outputs
# ------------------------------------------------------------------


class TestCaptureStageOutputs:
    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value=None)
    @patch("azext_prototype.stages.deploy_session.build_deploy_env", return_value={})
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub")
    def test_terraform_output_capture(self, mock_sub, mock_env, mock_oid, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)
        session._iac_tool = "terraform"
        session._output_capture = MagicMock()
        session._output_capture.capture_terraform.return_value = {"key_vault_id": "/sub/rg/kv"}
        session._output_capture.get_all.return_value = {"key_vault_id": "/sub/rg/kv"}

        stage = {"stage": 1, "dir": "concept/infra/terraform/stage-1", "services": []}
        session._capture_stage_outputs(stage)
        session._output_capture.capture_terraform.assert_called_once()

    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value=None)
    @patch("azext_prototype.stages.deploy_session.build_deploy_env", return_value={})
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub")
    def test_bicep_output_capture(self, mock_sub, mock_env, mock_oid, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)
        session._iac_tool = "bicep"
        session._output_capture = MagicMock()
        session._output_capture.capture_bicep.return_value = {"result": "ok"}
        session._output_capture.get_all.return_value = {"result": "ok"}

        stage = {"stage": 1, "dir": "concept/infra/bicep/stage-1", "deploy_output": "some output", "services": []}
        session._capture_stage_outputs(stage)
        session._output_capture.capture_bicep.assert_called_once_with("some output")

    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value=None)
    @patch("azext_prototype.stages.deploy_session.build_deploy_env", return_value={})
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub")
    def test_bicep_no_output_skips(self, mock_sub, mock_env, mock_oid, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)
        session._iac_tool = "bicep"
        session._output_capture = MagicMock()
        session._output_capture.capture_bicep.return_value = {}

        stage = {"stage": 1, "dir": "concept/infra/bicep/stage-1", "services": []}
        session._capture_stage_outputs(stage)
        # No deploy_output key = no capture call
        session._output_capture.capture_bicep.assert_not_called()


# ------------------------------------------------------------------
# _extract_providers_from_files
# ------------------------------------------------------------------


class TestExtractProviders:
    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value=None)
    @patch("azext_prototype.stages.deploy_session.build_deploy_env", return_value={})
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub")
    def test_extracts_terraform_providers(self, mock_sub, mock_env, mock_oid, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)

        stage_dir = Path(deploy_context.project_dir) / "concept" / "infra" / "terraform" / "stage-1-foundation"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text(
            'resource "azapi_resource" "kv" {\n' '  type = "Microsoft.KeyVault/vaults@2023-07-01"\n' "}\n",
            encoding="utf-8",
        )

        session._deploy_state._state["deployment_stages"] = [
            {"stage": 1, "dir": "concept/infra/terraform/stage-1-foundation", "services": []}
        ]

        namespaces = session._extract_providers_from_files()
        assert "Microsoft.KeyVault" in namespaces

    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value=None)
    @patch("azext_prototype.stages.deploy_session.build_deploy_env", return_value={})
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub")
    def test_no_files_returns_empty(self, mock_sub, mock_env, mock_oid, deploy_context, deploy_registry):
        session = _make_session(deploy_context, deploy_registry)
        session._deploy_state._state["deployment_stages"] = []
        namespaces = session._extract_providers_from_files()
        assert namespaces == set()
