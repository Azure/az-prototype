"""Tests for azext_prototype.stages — guards, base, init, design, build, deploy."""


from unittest.mock import MagicMock, patch


from azext_prototype.stages.base import StageGuard, StageState
from azext_prototype.stages.guards import check_prerequisites


class TestStageState:
    """Test stage state enum."""

    def test_all_states_defined(self):
        states = list(StageState)
        assert StageState.NOT_STARTED in states
        assert StageState.IN_PROGRESS in states
        assert StageState.COMPLETED in states
        assert StageState.FAILED in states

    def test_string_values(self):
        assert StageState.NOT_STARTED.value == "not_started"
        assert StageState.COMPLETED.value == "completed"


class TestStageGuard:
    """Test stage guard dataclass."""

    def test_basic_guard(self):
        guard = StageGuard(
            name="test",
            description="Test guard",
            check_fn=lambda: True,
            error_message="Test failed",
        )
        assert guard.name == "test"
        assert guard.check_fn() is True

    def test_failing_guard(self):
        guard = StageGuard(
            name="fail",
            description="Fails",
            check_fn=lambda: False,
            error_message="Check failed",
        )
        assert guard.check_fn() is False


class TestCheckPrerequisites:
    """Test prerequisite checking for each stage."""

    @patch("azext_prototype.stages.guards._check_gh_installed", return_value=True)
    def test_init_prerequisites_pass(self, mock_check, tmp_project):
        passed, failures = check_prerequisites("init", str(tmp_project))
        # When gh is installed, init check should pass
        assert isinstance(passed, bool)
        assert isinstance(failures, list)

    @patch("azext_prototype.stages.guards._check_gh_installed", return_value=False)
    def test_init_gh_not_installed(self, mock_check, tmp_project):
        passed, failures = check_prerequisites("init", str(tmp_project))
        # gh not installed → should have a failure
        assert any("gh" in f.lower() or "github" in f.lower() for f in failures)

    def test_design_requires_config(self, project_with_config):
        passed, failures = check_prerequisites("design", str(project_with_config))
        # Config exists → config check should pass
        config_fail = [f for f in failures if "config" in f.lower()]
        assert len(config_fail) == 0

    def test_design_no_config_fails(self, tmp_project):
        passed, failures = check_prerequisites("design", str(tmp_project))
        # No config → should fail
        assert not passed or len(failures) > 0

    def test_deploy_requires_build(self, project_with_build):
        passed, failures = check_prerequisites("deploy", str(project_with_build))
        build_fail = [f for f in failures if "build" in f.lower()]
        assert len(build_fail) == 0


class TestBaseStageCanRun:
    """Test the can_run() method on BaseStage."""

    def test_can_run_all_pass(self):
        """A stage with passing guards should return (True, [])."""
        from azext_prototype.stages.init_stage import InitStage

        stage = InitStage()
        # Temporarily override guards to all pass
        stage.get_guards = lambda: [
            StageGuard(
                name="always_pass",
                description="Always passes",
                check_fn=lambda: True,
                error_message="Should not appear",
            ),
        ]

        can_run, failures = stage.can_run()
        assert can_run is True
        assert failures == []

    def test_can_run_guard_fails(self):
        from azext_prototype.stages.init_stage import InitStage

        stage = InitStage()
        stage.get_guards = lambda: [
            StageGuard(
                name="always_fail",
                description="Always fails",
                check_fn=lambda: False,
                error_message="This check always fails",
            ),
        ]

        can_run, failures = stage.can_run()
        assert can_run is False
        assert len(failures) == 1


class TestInitStage:
    """Test the init stage."""

    def test_init_stage_instantiates(self):
        from azext_prototype.stages.init_stage import InitStage

        stage = InitStage()
        assert stage.name == "init"
        assert stage.reentrant is False

    def test_init_stage_has_guards(self):
        from azext_prototype.stages.init_stage import InitStage

        stage = InitStage()
        guards = stage.get_guards()
        # No unconditional guards — gh check is conditional inside execute()
        assert len(guards) == 0


class TestDeployStage:
    """Test the deploy stage."""

    def test_deploy_stage_instantiates(self):
        from azext_prototype.stages.deploy_stage import DeployStage

        stage = DeployStage()
        assert stage is not None
        assert stage.name == "deploy"

    def test_deploy_stage_has_execute(self):
        from azext_prototype.stages.deploy_stage import DeployStage

        stage = DeployStage()
        assert hasattr(stage, "execute")
        assert callable(stage.execute)


class TestDeployBicepStaging:
    """Test Bicep staged deployment capabilities (via deploy_helpers)."""

    def test_find_bicep_params_main_parameters_json(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import find_bicep_params

        template = tmp_path / "main.bicep"
        template.write_text("resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {}")
        params = tmp_path / "main.parameters.json"
        params.write_text('{"parameters": {"location": {"value": "eastus"}}}')

        result = find_bicep_params(tmp_path, template)
        assert result == params

    def test_find_bicep_params_bicepparam(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import find_bicep_params

        template = tmp_path / "main.bicep"
        template.write_text("")
        bp = tmp_path / "main.bicepparam"
        bp.write_text("using './main.bicep'\nparam location = 'eastus'")

        result = find_bicep_params(tmp_path, template)
        assert result == bp

    def test_find_bicep_params_fallback_parameters_json(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import find_bicep_params

        template = tmp_path / "network.bicep"
        template.write_text("")
        params = tmp_path / "parameters.json"
        params.write_text('{"parameters": {}}')

        result = find_bicep_params(tmp_path, template)
        assert result == params

    def test_find_bicep_params_none_when_missing(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import find_bicep_params

        template = tmp_path / "main.bicep"
        template.write_text("")

        result = find_bicep_params(tmp_path, template)
        assert result is None

    def test_is_subscription_scoped_true(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import is_subscription_scoped

        bicep_file = tmp_path / "main.bicep"
        bicep_file.write_text("targetScope = 'subscription'\n\nresource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {}")

        assert is_subscription_scoped(bicep_file) is True

    def test_is_subscription_scoped_false(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import is_subscription_scoped

        bicep_file = tmp_path / "main.bicep"
        bicep_file.write_text("resource sa 'Microsoft.Storage/storageAccounts@2023-01-01' = {}")

        assert is_subscription_scoped(bicep_file) is False

    def test_get_deploy_location_from_params(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import get_deploy_location

        params = tmp_path / "parameters.json"
        params.write_text('{"parameters": {"location": {"value": "westus2"}}}')

        result = get_deploy_location(tmp_path)
        assert result == "westus2"

    def test_get_deploy_location_returns_none(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import get_deploy_location

        result = get_deploy_location(tmp_path)
        assert result is None

    @patch("subprocess.run")
    def test_deploy_bicep_resource_group_scope(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_bicep

        bicep_dir = tmp_path / "stage1"
        bicep_dir.mkdir()
        (bicep_dir / "main.bicep").write_text("resource sa 'Microsoft.Storage/storageAccounts@2023-01-01' = {}")

        mock_run.return_value = MagicMock(returncode=0, stdout='{"properties":{}}', stderr="")

        result = deploy_bicep(bicep_dir, "sub-123", "my-rg")
        assert result["status"] == "deployed"
        assert result["scope"] == "resourceGroup"
        assert result["template"] == "main.bicep"

        # Verify az deployment group create was called (not sub create)
        cmd_parts = mock_run.call_args[0][0]
        assert "group" in cmd_parts
        assert "create" in cmd_parts

    @patch("subprocess.run")
    def test_deploy_bicep_subscription_scope(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_bicep

        bicep_dir = tmp_path / "stage1"
        bicep_dir.mkdir()
        (bicep_dir / "main.bicep").write_text("targetScope = 'subscription'\n\nresource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {}")

        mock_run.return_value = MagicMock(returncode=0, stdout='{"properties":{}}', stderr="")

        result = deploy_bicep(bicep_dir, "sub-123", "")
        assert result["status"] == "deployed"
        assert result["scope"] == "subscription"

        # Verify az deployment sub create was called
        cmd_parts = mock_run.call_args[0][0]
        assert "sub" in cmd_parts

    @patch("subprocess.run")
    def test_deploy_bicep_with_params_file(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_bicep

        (tmp_path / "main.bicep").write_text("param location string\n")
        (tmp_path / "main.parameters.json").write_text('{"parameters":{"location":{"value":"eastus"}}}')

        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")

        deploy_bicep(tmp_path, "sub-123", "my-rg")

        cmd_parts = mock_run.call_args[0][0]
        assert "--parameters" in cmd_parts

    def test_deploy_bicep_no_bicep_files_skips(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_bicep

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        result = deploy_bicep(empty_dir, "sub-123", "my-rg")
        assert result["status"] == "skipped"

    def test_deploy_bicep_fallback_to_first_file(self, tmp_path):
        """When no main.bicep exists, uses the first .bicep file."""
        from azext_prototype.stages.deploy_helpers import deploy_bicep

        (tmp_path / "network.bicep").write_text("resource vnet 'Microsoft.Network/virtualNetworks@2023-05-01' = {}")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            result = deploy_bicep(tmp_path, "sub-123", "my-rg")

        assert result["status"] == "deployed"
        assert result["template"] == "network.bicep"

    def test_deploy_bicep_rg_required_for_rg_scope(self, tmp_path):
        from azext_prototype.stages.deploy_helpers import deploy_bicep

        (tmp_path / "main.bicep").write_text("resource sa 'Microsoft.Storage/storageAccounts@2023-01-01' = {}")

        result = deploy_bicep(tmp_path, "sub-123", "")
        assert result["status"] == "failed"
        assert "Resource group required" in result["error"]

    @patch("subprocess.run")
    def test_whatif_bicep_runs(self, mock_run, tmp_path):
        from azext_prototype.stages.deploy_helpers import whatif_bicep

        (tmp_path / "main.bicep").write_text("resource sa 'Microsoft.Storage/storageAccounts@2023-01-01' = {}")
        mock_run.return_value = MagicMock(returncode=0, stdout="Resource changes: 1 to create", stderr="")

        result = whatif_bicep(tmp_path, "sub-123", "my-rg")
        assert result["status"] == "previewed"
        assert "Resource changes" in result["output"]

        cmd_parts = mock_run.call_args[0][0]
        assert "what-if" in cmd_parts


class TestDesignStage:
    """Test the design stage."""

    def test_design_stage_instantiates(self):
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()
        assert stage.name == "design"
        assert stage.reentrant is True

    def test_design_stage_has_execute(self):
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()
        assert callable(stage.execute)

    def test_design_execute_single_pass(
        self, project_with_config, mock_agent_context, populated_registry
    ):
        """Design stage runs architect agent and writes docs in single-pass mode."""
        from azext_prototype.stages.design_stage import DesignStage
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.stages.discovery import DiscoveryResult

        # Patch guards so the stage can run
        stage = DesignStage()
        stage.get_guards = lambda: []  # type: ignore[assignment]

        # Ensure agents return predictable content
        mock_agent_context.project_dir = str(project_with_config)
        mock_agent_context.ai_provider.chat.return_value = AIResponse(
            content="## Architecture\nMock architecture output",
            model="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )

        # Mock the discovery session so we test the architect path
        mock_discovery_result = DiscoveryResult(
            requirements="Build a simple web app",
            conversation=[],
            policy_overrides=[],
            exchange_count=2,
        )
        with patch(
            "azext_prototype.stages.design_stage.DiscoverySession"
        ) as MockDS:
            MockDS.return_value.run.return_value = mock_discovery_result

            result = stage.execute(
                mock_agent_context,
                populated_registry,
                **{"context": "Build a simple web app", "interactive": False},
            )

        assert result["status"] == "success"
        assert result["iteration"] >= 1
        arch_path = project_with_config / "concept" / "docs" / "ARCHITECTURE.md"
        assert arch_path.exists()

    def test_design_refine_loop_accept_immediately(
        self, project_with_config, mock_agent_context, populated_registry
    ):
        """When user immediately accepts, loop exits without extra iterations."""
        from azext_prototype.stages.design_stage import DesignStage
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.stages.discovery import DiscoveryResult

        stage = DesignStage()
        stage.get_guards = lambda: []  # type: ignore[assignment]

        mock_agent_context.project_dir = str(project_with_config)
        mock_agent_context.ai_provider.chat.return_value = AIResponse(
            content="## Architecture\nInitial design",
            model="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )

        mock_discovery_result = DiscoveryResult(
            requirements="Build a web app",
            conversation=[],
            policy_overrides=[],
            exchange_count=2,
        )

        # User presses Enter (empty input) → accept immediately
        with patch("builtins.input", return_value=""), patch(
            "azext_prototype.stages.design_stage.DiscoverySession"
        ) as MockDS:
            MockDS.return_value.run.return_value = mock_discovery_result
            result = stage.execute(
                mock_agent_context,
                populated_registry,
                **{"context": "Build a web app", "interactive": True},
            )

        assert result["status"] == "success"
        # Should be iteration 1 — no extra refinement iterations
        assert result["iteration"] == 1

    def test_design_refine_loop_one_feedback_then_accept(
        self, project_with_config, mock_agent_context, populated_registry
    ):
        """User gives feedback once, then accepts."""
        from azext_prototype.stages.design_stage import DesignStage
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.stages.discovery import DiscoveryResult

        stage = DesignStage()
        stage.get_guards = lambda: []  # type: ignore[assignment]

        mock_agent_context.project_dir = str(project_with_config)
        # Iterative flow: plan → section(s) → IaC review → refinement
        mock_agent_context.ai_provider.chat.side_effect = [
            AIResponse(  # planning call — 1-section plan
                content='```json\n[{"name": "Solution Overview", "context": "Overview"}]\n```',
                model="gpt-4o",
                usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            ),
            AIResponse(  # section generation
                content="## Solution Overview\nInitial design",
                model="gpt-4o",
                usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            ),
            AIResponse(  # IaC agent review (orchestrator delegation)
                content="IaC review: looks good",
                model="gpt-4o",
                usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            ),
            AIResponse(  # architect refinement
                content="## Architecture\nRefined design with Redis",
                model="gpt-4o",
                usage={"prompt_tokens": 15, "completion_tokens": 25, "total_tokens": 40},
            ),
        ]

        mock_discovery_result = DiscoveryResult(
            requirements="Build a web app",
            conversation=[],
            policy_overrides=[],
            exchange_count=3,
        )

        # User gives feedback "Add Redis", then types "done"
        with patch("builtins.input", side_effect=["Add Redis caching", "done"]), patch(
            "azext_prototype.stages.design_stage.DiscoverySession"
        ) as MockDS:
            MockDS.return_value.run.return_value = mock_discovery_result
            result = stage.execute(
                mock_agent_context,
                populated_registry,
                **{"context": "Build a web app", "interactive": True},
            )

        assert result["status"] == "success"
        assert result["iteration"] == 2  # initial + 1 refinement

        # Architecture doc should have the refined content
        arch_path = project_with_config / "concept" / "docs" / "ARCHITECTURE.md"
        arch_content = arch_path.read_text(encoding="utf-8")
        assert "Refined design with Redis" in arch_content

    def test_design_refine_loop_eof_exits_gracefully(
        self, project_with_config, mock_agent_context, populated_registry
    ):
        """EOFError (non-interactive terminal) exits the loop gracefully."""
        from azext_prototype.stages.design_stage import DesignStage
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.stages.discovery import DiscoveryResult

        stage = DesignStage()
        stage.get_guards = lambda: []  # type: ignore[assignment]

        mock_agent_context.project_dir = str(project_with_config)
        mock_agent_context.ai_provider.chat.return_value = AIResponse(
            content="## Architecture\nDesign",
            model="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )

        mock_discovery_result = DiscoveryResult(
            requirements="Build a web app",
            conversation=[],
            policy_overrides=[],
            exchange_count=2,
        )

        with patch("builtins.input", side_effect=EOFError), patch(
            "azext_prototype.stages.design_stage.DiscoverySession"
        ) as MockDS:
            MockDS.return_value.run.return_value = mock_discovery_result
            result = stage.execute(
                mock_agent_context,
                populated_registry,
                **{"context": "Build a web app", "interactive": True},
            )

        assert result["status"] == "success"

    def test_design_state_persists_decisions(
        self, project_with_config, mock_agent_context, populated_registry
    ):
        """Feedback from refinement loop is stored in design state decisions."""
        import json as _json
        from azext_prototype.stages.design_stage import DesignStage
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.stages.discovery import DiscoveryResult

        stage = DesignStage()
        stage.get_guards = lambda: []  # type: ignore[assignment]

        mock_agent_context.project_dir = str(project_with_config)
        # Iterative flow: plan → section → IaC review → refinement
        mock_agent_context.ai_provider.chat.side_effect = [
            AIResponse(  # planning call — 1-section plan
                content='```json\n[{"name": "Solution Overview", "context": "Overview"}]\n```',
                model="gpt-4o",
                usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            ),
            AIResponse(  # section generation
                content="## Solution Overview\nInitial",
                model="gpt-4o",
                usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            ),
            AIResponse(  # IaC agent review (orchestrator delegation)
                content="IaC review: looks good",
                model="gpt-4o",
                usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            ),
            AIResponse(  # architect refinement
                content="## Architecture\nRevised",
                model="gpt-4o",
                usage={"prompt_tokens": 15, "completion_tokens": 25, "total_tokens": 40},
            ),
        ]

        mock_discovery_result = DiscoveryResult(
            requirements="Build a web app",
            conversation=[],
            policy_overrides=[],
            exchange_count=3,
        )

        with patch("builtins.input", side_effect=["Use AKS instead of App Service", "accept"]), patch(
            "azext_prototype.stages.design_stage.DiscoverySession"
        ) as MockDS:
            MockDS.return_value.run.return_value = mock_discovery_result
            stage.execute(
                mock_agent_context,
                populated_registry,
                **{"context": "Build a web app", "interactive": True},
            )

        state_path = project_with_config / ".prototype" / "state" / "design.json"
        state = _json.loads(state_path.read_text(encoding="utf-8"))
        assert len(state["decisions"]) == 1
        assert "AKS" in state["decisions"][0]["feedback"]


    def test_design_iterative_planning_fallback(
        self, project_with_config, mock_agent_context, populated_registry
    ):
        """If the planning call returns invalid JSON, default sections are used."""
        from azext_prototype.stages.design_stage import DesignStage
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.stages.discovery import DiscoveryResult

        stage = DesignStage()
        stage.get_guards = lambda: []  # type: ignore[assignment]

        mock_agent_context.project_dir = str(project_with_config)
        # return_value → every call returns the same (including planning)
        mock_agent_context.ai_provider.chat.return_value = AIResponse(
            content="## Architecture\nSome content that is not JSON",
            model="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )

        mock_discovery_result = DiscoveryResult(
            requirements="Build a web app",
            conversation=[],
            policy_overrides=[],
            exchange_count=2,
        )
        with patch(
            "azext_prototype.stages.design_stage.DiscoverySession"
        ) as MockDS:
            MockDS.return_value.run.return_value = mock_discovery_result
            result = stage.execute(
                mock_agent_context,
                populated_registry,
                **{"context": "Build a web app", "interactive": False},
            )

        assert result["status"] == "success"
        # Planning fallback still produces architecture
        arch_path = project_with_config / "concept" / "docs" / "ARCHITECTURE.md"
        assert arch_path.exists()
        content = arch_path.read_text(encoding="utf-8")
        # Should contain content from multiple section calls (9 default sections)
        assert len(content) > 0

    def test_design_iterative_section_failure(
        self, project_with_config, mock_agent_context, populated_registry
    ):
        """If a section call fails, the error propagates (partial work is saved by caller)."""
        from azext_prototype.stages.design_stage import DesignStage
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.stages.discovery import DiscoveryResult

        stage = DesignStage()
        stage.get_guards = lambda: []  # type: ignore[assignment]

        mock_agent_context.project_dir = str(project_with_config)
        # Planning succeeds with 2 sections, first section succeeds, second fails
        mock_agent_context.ai_provider.chat.side_effect = [
            AIResponse(  # planning
                content='```json\n[{"name": "Overview", "context": "x"}, {"name": "Services", "context": "y"}]\n```',
                model="gpt-4o",
                usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            ),
            AIResponse(  # section 1 OK
                content="## Overview\nContent here",
                model="gpt-4o",
                usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            ),
            RuntimeError("API connection lost"),  # section 2 fails
        ]

        mock_discovery_result = DiscoveryResult(
            requirements="Build a web app",
            conversation=[],
            policy_overrides=[],
            exchange_count=2,
        )
        import pytest
        with pytest.raises(RuntimeError, match="API connection lost"):
            with patch(
                "azext_prototype.stages.design_stage.DiscoverySession"
            ) as MockDS:
                MockDS.return_value.run.return_value = mock_discovery_result
                stage.execute(
                    mock_agent_context,
                    populated_registry,
                    **{"context": "Build a web app", "interactive": False},
                )

    def test_design_iterative_usage_accumulation(
        self, project_with_config, mock_agent_context, populated_registry
    ):
        """Token usage is accumulated across all iterative section calls."""
        from azext_prototype.stages.design_stage import DesignStage
        from azext_prototype.ai.provider import AIResponse

        stage = DesignStage()

        # Directly test _generate_architecture_sections with known sections
        from azext_prototype.config import ProjectConfig
        config = ProjectConfig(str(project_with_config))
        config.load()

        sections = [
            {"name": "Overview", "context": "x"},
            {"name": "Services", "context": "y"},
        ]

        mock_agent_context.project_dir = str(project_with_config)
        mock_agent_context.ai_provider.chat.side_effect = [
            AIResponse(
                content="## Overview\nFirst section",
                model="gpt-4o",
                usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
            ),
            AIResponse(
                content="## Services\nSecond section",
                model="gpt-4o",
                usage={"prompt_tokens": 150, "completion_tokens": 250, "total_tokens": 400},
            ),
        ]

        # Build a mock architect that delegates to the mock provider
        architect = populated_registry.find_by_capability(
            __import__("azext_prototype.agents.base", fromlist=["AgentCapability"]).AgentCapability.ARCHITECT
        )[0]

        output, usage = stage._generate_architecture_sections(
            None,  # no UI
            mock_agent_context,
            architect,
            config,
            sections,
            "Build a web app",
            print,
        )

        assert "## Overview" in output
        assert "## Services" in output
        assert usage["prompt_tokens"] == 250
        assert usage["completion_tokens"] == 450
        assert usage["total_tokens"] == 700

    def test_design_architecture_sliding_window(
        self, project_with_config, mock_agent_context, populated_registry
    ):
        """Older sections are summarised to headings only when >3 accumulated."""
        from azext_prototype.stages.design_stage import DesignStage
        from azext_prototype.ai.provider import AIResponse

        stage = DesignStage()
        from azext_prototype.config import ProjectConfig

        config = ProjectConfig(str(project_with_config))
        config.load()

        # 6 sections — by section 6 the first 2 should be headings-only
        section_names = ["Overview", "Services", "Diagram", "Data Model", "Security", "Cost"]
        sections = [{"name": n, "context": f"ctx-{n}"} for n in section_names]

        responses = [
            AIResponse(
                content=f"## {n}\nContent for {n} section with details.",
                model="gpt-4o",
                usage={"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
            )
            for n in section_names
        ]

        mock_agent_context.project_dir = str(project_with_config)
        mock_agent_context.ai_provider.chat.side_effect = responses

        architect = populated_registry.find_by_capability(
            __import__("azext_prototype.agents.base", fromlist=["AgentCapability"]).AgentCapability.ARCHITECT
        )[0]

        output, usage = stage._generate_architecture_sections(
            None, mock_agent_context, architect, config, sections, "Build an app", print
        )

        # All sections appear in the final output (accumulated list is untouched)
        for n in section_names:
            assert f"## {n}" in output

        # Inspect the prompt sent for the LAST section (6th call)
        last_call_args = mock_agent_context.ai_provider.chat.call_args_list[-1]
        last_prompt = last_call_args[0][0][-1].content  # last message = user prompt

        # Older sections (Overview, Services) should be summarised
        assert "omitted for brevity" in last_prompt
        # Recent sections (Data Model, Security) should be in full
        assert "Content for Data Model section" in last_prompt
        assert "Content for Security section" in last_prompt
        # Overview full content should NOT appear in the last prompt
        assert "Content for Overview section" not in last_prompt

    def test_design_skip_discovery_uses_existing_state(
        self, project_with_config, mock_agent_context, populated_registry
    ):
        """--skip-discovery skips the discovery session and uses existing state."""
        from azext_prototype.stages.design_stage import DesignStage
        from azext_prototype.stages.discovery_state import DiscoveryState
        from azext_prototype.ai.provider import AIResponse

        # Create a discovery state with content
        ds = DiscoveryState(str(project_with_config))
        ds.load()
        ds.state["project"]["summary"] = "Build a web API with PostgreSQL"
        ds.state["requirements"]["functional"] = ["REST endpoints", "User auth"]
        ds.state["_metadata"]["exchange_count"] = 5
        ds.save()

        stage = DesignStage()
        stage.get_guards = lambda: []  # type: ignore[assignment]

        mock_agent_context.project_dir = str(project_with_config)
        mock_agent_context.ai_provider.chat.return_value = AIResponse(
            content="## Architecture\nSkip-discovery output",
            model="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )

        # User presses Enter (no extra context) at the skip-discovery prompt
        # DiscoverySession should NOT be called
        with patch(
            "azext_prototype.stages.design_stage.DiscoverySession"
        ) as MockDS:
            result = stage.execute(
                mock_agent_context,
                populated_registry,
                **{
                    "context": "", "interactive": False, "skip_discovery": True,
                    "input_fn": lambda _: "", "print_fn": lambda x: None,
                },
            )
            MockDS.assert_not_called()

        assert result["status"] == "success"
        arch_path = project_with_config / "concept" / "docs" / "ARCHITECTURE.md"
        assert arch_path.exists()

    def test_design_skip_discovery_with_extra_context(
        self, project_with_config, mock_agent_context, populated_registry
    ):
        """--skip-discovery allows user to add context before architecture generation."""
        from azext_prototype.stages.design_stage import DesignStage
        from azext_prototype.stages.discovery_state import DiscoveryState
        from azext_prototype.ai.provider import AIResponse

        ds = DiscoveryState(str(project_with_config))
        ds.load()
        ds.state["project"]["summary"] = "Build a web API"
        ds.state["_metadata"]["exchange_count"] = 3
        ds.save()

        stage = DesignStage()
        stage.get_guards = lambda: []  # type: ignore[assignment]

        mock_agent_context.project_dir = str(project_with_config)
        # Capture the prompts sent to the AI so we can verify extra context is included
        calls = []
        def _chat(*args, **kwargs):
            calls.append(args)
            return AIResponse(
                content="## Architecture\nOutput",
                model="gpt-4o",
                usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            )
        mock_agent_context.ai_provider.chat.side_effect = _chat

        # User types additional context at the prompt
        result = stage.execute(
            mock_agent_context,
            populated_registry,
            **{
                "context": "", "interactive": False, "skip_discovery": True,
                "input_fn": lambda _: "Also add Redis caching",
                "print_fn": lambda x: None,
            },
        )

        assert result["status"] == "success"

    def test_design_skip_discovery_uses_conversation_summary(
        self, project_with_config, mock_agent_context, populated_registry
    ):
        """--skip-discovery extracts requirements from conversation history, not empty structured fields."""
        from azext_prototype.stages.design_stage import DesignStage
        from azext_prototype.stages.discovery_state import DiscoveryState
        from azext_prototype.ai.provider import AIResponse

        # Create a discovery state with EMPTY structured fields but rich conversation
        ds = DiscoveryState(str(project_with_config))
        ds.load()
        # Structured fields remain empty (default)
        ds.state["conversation_history"] = [
            {
                "exchange": 1,
                "user": "Build an email drafting tool for PMs",
                "assistant": "Tell me more about the requirements.",
            },
            {
                "exchange": 2,
                "user": "Use Azure Functions and App Service",
                "assistant": (
                    "## Project Summary\n"
                    "An AI-powered email drafting tool for project managers.\n\n"
                    "## Confirmed Functional Requirements\n"
                    "- Scheduled draft generation via Azure Functions\n"
                    "- Web app queue for PM review and send\n\n"
                    "## Azure Services\n"
                    "- Azure Functions\n- Azure App Service\n- Cosmos DB\n\n"
                    "[READY]\nDoes this look right?"
                ),
            },
        ]
        ds.state["_metadata"]["exchange_count"] = 2
        ds.save()

        stage = DesignStage()
        stage.get_guards = lambda: []  # type: ignore[assignment]

        mock_agent_context.project_dir = str(project_with_config)
        # Capture what the AI receives to verify context is from conversation
        prompts_received = []
        def _chat(*args, **kwargs):
            prompts_received.append(args)
            return AIResponse(
                content="## Architecture\nCorrect output",
                model="gpt-4o",
                usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            )
        mock_agent_context.ai_provider.chat.side_effect = _chat

        result = stage.execute(
            mock_agent_context,
            populated_registry,
            **{
                "context": "", "interactive": False, "skip_discovery": True,
                "input_fn": lambda _: "", "print_fn": lambda x: None,
            },
        )

        assert result["status"] == "success"
        # Verify the conversation summary (not empty structured fields) was used
        # The planning prompt should contain "email drafting" from the conversation
        all_prompts = str(prompts_received)
        assert "email drafting" in all_prompts or "Azure Functions" in all_prompts

    def test_design_extract_last_summary_method(self):
        """_extract_last_summary delegates to DiscoveryState.extract_conversation_summary."""
        from azext_prototype.stages.design_stage import DesignStage
        from unittest.mock import MagicMock

        ds = MagicMock()
        ds.extract_conversation_summary.return_value = "## Project Summary\nA web app."

        result = DesignStage._extract_last_summary(ds)
        ds.extract_conversation_summary.assert_called_once()
        assert result == "## Project Summary\nA web app."

    def test_design_extract_last_summary_empty_history(self):
        """_extract_last_summary returns empty string when delegate returns empty."""
        from azext_prototype.stages.design_stage import DesignStage
        from unittest.mock import MagicMock

        ds = MagicMock()
        ds.extract_conversation_summary.return_value = ""
        assert DesignStage._extract_last_summary(ds) == ""

    def test_design_skip_discovery_fails_without_state(
        self, project_with_config, mock_agent_context, populated_registry
    ):
        """--skip-discovery raises CLIError when no discovery state exists."""
        import pytest
        from azext_prototype.stages.design_stage import DesignStage
        from knack.util import CLIError

        stage = DesignStage()
        stage.get_guards = lambda: []  # type: ignore[assignment]

        mock_agent_context.project_dir = str(project_with_config)

        with pytest.raises(CLIError, match="No discovery state found"):
            stage.execute(
                mock_agent_context,
                populated_registry,
                **{
                    "interactive": False, "skip_discovery": True,
                    "input_fn": lambda _: "", "print_fn": lambda x: None,
                },
            )


class TestBuildStage:
    """Test the build stage."""

    def test_build_stage_instantiates(self):
        from azext_prototype.stages.build_stage import BuildStage

        stage = BuildStage()
        assert stage is not None
        assert stage.name == "build"

    def test_match_templates_empty_architecture(self):
        from azext_prototype.stages.build_stage import BuildStage

        stage = BuildStage()
        config = MagicMock()
        result = stage._match_templates({"architecture": ""}, config)
        assert result == []
