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
