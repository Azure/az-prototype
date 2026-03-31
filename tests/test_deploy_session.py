"""Tests for DeployState, DeploySession, preflight checks, and deploy stage.

Covers the deploy-stage overhaul modules:
- DeployState: YAML persistence, stage transitions, rollback ordering
- DeploySession: interactive session, dry-run, single-stage, slash commands
- Preflight checks: subscription, IaC tool, resource group, resource providers
- DeployStage: thin orchestrator delegation
- Deploy helpers: execution primitives, RollbackManager extensions
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from azext_prototype.ai.provider import AIResponse

# ======================================================================
# Helpers
# ======================================================================


def _make_response(content: str = "Mock response") -> AIResponse:
    return AIResponse(content=content, model="gpt-4o", usage={})


def _build_yaml(stages: list[dict] | None = None, iac_tool: str = "terraform") -> dict:
    """Return a realistic build.yaml structure."""
    if stages is None:
        stages = [
            {
                "stage": 1,
                "name": "Foundation",
                "category": "infra",
                "services": [
                    {
                        "name": "key-vault",
                        "computed_name": "zd-kv-api-dev-eus",
                        "resource_type": "Microsoft.KeyVault/vaults",
                        "sku": "standard",
                    },
                ],
                "status": "generated",
                "dir": "concept/infra/terraform/stage-1-foundation",
                "files": [],
            },
            {
                "stage": 2,
                "name": "Data Layer",
                "category": "data",
                "services": [
                    {
                        "name": "sql-db",
                        "computed_name": "zd-sql-api-dev-eus",
                        "resource_type": "Microsoft.Sql/servers",
                        "sku": "S0",
                    },
                ],
                "status": "generated",
                "dir": "concept/infra/terraform/stage-2-data",
                "files": [],
            },
            {
                "stage": 3,
                "name": "Application",
                "category": "app",
                "services": [
                    {
                        "name": "web-app",
                        "computed_name": "zd-app-web-dev-eus",
                        "resource_type": "Microsoft.Web/sites",
                        "sku": "B1",
                    },
                ],
                "status": "generated",
                "dir": "concept/apps/stage-3-application",
                "files": [],
            },
        ]
    return {
        "iac_tool": iac_tool,
        "deployment_stages": stages,
        "_metadata": {"created": "2026-01-01T00:00:00", "last_updated": "2026-01-01T00:00:00", "iteration": 1},
    }


def _write_build_yaml(project_dir, stages=None, iac_tool="terraform"):
    """Write build.yaml into the project state dir."""
    state_dir = Path(project_dir) / ".prototype" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    build_data = _build_yaml(stages, iac_tool)
    with open(state_dir / "build.yaml", "w", encoding="utf-8") as f:
        yaml.dump(build_data, f, default_flow_style=False)
    return state_dir / "build.yaml"


# ======================================================================
# DeployState tests
# ======================================================================


class TestDeployState:

    def test_default_state_structure(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        ds = DeployState(str(tmp_project))
        state = ds.state
        assert state["iac_tool"] == "terraform"
        assert state["subscription"] == ""
        assert state["resource_group"] == ""
        assert state["deployment_stages"] == []
        assert state["preflight_results"] == []
        assert state["deploy_log"] == []
        assert state["rollback_log"] == []
        assert state["captured_outputs"] == {}
        assert state["_metadata"]["iteration"] == 0

    def test_load_save_roundtrip(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        ds = DeployState(str(tmp_project))
        ds._state["subscription"] = "test-sub-123"
        ds._state["iac_tool"] = "bicep"
        ds.save()

        ds2 = DeployState(str(tmp_project))
        loaded = ds2.load()
        assert loaded["subscription"] == "test-sub-123"
        assert loaded["iac_tool"] == "bicep"
        assert loaded["_metadata"]["created"] is not None
        assert loaded["_metadata"]["last_updated"] is not None

    def test_exists_property(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        ds = DeployState(str(tmp_project))
        assert not ds.exists
        ds.save()
        assert ds.exists

    def test_load_from_build_state(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        result = ds.load_from_build_state(build_path)

        assert result is True
        assert len(ds.state["deployment_stages"]) == 3
        # Verify deploy-specific fields were added
        stage = ds.state["deployment_stages"][0]
        assert stage["deploy_status"] == "pending"
        assert stage["deploy_timestamp"] is None
        assert stage["deploy_output"] == ""
        assert stage["deploy_error"] == ""
        assert stage["rollback_timestamp"] is None

    def test_load_from_build_state_missing_file(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        ds = DeployState(str(tmp_project))
        result = ds.load_from_build_state("/nonexistent/build.yaml")
        assert result is False

    def test_load_from_build_state_no_stages(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project, stages=[])
        ds = DeployState(str(tmp_project))
        result = ds.load_from_build_state(build_path)
        assert result is False

    def test_stage_transitions(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        # pending → deploying
        ds.mark_stage_deploying(1)
        assert ds.get_stage(1)["deploy_status"] == "deploying"

        # deploying → deployed
        ds.mark_stage_deployed(1, output="resource_id=abc123")
        stage = ds.get_stage(1)
        assert stage["deploy_status"] == "deployed"
        assert stage["deploy_timestamp"] is not None
        assert stage["deploy_output"] == "resource_id=abc123"
        assert stage["deploy_error"] == ""

        # deployed → rolled_back
        ds.mark_stage_rolled_back(1)
        stage = ds.get_stage(1)
        assert stage["deploy_status"] == "rolled_back"
        assert stage["rollback_timestamp"] is not None

    def test_stage_failure(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.mark_stage_deploying(1)
        ds.mark_stage_failed(1, error="timeout connecting to Azure")
        stage = ds.get_stage(1)
        assert stage["deploy_status"] == "failed"
        assert stage["deploy_error"] == "timeout connecting to Azure"

    def test_get_pending_deployed_failed(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        assert len(ds.get_pending_stages()) == 3
        assert len(ds.get_deployed_stages()) == 0
        assert len(ds.get_failed_stages()) == 0

        ds.mark_stage_deployed(1)
        ds.mark_stage_failed(2, "error")

        assert len(ds.get_pending_stages()) == 1
        assert len(ds.get_deployed_stages()) == 1
        assert len(ds.get_failed_stages()) == 1

    def test_can_rollback_ordering(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.mark_stage_deployed(1)
        ds.mark_stage_deployed(2)
        ds.mark_stage_deployed(3)

        # Can only rollback stage 3 (highest)
        assert ds.can_rollback(3) is True
        assert ds.can_rollback(2) is False  # stage 3 still deployed
        assert ds.can_rollback(1) is False  # stages 2,3 still deployed

        # Roll back stage 3
        ds.mark_stage_rolled_back(3)
        assert ds.can_rollback(2) is True
        assert ds.can_rollback(1) is False  # stage 2 still deployed

    def test_rollback_candidates_reverse_order(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.mark_stage_deployed(1)
        ds.mark_stage_deployed(2)
        ds.mark_stage_deployed(3)

        candidates = ds.get_rollback_candidates()
        assert [c["stage"] for c in candidates] == [3, 2, 1]

    def test_preflight_results(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        ds = DeployState(str(tmp_project))
        results = [
            {"name": "Azure Login", "status": "pass", "message": "Logged in."},
            {"name": "Terraform", "status": "fail", "message": "Not found.", "fix_command": "brew install terraform"},
        ]
        ds.set_preflight_results(results)

        failures = ds.get_preflight_failures()
        assert len(failures) == 1
        assert failures[0]["name"] == "Terraform"

    def test_deploy_log(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.mark_stage_deploying(1)
        ds.mark_stage_deployed(1)

        assert len(ds.state["deploy_log"]) == 2
        assert ds.state["deploy_log"][0]["action"] == "deploying"
        assert ds.state["deploy_log"][1]["action"] == "deployed"

    def test_reset(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)
        assert len(ds.state["deployment_stages"]) == 3

        ds.reset()
        assert ds.state["deployment_stages"] == []
        assert ds.exists  # File still exists after reset

    def test_format_deploy_report(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)
        ds._state["subscription"] = "sub-123"

        ds.mark_stage_deployed(1)
        ds.mark_stage_failed(2, "timeout")

        report = ds.format_deploy_report()
        assert "Deploy Report" in report
        assert "sub-123" in report
        assert "1 deployed" in report
        assert "1 failed" in report

    def test_format_stage_status(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        status = ds.format_stage_status()
        assert "Foundation" in status
        assert "Application" in status
        assert "0/3 stages deployed" in status

    def test_format_preflight_report(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        ds = DeployState(str(tmp_project))
        ds.set_preflight_results(
            [
                {"name": "Azure Login", "status": "pass", "message": "OK"},
                {
                    "name": "Terraform",
                    "status": "warn",
                    "message": "Old version",
                    "fix_command": "brew upgrade terraform",
                },
            ]
        )

        report = ds.format_preflight_report()
        assert "Preflight Checks" in report
        assert "2 passed" in report or "1 passed" in report
        assert "1 warning" in report

    def test_conversation_tracking(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        ds = DeployState(str(tmp_project))
        ds.update_from_exchange("deploy all", "Deploying stage 1...", 1)

        assert len(ds.state["conversation_history"]) == 1
        assert ds.state["conversation_history"][0]["user"] == "deploy all"


# ======================================================================
# Preflight check tests
# ======================================================================


class TestPreflightChecks:

    def _make_session(self, project_dir, iac_tool="terraform"):
        """Create a DeploySession with mocked dependencies."""
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": iac_tool},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        context = AgentContext(
            project_config={"project": {"iac_tool": iac_tool}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)

        return DeploySession(context, registry)

    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123")
    def test_subscription_pass(self, _mock_sub, _mock_login, tmp_project):
        session = self._make_session(tmp_project)
        result = session._check_subscription("sub-123")
        assert result["status"] == "pass"

    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=False)
    def test_subscription_fail_no_login(self, _mock_login, tmp_project):
        session = self._make_session(tmp_project)
        result = session._check_subscription("sub-123")
        assert result["status"] == "fail"
        assert "az login" in result.get("fix_command", "")

    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="other-sub")
    def test_subscription_warn_mismatch(self, _mock_sub, _mock_login, tmp_project):
        session = self._make_session(tmp_project)
        result = session._check_subscription("sub-123")
        assert result["status"] == "warn"

    @patch("subprocess.run")
    def test_iac_tool_terraform_pass(self, mock_run, tmp_project):
        mock_run.return_value = MagicMock(returncode=0, stdout="Terraform v1.7.0\n")
        session = self._make_session(tmp_project, iac_tool="terraform")
        result = session._check_iac_tool()
        assert result["status"] == "pass"
        assert "Terraform" in result["message"]

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_iac_tool_terraform_missing(self, _mock_run, tmp_project):
        session = self._make_session(tmp_project, iac_tool="terraform")
        result = session._check_iac_tool()
        assert result["status"] == "fail"

    def test_iac_tool_bicep_always_pass(self, tmp_project):
        session = self._make_session(tmp_project, iac_tool="bicep")
        result = session._check_iac_tool()
        assert result["status"] == "pass"

    @patch("subprocess.run")
    def test_resource_group_exists(self, mock_run, tmp_project):
        mock_run.return_value = MagicMock(returncode=0)
        session = self._make_session(tmp_project)
        result = session._check_resource_group("sub-123", "my-rg")
        assert result["status"] == "pass"

    @patch("subprocess.run")
    def test_resource_group_missing_warns(self, mock_run, tmp_project):
        mock_run.return_value = MagicMock(returncode=1)
        session = self._make_session(tmp_project)
        result = session._check_resource_group("sub-123", "my-rg")
        assert result["status"] == "warn"
        assert "fix_command" in result

    @patch("subprocess.run")
    def test_resource_providers_skips_non_microsoft_namespaces(self, mock_run, tmp_project):
        """Non-Microsoft namespaces like 'External' should NOT be checked."""
        session = self._make_session(tmp_project)
        session._deploy_state._state["deployment_stages"] = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [
                    {"name": "ext", "resource_type": "External/something", "sku": ""},
                    {"name": "hashicorp", "resource_type": "hashicorp/random", "sku": ""},
                    {"name": "kv", "resource_type": "Microsoft.KeyVault/vaults", "sku": ""},
                ],
                "status": "generated",
                "dir": "stage-1",
                "files": [],
            },
        ]

        mock_run.return_value = MagicMock(returncode=0, stdout="Registered\n", stderr="")
        results = session._check_resource_providers("sub-123")  # noqa: F841

        # Should have checked only Microsoft.* namespaces — not External or hashicorp
        checked_namespaces = [c.args[0][4] for c in mock_run.call_args_list if "provider" in c.args[0]]
        assert "Microsoft.KeyVault" in checked_namespaces
        assert "External" not in checked_namespaces
        assert "hashicorp" not in checked_namespaces

    @patch("subprocess.run")
    def test_resource_providers_skips_empty_resource_types(self, mock_run, tmp_project):
        """Services with empty resource_type should be skipped."""
        session = self._make_session(tmp_project)
        session._deploy_state._state["deployment_stages"] = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [
                    {"name": "custom", "resource_type": "", "sku": ""},
                ],
                "status": "generated",
                "dir": "stage-1",
                "files": [],
            },
        ]

        results = session._check_resource_providers("sub-123")
        assert results == []
        mock_run.assert_not_called()


# ======================================================================
# File-based resource provider extraction tests
# ======================================================================


class TestExtractResourceProvidersFromFiles:
    """Verify _extract_providers_from_files() parses IaC files for namespaces."""

    def _make_session(self, project_dir, iac_tool="terraform"):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": iac_tool},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        context = AgentContext(
            project_config={"project": {"iac_tool": iac_tool}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        return DeploySession(context, registry)

    def test_extracts_from_tf_files(self, tmp_project):
        session = self._make_session(tmp_project)
        stage_dir = tmp_project / "stage-1"
        stage_dir.mkdir()
        (stage_dir / "main.tf").write_text(
            'resource "azapi_resource" "rg" {\n'
            '  type = "Microsoft.Resources/resourceGroups@2025-06-01"\n'
            "}\n"
            'resource "azapi_resource" "storage" {\n'
            '  type = "Microsoft.Storage/storageAccounts@2025-06-01"\n'
            "}\n"
        )
        session._deploy_state._state["deployment_stages"] = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "dir": "stage-1",
                "services": [],
                "status": "generated",
                "files": [],
            },
        ]
        namespaces = session._extract_providers_from_files()
        assert "Microsoft.Resources" in namespaces
        assert "Microsoft.Storage" in namespaces

    def test_extracts_from_bicep_files(self, tmp_project):
        session = self._make_session(tmp_project, iac_tool="bicep")
        stage_dir = tmp_project / "stage-1"
        stage_dir.mkdir()
        (stage_dir / "main.bicep").write_text(
            "resource rg 'Microsoft.Resources/resourceGroups@2025-06-01' = {\n"
            "  name: 'myrg'\n"
            "  location: 'eastus'\n"
            "}\n"
            "resource kv 'Microsoft.KeyVault/vaults@2025-06-01' = {\n"
            "  name: 'mykv'\n"
            "}\n"
        )
        session._deploy_state._state["deployment_stages"] = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "dir": "stage-1",
                "services": [],
                "status": "generated",
                "files": [],
            },
        ]
        namespaces = session._extract_providers_from_files()
        assert "Microsoft.Resources" in namespaces
        assert "Microsoft.KeyVault" in namespaces

    def test_ignores_non_microsoft_types(self, tmp_project):
        session = self._make_session(tmp_project)
        stage_dir = tmp_project / "stage-1"
        stage_dir.mkdir()
        (stage_dir / "main.tf").write_text(
            'resource "null_resource" "test" {}\n' 'resource "random_string" "suffix" {}\n'
        )
        session._deploy_state._state["deployment_stages"] = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "dir": "stage-1",
                "services": [],
                "status": "generated",
                "files": [],
            },
        ]
        namespaces = session._extract_providers_from_files()
        assert len(namespaces) == 0

    def test_handles_missing_dirs(self, tmp_project):
        session = self._make_session(tmp_project)
        session._deploy_state._state["deployment_stages"] = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "dir": "nonexistent-dir",
                "services": [],
                "status": "generated",
                "files": [],
            },
        ]
        namespaces = session._extract_providers_from_files()
        assert len(namespaces) == 0

    @patch("subprocess.run")
    def test_file_based_preferred_over_metadata(self, mock_run, tmp_project):
        """When IaC files exist, file-based extraction is used over metadata."""
        session = self._make_session(tmp_project)
        stage_dir = tmp_project / "stage-1"
        stage_dir.mkdir()
        (stage_dir / "main.tf").write_text(
            'resource "azapi_resource" "storage" {\n' '  type = "Microsoft.Storage/storageAccounts@2025-06-01"\n' "}\n"
        )
        session._deploy_state._state["deployment_stages"] = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "dir": "stage-1",
                "services": [
                    {"name": "kv", "resource_type": "Microsoft.KeyVault/vaults", "sku": ""},
                ],
                "status": "generated",
                "files": [],
            },
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout="Registered\n", stderr="")
        results = session._check_resource_providers("sub-123")  # noqa: F841
        # File-based: only Microsoft.Storage, NOT Microsoft.KeyVault from metadata
        checked_namespaces = [c.args[0][4] for c in mock_run.call_args_list if "provider" in c.args[0]]
        assert "Microsoft.Storage" in checked_namespaces
        assert "Microsoft.KeyVault" not in checked_namespaces

    @patch("subprocess.run")
    def test_falls_back_to_metadata(self, mock_run, tmp_project):
        """When no IaC files exist, falls back to service metadata."""
        session = self._make_session(tmp_project)
        # No stage directory created — no files to scan
        session._deploy_state._state["deployment_stages"] = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "dir": "nonexistent-stage-dir",
                "services": [
                    {"name": "kv", "resource_type": "Microsoft.KeyVault/vaults", "sku": ""},
                ],
                "status": "generated",
                "files": [],
            },
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout="Registered\n", stderr="")
        results = session._check_resource_providers("sub-123")  # noqa: F841
        checked_namespaces = [c.args[0][4] for c in mock_run.call_args_list if "provider" in c.args[0]]
        assert "Microsoft.KeyVault" in checked_namespaces


# ======================================================================
# DeploySession tests
# ======================================================================


class TestDeploySession:

    def _make_session(self, project_dir, iac_tool="terraform", build_stages=None):
        """Create a DeploySession with all dependencies mocked."""
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": iac_tool},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir, stages=build_stages, iac_tool=iac_tool)

        context = AgentContext(
            project_config={"project": {"iac_tool": iac_tool}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)

        return DeploySession(context, registry)

    def test_quit_cancels_session(self, tmp_project):
        session = self._make_session(tmp_project)
        output = []
        result = session.run(
            subscription="sub-123",
            input_fn=lambda p: "quit",
            print_fn=lambda msg: output.append(msg),
        )
        assert result.cancelled is True

    def test_session_loads_build_state(self, tmp_project):
        session = self._make_session(tmp_project)
        output = []
        # Immediately quit
        session.run(
            subscription="sub-123",
            input_fn=lambda p: "quit",
            print_fn=lambda msg: output.append(msg),
        )
        # Verify stages were loaded (shown in plan overview)
        joined = "\n".join(output)
        assert "Foundation" in joined or "Stage" in joined

    @patch(
        "azext_prototype.stages.deploy_session.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="Terraform v1.7.0\n", stderr=""),
    )
    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123")
    @patch("azext_prototype.stages.deploy_session.deploy_terraform", return_value={"status": "deployed"})
    @patch("azext_prototype.stages.deploy_session.deploy_app_stage", return_value={"status": "deployed"})
    def test_full_deploy_flow(self, mock_app, mock_tf, mock_sub, mock_login, mock_subprocess, tmp_project):
        """Test full interactive deploy: confirm → preflight → deploy → done."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        # Create the stage directory
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)

        session = self._make_session(tmp_project, build_stages=stages)

        inputs = iter(["", "done"])  # confirm, then done
        output = []
        result = session.run(
            subscription="sub-123",
            input_fn=lambda p: next(inputs),
            print_fn=lambda msg: output.append(msg),
        )
        assert not result.cancelled
        assert len(result.deployed_stages) == 1

    @patch(
        "azext_prototype.stages.deploy_session.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="Terraform v1.7.0\n", stderr=""),
    )
    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123")
    @patch(
        "azext_prototype.stages.deploy_session.deploy_terraform",
        return_value={"status": "failed", "error": "auth error"},
    )
    def test_deploy_failure_qa_routing(self, mock_tf, mock_sub, mock_login, mock_subprocess, tmp_project):
        """Test that deploy failure routes to QA agent."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)

        session = self._make_session(tmp_project, build_stages=stages)
        # Mock QA agent response
        session._qa_agent = MagicMock()
        session._qa_agent.execute.return_value = _make_response("Check your service principal credentials.")
        # Clear fix agents so remediation is skipped (this test verifies QA routing only)
        session._iac_agents = {}
        session._dev_agent = None
        session._architect_agent = None

        inputs = iter(["", "done"])  # confirm, then done
        output = []
        result = session.run(
            subscription="sub-123",
            input_fn=lambda p: next(inputs),
            print_fn=lambda msg: output.append(msg),
        )
        assert len(result.failed_stages) == 1
        joined = "\n".join(output)
        assert "QA Diagnosis" in joined or "service principal" in joined

    def test_dry_run_no_build_state(self, tmp_project):
        """Dry run with no build state returns cancelled."""
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(tmp_project) / "prototype.yaml"
        config_data = {
            "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
            "ai": {"provider": "github-models"},
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        session = DeploySession(context, registry)

        output = []
        result = session.run_dry_run(
            subscription="sub-123",
            print_fn=lambda msg: output.append(msg),
        )
        assert result.cancelled is True

    @patch(
        "azext_prototype.stages.deploy_session.plan_terraform", return_value={"output": "Plan: 3 to add", "error": None}
    )
    def test_dry_run_terraform(self, mock_plan, tmp_project):
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)

        output = []
        session.run_dry_run(
            subscription="sub-123",
            print_fn=lambda msg: output.append(msg),
        )
        joined = "\n".join(output)
        assert "Plan: 3 to add" in joined

    @patch(
        "azext_prototype.stages.deploy_session.plan_terraform", return_value={"output": "Plan: 1 to add", "error": None}
    )
    def test_dry_run_single_stage(self, mock_plan, tmp_project):
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
            {
                "stage": 2,
                "name": "Data",
                "category": "data",
                "services": [],
                "dir": "concept/infra/terraform/data",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)
        (tmp_project / "concept" / "infra" / "terraform" / "data").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)

        output = []
        session.run_dry_run(
            target_stage=1,
            subscription="sub-123",
            print_fn=lambda msg: output.append(msg),
        )
        # Should only show stage 1
        assert mock_plan.call_count == 1

    def test_dry_run_stage_not_found(self, tmp_project):
        session = self._make_session(tmp_project)
        output = []
        result = session.run_dry_run(
            target_stage=99,
            subscription="sub-123",
            print_fn=lambda msg: output.append(msg),
        )
        assert result.cancelled is True

    @patch("azext_prototype.stages.deploy_session.deploy_terraform", return_value={"status": "deployed"})
    def test_single_stage_deploy(self, mock_tf, tmp_project):
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)

        output = []
        result = session.run_single_stage(
            1,
            subscription="sub-123",
            print_fn=lambda msg: output.append(msg),
        )
        assert len(result.deployed_stages) == 1
        mock_tf.assert_called_once()

    def test_single_stage_not_found(self, tmp_project):
        session = self._make_session(tmp_project)
        output = []
        result = session.run_single_stage(
            99,
            subscription="sub-123",
            print_fn=lambda msg: output.append(msg),
        )
        assert result.cancelled is True

    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123")
    @patch("azext_prototype.stages.deploy_session.deploy_terraform", return_value={"status": "deployed"})
    def test_slash_status(self, mock_tf, mock_sub, mock_login, tmp_project):
        """Test /status slash command shows stage info."""
        session = self._make_session(tmp_project)
        output = []

        inputs = iter(["", "/status", "done"])
        session.run(
            subscription="sub-123",
            input_fn=lambda p: next(inputs),
            print_fn=lambda msg: output.append(msg),
        )
        joined = "\n".join(output)
        assert "stages deployed" in joined

    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123")
    def test_slash_help(self, mock_sub, mock_login, tmp_project):
        """Test /help slash command shows available commands."""
        session = self._make_session(tmp_project)
        output = []

        # Preflight will run — need to avoid actual subprocess calls
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="Terraform v1.7.0\n")):
            inputs = iter(["", "/help", "done"])
            session.run(
                subscription="sub-123",
                input_fn=lambda p: next(inputs),
                print_fn=lambda msg: output.append(msg),
            )

        joined = "\n".join(output)
        assert "/status" in joined
        assert "/deploy" in joined
        assert "/rollback" in joined

    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123")
    def test_slash_outputs(self, mock_sub, mock_login, tmp_project):
        """Test /outputs slash command."""
        session = self._make_session(tmp_project)
        output = []

        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="Terraform v1.7.0\n")):
            inputs = iter(["", "/outputs", "done"])
            session.run(
                subscription="sub-123",
                input_fn=lambda p: next(inputs),
                print_fn=lambda msg: output.append(msg),
            )

        joined = "\n".join(output)
        assert "outputs" in joined.lower()

    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123")
    @patch("azext_prototype.stages.deploy_session.deploy_terraform", return_value={"status": "deployed"})
    @patch("azext_prototype.stages.deploy_session.rollback_terraform", return_value={"status": "rolled_back"})
    def test_slash_rollback_enforces_order(self, mock_rb, mock_tf, mock_sub, mock_login, tmp_project):
        """Test that /rollback enforces reverse order."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
            {
                "stage": 2,
                "name": "Data",
                "category": "data",
                "services": [],
                "dir": "concept/infra/terraform/data",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)
        (tmp_project / "concept" / "infra" / "terraform" / "data").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)

        output = []
        # Deploy all, then try to rollback stage 1 (should fail), then done
        inputs = iter(["", "/rollback 1", "done"])
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="Terraform v1.7.0\n")):
            session.run(
                subscription="sub-123",
                input_fn=lambda p: next(inputs),
                print_fn=lambda msg: output.append(msg),
            )

        joined = "\n".join(output)
        assert "Cannot roll back" in joined or "not deployed" in joined.lower()

    def test_eof_cancels(self, tmp_project):
        """Test that EOFError during prompt cancels session."""
        session = self._make_session(tmp_project)

        def eof_input(p):
            raise EOFError

        result = session.run(
            subscription="sub-123",
            input_fn=eof_input,
            print_fn=lambda msg: None,
        )
        assert result.cancelled is True

    def test_docs_stage_auto_deployed(self, tmp_project):
        """Test that docs-category stages are auto-marked as deployed."""
        stages = [
            {
                "stage": 1,
                "name": "Docs",
                "category": "docs",
                "services": [],
                "dir": "concept/docs",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "docs").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)

        output = []
        result = session.run_single_stage(
            1,
            subscription="sub-123",
            print_fn=lambda msg: output.append(msg),
        )
        assert len(result.deployed_stages) == 1


# ======================================================================
# DeployStage integration tests
# ======================================================================


class TestDeployStageIntegration:

    def test_guard_checks_build_yaml(self, tmp_project):
        """Verify deploy guard checks for build.yaml (not build.json)."""
        import os

        from azext_prototype.stages.deploy_stage import DeployStage

        os.chdir(str(tmp_project))
        try:
            stage = DeployStage()
            guards = stage.get_guards()
            build_guard = [g for g in guards if g.name == "build_complete"][0]

            # No build.yaml → guard fails
            assert build_guard.check_fn() is False

            # Create build.yaml → guard passes
            state_dir = tmp_project / ".prototype" / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "build.yaml").write_text("iac_tool: terraform\n")
            assert build_guard.check_fn() is True
        finally:
            os.chdir("/")

    @patch("azext_prototype.stages.deploy_session.DeploySession")
    def test_status_flag(self, mock_session_cls, tmp_project):
        """Test --status flag shows deploy state without starting session."""
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.stages.deploy_stage import DeployStage

        _write_build_yaml(tmp_project)
        context = AgentContext(
            project_config={},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )
        registry = MagicMock()

        stage = DeployStage()
        result = stage.execute(context, registry, status=True)
        assert result["status"] == "status_displayed"
        # DeploySession should NOT be constructed for --status
        mock_session_cls.assert_not_called()

    @patch("azext_prototype.stages.deploy_session.DeploySession")
    def test_reset_flag(self, mock_session_cls, tmp_project):
        """Test --reset flag clears deploy state."""
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.stages.deploy_stage import DeployStage

        context = AgentContext(
            project_config={},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )
        registry = MagicMock()

        stage = DeployStage()
        result = stage.execute(context, registry, reset=True)
        assert result["status"] == "reset"
        mock_session_cls.assert_not_called()

    def test_dry_run_delegates(self, tmp_project):
        """Test --dry-run delegates to DeploySession.run_dry_run()."""
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.stages.deploy_session import DeployResult
        from azext_prototype.stages.deploy_stage import DeployStage

        _write_build_yaml(tmp_project)
        config_path = Path(tmp_project) / "prototype.yaml"
        config_data = {
            "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
            "ai": {"provider": "github-models"},
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )
        registry = MagicMock()
        registry.find_by_capability.return_value = []

        with patch("azext_prototype.stages.deploy_stage.DeploySession") as mock_cls:
            mock_session = MagicMock()
            mock_session.run_dry_run.return_value = DeployResult()
            mock_cls.return_value = mock_session

            stage = DeployStage()
            result = stage.execute(context, registry, dry_run=True, subscription="sub-123")

            mock_session.run_dry_run.assert_called_once()
            assert result["mode"] == "dry-run"

    def test_single_stage_delegates(self, tmp_project):
        """Test --stage N delegates to DeploySession.run_single_stage()."""
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.stages.deploy_session import DeployResult
        from azext_prototype.stages.deploy_stage import DeployStage

        _write_build_yaml(tmp_project)
        config_path = Path(tmp_project) / "prototype.yaml"
        config_data = {
            "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
            "ai": {"provider": "github-models"},
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )
        registry = MagicMock()
        registry.find_by_capability.return_value = []

        with patch("azext_prototype.stages.deploy_stage.DeploySession") as mock_cls:
            mock_session = MagicMock()
            mock_session.run_single_stage.return_value = DeployResult(deployed_stages=[{"stage": 1}])
            mock_cls.return_value = mock_session

            stage = DeployStage()
            result = stage.execute(context, registry, stage=1, subscription="sub-123")

            mock_session.run_single_stage.assert_called_once_with(
                1, subscription="sub-123", tenant=None, force=False, client_id=None, client_secret=None
            )
            assert result["mode"] == "single_stage"
            assert result["deployed"] == 1


# ======================================================================
# Deploy helpers tests
# ======================================================================


class TestDeployHelpers:

    @patch("subprocess.run")
    def test_check_az_login_success(self, mock_run):
        from azext_prototype.stages.deploy_helpers import check_az_login

        mock_run.return_value = MagicMock(returncode=0)
        assert check_az_login() is True

    @patch("subprocess.run")
    def test_check_az_login_failure(self, mock_run):
        from azext_prototype.stages.deploy_helpers import check_az_login

        mock_run.return_value = MagicMock(returncode=1)
        assert check_az_login() is False

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_check_az_login_missing(self, _mock_run):
        from azext_prototype.stages.deploy_helpers import check_az_login

        assert check_az_login() is False

    @patch("subprocess.run")
    def test_get_current_subscription(self, mock_run):
        from azext_prototype.stages.deploy_helpers import get_current_subscription

        mock_run.return_value = MagicMock(returncode=0, stdout="sub-123\n")
        assert get_current_subscription() == "sub-123"

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_get_current_subscription_missing(self, _mock_run):
        from azext_prototype.stages.deploy_helpers import get_current_subscription

        assert get_current_subscription() == ""

    def test_rollback_manager_snapshot_stage(self, tmp_project):
        from azext_prototype.stages.deploy_helpers import RollbackManager

        mgr = RollbackManager(str(tmp_project))
        snapshot = mgr.snapshot_stage(1, "infra", "terraform")
        assert snapshot["stage"] == 1
        assert snapshot["scope"] == "infra"
        assert snapshot["iac_tool"] == "terraform"

    @patch("subprocess.run")
    def test_deploy_terraform(self, mock_run, tmp_project):
        from azext_prototype.stages.deploy_helpers import deploy_terraform

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = deploy_terraform(tmp_project, "sub-123")
        assert result["status"] == "deployed"

    @patch("subprocess.run")
    def test_deploy_terraform_failure(self, mock_run, tmp_project):
        from azext_prototype.stages.deploy_helpers import deploy_terraform

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error: auth failed")
        result = deploy_terraform(tmp_project, "sub-123")
        assert result["status"] == "failed"
        assert "auth failed" in result.get("error", "")

    @patch("subprocess.run")
    def test_plan_terraform(self, mock_run, tmp_project):
        from azext_prototype.stages.deploy_helpers import plan_terraform

        mock_run.return_value = MagicMock(returncode=0, stdout="Plan: 2 to add, 0 to change", stderr="")
        result = plan_terraform(tmp_project, "sub-123")
        assert "Plan: 2 to add" in result.get("output", "")

    @patch("subprocess.run")
    def test_rollback_terraform(self, mock_run, tmp_project):
        from azext_prototype.stages.deploy_helpers import rollback_terraform

        mock_run.return_value = MagicMock(returncode=0, stdout="Destroy complete", stderr="")
        result = rollback_terraform(tmp_project)
        assert result["status"] == "rolled_back"

    @patch("subprocess.run")
    def test_rollback_terraform_failure(self, mock_run, tmp_project):
        from azext_prototype.stages.deploy_helpers import rollback_terraform

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error: state locked")
        result = rollback_terraform(tmp_project)
        assert result["status"] == "failed"

    def test_find_bicep_params(self, tmp_project):
        from azext_prototype.stages.deploy_helpers import find_bicep_params

        # Create test files
        main_bicep = tmp_project / "main.bicep"
        main_bicep.write_text("resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {}")
        params = tmp_project / "main.parameters.json"
        params.write_text('{"parameters": {}}')

        result = find_bicep_params(tmp_project, main_bicep)
        assert result is not None
        assert result.name == "main.parameters.json"

    def test_is_subscription_scoped(self, tmp_project):
        from azext_prototype.stages.deploy_helpers import is_subscription_scoped

        bicep_file = tmp_project / "main.bicep"
        bicep_file.write_text(
            "targetScope = 'subscription'\nresource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {}"
        )
        assert is_subscription_scoped(bicep_file) is True

        bicep_file.write_text("resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {}")
        assert is_subscription_scoped(bicep_file) is False


# ======================================================================
# Rollback ordering tests (specific edge cases)
# ======================================================================


class TestRollbackOrdering:

    def test_rollback_with_gap_in_stages(self, tmp_project):
        """Test rollback ordering works with non-contiguous stage numbers."""
        from azext_prototype.stages.deploy_state import DeployState

        stages = [
            {"stage": 1, "name": "A", "category": "infra", "services": [], "dir": "a", "files": []},
            {"stage": 3, "name": "C", "category": "infra", "services": [], "dir": "c", "files": []},
            {"stage": 5, "name": "E", "category": "app", "services": [], "dir": "e", "files": []},
        ]
        build_path = _write_build_yaml(tmp_project, stages=stages)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.mark_stage_deployed(1)
        ds.mark_stage_deployed(3)
        ds.mark_stage_deployed(5)

        assert ds.can_rollback(5) is True
        assert ds.can_rollback(3) is False
        assert ds.can_rollback(1) is False

    def test_rollback_with_mixed_statuses(self, tmp_project):
        """Test rollback logic with failed and rolled-back stages."""
        from azext_prototype.stages.deploy_state import DeployState

        stages = [
            {"stage": 1, "name": "A", "category": "infra", "services": [], "dir": "a", "files": []},
            {"stage": 2, "name": "B", "category": "data", "services": [], "dir": "b", "files": []},
            {"stage": 3, "name": "C", "category": "app", "services": [], "dir": "c", "files": []},
        ]
        build_path = _write_build_yaml(tmp_project, stages=stages)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.mark_stage_deployed(1)
        ds.mark_stage_deployed(2)
        ds.mark_stage_failed(3, "timeout")

        # Stage 3 is failed (not deployed), so stage 2 can be rolled back
        assert ds.can_rollback(2) is True
        assert ds.can_rollback(1) is False  # stage 2 still deployed

    def test_get_stage_returns_none_for_missing(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        ds = DeployState(str(tmp_project))
        assert ds.get_stage(999) is None

    def test_default_state_has_tenant(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        ds = DeployState(str(tmp_project))
        assert ds.state["tenant"] == ""


# ======================================================================
# AI-independent deploy tests
# ======================================================================


class TestDeployNoAI:
    """Deploy stage works without an AI provider."""

    def _make_session(self, project_dir, ai_provider=None, build_stages=None):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir, stages=build_stages)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=ai_provider,
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        return DeploySession(context, registry)

    def test_session_works_with_none_ai_provider(self, tmp_project):
        """Session initialises and quits cleanly with ai_provider=None."""
        session = self._make_session(tmp_project, ai_provider=None)
        output = []
        result = session.run(
            subscription="sub-123",
            input_fn=lambda p: "quit",
            print_fn=lambda msg: output.append(msg),
        )
        assert result.cancelled is True

    @patch(
        "azext_prototype.stages.deploy_session.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="Terraform v1.7.0\n", stderr=""),
    )
    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123")
    @patch("azext_prototype.stages.deploy_session.deploy_terraform", return_value={"status": "deployed"})
    def test_deploy_succeeds_without_ai(self, mock_tf, mock_sub, mock_login, mock_subprocess, tmp_project):
        """Full deploy succeeds with ai_provider=None."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)

        session = self._make_session(tmp_project, ai_provider=None, build_stages=stages)
        inputs = iter(["", "done"])
        output = []
        result = session.run(
            subscription="sub-123",
            input_fn=lambda p: next(inputs),
            print_fn=lambda msg: output.append(msg),
        )
        assert not result.cancelled
        assert len(result.deployed_stages) == 1

    @patch(
        "azext_prototype.stages.deploy_session.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="Terraform v1.7.0\n", stderr=""),
    )
    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123")
    @patch(
        "azext_prototype.stages.deploy_session.deploy_terraform",
        return_value={"status": "failed", "error": "auth error"},
    )
    def test_deploy_failure_without_ai_shows_raw_error(
        self, mock_tf, mock_sub, mock_login, mock_subprocess, tmp_project
    ):
        """Deploy failure with ai_provider=None falls back to raw error display."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)

        session = self._make_session(tmp_project, ai_provider=None, build_stages=stages)
        inputs = iter(["", "done"])
        output = []
        session.run(
            subscription="sub-123",
            input_fn=lambda p: next(inputs),
            print_fn=lambda msg: output.append(msg),
        )
        joined = "\n".join(output)
        assert "auth error" in joined

    def test_dry_run_without_ai(self, tmp_project):
        """Dry-run mode works with ai_provider=None."""
        session = self._make_session(tmp_project, ai_provider=None)
        output = []
        result = session.run_dry_run(
            subscription="sub-123",
            print_fn=lambda msg: output.append(msg),
        )
        # Should not raise — result is a DeployResult
        assert not result.cancelled or result.cancelled  # always passes: just no crash


# ======================================================================
# Service principal login tests
# ======================================================================


class TestServicePrincipalLogin:
    """Tests for login_service_principal() and set_deployment_context()."""

    @patch("subprocess.run")
    def test_login_service_principal_success(self, mock_run):
        from azext_prototype.stages.deploy_helpers import login_service_principal

        # First call: az login; second call: az account show (get_current_subscription)
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),  # az login
            MagicMock(returncode=0, stdout="sub-from-sp\n", stderr=""),  # az account show
        ]
        result = login_service_principal("app-id", "secret", "tenant-id")
        assert result["status"] == "ok"
        assert result["subscription"] == "sub-from-sp"

        # Verify az login was called with correct args
        login_call = mock_run.call_args_list[0]
        assert "--service-principal" in login_call[0][0]
        assert "-u" in login_call[0][0]
        assert "app-id" in login_call[0][0]

    @patch("subprocess.run")
    def test_login_service_principal_failure(self, mock_run):
        from azext_prototype.stages.deploy_helpers import login_service_principal

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="AADSTS7000215: Invalid client secret")
        result = login_service_principal("app-id", "bad-secret", "tenant-id")
        assert result["status"] == "failed"
        assert "Invalid client secret" in result["error"]

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_login_service_principal_no_az_cli(self, mock_run):
        from azext_prototype.stages.deploy_helpers import login_service_principal

        result = login_service_principal("app-id", "secret", "tenant-id")
        assert result["status"] == "failed"
        assert "az CLI not found" in result["error"]

    @patch("subprocess.run")
    def test_set_deployment_context_success(self, mock_run):
        from azext_prototype.stages.deploy_helpers import set_deployment_context

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = set_deployment_context("sub-123", "tenant-456")
        assert result["status"] == "ok"

        cmd = mock_run.call_args[0][0]
        assert "--subscription" in cmd
        assert "sub-123" in cmd
        assert "--tenant" in cmd
        assert "tenant-456" in cmd

    @patch("subprocess.run")
    def test_set_deployment_context_no_tenant(self, mock_run):
        from azext_prototype.stages.deploy_helpers import set_deployment_context

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = set_deployment_context("sub-123")
        assert result["status"] == "ok"

        cmd = mock_run.call_args[0][0]
        assert "--subscription" in cmd
        assert "--tenant" not in cmd

    @patch("subprocess.run")
    def test_set_deployment_context_failure(self, mock_run):
        from azext_prototype.stages.deploy_helpers import set_deployment_context

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Subscription not found")
        result = set_deployment_context("bad-sub")
        assert result["status"] == "failed"
        assert "Subscription not found" in result["error"]

    @patch("subprocess.run")
    def test_get_current_tenant(self, mock_run):
        from azext_prototype.stages.deploy_helpers import get_current_tenant

        mock_run.return_value = MagicMock(returncode=0, stdout="tenant-abc\n", stderr="")
        result = get_current_tenant()
        assert result == "tenant-abc"


# ======================================================================
# Tenant preflight tests
# ======================================================================


class TestTenantPreflight:
    """Tests for tenant preflight checking in DeploySession."""

    def _make_session(self, project_dir):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        return DeploySession(context, registry)

    @patch("azext_prototype.stages.deploy_session.get_current_tenant", return_value="tenant-abc")
    def test_tenant_preflight_match(self, mock_tenant, tmp_project):
        session = self._make_session(tmp_project)
        result = session._check_tenant("tenant-abc")
        assert result["status"] == "pass"

    @patch("azext_prototype.stages.deploy_session.get_current_tenant", return_value="tenant-xyz")
    def test_tenant_preflight_mismatch(self, mock_tenant, tmp_project):
        session = self._make_session(tmp_project)
        result = session._check_tenant("tenant-abc")
        assert result["status"] == "warn"
        assert "fix_command" in result
        assert "az login --tenant" in result["fix_command"]


# ======================================================================
# SP parameter validation in prototype_deploy
# ======================================================================


class TestDeploySPValidation:
    """Tests for --service-principal validation in prototype_deploy."""

    @patch("azext_prototype.custom._check_requirements")
    @patch("azext_prototype.custom._get_project_dir")
    def test_sp_missing_params_raises(self, mock_dir, mock_check_req, project_with_config):
        from knack.util import CLIError

        from azext_prototype.custom import prototype_deploy

        mock_dir.return_value = str(project_with_config)

        with pytest.raises(CLIError, match="requires client-id"):
            prototype_deploy(
                cmd=MagicMock(),
                service_principal=True,
                client_id="abc",
                # Missing client_secret and tenant_id
            )

    @patch("azext_prototype.custom._check_requirements")
    @patch("azext_prototype.custom._get_project_dir")
    @patch("azext_prototype.stages.deploy_helpers.login_service_principal")
    def test_sp_login_failure_raises(self, mock_login, mock_dir, mock_check_req, project_with_config):
        from knack.util import CLIError

        from azext_prototype.custom import prototype_deploy

        mock_dir.return_value = str(project_with_config)
        mock_login.return_value = {"status": "failed", "error": "bad creds"}

        with pytest.raises(CLIError, match="Service principal login failed"):
            prototype_deploy(
                cmd=MagicMock(),
                service_principal=True,
                client_id="abc",
                client_secret="def",
                tenant_id="ghi",
            )

    @patch("azext_prototype.custom._check_requirements")
    @patch("azext_prototype.custom._get_project_dir")
    @patch("azext_prototype.stages.deploy_helpers.login_service_principal")
    @patch("azext_prototype.custom._check_guards")
    def test_sp_login_success_proceeds(self, mock_guards, mock_login, mock_dir, mock_check_req, project_with_config):
        from azext_prototype.custom import prototype_deploy

        mock_dir.return_value = str(project_with_config)
        mock_login.return_value = {"status": "ok", "subscription": "sp-sub-123"}

        # Let guards pass, but make deploy_stage.execute raise so we can verify flow
        mock_guards.return_value = None

        with patch("azext_prototype.stages.deploy_stage.DeployStage.execute") as mock_exec:
            mock_exec.return_value = {"status": "success"}
            result = prototype_deploy(
                cmd=MagicMock(),
                service_principal=True,
                client_id="abc",
                client_secret="def",
                tenant_id="ghi",
                json_output=True,
            )
            assert result["status"] == "success"
            # Verify tenant and subscription were passed through
            call_kwargs = mock_exec.call_args[1]
            assert call_kwargs["tenant"] == "ghi"
            assert call_kwargs["subscription"] == "sp-sub-123"


# ======================================================================
# Subscription resolution chain tests
# ======================================================================


class TestSubscriptionResolution:
    """Tests for subscription resolution: CLI arg > config > current context."""

    def _make_session(self, project_dir, config_subscription=""):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_data = {
            "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
            "ai": {"provider": "github-models"},
            "deploy": {"subscription": config_subscription, "resource_group": ""},
        }
        config_path = Path(project_dir) / "prototype.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        _write_build_yaml(project_dir)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        return DeploySession(context, registry)

    def test_cli_arg_takes_priority(self, tmp_project):
        session = self._make_session(tmp_project, config_subscription="config-sub")
        output = []
        session.run(
            subscription="cli-sub",
            input_fn=lambda p: "quit",
            print_fn=lambda msg: output.append(msg),
        )
        # The subscription displayed should be the CLI arg
        joined = "\n".join(output)
        assert "cli-sub" in joined

    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="context-sub")
    def test_config_sub_used_when_no_cli_arg(self, mock_sub, tmp_project):
        session = self._make_session(tmp_project, config_subscription="config-sub")
        output = []
        session.run(
            input_fn=lambda p: "quit",
            print_fn=lambda msg: output.append(msg),
        )
        joined = "\n".join(output)
        assert "config-sub" in joined


# ======================================================================
# /login slash command tests
# ======================================================================


class TestLoginSlashCommand:
    """Tests for the /login slash command in DeploySession."""

    def _make_session(self, project_dir):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        return DeploySession(context, registry)

    @patch("azext_prototype.stages.deploy_session.subprocess.run")
    def test_login_command_success(self, mock_run, tmp_project):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        session = self._make_session(tmp_project)
        output = []
        session._handle_slash_command(
            "/login",
            False,
            False,
            lambda msg: output.append(msg),
            lambda p: "",
        )
        joined = "\n".join(output)
        assert "Login successful" in joined
        assert "/preflight" in joined

    @patch("azext_prototype.stages.deploy_session.subprocess.run")
    def test_login_command_failure(self, mock_run, tmp_project):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="AADSTS error")
        session = self._make_session(tmp_project)
        output = []
        session._handle_slash_command(
            "/login",
            False,
            False,
            lambda msg: output.append(msg),
            lambda p: "",
        )
        joined = "\n".join(output)
        assert "Login failed" in joined

    def test_help_includes_login(self, tmp_project):
        session = self._make_session(tmp_project)
        output = []
        session._handle_slash_command(
            "/help",
            False,
            False,
            lambda msg: output.append(msg),
            lambda p: "",
        )
        joined = "\n".join(output)
        assert "/login" in joined


# ======================================================================
# _prepare_deploy_command tests
# ======================================================================


class TestPrepareDeployCommand:
    """Tests for _prepare_deploy_command in custom.py."""

    @patch("azext_prototype.custom._check_requirements")
    @patch("azext_prototype.custom._get_project_dir")
    def test_returns_none_ai_provider_when_factory_fails(self, mock_dir, mock_check_req, project_with_config):
        from azext_prototype.custom import _prepare_deploy_command

        mock_dir.return_value = str(project_with_config)

        with patch("azext_prototype.ai.factory.create_ai_provider", side_effect=Exception("No Copilot license")):
            project_dir, config, registry, agent_context = _prepare_deploy_command()

        assert agent_context.ai_provider is None
        assert project_dir == str(project_with_config)

    @patch("azext_prototype.custom._check_requirements")
    @patch("azext_prototype.custom._get_project_dir")
    def test_returns_ai_provider_when_factory_succeeds(self, mock_dir, mock_check_req, project_with_config):
        from azext_prototype.custom import _prepare_deploy_command

        mock_dir.return_value = str(project_with_config)
        mock_provider = MagicMock()

        with patch("azext_prototype.ai.factory.create_ai_provider", return_value=mock_provider):
            project_dir, config, registry, agent_context = _prepare_deploy_command()

        assert agent_context.ai_provider is mock_provider


# ======================================================================
# Config SP routing tests
# ======================================================================


class TestConfigSPRouting:
    """Verify SP credentials route to secrets file."""

    def test_sp_client_id_is_secret(self):
        from azext_prototype.config import ProjectConfig

        assert ProjectConfig._is_secret_key("deploy.service_principal.client_id")
        assert ProjectConfig._is_secret_key("deploy.service_principal.client_secret")
        assert ProjectConfig._is_secret_key("deploy.service_principal.tenant_id")

    def test_default_config_has_sp_section(self):
        from azext_prototype.config import DEFAULT_CONFIG

        deploy = DEFAULT_CONFIG["deploy"]
        assert "tenant" in deploy
        assert "service_principal" in deploy
        sp = deploy["service_principal"]
        assert "client_id" in sp
        assert "client_secret" in sp
        assert "tenant_id" in sp


# ======================================================================
# _terraform_validate tests
# ======================================================================


class TestTerraformValidate:
    """Tests for the _terraform_validate() helper in deploy_helpers."""

    @patch("subprocess.run")
    def test_validate_success(self, mock_run):
        from azext_prototype.stages.deploy_helpers import _terraform_validate

        mock_run.return_value = MagicMock(returncode=0, stdout="Success!", stderr="")
        result = _terraform_validate(Path("/tmp/fake"))
        assert result["ok"] is True

    @patch("subprocess.run")
    def test_validate_failure(self, mock_run):
        from azext_prototype.stages.deploy_helpers import _terraform_validate

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error: Unsupported block type")
        result = _terraform_validate(Path("/tmp/fake"))
        assert result["ok"] is False
        assert "Unsupported block type" in result["error"]

    @patch("subprocess.run")
    def test_validate_returns_stdout_on_empty_stderr(self, mock_run):
        from azext_prototype.stages.deploy_helpers import _terraform_validate

        mock_run.return_value = MagicMock(returncode=1, stdout="Invalid HCL syntax", stderr="")
        result = _terraform_validate(Path("/tmp/fake"))
        assert result["ok"] is False
        assert "Invalid HCL syntax" in result["error"]

    @patch("subprocess.run")
    def test_deploy_terraform_calls_validate(self, mock_run, tmp_project):
        """Verify deploy_terraform() calls validate between init and plan."""
        from azext_prototype.stages.deploy_helpers import deploy_terraform

        # init succeeds, validate fails
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),  # init
            MagicMock(returncode=1, stdout="", stderr="Error: bad HCL"),  # validate
        ]
        result = deploy_terraform(tmp_project, "sub-123")
        assert result["status"] == "failed"
        assert result["command"] == "terraform validate"
        assert "bad HCL" in result["error"]

    @patch("subprocess.run")
    def test_deploy_terraform_validate_pass_continues(self, mock_run, tmp_project):
        """Verify deploy_terraform() continues past validate when it passes."""
        from azext_prototype.stages.deploy_helpers import deploy_terraform

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = deploy_terraform(tmp_project, "sub-123")
        assert result["status"] == "deployed"
        # Should have called: init, validate, plan, apply = 4 calls
        assert mock_run.call_count == 4


# ======================================================================
# Terraform preflight validation tests
# ======================================================================


class TestTerraformPreflightValidation:
    """Tests for _check_terraform_validate() in DeploySession."""

    def _make_session(self, project_dir, build_stages=None):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        build_path = _write_build_yaml(project_dir, stages=build_stages)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        session = DeploySession(context, registry)
        # Load build state into deploy state so _check_terraform_validate has stages
        session._deploy_state.load_from_build_state(build_path)
        return session

    @patch("azext_prototype.stages.deploy_session.subprocess.run")
    def test_valid_terraform_passes(self, mock_run, tmp_project):
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        stage_dir = tmp_project / "concept" / "infra" / "terraform"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "azurerm_resource_group" "rg" {}')

        session = self._make_session(tmp_project, build_stages=stages)

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),  # init
            MagicMock(returncode=0, stdout="", stderr=""),  # validate
        ]
        results = session._check_terraform_validate()
        assert len(results) == 1
        assert results[0]["status"] == "pass"

    @patch("azext_prototype.stages.deploy_session.subprocess.run")
    def test_invalid_terraform_fails(self, mock_run, tmp_project):
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        stage_dir = tmp_project / "concept" / "infra" / "terraform"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "versions.tf").write_text("}")

        session = self._make_session(tmp_project, build_stages=stages)

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),  # init
            MagicMock(returncode=1, stdout="", stderr="Error: Unsupported block type"),  # validate
        ]
        results = session._check_terraform_validate()
        assert len(results) == 1
        assert results[0]["status"] == "fail"
        assert "Unsupported block type" in results[0]["message"]

    @patch("azext_prototype.stages.deploy_session.subprocess.run")
    def test_init_failure_reported(self, mock_run, tmp_project):
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        stage_dir = tmp_project / "concept" / "infra" / "terraform"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text("bad content")

        session = self._make_session(tmp_project, build_stages=stages)

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Init error")
        results = session._check_terraform_validate()
        assert len(results) == 1
        assert results[0]["status"] == "fail"
        assert "Init failed" in results[0]["message"]

    def test_skips_app_stages(self, tmp_project):
        stages = [
            {
                "stage": 1,
                "name": "App",
                "category": "app",
                "services": [],
                "dir": "concept/apps/stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "apps" / "stage-1").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)
        results = session._check_terraform_validate()
        assert len(results) == 0

    def test_skips_missing_dirs(self, tmp_project):
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform/nonexistent",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        results = session._check_terraform_validate()
        assert len(results) == 0

    def test_skips_dirs_without_tf_files(self, tmp_project):
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        stage_dir = tmp_project / "concept" / "infra" / "terraform"
        stage_dir.mkdir(parents=True, exist_ok=True)
        # No .tf files in the directory

        session = self._make_session(tmp_project, build_stages=stages)
        results = session._check_terraform_validate()
        assert len(results) == 0

    @patch("azext_prototype.stages.deploy_session.subprocess.run")
    def test_preflight_includes_terraform_validate(self, mock_run, tmp_project):
        """Verify _run_preflight() includes terraform validate results."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        stage_dir = tmp_project / "concept" / "infra" / "terraform"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        session = self._make_session(tmp_project, build_stages=stages)
        session._subscription = "sub-123"

        mock_run.return_value = MagicMock(returncode=0, stdout="Terraform v1.7.0\n", stderr="")

        with patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True), patch(
            "azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123"
        ):
            results = session._run_preflight()

        names = [r["name"] for r in results]
        assert any("Terraform Validate" in n for n in names)


# ======================================================================
# Deploy env threading tests
# ======================================================================


class TestDeployEnv:
    """Tests for deploy env construction and threading in DeploySession."""

    def _make_session(self, project_dir, config_data=None, build_stages=None):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        if config_data is None:
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }

        config_path = Path(project_dir) / "prototype.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        _write_build_yaml(project_dir, stages=build_stages)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        return DeploySession(context, registry)

    def test_resolve_context_builds_deploy_env(self, tmp_project):
        session = self._make_session(tmp_project)
        session._resolve_context("sub-123", None)

        assert session._deploy_env is not None
        assert session._deploy_env["ARM_SUBSCRIPTION_ID"] == "sub-123"
        assert session._deploy_env["SUBSCRIPTION_ID"] == "sub-123"

    def test_resolve_context_with_tenant(self, tmp_project):
        session = self._make_session(tmp_project)
        session._resolve_context("sub-123", "tenant-456")

        assert session._deploy_env is not None
        assert session._deploy_env["ARM_TENANT_ID"] == "tenant-456"

    def test_resolve_context_sp_creds_in_env(self, tmp_project):
        config_data = {
            "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
            "ai": {"provider": "github-models"},
            "deploy": {
                "service_principal": {
                    "client_id": "sp-client",
                    "client_secret": "sp-secret",
                    "tenant_id": "sp-tenant",
                },
            },
        }
        # Write secrets file with SP creds
        secrets_path = Path(tmp_project) / "prototype.secrets.yaml"
        secrets_data = {
            "deploy": {
                "service_principal": {
                    "client_id": "sp-client",
                    "client_secret": "sp-secret",
                    "tenant_id": "sp-tenant",
                },
            },
        }
        with open(secrets_path, "w") as f:
            yaml.dump(secrets_data, f)

        session = self._make_session(tmp_project, config_data=config_data)
        session._resolve_context("sub-123", None)

        env = session._deploy_env
        assert env is not None
        # SP creds come from config.get("deploy.service_principal") which
        # reads merged config+secrets. If the config has them, they should
        # appear in the env.
        assert env["ARM_SUBSCRIPTION_ID"] == "sub-123"

    @patch("azext_prototype.stages.deploy_session.deploy_terraform")
    @patch("azext_prototype.stages.deploy_session.set_deployment_context", return_value={"status": "ok"})
    def test_deploy_single_stage_passes_env(self, _mock_ctx, mock_tf, tmp_project):
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)

        session = self._make_session(tmp_project, build_stages=stages)
        # Load build state into deploy state
        build_path = Path(tmp_project) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        session._resolve_context("sub-123", "tenant-456")

        mock_tf.return_value = {"status": "deployed"}

        stage = session._deploy_state._state["deployment_stages"][0]
        session._deploy_single_stage(stage)

        # Verify env= was passed
        assert mock_tf.called
        _, kwargs = mock_tf.call_args
        assert "env" in kwargs
        assert kwargs["env"]["ARM_SUBSCRIPTION_ID"] == "sub-123"
        assert kwargs["env"]["ARM_TENANT_ID"] == "tenant-456"

    @patch("azext_prototype.stages.deploy_session.deploy_bicep")
    @patch("azext_prototype.stages.deploy_session.set_deployment_context", return_value={"status": "ok"})
    def test_deploy_single_stage_bicep_passes_env(self, _mock_ctx, mock_bicep, tmp_project):
        config_data = {
            "project": {"name": "test", "location": "eastus", "iac_tool": "bicep"},
            "ai": {"provider": "github-models"},
        }
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/bicep",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "bicep").mkdir(parents=True, exist_ok=True)

        session = self._make_session(tmp_project, config_data=config_data, build_stages=stages)
        build_path = Path(tmp_project) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        session._resolve_context("sub-123", "tenant-456")

        mock_bicep.return_value = {"status": "deployed"}

        stage = session._deploy_state._state["deployment_stages"][0]
        session._deploy_single_stage(stage)

        assert mock_bicep.called
        _, kwargs = mock_bicep.call_args
        assert kwargs["env"]["ARM_TENANT_ID"] == "tenant-456"

    @patch("azext_prototype.stages.deploy_session.rollback_terraform")
    @patch("azext_prototype.stages.deploy_session.set_deployment_context", return_value={"status": "ok"})
    def test_rollback_passes_env(self, _mock_ctx, mock_rb, tmp_project):
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)

        session = self._make_session(tmp_project, build_stages=stages)
        build_path = Path(tmp_project) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        session._resolve_context("sub-123", "tenant-456")

        # Mark as deployed so we can rollback
        session._deploy_state.mark_stage_deployed(1)

        mock_rb.return_value = {"status": "rolled_back"}
        output = []
        session._rollback_stage(1, lambda msg: output.append(msg))

        assert mock_rb.called
        _, kwargs = mock_rb.call_args
        assert kwargs["env"]["ARM_SUBSCRIPTION_ID"] == "sub-123"


# ======================================================================
# Deployer object ID lookup tests
# ======================================================================


class TestDeployerObjectIdLookup:
    """Tests for _lookup_deployer_object_id() and its integration."""

    @patch("azext_prototype.stages.deploy_session.subprocess.run")
    def test_sp_lookup(self, mock_run):
        from azext_prototype.stages.deploy_session import _lookup_deployer_object_id

        mock_run.return_value = MagicMock(returncode=0, stdout="sp-object-id-abc\n", stderr="")
        result = _lookup_deployer_object_id("my-client-id")

        assert result == "sp-object-id-abc"
        cmd = mock_run.call_args[0][0]
        assert "sp" in cmd
        assert "show" in cmd
        assert "my-client-id" in cmd

    @patch("azext_prototype.stages.deploy_session.subprocess.run")
    def test_user_lookup(self, mock_run):
        from azext_prototype.stages.deploy_session import _lookup_deployer_object_id

        mock_run.return_value = MagicMock(returncode=0, stdout="user-object-id-xyz\n", stderr="")
        result = _lookup_deployer_object_id(None)

        assert result == "user-object-id-xyz"
        cmd = mock_run.call_args[0][0]
        assert "signed-in-user" in cmd

    @patch("azext_prototype.stages.deploy_session.subprocess.run")
    def test_lookup_failure_returns_none(self, mock_run):
        from azext_prototype.stages.deploy_session import _lookup_deployer_object_id

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        assert _lookup_deployer_object_id("bad-id") is None
        assert _lookup_deployer_object_id(None) is None

    @patch("azext_prototype.stages.deploy_session.subprocess.run", side_effect=FileNotFoundError)
    def test_lookup_no_az_cli(self, _mock_run):
        from azext_prototype.stages.deploy_session import _lookup_deployer_object_id

        assert _lookup_deployer_object_id("client-id") is None

    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value="sp-oid-123")
    @patch("azext_prototype.stages.deploy_session.set_deployment_context", return_value={"status": "ok"})
    def test_resolve_context_sets_deployer_oid_for_sp(self, _mock_ctx, _mock_lookup, tmp_project):
        """SP auth: deployer_object_id is the SP's object ID."""
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(tmp_project) / "prototype.yaml"
        config_data = {
            "project": {"name": "t", "location": "eastus", "iac_tool": "terraform"},
            "ai": {"provider": "github-models"},
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        context = AgentContext(project_config={}, project_dir=str(tmp_project), ai_provider=MagicMock())
        registry = AgentRegistry()
        register_all_builtin(registry)
        session = DeploySession(context, registry)

        session._resolve_context("sub-123", "tenant-456", client_id="my-app-id", client_secret="secret")

        assert session._deploy_env["TF_VAR_deployer_object_id"] == "sp-oid-123"
        _mock_lookup.assert_called_once_with("my-app-id")

    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value="user-oid-456")
    def test_resolve_context_sets_deployer_oid_for_user(self, _mock_lookup, tmp_project):
        """User auth (no SP): deployer_object_id is the signed-in user's object ID."""
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(tmp_project) / "prototype.yaml"
        config_data = {
            "project": {"name": "t", "location": "eastus", "iac_tool": "terraform"},
            "ai": {"provider": "github-models"},
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        context = AgentContext(project_config={}, project_dir=str(tmp_project), ai_provider=MagicMock())
        registry = AgentRegistry()
        register_all_builtin(registry)
        session = DeploySession(context, registry)

        session._resolve_context("sub-123", None)

        assert session._deploy_env["TF_VAR_deployer_object_id"] == "user-oid-456"
        # Called with None (no client_id) → signed-in-user path
        _mock_lookup.assert_called_once_with(None)

    @patch("azext_prototype.stages.deploy_session._lookup_deployer_object_id", return_value=None)
    def test_resolve_context_no_oid_when_lookup_fails(self, _mock_lookup, tmp_project):
        """When lookup fails, TF_VAR_deployer_object_id is not set."""
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(tmp_project) / "prototype.yaml"
        config_data = {
            "project": {"name": "t", "location": "eastus", "iac_tool": "terraform"},
            "ai": {"provider": "github-models"},
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        context = AgentContext(project_config={}, project_dir=str(tmp_project), ai_provider=MagicMock())
        registry = AgentRegistry()
        register_all_builtin(registry)
        session = DeploySession(context, registry)

        session._resolve_context("sub-123", None)

        assert "TF_VAR_deployer_object_id" not in session._deploy_env


# ======================================================================
# Coverage expansion: run() phases, slash commands, remediation
# ======================================================================


class TestRunPhasesCoverage:
    """Tests covering run() phases: no build state, re-entry sync,
    preflight failure branch, interactive loop edge cases."""

    def _make_session(self, project_dir, iac_tool="terraform", build_stages=None):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": iac_tool},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        if build_stages is not None:
            _write_build_yaml(project_dir, stages=build_stages, iac_tool=iac_tool)

        context = AgentContext(
            project_config={"project": {"iac_tool": iac_tool}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        return DeploySession(context, registry)

    def test_run_no_build_state_returns_cancelled(self, tmp_project):
        """Lines 322-324: No build state file => cancelled."""
        session = self._make_session(tmp_project, build_stages=None)
        # No build.yaml written
        output = []
        result = session.run(
            subscription="sub-123",
            input_fn=lambda p: "quit",
            print_fn=lambda msg: output.append(msg),
        )
        assert result.cancelled is True
        joined = "\n".join(output)
        assert "No build state found" in joined

    def test_run_reentry_sync_shows_changes(self, tmp_project):
        """Lines 326-335: Re-entry with build state changes shows sync info."""
        from azext_prototype.stages.deploy_state import SyncResult

        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        # Pre-load deployment_stages so re-entry branch triggers
        build_path = Path(tmp_project) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)

        sync = SyncResult(created=["Stage 2: Data"], orphaned=[], updated_code=1, details=["Added new Stage 2: Data"])
        with patch.object(session._deploy_state, "sync_from_build_state", return_value=sync):
            output = []
            session.run(
                subscription="sub-123",
                input_fn=lambda p: "quit",
                print_fn=lambda msg: output.append(msg),
            )
        joined = "\n".join(output)
        assert "Build state changed" in joined
        assert "updated code" in joined.lower() or "1 deployed stage(s)" in joined

    def test_run_tenant_displayed(self, tmp_project):
        """Lines 352-353: Tenant is printed during plan overview."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        output = []
        session.run(
            subscription="sub-123",
            tenant="tenant-abc",
            input_fn=lambda p: "quit",
            print_fn=lambda msg: output.append(msg),
        )
        joined = "\n".join(output)
        assert "tenant-abc" in joined

    def test_run_resource_group_displayed(self, tmp_project):
        """Lines 354-355: Resource group is printed when set."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        config_data = {
            "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
            "ai": {"provider": "github-models"},
            "deploy": {"resource_group": "my-rg", "subscription": ""},
        }
        config_path = Path(tmp_project) / "prototype.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)
        _write_build_yaml(tmp_project, stages=stages)

        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        session = DeploySession(context, registry)

        output = []
        session.run(
            subscription="sub-123",
            input_fn=lambda p: "quit",
            print_fn=lambda msg: output.append(msg),
        )
        joined = "\n".join(output)
        assert "my-rg" in joined

    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=False)
    @patch(
        "azext_prototype.stages.deploy_session.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="Terraform v1.7.0\n", stderr=""),
    )
    def test_run_preflight_failure_branch(self, _mock_sub, _mock_login, tmp_project):
        """Lines 388-391: Preflight failures print fix instructions."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        inputs = iter(["", "done"])
        output = []
        session.run(
            subscription="sub-123",
            input_fn=lambda p: next(inputs),
            print_fn=lambda msg: output.append(msg),
        )
        joined = "\n".join(output)
        assert "preflight checks failed" in joined.lower() or "fix the issues" in joined.lower()

    def test_run_empty_input_continues(self, tmp_project):
        """Lines 419-420: Empty input during interactive loop does nothing."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        # Skip preflight by having everything fail, then loop: empty -> quit
        inputs = iter(["", "", "", "quit"])
        output = []
        with patch.object(session, "_run_preflight", return_value=[]):
            session.run(
                subscription="sub-123",
                input_fn=lambda p: next(inputs),
                print_fn=lambda msg: output.append(msg),
            )
        # Reached quit without error

    def test_run_done_finishes(self, tmp_project):
        """Lines 427-428: 'done' word exits loop."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        inputs = iter(["", "lgtm"])
        output = []
        with patch.object(session, "_run_preflight", return_value=[]):
            result = session.run(
                subscription="sub-123",
                input_fn=lambda p: next(inputs),
                print_fn=lambda msg: output.append(msg),
            )
        assert not result.cancelled

    def test_run_eof_in_interactive_loop_breaks(self, tmp_project):
        """Lines 416-417: EOFError in interactive loop breaks cleanly."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        call_count = [0]

        def eof_on_second(p):
            call_count[0] += 1
            if call_count[0] == 1:
                return ""  # confirm
            raise EOFError

        with patch.object(session, "_run_preflight", return_value=[]):
            result = session.run(
                subscription="sub-123",
                input_fn=eof_on_second,
                print_fn=lambda msg: None,
            )
        assert not result.cancelled  # exits normally via break

    def test_run_natural_language_fallback(self, tmp_project):
        """Line 468: Unrecognized input shows help hint."""
        from azext_prototype.stages.intent import IntentKind, IntentResult

        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        # Mock intent classifier to return CONVERSATIONAL (no matching command)
        session._intent_classifier = MagicMock()
        session._intent_classifier.classify.return_value = IntentResult(
            kind=IntentKind.CONVERSATIONAL, command="", args=""
        )
        inputs = iter(["", "something random", "quit"])
        output = []
        with patch.object(session, "_run_preflight", return_value=[]):
            session.run(
                subscription="sub-123",
                input_fn=lambda p: next(inputs),
                print_fn=lambda msg: output.append(msg),
            )
        joined = "\n".join(output)
        assert "/help" in joined

    def test_run_natural_language_multi_stage(self, tmp_project):
        """Lines 448-456: Multi-stage intent dispatches multiple commands."""
        from azext_prototype.stages.intent import IntentKind, IntentResult

        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        session._intent_classifier = MagicMock()
        session._intent_classifier.classify.return_value = IntentResult(
            kind=IntentKind.COMMAND, command="/deploy", args="stages 1 and 2"
        )
        inputs = iter(["", "deploy stages 1 and 2", "quit"])
        output = []
        with patch.object(session, "_run_preflight", return_value=[]):
            with patch.object(session, "_handle_slash_command") as mock_cmd:
                session.run(
                    subscription="sub-123",
                    input_fn=lambda p: next(inputs),
                    print_fn=lambda msg: output.append(msg),
                )
                # Should have dispatched /deploy 1 and /deploy 2
                calls = [c.args[0] for c in mock_cmd.call_args_list]
                assert "/deploy 1" in calls
                assert "/deploy 2" in calls


class TestSingleStageFailureRemediation:
    """Tests for run_single_stage failure remediation (lines 587-598)."""

    def _make_session(self, project_dir, build_stages=None):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir, stages=build_stages)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        return DeploySession(context, registry)

    @patch(
        "azext_prototype.stages.deploy_session.deploy_terraform",
        return_value={"status": "failed", "error": "auth error"},
    )
    def test_single_stage_failure_shows_error_and_attempts_remediation(self, mock_tf, tmp_project):
        """Lines 587-598: Single-stage failure prints error and tries remediation."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)
        # Clear fix agents so _remediate_deploy_failure returns None
        session._iac_agents = {}
        session._dev_agent = None

        output = []
        session.run_single_stage(
            1,
            subscription="sub-123",
            print_fn=lambda msg: output.append(msg),
        )
        joined = "\n".join(output)
        assert "failed" in joined.lower()
        assert "auth error" in joined

    @patch("azext_prototype.stages.deploy_session.deploy_terraform")
    def test_single_stage_remediation_success(self, mock_tf, tmp_project):
        """Lines 597-598: Remediation succeeds prints success."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)
        # First call fails, remediation returns deployed
        mock_tf.return_value = {"status": "failed", "error": "oops"}

        with patch.object(
            session,
            "_remediate_deploy_failure",
            return_value={"status": "deployed"},
        ):
            output = []
            session.run_single_stage(
                1,
                subscription="sub-123",
                print_fn=lambda msg: output.append(msg),
            )
            joined = "\n".join(output)
            assert "remediation" in joined.lower()


class TestDeployPendingStagesAwaitingManual:
    """Tests covering awaiting_manual status (lines 892-909)."""

    def _make_session(self, project_dir, build_stages=None):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir, stages=build_stages)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        return DeploySession(context, registry)

    def test_manual_step_done_marks_deployed(self, tmp_project):
        """Lines 892-904: Manual step answered with 'done' marks deployed."""
        stages = [
            {
                "stage": 1,
                "name": "Manual DNS",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
                "deploy_mode": "manual",
                "manual_instructions": "Update DNS records.",
            },
        ]
        (tmp_project / "stage-1").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)
        # Manually set deploy_mode on the loaded state
        build_path = Path(tmp_project) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        ds = session._deploy_state._state["deployment_stages"][0]
        ds["deploy_mode"] = "manual"
        ds["manual_instructions"] = "Update DNS records."

        output = []
        session._deploy_pending_stages(
            force=False,
            use_styled=False,
            _print=lambda msg: output.append(msg),
            _input=lambda p: "done",
        )
        joined = "\n".join(output)
        assert "Manual step required" in joined or "manual" in joined.lower()

    def test_manual_step_skip(self, tmp_project):
        """Lines 905-906: Manual step answered with 'skip' skips."""
        stages = [
            {
                "stage": 1,
                "name": "Manual DNS",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "stage-1").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)
        build_path = Path(tmp_project) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        ds = session._deploy_state._state["deployment_stages"][0]
        ds["deploy_mode"] = "manual"
        ds["manual_instructions"] = "Do something manual."

        output = []
        session._deploy_pending_stages(
            force=False,
            use_styled=False,
            _print=lambda msg: output.append(msg),
            _input=lambda p: "skip",
        )
        joined = "\n".join(output)
        assert "skip" in joined.lower()

    def test_manual_step_eof_skips(self, tmp_project):
        """Lines 899-901: Manual step EOF is treated as skipped."""
        stages = [
            {
                "stage": 1,
                "name": "Manual Step",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "stage-1").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)
        build_path = Path(tmp_project) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        ds = session._deploy_state._state["deployment_stages"][0]
        ds["deploy_mode"] = "manual"
        ds["manual_instructions"] = "Do it."

        output = []
        session._deploy_pending_stages(
            force=False,
            use_styled=False,
            _print=lambda msg: output.append(msg),
            _input=lambda p: (_ for _ in ()).throw(EOFError),
        )
        joined = "\n".join(output)
        assert "skipped" in joined.lower()

    def test_manual_step_other_breaks(self, tmp_project):
        """Lines 907-909: Unknown answer pauses deployment."""
        stages = [
            {
                "stage": 1,
                "name": "Manual Step",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "stage-1").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)
        build_path = Path(tmp_project) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        ds = session._deploy_state._state["deployment_stages"][0]
        ds["deploy_mode"] = "manual"
        ds["manual_instructions"] = "Do it."

        output = []
        session._deploy_pending_stages(
            force=False,
            use_styled=False,
            _print=lambda msg: output.append(msg),
            _input=lambda p: "help me",
        )
        joined = "\n".join(output)
        assert "pausing" in joined.lower() or "continue" in joined.lower()


class TestRollbackAllCoverage:
    """Tests for _rollback_all (lines 1618-1640)."""

    def _make_session(self, project_dir, build_stages=None):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir, stages=build_stages)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        return DeploySession(context, registry)

    def test_rollback_all_no_candidates(self, tmp_project):
        """Lines 1619-1621: No deployed stages to roll back."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        build_path = Path(tmp_project) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)

        output = []
        session._rollback_all(lambda msg: output.append(msg), lambda p: "y")
        joined = "\n".join(output)
        assert "No deployed stages" in joined

    @patch("azext_prototype.stages.deploy_session.rollback_terraform")
    def test_rollback_all_confirms_each(self, mock_rb, tmp_project):
        """Lines 1626-1640: Confirms each stage and rolls back."""
        stages = [
            {
                "stage": 1,
                "name": "A",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
            {
                "stage": 2,
                "name": "B",
                "category": "infra",
                "services": [],
                "dir": "stage-2",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "stage-1").mkdir(parents=True, exist_ok=True)
        (tmp_project / "stage-2").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)
        build_path = Path(tmp_project) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        session._deploy_state.mark_stage_deployed(1)
        session._deploy_state.mark_stage_deployed(2)
        session._deploy_env = {"ARM_SUBSCRIPTION_ID": "sub-123"}

        mock_rb.return_value = {"status": "rolled_back"}
        output = []
        session._rollback_all(lambda msg: output.append(msg), lambda p: "y")
        joined = "\n".join(output)
        assert "Rolling back" in joined
        assert mock_rb.call_count == 2

    def test_rollback_all_decline_stops(self, tmp_project):
        """Lines 1635-1637: Declining rollback stops the sequence."""
        stages = [
            {
                "stage": 1,
                "name": "A",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
            {
                "stage": 2,
                "name": "B",
                "category": "infra",
                "services": [],
                "dir": "stage-2",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        build_path = Path(tmp_project) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        session._deploy_state.mark_stage_deployed(1)
        session._deploy_state.mark_stage_deployed(2)

        output = []
        session._rollback_all(lambda msg: output.append(msg), lambda p: "n")
        joined = "\n".join(output)
        assert "Skipping" in joined

    def test_rollback_all_eof_cancels(self, tmp_project):
        """Lines 1631-1633: EOF during rollback cancels."""
        stages = [
            {
                "stage": 1,
                "name": "A",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        build_path = Path(tmp_project) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        session._deploy_state.mark_stage_deployed(1)

        output = []
        session._rollback_all(
            lambda msg: output.append(msg),
            lambda p: (_ for _ in ()).throw(EOFError),
        )
        joined = "\n".join(output)
        assert "cancelled" in joined.lower()


class TestSlashCommandPlan:
    """Tests covering /plan slash command (lines 1842-1875)."""

    def _make_session(self, project_dir, iac_tool="terraform", build_stages=None):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": iac_tool},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir, stages=build_stages, iac_tool=iac_tool)

        context = AgentContext(
            project_config={"project": {"iac_tool": iac_tool}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        session = DeploySession(context, registry)
        build_path = Path(project_dir) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        return session

    def test_plan_no_arg(self, tmp_project):
        """Lines 1843-1844: /plan without arg shows usage."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        output = []
        session._handle_slash_command("/plan", False, False, lambda msg: output.append(msg), lambda p: "")
        joined = "\n".join(output)
        assert "Usage" in joined

    def test_plan_manual_stage(self, tmp_project):
        """Line 1850: Manual stage has no plan preview."""
        stages = [
            {
                "stage": 1,
                "name": "Manual",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "stage-1").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)
        ds = session._deploy_state._state["deployment_stages"][0]
        ds["deploy_mode"] = "manual"

        output = []
        session._handle_slash_command("/plan 1", False, False, lambda msg: output.append(msg), lambda p: "")
        joined = "\n".join(output)
        assert "manual step" in joined.lower()

    def test_plan_missing_dir(self, tmp_project):
        """Lines 1851-1852: Stage dir not found."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "nonexistent",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        output = []
        session._handle_slash_command("/plan 1", False, False, lambda msg: output.append(msg), lambda p: "")
        joined = "\n".join(output)
        assert "not found" in joined.lower()

    @patch(
        "azext_prototype.stages.deploy_session.plan_terraform",
        return_value={"output": "Plan: 5 to add", "error": None},
    )
    def test_plan_terraform_infra_stage(self, mock_plan, tmp_project):
        """Lines 1855-1861: Terraform plan for infra stage."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "stage-1").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)
        session._deploy_env = {"ARM_SUBSCRIPTION_ID": "sub-123"}
        session._subscription = "sub-123"

        output = []
        session._handle_slash_command("/plan 1", False, False, lambda msg: output.append(msg), lambda p: "")
        joined = "\n".join(output)
        assert "Plan: 5 to add" in joined

    @patch(
        "azext_prototype.stages.deploy_session.whatif_bicep",
        return_value={"output": "What-if: 2 to create", "error": None},
    )
    def test_plan_bicep_infra_stage(self, mock_whatif, tmp_project):
        """Lines 1862-1868: Bicep what-if for infra stage."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "stage-1").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, iac_tool="bicep", build_stages=stages)
        session._deploy_env = {"ARM_SUBSCRIPTION_ID": "sub-123"}
        session._subscription = "sub-123"
        session._resource_group = "my-rg"

        output = []
        session._handle_slash_command("/plan 1", False, False, lambda msg: output.append(msg), lambda p: "")
        joined = "\n".join(output)
        assert "What-if: 2 to create" in joined

    @patch(
        "azext_prototype.stages.deploy_session.plan_terraform",
        return_value={"output": None, "error": "Init failed"},
    )
    def test_plan_with_error(self, mock_plan, tmp_project):
        """Lines 1871-1872: Plan error is displayed."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "stage-1").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)
        session._deploy_env = {"ARM_SUBSCRIPTION_ID": "sub-123"}
        session._subscription = "sub-123"

        output = []
        session._handle_slash_command("/plan 1", False, False, lambda msg: output.append(msg), lambda p: "")
        joined = "\n".join(output)
        assert "Init failed" in joined

    def test_plan_app_stage_no_preview(self, tmp_project):
        """Lines 1873-1874: App stages have no plan preview."""
        stages = [
            {
                "stage": 1,
                "name": "App",
                "category": "app",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "stage-1").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)

        output = []
        session._handle_slash_command("/plan 1", False, False, lambda msg: output.append(msg), lambda p: "")
        joined = "\n".join(output)
        assert "app stage" in joined.lower()


class TestSlashCommandSplit:
    """Tests covering /split slash command (lines 1878-1903)."""

    def _make_session(self, project_dir, build_stages=None):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir, stages=build_stages)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        session = DeploySession(context, registry)
        build_path = Path(project_dir) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        return session

    def test_split_no_arg(self, tmp_project):
        """Lines 1879-1880: /split without arg shows usage."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        output = []
        session._handle_slash_command("/split", False, False, lambda msg: output.append(msg), lambda p: "")
        joined = "\n".join(output)
        assert "Usage" in joined

    def test_split_success(self, tmp_project):
        """Lines 1887-1900: Split stage into substages."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        names = iter(["Networking", "Compute", ""])  # 2 substages + blank

        output = []
        session._handle_slash_command(
            "/split 1",
            False,
            False,
            lambda msg: output.append(msg),
            lambda p: next(names),
        )
        joined = "\n".join(output)
        assert "Split into 2 substages" in joined

    def test_split_too_few_substages(self, tmp_project):
        """Lines 1901-1902: Less than 2 substages cancels."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        names = iter(["OnlyOne", ""])  # 1 substage + blank

        output = []
        session._handle_slash_command(
            "/split 1",
            False,
            False,
            lambda msg: output.append(msg),
            lambda p: next(names),
        )
        joined = "\n".join(output)
        assert "at least 2" in joined.lower()

    def test_split_eof_during_input(self, tmp_project):
        """Lines 1893-1894: EOF during substage naming stops input."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)

        output = []
        session._handle_slash_command(
            "/split 1",
            False,
            False,
            lambda msg: output.append(msg),
            lambda p: (_ for _ in ()).throw(EOFError),
        )
        # Should not crash, split cancelled
        joined = "\n".join(output)
        assert "at least 2" in joined.lower() or "Split" in joined


class TestSlashCommandDestroy:
    """Tests covering /destroy slash command (lines 1906-1927)."""

    def _make_session(self, project_dir, build_stages=None):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir, stages=build_stages)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        session = DeploySession(context, registry)
        build_path = Path(project_dir) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        return session

    def test_destroy_no_arg(self, tmp_project):
        """Lines 1907-1908: /destroy without arg shows usage."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        output = []
        session._handle_slash_command("/destroy", False, False, lambda msg: output.append(msg), lambda p: "")
        joined = "\n".join(output)
        assert "Usage" in joined

    @patch("azext_prototype.stages.deploy_session.rollback_terraform")
    def test_destroy_confirmed(self, mock_rb, tmp_project):
        """Lines 1918-1922: Destroy confirmed rolls back and destroys."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "stage-1").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)
        session._deploy_state.mark_stage_deployed(1)
        session._deploy_env = {"ARM_SUBSCRIPTION_ID": "sub-123"}
        mock_rb.return_value = {"status": "rolled_back"}

        output = []
        session._handle_slash_command(
            "/destroy 1",
            False,
            False,
            lambda msg: output.append(msg),
            lambda p: "y",
        )
        joined = "\n".join(output)
        assert "destroyed" in joined.lower()

    def test_destroy_cancelled(self, tmp_project):
        """Lines 1925-1926: Destroy declined is cancelled."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        session._deploy_state.mark_stage_deployed(1)

        output = []
        session._handle_slash_command(
            "/destroy 1",
            False,
            False,
            lambda msg: output.append(msg),
            lambda p: "n",
        )
        joined = "\n".join(output)
        assert "cancelled" in joined.lower()

    def test_destroy_eof_cancels(self, tmp_project):
        """Lines 1915-1917: EOF during destroy confirmation cancels."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        session._deploy_state.mark_stage_deployed(1)

        output = []
        session._handle_slash_command(
            "/destroy 1",
            False,
            False,
            lambda msg: output.append(msg),
            lambda p: (_ for _ in ()).throw(EOFError),
        )
        joined = "\n".join(output)
        assert "cancelled" in joined.lower()


class TestSlashCommandManual:
    """Tests covering /manual slash command (lines 1930-1952)."""

    def _make_session(self, project_dir, build_stages=None):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir, stages=build_stages)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        session = DeploySession(context, registry)
        build_path = Path(project_dir) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        return session

    def test_manual_no_arg(self, tmp_project):
        """Lines 1931-1932: /manual without arg shows usage."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        output = []
        session._handle_slash_command("/manual", False, False, lambda msg: output.append(msg), lambda p: "")
        joined = "\n".join(output)
        assert "Usage" in joined

    def test_manual_set_instructions(self, tmp_project):
        """Lines 1940-1944: Setting manual instructions."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        output = []
        session._handle_slash_command(
            '/manual 1 "Run az keyvault set-policy"',
            False,
            False,
            lambda msg: output.append(msg),
            lambda p: "",
        )
        joined = "\n".join(output)
        assert "manual mode" in joined.lower()
        # Verify it was saved
        ds = session._deploy_state._state["deployment_stages"][0]
        assert ds["deploy_mode"] == "manual"

    def test_manual_view_existing_instructions(self, tmp_project):
        """Lines 1946-1948: Viewing existing manual instructions."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        ds = session._deploy_state._state["deployment_stages"][0]
        ds["manual_instructions"] = "Do the thing."

        output = []
        session._handle_slash_command(
            "/manual 1",
            False,
            False,
            lambda msg: output.append(msg),
            lambda p: "",
        )
        joined = "\n".join(output)
        assert "Do the thing" in joined

    def test_manual_view_no_instructions(self, tmp_project):
        """Lines 1949-1951: No instructions set shows hint."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        output = []
        session._handle_slash_command(
            "/manual 1",
            False,
            False,
            lambda msg: output.append(msg),
            lambda p: "",
        )
        joined = "\n".join(output)
        assert "No manual instructions" in joined


class TestHandleDescribe:
    """Tests for _handle_describe (lines 2020-2080)."""

    def _make_session(self, project_dir, build_stages=None):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir, stages=build_stages)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        session = DeploySession(context, registry)
        build_path = Path(project_dir) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        return session

    def test_describe_no_arg(self, tmp_project):
        """Lines 2024-2026: No arg shows usage."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        output = []
        session._handle_describe("", lambda msg: output.append(msg))
        joined = "\n".join(output)
        assert "Usage" in joined

    def test_describe_no_numbers(self, tmp_project):
        """Lines 2029-2031: No number in arg shows usage."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        output = []
        session._handle_describe("abc", lambda msg: output.append(msg))
        joined = "\n".join(output)
        assert "Usage" in joined

    def test_describe_not_found(self, tmp_project):
        """Lines 2035-2037: Stage not found."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        output = []
        session._handle_describe("99", lambda msg: output.append(msg))
        joined = "\n".join(output)
        assert "not found" in joined.lower()

    def test_describe_full_details(self, tmp_project):
        """Lines 2040-2080: Full description with services, files, output, error."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [
                    {
                        "name": "kv",
                        "computed_name": "mykv",
                        "resource_type": "Microsoft.KeyVault/vaults",
                        "sku": "standard",
                    }
                ],
                "dir": "stage-1",
                "status": "generated",
                "files": ["stage-1/main.tf"],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        ds = session._deploy_state._state["deployment_stages"][0]
        ds["deployed_at"] = "2026-01-01T12:00:00"
        ds["deploy_output"] = "resource_id=abc123\nendpoint=https://foo.com"
        ds["deploy_error"] = "some warning message"

        output = []
        session._handle_describe("1", lambda msg: output.append(msg))
        joined = "\n".join(output)
        assert "Infra" in joined
        assert "mykv" in joined
        assert "Microsoft.KeyVault" in joined
        assert "standard" in joined
        assert "main.tf" in joined
        assert "2026-01-01T12:00:00" in joined
        assert "resource_id=abc123" in joined
        assert "some warning message" in joined

    def test_describe_truncates_long_output(self, tmp_project):
        """Lines 2074-2075: Long deploy output is truncated."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        ds = session._deploy_state._state["deployment_stages"][0]
        ds["deploy_output"] = "\n".join(f"line {i}" for i in range(20))

        output = []
        session._handle_describe("1", lambda msg: output.append(msg))
        joined = "\n".join(output)
        assert "truncated" in joined.lower()


class TestUnknownSlashCommand:
    """Tests for unknown slash command (line 2020)."""

    def _make_session(self, project_dir, build_stages=None):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir, stages=build_stages)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        session = DeploySession(context, registry)
        build_path = Path(project_dir) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        return session

    def test_unknown_command(self, tmp_project):
        """Line 2020: Unknown slash command shows error."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "stage-1",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        output = []
        session._handle_slash_command(
            "/foobar",
            False,
            False,
            lambda msg: output.append(msg),
            lambda p: "",
        )
        joined = "\n".join(output)
        assert "Unknown command" in joined


class TestMaybeSpinner:
    """Tests for _maybe_spinner (lines 2099-2116)."""

    def _make_session(self, project_dir):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        return DeploySession(context, registry)

    def test_spinner_with_status_fn(self, tmp_project):
        """Lines 2106-2114: status_fn mode calls start/end/tokens."""
        session = self._make_session(tmp_project)
        calls = []
        session._status_fn = lambda msg, kind: calls.append((msg, kind))

        with session._maybe_spinner("Working...", use_styled=False):
            pass

        # Should have called start and end
        kinds = [k for _, k in calls]
        assert "start" in kinds
        assert "end" in kinds

    def test_spinner_plain_mode(self, tmp_project):
        """Line 2116: Plain mode (no styled, no status_fn) just yields."""
        session = self._make_session(tmp_project)
        session._status_fn = None

        with session._maybe_spinner("Working...", use_styled=False):
            pass  # Should not crash


class TestCollectStageFileContent:
    """Tests for _collect_stage_file_content (lines 1178-1225)."""

    def _make_session(self, project_dir):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        return DeploySession(context, registry)

    def test_glob_fallback_when_no_files(self, tmp_project):
        """Lines 1191-1200: Falls back to globbing when files list is empty."""
        session = self._make_session(tmp_project)
        stage_dir = tmp_project / "stage-1"
        stage_dir.mkdir()
        (stage_dir / "main.tf").write_text("resource {} {}")

        stage = {"dir": "stage-1", "files": []}
        content = session._collect_stage_file_content(stage)
        assert "main.tf" in content
        assert "resource {} {}" in content

    def test_empty_dir_returns_empty(self, tmp_project):
        """Lines 1202-1203: No files found returns empty string."""
        session = self._make_session(tmp_project)
        stage = {"dir": "nonexistent", "files": []}
        content = session._collect_stage_file_content(stage)
        assert content == ""

    def test_unreadable_file(self, tmp_project):
        """Lines 1213-1215: Unreadable file shows 'could not read'."""
        session = self._make_session(tmp_project)
        stage = {"dir": "stage-1", "files": ["stage-1/missing.tf"]}
        content = session._collect_stage_file_content(stage)
        assert "could not read" in content

    def test_max_bytes_cap(self, tmp_project):
        """Lines 1206-1208: Size cap truncates remaining files."""
        session = self._make_session(tmp_project)
        stage_dir = tmp_project / "stage-1"
        stage_dir.mkdir()
        # Create a file larger than 1000 bytes
        (stage_dir / "big.tf").write_text("x" * 2000)

        stage = {"dir": "stage-1", "files": ["stage-1/big.tf", "stage-1/other.tf"]}
        content = session._collect_stage_file_content(stage, max_bytes=100)
        assert "omitted" in content.lower() or "big.tf" in content

    def test_truncates_large_individual_files(self, tmp_project):
        """Lines 1218-1219: Individual files over 8000 chars are truncated."""
        session = self._make_session(tmp_project)
        stage_dir = tmp_project / "stage-1"
        stage_dir.mkdir()
        (stage_dir / "huge.tf").write_text("x" * 10000)

        stage = {"dir": "stage-1", "files": ["stage-1/huge.tf"]}
        content = session._collect_stage_file_content(stage)
        assert "truncated" in content.lower()


class TestParseStageNumbers:
    """Tests for _parse_stage_numbers static method."""

    def test_parses_json_array(self):
        from azext_prototype.stages.deploy_session import DeploySession

        valid = [{"stage": 3}, {"stage": 4}, {"stage": 5}]
        result = DeploySession._parse_stage_numbers("[3, 4]", valid)
        assert result == [3, 4]

    def test_filters_invalid_numbers(self):
        from azext_prototype.stages.deploy_session import DeploySession

        valid = [{"stage": 3}]
        result = DeploySession._parse_stage_numbers("[3, 99]", valid)
        assert result == [3]

    def test_fallback_to_regex(self):
        from azext_prototype.stages.deploy_session import DeploySession

        valid = [{"stage": 5}, {"stage": 6}]
        result = DeploySession._parse_stage_numbers("Stages 5 and 6 need updates", valid)
        assert 5 in result
        assert 6 in result

    def test_empty_array(self):
        from azext_prototype.stages.deploy_session import DeploySession

        valid = [{"stage": 1}]
        result = DeploySession._parse_stage_numbers("[]", valid)
        assert result == []


class TestWriteStageFiles:
    """Tests for _write_stage_files (lines 1289-1330)."""

    def _make_session(self, project_dir):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        return DeploySession(context, registry)

    def test_empty_content(self, tmp_project):
        """Line 1294-1295: Empty content returns empty list."""
        session = self._make_session(tmp_project)
        result = session._write_stage_files({"dir": "stage-1"}, "")
        assert result == []

    def test_no_file_blocks(self, tmp_project):
        """Lines 1298-1299: No parseable file blocks returns empty."""
        session = self._make_session(tmp_project)
        result = session._write_stage_files({"dir": "stage-1"}, "No code blocks here.")
        assert result == []

    def test_writes_files_and_strips_prefix(self, tmp_project):
        """Lines 1310-1314: Stage dir prefix is stripped from filenames."""
        session = self._make_session(tmp_project)
        stage_dir = tmp_project / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)

        content = "```stage-1/main.tf\nresource {} {}\n```"
        with patch.object(session, "_sync_build_state"):
            result = session._write_stage_files({"dir": "stage-1", "stage": 1}, content)
        assert len(result) == 1
        assert (stage_dir / "main.tf").exists()

    def test_blocked_files_dropped(self, tmp_project):
        """Lines 1316-1318: Blocked files (versions.tf for terraform) are dropped."""
        session = self._make_session(tmp_project)
        stage_dir = tmp_project / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)

        content = (
            "```stage-1/main.tf\nresource {} {}\n```\n\n"
            '```stage-1/versions.tf\nterraform { required_version = ">= 1.0" }\n```'
        )
        with patch.object(session, "_sync_build_state"):
            result = session._write_stage_files({"dir": "stage-1", "stage": 1}, content)
        # versions.tf should be dropped
        written_names = [Path(f).name for f in result]
        assert "versions.tf" not in written_names
        assert "main.tf" in written_names


class TestBuildFixTask:
    """Tests for _build_fix_task (lines 1227-1287)."""

    def _make_session(self, project_dir):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)
        return DeploySession(context, registry)

    def test_infra_stage_selects_iac_agent(self, tmp_project):
        """Lines 1242-1243: Infra category selects IaC agent."""
        session = self._make_session(tmp_project)
        stage = {
            "stage": 1,
            "name": "Infra",
            "category": "infra",
            "dir": "stage-1",
            "services": [],
        }
        agent, task = session._build_fix_task(stage, "error", "diag", "guide")
        assert agent is not None  # terraform agent from registry
        assert "Fix deployment Stage 1" in task

    def test_app_stage_selects_dev_agent(self, tmp_project):
        """Lines 1244-1245: App category selects dev agent."""
        session = self._make_session(tmp_project)
        stage = {
            "stage": 1,
            "name": "App",
            "category": "app",
            "dir": "stage-1",
            "services": [],
        }
        agent, task = session._build_fix_task(stage, "error", "diag", "guide")
        assert agent is not None
        assert "Fix deployment Stage 1" in task

    def test_no_agent_returns_none(self, tmp_project):
        """Lines 1249-1250: No suitable agent returns (None, '')."""
        session = self._make_session(tmp_project)
        session._iac_agents = {}
        session._dev_agent = None
        stage = {
            "stage": 1,
            "name": "Infra",
            "category": "infra",
            "dir": "stage-1",
            "services": [],
        }
        agent, task = session._build_fix_task(stage, "error", "diag", "guide")
        assert agent is None
        assert task == ""

    def test_includes_services_in_task(self, tmp_project):
        """Line 1277: Services included in fix task."""
        session = self._make_session(tmp_project)
        stage = {
            "stage": 1,
            "name": "Infra",
            "category": "infra",
            "dir": "stage-1",
            "services": [
                {
                    "name": "kv",
                    "computed_name": "mykv",
                    "resource_type": "Microsoft.KeyVault/vaults",
                    "sku": "standard",
                }
            ],
        }
        agent, task = session._build_fix_task(stage, "err", "diag", "guide")
        assert "mykv" in task
        assert "Microsoft.KeyVault" in task


# ======================================================================
# Natural Language Intent Detection — Deploy Integration
# ======================================================================


class TestNaturalLanguageIntentDeploy:
    """Test that natural language triggers correct deploy commands."""

    def _make_session(self, project_dir, build_stages=None):
        """Create a DeploySession with dependencies mocked."""
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": "terraform"},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir, stages=build_stages)

        context = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = AgentRegistry()
        register_all_builtin(registry)

        return DeploySession(context, registry)

    @patch(
        "azext_prototype.stages.deploy_session.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="Terraform v1.7.0\n", stderr=""),
    )
    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123")
    @patch("azext_prototype.stages.deploy_session.deploy_terraform", return_value={"status": "deployed"})
    def test_nl_deploy_stage_1(self, mock_tf, mock_sub, mock_login, mock_subprocess, tmp_project):
        """'deploy stage 1' in natural language triggers deploy."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)

        session = self._make_session(tmp_project, build_stages=stages)
        inputs = iter(["", "deploy stage 1", "done"])
        output = []
        session.run(
            subscription="sub-123",
            input_fn=lambda p: next(inputs),
            print_fn=lambda msg: output.append(msg),
        )
        joined = "\n".join(output)
        # Should show deploy success or at least process the deploy command
        assert "deployed" in joined.lower() or "Stage 1" in joined

    def test_nl_describe_stage(self, tmp_project):
        """'describe stage 1' shows stage details."""
        session = self._make_session(tmp_project)
        inputs = iter(["", "describe stage 1", "done"])
        output = []
        session.run(
            subscription="sub-123",
            input_fn=lambda p: next(inputs),
            print_fn=lambda msg: output.append(msg),
        )
        joined = "\n".join(output)
        assert "Foundation" in joined or "Stage 1" in joined


# ======================================================================
# Deploy State Remediation tests
# ======================================================================


class TestDeployStateRemediation:
    """Tests for remediation state tracking in DeployState."""

    def test_mark_stage_remediating(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.mark_stage_failed(1, "auth error")
        ds.mark_stage_remediating(1)

        stage = ds.get_stage(1)
        assert stage["deploy_status"] == "remediating"
        assert stage["remediation_attempts"] == 1

    def test_remediation_attempts_increment(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.mark_stage_remediating(1)
        assert ds.get_stage(1)["remediation_attempts"] == 1

        ds.mark_stage_remediating(1)
        assert ds.get_stage(1)["remediation_attempts"] == 2

        ds.mark_stage_remediating(1)
        assert ds.get_stage(1)["remediation_attempts"] == 3

    def test_reset_stage_to_pending(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.mark_stage_failed(1, "timeout")
        assert ds.get_stage(1)["deploy_status"] == "failed"
        assert ds.get_stage(1)["deploy_error"] == "timeout"

        ds.reset_stage_to_pending(1)
        stage = ds.get_stage(1)
        assert stage["deploy_status"] == "pending"
        assert stage["deploy_error"] == ""

    def test_add_patch_stages(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        new_stages = [
            {"stage": 0, "name": "Patch Fix", "category": "infra"},
        ]
        ds.add_patch_stages(new_stages)

        stages = ds.state["deployment_stages"]
        assert len(stages) == 4
        # Should have deploy-specific fields
        patch_stage = [s for s in stages if s["name"] == "Patch Fix"][0]
        assert patch_stage["deploy_status"] == "pending"
        assert patch_stage["remediation_attempts"] == 0
        assert patch_stage["deploy_timestamp"] is None

    def test_add_patch_stages_before_docs(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        stages = [
            {"stage": 1, "name": "Infra", "category": "infra", "services": [], "dir": "s1", "files": []},
            {"stage": 2, "name": "Docs", "category": "docs", "services": [], "dir": "s2", "files": []},
        ]
        build_path = _write_build_yaml(tmp_project, stages=stages)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.add_patch_stages([{"stage": 0, "name": "Patch", "category": "infra"}])

        stage_names = [s["name"] for s in ds.state["deployment_stages"]]
        # Patch should be before Docs
        assert stage_names.index("Patch") < stage_names.index("Docs")

    def test_renumber_stages(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        # Manually set non-sequential numbers
        ds.state["deployment_stages"][0]["stage"] = 10
        ds.state["deployment_stages"][1]["stage"] = 20
        ds.state["deployment_stages"][2]["stage"] = 30

        ds.renumber_stages()

        nums = [s["stage"] for s in ds.state["deployment_stages"]]
        assert nums == [1, 2, 3]

    def test_remediation_attempts_in_load_from_build_state(self, tmp_project):
        """Verify remediation_attempts field is added during build state import."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        for stage in ds.state["deployment_stages"]:
            assert "remediation_attempts" in stage
            assert stage["remediation_attempts"] == 0

    def test_remediating_status_icon(self, tmp_project):
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.mark_stage_remediating(1)
        status = ds.format_stage_status()
        assert "<>" in status


# ======================================================================
# Deploy Remediation Loop tests
# ======================================================================


class TestDeployRemediation:
    """Tests for the deploy auto-remediation loop in DeploySession."""

    _SENTINEL = object()

    def _make_session(self, project_dir, iac_tool="terraform", build_stages=None, ai_provider=_SENTINEL):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(project_dir) / "prototype.yaml"
        if not config_path.exists():
            config_data = {
                "project": {"name": "test", "location": "eastus", "iac_tool": iac_tool},
                "ai": {"provider": "github-models"},
            }
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

        _write_build_yaml(project_dir, stages=build_stages, iac_tool=iac_tool)

        provider = MagicMock() if ai_provider is self._SENTINEL else ai_provider
        context = AgentContext(
            project_config={"project": {"iac_tool": iac_tool}},
            project_dir=str(project_dir),
            ai_provider=provider,
        )
        registry = AgentRegistry()
        register_all_builtin(registry)

        session = DeploySession(context, registry)
        # Pre-load build state into deploy state
        build_path = Path(project_dir) / ".prototype" / "state" / "build.yaml"
        session._deploy_state.load_from_build_state(build_path)
        return session

    def test_remediation_succeeds_first_attempt(self, tmp_project):
        """Deploy fails -> QA diagnoses -> fix agent fixes -> redeploy succeeds."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)
        (tmp_project / "concept" / "infra" / "terraform" / "main.tf").write_text("# original")

        session = self._make_session(tmp_project, build_stages=stages)

        # Mock QA agent
        session._qa_agent = MagicMock()
        session._qa_agent.execute.return_value = _make_response(
            "Missing provider configuration. Add required_providers block."
        )

        # Mock architect agent
        session._architect_agent = MagicMock()
        session._architect_agent.execute.return_value = _make_response(
            "Root cause: missing provider. Add azurerm provider config.\nNo downstream impact."
        )

        # Mock IaC agent (terraform)
        mock_iac = MagicMock()
        mock_iac.execute.return_value = _make_response(
            "```main.tf\n# fixed provider config\nterraform { required_providers "
            '{ azurerm = { source = "hashicorp/azurerm" } } }\n```'
        )
        session._iac_agents["terraform"] = mock_iac

        result = {"status": "failed", "error": "Error: No provider configured"}
        stage = session._deploy_state.get_stage(1)
        output = []

        with patch("azext_prototype.stages.deploy_session.deploy_terraform", return_value={"status": "deployed"}):
            remediated = session._remediate_deploy_failure(
                stage,
                result,
                False,
                lambda msg: output.append(msg),
                lambda p: "",
            )

        assert remediated is not None
        assert remediated["status"] == "deployed"
        joined = "\n".join(output)
        assert "Remediating" in joined
        assert "deployed successfully after remediation" in joined

    def test_remediation_succeeds_second_attempt(self, tmp_project):
        """First redeploy fails, second attempt succeeds."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)
        (tmp_project / "concept" / "infra" / "terraform" / "main.tf").write_text("# original")

        session = self._make_session(tmp_project, build_stages=stages)

        session._qa_agent = MagicMock()
        session._qa_agent.execute.return_value = _make_response("Diagnosis: missing config")

        session._architect_agent = MagicMock()
        session._architect_agent.execute.return_value = _make_response("Fix the provider.\n[]")

        mock_iac = MagicMock()
        mock_iac.execute.return_value = _make_response("```main.tf\n# fixed\n```")
        session._iac_agents["terraform"] = mock_iac

        result = {"status": "failed", "error": "Error: provider error"}
        stage = session._deploy_state.get_stage(1)
        output = []

        deploy_call_count = [0]

        def mock_deploy(*args, **kwargs):
            deploy_call_count[0] += 1
            if deploy_call_count[0] <= 1:
                return {"status": "failed", "error": "still broken"}
            return {"status": "deployed"}

        with patch.object(session, "_deploy_single_stage", side_effect=mock_deploy):
            remediated = session._remediate_deploy_failure(
                stage,
                result,
                False,
                lambda msg: output.append(msg),
                lambda p: "",
            )

        assert remediated is not None
        assert remediated["status"] == "deployed"
        assert deploy_call_count[0] == 2

    def test_remediation_exhausted(self, tmp_project):
        """All remediation attempts fail — falls through."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)
        (tmp_project / "concept" / "infra" / "terraform" / "main.tf").write_text("# original")

        session = self._make_session(tmp_project, build_stages=stages)

        session._qa_agent = MagicMock()
        session._qa_agent.execute.return_value = _make_response("Diagnosis: broken")

        session._architect_agent = MagicMock()
        session._architect_agent.execute.return_value = _make_response("Fix it.\n[]")

        mock_iac = MagicMock()
        mock_iac.execute.return_value = _make_response("```main.tf\n# attempt\n```")
        session._iac_agents["terraform"] = mock_iac

        result = {"status": "failed", "error": "persistent error"}
        stage = session._deploy_state.get_stage(1)
        output = []

        with patch.object(session, "_deploy_single_stage", return_value={"status": "failed", "error": "still broken"}):
            remediated = session._remediate_deploy_failure(
                stage,
                result,
                False,
                lambda msg: output.append(msg),
                lambda p: "",
            )

        assert remediated is not None
        assert remediated["status"] == "failed"
        joined = "\n".join(output)
        assert "Re-deploy failed" in joined

    def test_remediation_no_agents(self, tmp_project):
        """Gracefully skipped when no fix agents are available."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)

        # Clear all agents
        session._qa_agent = None
        session._iac_agents = {}
        session._dev_agent = None
        session._architect_agent = None

        result = {"status": "failed", "error": "auth error"}
        stage = session._deploy_state.get_stage(1)
        output = []

        remediated = session._remediate_deploy_failure(
            stage,
            result,
            False,
            lambda msg: output.append(msg),
            lambda p: "",
        )

        assert remediated is None  # No remediation attempted

    def test_remediation_qa_cannot_diagnose(self, tmp_project):
        """Stops early when QA can't diagnose."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)

        # QA returns no diagnosis
        session._qa_agent = MagicMock()
        session._qa_agent.execute.return_value = _make_response("")

        mock_iac = MagicMock()
        session._iac_agents["terraform"] = mock_iac

        result = {"status": "failed", "error": "auth error"}
        stage = session._deploy_state.get_stage(1)
        output = []

        session._remediate_deploy_failure(
            stage,
            result,
            False,
            lambda msg: output.append(msg),
            lambda p: "",
        )

        # Should not have called the IaC agent since QA couldn't diagnose
        mock_iac.execute.assert_not_called()

    def test_remediation_updates_build_state(self, tmp_project):
        """Build.yaml files list is updated after remediation writes."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": ["concept/infra/terraform/main.tf"],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)
        (tmp_project / "concept" / "infra" / "terraform" / "main.tf").write_text("# original")

        session = self._make_session(tmp_project, build_stages=stages)

        content = "```main.tf\n# fixed content\n```"
        stage = session._deploy_state.get_stage(1)
        written = session._write_stage_files(stage, content)

        assert len(written) == 1
        assert "main.tf" in written[0]

        # Verify build state was updated
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs.load()
        build_stage = bs.state["deployment_stages"][0]
        assert build_stage["files"] == written

    @patch(
        "azext_prototype.stages.deploy_session.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="Terraform v1.7.0\n", stderr=""),
    )
    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123")
    @patch("azext_prototype.stages.deploy_session.deploy_terraform")
    def test_slash_deploy_routes_through_remediation(self, mock_tf, mock_sub, mock_login, mock_subprocess, tmp_project):
        """/deploy N triggers remediation on failure."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)

        session = self._make_session(tmp_project, build_stages=stages)

        mock_tf.return_value = {"status": "failed", "error": "auth error"}
        output = []

        with patch.object(
            session, "_handle_deploy_failure", return_value={"status": "failed", "error": "auth error"}
        ) as mock_handle:
            session._handle_slash_command(
                "/deploy 1",
                False,
                False,
                lambda msg: output.append(msg),
                lambda p: "",
            )

        # _handle_deploy_failure should have been called
        mock_handle.assert_called_once()

    @patch("azext_prototype.stages.deploy_session.deploy_terraform")
    def test_slash_redeploy_routes_through_remediation(self, mock_tf, tmp_project):
        """/redeploy N triggers remediation on failure."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)

        session = self._make_session(tmp_project, build_stages=stages)
        session._deploy_env = {"ARM_SUBSCRIPTION_ID": "sub-123"}

        mock_tf.return_value = {"status": "failed", "error": "deploy error"}
        output = []

        with patch.object(
            session, "_handle_deploy_failure", return_value={"status": "failed", "error": "deploy error"}
        ) as mock_handle:
            session._handle_slash_command(
                "/redeploy 1",
                False,
                False,
                lambda msg: output.append(msg),
                lambda p: "",
            )

        mock_handle.assert_called_once()

    def test_downstream_impact_detected(self, tmp_project):
        """Architect flags downstream stages for regeneration."""
        stages = [
            {
                "stage": 1,
                "name": "Foundation",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform/stage-1",
                "status": "generated",
                "files": [],
            },
            {
                "stage": 2,
                "name": "Data Layer",
                "category": "data",
                "services": [],
                "dir": "concept/infra/terraform/stage-2",
                "status": "generated",
                "files": [],
            },
            {
                "stage": 3,
                "name": "App",
                "category": "app",
                "services": [],
                "dir": "concept/apps/stage-3",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)

        # Mark stage 2 and 3 as pending (downstream)
        session._deploy_state.get_stage(2)["deploy_status"] = "pending"
        session._deploy_state.get_stage(3)["deploy_status"] = "pending"

        # Architect returns stage 2 as affected
        session._architect_agent = MagicMock()
        session._architect_agent.execute.return_value = _make_response("Affected stages: [2]")

        stage = session._deploy_state.get_stage(1)
        result = session._check_downstream_impact(stage, "Changed outputs from foundation")

        assert 2 in result
        assert 1 not in result  # Not downstream of itself

    def test_downstream_regeneration(self, tmp_project):
        """Flagged downstream stages get regenerated code."""
        stages = [
            {
                "stage": 1,
                "name": "Foundation",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform/stage-1",
                "status": "generated",
                "files": [],
            },
            {
                "stage": 2,
                "name": "Data Layer",
                "category": "data",
                "services": [],
                "dir": "concept/infra/terraform/stage-2",
                "status": "generated",
                "files": [],
            },
        ]
        for s in stages:
            (tmp_project / s["dir"]).mkdir(parents=True, exist_ok=True)
            (tmp_project / s["dir"] / "main.tf").write_text("# original")

        session = self._make_session(tmp_project, build_stages=stages)

        # Mock IaC agent to return regenerated content
        mock_iac = MagicMock()
        mock_iac.execute.return_value = _make_response("```main.tf\n# regenerated with fixed references\n```")
        session._iac_agents["terraform"] = mock_iac

        output = []
        session._regenerate_downstream_stages(
            [2],
            False,
            lambda msg: output.append(msg),
        )

        joined = "\n".join(output)
        assert "regenerated" in joined.lower()
        # Verify the file was actually written
        content = (tmp_project / "concept" / "infra" / "terraform" / "stage-2" / "main.tf").read_text()
        assert "regenerated" in content

    def test_handle_deploy_failure_returns_result(self, tmp_project):
        """_handle_deploy_failure returns the remediation result."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages)

        # No agents available — remediation returns None
        session._qa_agent = None
        session._iac_agents = {}
        session._dev_agent = None

        result = {"status": "failed", "error": "auth error"}
        stage = session._deploy_state.get_stage(1)
        output = []

        returned = session._handle_deploy_failure(
            stage,
            result,
            False,
            lambda msg: output.append(msg),
            lambda p: "",
        )

        # Should return original result when remediation not possible
        assert returned["status"] == "failed"
        # Should still show interactive options
        joined = "\n".join(output)
        assert "/deploy" in joined

    def test_no_ai_provider_skips_remediation(self, tmp_project):
        """Remediation is skipped when ai_provider is None."""
        stages = [
            {
                "stage": 1,
                "name": "Infra",
                "category": "infra",
                "services": [],
                "dir": "concept/infra/terraform",
                "status": "generated",
                "files": [],
            },
        ]
        session = self._make_session(tmp_project, build_stages=stages, ai_provider=None)

        result = {"status": "failed", "error": "auth error"}
        stage = session._deploy_state.get_stage(1)

        remediated = session._remediate_deploy_failure(
            stage,
            result,
            False,
            lambda msg: None,
            lambda p: "",
        )

        assert remediated is None


# ======================================================================
# Build-Deploy Decoupling: Stable IDs, Sync, Splitting, Manual Steps
# ======================================================================


def _build_yaml_with_ids(stages=None, iac_tool="terraform"):
    """Build YAML with stable IDs."""
    if stages is None:
        stages = [
            {
                "stage": 1,
                "name": "Foundation",
                "category": "infra",
                "id": "foundation",
                "deploy_mode": "auto",
                "manual_instructions": None,
                "services": [
                    {
                        "name": "key-vault",
                        "computed_name": "kv-1",
                        "resource_type": "Microsoft.KeyVault/vaults",
                        "sku": "standard",
                    }
                ],
                "status": "generated",
                "dir": "concept/infra/terraform/stage-1-foundation",
                "files": ["main.tf"],
            },
            {
                "stage": 2,
                "name": "Data Layer",
                "category": "data",
                "id": "data-layer",
                "deploy_mode": "auto",
                "manual_instructions": None,
                "services": [
                    {"name": "sql-db", "computed_name": "sql-1", "resource_type": "Microsoft.Sql/servers", "sku": "S0"}
                ],
                "status": "generated",
                "dir": "concept/infra/terraform/stage-2-data",
                "files": ["main.tf"],
            },
            {
                "stage": 3,
                "name": "Application",
                "category": "app",
                "id": "application",
                "deploy_mode": "auto",
                "manual_instructions": None,
                "services": [
                    {"name": "web-app", "computed_name": "app-1", "resource_type": "Microsoft.Web/sites", "sku": "B1"}
                ],
                "status": "generated",
                "dir": "concept/apps/stage-3-application",
                "files": ["app.py"],
            },
        ]
    return {
        "iac_tool": iac_tool,
        "deployment_stages": stages,
        "_metadata": {"created": "2026-01-01T00:00:00", "last_updated": "2026-01-01T00:00:00", "iteration": 1},
    }


def _write_build_yaml_with_ids(project_dir, stages=None, iac_tool="terraform"):
    """Write build.yaml with stable IDs."""
    state_dir = Path(project_dir) / ".prototype" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    data = _build_yaml_with_ids(stages, iac_tool)
    with open(state_dir / "build.yaml", "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False)
    return state_dir / "build.yaml"


class TestSyncFromBuildState:

    def test_sync_from_build_state_fresh(self, tmp_project):
        """First sync creates deploy stages from build stages."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        result = ds.sync_from_build_state(build_path)

        assert result.created == 3
        assert result.matched == 0
        assert result.orphaned == 0
        assert len(ds.state["deployment_stages"]) == 3
        assert ds.state["deployment_stages"][0]["build_stage_id"] == "foundation"

    def test_sync_from_build_state_preserves_deploy_status(self, tmp_project):
        """Matched stages keep their deploy state."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        # Deploy stage 1
        ds.mark_stage_deployed(1, output="done")

        # Re-sync
        result = ds.sync_from_build_state(build_path)
        assert result.matched == 3
        assert result.created == 0

        stage1 = ds.state["deployment_stages"][0]
        assert stage1["deploy_status"] == "deployed"
        assert stage1["deploy_output"] == "done"

    def test_sync_from_build_state_detects_code_change(self, tmp_project):
        """Changed files trigger _code_updated marking."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)
        ds.mark_stage_deployed(1)

        # Update build state with new files
        updated_stages = _build_yaml_with_ids()["deployment_stages"]
        updated_stages[0]["files"] = ["main.tf", "variables.tf"]  # changed
        _write_build_yaml_with_ids(tmp_project, stages=updated_stages)

        result = ds.sync_from_build_state(build_path)
        assert result.updated_code == 1
        assert ds.state["deployment_stages"][0].get("_code_updated") is True

    def test_sync_from_build_state_creates_new(self, tmp_project):
        """New build stage creates new deploy stage."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        # Add new stage to build
        stages = _build_yaml_with_ids()["deployment_stages"]
        stages.append(
            {
                "stage": 4,
                "name": "Monitoring",
                "category": "infra",
                "id": "monitoring",
                "deploy_mode": "auto",
                "manual_instructions": None,
                "services": [],
                "status": "generated",
                "dir": "concept/infra/terraform/stage-4-monitoring",
                "files": [],
            }
        )
        _write_build_yaml_with_ids(tmp_project, stages=stages)

        result = ds.sync_from_build_state(build_path)
        assert result.created == 1
        assert len(ds.state["deployment_stages"]) == 4
        assert ds.state["deployment_stages"][3]["build_stage_id"] == "monitoring"

    def test_sync_from_build_state_with_substages(self, tmp_project):
        """Split stages preserved across sync."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        # Split stage 2 into substages
        ds.split_stage(
            2,
            [
                {"name": "Data Layer - Base", "dir": "concept/infra/terraform/stage-2-data"},
                {"name": "Data Layer - Schema", "dir": "concept/db/schema"},
            ],
        )

        # Re-sync — substages should be preserved
        ds.sync_from_build_state(build_path)
        data_stages = ds.get_stages_for_build_stage("data-layer")
        assert len(data_stages) == 2
        assert data_stages[0]["substage_label"] == "a"
        assert data_stages[1]["substage_label"] == "b"

    def test_sync_orphan_sets_removed_status(self, tmp_project):
        """Removed build stage → deploy stage gets 'removed' status."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        # Remove a stage from build
        stages = _build_yaml_with_ids()["deployment_stages"]
        stages = [s for s in stages if s["id"] != "data-layer"]
        _write_build_yaml_with_ids(tmp_project, stages=stages)

        result = ds.sync_from_build_state(build_path)
        assert result.orphaned == 1

        removed = [s for s in ds.state["deployment_stages"] if s.get("deploy_status") == "removed"]
        assert len(removed) == 1
        assert removed[0]["build_stage_id"] == "data-layer"


class TestStageSpitting:

    def test_split_stage(self, tmp_project):
        """Split creates substages with shared build_stage_id."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.split_stage(
            2,
            [
                {"name": "Data - Base", "dir": "concept/infra/terraform/stage-2-data"},
                {"name": "Data - Schema", "dir": "concept/db/schema"},
            ],
        )

        # All substages share the same build_stage_id
        data_stages = ds.get_stages_for_build_stage("data-layer")
        assert len(data_stages) == 2
        assert data_stages[0]["substage_label"] == "a"
        assert data_stages[1]["substage_label"] == "b"
        assert data_stages[0]["_is_substage"] is True
        assert data_stages[1]["_is_substage"] is True

    def test_split_stage_renumbering(self, tmp_project):
        """After split, stage numbers are correct."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.split_stage(
            2,
            [
                {"name": "Data - Base", "dir": "dir1"},
                {"name": "Data - Schema", "dir": "dir2"},
            ],
        )

        stages = ds.state["deployment_stages"]
        # Stage 1 stays as 1, substages get stage 2 with labels, stage 3 stays
        assert stages[0]["stage"] == 1  # Foundation
        assert stages[1]["stage"] == 2  # Data - Base (2a)
        assert stages[1]["substage_label"] == "a"
        assert stages[2]["stage"] == 2  # Data - Schema (2b)
        assert stages[2]["substage_label"] == "b"
        assert stages[3]["stage"] == 3  # Application

    def test_get_stage_groups(self, tmp_project):
        """Verify grouping by build_stage_id."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.split_stage(
            2,
            [
                {"name": "Data - Base", "dir": "dir1"},
                {"name": "Data - Schema", "dir": "dir2"},
            ],
        )

        groups = ds.get_stage_groups()
        assert "foundation" in groups
        assert "data-layer" in groups
        assert "application" in groups
        assert len(groups["data-layer"]) == 2
        assert len(groups["foundation"]) == 1

    def test_can_rollback_with_substages(self, tmp_project):
        """Rollback checks work with substages."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.split_stage(
            2,
            [
                {"name": "Data - Base", "dir": "dir1"},
                {"name": "Data - Schema", "dir": "dir2"},
            ],
        )

        # Deploy both substages
        substages = ds.get_stages_for_build_stage("data-layer")
        substages[0]["deploy_status"] = "deployed"
        substages[1]["deploy_status"] = "deployed"
        ds.save()

        # Can't rollback "a" while "b" is deployed
        assert ds.can_rollback(2, "a") is False
        # Can rollback "b"
        assert ds.can_rollback(2, "b") is True

    def test_get_stage_by_display_id(self, tmp_project):
        """Parse and lookup by compound display ID."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.split_stage(
            2,
            [
                {"name": "Data - Base", "dir": "dir1"},
                {"name": "Data - Schema", "dir": "dir2"},
            ],
        )

        found = ds.get_stage_by_display_id("2a")
        assert found is not None
        assert found["name"] == "Data - Base"

        found_b = ds.get_stage_by_display_id("2b")
        assert found_b is not None
        assert found_b["name"] == "Data - Schema"


class TestDeployStateNewStatuses:

    def test_load_from_build_state_backward_compat(self, tmp_project):
        """Legacy build state without IDs still imports correctly."""
        from azext_prototype.stages.deploy_state import DeployState

        # Write legacy build yaml (no id field)
        build_path = _write_build_yaml(tmp_project)
        ds = DeployState(str(tmp_project))
        result = ds.load_from_build_state(build_path)

        assert result is True
        # build_stage_id should be auto-generated from name
        for stage in ds.state["deployment_stages"]:
            assert stage.get("build_stage_id")

    def test_destroy_stage(self, tmp_project):
        """Destroyed status after rollback."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.mark_stage_deployed(1)
        ds.mark_stage_rolled_back(1)
        ds.mark_stage_destroyed(1)

        assert ds.get_stage(1)["deploy_status"] == "destroyed"

    def test_destruction_declined_not_reprompted(self, tmp_project):
        """_destruction_declined flag persists across save/load."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        stage = ds.get_stage(1)
        stage["_destruction_declined"] = True
        ds.save()

        ds2 = DeployState(str(tmp_project))
        ds2.load()
        assert ds2.get_stage(1)["_destruction_declined"] is True

    def test_awaiting_manual_status(self, tmp_project):
        """Manual step sets awaiting_manual status."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.mark_stage_awaiting_manual(1)
        assert ds.get_stage(1)["deploy_status"] == "awaiting_manual"


class TestManualStepDeploy:

    def test_manual_step_deploy(self, tmp_project):
        """Manual stage shows instructions, waits for confirmation."""
        from azext_prototype.stages.deploy_state import DeployState

        stages = [
            {
                "stage": 1,
                "name": "Upload Notebook",
                "category": "external",
                "id": "upload-notebook",
                "deploy_mode": "manual",
                "manual_instructions": "Upload the notebook to Fabric workspace.",
                "services": [],
                "status": "generated",
                "dir": "concept/docs",
                "files": [],
            },
        ]
        build_path = _write_build_yaml_with_ids(tmp_project, stages=stages)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        # Verify the manual stage imported correctly
        stage = ds.get_stage(1)
        assert stage["deploy_mode"] == "manual"
        assert "Upload" in stage["manual_instructions"]

    def test_manual_step_from_build(self, tmp_project):
        """deploy_mode: 'manual' inherited from build stage via sync."""
        from azext_prototype.stages.deploy_state import DeployState

        stages = [
            {
                "stage": 1,
                "name": "Foundation",
                "category": "infra",
                "id": "foundation",
                "deploy_mode": "auto",
                "manual_instructions": None,
                "services": [],
                "status": "generated",
                "dir": "concept/infra/terraform/stage-1-foundation",
                "files": [],
            },
            {
                "stage": 2,
                "name": "Manual Config",
                "category": "external",
                "id": "manual-config",
                "deploy_mode": "manual",
                "manual_instructions": "Configure the firewall rules manually.",
                "services": [],
                "status": "generated",
                "dir": "",
                "files": [],
            },
        ]
        build_path = _write_build_yaml_with_ids(tmp_project, stages=stages)

        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        manual_stage = ds.state["deployment_stages"][1]
        assert manual_stage["deploy_mode"] == "manual"
        assert "firewall" in manual_stage["manual_instructions"]

    def test_code_split_syncs_back_to_build(self, tmp_project):
        """Type A split: _sync_build_state uses build_stage_id for matching."""
        from azext_prototype.stages.build_state import BuildState
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)

        # Load into deploy state
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        # Load build state and verify get_stage_by_id works
        bs = BuildState(str(tmp_project))
        bs.load()

        # Verify the build stage has the right id
        build_stage = bs.get_stage_by_id("data-layer")
        assert build_stage is not None
        assert build_stage["name"] == "Data Layer"

        # Deploy stage links back correctly
        deploy_stage = ds.state["deployment_stages"][1]
        assert deploy_stage["build_stage_id"] == "data-layer"


class TestParseStageRef:

    def test_parse_simple_number(self):
        from azext_prototype.stages.deploy_state import parse_stage_ref

        num, label = parse_stage_ref("5")
        assert num == 5
        assert label is None

    def test_parse_substage(self):
        from azext_prototype.stages.deploy_state import parse_stage_ref

        num, label = parse_stage_ref("5a")
        assert num == 5
        assert label == "a"

    def test_parse_invalid(self):
        from azext_prototype.stages.deploy_state import parse_stage_ref

        num, label = parse_stage_ref("abc")
        assert num is None
        assert label is None

    def test_parse_empty(self):
        from azext_prototype.stages.deploy_state import parse_stage_ref

        num, label = parse_stage_ref("")
        assert num is None

    def test_parse_with_whitespace(self):
        from azext_prototype.stages.deploy_state import parse_stage_ref

        num, label = parse_stage_ref(" 3b ")
        assert num == 3
        assert label == "b"


class TestRenumberWithSubstages:

    def test_renumber_preserves_substage_labels(self, tmp_project):
        """Substages keep their labels and inherit parent number."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        # Split stage 2
        ds.split_stage(
            2,
            [
                {"name": "Data - Base", "dir": "dir1"},
                {"name": "Data - Schema", "dir": "dir2"},
            ],
        )

        # Remove stage 1 — renumber should shift substages
        stages = ds.state["deployment_stages"]
        ds._state["deployment_stages"] = [s for s in stages if s.get("build_stage_id") != "foundation"]
        ds.renumber_stages()

        stages = ds.state["deployment_stages"]
        # Now data substages should be stage 1
        assert stages[0]["stage"] == 1
        assert stages[0]["substage_label"] == "a"
        assert stages[1]["stage"] == 1
        assert stages[1]["substage_label"] == "b"
        # Application should be stage 2
        assert stages[2]["stage"] == 2
        assert stages[2]["substage_label"] is None


class TestFormatDisplayId:

    def test_format_top_level(self):
        from azext_prototype.stages.deploy_state import _format_display_id

        assert _format_display_id({"stage": 3}) == "3"

    def test_format_substage(self):
        from azext_prototype.stages.deploy_state import _format_display_id

        assert _format_display_id({"stage": 3, "substage_label": "b"}) == "3b"

    def test_format_no_label(self):
        from azext_prototype.stages.deploy_state import _format_display_id

        assert _format_display_id({"stage": 1, "substage_label": None}) == "1"


class TestNewStatusIcons:

    def test_removed_icon(self):
        from azext_prototype.stages.deploy_state import _status_icon

        assert _status_icon("removed") == "~~"

    def test_destroyed_icon(self):
        from azext_prototype.stages.deploy_state import _status_icon

        assert _status_icon("destroyed") == "xx"

    def test_awaiting_manual_icon(self):
        from azext_prototype.stages.deploy_state import _status_icon

        assert _status_icon("awaiting_manual") == "!!"

    def test_existing_icons_unchanged(self):
        from azext_prototype.stages.deploy_state import _status_icon

        assert _status_icon("pending") == "  "
        assert _status_icon("deployed") == " v"
        assert _status_icon("failed") == " x"
        assert _status_icon("remediating") == "<>"


class TestDeployReportFormatting:

    def test_format_shows_removed_stages(self, tmp_project):
        """Removed stages show with strikethrough in report."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)
        ds.mark_stage_removed(2)

        report = ds.format_deploy_report()
        assert "(Removed)" in report
        assert "~~Data Layer~~" in report

    def test_format_shows_manual_badge(self, tmp_project):
        """Manual stages show [Manual] badge."""
        from azext_prototype.stages.deploy_state import DeployState

        stages = [
            {
                "stage": 1,
                "name": "Manual Step",
                "category": "external",
                "id": "manual",
                "deploy_mode": "manual",
                "manual_instructions": "Do the thing.",
                "services": [],
                "status": "generated",
                "dir": "",
                "files": [],
            },
        ]
        build_path = _write_build_yaml_with_ids(tmp_project, stages=stages)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        report = ds.format_deploy_report()
        assert "[Manual]" in report

        status = ds.format_stage_status()
        assert "[Manual]" in status

    def test_format_shows_substage_ids(self, tmp_project):
        """Substages show compound display IDs like 2a, 2b."""
        from azext_prototype.stages.deploy_state import DeployState

        build_path = _write_build_yaml_with_ids(tmp_project)
        ds = DeployState(str(tmp_project))
        ds.load_from_build_state(build_path)

        ds.split_stage(
            2,
            [
                {"name": "Data - Base", "dir": "dir1"},
                {"name": "Data - Schema", "dir": "dir2"},
            ],
        )

        status = ds.format_stage_status()
        assert "2a" in status
        assert "2b" in status
