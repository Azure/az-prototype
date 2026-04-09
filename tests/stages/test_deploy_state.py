"""Tests for DeployState — stage sync, legacy fallback, state persistence.

Covers:
- Stage sync with build state (matched, orphaned, new stages)
- Legacy fallback matching (name+capability)
- Post-load backfill of build_stage_ids
- Stage splitting (1:N divergence)
- Stage status transitions (deploying, deployed, failed, rolled_back, etc.)
- Rollback ordering enforcement
- Preflight result tracking
- Audit logging (deploy_log, rollback_log)
- Display formatting methods
- parse_stage_ref / _format_display_id / _status_icon
- Conversation tracking
- add_patch_stages / renumber_stages
"""

from pathlib import Path

import pytest
import yaml

from azext_prototype.stages.deploy_state import (
    DeployState,
    _enrich_deploy_fields,
    _format_display_id,
    _status_icon,
    parse_stage_ref,
)

# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def deploy_state(tmp_project):
    ds = DeployState(str(tmp_project))
    return ds


@pytest.fixture
def deploy_state_with_stages(deploy_state):
    """Deploy state with 2 stages loaded."""
    deploy_state._state["deployment_stages"] = [
        {
            "stage": 1,
            "name": "Foundation",
            "capability": "infra",
            "services": [{"name": "kv"}],
            "build_stage_id": "foundation",
            "deploy_status": "pending",
            "deploy_timestamp": None,
            "deploy_output": "",
            "deploy_error": "",
            "rollback_timestamp": None,
            "remediation_attempts": 0,
            "deploy_mode": "auto",
            "manual_instructions": None,
            "substage_label": None,
            "_is_substage": False,
            "_destruction_declined": False,
            "dir": "concept/infra/stage-1",
            "files": ["main.tf"],
        },
        {
            "stage": 2,
            "name": "Application",
            "capability": "app",
            "services": [{"name": "web"}],
            "build_stage_id": "application",
            "deploy_status": "pending",
            "deploy_timestamp": None,
            "deploy_output": "",
            "deploy_error": "",
            "rollback_timestamp": None,
            "remediation_attempts": 0,
            "deploy_mode": "auto",
            "manual_instructions": None,
            "substage_label": None,
            "_is_substage": False,
            "_destruction_declined": False,
            "dir": "concept/apps/stage-2",
            "files": ["app.py"],
        },
    ]
    return deploy_state


# ======================================================================
# load_from_build_state
# ======================================================================


class TestLoadFromBuildState:
    """Test importing deployment stages from build.yaml."""

    def test_imports_stages(self, deploy_state, project_with_build):
        build_path = Path(str(project_with_build)) / ".prototype" / "state" / "build.yaml"
        result = deploy_state.load_from_build_state(build_path)
        assert result is True
        stages = deploy_state._state["deployment_stages"]
        assert len(stages) == 2
        assert stages[0]["build_stage_id"] is not None
        assert stages[0]["deploy_status"] == "pending"

    def test_missing_build_file(self, deploy_state, tmp_path):
        result = deploy_state.load_from_build_state(tmp_path / "missing.yaml")
        assert result is False

    def test_empty_build_stages(self, deploy_state, tmp_path):
        build_file = tmp_path / "build.yaml"
        build_file.write_text(yaml.dump({"deployment_stages": []}), encoding="utf-8")
        result = deploy_state.load_from_build_state(build_file)
        assert result is False

    def test_bad_yaml(self, deploy_state, tmp_path):
        build_file = tmp_path / "build.yaml"
        build_file.write_text(": invalid: yaml: {{", encoding="utf-8")
        result = deploy_state.load_from_build_state(build_file)
        assert result is False

    def test_iac_tool_carried_over(self, deploy_state, tmp_path):
        build_file = tmp_path / "build.yaml"
        build_file.write_text(
            yaml.dump(
                {
                    "iac_tool": "bicep",
                    "deployment_stages": [{"stage": 1, "name": "Foundation"}],
                }
            ),
            encoding="utf-8",
        )
        deploy_state.load_from_build_state(build_file)
        assert deploy_state._state["iac_tool"] == "bicep"


# ======================================================================
# sync_from_build_state
# ======================================================================


class TestSyncFromBuildState:
    """Test smart reconciliation: matched, orphaned, new stages."""

    def test_matched_stages(self, deploy_state_with_stages, tmp_path):
        """Build state with same IDs → matched, no new or orphaned."""
        build_file = tmp_path / "build.yaml"
        build_file.write_text(
            yaml.dump(
                {
                    "deployment_stages": [
                        {"id": "foundation", "name": "Foundation", "capability": "infra"},
                        {"id": "application", "name": "Application", "capability": "app"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = deploy_state_with_stages.sync_from_build_state(build_file)
        assert result.matched == 2
        assert result.created == 0
        assert result.orphaned == 0

    def test_new_stage_created(self, deploy_state_with_stages, tmp_path):
        """Build state has an extra stage → created."""
        build_file = tmp_path / "build.yaml"
        build_file.write_text(
            yaml.dump(
                {
                    "deployment_stages": [
                        {"id": "foundation", "name": "Foundation", "capability": "infra"},
                        {"id": "application", "name": "Application", "capability": "app"},
                        {"id": "database", "name": "Database", "capability": "db"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = deploy_state_with_stages.sync_from_build_state(build_file)
        assert result.matched == 2
        assert result.created == 1
        assert any("Database" in d for d in result.details)

    def test_orphaned_stage(self, deploy_state_with_stages, tmp_path):
        """Build state removed a stage → orphaned."""
        build_file = tmp_path / "build.yaml"
        build_file.write_text(
            yaml.dump(
                {
                    "deployment_stages": [
                        {"id": "foundation", "name": "Foundation", "capability": "infra"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = deploy_state_with_stages.sync_from_build_state(build_file)
        assert result.orphaned == 1
        # The orphaned stage should be marked as "removed"
        orphaned = [
            s for s in deploy_state_with_stages._state["deployment_stages"] if s.get("deploy_status") == "removed"
        ]
        assert len(orphaned) == 1

    def test_legacy_fallback_matching(self, deploy_state, tmp_path):
        """Stage without build_stage_id matches by name+capability."""
        deploy_state._state["deployment_stages"] = [
            {
                "stage": 1,
                "name": "Foundation",
                "capability": "infra",
                "deploy_status": "deployed",
                "deploy_mode": "auto",
            }
        ]
        build_file = tmp_path / "build.yaml"
        build_file.write_text(
            yaml.dump(
                {
                    "deployment_stages": [
                        {"id": "foundation", "name": "Foundation", "capability": "infra"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = deploy_state.sync_from_build_state(build_file)
        assert result.matched == 1
        # build_stage_id should now be set
        stage = deploy_state._state["deployment_stages"][0]
        assert stage.get("build_stage_id") == "foundation"

    def test_code_change_detection(self, deploy_state_with_stages, tmp_path):
        """When a matched stage's code changed, mark _code_updated."""
        deploy_state_with_stages._state["deployment_stages"][0]["deploy_status"] = "deployed"
        build_file = tmp_path / "build.yaml"
        build_file.write_text(
            yaml.dump(
                {
                    "deployment_stages": [
                        {
                            "id": "foundation",
                            "name": "Foundation",
                            "capability": "infra",
                            "dir": "concept/infra/stage-1-v2",
                            "files": ["main.tf", "new.tf"],
                        },
                        {"id": "application", "name": "Application", "capability": "app"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = deploy_state_with_stages.sync_from_build_state(build_file)
        assert result.updated_code == 1

    def test_missing_build_file(self, deploy_state, tmp_path):
        result = deploy_state.sync_from_build_state(tmp_path / "missing.yaml")
        assert "not found" in result.details[0].lower()

    def test_bad_yaml(self, deploy_state, tmp_path):
        build_file = tmp_path / "build.yaml"
        build_file.write_text(": bad yaml {{", encoding="utf-8")
        result = deploy_state.sync_from_build_state(build_file)
        assert len(result.details) == 1

    def test_empty_deployment_stages(self, deploy_state, tmp_path):
        build_file = tmp_path / "build.yaml"
        build_file.write_text(yaml.dump({"deployment_stages": []}), encoding="utf-8")
        result = deploy_state.sync_from_build_state(build_file)
        assert "no deployment_stages" in result.details[0].lower()


# ======================================================================
# Post-load backfill
# ======================================================================


class TestPostLoadBackfill:
    """Test _backfill_build_stage_ids on legacy state."""

    def test_backfills_missing_ids(self, deploy_state):
        deploy_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Data Layer"},
        ]
        deploy_state._backfill_build_stage_ids()
        stage = deploy_state._state["deployment_stages"][0]
        assert stage["build_stage_id"] == "data-layer"
        assert "deploy_status" in stage  # _enrich_deploy_fields was called

    def test_preserves_existing_ids(self, deploy_state):
        deploy_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Foundation", "build_stage_id": "custom-id"},
        ]
        deploy_state._backfill_build_stage_ids()
        assert deploy_state._state["deployment_stages"][0]["build_stage_id"] == "custom-id"


# ======================================================================
# Stage splitting
# ======================================================================


class TestStageSplitting:
    """Test split_stage for 1:N divergence."""

    def test_split_creates_substages(self, deploy_state_with_stages):
        deploy_state_with_stages.split_stage(
            1,
            [
                {"name": "Foundation-VNet", "dir": "concept/infra/vnet"},
                {"name": "Foundation-KV", "dir": "concept/infra/kv"},
            ],
        )
        stages = deploy_state_with_stages._state["deployment_stages"]
        substages = [s for s in stages if s.get("substage_label")]
        assert len(substages) == 2
        assert substages[0]["substage_label"] == "a"
        assert substages[1]["substage_label"] == "b"
        assert all(s["_is_substage"] for s in substages)
        assert all(s["build_stage_id"] == "foundation" for s in substages)

    def test_split_nonexistent_stage(self, deploy_state_with_stages):
        """Splitting a stage that doesn't exist is a no-op."""
        deploy_state_with_stages.split_stage(99, [{"name": "X", "dir": "x"}])
        # No change
        substages = [s for s in deploy_state_with_stages._state["deployment_stages"] if s.get("substage_label")]
        assert len(substages) == 0


# ======================================================================
# Stage status transitions
# ======================================================================


class TestStageStatusTransitions:
    """Test all status transition methods."""

    def test_mark_deploying(self, deploy_state_with_stages):
        deploy_state_with_stages.mark_stage_deploying(1)
        assert deploy_state_with_stages.get_stage(1)["deploy_status"] == "deploying"

    def test_mark_deployed(self, deploy_state_with_stages):
        deploy_state_with_stages.mark_stage_deployed(1, output="tf output")
        stage = deploy_state_with_stages.get_stage(1)
        assert stage["deploy_status"] == "deployed"
        assert stage["deploy_output"] == "tf output"
        assert stage["deploy_error"] == ""
        assert stage["deploy_timestamp"] is not None

    def test_mark_failed(self, deploy_state_with_stages):
        deploy_state_with_stages.mark_stage_failed(1, error="init failed")
        stage = deploy_state_with_stages.get_stage(1)
        assert stage["deploy_status"] == "failed"
        assert stage["deploy_error"] == "init failed"

    def test_mark_rolled_back(self, deploy_state_with_stages):
        deploy_state_with_stages.mark_stage_rolled_back(1)
        stage = deploy_state_with_stages.get_stage(1)
        assert stage["deploy_status"] == "rolled_back"
        assert stage["rollback_timestamp"] is not None

    def test_mark_remediating_bumps_counter(self, deploy_state_with_stages):
        deploy_state_with_stages.mark_stage_remediating(1)
        assert deploy_state_with_stages.get_stage(1)["remediation_attempts"] == 1
        deploy_state_with_stages.mark_stage_remediating(1)
        assert deploy_state_with_stages.get_stage(1)["remediation_attempts"] == 2

    def test_reset_stage_to_pending(self, deploy_state_with_stages):
        deploy_state_with_stages.mark_stage_failed(1, error="err")
        deploy_state_with_stages.reset_stage_to_pending(1)
        stage = deploy_state_with_stages.get_stage(1)
        assert stage["deploy_status"] == "pending"
        assert stage["deploy_error"] == ""

    def test_mark_stage_removed(self, deploy_state_with_stages):
        deploy_state_with_stages.mark_stage_removed(1)
        assert deploy_state_with_stages.get_stage(1)["deploy_status"] == "removed"

    def test_mark_stage_destroyed(self, deploy_state_with_stages):
        deploy_state_with_stages.mark_stage_destroyed(1)
        assert deploy_state_with_stages.get_stage(1)["deploy_status"] == "destroyed"

    def test_mark_stage_awaiting_manual(self, deploy_state_with_stages):
        deploy_state_with_stages.mark_stage_awaiting_manual(1)
        assert deploy_state_with_stages.get_stage(1)["deploy_status"] == "awaiting_manual"

    def test_mark_nonexistent_stage_no_error(self, deploy_state_with_stages):
        """Marking a nonexistent stage is a no-op."""
        deploy_state_with_stages.mark_stage_deploying(99)
        deploy_state_with_stages.mark_stage_deployed(99)
        deploy_state_with_stages.mark_stage_failed(99)
        deploy_state_with_stages.mark_stage_rolled_back(99)


# ======================================================================
# Rollback ordering
# ======================================================================


class TestRollbackOrdering:
    """Test can_rollback enforces ordered rollback."""

    def test_can_rollback_when_no_later_deployed(self, deploy_state_with_stages):
        deploy_state_with_stages.mark_stage_deployed(1)
        assert deploy_state_with_stages.can_rollback(1) is True

    def test_cannot_rollback_when_later_deployed(self, deploy_state_with_stages):
        deploy_state_with_stages.mark_stage_deployed(1)
        deploy_state_with_stages.mark_stage_deployed(2)
        assert deploy_state_with_stages.can_rollback(1) is False

    def test_can_rollback_highest_stage(self, deploy_state_with_stages):
        deploy_state_with_stages.mark_stage_deployed(1)
        deploy_state_with_stages.mark_stage_deployed(2)
        assert deploy_state_with_stages.can_rollback(2) is True

    def test_get_rollback_candidates_sorted(self, deploy_state_with_stages):
        deploy_state_with_stages.mark_stage_deployed(1)
        deploy_state_with_stages.mark_stage_deployed(2)
        candidates = deploy_state_with_stages.get_rollback_candidates()
        assert candidates[0]["stage"] == 2
        assert candidates[1]["stage"] == 1


# ======================================================================
# Stage queries
# ======================================================================


class TestStageQueries:
    """Test various stage query methods."""

    def test_get_stage(self, deploy_state_with_stages):
        assert deploy_state_with_stages.get_stage(1)["name"] == "Foundation"
        assert deploy_state_with_stages.get_stage(99) is None

    def test_get_all_stages_for_num(self, deploy_state_with_stages):
        stages = deploy_state_with_stages.get_all_stages_for_num(1)
        assert len(stages) == 1

    def test_get_pending_stages(self, deploy_state_with_stages):
        pending = deploy_state_with_stages.get_pending_stages()
        assert len(pending) == 2

    def test_get_deployed_stages(self, deploy_state_with_stages):
        deploy_state_with_stages.mark_stage_deployed(1)
        deployed = deploy_state_with_stages.get_deployed_stages()
        assert len(deployed) == 1

    def test_get_failed_stages(self, deploy_state_with_stages):
        deploy_state_with_stages.mark_stage_failed(1)
        failed = deploy_state_with_stages.get_failed_stages()
        assert len(failed) == 1

    def test_get_stage_by_display_id(self, deploy_state_with_stages):
        stage = deploy_state_with_stages.get_stage_by_display_id("1")
        assert stage is not None
        assert stage["name"] == "Foundation"

    def test_get_stage_by_display_id_invalid(self, deploy_state_with_stages):
        assert deploy_state_with_stages.get_stage_by_display_id("abc") is None

    def test_get_stage_by_display_id_nonexistent(self, deploy_state_with_stages):
        assert deploy_state_with_stages.get_stage_by_display_id("99") is None

    def test_get_stage_groups(self, deploy_state_with_stages):
        groups = deploy_state_with_stages.get_stage_groups()
        assert "foundation" in groups
        assert "application" in groups

    def test_get_stages_for_build_stage(self, deploy_state_with_stages):
        stages = deploy_state_with_stages.get_stages_for_build_stage("foundation")
        assert len(stages) == 1


# ======================================================================
# Preflight
# ======================================================================


class TestPreflight:
    """Test preflight result tracking."""

    def test_set_and_get_preflight_results(self, deploy_state):
        results = [
            {"name": "az-login", "status": "pass", "message": "Logged in"},
            {"name": "rg-exists", "status": "fail", "message": "RG not found", "fix_command": "az group create"},
        ]
        deploy_state.set_preflight_results(results)
        failures = deploy_state.get_preflight_failures()
        assert len(failures) == 1
        assert failures[0]["name"] == "rg-exists"

    def test_empty_preflight(self, deploy_state):
        assert deploy_state.get_preflight_failures() == []


# ======================================================================
# Audit logging
# ======================================================================


class TestAuditLogging:
    """Test deploy and rollback log entries."""

    def test_deploy_log_entry(self, deploy_state):
        deploy_state.add_deploy_log_entry(1, "deploying")
        logs = deploy_state._state["deploy_log"]
        assert len(logs) == 1
        assert logs[0]["stage"] == 1
        assert logs[0]["action"] == "deploying"

    def test_rollback_log_entry(self, deploy_state):
        deploy_state.add_rollback_log_entry(1, "user requested")
        logs = deploy_state._state["rollback_log"]
        assert len(logs) == 1
        assert logs[0]["stage"] == 1


# ======================================================================
# Conversation tracking
# ======================================================================


class TestConversationTracking:
    """Test exchange recording."""

    def test_update_from_exchange(self, deploy_state):
        deploy_state.update_from_exchange("deploy stage 1", "Deploying...", 1)
        history = deploy_state._state["conversation_history"]
        assert len(history) == 1
        assert history[0]["user"] == "deploy stage 1"
        assert history[0]["exchange"] == 1


# ======================================================================
# add_patch_stages / renumber_stages
# ======================================================================


class TestPatchAndRenumber:
    """Test adding patch stages and renumbering."""

    def test_add_patch_stages_before_docs(self, deploy_state):
        deploy_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Foundation", "capability": "infra"},
            {"stage": 2, "name": "Documentation", "capability": "docs"},
        ]
        deploy_state.add_patch_stages([{"name": "Hotfix", "capability": "infra", "build_stage_id": "hotfix"}])
        stages = deploy_state._state["deployment_stages"]
        names = [s["name"] for s in stages]
        assert names.index("Hotfix") < names.index("Documentation")

    def test_renumber_stages(self, deploy_state):
        deploy_state._state["deployment_stages"] = [
            {"stage": 5, "name": "A"},
            {"stage": 10, "name": "B"},
        ]
        deploy_state.renumber_stages()
        assert deploy_state._state["deployment_stages"][0]["stage"] == 1
        assert deploy_state._state["deployment_stages"][1]["stage"] == 2


# ======================================================================
# Formatting
# ======================================================================


class TestFormatting:
    """Test display formatting methods."""

    def test_format_stage_status_empty(self, deploy_state):
        result = deploy_state.format_stage_status()
        assert "No deployment stages" in result

    def test_format_stage_status_with_stages(self, deploy_state_with_stages):
        result = deploy_state_with_stages.format_stage_status()
        assert "Foundation" in result
        assert "Application" in result
        assert "0/2" in result

    def test_format_deploy_report(self, deploy_state_with_stages):
        deploy_state_with_stages.mark_stage_deployed(1)
        report = deploy_state_with_stages.format_deploy_report()
        assert "Deploy Report" in report
        assert "Foundation" in report

    def test_format_preflight_report_empty(self, deploy_state):
        result = deploy_state.format_preflight_report()
        assert "No preflight checks" in result

    def test_format_preflight_report_with_results(self, deploy_state):
        deploy_state.set_preflight_results(
            [
                {"name": "login", "status": "pass", "message": "OK"},
                {"name": "rg", "status": "fail", "message": "Missing", "fix_command": "az group create"},
            ]
        )
        result = deploy_state.format_preflight_report()
        assert "login" in result
        assert "Fix:" in result

    def test_format_outputs_empty(self, deploy_state):
        result = deploy_state.format_outputs()
        assert "No deployment outputs" in result

    def test_format_outputs_with_data(self, deploy_state):
        deploy_state._state["captured_outputs"] = {
            "terraform": {"endpoint": "https://app.com"},
        }
        result = deploy_state.format_outputs()
        assert "endpoint" in result
        assert "https://app.com" in result


# ======================================================================
# Module-level helpers
# ======================================================================


class TestModuleHelpers:
    """Test parse_stage_ref, _format_display_id, _status_icon."""

    def test_parse_stage_ref_number_only(self):
        num, label = parse_stage_ref("5")
        assert num == 5
        assert label is None

    def test_parse_stage_ref_with_label(self):
        num, label = parse_stage_ref("5a")
        assert num == 5
        assert label == "a"

    def test_parse_stage_ref_invalid(self):
        num, label = parse_stage_ref("abc")
        assert num is None
        assert label is None

    def test_parse_stage_ref_whitespace(self):
        num, label = parse_stage_ref("  3b  ")
        assert num == 3
        assert label == "b"

    def test_format_display_id_plain(self):
        assert _format_display_id({"stage": 3}) == "3"

    def test_format_display_id_with_label(self):
        assert _format_display_id({"stage": 3, "substage_label": "b"}) == "3b"

    def test_status_icon_mapping(self):
        assert _status_icon("pending") == "  "
        assert _status_icon("deploying") == ">>"
        assert _status_icon("deployed") == " v"
        assert _status_icon("failed") == " x"
        assert _status_icon("rolled_back") == " ~"
        assert _status_icon("unknown") == "  "


# ======================================================================
# _enrich_deploy_fields
# ======================================================================


class TestEnrichDeployFields:
    """Test _enrich_deploy_fields sets defaults."""

    def test_adds_all_fields(self):
        stage = {"name": "test"}
        enriched = _enrich_deploy_fields(stage)
        assert enriched["deploy_status"] == "pending"
        assert enriched["deploy_timestamp"] is None
        assert enriched["remediation_attempts"] == 0
        assert enriched["_is_substage"] is False

    def test_preserves_existing_values(self):
        stage = {"name": "test", "deploy_status": "deployed"}
        enriched = _enrich_deploy_fields(stage)
        assert enriched["deploy_status"] == "deployed"
