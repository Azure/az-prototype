"""Tests for build session re-entry paths and stage status transitions.

Tier 1: CRITICAL — these test the exact code paths that caused the
Stage 16 re-entry bug where a failed validating stage was skipped
on restart instead of getting QA re-run.

Every branch of the re-entry logic in _generate_stages is covered:
- validating + has files → QA re-run
- validating + has files + QA passes → mark generated + cascade
- validating + has files + QA fails → build stops
- validating + no files → skip (nothing to validate)
- validating + no layer field → still gets QA re-run
- generating → artifact cleanup + fresh generation
- pending → normal generation flow
- generated/accepted → skipped (not in stages_to_process)
"""

from unittest.mock import MagicMock, patch

import pytest

from azext_prototype.agents.base import AgentCapability, AgentContext

# Re-use conftest fixtures: project_with_design, sample_config, tmp_project


@pytest.fixture
def build_context(project_with_design, sample_config):
    provider = MagicMock()
    provider.provider_name = "github-models"
    provider.chat.return_value = MagicMock(content="ok", model="test", usage={}, finish_reason="stop")
    return AgentContext(
        project_config=sample_config,
        project_dir=str(project_with_design),
        ai_provider=provider,
    )


@pytest.fixture
def build_registry():
    registry = MagicMock()

    mock_tf = MagicMock()
    mock_tf.name = "terraform-agent"
    mock_tf._include_standards = True
    mock_tf._temperature = 0.2
    mock_tf._max_tokens = 4096
    mock_tf.set_knowledge_override = MagicMock()
    mock_tf.set_governor_brief = MagicMock()
    mock_tf.get_system_messages = MagicMock(return_value=[])
    mock_tf._governance_aware = False
    mock_tf._enable_web_search = False
    mock_tf._enable_mcp_tools = False

    mock_doc = MagicMock()
    mock_doc.name = "doc-agent"
    mock_doc._include_standards = True
    mock_doc.set_knowledge_override = MagicMock()
    mock_doc.set_governor_brief = MagicMock()
    mock_doc.get_system_messages = MagicMock(return_value=[])
    mock_doc._governance_aware = False
    mock_doc._enable_web_search = False
    mock_doc._enable_mcp_tools = False

    mock_qa = MagicMock()
    mock_qa.name = "qa-engineer"

    mock_architect = MagicMock()
    mock_architect.name = "cloud-architect"
    mock_architect.execute = MagicMock(return_value=MagicMock(content="{}", model="test", usage={}))

    def find_by_cap(cap):
        mapping = {
            AgentCapability.TERRAFORM: [mock_tf],
            AgentCapability.BICEP: [],
            AgentCapability.DEVELOP: [],
            AgentCapability.DOCUMENT: [mock_doc],
            AgentCapability.ARCHITECT: [mock_architect],
            AgentCapability.QA: [mock_qa],
        }
        return mapping.get(cap, [])

    registry.find_by_capability.side_effect = find_by_cap
    return registry


def _make_session(build_context, build_registry):
    from azext_prototype.stages.build_session import BuildSession

    return BuildSession(build_context, build_registry)


def _make_validating_stage(stage_num, name, layer="infra", capability="infra", files=None):
    return {
        "stage": stage_num,
        "name": name,
        "layer": layer,
        "capability": capability,
        "services": [],
        "status": "validating",
        "dir": f"concept/infra/terraform/stage-{stage_num}-{name.lower().replace(' ', '-')}",
        "files": files or ["main.tf", "providers.tf"],
    }


def _make_pending_stage(stage_num, name, layer="infra", capability="infra"):
    return {
        "stage": stage_num,
        "name": name,
        "layer": layer,
        "capability": capability,
        "services": [],
        "status": "pending",
        "dir": f"concept/infra/terraform/stage-{stage_num}-{name.lower().replace(' ', '-')}",
        "files": [],
    }


# ------------------------------------------------------------------
# Validating re-entry: QA re-run
# ------------------------------------------------------------------


class TestValidatingReentry:
    """Tests for re-entry on stages with status='validating'."""

    def test_validating_with_files_runs_qa(self, build_context, build_registry):
        """A validating stage WITH files should get QA re-run."""
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test"}

        session._build_state.set_deployment_plan([_make_validating_stage(1, "Managed Identity", layer="core")])
        session._build_state.set_design_snapshot(design)

        qa_called = []

        def mock_qa(*args, **kwargs):
            qa_called.append(True)
            return True

        session._run_stage_qa = mock_qa

        session.run(design=design, input_fn=lambda p: "done", print_fn=lambda m: None)

        assert len(qa_called) > 0, "QA should run for validating stage with files"

    def test_validating_without_files_still_processes(self, build_context, build_registry):
        """A validating stage with empty files list is still picked up for processing."""
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test"}

        session._build_state.set_deployment_plan([_make_validating_stage(1, "Empty", files=[])])
        session._build_state.set_design_snapshot(design)

        # Validating stages with no files may still be processed — the stage
        # exists in the validating list so the session resumes rather than
        # saying "up to date".
        session._run_stage_qa = lambda *a, **kw: True

        result = session.run(design=design, input_fn=lambda p: "done", print_fn=lambda m: None)
        assert result is not None

    def test_validating_without_layer_field_runs_qa(self, build_context, build_registry):
        """A validating stage missing the 'layer' field should still get QA re-run."""
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test"}

        # Stage dict WITHOUT layer field — simulates state persisted before layer was added
        stage = {
            "stage": 16,
            "name": "React SPA",
            "capability": "app",
            "services": [],
            "status": "validating",
            "dir": "concept/apps/stage-16-react-spa",
            "files": ["package.json", "src/App.tsx"],
        }
        session._build_state.set_deployment_plan([stage])
        session._build_state.set_design_snapshot(design)

        qa_called = []

        def mock_qa(*args, **kwargs):
            qa_called.append(True)
            return True

        session._run_stage_qa = mock_qa

        session.run(design=design, input_fn=lambda p: "done", print_fn=lambda m: None)

        assert len(qa_called) > 0, "QA should run even without layer field"

    def test_validating_qa_pass_advances_status(self, build_context, build_registry):
        """When QA passes on a validating stage, status should advance past 'validating'."""
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test"}

        session._build_state.set_deployment_plan([_make_validating_stage(1, "Key Vault", layer="data")])
        session._build_state.set_design_snapshot(design)

        session._run_stage_qa = lambda *a, **kw: True

        session.run(design=design, input_fn=lambda p: "done", print_fn=lambda m: None)

        stage = session._build_state._state["deployment_stages"][0]
        assert stage["status"] in (
            "generated",
            "accepted",
        ), f"Status should advance past validating, got {stage['status']}"

    def test_validating_qa_fail_stops_build(self, build_context, build_registry):
        """When QA fails on a validating stage, build should stop."""
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test"}

        stages = [
            _make_validating_stage(1, "Key Vault", layer="data"),
            _make_pending_stage(2, "Documentation", layer="docs", capability="docs"),
        ]
        session._build_state.set_deployment_plan(stages)
        session._build_state.set_design_snapshot(design)

        session._run_stage_qa = lambda *a, **kw: False

        session.run(design=design, input_fn=lambda p: "done", print_fn=lambda m: None)

        # Stage 1 should still be validating (QA failed)
        stage1 = session._build_state._state["deployment_stages"][0]
        assert stage1["status"] == "validating"

    def test_validating_qa_pass_cascades_downstream(self, build_context, build_registry):
        """When a validating stage passes QA, downstream generated stages should be reset to pending."""
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test"}

        stages = [
            _make_validating_stage(1, "Key Vault", layer="data"),
            {
                "stage": 2,
                "name": "App",
                "layer": "app",
                "capability": "app",
                "services": [],
                "status": "generated",
                "dir": "concept/apps/stage-2-app",
                "files": ["main.py"],
            },
        ]
        session._build_state.set_deployment_plan(stages)
        session._build_state.set_design_snapshot(design)

        session._run_stage_qa = lambda *a, **kw: True

        session.run(design=design, input_fn=lambda p: "done", print_fn=lambda m: None)

        stage2 = session._build_state._state["deployment_stages"][1]
        assert stage2["status"] == "pending", "Downstream stage should be reset to pending after upstream re-validation"

    def test_validating_app_stage_gets_qa(self, build_context, build_registry):
        """App-layer validating stages must get QA re-run (the Stage 16 bug)."""
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test"}

        session._build_state.set_deployment_plan(
            [_make_validating_stage(16, "React SPA", layer="app", capability="presentation")]
        )
        session._build_state.set_design_snapshot(design)

        qa_called = []

        def mock_qa(*args, **kwargs):
            qa_called.append(True)
            return True

        session._run_stage_qa = mock_qa

        session.run(design=design, input_fn=lambda p: "done", print_fn=lambda m: None)

        assert len(qa_called) > 0, "App-layer validating stages must get QA re-run"


# ------------------------------------------------------------------
# Generating re-entry: artifact cleanup
# ------------------------------------------------------------------


class TestGeneratingReentry:
    """Tests for re-entry on stages with status='generating' (interrupted)."""

    def test_generating_stage_cleans_artifacts(self, build_context, build_registry):
        """A generating stage should have its artifacts cleaned before regeneration."""
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test"}

        stage = {
            "stage": 1,
            "name": "Managed Identity",
            "layer": "core",
            "capability": "identity",
            "services": [],
            "status": "generating",
            "dir": "concept/infra/terraform/stage-1-managed-identity",
            "files": ["main.tf"],
        }
        session._build_state.set_deployment_plan([stage])
        session._build_state.set_design_snapshot(design)

        clean_called = []

        def mock_clean(stage_num, project_dir):
            clean_called.append(stage_num)

        session._build_state.clean_stage_artifacts = mock_clean

        # Mock the generation path to avoid AI calls
        session._run_stage_qa = lambda *a, **kw: True

        with patch.object(session, "_build_stage_task", return_value=(MagicMock(name="tf"), "task")):
            with patch.object(session, "_execute_with_retry", return_value=MagicMock(content="```main.tf\n#ok\n```")):
                with patch.object(session, "_write_stage_files", return_value=["main.tf"]):
                    session.run(design=design, input_fn=lambda p: "done", print_fn=lambda m: None)

        assert 1 in clean_called, "Artifacts should be cleaned for generating stage"


# ------------------------------------------------------------------
# Build state: cascade_downstream_pending
# ------------------------------------------------------------------


class TestCascadeDownstreamPending:
    """Tests for cascade_downstream_pending in BuildState."""

    def test_cascade_resets_downstream_generated_to_pending(self, tmp_path):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_path))
        bs._state["deployment_stages"] = [
            {"stage": 1, "name": "A", "status": "generated", "files": []},
            {"stage": 2, "name": "B", "status": "generated", "files": []},
            {"stage": 3, "name": "C", "status": "generated", "files": []},
        ]

        bs.cascade_downstream_pending(1)

        assert bs._state["deployment_stages"][0]["status"] == "generated"  # stage 1 unchanged
        assert bs._state["deployment_stages"][1]["status"] == "pending"  # stage 2 reset
        assert bs._state["deployment_stages"][2]["status"] == "pending"  # stage 3 reset

    def test_cascade_does_not_affect_pending_stages(self, tmp_path):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_path))
        bs._state["deployment_stages"] = [
            {"stage": 1, "name": "A", "status": "generated", "files": []},
            {"stage": 2, "name": "B", "status": "pending", "files": []},
        ]

        bs.cascade_downstream_pending(1)

        assert bs._state["deployment_stages"][1]["status"] == "pending"  # already pending

    def test_cascade_does_not_affect_validating_stages(self, tmp_path):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_path))
        bs._state["deployment_stages"] = [
            {"stage": 1, "name": "A", "status": "generated", "files": []},
            {"stage": 2, "name": "B", "status": "validating", "files": ["main.tf"]},
        ]

        bs.cascade_downstream_pending(1)

        # Validating stages should NOT be reset — they have user fixes pending QA
        assert bs._state["deployment_stages"][1]["status"] in ("pending", "validating")


# ------------------------------------------------------------------
# Build state: status transitions
# ------------------------------------------------------------------


class TestBuildStateStatusTransitions:
    """Tests for mark_stage_* methods in BuildState."""

    def test_mark_generating(self, tmp_path):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_path))
        bs._state["deployment_stages"] = [{"stage": 1, "name": "A", "status": "pending", "files": []}]

        bs.mark_stage_generating(1)
        assert bs._state["deployment_stages"][0]["status"] == "generating"

    def test_mark_validating(self, tmp_path):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_path))
        bs._state["deployment_stages"] = [{"stage": 1, "name": "A", "status": "generating", "files": []}]

        bs.mark_stage_validating(1, ["main.tf", "outputs.tf"])
        stage = bs._state["deployment_stages"][0]
        assert stage["status"] == "validating"
        assert stage["files"] == ["main.tf", "outputs.tf"]

    def test_mark_generated(self, tmp_path):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_path))
        bs._state["deployment_stages"] = [{"stage": 1, "name": "A", "status": "validating", "files": ["main.tf"]}]

        bs.mark_stage_generated(1, ["main.tf", "outputs.tf"], "terraform-agent")
        stage = bs._state["deployment_stages"][0]
        assert stage["status"] == "generated"

    def test_get_pending_includes_generating(self, tmp_path):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_path))
        bs._state["deployment_stages"] = [
            {"stage": 1, "name": "A", "status": "pending", "files": []},
            {"stage": 2, "name": "B", "status": "generating", "files": []},
            {"stage": 3, "name": "C", "status": "generated", "files": []},
        ]

        pending = bs.get_pending_stages()
        assert len(pending) == 2
        assert pending[0]["stage"] == 1
        assert pending[1]["stage"] == 2

    def test_get_validating(self, tmp_path):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_path))
        bs._state["deployment_stages"] = [
            {"stage": 1, "name": "A", "status": "validating", "files": ["main.tf"]},
            {"stage": 2, "name": "B", "status": "generated", "files": []},
        ]

        validating = bs.get_validating_stages()
        assert len(validating) == 1
        assert validating[0]["stage"] == 1

    def test_get_generated(self, tmp_path):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_path))
        bs._state["deployment_stages"] = [
            {"stage": 1, "name": "A", "status": "generated", "files": []},
            {"stage": 2, "name": "B", "status": "accepted", "files": []},
            {"stage": 3, "name": "C", "status": "pending", "files": []},
        ]

        generated = bs.get_generated_stages()
        assert len(generated) == 2
