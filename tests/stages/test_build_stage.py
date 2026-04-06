"""Tests for BuildStage — guard conditions, state transitions, dry-run routing.

Covers:
- Multi-guard validation (3 prerequisites: project_initialized, discovery_complete, design_complete)
- State transitions (IN_PROGRESS, COMPLETED, FAILED)
- Reset behavior (clears build state and output dirs)
- Dry-run vs interactive routing
- Template matching with threshold scoring
- Design loading from state file
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from azext_prototype.stages.base import StageState

# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def build_stage():
    from azext_prototype.stages.build_stage import BuildStage

    return BuildStage()


@pytest.fixture
def agent_context(project_with_design, sample_config):
    from azext_prototype.agents.base import AgentContext

    provider = MagicMock()
    provider.provider_name = "github-models"
    provider.chat.return_value = MagicMock(
        content="ok",
        model="test",
        usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    )
    return AgentContext(
        project_config=sample_config,
        project_dir=str(project_with_design),
        ai_provider=provider,
    )


@pytest.fixture
def registry():
    return MagicMock()


# ======================================================================
# Guard validation
# ======================================================================


class TestBuildStageGuards:
    """Test multi-guard prerequisite checking."""

    def test_guards_return_three_guards(self, build_stage):
        guards = build_stage.get_guards()
        assert len(guards) == 3
        names = [g.name for g in guards]
        assert "project_initialized" in names
        assert "discovery_complete" in names
        assert "design_complete" in names

    def test_all_guards_pass(self, build_stage, project_with_design, monkeypatch):
        """All files exist → can_run returns True."""
        monkeypatch.chdir(project_with_design)
        # Ensure discovery.yaml exists
        disco = project_with_design / ".prototype" / "state" / "discovery.yaml"
        disco.write_text("exchange_count: 1", encoding="utf-8")

        can_run, failures = build_stage.can_run()
        assert can_run is True
        assert failures == []

    def test_missing_project_yaml(self, build_stage, tmp_path, monkeypatch):
        """No prototype.yaml → first guard fails."""
        monkeypatch.chdir(tmp_path)
        can_run, failures = build_stage.can_run()
        assert can_run is False
        assert any("prototype" in f.lower() or "init" in f.lower() for f in failures)

    def test_missing_discovery_state(self, build_stage, project_with_config, monkeypatch):
        """prototype.yaml exists but no discovery.yaml → discovery guard fails."""
        monkeypatch.chdir(project_with_config)
        can_run, failures = build_stage.can_run()
        assert can_run is False
        assert any("discovery" in f.lower() for f in failures)

    def test_missing_design_json(self, build_stage, project_with_config, monkeypatch):
        """Has prototype.yaml and discovery.yaml but no design.json → design guard fails."""
        monkeypatch.chdir(project_with_config)
        disco = project_with_config / ".prototype" / "state" / "discovery.yaml"
        disco.write_text("exchange_count: 1", encoding="utf-8")
        can_run, failures = build_stage.can_run()
        assert can_run is False
        assert any("design" in f.lower() for f in failures)


# ======================================================================
# State transitions
# ======================================================================


class TestBuildStageStateTransitions:
    """Test stage state moves correctly during execute."""

    def test_initial_state_not_started(self, build_stage):
        assert build_stage.state == StageState.NOT_STARTED

    def test_execute_sets_in_progress_then_completed(self, build_stage, agent_context, registry):
        """Dry-run sets IN_PROGRESS then COMPLETED."""
        result = build_stage.execute(agent_context, registry, dry_run=True, print_fn=lambda x: None)
        assert build_stage.state == StageState.COMPLETED
        assert result["status"] == "dry-run"

    def test_cancelled_session_sets_failed(self, build_stage, agent_context, registry):
        """When BuildSession returns cancelled, state goes to FAILED."""
        mock_result = MagicMock()
        mock_result.cancelled = True

        with patch("azext_prototype.stages.build_stage.BuildSession") as mock_session_cls:
            mock_session_cls.return_value.run.return_value = mock_result
            result = build_stage.execute(agent_context, registry, print_fn=lambda x: None)
        assert build_stage.state == StageState.FAILED
        assert result["status"] == "cancelled"

    def test_successful_session_sets_completed(self, build_stage, agent_context, registry):
        """When BuildSession completes successfully, state goes to COMPLETED."""
        mock_result = MagicMock()
        mock_result.cancelled = False
        mock_result.policy_overrides = []
        mock_result.files_generated = ["main.tf"]
        mock_result.deployment_stages = []
        mock_result.resources = []

        with (
            patch("azext_prototype.stages.build_stage.BuildSession") as mock_session_cls,
            patch("azext_prototype.stages.build_stage.ProjectConfig") as mock_config_cls,
        ):
            mock_config_cls.return_value.load.return_value = None
            mock_config_cls.return_value.get.return_value = "terraform"
            mock_session_cls.return_value.run.return_value = mock_result
            result = build_stage.execute(agent_context, registry, print_fn=lambda x: None)
        assert build_stage.state == StageState.COMPLETED
        assert result["status"] == "success"

    def test_missing_architecture_raises(self, build_stage, agent_context, registry):
        """If design.json has no architecture key, CLIError is raised."""
        from knack.util import CLIError

        # Overwrite design.json with empty architecture
        design_path = Path(agent_context.project_dir) / ".prototype" / "state" / "design.json"
        with open(design_path, "w") as f:
            json.dump({"artifacts": []}, f)

        with pytest.raises(CLIError, match="No architecture"):
            build_stage.execute(agent_context, registry, print_fn=lambda x: None)


# ======================================================================
# Reset behavior
# ======================================================================


class TestBuildStageReset:
    """Test reset clears build state and output directories."""

    def test_reset_cleans_output_dirs(self, build_stage, agent_context, registry):
        """--reset cleans concept/infra, concept/apps, etc."""
        project_dir = Path(agent_context.project_dir)
        # Create output dirs
        for d in build_stage._OUTPUT_DIRS:
            (project_dir / d).mkdir(parents=True, exist_ok=True)
            (project_dir / d / "test.tf").write_text("content", encoding="utf-8")

        # Run with reset + dry_run to avoid full session
        build_stage.execute(agent_context, registry, reset=True, dry_run=True, print_fn=lambda x: None)

        for d in build_stage._OUTPUT_DIRS:
            assert not (project_dir / d).is_dir()

    def test_reset_nonexistent_dirs_no_error(self, build_stage, agent_context, registry):
        """--reset with no existing output dirs should not error."""
        build_stage.execute(agent_context, registry, reset=True, dry_run=True, print_fn=lambda x: None)
        assert build_stage.state == StageState.COMPLETED


# ======================================================================
# Dry-run routing
# ======================================================================


class TestBuildStageDryRun:
    """Test dry-run mode behavior."""

    def test_dry_run_all_scope(self, build_stage, agent_context, registry):
        printed = []
        result = build_stage.execute(agent_context, registry, dry_run=True, scope="all", print_fn=printed.append)
        assert result["status"] == "dry-run"
        assert result["scope"] == "all"
        assert "infra" in result["results"]
        assert "apps" in result["results"]
        assert "db" in result["results"]
        assert "docs" in result["results"]

    def test_dry_run_infra_only(self, build_stage, agent_context, registry):
        printed = []
        result = build_stage.execute(agent_context, registry, dry_run=True, scope="infra", print_fn=printed.append)
        assert "infra" in result["results"]
        assert "apps" not in result["results"]

    def test_dry_run_apps_only(self, build_stage, agent_context, registry):
        printed = []
        result = build_stage.execute(agent_context, registry, dry_run=True, scope="apps", print_fn=printed.append)
        assert "apps" in result["results"]
        assert "infra" not in result["results"]

    def test_dry_run_with_templates(self, build_stage, agent_context, registry):
        """When templates match, dry-run shows template names."""
        printed = []
        mock_tmpl = MagicMock()
        mock_tmpl.display_name = "Web App"
        mock_tmpl.service_names.return_value = []

        with patch.object(build_stage, "_match_templates", return_value=[mock_tmpl]):
            build_stage.execute(agent_context, registry, dry_run=True, print_fn=printed.append)
        assert any("Web App" in p for p in printed)


# ======================================================================
# Template matching
# ======================================================================


class TestTemplateMatching:
    """Test template matching with threshold scoring."""

    def test_match_templates_above_threshold(self, build_stage):
        mock_tmpl = MagicMock()
        mock_tmpl.service_names.return_value = ["key-vault", "app-service"]

        mock_registry = MagicMock()
        mock_registry.list_templates.return_value = [mock_tmpl]

        with patch("azext_prototype.templates.registry.TemplateRegistry", return_value=mock_registry):
            design = {"architecture": "Deploy key-vault and app-service resources"}
            config = MagicMock()
            templates = build_stage._match_templates(design, config)
        assert len(templates) == 1

    def test_match_templates_below_threshold(self, build_stage):
        mock_tmpl = MagicMock()
        mock_tmpl.service_names.return_value = ["key-vault", "cosmos-db", "redis", "apim"]

        mock_registry = MagicMock()
        mock_registry.list_templates.return_value = [mock_tmpl]

        with patch("azext_prototype.templates.registry.TemplateRegistry", return_value=mock_registry):
            design = {"architecture": "Only key-vault is mentioned"}
            config = MagicMock()
            templates = build_stage._match_templates(design, config)
        assert len(templates) == 0

    def test_match_templates_empty_architecture(self, build_stage):
        design = {"architecture": ""}
        config = MagicMock()
        assert build_stage._match_templates(design, config) == []

    def test_match_templates_no_templates_available(self, build_stage):
        mock_registry = MagicMock()
        mock_registry.list_templates.return_value = []

        with patch("azext_prototype.templates.registry.TemplateRegistry", return_value=mock_registry):
            design = {"architecture": "something"}
            config = MagicMock()
            templates = build_stage._match_templates(design, config)
        assert templates == []


# ======================================================================
# Design loading
# ======================================================================


class TestLoadDesign:
    """Test _load_design from state file."""

    def test_load_existing_design(self, build_stage, project_with_design):
        design = build_stage._load_design(str(project_with_design))
        assert "architecture" in design

    def test_load_missing_design(self, build_stage, tmp_path):
        design = build_stage._load_design(str(tmp_path))
        assert design == {}
