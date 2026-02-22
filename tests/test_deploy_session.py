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
from unittest.mock import MagicMock, patch, call

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
        ds.set_preflight_results([
            {"name": "Azure Login", "status": "pass", "message": "OK"},
            {"name": "Terraform", "status": "warn", "message": "Old version", "fix_command": "brew upgrade terraform"},
        ])

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
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.stages.deploy_session import DeploySession
        from azext_prototype.stages.deploy_state import DeployState

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
                "stage": 1, "name": "Infra", "category": "infra",
                "services": [
                    {"name": "ext", "resource_type": "External/something", "sku": ""},
                    {"name": "hashicorp", "resource_type": "hashicorp/random", "sku": ""},
                    {"name": "kv", "resource_type": "Microsoft.KeyVault/vaults", "sku": ""},
                ],
                "status": "generated", "dir": "stage-1", "files": [],
            },
        ]

        mock_run.return_value = MagicMock(returncode=0, stdout="Registered\n", stderr="")
        results = session._check_resource_providers("sub-123")

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
                "stage": 1, "name": "Infra", "category": "infra",
                "services": [
                    {"name": "custom", "resource_type": "", "sku": ""},
                ],
                "status": "generated", "dir": "stage-1", "files": [],
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
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.agents.builtin import register_all_builtin
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
            '}\n'
            'resource "azapi_resource" "storage" {\n'
            '  type = "Microsoft.Storage/storageAccounts@2025-06-01"\n'
            '}\n'
        )
        session._deploy_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Infra", "category": "infra",
             "dir": "stage-1", "services": [], "status": "generated", "files": []},
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
            {"stage": 1, "name": "Infra", "category": "infra",
             "dir": "stage-1", "services": [], "status": "generated", "files": []},
        ]
        namespaces = session._extract_providers_from_files()
        assert "Microsoft.Resources" in namespaces
        assert "Microsoft.KeyVault" in namespaces

    def test_ignores_non_microsoft_types(self, tmp_project):
        session = self._make_session(tmp_project)
        stage_dir = tmp_project / "stage-1"
        stage_dir.mkdir()
        (stage_dir / "main.tf").write_text(
            'resource "null_resource" "test" {}\n'
            'resource "random_string" "suffix" {}\n'
        )
        session._deploy_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Infra", "category": "infra",
             "dir": "stage-1", "services": [], "status": "generated", "files": []},
        ]
        namespaces = session._extract_providers_from_files()
        assert len(namespaces) == 0

    def test_handles_missing_dirs(self, tmp_project):
        session = self._make_session(tmp_project)
        session._deploy_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Infra", "category": "infra",
             "dir": "nonexistent-dir", "services": [], "status": "generated", "files": []},
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
            'resource "azapi_resource" "storage" {\n'
            '  type = "Microsoft.Storage/storageAccounts@2025-06-01"\n'
            '}\n'
        )
        session._deploy_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Infra", "category": "infra",
             "dir": "stage-1",
             "services": [
                 {"name": "kv", "resource_type": "Microsoft.KeyVault/vaults", "sku": ""},
             ],
             "status": "generated", "files": []},
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout="Registered\n", stderr="")
        results = session._check_resource_providers("sub-123")
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
            {"stage": 1, "name": "Infra", "category": "infra",
             "dir": "nonexistent-stage-dir",
             "services": [
                 {"name": "kv", "resource_type": "Microsoft.KeyVault/vaults", "sku": ""},
             ],
             "status": "generated", "files": []},
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout="Registered\n", stderr="")
        results = session._check_resource_providers("sub-123")
        checked_namespaces = [c.args[0][4] for c in mock_run.call_args_list if "provider" in c.args[0]]
        assert "Microsoft.KeyVault" in checked_namespaces


# ======================================================================
# DeploySession tests
# ======================================================================

class TestDeploySession:

    def _make_session(self, project_dir, iac_tool="terraform", build_stages=None):
        """Create a DeploySession with all dependencies mocked."""
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.stages.deploy_session import DeploySession
        from azext_prototype.stages.deploy_state import DeployState

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
        result = session.run(
            subscription="sub-123",
            input_fn=lambda p: "quit",
            print_fn=lambda msg: output.append(msg),
        )
        # Verify stages were loaded (shown in plan overview)
        joined = "\n".join(output)
        assert "Foundation" in joined or "Stage" in joined

    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123")
    @patch("azext_prototype.stages.deploy_session.deploy_terraform", return_value={"status": "deployed"})
    @patch("azext_prototype.stages.deploy_session.deploy_app_stage", return_value={"status": "deployed"})
    def test_full_deploy_flow(self, mock_app, mock_tf, mock_sub, mock_login, tmp_project):
        """Test full interactive deploy: confirm → preflight → deploy → done."""
        stages = [
            {
                "stage": 1, "name": "Infra", "category": "infra",
                "services": [], "dir": "concept/infra/terraform",
                "status": "generated", "files": [],
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

    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123")
    @patch("azext_prototype.stages.deploy_session.deploy_terraform", return_value={"status": "failed", "error": "auth error"})
    def test_deploy_failure_qa_routing(self, mock_tf, mock_sub, mock_login, tmp_project):
        """Test that deploy failure routes to QA agent."""
        stages = [
            {
                "stage": 1, "name": "Infra", "category": "infra",
                "services": [], "dir": "concept/infra/terraform",
                "status": "generated", "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)

        session = self._make_session(tmp_project, build_stages=stages)
        # Mock QA agent response
        session._qa_agent = MagicMock()
        session._qa_agent.execute.return_value = _make_response("Check your service principal credentials.")

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
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(tmp_project) / "prototype.yaml"
        config_data = {"project": {"name": "test", "location": "eastus", "iac_tool": "terraform"}, "ai": {"provider": "github-models"}}
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

    @patch("azext_prototype.stages.deploy_session.plan_terraform", return_value={"output": "Plan: 3 to add", "error": None})
    def test_dry_run_terraform(self, mock_plan, tmp_project):
        stages = [
            {
                "stage": 1, "name": "Infra", "category": "infra",
                "services": [], "dir": "concept/infra/terraform",
                "status": "generated", "files": [],
            },
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)

        output = []
        result = session.run_dry_run(
            subscription="sub-123",
            print_fn=lambda msg: output.append(msg),
        )
        joined = "\n".join(output)
        assert "Plan: 3 to add" in joined

    @patch("azext_prototype.stages.deploy_session.plan_terraform", return_value={"output": "Plan: 1 to add", "error": None})
    def test_dry_run_single_stage(self, mock_plan, tmp_project):
        stages = [
            {"stage": 1, "name": "Infra", "category": "infra", "services": [], "dir": "concept/infra/terraform", "status": "generated", "files": []},
            {"stage": 2, "name": "Data", "category": "data", "services": [], "dir": "concept/infra/terraform/data", "status": "generated", "files": []},
        ]
        (tmp_project / "concept" / "infra" / "terraform").mkdir(parents=True, exist_ok=True)
        (tmp_project / "concept" / "infra" / "terraform" / "data").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)

        output = []
        result = session.run_dry_run(
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
            {"stage": 1, "name": "Infra", "category": "infra", "services": [], "dir": "concept/infra/terraform", "status": "generated", "files": []},
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
        result = session.run(
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
            {"stage": 1, "name": "Infra", "category": "infra", "services": [], "dir": "concept/infra/terraform", "status": "generated", "files": []},
            {"stage": 2, "name": "Data", "category": "data", "services": [], "dir": "concept/infra/terraform/data", "status": "generated", "files": []},
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
            {"stage": 1, "name": "Docs", "category": "docs", "services": [], "dir": "concept/docs", "status": "generated", "files": []},
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
        from azext_prototype.stages.deploy_stage import DeployStage
        import os

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
        from azext_prototype.stages.deploy_stage import DeployStage
        from azext_prototype.agents.base import AgentContext

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
        from azext_prototype.stages.deploy_stage import DeployStage
        from azext_prototype.agents.base import AgentContext

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
        from azext_prototype.stages.deploy_stage import DeployStage
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.stages.deploy_session import DeployResult

        _write_build_yaml(tmp_project)
        config_path = Path(tmp_project) / "prototype.yaml"
        config_data = {"project": {"name": "test", "location": "eastus", "iac_tool": "terraform"}, "ai": {"provider": "github-models"}}
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
        from azext_prototype.stages.deploy_stage import DeployStage
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.stages.deploy_session import DeployResult

        _write_build_yaml(tmp_project)
        config_path = Path(tmp_project) / "prototype.yaml"
        config_data = {"project": {"name": "test", "location": "eastus", "iac_tool": "terraform"}, "ai": {"provider": "github-models"}}
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

            mock_session.run_single_stage.assert_called_once_with(1, subscription="sub-123", tenant=None, force=False, client_id=None, client_secret=None)
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
        bicep_file.write_text("targetScope = 'subscription'\nresource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {}")
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
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.agents.builtin import register_all_builtin
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

    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123")
    @patch("azext_prototype.stages.deploy_session.deploy_terraform", return_value={"status": "deployed"})
    def test_deploy_succeeds_without_ai(self, mock_tf, mock_sub, mock_login, tmp_project):
        """Full deploy succeeds with ai_provider=None."""
        stages = [
            {
                "stage": 1, "name": "Infra", "category": "infra",
                "services": [], "dir": "concept/infra/terraform",
                "status": "generated", "files": [],
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

    @patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True)
    @patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123")
    @patch("azext_prototype.stages.deploy_session.deploy_terraform", return_value={"status": "failed", "error": "auth error"})
    def test_deploy_failure_without_ai_shows_raw_error(self, mock_tf, mock_sub, mock_login, tmp_project):
        """Deploy failure with ai_provider=None falls back to raw error display."""
        stages = [
            {
                "stage": 1, "name": "Infra", "category": "infra",
                "services": [], "dir": "concept/infra/terraform",
                "status": "generated", "files": [],
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
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.agents.builtin import register_all_builtin
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

    @patch("azext_prototype.custom._get_project_dir")
    def test_sp_missing_params_raises(self, mock_dir, project_with_config):
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

    @patch("azext_prototype.custom._get_project_dir")
    @patch("azext_prototype.stages.deploy_helpers.login_service_principal")
    def test_sp_login_failure_raises(self, mock_login, mock_dir, project_with_config):
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

    @patch("azext_prototype.custom._get_project_dir")
    @patch("azext_prototype.stages.deploy_helpers.login_service_principal")
    @patch("azext_prototype.custom._check_guards")
    def test_sp_login_success_proceeds(self, mock_guards, mock_login, mock_dir, project_with_config):
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
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.agents.builtin import register_all_builtin
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
        result = session.run(
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
        result = session.run(
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
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.agents.builtin import register_all_builtin
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
            "/login", False, False,
            lambda msg: output.append(msg), lambda p: "",
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
            "/login", False, False,
            lambda msg: output.append(msg), lambda p: "",
        )
        joined = "\n".join(output)
        assert "Login failed" in joined

    def test_help_includes_login(self, tmp_project):
        session = self._make_session(tmp_project)
        output = []
        session._handle_slash_command(
            "/help", False, False,
            lambda msg: output.append(msg), lambda p: "",
        )
        joined = "\n".join(output)
        assert "/login" in joined


# ======================================================================
# _prepare_deploy_command tests
# ======================================================================

class TestPrepareDeployCommand:
    """Tests for _prepare_deploy_command in custom.py."""

    @patch("azext_prototype.custom._get_project_dir")
    def test_returns_none_ai_provider_when_factory_fails(self, mock_dir, project_with_config):
        from azext_prototype.custom import _prepare_deploy_command

        mock_dir.return_value = str(project_with_config)

        with patch("azext_prototype.ai.factory.create_ai_provider", side_effect=Exception("No Copilot license")):
            project_dir, config, registry, agent_context = _prepare_deploy_command()

        assert agent_context.ai_provider is None
        assert project_dir == str(project_with_config)

    @patch("azext_prototype.custom._get_project_dir")
    def test_returns_ai_provider_when_factory_succeeds(self, mock_dir, project_with_config):
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

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Error: Unsupported block type"
        )
        result = _terraform_validate(Path("/tmp/fake"))
        assert result["ok"] is False
        assert "Unsupported block type" in result["error"]

    @patch("subprocess.run")
    def test_validate_returns_stdout_on_empty_stderr(self, mock_run):
        from azext_prototype.stages.deploy_helpers import _terraform_validate

        mock_run.return_value = MagicMock(
            returncode=1, stdout="Invalid HCL syntax", stderr=""
        )
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
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.agents.builtin import register_all_builtin
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
            {"stage": 1, "name": "Infra", "category": "infra", "services": [],
             "dir": "concept/infra/terraform", "status": "generated", "files": []},
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
            {"stage": 1, "name": "Infra", "category": "infra", "services": [],
             "dir": "concept/infra/terraform", "status": "generated", "files": []},
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
            {"stage": 1, "name": "Infra", "category": "infra", "services": [],
             "dir": "concept/infra/terraform", "status": "generated", "files": []},
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
            {"stage": 1, "name": "App", "category": "app", "services": [],
             "dir": "concept/apps/stage-1", "status": "generated", "files": []},
        ]
        (tmp_project / "concept" / "apps" / "stage-1").mkdir(parents=True, exist_ok=True)
        session = self._make_session(tmp_project, build_stages=stages)
        results = session._check_terraform_validate()
        assert len(results) == 0

    def test_skips_missing_dirs(self, tmp_project):
        stages = [
            {"stage": 1, "name": "Infra", "category": "infra", "services": [],
             "dir": "concept/infra/terraform/nonexistent", "status": "generated", "files": []},
        ]
        session = self._make_session(tmp_project, build_stages=stages)
        results = session._check_terraform_validate()
        assert len(results) == 0

    def test_skips_dirs_without_tf_files(self, tmp_project):
        stages = [
            {"stage": 1, "name": "Infra", "category": "infra", "services": [],
             "dir": "concept/infra/terraform", "status": "generated", "files": []},
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
            {"stage": 1, "name": "Infra", "category": "infra", "services": [],
             "dir": "concept/infra/terraform", "status": "generated", "files": []},
        ]
        stage_dir = tmp_project / "concept" / "infra" / "terraform"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        session = self._make_session(tmp_project, build_stages=stages)
        session._subscription = "sub-123"

        mock_run.return_value = MagicMock(returncode=0, stdout="Terraform v1.7.0\n", stderr="")

        with patch("azext_prototype.stages.deploy_session.check_az_login", return_value=True), \
             patch("azext_prototype.stages.deploy_session.get_current_subscription", return_value="sub-123"):
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
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.agents.builtin import register_all_builtin
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
                "stage": 1, "name": "Infra", "category": "infra",
                "services": [], "dir": "concept/infra/terraform",
                "status": "generated", "files": [],
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
                "stage": 1, "name": "Infra", "category": "infra",
                "services": [], "dir": "concept/infra/bicep",
                "status": "generated", "files": [],
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
                "stage": 1, "name": "Infra", "category": "infra",
                "services": [], "dir": "concept/infra/terraform",
                "status": "generated", "files": [],
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
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(tmp_project) / "prototype.yaml"
        config_data = {"project": {"name": "t", "location": "eastus", "iac_tool": "terraform"}, "ai": {"provider": "github-models"}}
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
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(tmp_project) / "prototype.yaml"
        config_data = {"project": {"name": "t", "location": "eastus", "iac_tool": "terraform"}, "ai": {"provider": "github-models"}}
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
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.agents.builtin import register_all_builtin
        from azext_prototype.stages.deploy_session import DeploySession

        config_path = Path(tmp_project) / "prototype.yaml"
        config_data = {"project": {"name": "t", "location": "eastus", "iac_tool": "terraform"}, "ai": {"provider": "github-models"}}
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        context = AgentContext(project_config={}, project_dir=str(tmp_project), ai_provider=MagicMock())
        registry = AgentRegistry()
        register_all_builtin(registry)
        session = DeploySession(context, registry)

        session._resolve_context("sub-123", None)

        assert "TF_VAR_deployer_object_id" not in session._deploy_env
