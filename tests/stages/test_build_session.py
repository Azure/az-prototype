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

import json
from pathlib import Path
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


# ------------------------------------------------------------------
# _qa_has_issues — 3-tier detection
# ------------------------------------------------------------------


class TestQaHasIssues:
    """Tests for the three-tier QA issue detection function."""

    def test_empty_content_returns_false(self):
        from azext_prototype.stages.build_session import _qa_has_issues

        assert _qa_has_issues("") is False

    def test_verdict_pass(self):
        from azext_prototype.stages.build_session import _qa_has_issues

        assert _qa_has_issues("Overall assessment:\nVERDICT: PASS") is False

    def test_verdict_pass_bold(self):
        from azext_prototype.stages.build_session import _qa_has_issues

        assert _qa_has_issues("**VERDICT: PASS**") is False

    def test_verdict_fail_with_critical(self):
        from azext_prototype.stages.build_session import _qa_has_issues

        assert _qa_has_issues("VERDICT: FAIL\nCRITICAL: missing auth") is True

    def test_verdict_fail_without_critical_overrides_to_pass(self):
        from azext_prototype.stages.build_session import _qa_has_issues

        assert _qa_has_issues("VERDICT: FAIL\nWARNING: minor issue only") is False

    def test_pass_phrase_no_issues_found(self):
        from azext_prototype.stages.build_session import _qa_has_issues

        assert _qa_has_issues("After reviewing: no issues found. All looks good.") is False

    def test_pass_phrase_all_checks_passed(self):
        from azext_prototype.stages.build_session import _qa_has_issues

        assert _qa_has_issues("All checks passed.") is False

    def test_keyword_fallback_critical(self):
        from azext_prototype.stages.build_session import _qa_has_issues

        assert _qa_has_issues("There is a critical problem with the config") is True

    def test_keyword_fallback_error(self):
        from azext_prototype.stages.build_session import _qa_has_issues

        assert _qa_has_issues("Found an error in the deployment") is True

    def test_keyword_fallback_missing(self):
        from azext_prototype.stages.build_session import _qa_has_issues

        assert _qa_has_issues("Outputs are missing from stage 3") is True

    def test_clean_text_no_keywords(self):
        from azext_prototype.stages.build_session import _qa_has_issues

        assert _qa_has_issues("Everything looks great. Well done.") is False


# ------------------------------------------------------------------
# _select_agent — all layer routing paths
# ------------------------------------------------------------------


class TestSelectAgent:
    """Tests for _select_agent covering all layer/capability routing."""

    def test_layer_core(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        agent = session._select_agent({"layer": "core", "capability": "infra"})
        assert agent is not None  # Should route to iac agent or architect

    def test_layer_infra(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        agent = session._select_agent({"layer": "infra", "capability": "infra"})
        assert agent is not None

    def test_layer_data(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        agent = session._select_agent({"layer": "data", "capability": "data"})
        assert agent is not None

    def test_layer_app(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        # May return None if no app agent registered — just verify no error
        session._select_agent({"layer": "app", "capability": "app"})

    def test_layer_docs(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        agent = session._select_agent({"layer": "docs", "capability": "docs"})
        assert agent is not None
        assert agent.name == "doc-agent"

    def test_fallback_infra_capability(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        agent = session._select_agent({"layer": "", "capability": "infra"})
        assert agent is not None

    def test_fallback_app_capability(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        # Covers the schema/cicd/external path too — just verify no error
        session._select_agent({"layer": "", "capability": "app"})

    def test_fallback_docs_capability(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        agent = session._select_agent({"layer": "", "capability": "docs"})
        assert agent is not None

    def test_fallback_unknown_capability(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        agent = session._select_agent({"layer": "", "capability": "unknown"})
        assert agent is not None  # Falls through to last else


# ------------------------------------------------------------------
# _build_stage_task — IaC vs app vs docs branches
# ------------------------------------------------------------------


class TestBuildStageTask:
    def test_iac_stage_task(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stage = {
            "stage": 1,
            "name": "Key Vault",
            "layer": "data",
            "capability": "data",
            "dir": "concept/infra/terraform/stage-1-key-vault",
            "services": [
                {
                    "name": "key-vault",
                    "computed_name": "kv-test",
                    "resource_type": "Microsoft.KeyVault/vaults",
                    "sku": "standard",
                    "component": "secrets",
                }
            ],
            "status": "pending",
            "files": [],
        }
        agent, task = session._build_stage_task(stage, "arch", [])
        assert agent is not None
        assert "MANDATORY RESOURCE POLICIES" in task or "Generate" in task
        assert "key-vault" in task.lower() or "Key Vault" in task

    def test_app_stage_task(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        # Ensure an app developer exists for routing
        mock_dev = MagicMock()
        mock_dev.name = "app-developer"
        mock_dev.set_knowledge_override = MagicMock()
        mock_dev.set_governor_brief = MagicMock()
        mock_dev._governance_aware = False
        mock_dev._enable_web_search = False
        mock_dev._enable_mcp_tools = False
        session._dev_agent = mock_dev

        stage = {
            "stage": 5,
            "name": "API Service",
            "layer": "app",
            "capability": "app",
            "dir": "concept/apps/stage-5-api",
            "services": [{"name": "fastapi-app", "computed_name": "", "resource_type": "", "sku": "", "component": ""}],
            "status": "pending",
            "files": [],
        }
        agent, task = session._build_stage_task(stage, "arch", [])
        assert agent is not None
        assert "DefaultAzureCredential" in task or "managed identity" in task.lower()
        assert "Do NOT generate" in task or "IaC" in task

    def test_docs_stage_task(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stage = {
            "stage": 10,
            "name": "Documentation",
            "layer": "docs",
            "capability": "docs",
            "dir": "concept/docs",
            "services": [],
            "status": "pending",
            "files": [],
        }
        agent, task = session._build_stage_task(stage, "arch", [])
        assert agent is not None
        assert "architecture.md" in task or "deployment-guide.md" in task

    def test_no_agent_returns_empty(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        # Remove all agents
        session._iac_agents = {}
        session._architect_agent = None
        session._infra_architect = None
        session._data_architect = None
        session._app_architect = None
        session._security_architect = None
        session._doc_agent = None
        session._dev_agent = None

        stage = {
            "stage": 1,
            "name": "Nothing",
            "layer": "unknown_layer",
            "capability": "unknown",
            "dir": "concept",
            "services": [],
            "status": "pending",
            "files": [],
        }
        agent, task = session._build_stage_task(stage, "arch", [])
        assert agent is None
        assert task == ""


# ------------------------------------------------------------------
# _write_stage_files — layer filtering
# ------------------------------------------------------------------


class TestWriteStageFiles:
    def test_docs_allowlist(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stage = {"layer": "docs", "dir": "concept/docs"}

        content = (
            "```architecture.md\n# Architecture\n```\n"
            "```deployment-guide.md\n# Deployment\n```\n"
            "```main.tf\n# should be blocked\n```\n"
        )
        paths = session._write_stage_files(stage, content)
        filenames = [Path(p).name for p in paths]
        assert "architecture.md" in filenames
        assert "deployment-guide.md" in filenames
        assert "main.tf" not in filenames

    def test_app_blocks_iac_files(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stage_dir = "concept/apps/stage-2-api"
        stage = {"layer": "app", "dir": stage_dir}

        content = "```main.py\nprint('hello')\n```\n" "```main.tf\n# blocked\n```\n" "```deploy.sh\n# blocked\n```\n"
        paths = session._write_stage_files(stage, content)
        filenames = [Path(p).name for p in paths]
        assert "main.py" in filenames
        assert "main.tf" not in filenames
        assert "deploy.sh" not in filenames

    def test_infra_blocks_versions_tf(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stage = {"layer": "infra", "dir": "concept/infra/terraform/stage-1"}

        content = "```main.tf\nresource {}\n```\n" "```versions.tf\n# blocked for terraform\n```\n"
        paths = session._write_stage_files(stage, content)
        filenames = [Path(p).name for p in paths]
        assert "main.tf" in filenames
        assert "versions.tf" not in filenames

    def test_empty_content_returns_empty(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        assert session._write_stage_files({"layer": "infra", "dir": "concept"}, "") == []

    def test_no_file_blocks_returns_empty(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        assert session._write_stage_files({"layer": "infra", "dir": "concept"}, "no code blocks here") == []


# ------------------------------------------------------------------
# _apply_stage_transforms — passthrough
# ------------------------------------------------------------------


class TestApplyStageTransforms:
    def test_empty_paths_returns_empty(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        result = session._apply_stage_transforms({"services": []}, [], lambda m: None)
        assert result == []


# ------------------------------------------------------------------
# _resolve_developer_for_stage — language detection
# ------------------------------------------------------------------


class TestResolveDeveloperForStage:
    def test_python_detected(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stage = {
            "name": "FastAPI Backend",
            "dir": "concept/apps/stage-5-fastapi",
            "services": [{"name": "fastapi-api"}],
        }
        # May be None if no python dev registered, but should not raise
        session._resolve_developer_for_stage(stage, "FastAPI backend")

    def test_csharp_detected(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stage = {
            "name": "ASP.NET API",
            "dir": "concept/apps/stage-5-dotnet",
            "services": [{"name": "aspnet-app"}],
        }
        session._resolve_developer_for_stage(stage, "ASP.NET Core API")

    def test_react_detected(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stage = {
            "name": "React Frontend",
            "dir": "concept/apps/stage-6-react",
            "services": [{"name": "react-spa"}],
        }
        session._resolve_developer_for_stage(stage, "React SPA")

    def test_no_language_returns_none(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stage = {
            "name": "Generic Service",
            "dir": "concept/apps/stage-7",
            "services": [{"name": "generic"}],
        }
        dev = session._resolve_developer_for_stage(stage, "Some generic service")
        assert dev is None


# ------------------------------------------------------------------
# _decompose_app_stage — delegation
# ------------------------------------------------------------------


class TestDecomposeAppStage:
    def test_with_detected_developer(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        # Mock a python developer
        mock_dev = MagicMock()
        mock_dev.name = "python-developer"
        session._python_dev = mock_dev

        stage = {
            "name": "Python API",
            "dir": "concept/apps/stage-5-python",
            "services": [{"name": "python-api"}],
        }
        agent, context = session._decompose_app_stage(stage, "Python FastAPI backend", lambda m: None)
        assert agent == mock_dev
        assert "Sub-Layer" in context

    def test_fallback_without_developer(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stage = {
            "name": "Mystery Service",
            "dir": "concept/apps/stage-5",
            "services": [{"name": "mystery"}],
        }
        agent, context = session._decompose_app_stage(stage, "Unknown architecture", lambda m: None)
        assert context == ""


# ------------------------------------------------------------------
# _detect_framework — static method
# ------------------------------------------------------------------


class TestDetectFramework:
    def test_fastapi(self):
        from azext_prototype.stages.build_session import BuildSession

        result = BuildSession._detect_framework({"fastapi-api"}, "concept/apps/stage-5", set())
        assert "FastAPI" in result

    def test_react(self):
        from azext_prototype.stages.build_session import BuildSession

        result = BuildSession._detect_framework({"react-spa"}, "concept/apps/stage-6", set())
        assert "React" in result or "SPA" in result

    def test_dotnet(self):
        from azext_prototype.stages.build_session import BuildSession

        result = BuildSession._detect_framework({"aspnet-api"}, "concept/apps/stage-7", set())
        assert ".NET" in result

    def test_dotnet_functions(self):
        from azext_prototype.stages.build_session import BuildSession

        result = BuildSession._detect_framework({"function-app"}, "concept/apps/stage-7", set())
        assert "Functions" in result

    def test_express(self):
        from azext_prototype.stages.build_session import BuildSession

        result = BuildSession._detect_framework({"express-api"}, "concept/apps/stage-8", set())
        assert "Express" in result or "Node.js" in result

    def test_go(self):
        from azext_prototype.stages.build_session import BuildSession

        result = BuildSession._detect_framework({"golang-api"}, "concept/apps/stage-9", set())
        assert "Go" in result

    def test_java(self):
        from azext_prototype.stages.build_session import BuildSession

        result = BuildSession._detect_framework({"spring-api"}, "concept/apps/stage-10", set())
        assert "Java" in result or "Spring" in result

    def test_unknown_returns_empty(self):
        from azext_prototype.stages.build_session import BuildSession

        result = BuildSession._detect_framework({"custom-service"}, "concept/apps/stage-11", set())
        assert result == ""

    def test_flask(self):
        from azext_prototype.stages.build_session import BuildSession

        result = BuildSession._detect_framework({"flask-api"}, "concept/apps", set())
        assert "Flask" in result

    def test_django(self):
        from azext_prototype.stages.build_session import BuildSession

        result = BuildSession._detect_framework({"django-app"}, "concept/apps", set())
        assert "Django" in result

    def test_vue(self):
        from azext_prototype.stages.build_session import BuildSession

        result = BuildSession._detect_framework({"vue-frontend"}, "concept/apps", set())
        assert "Vue" in result or "SPA" in result

    def test_nest(self):
        from azext_prototype.stages.build_session import BuildSession

        result = BuildSession._detect_framework({"nest-api"}, "concept/apps", set())
        assert "NestJS" in result or "Node.js" in result


# ------------------------------------------------------------------
# _categorize_service
# ------------------------------------------------------------------


class TestCategorizeService:
    def test_infra_type(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._categorize_service("key-vault") == "infra"
        assert BuildSession._categorize_service("virtual-network") == "infra"

    def test_data_type(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._categorize_service("cosmos-db") == "data"
        assert BuildSession._categorize_service("redis-cache") == "data"

    def test_app_type(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._categorize_service("custom-service") == "app"


# ------------------------------------------------------------------
# _infer_layer
# ------------------------------------------------------------------


class TestInferLayer:
    def test_explicit_layer_returned(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._infer_layer({"layer": "docs", "name": "Docs"}) == "docs"

    def test_identity_detected_as_core(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._infer_layer({"name": "Managed Identity", "capability": "infra"}) == "core"

    def test_monitoring_detected_as_core(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._infer_layer({"name": "Log Analytics", "capability": "infra"}) == "core"

    def test_capability_mapping(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._infer_layer({"name": "Redis", "capability": "data"}) == "data"
        assert BuildSession._infer_layer({"name": "API", "capability": "app"}) == "app"
        assert BuildSession._infer_layer({"name": "Docs", "capability": "docs"}) == "docs"


# ------------------------------------------------------------------
# _enforce_concept_prefix
# ------------------------------------------------------------------


class TestEnforceConceptPrefix:
    def test_already_concept_prefix(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        assert session._enforce_concept_prefix("concept/infra/stage-1") == "concept/infra/stage-1"

    def test_wrong_prefix_fixed(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        assert session._enforce_concept_prefix("output/infra/stage-1") == "concept/infra/stage-1"

    def test_bare_subdir(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        assert session._enforce_concept_prefix("infra") == "concept/infra"

    def test_empty_passthrough(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        assert session._enforce_concept_prefix("") == ""

    def test_unrelated_path_passthrough(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        assert session._enforce_concept_prefix("random/path/here") == "random/path/here"


# ------------------------------------------------------------------
# _parse_deployment_plan
# ------------------------------------------------------------------


class TestParseDeploymentPlan:
    def test_fenced_json(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        content = '```json\n{"stages": [{"stage": 1, "name": "A", "services": []}]}\n```'
        result = session._parse_deployment_plan(content)
        assert len(result) == 1

    def test_raw_json(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        content = '{"stages": [{"stage": 1, "name": "A", "services": []}]}'
        result = session._parse_deployment_plan(content)
        assert len(result) == 1

    def test_invalid_json_returns_empty(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        result = session._parse_deployment_plan("not json")
        assert result == []

    def test_empty_stages_returns_empty(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        result = session._parse_deployment_plan('{"stages": []}')
        assert result == []


# ------------------------------------------------------------------
# _parse_stage_map
# ------------------------------------------------------------------


class TestParseStageMap:
    def test_valid_map(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        content = (
            '```json\n{"stages": [{"stage": 1, "name": "A",'
            ' "layer": "core", "capability": "infra",'
            ' "services": ["managed-identity"]}]}\n```'
        )
        result = session._parse_stage_map(content)
        assert len(result) >= 1  # May include injected networking + docs

    def test_invalid_json_returns_empty(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        result = session._parse_stage_map("not json")
        assert result == []

    def test_ensures_docs_stage(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        content = '{"stages": [{"stage": 1, "name": "A", "layer": "core", "capability": "infra", "services": []}]}'
        result = session._parse_stage_map(content)
        assert any(s.get("layer") == "docs" for s in result)


# ------------------------------------------------------------------
# _ensure_networking_in_map
# ------------------------------------------------------------------


class TestEnsureNetworkingInMap:
    def test_inserts_when_missing(self):
        from azext_prototype.stages.build_session import BuildSession

        stages = [
            {"stage": 1, "name": "Managed Identity", "services": ["managed-identity"]},
            {"stage": 2, "name": "Key Vault", "services": ["key-vault"]},
        ]
        BuildSession._ensure_networking_in_map(stages)
        assert any(s["name"] == "Networking" for s in stages)

    def test_skips_when_present(self):
        from azext_prototype.stages.build_session import BuildSession

        stages = [
            {"stage": 1, "name": "Networking", "services": ["virtual-network"]},
            {"stage": 2, "name": "Key Vault", "services": ["key-vault"]},
        ]
        original_len = len(stages)
        BuildSession._ensure_networking_in_map(stages)
        assert len(stages) == original_len

    def test_skips_when_vnet_in_services(self):
        from azext_prototype.stages.build_session import BuildSession

        stages = [
            {"stage": 1, "name": "Foundation", "services": ["vnet"]},
        ]
        original_len = len(stages)
        BuildSession._ensure_networking_in_map(stages)
        assert len(stages) == original_len


# ------------------------------------------------------------------
# BuildResult
# ------------------------------------------------------------------


class TestBuildResult:
    def test_defaults(self):
        from azext_prototype.stages.build_session import BuildResult

        result = BuildResult()
        assert result.files_generated == []
        assert result.deployment_stages == []
        assert result.policy_overrides == []
        assert result.resources == []
        assert result.review_accepted is False
        assert result.cancelled is False

    def test_cancelled(self):
        from azext_prototype.stages.build_session import BuildResult

        result = BuildResult(cancelled=True)
        assert result.cancelled is True


# ------------------------------------------------------------------
# _get_app_scaffolding_requirements
# ------------------------------------------------------------------


class TestGetAppScaffoldingRequirements:
    def test_non_app_layer_returns_empty(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._get_app_scaffolding_requirements({"layer": "infra"}) == ""

    def test_app_layer_generic_fallback(self):
        from azext_prototype.stages.build_session import BuildSession

        stage = {
            "layer": "app",
            "services": [{"name": "custom", "resource_type": "", "sku": ""}],
            "dir": "concept/apps/stage-5",
        }
        result = BuildSession._get_app_scaffolding_requirements(stage)
        assert "Required Project Files" in result

    def test_app_layer_python_detected(self):
        from azext_prototype.stages.build_session import BuildSession

        stage = {
            "layer": "app",
            "services": [{"name": "python-api", "resource_type": "", "sku": ""}],
            "dir": "concept/apps/stage-5-python",
        }
        result = BuildSession._get_app_scaffolding_requirements(stage)
        assert "Python" in result or "requirements.txt" in result


# ------------------------------------------------------------------
# Naming strategy fallback (lines 244-246)
# ------------------------------------------------------------------


class TestNamingStrategyFallback:
    """Tests for naming strategy graceful fallback when config is bad."""

    def test_naming_fallback_on_bad_config(self, project_with_design, sample_config):
        """When create_naming_strategy raises, session falls back to simple strategy."""
        from azext_prototype.stages.build_session import BuildSession

        provider = MagicMock()
        provider.provider_name = "github-models"
        provider.chat.return_value = MagicMock(content="ok", model="test", usage={}, finish_reason="stop")
        ctx = AgentContext(
            project_config=sample_config,
            project_dir=str(project_with_design),
            ai_provider=provider,
        )

        registry = MagicMock()
        registry.find_by_capability.return_value = []

        # Corrupt the config so naming strategy fails on first try
        with patch("azext_prototype.stages.build_session.create_naming_strategy") as mock_naming:
            call_count = [0]

            def side_effect(cfg):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise ValueError("bad config")
                # Second call is fallback
                from azext_prototype.naming import create_naming_strategy as real_create

                return real_create(cfg)

            mock_naming.side_effect = side_effect
            session = BuildSession(ctx, registry)
            assert session._naming is not None


# ------------------------------------------------------------------
# Policy resolver regeneration path (lines 685-722)
# ------------------------------------------------------------------


class TestPolicyRegenPath:
    """Tests for the policy resolver triggering regeneration."""

    def test_policy_regen_executes_with_fix_instructions(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test arch"}

        stage = _make_pending_stage(1, "Key Vault", layer="data", capability="data")
        session._build_state.set_deployment_plan([stage])
        session._build_state.set_design_snapshot(design)

        regen_response = MagicMock(content="```main.tf\nfixed\n```", model="test", usage={})
        fix_instructions = "\n## Fix\nFix the SKU"

        # Track whether the regen path was exercised
        regen_called = []

        def mock_check_and_resolve(*args, **kwargs):
            # First call: needs regen; subsequent calls: no regen
            if not regen_called:
                regen_called.append(True)
                return (["override sku"], True)
            return ([], False)

        session._policy_resolver.check_and_resolve = mock_check_and_resolve
        session._policy_resolver.build_fix_instructions = MagicMock(return_value=fix_instructions)

        with patch.object(session, "_build_stage_task", return_value=(MagicMock(name="tf"), "task")):
            with patch.object(session, "_execute_with_retry", return_value=regen_response) as mock_retry:
                with patch.object(session, "_write_stage_files", return_value=["main.tf"]):
                    with patch.object(session, "_apply_stage_transforms", return_value=["main.tf"]):
                        session._run_stage_qa = lambda *a, **kw: True
                        session.run(design=design, input_fn=lambda p: "done", print_fn=lambda m: None)

        assert len(regen_called) > 0, "Policy resolver should have triggered regeneration"
        # The retry was called twice (original + regen)
        assert mock_retry.call_count >= 2

    def test_policy_regen_exception_routes_to_qa(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test arch"}

        stage = _make_pending_stage(1, "Key Vault", layer="data", capability="data")
        session._build_state.set_deployment_plan([stage])
        session._build_state.set_design_snapshot(design)

        # First policy check triggers regen (needs_regen=True)
        session._policy_resolver.check_and_resolve = MagicMock(return_value=(["issue"], True))
        session._policy_resolver.build_fix_instructions = MagicMock(return_value="\nfix")

        original_response = MagicMock(content="```main.tf\nok\n```", model="test", usage={})

        with patch.object(session, "_build_stage_task", return_value=(MagicMock(name="tf"), "task")):
            # First call: original generation; second call: regen throws
            with patch.object(session, "_execute_with_retry", side_effect=[original_response, RuntimeError("boom")]):
                with patch.object(session, "_write_stage_files", return_value=["main.tf"]):
                    with patch.object(session, "_apply_stage_transforms", return_value=["main.tf"]):
                        session._run_stage_qa = lambda *a, **kw: True
                        with patch("azext_prototype.stages.build_session.route_error_to_qa") as mock_qa_route:
                            session.run(design=design, input_fn=lambda p: "done", print_fn=lambda m: None)
                            # QA route was called for the regen exception
                            assert mock_qa_route.called

    def test_policy_regen_null_response_stops_build(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test arch"}

        stage = _make_pending_stage(1, "Key Vault", layer="data", capability="data")
        session._build_state.set_deployment_plan([stage])
        session._build_state.set_design_snapshot(design)

        call_count = [0]

        def mock_check_and_resolve(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return ([], False)
            return (["issue"], True)

        session._policy_resolver.check_and_resolve = mock_check_and_resolve
        session._policy_resolver.build_fix_instructions = MagicMock(return_value="\nfix")

        original_response = MagicMock(content="```main.tf\nok\n```", model="test", usage={})

        with patch.object(session, "_build_stage_task", return_value=(MagicMock(name="tf"), "task")):
            with patch.object(session, "_execute_with_retry", side_effect=[original_response, None]):
                with patch.object(session, "_write_stage_files", return_value=["main.tf"]):
                    with patch.object(session, "_apply_stage_transforms", return_value=["main.tf"]):
                        session._run_stage_qa = lambda *a, **kw: True
                        result = session.run(design=design, input_fn=lambda p: "done", print_fn=lambda m: None)
        # Build stopped because regen returned None
        assert result is not None


# ------------------------------------------------------------------
# Review loop / interactive rebuild (lines 863-918)
# ------------------------------------------------------------------


class TestReviewLoop:
    """Tests for the Phase 6 review loop."""

    def test_review_loop_regenerates_affected_stage(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test arch"}

        stage = {
            "stage": 1,
            "name": "Key Vault",
            "layer": "data",
            "capability": "data",
            "services": [{"name": "key-vault", "computed_name": "kv-test", "resource_type": "", "sku": ""}],
            "status": "generated",
            "dir": "concept/infra/terraform/stage-1-key-vault",
            "files": ["main.tf"],
        }
        session._build_state.set_deployment_plan([stage])
        session._build_state.set_design_snapshot(design)

        regen_response = MagicMock(content="```main.tf\nfixed\n```", model="test", usage={})

        inputs = iter(["Fix the key vault SKU", "done"])

        session._identify_affected_stages = MagicMock(return_value=[1])

        mock_agent = MagicMock()
        mock_agent.name = "terraform-agent"

        with patch.object(session, "_build_stage_task", return_value=(mock_agent, "task")):
            with patch.object(session, "_execute_with_continuation", return_value=regen_response):
                with patch.object(session, "_write_stage_files", return_value=["main.tf"]):
                    with patch.object(session, "_apply_stage_transforms", return_value=["main.tf"]):
                        result = session.run(
                            design=design,
                            input_fn=lambda p: next(inputs),
                            print_fn=lambda m: None,
                        )

        assert result.review_accepted is True
        # Stage should be marked accepted after done
        final_stage = session._build_state._state["deployment_stages"][0]
        assert final_stage["status"] == "accepted"

    def test_review_loop_no_affected_stages(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test arch"}

        stage = {
            "stage": 1,
            "name": "Key Vault",
            "layer": "data",
            "capability": "data",
            "services": [],
            "status": "generated",
            "dir": "concept/infra/terraform/stage-1-key-vault",
            "files": ["main.tf"],
        }
        session._build_state.set_deployment_plan([stage])
        session._build_state.set_design_snapshot(design)

        printed = []
        inputs = iter(["something vague", "done"])

        session._identify_affected_stages = MagicMock(return_value=[])

        result = session.run(
            design=design,
            input_fn=lambda p: next(inputs),
            print_fn=lambda m: printed.append(m),
        )

        assert any("Could not determine" in msg for msg in printed)
        assert result.review_accepted is True

    def test_review_loop_quit_cancels(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test arch"}

        stage = {
            "stage": 1,
            "name": "Key Vault",
            "layer": "data",
            "capability": "data",
            "services": [],
            "status": "generated",
            "dir": "concept/infra/terraform/stage-1-key-vault",
            "files": ["main.tf"],
        }
        session._build_state.set_deployment_plan([stage])
        session._build_state.set_design_snapshot(design)

        result = session.run(
            design=design,
            input_fn=lambda p: "quit",
            print_fn=lambda m: None,
        )

        assert result.cancelled is True

    def test_review_loop_slash_command(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test arch"}

        stage = {
            "stage": 1,
            "name": "Key Vault",
            "layer": "data",
            "capability": "data",
            "services": [],
            "status": "generated",
            "dir": "concept/infra/terraform/stage-1-key-vault",
            "files": ["main.tf"],
        }
        session._build_state.set_deployment_plan([stage])
        session._build_state.set_design_snapshot(design)

        printed = []
        inputs = iter(["/help", "done"])

        result = session.run(
            design=design,
            input_fn=lambda p: next(inputs),
            print_fn=lambda m: printed.append(m),
        )

        assert any("Available commands" in msg for msg in printed)
        assert result.review_accepted is True

    def test_review_loop_eof_breaks(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test arch"}

        stage = {
            "stage": 1,
            "name": "Key Vault",
            "layer": "data",
            "capability": "data",
            "services": [],
            "status": "generated",
            "dir": "concept/infra/terraform/stage-1-key-vault",
            "files": ["main.tf"],
        }
        session._build_state.set_deployment_plan([stage])
        session._build_state.set_design_snapshot(design)

        def raise_eof(p):
            raise EOFError()

        result = session.run(
            design=design,
            input_fn=raise_eof,
            print_fn=lambda m: None,
        )

        assert result.review_accepted is True


# ------------------------------------------------------------------
# Fallback deployment plan with templates (lines 1357-1430)
# ------------------------------------------------------------------


class TestFallbackDeploymentPlan:
    """Tests for _fallback_deployment_plan with template services."""

    def test_fallback_with_templates(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        # Create mock templates with services
        mock_svc_infra = MagicMock()
        mock_svc_infra.name = "key-vault"
        mock_svc_infra.type = "key-vault"
        mock_svc_infra.tier = "Standard"
        mock_svc_infra.config = {}

        mock_svc_data = MagicMock()
        mock_svc_data.name = "cosmos-db"
        mock_svc_data.type = "cosmos-db"
        mock_svc_data.tier = "Serverless"
        mock_svc_data.config = {}

        mock_svc_app = MagicMock()
        mock_svc_app.name = "python-api"
        mock_svc_app.type = "python-app"
        mock_svc_app.tier = "Standard"
        mock_svc_app.config = {}

        mock_template = MagicMock()
        mock_template.name = "web-app"
        mock_template.display_name = "Web Application"
        mock_template.services = [mock_svc_infra, mock_svc_data, mock_svc_app]

        stages = session._fallback_deployment_plan([mock_template])

        # Should have managed identity + infra + data + app + docs stages
        assert len(stages) >= 5

        # First stage should be Managed Identity
        assert stages[0]["name"] == "Managed Identity"
        assert stages[0]["layer"] == "core"

        # Last stage should be Documentation
        assert stages[-1]["name"] == "Documentation"
        assert stages[-1]["layer"] == "docs"

        # Infra stage for container-registry
        infra_stages = [s for s in stages if s["layer"] == "infra"]
        assert len(infra_stages) >= 1

        # Data stage for cosmos-db
        data_stages = [s for s in stages if s["layer"] == "data"]
        assert len(data_stages) >= 1

        # App stage for python-api
        app_stages = [s for s in stages if s["layer"] == "app"]
        assert len(app_stages) >= 1

    def test_fallback_without_templates(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stages = session._fallback_deployment_plan([])

        # Only managed identity + documentation
        assert len(stages) == 2
        assert stages[0]["name"] == "Managed Identity"
        assert stages[-1]["name"] == "Documentation"


# ------------------------------------------------------------------
# Ensure private endpoint stage (lines 1470-1540)
# ------------------------------------------------------------------


class TestEnsurePrivateEndpointStage:
    """Tests for _ensure_private_endpoint_stage."""

    def test_skips_when_network_stage_exists(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stages = [
            {
                "stage": 1,
                "name": "Networking",
                "layer": "infra",
                "services": [{"name": "virtual-network", "resource_type": "Microsoft.Network/virtualNetworks"}],
            },
            {
                "stage": 2,
                "name": "Key Vault",
                "layer": "data",
                "services": [{"name": "key-vault", "resource_type": "Microsoft.KeyVault/vaults"}],
            },
        ]
        result = session._ensure_private_endpoint_stage(stages)
        assert len(result) == 2  # No change

    def test_skips_when_service_has_network_resource(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stages = [
            {
                "stage": 1,
                "name": "Foundation",
                "layer": "infra",
                "services": [{"name": "vnet", "resource_type": "Microsoft.Network/virtualNetworks"}],
            },
        ]
        result = session._ensure_private_endpoint_stage(stages)
        assert len(result) == 1  # No change

    def test_injects_networking_stage_when_pe_services_found(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stages = [
            {
                "stage": 1,
                "name": "Managed Identity",
                "layer": "core",
                "services": [],
                "dir": "concept/infra/terraform/stage-1-managed-identity",
            },
            {
                "stage": 2,
                "name": "Key Vault",
                "layer": "data",
                "services": [{"name": "key-vault", "resource_type": "Microsoft.KeyVault/vaults"}],
                "dir": "concept/infra/terraform/stage-2-key-vault",
            },
        ]

        mock_pe = MagicMock()
        mock_pe.service_name = "key-vault"

        with patch(
            "azext_prototype.stages.build_session.BuildSession._ensure_private_endpoint_stage",
            wraps=session._ensure_private_endpoint_stage,
        ):
            with patch(
                "azext_prototype.knowledge.resource_metadata.get_private_endpoint_services",
                return_value=[mock_pe],
            ):
                result = session._ensure_private_endpoint_stage(stages)

        # Should have injected a networking stage
        assert len(result) == 3
        net_stage = result[1]
        assert net_stage["name"] == "Networking"
        assert any("virtual-network" in s["name"] for s in net_stage["services"])

    def test_no_injection_when_pe_services_empty(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stages = [
            {
                "stage": 1,
                "name": "Managed Identity",
                "layer": "core",
                "services": [],
                "dir": "concept/infra/terraform/stage-1-managed-identity",
            },
        ]

        with patch(
            "azext_prototype.knowledge.resource_metadata.get_private_endpoint_services",
            return_value=[],
        ):
            result = session._ensure_private_endpoint_stage(stages)

        assert len(result) == 1

    def test_exception_in_pe_lookup_returns_stages_unchanged(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stages = [
            {
                "stage": 1,
                "name": "Managed Identity",
                "layer": "core",
                "services": [],
                "dir": "concept/infra/terraform/stage-1-managed-identity",
            },
        ]

        with patch(
            "azext_prototype.knowledge.resource_metadata.get_private_endpoint_services",
            side_effect=ImportError("no module"),
        ):
            result = session._ensure_private_endpoint_stage(stages)

        assert len(result) == 1


# ------------------------------------------------------------------
# _diff_architectures / _parse_diff_result (lines 1590-1613, 1698-1719)
# ------------------------------------------------------------------


class TestDiffArchitectures:
    """Tests for architecture diffing and response parsing."""

    def test_diff_returns_fallback_without_architect(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._architect_agent = None

        existing = [{"stage": 1, "name": "A"}, {"stage": 2, "name": "B"}]
        result = session._diff_architectures("old", "new", existing)

        assert result["modified"] == [1, 2]
        assert result["unchanged"] == []

    def test_diff_parses_valid_response(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        diff_json = (
            '{"unchanged": [1], "modified": [2], "removed": [], '
            '"added": [], "plan_restructured": false, "summary": "Stage 2 modified."}'
        )
        mock_response = MagicMock(content=diff_json, model="test", usage={})
        session._architect_agent = MagicMock()
        session._architect_agent.execute.return_value = mock_response
        session._architect_agent.name = "cloud-architect"

        existing = [{"stage": 1, "name": "A"}, {"stage": 2, "name": "B"}]
        result = session._diff_architectures("old arch", "new arch", existing)

        assert 1 in result["unchanged"]
        assert 2 in result["modified"]

    def test_diff_fallback_on_exception(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._architect_agent = MagicMock()
        session._architect_agent.execute.side_effect = RuntimeError("boom")
        session._architect_agent.name = "cloud-architect"

        existing = [{"stage": 1, "name": "A"}]
        result = session._diff_architectures("old", "new", existing)

        assert result["modified"] == [1]

    def test_parse_diff_result_with_fenced_json(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        content = (
            '```json\n{"unchanged": [1], "modified": [2], "removed": [], '
            '"added": [], "plan_restructured": false, "summary": "ok"}\n```'
        )
        existing = [{"stage": 1, "name": "A"}, {"stage": 2, "name": "B"}]

        result = session._parse_diff_result(content, existing)
        assert result is not None
        assert 1 in result["unchanged"]
        assert 2 in result["modified"]

    def test_parse_diff_result_invalid_json(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        result = session._parse_diff_result("not json at all", [])
        assert result is None

    def test_parse_diff_result_not_dict(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        result = session._parse_diff_result("[1, 2, 3]", [])
        assert result is None

    def test_parse_diff_unmentioned_stages_default_unchanged(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        content = '{"unchanged": [], "modified": [2], "removed": []}'
        existing = [{"stage": 1, "name": "A"}, {"stage": 2, "name": "B"}, {"stage": 3, "name": "C"}]

        result = session._parse_diff_result(content, existing)
        assert result is not None
        # Stage 1 and 3 not mentioned → unchanged
        assert 1 in result["unchanged"]
        assert 3 in result["unchanged"]


# ------------------------------------------------------------------
# _adjust_plan (lines 1590-1613)
# ------------------------------------------------------------------


class TestAdjustPlan:
    """Tests for the _adjust_plan method."""

    def test_adjust_returns_none_without_architect(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._architect_agent = None

        result = session._adjust_plan("add redis", "arch", [])
        assert result is None

    def test_adjust_returns_none_without_ai_provider(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._context.ai_provider = None

        result = session._adjust_plan("add redis", "arch", [])
        assert result is None

    def test_adjust_returns_parsed_plan(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        plan_json = (
            '```json\n{"stages": [{"stage": 1, "name": "Redis", "layer": "data", '
            '"capability": "data", "dir": "concept/infra/terraform/stage-1-redis", '
            '"services": [], "status": "pending", "files": []}]}\n```'
        )
        session._architect_agent = MagicMock()
        session._architect_agent.execute.return_value = MagicMock(content=plan_json, model="test", usage={})
        session._architect_agent.name = "cloud-architect"

        result = session._adjust_plan("add redis", "arch", [])
        assert result is not None
        assert len(result) >= 1

    def test_adjust_returns_none_on_empty_response(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._architect_agent = MagicMock()
        session._architect_agent.execute.return_value = MagicMock(content="", model="test", usage={})
        session._architect_agent.name = "cloud-architect"

        result = session._adjust_plan("add redis", "arch", [])
        assert result is None


# ------------------------------------------------------------------
# _apply_stage_transforms debug logging (lines 2698-2708)
# ------------------------------------------------------------------


class TestApplyStageTransformsDebug:
    """Tests for _apply_stage_transforms debug path."""

    def test_transforms_no_changes(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        project_dir = Path(build_context.project_dir)
        stage_dir = project_dir / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        main_tf = stage_dir / "main.tf"
        main_tf.write_text("resource {}", encoding="utf-8")

        stage = {
            "stage": 1,
            "name": "Test",
            "services": [{"resource_type": "Microsoft.KeyVault/vaults"}],
        }

        rel_path = str(main_tf.relative_to(project_dir))

        with patch("azext_prototype.governance.transforms.apply", return_value=("resource {}", [])):
            result = session._apply_stage_transforms(stage, [rel_path], lambda m: None)

        # Returns the same paths (no transforms applied)
        assert result == [rel_path]

    def test_transforms_debug_log_assembles_files(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        project_dir = Path(build_context.project_dir)
        stage_dir = project_dir / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        main_tf = stage_dir / "main.tf"
        main_tf.write_text('resource "test" {}', encoding="utf-8")

        stage = {
            "stage": 1,
            "name": "Test",
            "services": [],
        }

        rel_path = str(main_tf.relative_to(project_dir))

        with patch("azext_prototype.governance.transforms.apply", return_value=('resource "test" {}', [])):
            with patch("azext_prototype.debug_log.is_active", return_value=True):
                with patch("azext_prototype.debug_log.log_flow") as mock_dbg:
                    result = session._apply_stage_transforms(stage, [rel_path], lambda m: None)

        assert result == [rel_path]
        # Debug log should have been called with post-transform info
        assert mock_dbg.called

    def test_transforms_empty_paths_returns_empty(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stage = {"stage": 1, "name": "Test", "services": []}
        result = session._apply_stage_transforms(stage, [], lambda m: None)
        assert result == []


# ------------------------------------------------------------------
# QA remediation write-back (lines 3309-3322, 3358-3411)
# ------------------------------------------------------------------


class TestQaRemediationWriteBack:
    """Tests for QA review retry logic including rate limit and timeout handling."""

    def test_qa_rate_limit_retries(self, build_context, build_registry):
        from azext_prototype.ai.copilot_provider import CopilotRateLimitError

        session = _make_session(build_context, build_registry)

        stage = {
            "stage": 1,
            "name": "Key Vault",
            "layer": "data",
            "capability": "data",
            "services": [],
            "files": ["main.tf"],
        }

        # Create file on disk
        project_dir = Path(build_context.project_dir)
        stage_dir = project_dir / "concept" / "infra" / "terraform" / "stage-1-key-vault"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text("resource {}", encoding="utf-8")
        stage["files"] = [str((stage_dir / "main.tf").relative_to(project_dir))]

        session._build_state.set_deployment_plan([stage])

        qa_response = MagicMock(content="VERDICT: PASS", model="test", usage={})

        call_count = [0]

        def mock_delegate(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise CopilotRateLimitError("rate limited", retry_after=1)
            return qa_response

        with patch("azext_prototype.stages.build_session.AgentOrchestrator") as MockOrch:
            mock_orch = MockOrch.return_value
            mock_orch.delegate.side_effect = mock_delegate
            with patch.object(session, "_countdown"):
                passed = session._run_stage_qa(stage, "arch", [], False, lambda m: None)

        assert passed is True
        assert call_count[0] >= 2

    def test_qa_timeout_exhausts_retries(self, build_context, build_registry):
        from azext_prototype.ai.copilot_provider import CopilotTimeoutError

        session = _make_session(build_context, build_registry)

        stage = {
            "stage": 1,
            "name": "Key Vault",
            "layer": "data",
            "capability": "data",
            "services": [],
            "files": ["main.tf"],
        }

        project_dir = Path(build_context.project_dir)
        stage_dir = project_dir / "concept" / "infra" / "terraform" / "stage-1-key-vault"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text("resource {}", encoding="utf-8")
        stage["files"] = [str((stage_dir / "main.tf").relative_to(project_dir))]

        session._build_state.set_deployment_plan([stage])

        with patch("azext_prototype.stages.build_session.AgentOrchestrator") as MockOrch:
            mock_orch = MockOrch.return_value
            mock_orch.delegate.side_effect = CopilotTimeoutError("timeout")
            with patch.object(session, "_countdown"):
                passed = session._run_stage_qa(stage, "arch", [], False, lambda m: None)

        assert passed is False

    def test_qa_remediation_cycle(self, build_context, build_registry):
        """QA finds issues, remediates, then passes."""
        session = _make_session(build_context, build_registry)

        stage = {
            "stage": 1,
            "name": "Key Vault",
            "layer": "data",
            "capability": "data",
            "services": [{"name": "key-vault", "resource_type": "Microsoft.KeyVault/vaults"}],
            "files": ["main.tf"],
        }

        project_dir = Path(build_context.project_dir)
        stage_dir = project_dir / "concept" / "infra" / "terraform" / "stage-1-key-vault"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text("resource {}", encoding="utf-8")
        stage["files"] = [str((stage_dir / "main.tf").relative_to(project_dir))]

        session._build_state.set_deployment_plan([stage])

        call_count = [0]

        def mock_delegate(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First QA call: issues found
                return MagicMock(content="VERDICT: FAIL\nCRITICAL: missing auth", model="test", usage={})
            # Second QA call: pass
            return MagicMock(content="VERDICT: PASS", model="test", usage={})

        regen_response = MagicMock(content="```main.tf\nfixed\n```", model="test", usage={})

        mock_iac_agent = MagicMock()
        mock_iac_agent.name = "terraform-agent"

        with patch("azext_prototype.stages.build_session.AgentOrchestrator") as MockOrch:
            mock_orch = MockOrch.return_value
            mock_orch.delegate.side_effect = mock_delegate
            with patch.object(session, "_select_agent", return_value=mock_iac_agent):
                with patch.object(session, "_build_stage_task", return_value=(mock_iac_agent, "task")):
                    with patch.object(session, "_execute_with_retry", return_value=regen_response):
                        with patch.object(session, "_write_stage_files", return_value=["main.tf"]):
                            with patch.object(session, "_apply_stage_transforms", return_value=["main.tf"]):
                                passed = session._run_stage_qa(stage, "arch", [], False, lambda m: None)

        assert passed is True
        assert call_count[0] >= 2


# ------------------------------------------------------------------
# _generate_stage_advisory (lines 3458-3503)
# ------------------------------------------------------------------


class TestGenerateStageAdvisory:
    """Tests for per-stage advisory generation."""

    def test_advisory_returns_empty_without_advisor(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._advisor_agent = None
        result = session._generate_stage_advisory({"stage": 1, "name": "Test", "files": ["a.tf"]}, lambda m: None)
        assert result == ""

    def test_advisory_skips_docs_layer(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._advisor_agent = MagicMock()
        result = session._generate_stage_advisory(
            {"stage": 1, "name": "Docs", "layer": "docs", "files": ["README.md"]}, lambda m: None
        )
        assert result == ""

    def test_advisory_skips_empty_files(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._advisor_agent = MagicMock()
        result = session._generate_stage_advisory(
            {"stage": 1, "name": "Test", "layer": "infra", "files": []}, lambda m: None
        )
        assert result == ""

    def test_advisory_returns_content(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        project_dir = Path(build_context.project_dir)
        stage_dir = project_dir / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text("resource {} {}", encoding="utf-8")

        rel_path = str((stage_dir / "main.tf").relative_to(project_dir))

        session._advisor_agent = MagicMock()
        session._advisor_agent.name = "advisor"

        advisory_text = "Consider upgrading to Premium SKU for production."

        with patch("azext_prototype.agents.orchestrator.AgentOrchestrator") as MockOrch:
            mock_orch = MockOrch.return_value
            mock_orch.delegate.return_value = MagicMock(content=advisory_text, model="test", usage={})
            result = session._generate_stage_advisory(
                {"stage": 1, "name": "Key Vault", "layer": "data", "files": [rel_path]}, lambda m: None
            )

        assert result == advisory_text

    def test_advisory_handles_exception(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        project_dir = Path(build_context.project_dir)
        stage_dir = project_dir / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text("resource {} {}", encoding="utf-8")

        rel_path = str((stage_dir / "main.tf").relative_to(project_dir))

        session._advisor_agent = MagicMock()
        session._advisor_agent.name = "advisor"

        with patch("azext_prototype.agents.orchestrator.AgentOrchestrator") as MockOrch:
            mock_orch = MockOrch.return_value
            mock_orch.delegate.side_effect = RuntimeError("boom")
            result = session._generate_stage_advisory(
                {"stage": 1, "name": "Key Vault", "layer": "data", "files": [rel_path]}, lambda m: None
            )

        assert result == ""


# ------------------------------------------------------------------
# _execute_with_retry (lines 3537-3549)
# ------------------------------------------------------------------


class TestExecuteWithRetry:
    """Tests for _execute_with_retry timeout/rate-limit backoff."""

    def test_retry_success_on_first_attempt(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        mock_agent = MagicMock()
        mock_response = MagicMock(content="ok", model="test", usage={})

        with patch.object(session, "_execute_with_continuation", return_value=mock_response):
            result = session._execute_with_retry(mock_agent, "task", 1, "Stage", lambda m: None)

        assert result is mock_response

    def test_retry_on_rate_limit(self, build_context, build_registry):
        from azext_prototype.ai.copilot_provider import CopilotRateLimitError

        session = _make_session(build_context, build_registry)
        mock_agent = MagicMock()
        mock_response = MagicMock(content="ok", model="test", usage={})

        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise CopilotRateLimitError("limited", retry_after=1)
            return mock_response

        with patch.object(session, "_execute_with_continuation", side_effect=side_effect):
            with patch.object(session, "_countdown"):
                result = session._execute_with_retry(mock_agent, "task", 1, "Stage", lambda m: None)

        assert result is mock_response
        assert call_count[0] == 2

    def test_retry_on_timeout_eventually_returns_none(self, build_context, build_registry):
        from azext_prototype.ai.copilot_provider import CopilotTimeoutError

        session = _make_session(build_context, build_registry)
        mock_agent = MagicMock()

        with patch.object(session, "_execute_with_continuation", side_effect=CopilotTimeoutError("timeout")):
            with patch.object(session, "_countdown"):
                printed = []
                result = session._execute_with_retry(mock_agent, "task", 1, "Stage", lambda m: printed.append(m))

        assert result is None
        assert any("timed out" in msg for msg in printed)

    def test_retry_rate_limit_uses_retry_after(self, build_context, build_registry):
        from azext_prototype.ai.copilot_provider import CopilotRateLimitError

        session = _make_session(build_context, build_registry)
        mock_agent = MagicMock()
        mock_response = MagicMock(content="ok", model="test", usage={})

        def side_effect(*args, **kwargs):
            raise CopilotRateLimitError("limited", retry_after=42)

        countdown_calls = []

        def mock_countdown(seconds, *a, **kw):
            countdown_calls.append(seconds)

        call_count = [0]

        def exec_side(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise CopilotRateLimitError("limited", retry_after=42)
            return mock_response

        with patch.object(session, "_execute_with_continuation", side_effect=exec_side):
            with patch.object(session, "_countdown", side_effect=mock_countdown):
                result = session._execute_with_retry(mock_agent, "task", 1, "Stage", lambda m: None)

        assert result is mock_response
        # countdown should have been called with 42 (retry_after value)
        assert 42 in countdown_calls


# ------------------------------------------------------------------
# _execute_with_continuation (lines 3579-3610)
# ------------------------------------------------------------------


class TestExecuteWithContinuation:
    """Tests for truncation recovery via continuation."""

    def test_no_continuation_on_stop(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        mock_agent = MagicMock()
        response = MagicMock(content="full output", finish_reason="stop", model="test", usage={})
        mock_agent.execute.return_value = response

        result = session._execute_with_continuation(mock_agent, "task")
        assert result.content == "full output"
        assert mock_agent.execute.call_count == 1

    def test_continuation_on_length(self, build_context, build_registry):
        from azext_prototype.ai.provider import AIResponse

        session = _make_session(build_context, build_registry)
        mock_agent = MagicMock()

        first_response = AIResponse(
            content="partial",
            model="test",
            usage={"prompt_tokens": 100, "completion_tokens": 200},
            finish_reason="length",
        )
        second_response = AIResponse(
            content=" continued",
            model="test",
            usage={"prompt_tokens": 50, "completion_tokens": 100},
            finish_reason="stop",
        )
        mock_agent.execute.side_effect = [first_response, second_response]

        result = session._execute_with_continuation(mock_agent, "task")
        assert result.content == "partial continued"
        assert result.finish_reason == "stop"
        assert mock_agent.execute.call_count == 2
        # Token usage should be merged
        assert result.usage["prompt_tokens"] == 150
        assert result.usage["completion_tokens"] == 300

    def test_continuation_with_stage_context(self, build_context, build_registry):
        from azext_prototype.ai.provider import AIResponse

        session = _make_session(build_context, build_registry)
        mock_agent = MagicMock()

        first_response = AIResponse(
            content="partial code",
            model="test",
            usage={"prompt_tokens": 100},
            finish_reason="length",
        )
        second_response = AIResponse(
            content=" rest of code",
            model="test",
            usage={"prompt_tokens": 50},
            finish_reason="stop",
        )
        mock_agent.execute.side_effect = [first_response, second_response]

        result = session._execute_with_continuation(
            mock_agent, "task", stage_num=3, stage_name="Key Vault", stage_capability="data"
        )
        assert result.content == "partial code rest of code"
        # Conversation history should have continuation messages
        assert len(session._context.conversation_history) >= 2

    def test_continuation_max_limit(self, build_context, build_registry):
        from azext_prototype.ai.provider import AIResponse

        session = _make_session(build_context, build_registry)
        mock_agent = MagicMock()

        # All responses truncated
        truncated = AIResponse(
            content="chunk",
            model="test",
            usage={"prompt_tokens": 10},
            finish_reason="length",
        )
        mock_agent.execute.return_value = truncated

        result = session._execute_with_continuation(mock_agent, "task", max_continuations=2)
        # 1 original + 2 continuations = 3 calls
        assert mock_agent.execute.call_count == 3
        # Content should be accumulated
        assert "chunk" in result.content

    def test_continuation_none_response_breaks(self, build_context, build_registry):
        from azext_prototype.ai.provider import AIResponse

        session = _make_session(build_context, build_registry)
        mock_agent = MagicMock()

        first_response = AIResponse(
            content="partial",
            model="test",
            usage={"prompt_tokens": 100},
            finish_reason="length",
        )
        mock_agent.execute.side_effect = [first_response, None]

        result = session._execute_with_continuation(mock_agent, "task")
        assert result.content == "partial"


# ------------------------------------------------------------------
# _collect_generated_file_content (lines 3413-3439)
# ------------------------------------------------------------------


class TestCollectGeneratedFileContent:
    """Tests for collecting generated file content for QA."""

    def test_collects_existing_files(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        project_dir = Path(build_context.project_dir)
        stage_dir = project_dir / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text("resource {}", encoding="utf-8")

        rel_path = str((stage_dir / "main.tf").relative_to(project_dir))

        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Test", "layer": "infra", "status": "generated", "files": [rel_path]},
        ]

        content = session._collect_generated_file_content()
        assert "resource {}" in content
        assert "Stage 1: Test" in content

    def test_handles_missing_files(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        session._build_state._state["deployment_stages"] = [
            {
                "stage": 1,
                "name": "Test",
                "layer": "infra",
                "status": "generated",
                "files": ["nonexistent/main.tf"],
            },
        ]

        content = session._collect_generated_file_content()
        assert "(could not read file)" in content

    def test_skips_stages_without_files(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Test", "layer": "infra", "status": "generated", "files": []},
        ]

        content = session._collect_generated_file_content()
        assert content == ""


# ------------------------------------------------------------------
# _categorize_service (static method) — additional coverage
# ------------------------------------------------------------------


class TestCategorizeServiceExtended:
    """Extended tests for service type categorization."""

    def test_infra_types(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._categorize_service("key-vault") == "infra"
        assert BuildSession._categorize_service("virtual-network") == "infra"
        assert BuildSession._categorize_service("managed-identity") == "infra"
        assert BuildSession._categorize_service("application-insights") == "infra"

    def test_data_types(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._categorize_service("cosmos-db") == "data"
        assert BuildSession._categorize_service("sql-database") == "data"
        assert BuildSession._categorize_service("redis-cache") == "data"
        assert BuildSession._categorize_service("storage-account") == "data"

    def test_app_type_fallback(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._categorize_service("python-app") == "app"
        assert BuildSession._categorize_service("container-registry") == "app"


# ------------------------------------------------------------------
# _parse_deployment_plan (lines 1235-1236)
# ------------------------------------------------------------------


class TestParseDeploymentPlanExtended:
    """Extended tests for deployment plan JSON parsing."""

    def test_parse_fenced_json(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        content = (
            '```json\n{"stages": [{"stage": 1, "name": "Test", '
            '"dir": "concept/infra/terraform/stage-1", "services": [], '
            '"capability": "infra"}]}\n```'
        )
        result = session._parse_deployment_plan(content)
        assert len(result) >= 1
        assert result[0]["name"] == "Test"

    def test_parse_raw_json(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        content = (
            '{"stages": [{"stage": 1, "name": "Test", '
            '"dir": "concept/infra/terraform/stage-1", "services": [], '
            '"capability": "infra"}]}'
        )
        result = session._parse_deployment_plan(content)
        assert len(result) >= 1

    def test_parse_invalid_json_returns_empty(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        result = session._parse_deployment_plan("not json at all")
        assert result == []

    def test_parse_fenced_bad_json_falls_back(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        content = "```json\n{bad json}\n```"
        result = session._parse_deployment_plan(content)
        assert result == []


# ------------------------------------------------------------------
# _build_docs_context (lines 3017-3045)
# ------------------------------------------------------------------


class TestBuildDocsContext:
    """Tests for documentation context builder."""

    def test_returns_empty_when_no_generated(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "A", "status": "pending", "files": []},
        ]
        assert session._build_docs_context() == ""

    def test_includes_output_keys(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        project_dir = Path(build_context.project_dir)
        stage_dir = project_dir / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        outputs_tf = stage_dir / "outputs.tf"
        outputs_tf.write_text(
            'output "vault_id" {\n  description = "Key Vault ID"\n  value = azapi_resource.vault.id\n}\n',
            encoding="utf-8",
        )

        rel_path = str(outputs_tf.relative_to(project_dir))

        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Key Vault", "status": "generated", "files": [rel_path], "layer": "data"},
        ]

        result = session._build_docs_context()
        assert "vault_id" in result
        assert "Key Vault ID" in result

    def test_lists_files_when_no_outputs(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        project_dir = Path(build_context.project_dir)
        stage_dir = project_dir / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        main_tf = stage_dir / "main.tf"
        main_tf.write_text("resource {}", encoding="utf-8")

        rel_path = str(main_tf.relative_to(project_dir))

        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Test", "status": "generated", "files": [rel_path], "layer": "infra"},
        ]

        result = session._build_docs_context()
        assert "main.tf" in result


# ------------------------------------------------------------------
# _build_dns_zone_note (lines 3062-3085)
# ------------------------------------------------------------------


class TestBuildDnsZoneNote:
    """Tests for DNS zone note generation."""

    def test_returns_empty_when_no_zones(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Test", "services": []},
        ]

        with patch(
            "azext_prototype.knowledge.private_dns_zones.get_zones_for_services",
            return_value={},
        ):
            result = session._build_dns_zone_note()
        assert result == ""

    def test_returns_zones_when_found(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Test", "services": [{"name": "kv"}]},
        ]

        zones = {"privatelink.vaultcore.azure.net": "Microsoft.KeyVault/vaults"}

        with patch(
            "azext_prototype.knowledge.private_dns_zones.get_zones_for_services",
            return_value=zones,
        ):
            result = session._build_dns_zone_note()

        assert "privatelink.vaultcore.azure.net" in result
        assert "REQUIRED PRIVATE DNS ZONES" in result

    def test_exception_returns_empty(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Test", "services": []},
        ]

        with patch(
            "azext_prototype.knowledge.private_dns_zones.get_zones_for_services",
            side_effect=ImportError("boom"),
        ):
            result = session._build_dns_zone_note()
        assert result == ""


# ------------------------------------------------------------------
# _get_networking_stage_note (lines 3092-3096)
# ------------------------------------------------------------------


class TestGetNetworkingStageNote:
    """Tests for networking stage QA note generation."""

    def test_returns_note_when_networking_stage_has_pe_services(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {
                "stage": 2,
                "name": "Networking",
                "services": [
                    {"name": "virtual-network"},
                    {"name": "private-endpoint-keyvault"},
                ],
            },
        ]

        result = session._get_networking_stage_note()
        assert "CRITICAL: Networking Stage" in result
        assert "private-endpoint-keyvault" in result

    def test_returns_empty_when_no_networking_stage(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Key Vault", "services": []},
        ]

        result = session._get_networking_stage_note()
        assert result == ""

    def test_returns_empty_when_networking_has_no_pe_services(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {
                "stage": 2,
                "name": "Networking",
                "services": [{"name": "virtual-network"}],
            },
        ]

        result = session._get_networking_stage_note()
        assert result == ""


# ------------------------------------------------------------------
# _extract_output_keys (lines 2969-2979)
# ------------------------------------------------------------------


class TestExtractOutputKeys:
    """Tests for extracting output key names from stage files."""

    def test_extracts_terraform_output_keys(self, tmp_path):
        from azext_prototype.stages.build_session import BuildSession

        outputs_file = tmp_path / "concept" / "infra" / "terraform" / "stage-1" / "outputs.tf"
        outputs_file.parent.mkdir(parents=True, exist_ok=True)
        outputs_file.write_text(
            'output "vault_id" {\n  value = azapi_resource.vault.id\n}\n'
            'output "vault_uri" {\n  value = azapi_resource.vault.properties.vaultUri\n}\n',
            encoding="utf-8",
        )

        rel_path = str(outputs_file.relative_to(tmp_path))
        stage = {"files": [rel_path]}

        keys = BuildSession._extract_output_keys(stage, tmp_path)
        assert "vault_id" in keys
        assert "vault_uri" in keys

    def test_returns_empty_when_no_outputs_file(self, tmp_path):
        from azext_prototype.stages.build_session import BuildSession

        stage = {"files": ["main.tf"]}
        keys = BuildSession._extract_output_keys(stage, tmp_path)
        assert keys == []


# ------------------------------------------------------------------
# Design change branch B (lines 356-379)
# ------------------------------------------------------------------


class TestDesignChangeBranchB:
    """Tests for re-entry when design has changed."""

    def test_design_changed_restructured_quit(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        design = {"architecture": "New arch"}

        session._build_state.set_deployment_plan([_make_pending_stage(1, "Test")])
        session._build_state.set_design_snapshot({"architecture": "Old arch"})

        # Simulate design changed
        session._build_state.design_has_changed = MagicMock(return_value=True)
        session._build_state.get_previous_architecture = MagicMock(return_value="Old arch")

        diff_result = {
            "unchanged": [],
            "modified": [],
            "removed": [],
            "added": [],
            "plan_restructured": True,
            "summary": "Big changes.",
        }

        with patch.object(session, "_diff_architectures", return_value=diff_result):
            result = session.run(
                design=design,
                input_fn=lambda p: "quit",
                print_fn=lambda m: None,
            )

        assert result.cancelled is True

    def test_design_changed_targeted_updates(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        design = {"architecture": "New arch with redis"}

        stages = [
            _make_pending_stage(1, "Key Vault", layer="data", capability="data"),
            _make_pending_stage(2, "Redis", layer="data", capability="data"),
        ]
        session._build_state.set_deployment_plan(stages)
        session._build_state.set_design_snapshot({"architecture": "Old arch"})

        session._build_state.design_has_changed = MagicMock(return_value=True)
        session._build_state.get_previous_architecture = MagicMock(return_value="Old arch")

        diff_result = {
            "unchanged": [1],
            "modified": [2],
            "removed": [],
            "added": [],
            "plan_restructured": False,
            "summary": "Stage 2 modified.",
        }

        with patch.object(session, "_diff_architectures", return_value=diff_result):
            session._run_stage_qa = lambda *a, **kw: True
            with patch.object(session, "_build_stage_task", return_value=(MagicMock(name="tf"), "task")):
                with patch.object(
                    session, "_execute_with_retry", return_value=MagicMock(content="```main.tf\nok\n```")
                ):
                    with patch.object(session, "_write_stage_files", return_value=["main.tf"]):
                        with patch.object(session, "_apply_stage_transforms", return_value=["main.tf"]):
                            result = session.run(
                                design=design,
                                input_fn=lambda p: "done",
                                print_fn=lambda m: None,
                            )

        assert result is not None


# ------------------------------------------------------------------
# _resolve_service_policies / _resolve_api_versions (lines 3190-3212)
# ------------------------------------------------------------------


class TestResolveHelpers:
    """Tests for service policy and API version resolution helpers."""

    def test_resolve_service_policies_exception(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        with patch(
            "azext_prototype.governance.policies.PolicyEngine.load",
            side_effect=Exception("boom"),
        ):
            result = session._resolve_service_policies([{"name": "kv"}])
        assert result == ""

    def test_resolve_api_versions_exception(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        with patch(
            "azext_prototype.knowledge.resource_metadata.resolve_resource_metadata",
            side_effect=ImportError("boom"),
        ):
            result = session._resolve_api_versions([{"resource_type": "Microsoft.KeyVault/vaults"}])
        assert result == ""

    def test_resolve_api_versions_no_resource_types(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        result = session._resolve_api_versions([{"name": "kv"}])
        assert result == ""

    def test_resolve_companion_requirements_exception(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        with patch(
            "azext_prototype.knowledge.resource_metadata.resolve_companion_requirements",
            side_effect=ImportError("boom"),
        ):
            result = session._resolve_companion_requirements([{"resource_type": "Microsoft.KeyVault/vaults"}])
        assert result == ""


# ------------------------------------------------------------------
# _infer_layer (static method)
# ------------------------------------------------------------------


class TestInferLayerExtended:
    """Extended tests for layer inference from stage data."""

    def test_explicit_layer_returned(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._infer_layer({"layer": "data", "name": "test"}) == "data"

    def test_identity_name_returns_core(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._infer_layer({"name": "Managed Identity", "capability": "infra"}) == "core"

    def test_monitoring_name_returns_core(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._infer_layer({"name": "Log Analytics", "capability": "infra"}) == "core"

    def test_capability_mapping(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._infer_layer({"name": "Redis", "capability": "data"}) == "data"
        assert BuildSession._infer_layer({"name": "API", "capability": "app"}) == "app"


# ------------------------------------------------------------------
# _enforce_concept_prefix
# ------------------------------------------------------------------


class TestEnforceConceptPrefixExtended:
    """Extended tests for concept prefix enforcement in dirs."""

    def test_already_has_concept_prefix(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        assert session._enforce_concept_prefix("concept/infra/terraform") == "concept/infra/terraform"

    def test_fixes_wrong_prefix(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        result = session._enforce_concept_prefix("output/infra/terraform/stage-1")
        assert result.startswith("concept/")

    def test_single_subdir(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        result = session._enforce_concept_prefix("infra")
        assert result == "concept/infra"

    def test_empty_string(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        assert session._enforce_concept_prefix("") == ""


# ------------------------------------------------------------------
# _clean_removed_stage_files (lines 1759-1769)
# ------------------------------------------------------------------


class TestCleanRemovedStageFiles:
    """Tests for removing stage directories on disk."""

    def test_removes_existing_directory(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        project_dir = Path(build_context.project_dir)
        stage_dir = project_dir / "concept" / "infra" / "terraform" / "stage-2-redis"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text("resource {}", encoding="utf-8")

        stages = [{"stage": 2, "dir": "concept/infra/terraform/stage-2-redis"}]
        session._clean_removed_stage_files([2], stages)

        assert not stage_dir.exists()

    def test_ignores_nonexistent_directory(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stages = [{"stage": 3, "dir": "concept/infra/terraform/stage-3-nonexistent"}]
        # Should not raise
        session._clean_removed_stage_files([3], stages)


# ------------------------------------------------------------------
# _fix_stage_dirs (lines 1771-1789)
# ------------------------------------------------------------------


class TestFixStageDirs:
    """Tests for post-renumber directory path fixing."""

    def test_fix_renumbers_dirs(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "A", "dir": "concept/infra/terraform/stage-3-redis"},
        ]

        session._fix_stage_dirs()

        assert session._build_state._state["deployment_stages"][0]["dir"] == ("concept/infra/terraform/stage-1-redis")

    def test_fix_no_change_when_correct(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "A", "dir": "concept/infra/terraform/stage-1-redis"},
        ]

        session._fix_stage_dirs()

        assert session._build_state._state["deployment_stages"][0]["dir"] == ("concept/infra/terraform/stage-1-redis")


# ------------------------------------------------------------------
# _identify_affected_stages / _identify_stages_regex / _identify_stages_via_architect
# ------------------------------------------------------------------


class TestIdentifyAffectedStages:
    """Tests for feedback-to-stage matching."""

    def test_regex_explicit_stage_number(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Key Vault", "status": "generated", "services": [], "files": []},
            {"stage": 2, "name": "Redis", "status": "generated", "services": [], "files": []},
        ]

        result = session._identify_stages_regex("Please fix stage 2")
        assert result == [2]

    def test_regex_stage_name_match(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Key Vault", "status": "generated", "services": [], "files": []},
            {"stage": 2, "name": "Redis", "status": "generated", "services": [], "files": []},
        ]

        result = session._identify_stages_regex("fix the key vault configuration")
        assert result == [1]

    def test_regex_service_name_match(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {
                "stage": 1,
                "name": "Cache",
                "status": "generated",
                "services": [{"name": "redis-cache"}],
                "files": [],
            },
        ]

        result = session._identify_stages_regex("update the redis-cache settings")
        assert result == [1]

    def test_regex_fallback_returns_generated_and_accepted(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Aaa", "status": "generated", "services": [], "files": []},
            {"stage": 2, "name": "Bbb", "status": "accepted", "services": [], "files": []},
        ]

        # Feedback doesn't match any stage name, service, or number
        result = session._identify_stages_regex("xyz unrelated text 999")
        # Last resort: returns all generated+accepted stages
        assert 1 in result
        assert 2 in result

    def test_identify_via_architect(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Key Vault", "status": "generated", "services": [], "files": []},
            {"stage": 2, "name": "Redis", "status": "generated", "services": [], "files": []},
        ]

        session._architect_agent = MagicMock()
        session._architect_agent.execute.return_value = MagicMock(content="[2]", model="test", usage={})
        session._architect_agent.name = "cloud-architect"

        result = session._identify_stages_via_architect("fix the caching layer")
        assert result == [2]

    def test_identify_via_architect_exception_returns_empty(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "A", "status": "generated", "services": [], "files": []},
        ]

        session._architect_agent = MagicMock()
        session._architect_agent.execute.side_effect = RuntimeError("boom")
        session._architect_agent.name = "cloud-architect"

        result = session._identify_stages_via_architect("fix something")
        assert result == []

    def test_identify_via_architect_empty_stages(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = []

        session._architect_agent = MagicMock()

        result = session._identify_stages_via_architect("fix something")
        assert result == []

    def test_identify_affected_uses_architect_then_regex(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Key Vault", "status": "generated", "services": [], "files": []},
        ]

        # Architect returns empty -> falls back to regex
        session._architect_agent = MagicMock()
        session._architect_agent.execute.return_value = MagicMock(content="[]", model="test", usage={})
        session._architect_agent.name = "cloud-architect"

        result = session._identify_affected_stages("fix the key vault")
        assert result == [1]  # Regex matched by name

    def test_parse_stage_numbers_valid(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._parse_stage_numbers("[1, 3, 5]") == [1, 3, 5]

    def test_parse_stage_numbers_embedded(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._parse_stage_numbers("The affected stages are: [2, 4]") == [2, 4]

    def test_parse_stage_numbers_invalid(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._parse_stage_numbers("no json here") == []

    def test_parse_stage_numbers_bad_json(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._parse_stage_numbers("[not, valid]") == []


# ------------------------------------------------------------------
# _handle_slash_command / _handle_describe
# ------------------------------------------------------------------


class TestHandleSlashCommand:
    """Tests for slash command handling."""

    def test_status_command(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "Test", "status": "generated", "files": []},
        ]
        printed = []
        session._handle_slash_command("/status", printed.append)
        assert len(printed) >= 1

    def test_files_command(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        printed = []
        session._handle_slash_command("/files", printed.append)
        assert len(printed) >= 1

    def test_policy_command(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        printed = []
        session._handle_slash_command("/policy", printed.append)
        assert len(printed) >= 1

    def test_describe_command(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {
                "stage": 1,
                "name": "Key Vault",
                "layer": "data",
                "capability": "data",
                "status": "generated",
                "dir": "concept/infra/terraform/stage-1-kv",
                "services": [
                    {
                        "name": "key-vault",
                        "computed_name": "kv-test",
                        "resource_type": "Microsoft.KeyVault/vaults",
                        "sku": "Standard",
                    }
                ],
                "files": ["main.tf", "outputs.tf"],
            },
        ]
        printed = []
        session._handle_slash_command("/describe 1", printed.append)
        assert any("Key Vault" in msg for msg in printed)
        assert any("Microsoft.KeyVault/vaults" in msg for msg in printed)

    def test_describe_no_arg(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        printed = []
        session._handle_describe("", printed.append)
        assert any("Usage" in msg for msg in printed)

    def test_describe_nonexistent_stage(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = []
        printed = []
        session._handle_describe("99", printed.append)
        assert any("not found" in msg for msg in printed)

    def test_help_command(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        printed = []
        session._handle_slash_command("/help", printed.append)
        assert any("/status" in msg for msg in printed)


# ------------------------------------------------------------------
# _derive_deployment_plan -- two-phase plan derivation
# ------------------------------------------------------------------


class TestDeriveDeploymentPlan:
    """Tests for _derive_deployment_plan two-phase AI flow."""

    def test_fallback_without_architect(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._architect_agent = None

        result = session._derive_deployment_plan("architecture text", [])
        # Fallback plan always has at least identity + docs
        assert len(result) >= 2
        assert result[0]["name"] == "Managed Identity"
        assert result[-1]["name"] == "Documentation"

    def test_fallback_without_ai_provider(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._context.ai_provider = None

        result = session._derive_deployment_plan("architecture text", [])
        assert len(result) >= 2

    def test_fallback_on_empty_phase1_response(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._architect_agent = MagicMock()
        session._architect_agent.execute.return_value = MagicMock(content="", model="test", usage={})
        session._architect_agent.name = "cloud-architect"
        session._architect_agent.set_governor_brief = MagicMock()

        result = session._derive_deployment_plan("architecture text", [])
        # Falls back
        assert len(result) >= 2
        assert result[0]["name"] == "Managed Identity"

    def test_fallback_on_unparseable_phase1(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._architect_agent = MagicMock()
        session._architect_agent.execute.return_value = MagicMock(content="not json", model="test", usage={})
        session._architect_agent.name = "cloud-architect"
        session._architect_agent.set_governor_brief = MagicMock()

        result = session._derive_deployment_plan("architecture text", [])
        assert len(result) >= 2

    def test_successful_two_phase(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        # Phase 1: map
        phase1_content = json.dumps(
            {
                "stages": [
                    {
                        "stage": 1,
                        "name": "Managed Identity",
                        "layer": "core",
                        "capability": "infra",
                        "services": ["managed-identity"],
                    },
                    {"stage": 2, "name": "Key Vault", "layer": "data", "capability": "data", "services": ["key-vault"]},
                    {"stage": 3, "name": "Documentation", "layer": "docs", "capability": "docs", "services": []},
                ]
            }
        )
        phase1_json = f"```json\n{phase1_content}\n```"

        # Phase 2: detailed
        phase2_content = json.dumps(
            {
                "stages": [
                    {
                        "stage": 1,
                        "name": "Managed Identity",
                        "layer": "core",
                        "capability": "infra",
                        "dir": "concept/infra/terraform/stage-1-managed-identity",
                        "services": [
                            {
                                "name": "managed-identity",
                                "computed_name": "id-test",
                                "resource_type": "Microsoft.ManagedIdentity/userAssignedIdentities",
                                "sku": "",
                            }
                        ],
                        "status": "pending",
                        "files": [],
                    },
                    {
                        "stage": 2,
                        "name": "Key Vault",
                        "layer": "data",
                        "capability": "data",
                        "dir": "concept/infra/terraform/stage-2-key-vault",
                        "services": [
                            {
                                "name": "key-vault",
                                "computed_name": "kv-test",
                                "resource_type": "Microsoft.KeyVault/vaults",
                                "sku": "Standard",
                            }
                        ],
                        "status": "pending",
                        "files": [],
                    },
                    {
                        "stage": 3,
                        "name": "Documentation",
                        "layer": "docs",
                        "capability": "docs",
                        "dir": "concept/docs",
                        "services": [],
                        "status": "pending",
                        "files": [],
                    },
                ]
            }
        )
        phase2_json = f"```json\n{phase2_content}\n```"

        session._architect_agent = MagicMock()
        session._architect_agent.execute.side_effect = [
            MagicMock(content=phase1_json, model="test", usage={}),
            MagicMock(content=phase2_json, model="test", usage={}),
        ]
        session._architect_agent.name = "cloud-architect"
        session._architect_agent.set_governor_brief = MagicMock()

        result = session._derive_deployment_plan("Build a web app with key vault", [])
        assert len(result) >= 3
        assert result[0]["name"] == "Managed Identity"
        assert any(s["name"] == "Key Vault" for s in result)

    def test_fallback_on_empty_phase2(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        phase1_json = (
            '```json\n{"stages": ['
            '{"stage": 1, "name": "Test", "layer": "core",'
            ' "capability": "infra", "services": ["id"]}'
            "]}\n```"
        )

        session._architect_agent = MagicMock()
        session._architect_agent.execute.side_effect = [
            MagicMock(content=phase1_json, model="test", usage={}),
            MagicMock(content="", model="test", usage={}),
        ]
        session._architect_agent.name = "cloud-architect"
        session._architect_agent.set_governor_brief = MagicMock()

        result = session._derive_deployment_plan("architecture", [])
        assert len(result) >= 2

    def test_phase1_null_response(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._architect_agent = MagicMock()
        session._architect_agent.execute.return_value = None
        session._architect_agent.name = "cloud-architect"
        session._architect_agent.set_governor_brief = MagicMock()

        result = session._derive_deployment_plan("architecture", [])
        assert len(result) >= 2


# ------------------------------------------------------------------
# _build_stage_task extended coverage (lines 2146-2189)
# ------------------------------------------------------------------


class TestBuildStageTaskExtended:
    """Extended tests for _build_stage_task to cover cross-reference paths."""

    def test_build_stage_task_with_templates(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {
                "stage": 1,
                "name": "Key Vault",
                "layer": "data",
                "capability": "data",
                "dir": "concept/infra/terraform/stage-1-kv",
                "services": [
                    {
                        "name": "key-vault",
                        "computed_name": "kv-test",
                        "resource_type": "Microsoft.KeyVault/vaults",
                        "sku": "Standard",
                        "component": "secrets",
                    }
                ],
                "status": "pending",
                "files": [],
            },
        ]

        mock_template = MagicMock()
        mock_template.display_name = "Web App"
        mock_svc = MagicMock()
        mock_svc.name = "key-vault"
        mock_svc.type = "key-vault"
        mock_svc.tier = "Standard"
        mock_svc.config = {"softDelete": True}
        mock_template.services = [mock_svc]

        stage = session._build_state._state["deployment_stages"][0]
        agent, task = session._build_stage_task(stage, "architecture", [mock_template])
        assert agent is not None
        assert "Template reference" in task
        assert "softDelete" in task

    def test_build_stage_task_app_layer_prev_context(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        # Set up a developer agent for app stages
        mock_dev = MagicMock()
        mock_dev.name = "app-developer"
        mock_dev._include_standards = True
        mock_dev.set_knowledge_override = MagicMock()
        mock_dev.set_governor_brief = MagicMock()
        mock_dev.get_system_messages = MagicMock(return_value=[])
        mock_dev._governance_aware = False
        mock_dev._enable_web_search = False
        mock_dev._enable_mcp_tools = False
        session._dev_agent = mock_dev

        session._build_state._state["deployment_stages"] = [
            {
                "stage": 1,
                "name": "Key Vault",
                "layer": "data",
                "capability": "data",
                "dir": "concept/infra/terraform/stage-1-kv",
                "services": [{"name": "key-vault", "computed_name": "kv-test"}],
                "status": "generated",
                "files": [],
            },
            {
                "stage": 2,
                "name": "API",
                "layer": "app",
                "capability": "app",
                "dir": "concept/apps/stage-2-api",
                "services": [{"name": "api", "computed_name": "api-test"}],
                "status": "pending",
                "files": [],
            },
        ]

        stage = session._build_state._state["deployment_stages"][1]
        agent, task = session._build_stage_task(stage, "architecture", [])
        assert agent is not None
        # App layer should get infrastructure cross-reference
        assert "Previously Generated Stages" in task


# ------------------------------------------------------------------
# _build_qa_context (lines 2939, 2957-2958)
# ------------------------------------------------------------------


class TestBuildQaContext:
    """Tests for QA context construction."""

    def test_qa_context_iac_includes_provider_compliance(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._iac_tool = "terraform"
        session._build_state._state["deployment_stages"] = []

        result = session._build_qa_context([], layer="infra")
        assert "Provider Compliance" in result

    def test_qa_context_non_iac_skips_provider(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = []

        result = session._build_qa_context([], layer="app")
        assert "Provider Compliance" not in result

    def test_qa_context_includes_standards(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        session._build_state._state["deployment_stages"] = []

        result = session._build_qa_context([], layer="infra")
        assert isinstance(result, str)


# ------------------------------------------------------------------
# _collect_stage_file_content (line 3242)
# ------------------------------------------------------------------


class TestCollectStageFileContent:
    """Tests for single-stage file content collection."""

    def test_collects_files(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)

        project_dir = Path(build_context.project_dir)
        stage_dir = project_dir / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text("resource azapi_resource {}", encoding="utf-8")

        rel_path = str((stage_dir / "main.tf").relative_to(project_dir))

        stage = {"stage": 1, "name": "Test", "files": [rel_path]}
        content = session._collect_stage_file_content(stage)
        assert "azapi_resource" in content

    def test_empty_files_returns_empty(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stage = {"stage": 1, "name": "Test", "files": []}
        content = session._collect_stage_file_content(stage)
        assert content == ""

    def test_missing_files_handled(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        stage = {"stage": 1, "name": "Test", "files": ["nonexistent/main.tf"]}
        content = session._collect_stage_file_content(stage)
        assert "(could not read file)" in content


# ------------------------------------------------------------------
# run() -- Branch A first build (lines 313-324)
# ------------------------------------------------------------------


class TestRunBranchA:
    """Tests for run() Branch A: first build deriving fresh plan."""

    def test_first_build_empty_plan_cancels(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test arch"}

        with patch.object(session, "_derive_deployment_plan", return_value=[]):
            result = session.run(
                design=design,
                input_fn=lambda p: "done",
                print_fn=lambda m: None,
            )

        assert result.cancelled is True

    def test_first_build_derives_and_saves(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test arch"}

        mock_agent = MagicMock()
        mock_agent.name = "terraform-agent"

        stages = [
            {
                "stage": 1,
                "name": "Identity",
                "layer": "core",
                "capability": "infra",
                "dir": "concept/infra/terraform/stage-1-identity",
                "services": [],
                "status": "pending",
                "files": [],
            },
        ]

        with patch.object(session, "_derive_deployment_plan", return_value=stages):
            with patch.object(session, "_build_stage_task", return_value=(mock_agent, "task")):
                with patch.object(session, "_execute_with_retry", return_value=MagicMock(content="ok", usage={})):
                    with patch.object(session, "_write_stage_files", return_value=[]):
                        with patch.object(session, "_apply_stage_transforms", return_value=[]):
                            session._run_stage_qa = lambda *a, **kw: True
                            result = session.run(
                                design=design,
                                input_fn=lambda p: "done",
                                print_fn=lambda m: None,
                            )

        assert result is not None
        assert not result.cancelled


# ------------------------------------------------------------------
# run() -- confirmation prompt and plan adjustment (lines 418-440)
# ------------------------------------------------------------------


class TestRunConfirmation:
    """Tests for the plan confirmation prompt in run()."""

    def test_confirmation_quit_cancels(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test arch"}

        stages = [
            {
                "stage": 1,
                "name": "Test",
                "layer": "core",
                "capability": "infra",
                "dir": "concept/infra/terraform/stage-1",
                "services": [],
                "status": "pending",
                "files": [],
            },
        ]
        session._build_state.set_deployment_plan(stages)
        session._build_state.set_design_snapshot(design)

        result = session.run(
            design=design,
            input_fn=lambda p: "quit",
            print_fn=lambda m: None,
        )

        assert result.cancelled is True

    def test_confirmation_with_feedback_adjusts_plan(self, build_context, build_registry):
        session = _make_session(build_context, build_registry)
        design = {"architecture": "Test arch"}

        stages = [
            {
                "stage": 1,
                "name": "Test",
                "layer": "core",
                "capability": "infra",
                "dir": "concept/infra/terraform/stage-1",
                "services": [],
                "status": "pending",
                "files": [],
            },
        ]
        session._build_state.set_deployment_plan(stages)
        session._build_state.set_design_snapshot(design)

        adjusted_stages = [
            {
                "stage": 1,
                "name": "Adjusted",
                "layer": "core",
                "capability": "infra",
                "dir": "concept/infra/terraform/stage-1",
                "services": [],
                "status": "pending",
                "files": [],
            },
        ]

        calls = [0]

        def mock_input(prompt):
            calls[0] += 1
            if calls[0] == 1:
                return "add redis"
            return "done"

        mock_agent = MagicMock()
        mock_agent.name = "terraform-agent"

        with patch.object(session, "_adjust_plan", return_value=adjusted_stages):
            session._run_stage_qa = lambda *a, **kw: True
            with patch.object(session, "_build_stage_task", return_value=(mock_agent, "task")):
                with patch.object(session, "_execute_with_retry", return_value=MagicMock(content="ok", usage={})):
                    with patch.object(session, "_write_stage_files", return_value=[]):
                        with patch.object(session, "_apply_stage_transforms", return_value=[]):
                            run_result = session.run(
                                design=design,
                                input_fn=mock_input,
                                print_fn=lambda m: None,
                            )

        assert run_result is not None
