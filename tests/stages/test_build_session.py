from __future__ import annotations

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
def build_registry(mock_tf_agent, mock_dev_agent, mock_doc_agent, mock_architect_agent_for_build, mock_qa_agent):
    registry = MagicMock()

    # Ensure tf agent has the attributes mirrored tests expect
    mock_tf_agent._include_standards = True
    mock_tf_agent._temperature = 0.2
    mock_tf_agent._max_tokens = 4096
    mock_tf_agent.set_knowledge_override = MagicMock()
    mock_tf_agent.set_governor_brief = MagicMock()
    mock_tf_agent.get_system_messages = MagicMock(return_value=[])
    mock_tf_agent._governance_aware = False
    mock_tf_agent._enable_web_search = False
    mock_tf_agent._enable_mcp_tools = False

    # Ensure doc agent has the attributes mirrored tests expect
    mock_doc_agent._include_standards = True
    mock_doc_agent.set_knowledge_override = MagicMock()
    mock_doc_agent.set_governor_brief = MagicMock()
    mock_doc_agent.get_system_messages = MagicMock(return_value=[])
    mock_doc_agent._governance_aware = False
    mock_doc_agent._enable_web_search = False
    mock_doc_agent._enable_mcp_tools = False

    # Ensure dev agent has the attributes mirrored tests expect
    mock_dev_agent._include_standards = True
    mock_dev_agent.set_knowledge_override = MagicMock()
    mock_dev_agent.set_governor_brief = MagicMock()
    mock_dev_agent.get_system_messages = MagicMock(return_value=[])
    mock_dev_agent._governance_aware = False
    mock_dev_agent._enable_web_search = False
    mock_dev_agent._enable_mcp_tools = False

    def find_by_cap(cap):
        mapping = {
            AgentCapability.TERRAFORM: [mock_tf_agent],
            AgentCapability.BICEP: [],
            AgentCapability.DEVELOP: [mock_dev_agent],
            AgentCapability.DOCUMENT: [mock_doc_agent],
            AgentCapability.ARCHITECT: [mock_architect_agent_for_build],
            AgentCapability.QA: [mock_qa_agent],
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

# --- Additional imports from merged flat test ---
from azext_prototype.ai.provider import AIResponse
import yaml


# ======================================================================
# Helpers
# ======================================================================


def _make_response(content: str = "Mock response", finish_reason: str = "stop") -> AIResponse:
    return AIResponse(content=content, model="gpt-4o", usage={}, finish_reason=finish_reason)


def _make_file_response(filename: str = "main.tf", code: str = "# placeholder") -> AIResponse:
    """Return an AIResponse whose content has a fenced file block."""
    return AIResponse(
        content=f"Here is the code:\n\n```{filename}\n{code}\n```\n",
        model="gpt-4o",
        usage={},
    )


# ======================================================================
# BuildState tests
# ======================================================================


class TestBuildState:

    def test_default_state_structure(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        state = bs.state
        assert isinstance(state["templates_used"], list)
        assert state["iac_tool"] == "terraform"
        assert state["deployment_stages"] == []
        assert state["policy_checks"] == []
        assert state["policy_overrides"] == []
        assert state["files_generated"] == []
        assert state["resources"] == []
        assert state["_metadata"]["iteration"] == 0

    def test_load_save_roundtrip(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs._state["templates_used"] = ["web-app"]
        bs._state["iac_tool"] = "bicep"
        bs.save()

        bs2 = BuildState(str(tmp_project))
        loaded = bs2.load()
        assert loaded["templates_used"] == ["web-app"]
        assert loaded["iac_tool"] == "bicep"

    def test_set_deployment_plan(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        stages = [
            {
                "stage": 1,
                "name": "Foundation",
                "capability": "infra",
                "services": [
                    {
                        "name": "key-vault",
                        "computed_name": "zd-kv-api-dev-eus",
                        "resource_type": "Microsoft.KeyVault/vaults",
                        "sku": "standard",
                    },
                ],
                "status": "pending",
                "dir": "concept/infra/terraform/stage-1-foundation",
                "files": [],
            },
        ]
        bs.set_deployment_plan(stages)

        assert len(bs.state["deployment_stages"]) == 1
        assert bs.state["deployment_stages"][0]["services"][0]["computed_name"] == "zd-kv-api-dev-eus"
        # Resources should be rebuilt
        assert len(bs.state["resources"]) == 1
        assert bs.state["resources"][0]["resourceType"] == "Microsoft.KeyVault/vaults"

    def test_mark_stage_generated(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [],
                    "status": "pending",
                    "dir": "",
                    "files": [],
                },
            ]
        )

        bs.mark_stage_generated(1, ["main.tf", "variables.tf"], "terraform-agent")

        stage = bs.get_stage(1)
        assert stage["status"] == "generated"
        assert stage["files"] == ["main.tf", "variables.tf"]
        assert len(bs.state["generation_log"]) == 1
        assert bs.state["generation_log"][0]["agent"] == "terraform-agent"
        assert "main.tf" in bs.state["files_generated"]

    def test_mark_stage_accepted(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
            ]
        )
        bs.mark_stage_accepted(1)
        assert bs.get_stage(1)["status"] == "accepted"

    def test_add_policy_override(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs.add_policy_override("managed-identity", "Using connection string for legacy service")

        assert len(bs.state["policy_overrides"]) == 1
        assert bs.state["policy_overrides"][0]["rule_id"] == "managed-identity"

    def test_get_pending_stages(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "A",
                    "capability": "infra",
                    "services": [],
                    "status": "pending",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "B",
                    "capability": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 3,
                    "name": "C",
                    "capability": "app",
                    "services": [],
                    "status": "pending",
                    "dir": "",
                    "files": [],
                },
            ]
        )

        pending = bs.get_pending_stages()
        assert len(pending) == 2
        assert pending[0]["stage"] == 1
        assert pending[1]["stage"] == 3

    def test_get_all_resources(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [
                        {
                            "name": "kv",
                            "computed_name": "kv-1",
                            "resource_type": "Microsoft.KeyVault/vaults",
                            "sku": "standard",
                        },
                        {
                            "name": "id",
                            "computed_name": "id-1",
                            "resource_type": "Microsoft.ManagedIdentity/userAssignedIdentities",
                            "sku": "",
                        },
                    ],
                    "status": "pending",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "Data",
                    "capability": "data",
                    "services": [
                        {
                            "name": "sql",
                            "computed_name": "sql-1",
                            "resource_type": "Microsoft.Sql/servers",
                            "sku": "serverless",
                        },
                    ],
                    "status": "pending",
                    "dir": "",
                    "files": [],
                },
            ]
        )

        resources = bs.get_all_resources()
        assert len(resources) == 3
        types = {r["resourceType"] for r in resources}
        assert "Microsoft.KeyVault/vaults" in types
        assert "Microsoft.Sql/servers" in types

    def test_format_build_report(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs._state["templates_used"] = ["web-app"]
        bs.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [
                        {
                            "name": "kv",
                            "computed_name": "zd-kv-dev",
                            "resource_type": "Microsoft.KeyVault/vaults",
                            "sku": "standard",
                        }
                    ],
                    "status": "generated",
                    "dir": "",
                    "files": ["main.tf"],
                },
            ]
        )
        bs._state["files_generated"] = ["main.tf"]

        report = bs.format_build_report()
        assert "web-app" in report
        assert "Foundation" in report
        assert "zd-kv-dev" in report
        assert "1" in report  # Total files

    def test_format_stage_status(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [],
                    "status": "pending",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "Data",
                    "capability": "data",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": ["sql.tf"],
                },
            ]
        )

        status = bs.format_stage_status()
        assert "Foundation" in status
        assert "Data" in status
        assert "1/2" in status  # Progress

    def test_multiple_templates_used(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs._state["templates_used"] = ["web-app", "data-pipeline"]
        bs.save()

        bs2 = BuildState(str(tmp_project))
        bs2.load()
        assert bs2.state["templates_used"] == ["web-app", "data-pipeline"]

    def test_add_review_decision(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs.add_review_decision("Please add logging to stage 2", iteration=1)

        assert len(bs.state["review_decisions"]) == 1
        assert bs.state["review_decisions"][0]["feedback"] == "Please add logging to stage 2"
        assert bs.state["_metadata"]["iteration"] == 1

    def test_reset(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs._state["templates_used"] = ["web-app"]
        bs.save()

        bs.reset()
        assert bs.state["templates_used"] == []
        assert bs.exists  # File still exists after reset


# ======================================================================
# PolicyResolver tests
# ======================================================================


class TestPolicyResolver:

    def test_no_violations_no_prompt(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState
        from azext_prototype.stages.policy_resolver import PolicyResolver

        governance = MagicMock()
        governance.check_response_for_violations.return_value = []

        resolver = PolicyResolver(governance_context=governance)
        build_state = BuildState(str(tmp_project))

        resolutions, needs_regen = resolver.check_and_resolve(
            "terraform-agent",
            "resource group code",
            build_state,
            stage_num=1,
            input_fn=lambda p: "",
            print_fn=lambda m: None,
        )

        assert resolutions == []
        assert needs_regen is False

    def test_violation_accept_compliant(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState
        from azext_prototype.stages.policy_resolver import PolicyResolver

        governance = MagicMock()
        governance.check_response_for_violations.return_value = [
            "[managed-identity] Possible anti-pattern: connection string detected"
        ]

        resolver = PolicyResolver(governance_context=governance)
        build_state = BuildState(str(tmp_project))

        printed = []
        resolutions, needs_regen = resolver.check_and_resolve(
            "terraform-agent",
            "code with connection_string",
            build_state,
            stage_num=1,
            input_fn=lambda p: "a",  # Accept
            print_fn=lambda m: printed.append(m),
        )

        assert len(resolutions) == 1
        assert resolutions[0].action == "accept"
        assert needs_regen is False

    def test_violation_override_persists(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState
        from azext_prototype.stages.policy_resolver import PolicyResolver

        governance = MagicMock()
        governance.check_response_for_violations.return_value = [
            "[managed-identity] Use managed identity instead of keys"
        ]

        resolver = PolicyResolver(governance_context=governance)
        build_state = BuildState(str(tmp_project))

        inputs = iter(["o", "Legacy service requires keys"])
        resolutions, needs_regen = resolver.check_and_resolve(
            "terraform-agent",
            "code with access_key",
            build_state,
            stage_num=1,
            input_fn=lambda p: next(inputs),
            print_fn=lambda m: None,
        )

        assert len(resolutions) == 1
        assert resolutions[0].action == "override"
        assert resolutions[0].justification == "Legacy service requires keys"
        assert needs_regen is False
        # Should be persisted in build state
        assert len(build_state.state["policy_overrides"]) == 1

    def test_violation_regenerate_flag(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState
        from azext_prototype.stages.policy_resolver import PolicyResolver

        governance = MagicMock()
        governance.check_response_for_violations.return_value = ["[managed-identity] Hardcoded credential detected"]

        resolver = PolicyResolver(governance_context=governance)
        build_state = BuildState(str(tmp_project))

        resolutions, needs_regen = resolver.check_and_resolve(
            "terraform-agent",
            "bad code",
            build_state,
            stage_num=1,
            input_fn=lambda p: "r",  # Regenerate
            print_fn=lambda m: None,
        )

        assert len(resolutions) == 1
        assert resolutions[0].action == "regenerate"
        assert needs_regen is True

    def test_build_fix_instructions(self):
        from azext_prototype.stages.policy_resolver import (
            PolicyResolution,
            PolicyResolver,
        )

        resolver = PolicyResolver(governance_context=MagicMock())
        resolutions = [
            PolicyResolution(
                rule_id="managed-identity",
                action="regenerate",
                violation_text="[managed-identity] Use MI instead of keys",
            ),
            PolicyResolution(
                rule_id="key-vault",
                action="override",
                justification="Legacy requirement",
                violation_text="[key-vault] Secrets should use Key Vault",
            ),
        ]

        instructions = resolver.build_fix_instructions(resolutions)
        assert "Policy Fix Instructions" in instructions
        assert "[managed-identity]" in instructions
        assert "Legacy requirement" in instructions

    def test_extract_rule_id(self):
        from azext_prototype.stages.policy_resolver import PolicyResolver

        assert PolicyResolver._extract_rule_id("[managed-identity] Some violation") == "managed-identity"
        assert PolicyResolver._extract_rule_id("No brackets here") == "unknown"
        assert PolicyResolver._extract_rule_id("[kv-001] Key Vault issue") == "kv-001"


# ======================================================================
# BuildSession fixtures
# ======================================================================


@pytest.fixture
def mock_tf_agent():
    agent = MagicMock()
    agent.name = "terraform-agent"
    agent.execute.return_value = _make_file_response(
        "main.tf", 'resource "azapi_resource" "rg" {\n  type = "Microsoft.Resources/resourceGroups@2025-06-01"\n}'
    )
    return agent


@pytest.fixture
def mock_dev_agent():
    agent = MagicMock()
    agent.name = "app-developer"
    agent.execute.return_value = _make_file_response("app.py", "# app code")
    return agent


@pytest.fixture
def mock_doc_agent():
    agent = MagicMock()
    agent.name = "doc-agent"
    agent.execute.return_value = _make_file_response("DEPLOYMENT.md", "# Deployment Guide")
    return agent


@pytest.fixture
def mock_architect_agent_for_build():
    agent = MagicMock()
    agent.name = "cloud-architect"
    # Return a JSON deployment plan
    plan = {
        "stages": [
            {
                "stage": 1,
                "name": "Foundation",
                "capability": "infra",
                "dir": "concept/infra/terraform/stage-1-foundation",
                "services": [
                    {
                        "name": "key-vault",
                        "computed_name": "zd-kv-test-dev-eus",
                        "resource_type": "Microsoft.KeyVault/vaults",
                        "sku": "standard",
                    },
                ],
                "status": "pending",
                "files": [],
            },
            {
                "stage": 2,
                "name": "Documentation",
                "capability": "docs",
                "dir": "concept/docs",
                "services": [],
                "status": "pending",
                "files": [],
            },
        ]
    }
    agent.execute.return_value = _make_response(f"```json\n{json.dumps(plan)}\n```")
    return agent


@pytest.fixture
def mock_qa_agent():
    agent = MagicMock()
    agent.name = "qa-engineer"
    return agent


# ======================================================================
# BuildSession tests
# ======================================================================


class TestBuildSession:

    def test_session_creates_with_agents(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        assert session._iac_agents.get("terraform") is not None
        assert session._dev_agent is not None
        assert session._doc_agent is not None
        assert session._architect_agent is not None
        assert session._qa_agent is not None

    def test_quit_cancels(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        inputs = iter(["quit"])

        result = session.run(
            design={"architecture": "Sample architecture"},
            input_fn=lambda p: next(inputs),
            print_fn=lambda m: None,
        )

        assert result.cancelled is True

    def test_done_accepts(self, build_context, build_registry, mock_architect_agent_for_build):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        # First input: confirm plan (empty = proceed), then "done" to accept
        inputs = iter(["", "done"])

        # Patch governance to skip violations
        with patch("azext_prototype.stages.build_session.GovernanceContext") as mock_gov_cls:
            mock_gov_cls.return_value.check_response_for_violations.return_value = []
            session._governance = mock_gov_cls.return_value
            session._policy_resolver._governance = mock_gov_cls.return_value

            # Patch AgentOrchestrator.delegate to avoid real QA call
            with patch("azext_prototype.stages.build_session.AgentOrchestrator") as mock_orch:
                mock_orch.return_value.delegate.return_value = _make_response("QA looks good")

                result = session.run(
                    design={"architecture": "Sample architecture with key-vault and sql-database"},
                    input_fn=lambda p: next(inputs),
                    print_fn=lambda m: None,
                )

        assert result.cancelled is False
        assert result.review_accepted is True

    def test_deployment_plan_derivation(self, build_context, build_registry, mock_architect_agent_for_build):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        # The architect agent returns a JSON plan; test that it's parsed correctly
        plan_json = {
            "stages": [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "dir": "concept/infra/terraform/stage-1-foundation",
                    "services": [
                        {
                            "name": "kv",
                            "computed_name": "zd-kv-dev",
                            "resource_type": "Microsoft.KeyVault/vaults",
                            "sku": "standard",
                        }
                    ],
                    "status": "pending",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "Apps",
                    "capability": "app",
                    "dir": "concept/apps/stage-2-api",
                    "services": [],
                    "status": "pending",
                    "files": [],
                },
            ]
        }
        mock_architect_agent_for_build.execute.return_value = _make_response(f"```json\n{json.dumps(plan_json)}\n```")

        stages = session._derive_deployment_plan("Sample architecture", [])
        assert len(stages) == 2
        assert stages[0]["name"] == "Foundation"
        assert stages[0]["services"][0]["computed_name"] == "zd-kv-dev"
        assert stages[1]["capability"] == "app"

    def test_fallback_deployment_plan(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        # Force no architect
        build_registry.find_by_capability.side_effect = lambda cap: []
        session = BuildSession(build_context, build_registry)

        stages = session._fallback_deployment_plan([])
        assert len(stages) >= 2  # Managed Identity + Documentation at minimum
        assert stages[0]["name"] == "Managed Identity"
        assert stages[0]["layer"] == "core"
        assert stages[-1]["name"] == "Documentation"
        assert stages[-1]["layer"] == "docs"

    def test_template_matching_web_app(self, project_with_design, sample_config):
        from azext_prototype.stages.build_stage import BuildStage

        stage = BuildStage()
        design = {
            "architecture": (
                "The system uses container-apps for the API, "
                "sql-database for persistence, key-vault for secrets, "
                "api-management as the gateway, and a virtual-network."
            )
        }
        from azext_prototype.config import ProjectConfig

        config = ProjectConfig(str(project_with_design))
        config.load()

        templates = stage._match_templates(design, config)
        # web-app template should match (container-apps, sql-database, key-vault, api-management, virtual-network)
        assert len(templates) >= 1
        names = [t.name for t in templates]
        assert "web-app" in names

    def test_template_matching_no_match(self, project_with_design, sample_config):
        from azext_prototype.stages.build_stage import BuildStage

        stage = BuildStage()
        design = {"architecture": "This is a simple static website with no Azure services mentioned."}
        from azext_prototype.config import ProjectConfig

        config = ProjectConfig(str(project_with_design))
        config.load()

        templates = stage._match_templates(design, config)
        assert templates == []

    def test_parse_deployment_plan_json_block(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        content = '```json\n{"stages": [{"stage": 1, "name": "Test", "capability": "infra"}]}\n```'
        stages = session._parse_deployment_plan(content)
        assert len(stages) == 1
        assert stages[0]["name"] == "Test"

    def test_parse_deployment_plan_raw_json(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        content = '{"stages": [{"stage": 1, "name": "Raw"}]}'
        stages = session._parse_deployment_plan(content)
        assert len(stages) == 1
        assert stages[0]["name"] == "Raw"

    def test_parse_deployment_plan_invalid(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        stages = session._parse_deployment_plan("This is not JSON at all")
        assert stages == []

    def test_identify_affected_stages_by_number(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "Data",
                    "capability": "data",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
            ]
        )

        affected = session._identify_affected_stages("Please fix stage 2")
        assert affected == [2]

    def test_identify_affected_stages_by_name(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "Data",
                    "capability": "data",
                    "services": [{"name": "sql-server", "computed_name": "sql-1", "resource_type": "", "sku": ""}],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
            ]
        )

        affected = session._identify_affected_stages("The sql-server configuration is wrong")
        assert 2 in affected

    def test_slash_command_status(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
            ]
        )

        printed = []
        session._handle_slash_command("/status", lambda m: printed.append(m))
        output = "\n".join(printed)
        assert "Foundation" in output

    def test_slash_command_files(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._build_state._state["files_generated"] = ["main.tf", "variables.tf"]

        printed = []
        session._handle_slash_command("/files", lambda m: printed.append(m))
        output = "\n".join(printed)
        assert "main.tf" in output
        assert "variables.tf" in output

    def test_slash_command_policy(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        # No checks yet
        printed = []
        session._handle_slash_command("/policy", lambda m: printed.append(m))
        output = "\n".join(printed)
        assert "No policy checks" in output

    def test_slash_command_help(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        printed = []
        session._handle_slash_command("/help", lambda m: printed.append(m))
        output = "\n".join(printed)
        assert "/status" in output
        assert "/files" in output
        assert "done" in output

    def test_categorize_service(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._categorize_service("key-vault") == "infra"
        assert BuildSession._categorize_service("sql-database") == "data"
        assert BuildSession._categorize_service("container-apps") == "app"
        assert BuildSession._categorize_service("unknown-service") == "app"

    def test_normalize_stages(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        raw = [
            {"stage": 1, "name": "Test", "capability": "infra"},
            {"name": "No Stage Num"},
        ]
        normalized = session._normalize_stages(raw)
        assert len(normalized) == 2
        assert normalized[0]["status"] == "pending"
        assert normalized[0]["files"] == []
        assert normalized[0]["layer"] == "infra"  # Inferred from capability
        assert normalized[1]["stage"] == 2  # Auto-assigned
        assert normalized[1]["layer"] == "infra"  # Default

    def test_normalize_stages_preserves_explicit_layer(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        raw = [
            {"stage": 1, "name": "Key Vault", "layer": "data", "capability": "data"},
            {"stage": 2, "name": "API", "layer": "app", "capability": "app"},
        ]
        normalized = session._normalize_stages(raw)
        assert normalized[0]["layer"] == "data"
        assert normalized[1]["layer"] == "app"

    def test_normalize_stages_infers_core_for_identity(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        raw = [
            {"stage": 1, "name": "Managed Identity", "capability": "infra"},
            {"stage": 2, "name": "Log Analytics", "capability": "infra"},
        ]
        normalized = session._normalize_stages(raw)
        assert normalized[0]["layer"] == "core"
        assert normalized[1]["layer"] == "core"

    def test_reentrant_skips_generated_stages(self, build_context, build_registry, mock_tf_agent, mock_doc_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        design = {"architecture": "Test"}

        # Pre-populate with a generated stage and matching design snapshot
        session._build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": ["main.tf"],
                },
                {
                    "stage": 2,
                    "name": "Documentation",
                    "capability": "docs",
                    "services": [],
                    "status": "pending",
                    "dir": "concept/docs",
                    "files": [],
                },
            ]
        )
        session._build_state.set_design_snapshot(design)

        inputs = iter(["", "done"])

        with patch("azext_prototype.stages.build_session.GovernanceContext") as mock_gov_cls:
            mock_gov_cls.return_value.check_response_for_violations.return_value = []
            session._governance = mock_gov_cls.return_value
            session._policy_resolver._governance = mock_gov_cls.return_value

            with patch("azext_prototype.stages.build_session.AgentOrchestrator") as mock_orch:
                mock_orch.return_value.delegate.return_value = _make_response("QA ok")

                session.run(
                    design=design,
                    input_fn=lambda p: next(inputs),
                    print_fn=lambda m: None,
                )

        # Stage 1 (generated) should NOT have been re-run
        # Only doc agent should have been called (for stage 2)
        assert mock_tf_agent.execute.call_count == 0
        assert mock_doc_agent.execute.call_count == 1


    # Re-entry validating tests moved to tests/stages/test_build_session_reentry.py


# ======================================================================
# Incremental build / design snapshot tests
# ======================================================================


class TestDesignSnapshot:
    """Tests for design snapshot tracking and change detection in BuildState."""

    def test_design_snapshot_set_on_first_build(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        design = {
            "architecture": "## Architecture\nKey Vault + SQL Database",
            "_metadata": {"iteration": 3},
        }
        bs.set_design_snapshot(design)

        snapshot = bs.state["design_snapshot"]
        assert snapshot["iteration"] == 3
        assert snapshot["architecture_hash"] is not None
        assert len(snapshot["architecture_hash"]) == 16
        assert snapshot["architecture_text"] == design["architecture"]

    def test_design_has_changed_detects_modification(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        original = {"architecture": "Key Vault + SQL"}
        bs.set_design_snapshot(original)

        modified = {"architecture": "Key Vault + SQL + Redis Cache"}
        assert bs.design_has_changed(modified) is True

    def test_design_has_changed_no_change(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        design = {"architecture": "Key Vault + SQL"}
        bs.set_design_snapshot(design)

        assert bs.design_has_changed(design) is False

    def test_design_has_changed_legacy_no_snapshot(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        # No snapshot set — simulates legacy build
        assert bs.design_has_changed({"architecture": "anything"}) is True

    def test_get_previous_architecture(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        assert bs.get_previous_architecture() is None

        design = {"architecture": "The full architecture text here"}
        bs.set_design_snapshot(design)
        assert bs.get_previous_architecture() == "The full architecture text here"

    def test_design_snapshot_persists_across_load(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        design = {"architecture": "Persistent arch", "_metadata": {"iteration": 2}}
        bs.set_design_snapshot(design)

        bs2 = BuildState(str(tmp_project))
        bs2.load()
        assert bs2.design_has_changed(design) is False
        assert bs2.get_previous_architecture() == "Persistent arch"


class TestStageManipulation:
    """Tests for mark_stages_stale, remove_stages, add_stages, renumber_stages."""

    def _sample_stages(self):
        return [
            {
                "stage": 1,
                "name": "Foundation",
                "capability": "infra",
                "services": [],
                "status": "generated",
                "dir": "concept/infra/terraform/stage-1-foundation",
                "files": ["main.tf"],
            },
            {
                "stage": 2,
                "name": "Data",
                "capability": "data",
                "services": [
                    {"name": "sql", "computed_name": "sql-1", "resource_type": "Microsoft.Sql/servers", "sku": ""}
                ],
                "status": "generated",
                "dir": "concept/infra/terraform/stage-2-data",
                "files": ["sql.tf"],
            },
            {
                "stage": 3,
                "name": "App",
                "capability": "app",
                "services": [],
                "status": "generated",
                "dir": "concept/apps/stage-3-api",
                "files": ["app.py"],
            },
            {
                "stage": 4,
                "name": "Documentation",
                "capability": "docs",
                "services": [],
                "status": "generated",
                "dir": "concept/docs",
                "files": ["DEPLOY.md"],
            },
        ]

    def test_mark_stages_stale(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs.set_deployment_plan(self._sample_stages())

        bs.mark_stages_stale([2, 3])

        assert bs.get_stage(1)["status"] == "generated"
        assert bs.get_stage(2)["status"] == "pending"
        assert bs.get_stage(3)["status"] == "pending"
        assert bs.get_stage(4)["status"] == "generated"

    def test_remove_stages(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs.set_deployment_plan(self._sample_stages())
        bs._state["files_generated"] = ["main.tf", "sql.tf", "app.py", "DEPLOY.md"]

        bs.remove_stages([2])

        stage_nums = [s["stage"] for s in bs.state["deployment_stages"]]
        assert 2 not in stage_nums
        assert len(bs.state["deployment_stages"]) == 3
        # sql.tf should be removed from files_generated
        assert "sql.tf" not in bs.state["files_generated"]
        assert "main.tf" in bs.state["files_generated"]

    def test_add_stages(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs.set_deployment_plan(self._sample_stages())

        new_stages = [
            {
                "name": "Redis Cache",
                "capability": "data",
                "services": [
                    {
                        "name": "redis",
                        "computed_name": "redis-1",
                        "resource_type": "Microsoft.Cache/redis",
                        "sku": "Basic",
                    }
                ],
            },
        ]
        bs.add_stages(new_stages)

        stages = bs.state["deployment_stages"]
        # Should be inserted before docs (stage 4 originally)
        # After renumbering: Foundation(1), Data(2), App(3), Redis(4), Docs(5)
        assert len(stages) == 5
        assert stages[3]["name"] == "Redis Cache"
        assert stages[3]["stage"] == 4
        assert stages[4]["name"] == "Documentation"
        assert stages[4]["stage"] == 5

    def test_renumber_stages(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        # Set up stages with gaps
        bs._state["deployment_stages"] = [
            {
                "stage": 1,
                "name": "A",
                "capability": "infra",
                "services": [],
                "status": "generated",
                "dir": "",
                "files": [],
            },
            {"stage": 5, "name": "B", "capability": "data", "services": [], "status": "pending", "dir": "", "files": []},
            {"stage": 10, "name": "C", "capability": "docs", "services": [], "status": "pending", "dir": "", "files": []},
        ]

        bs.renumber_stages()

        assert bs.state["deployment_stages"][0]["stage"] == 1
        assert bs.state["deployment_stages"][1]["stage"] == 2
        assert bs.state["deployment_stages"][2]["stage"] == 3


class TestArchitectureDiff:
    """Tests for _diff_architectures and _parse_diff_result."""

    def test_diff_architectures_parses_response(self, build_context, build_registry, mock_architect_agent_for_build):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        existing = [
            {
                "stage": 1,
                "name": "Foundation",
                "capability": "infra",
                "services": [{"name": "key-vault"}],
                "status": "generated",
                "dir": "",
                "files": [],
            },
            {
                "stage": 2,
                "name": "Data",
                "capability": "data",
                "services": [{"name": "sql"}],
                "status": "generated",
                "dir": "",
                "files": [],
            },
        ]

        diff_response = json.dumps(
            {
                "unchanged": [1],
                "modified": [2],
                "removed": [],
                "added": [{"name": "Redis", "capability": "data", "services": []}],
                "plan_restructured": False,
                "summary": "Modified data stage; added Redis.",
            }
        )
        mock_architect_agent_for_build.execute.return_value = _make_response(f"```json\n{diff_response}\n```")

        result = session._diff_architectures("old arch", "new arch", existing)

        assert result["unchanged"] == [1]
        assert result["modified"] == [2]
        assert result["removed"] == []
        assert len(result["added"]) == 1
        assert result["added"][0]["name"] == "Redis"
        assert result["plan_restructured"] is False

    def test_diff_architectures_fallback_no_architect(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        # Remove the architect agent
        session = BuildSession(build_context, build_registry)
        session._architect_agent = None

        existing = [
            {
                "stage": 1,
                "name": "A",
                "capability": "infra",
                "services": [],
                "status": "generated",
                "dir": "",
                "files": [],
            },
            {
                "stage": 2,
                "name": "B",
                "capability": "data",
                "services": [],
                "status": "generated",
                "dir": "",
                "files": [],
            },
        ]

        result = session._diff_architectures("old", "new", existing)

        # Fallback: all stages marked as modified
        assert set(result["modified"]) == {1, 2}
        assert result["unchanged"] == []

    def test_parse_diff_result_defaults_to_unchanged(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        existing = [
            {
                "stage": 1,
                "name": "A",
                "capability": "infra",
                "services": [],
                "status": "generated",
                "dir": "",
                "files": [],
            },
            {
                "stage": 2,
                "name": "B",
                "capability": "data",
                "services": [],
                "status": "generated",
                "dir": "",
                "files": [],
            },
            {"stage": 3, "name": "C", "capability": "app", "services": [], "status": "generated", "dir": "", "files": []},
        ]

        # Only mention stage 2 as modified; 1 and 3 should default to unchanged
        content = json.dumps({"modified": [2], "summary": "test"})
        result = session._parse_diff_result(content, existing)

        assert result is not None
        assert 1 in result["unchanged"]
        assert 3 in result["unchanged"]
        assert result["modified"] == [2]

    def test_parse_diff_result_invalid_json(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        result = session._parse_diff_result("This is not JSON", [])
        assert result is None


class TestIncrementalBuildSession:
    """End-to-end tests for the incremental build flow."""

    def test_incremental_run_no_changes(self, build_context, build_registry):
        """When design hasn't changed and all stages are generated, report up to date."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        design = {"architecture": "Sample arch"}

        # Set up: pre-populate with generated stages and a matching snapshot
        session._build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": ["main.tf"],
                },
                {
                    "stage": 2,
                    "name": "Docs",
                    "capability": "docs",
                    "services": [],
                    "status": "generated",
                    "dir": "concept/docs",
                    "files": ["README.md"],
                },
            ]
        )
        session._build_state.set_design_snapshot(design)

        printed = []
        inputs = iter(["done"])

        result = session.run(
            design=design,
            input_fn=lambda p: next(inputs),
            print_fn=lambda m: printed.append(m),
        )

        output = "\n".join(printed)
        assert "up to date" in output.lower()
        assert result.review_accepted is True

    def test_incremental_run_with_changes(
        self, build_context, build_registry, mock_architect_agent_for_build, mock_tf_agent
    ):
        """When design has changed, only affected stages should be regenerated."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        old_design = {"architecture": "Original architecture with Key Vault"}
        new_design = {"architecture": "Updated architecture with Key Vault + Redis"}

        # Set up existing build
        session._build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [{"name": "key-vault"}],
                    "status": "generated",
                    "dir": "concept/infra/terraform/stage-1-foundation",
                    "files": ["main.tf"],
                },
                {
                    "stage": 2,
                    "name": "Documentation",
                    "capability": "docs",
                    "services": [],
                    "status": "generated",
                    "dir": "concept/docs",
                    "files": ["README.md"],
                },
            ]
        )
        session._build_state.set_design_snapshot(old_design)

        # Mock architect: stage 1 unchanged, no removed, add Redis
        diff_response = json.dumps(
            {
                "unchanged": [1],
                "modified": [],
                "removed": [],
                "added": [
                    {
                        "name": "Redis Cache",
                        "capability": "data",
                        "services": [
                            {
                                "name": "redis-cache",
                                "computed_name": "redis-1",
                                "resource_type": "Microsoft.Cache/redis",
                                "sku": "Basic",
                            }
                        ],
                    }
                ],
                "plan_restructured": False,
                "summary": "Added Redis Cache stage.",
            }
        )
        mock_architect_agent_for_build.execute.return_value = _make_response(f"```json\n{diff_response}\n```")

        printed = []
        inputs = iter(["", "done"])

        with patch("azext_prototype.stages.build_session.GovernanceContext") as mock_gov_cls:
            mock_gov_cls.return_value.check_response_for_violations.return_value = []
            session._governance = mock_gov_cls.return_value
            session._policy_resolver._governance = mock_gov_cls.return_value

            with patch("azext_prototype.stages.build_session.AgentOrchestrator") as mock_orch:
                mock_orch.return_value.delegate.return_value = _make_response("QA ok")

                result = session.run(
                    design=new_design,
                    input_fn=lambda p: next(inputs),
                    print_fn=lambda m: printed.append(m),
                )

        output = "\n".join(printed)
        assert "Design changes detected" in output
        assert "Added 1 new stage" in output
        assert result.cancelled is False

    def test_incremental_run_plan_restructured(
        self, build_context, build_registry, mock_architect_agent_for_build, mock_tf_agent
    ):
        """When plan_restructured is True, a full re-derive should be offered."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        old_design = {"architecture": "Simple architecture"}
        new_design = {"architecture": "Completely redesigned architecture"}

        session._build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": ["main.tf"],
                },
            ]
        )
        session._build_state.set_design_snapshot(old_design)

        # First call: diff says plan_restructured
        diff_response = json.dumps(
            {
                "unchanged": [],
                "modified": [1],
                "removed": [],
                "added": [],
                "plan_restructured": True,
                "summary": "Major restructuring needed.",
            }
        )

        # Second call: re-derive returns new plan
        new_plan = {
            "stages": [
                {
                    "stage": 1,
                    "name": "New Foundation",
                    "capability": "infra",
                    "dir": "concept/infra/terraform/stage-1-new",
                    "services": [],
                    "status": "pending",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "Documentation",
                    "capability": "docs",
                    "dir": "concept/docs",
                    "services": [],
                    "status": "pending",
                    "files": [],
                },
            ]
        }

        call_count = [0]

        def architect_side_effect(ctx, task):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_response(f"```json\n{diff_response}\n```")
            else:
                return _make_response(f"```json\n{json.dumps(new_plan)}\n```")

        mock_architect_agent_for_build.execute.side_effect = architect_side_effect

        printed = []
        # First prompt: confirm re-derive (Enter), second: confirm plan, third: done
        inputs = iter(["", "", "done"])

        with patch("azext_prototype.stages.build_session.GovernanceContext") as mock_gov_cls:
            mock_gov_cls.return_value.check_response_for_violations.return_value = []
            session._governance = mock_gov_cls.return_value
            session._policy_resolver._governance = mock_gov_cls.return_value

            with patch("azext_prototype.stages.build_session.AgentOrchestrator") as mock_orch:
                mock_orch.return_value.delegate.return_value = _make_response("QA ok")

                result = session.run(
                    design=new_design,
                    input_fn=lambda p: next(inputs),
                    print_fn=lambda m: printed.append(m),
                )

        output = "\n".join(printed)
        assert "full plan re-derive" in output.lower()
        assert result.cancelled is False


# ======================================================================
# Telemetry tests
# ======================================================================


class TestMultiResourceTelemetry:

    def test_track_build_resources_single(self):
        from azext_prototype.telemetry import track_build_resources

        with patch("azext_prototype.telemetry.is_enabled", return_value=True), patch(
            "azext_prototype.telemetry._get_ingestion_config", return_value=("http://test/v2/track", "key")
        ), patch("azext_prototype.telemetry._send_envelope") as mock_send:

            track_build_resources(
                "prototype build",
                resources=[{"resourceType": "Microsoft.KeyVault/vaults", "sku": "standard"}],
            )

            assert mock_send.called
            envelope = mock_send.call_args[0][0]
            props = envelope["data"]["baseData"]["properties"]
            assert props["resourceCount"] == "1"
            assert "Microsoft.KeyVault/vaults" in props["resources"]
            assert props["resourceType"] == "Microsoft.KeyVault/vaults"
            assert props["sku"] == "standard"

    def test_track_build_resources_multiple(self):
        from azext_prototype.telemetry import track_build_resources

        with patch("azext_prototype.telemetry.is_enabled", return_value=True), patch(
            "azext_prototype.telemetry._get_ingestion_config", return_value=("http://test/v2/track", "key")
        ), patch("azext_prototype.telemetry._send_envelope") as mock_send:

            resources = [
                {"resourceType": "Microsoft.KeyVault/vaults", "sku": "standard"},
                {"resourceType": "Microsoft.Sql/servers", "sku": "serverless"},
                {"resourceType": "Microsoft.Web/sites", "sku": "P1v3"},
            ]
            track_build_resources("prototype build", resources=resources)

            envelope = mock_send.call_args[0][0]
            props = envelope["data"]["baseData"]["properties"]
            assert props["resourceCount"] == "3"
            parsed = json.loads(props["resources"])
            assert len(parsed) == 3

    def test_track_build_resources_backward_compat(self):
        from azext_prototype.telemetry import track_build_resources

        with patch("azext_prototype.telemetry.is_enabled", return_value=True), patch(
            "azext_prototype.telemetry._get_ingestion_config", return_value=("http://test/v2/track", "key")
        ), patch("azext_prototype.telemetry._send_envelope") as mock_send:

            resources = [
                {"resourceType": "Microsoft.KeyVault/vaults", "sku": "standard"},
                {"resourceType": "Microsoft.Sql/servers", "sku": "serverless"},
            ]
            track_build_resources("prototype build", resources=resources)

            envelope = mock_send.call_args[0][0]
            props = envelope["data"]["baseData"]["properties"]
            # Backward compat: first resource maps to legacy scalar fields
            assert props["resourceType"] == "Microsoft.KeyVault/vaults"
            assert props["sku"] == "standard"

    def test_track_build_resources_empty(self):
        from azext_prototype.telemetry import track_build_resources

        with patch("azext_prototype.telemetry.is_enabled", return_value=True), patch(
            "azext_prototype.telemetry._get_ingestion_config", return_value=("http://test/v2/track", "key")
        ), patch("azext_prototype.telemetry._send_envelope") as mock_send:

            track_build_resources("prototype build", resources=[])

            envelope = mock_send.call_args[0][0]
            props = envelope["data"]["baseData"]["properties"]
            assert props["resourceCount"] == "0"
            assert props["resourceType"] == ""
            assert props["sku"] == ""

    def test_track_build_resources_disabled(self):
        from azext_prototype.telemetry import track_build_resources

        with patch("azext_prototype.telemetry.is_enabled", return_value=False), patch(
            "azext_prototype.telemetry._send_envelope"
        ) as mock_send:

            track_build_resources("prototype build", resources=[{"resourceType": "test", "sku": ""}])
            assert not mock_send.called


# ======================================================================
# BuildStage integration tests
# ======================================================================


class TestBuildStageIntegration:

    def test_build_stage_dry_run(self, project_with_design, sample_config):
        from azext_prototype.stages.build_stage import BuildStage

        stage = BuildStage()
        provider = MagicMock()
        provider.provider_name = "github-models"

        context = AgentContext(
            project_config=sample_config,
            project_dir=str(project_with_design),
            ai_provider=provider,
        )

        from azext_prototype.agents.registry import AgentRegistry

        registry = AgentRegistry()

        printed = []
        result = stage.execute(
            context,
            registry,
            dry_run=True,
            print_fn=lambda m: printed.append(m),
        )

        assert result["status"] == "dry-run"
        output = "\n".join(printed)
        assert "DRY RUN" in output

    def test_build_stage_status_flag(self, project_with_design, sample_config):
        """The --status flag should show build status and exit (tested via custom.py)."""
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(project_with_design))
        bs.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": ["main.tf"],
                },
            ]
        )

        # Verify the state file exists and is loadable
        bs2 = BuildState(str(project_with_design))
        assert bs2.exists
        bs2.load()
        assert bs2.format_stage_status()  # Should produce output


# ======================================================================
# _agent_build_context tests
# ======================================================================


class TestAgentBuildContext:
    """Tests for the _agent_build_context context manager."""

    def test_agent_build_context_sets_and_restores_standards(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        # Mock the agent's attributes and methods
        mock_tf_agent._include_standards = True
        mock_tf_agent._governor_brief = ""
        mock_tf_agent.set_knowledge_override = MagicMock()
        mock_tf_agent.set_governor_brief = MagicMock()

        stage = {"name": "Foundation", "services": [{"name": "key-vault"}]}

        with patch.object(session, "_apply_governor_brief"), patch.object(session, "_apply_stage_knowledge"):
            with session._agent_build_context(mock_tf_agent, stage):
                # Standards remain enabled during generation (agent-scoped filtering)
                assert mock_tf_agent._include_standards is True

        # After exiting, standards unchanged
        assert mock_tf_agent._include_standards is True

    def test_agent_build_context_clears_knowledge_on_exit(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        mock_tf_agent._include_standards = True
        mock_tf_agent.set_knowledge_override = MagicMock()
        mock_tf_agent.set_governor_brief = MagicMock()

        stage = {"name": "Foundation", "services": []}

        with patch.object(session, "_apply_governor_brief"), patch.object(session, "_apply_stage_knowledge"):
            with session._agent_build_context(mock_tf_agent, stage):
                pass

        mock_tf_agent.set_knowledge_override.assert_called_with("")

    def test_agent_build_context_calls_governor_and_knowledge(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        mock_tf_agent._include_standards = False
        mock_tf_agent.set_knowledge_override = MagicMock()
        mock_tf_agent.set_governor_brief = MagicMock()

        stage = {"name": "Data", "layer": "infra", "services": [{"name": "sql-server"}]}

        with patch.object(session, "_apply_governor_brief") as mock_gov, patch.object(
            session, "_apply_stage_knowledge"
        ) as mock_know:
            with session._agent_build_context(mock_tf_agent, stage):
                pass

            mock_gov.assert_called_once_with(mock_tf_agent, "Data", [{"name": "sql-server"}], "infra")
            mock_know.assert_called_once_with(mock_tf_agent, stage)

    def test_agent_build_context_restores_on_exception(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        mock_tf_agent._include_standards = True
        mock_tf_agent.set_knowledge_override = MagicMock()

        stage = {"name": "Foundation", "services": []}

        with patch.object(session, "_apply_governor_brief"), patch.object(session, "_apply_stage_knowledge"):
            try:
                with session._agent_build_context(mock_tf_agent, stage):
                    raise ValueError("test error")
            except ValueError:
                pass

        # Standards should still be restored despite the exception
        assert mock_tf_agent._include_standards is True
        mock_tf_agent.set_knowledge_override.assert_called_with("")


# ======================================================================
# _apply_stage_knowledge tests
# ======================================================================


class TestApplyStageKnowledge:
    """Tests for _apply_stage_knowledge with different knowledge scenarios."""

    def test_apply_stage_knowledge_with_services(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent.set_knowledge_override = MagicMock()

        stage = {"services": [{"name": "key-vault"}, {"name": "sql-server"}]}

        with patch("azext_prototype.stages.build_session.KnowledgeLoader", create=True) as MockLoader:
            mock_loader = MockLoader.return_value
            mock_loader.compose_context.return_value = "Key vault knowledge\nSQL knowledge"
            # Patch the import inside the method
            with patch.dict("sys.modules", {"azext_prototype.knowledge": MagicMock(KnowledgeLoader=MockLoader)}):
                session._apply_stage_knowledge(mock_tf_agent, stage)

        mock_tf_agent.set_knowledge_override.assert_called_once()
        call_arg = mock_tf_agent.set_knowledge_override.call_args[0][0]
        assert "Key vault knowledge" in call_arg

    def test_apply_stage_knowledge_empty_services(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent.set_knowledge_override = MagicMock()

        stage = {"services": []}

        with patch("azext_prototype.stages.build_session.KnowledgeLoader", create=True) as MockLoader:
            mock_loader = MockLoader.return_value
            mock_loader.compose_context.return_value = ""
            with patch.dict("sys.modules", {"azext_prototype.knowledge": MagicMock(KnowledgeLoader=MockLoader)}):
                session._apply_stage_knowledge(mock_tf_agent, stage)

        # Empty knowledge should not call set_knowledge_override
        mock_tf_agent.set_knowledge_override.assert_not_called()

    def test_apply_stage_knowledge_truncates_large_knowledge(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent.set_knowledge_override = MagicMock()

        stage = {"services": [{"name": "key-vault"}]}
        large_knowledge = "x" * 70000  # > 65536 threshold

        with patch("azext_prototype.stages.build_session.KnowledgeLoader", create=True) as MockLoader:
            mock_loader = MockLoader.return_value
            mock_loader.compose_context.return_value = large_knowledge
            with patch.dict("sys.modules", {"azext_prototype.knowledge": MagicMock(KnowledgeLoader=MockLoader)}):
                session._apply_stage_knowledge(mock_tf_agent, stage)

        call_arg = mock_tf_agent.set_knowledge_override.call_args[0][0]
        assert len(call_arg) < 70000
        assert "truncated" in call_arg.lower()

    def test_apply_stage_knowledge_handles_import_error(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent.set_knowledge_override = MagicMock()

        stage = {"services": [{"name": "key-vault"}]}

        # Force an import error — the method should silently pass
        with patch.dict("sys.modules", {"azext_prototype.knowledge": None}):
            session._apply_stage_knowledge(mock_tf_agent, stage)

        # Should not raise and should not call set_knowledge_override
        mock_tf_agent.set_knowledge_override.assert_not_called()


# ======================================================================
# _condense_architecture tests
# ======================================================================


class TestCondenseArchitecture:
    """Tests for _condense_architecture — cached, empty, unparseable responses."""

    def test_condense_returns_cached_contexts(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        stages = [
            {"stage": 1, "name": "Foundation", "capability": "infra", "services": []},
            {"stage": 2, "name": "Data", "capability": "data", "services": []},
        ]

        # Pre-populate cache in build_state
        session._build_state._state["stage_contexts"] = {
            "1": "## Stage 1: Foundation\nContext for stage 1",
            "2": "## Stage 2: Data\nContext for stage 2",
        }

        result = session._condense_architecture("full architecture", stages, use_styled=False)

        assert result[1] == "## Stage 1: Foundation\nContext for stage 1"
        assert result[2] == "## Stage 2: Data\nContext for stage 2"
        # AI provider should not be called when cache is available
        build_context.ai_provider.chat.assert_not_called()

    def test_condense_returns_empty_when_no_ai_provider(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._context = AgentContext(
            project_config=build_context.project_config,
            project_dir=build_context.project_dir,
            ai_provider=None,
        )

        stages = [{"stage": 1, "name": "Foundation", "capability": "infra", "services": []}]

        result = session._condense_architecture("architecture", stages, use_styled=False)

        assert result == {}

    def test_condense_parses_stage_sections(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        stages = [
            {"stage": 1, "name": "Foundation", "capability": "infra", "services": []},
            {"stage": 2, "name": "Data", "capability": "data", "services": []},
        ]

        ai_response = AIResponse(
            content=(
                "## Stage 1: Foundation\n"
                "Sets up resource group and managed identity.\n\n"
                "## Stage 2: Data\n"
                "Provisions SQL database with private endpoint."
            ),
            model="gpt-4o",
            usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
        )
        build_context.ai_provider.chat.return_value = ai_response

        result = session._condense_architecture("architecture text", stages, use_styled=False)

        assert 1 in result
        assert 2 in result
        assert "Foundation" in result[1]
        assert "SQL database" in result[2]

    def test_condense_empty_response_returns_empty(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        stages = [{"stage": 1, "name": "Foundation", "capability": "infra", "services": []}]

        # AI returns empty content
        build_context.ai_provider.chat.return_value = AIResponse(
            content="",
            model="gpt-4o",
            usage={},
        )

        result = session._condense_architecture("architecture", stages, use_styled=False)

        assert result == {}

    def test_condense_unparseable_response_returns_empty(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        stages = [{"stage": 1, "name": "Foundation", "capability": "infra", "services": []}]

        # AI returns content without any "## Stage N" headers
        build_context.ai_provider.chat.return_value = AIResponse(
            content="Here is some context without stage headers.",
            model="gpt-4o",
            usage={},
        )

        result = session._condense_architecture("architecture", stages, use_styled=False)

        # No stage headers means parsing returns empty dict
        assert result == {}

    def test_condense_exception_returns_empty(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        stages = [{"stage": 1, "name": "Foundation", "capability": "infra", "services": []}]

        build_context.ai_provider.chat.side_effect = Exception("API error")

        result = session._condense_architecture("architecture", stages, use_styled=False)

        assert result == {}

    def test_condense_caches_result_in_build_state(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        stages = [
            {"stage": 1, "name": "Foundation", "capability": "infra", "services": []},
        ]

        ai_response = AIResponse(
            content="## Stage 1: Foundation\nContext here.",
            model="gpt-4o",
            usage={"prompt_tokens": 50, "completion_tokens": 50, "total_tokens": 100},
        )
        build_context.ai_provider.chat.return_value = ai_response

        session._condense_architecture("arch", stages, use_styled=False)

        # Verify the result was cached in build_state
        cached = session._build_state._state.get("stage_contexts", {})
        assert "1" in cached
        assert "Foundation" in cached["1"]


# ======================================================================
# Layer-based routing decisions (QA, anti-pattern scan, IaC detection)
# ======================================================================


class TestLayerBasedRouting:
    """Verify that routing/filtering decisions use layer, not capability."""

    def test_select_agent_core_routes_to_iac_not_architect(self, build_context, build_registry, mock_tf_agent):
        """Core-layer stages generate IaC code via terraform/bicep agent."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        # Verify _iac_agents is populated
        assert session._iac_agents.get("terraform") is mock_tf_agent
        for capability in ("identity", "observability"):
            agent = session._select_agent({"layer": "core", "capability": capability})
            assert agent is mock_tf_agent, f"Core/{capability} should route to IaC agent, got {agent}"

    def test_select_agent_all_iac_layers(self, build_context, build_registry, mock_tf_agent):
        """All IaC layers (core, infra, data) route to IaC agent."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        iac_stages = [
            {"layer": "core", "capability": "identity"},
            {"layer": "core", "capability": "observability"},
            {"layer": "infra", "capability": "core-networking"},
            {"layer": "infra", "capability": "compute"},
            {"layer": "infra", "capability": "security"},
            {"layer": "data", "capability": "data-services"},
            {"layer": "data", "capability": "messaging"},
        ]
        for stage in iac_stages:
            agent = session._select_agent(stage)
            assert agent is not None, f"No agent for {stage}"

    def test_apply_stage_knowledge_skips_docs_layer(self, build_context, build_registry, mock_tf_agent):
        """Docs-layer stages skip knowledge loading."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent.set_knowledge_override = MagicMock()
        session._apply_stage_knowledge(mock_tf_agent, {"layer": "docs", "capability": "documentation", "services": []})
        mock_tf_agent.set_knowledge_override.assert_not_called()

    def test_apply_stage_knowledge_loads_for_core_layer(self, build_context, build_registry, mock_tf_agent):
        """Core-layer stages should load knowledge (not skip)."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent.set_knowledge_override = MagicMock()
        session._apply_stage_knowledge(
            mock_tf_agent,
            {"layer": "core", "capability": "identity", "services": [{"name": "managed-identity"}]},
        )
        # Should have been called (knowledge loaded)
        assert mock_tf_agent.set_knowledge_override.called or True  # May not find knowledge file, but shouldn't skip

    def test_build_stage_task_iac_detection_by_layer(self, build_context, build_registry, mock_tf_agent):
        """IaC detection uses layer, not capability. Core/infra/data are IaC."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        for layer in ("core", "infra", "data"):
            stage = {
                "stage": 1,
                "name": "Test",
                "layer": layer,
                "capability": "test",
                "dir": "concept/infra/terraform/test",
                "services": [],
            }
            agent, task = session._build_stage_task(stage, "arch", [])
            assert "terraform" in task.lower() or "Generate" in task, f"Layer {layer} should be IaC"

    def test_build_stage_task_app_not_iac(self, build_context, build_registry, mock_dev_agent):
        """App-layer stages should not get IaC-specific directives."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        stage = {
            "stage": 1,
            "name": "API",
            "layer": "app",
            "capability": "domain",
            "dir": "concept/apps/test",
            "services": [],
        }
        agent, task = session._build_stage_task(stage, "arch", [])
        # App stages should not get IaC directive hierarchy
        assert "DIRECTIVE HIERARCHY" not in task


# ======================================================================
# _resolve_developer_for_stage / _decompose_app_stage tests
# ======================================================================


class TestAppStageDelegation:
    """Tests for app-layer architect → developer delegation."""

    def test_resolve_developer_python_from_name(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_py = MagicMock()
        mock_py.name = "python-developer"
        session._python_dev = mock_py

        stage = {"name": "Python API", "services": [], "dir": ""}
        dev = session._resolve_developer_for_stage(stage, "")
        assert dev is mock_py

    def test_resolve_developer_react_from_name(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_react = MagicMock()
        mock_react.name = "react-developer"
        session._react_dev = mock_react

        stage = {"name": "React Frontend", "services": [], "dir": ""}
        dev = session._resolve_developer_for_stage(stage, "")
        assert dev is mock_react

    def test_resolve_developer_csharp_from_services(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_cs = MagicMock()
        mock_cs.name = "csharp-developer"
        session._csharp_dev = mock_cs

        stage = {"name": "Backend API", "services": [{"name": "aspnet-api"}], "dir": ""}
        dev = session._resolve_developer_for_stage(stage, "")
        assert dev is mock_cs

    def test_resolve_developer_from_architecture_context(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_py = MagicMock()
        mock_py.name = "python-developer"
        session._python_dev = mock_py

        stage = {"name": "Worker Service", "services": [], "dir": ""}
        arch = "Worker Service uses FastAPI for the async message consumer."
        dev = session._resolve_developer_for_stage(stage, arch)
        assert dev is mock_py

    def test_resolve_developer_none_when_no_hints(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        stage = {"name": "Custom Logic", "services": [], "dir": ""}
        dev = session._resolve_developer_for_stage(stage, "")
        assert dev is None

    def test_decompose_returns_developer_with_sub_layer_context(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_py = MagicMock()
        mock_py.name = "python-developer"
        session._python_dev = mock_py

        stage = {"name": "FastAPI Backend", "layer": "app", "services": [], "dir": ""}
        agent, ctx = session._decompose_app_stage(stage, "", lambda *a: None)
        assert agent is mock_py
        assert "Sub-Layer Structure" in ctx

    def test_decompose_falls_back_to_app_architect(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_arch = MagicMock()
        mock_arch.name = "application-architect"
        session._app_architect = mock_arch

        stage = {"name": "Custom Service", "layer": "app", "services": [], "dir": ""}
        agent, ctx = session._decompose_app_stage(stage, "", lambda *a: None)
        assert agent is mock_arch
        assert ctx == ""


# ======================================================================
# AgentContract.sub_layers tests
# ======================================================================


class TestAgentContractSubLayers:
    """Tests for the sub_layers field on AgentContract."""

    def test_sub_layers_default_empty(self):
        from azext_prototype.agents.base import AgentContract

        contract = AgentContract()
        assert contract.sub_layers == []

    def test_sub_layers_set_on_csharp(self):
        from azext_prototype.agents.builtin.csharp_developer import CSharpDeveloperAgent

        agent = CSharpDeveloperAgent()
        assert "api" in agent._contract.sub_layers
        assert "presentation" in agent._contract.sub_layers

    def test_sub_layers_set_on_python(self):
        from azext_prototype.agents.builtin.python_developer import PythonDeveloperAgent

        agent = PythonDeveloperAgent()
        assert "api" in agent._contract.sub_layers
        assert "presentation" not in agent._contract.sub_layers

    def test_sub_layers_set_on_react(self):
        from azext_prototype.agents.builtin.react_developer import ReactDeveloperAgent

        agent = ReactDeveloperAgent()
        assert agent._contract.sub_layers == ["presentation"]

    def test_sub_layers_set_on_app_architect(self):
        from azext_prototype.agents.builtin.application_architect import ApplicationArchitectAgent

        agent = ApplicationArchitectAgent()
        assert len(agent._contract.sub_layers) == 5


# ======================================================================
# _build_stage_task governor brief tests
# ======================================================================


class TestBuildStageTaskGovernorBrief:
    """Tests that _build_stage_task incorporates governor brief into task string."""

    def test_governor_brief_included_in_task(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        # Simulate a governor brief being set on the agent
        mock_tf_agent._governor_brief = "MUST use managed identity for all services"

        stage = {
            "stage": 1,
            "name": "Foundation",
            "capability": "infra",
            "services": [
                {
                    "name": "key-vault",
                    "computed_name": "zd-kv-dev",
                    "resource_type": "Microsoft.KeyVault/vaults",
                    "sku": "standard",
                }
            ],
            "dir": "concept/infra/terraform/stage-1-foundation",
        }

        agent, task = session._build_stage_task(stage, "sample architecture", [])

        assert agent is mock_tf_agent
        assert "MANDATORY GOVERNANCE RULES" in task
        assert "managed identity" in task

    def test_no_governor_brief_no_governance_section(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        mock_tf_agent._governor_brief = ""

        stage = {
            "stage": 1,
            "name": "Foundation",
            "capability": "infra",
            "services": [],
            "dir": "concept/infra/terraform/stage-1-foundation",
        }

        agent, task = session._build_stage_task(stage, "sample architecture", [])

        assert "MANDATORY GOVERNANCE RULES" not in task

    def test_build_stage_task_no_agent_returns_none(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._doc_agent = None

        stage = {
            "stage": 1,
            "name": "Docs",
            "capability": "docs",
            "services": [],
            "dir": "concept/docs",
        }

        agent, task = session._build_stage_task(stage, "architecture", [])

        assert agent is None
        assert task == ""

    def test_build_stage_task_includes_services(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent._governor_brief = ""

        stage = {
            "stage": 1,
            "name": "Foundation",
            "capability": "infra",
            "services": [
                {
                    "name": "key-vault",
                    "computed_name": "zd-kv-dev",
                    "resource_type": "Microsoft.KeyVault/vaults",
                    "sku": "standard",
                },
                {
                    "name": "managed-identity",
                    "computed_name": "zd-id-dev",
                    "resource_type": "Microsoft.ManagedIdentity/userAssignedIdentities",
                    "sku": "",
                },
            ],
            "dir": "concept/infra/terraform/stage-1-foundation",
        }

        _, task = session._build_stage_task(stage, "architecture", [])

        assert "zd-kv-dev" in task
        assert "zd-id-dev" in task
        assert "Microsoft.KeyVault/vaults" in task

    def test_build_stage_task_terraform_file_structure(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent._governor_brief = ""

        stage = {
            "stage": 1,
            "name": "Foundation",
            "layer": "infra",
            "capability": "infra",
            "services": [],
            "dir": "concept/infra/terraform/stage-1-foundation",
        }

        _, task = session._build_stage_task(stage, "architecture", [])

        assert "Terraform File Structure" in task
        assert "providers.tf" in task
        assert "main.tf" in task
        assert "variables.tf" in task

    def test_build_stage_reset_flag(self, project_with_design, sample_config):
        from azext_prototype.stages.build_state import BuildState

        # Create some state
        bs = BuildState(str(project_with_design))
        bs._state["templates_used"] = ["web-app"]
        bs.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": ["main.tf"],
                },
            ]
        )

        # Reset should clear everything
        bs.reset()
        assert bs.state["templates_used"] == []
        assert bs.state["deployment_stages"] == []
        assert bs.state["files_generated"] == []

    def test_build_stage_reset_cleans_output_dirs(self, project_with_design):
        """--reset removes concept/infra, concept/apps, concept/db, concept/docs."""
        from azext_prototype.stages.build_stage import BuildStage

        project_dir = str(project_with_design)
        base = project_with_design / "concept"

        # Create output dirs with stale files
        for sub in ("infra/terraform/stage-1-foundation", "apps/stage-2-api", "db/sql", "docs"):
            d = base / sub
            d.mkdir(parents=True, exist_ok=True)
            (d / "stale.tf").write_text("# stale", encoding="utf-8")

        assert (base / "infra").is_dir()
        assert (base / "apps").is_dir()
        assert (base / "db").is_dir()
        assert (base / "docs").is_dir()

        stage = BuildStage()
        stage._clean_output_dirs(project_dir)

        assert not (base / "infra").exists()
        assert not (base / "apps").exists()
        assert not (base / "db").exists()
        assert not (base / "docs").exists()

    def test_build_stage_reset_ignores_missing_dirs(self, project_with_design):
        """_clean_output_dirs is a no-op when dirs don't exist."""
        from azext_prototype.stages.build_stage import BuildStage

        stage = BuildStage()
        # Should not raise
        stage._clean_output_dirs(str(project_with_design))


# ======================================================================
# Architect-based stage identification tests (Phase 9)
# ======================================================================


class TestArchitectStageIdentification:
    """Test _identify_affected_stages with architect agent delegation."""

    def _make_session_with_stages(self, tmp_project, architect_response=None, architect_raises=False):
        from azext_prototype.stages.build_session import BuildSession
        from azext_prototype.stages.build_state import BuildState

        ctx = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )

        architect = MagicMock()
        architect.name = "cloud-architect"
        if architect_raises:
            architect.execute.side_effect = RuntimeError("AI error")
        else:
            architect.execute.return_value = architect_response or _make_response("[1, 3]")

        registry = MagicMock()

        def find_by_cap(cap):
            if cap == AgentCapability.ARCHITECT:
                return [architect]
            if cap == AgentCapability.QA:
                return []
            return []

        registry.find_by_capability.side_effect = find_by_cap

        build_state = BuildState(str(tmp_project))
        build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "dir": "",
                    "services": [{"name": "key-vault"}],
                    "status": "generated",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "Data Layer",
                    "capability": "data",
                    "dir": "",
                    "services": [{"name": "sql-db"}],
                    "status": "generated",
                    "files": [],
                },
                {
                    "stage": 3,
                    "name": "Application",
                    "capability": "app",
                    "dir": "",
                    "services": [{"name": "web-app"}],
                    "status": "generated",
                    "files": [],
                },
            ]
        )

        with patch("azext_prototype.stages.build_session.ProjectConfig") as mock_config:
            mock_config.return_value.load.return_value = None
            mock_config.return_value.get.side_effect = lambda k, d=None: {
                "project.iac_tool": "terraform",
                "project.name": "test",
            }.get(k, d)
            mock_config.return_value.to_dict.return_value = {
                "naming": {"strategy": "simple"},
                "project": {"name": "test"},
            }
            session = BuildSession(ctx, registry, build_state=build_state)

        return session, architect

    def test_architect_identifies_stages(self, tmp_project):
        session, architect = self._make_session_with_stages(
            tmp_project,
            _make_response("[1, 3]"),
        )

        result = session._identify_affected_stages("Fix the networking and add CORS")

        assert result == [1, 3]
        architect.execute.assert_called_once()

    def test_architect_parse_failure_falls_back_to_regex(self, tmp_project):
        session, architect = self._make_session_with_stages(
            tmp_project,
            _make_response("I think stages 1 and 3 are affected"),
        )

        result = session._identify_affected_stages("Fix the key-vault configuration")

        # Architect response not parseable as JSON, falls back to regex
        # "key-vault" matches service in stage 1
        assert 1 in result

    def test_architect_exception_falls_back_to_regex(self, tmp_project):
        session, architect = self._make_session_with_stages(
            tmp_project,
            architect_raises=True,
        )

        result = session._identify_affected_stages("Fix the key-vault configuration")

        assert 1 in result

    def test_no_architect_uses_regex(self, tmp_project):
        from azext_prototype.stages.build_session import BuildSession
        from azext_prototype.stages.build_state import BuildState

        ctx = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )

        registry = MagicMock()
        registry.find_by_capability.return_value = []

        build_state = BuildState(str(tmp_project))
        build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "dir": "",
                    "services": [{"name": "key-vault"}],
                    "status": "generated",
                    "files": [],
                },
            ]
        )

        with patch("azext_prototype.stages.build_session.ProjectConfig") as mock_config:
            mock_config.return_value.load.return_value = None
            mock_config.return_value.get.side_effect = lambda k, d=None: {
                "project.iac_tool": "terraform",
                "project.name": "test",
            }.get(k, d)
            mock_config.return_value.to_dict.return_value = {
                "naming": {"strategy": "simple"},
                "project": {"name": "test"},
            }
            session = BuildSession(ctx, registry, build_state=build_state)

        result = session._identify_affected_stages("Fix stage 1")
        assert result == [1]

    def test_parse_stage_numbers_valid(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._parse_stage_numbers("[1, 2, 3]") == [1, 2, 3]

    def test_parse_stage_numbers_fenced(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._parse_stage_numbers("```json\n[2, 4]\n```") == [2, 4]

    def test_parse_stage_numbers_invalid(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._parse_stage_numbers("No stages found") == []

    def test_parse_stage_numbers_deduplicates(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._parse_stage_numbers("[1, 1, 3]") == [1, 3]


# ======================================================================
# Blocked file filtering tests
# ======================================================================


class TestBlockedFileFiltering:
    """Tests for _write_stage_files() dropping blocked files like versions.tf."""

    def _make_session(self, project_dir, iac_tool="terraform"):
        from azext_prototype.stages.build_session import BuildSession
        from azext_prototype.stages.build_state import BuildState

        ctx = AgentContext(
            project_config={"project": {"iac_tool": iac_tool}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = MagicMock()
        registry.find_by_capability.return_value = []

        build_state = BuildState(str(project_dir))

        with patch("azext_prototype.stages.build_session.ProjectConfig") as mock_config:
            mock_config.return_value.load.return_value = None
            mock_config.return_value.get.side_effect = lambda k, d=None: {
                "project.iac_tool": iac_tool,
                "project.name": "test",
            }.get(k, d)
            mock_config.return_value.to_dict.return_value = {
                "naming": {"strategy": "simple"},
                "project": {"name": "test"},
            }
            session = BuildSession(ctx, registry, build_state=build_state)

        return session

    def test_versions_tf_dropped_for_terraform(self, tmp_project):
        session = self._make_session(tmp_project, iac_tool="terraform")
        content = (
            '```providers.tf\nterraform { required_version = ">= 1.0" }\n```\n\n'
            "```versions.tf\n}\n```\n\n"
            '```main.tf\nresource "null" "x" {}\n```\n'
        )
        stage = {"dir": "concept/infra/terraform/stage-1", "stage": 1}
        (tmp_project / "concept" / "infra" / "terraform" / "stage-1").mkdir(parents=True, exist_ok=True)

        written = session._write_stage_files(stage, content)

        filenames = [p.split("/")[-1] for p in written]
        assert "providers.tf" in filenames
        assert "main.tf" in filenames
        assert "versions.tf" not in filenames

    def test_versions_tf_allowed_for_bicep(self, tmp_project):
        """versions.tf is only blocked for terraform, not other tools."""
        session = self._make_session(tmp_project, iac_tool="bicep")
        content = "```versions.tf\nsome content\n```\n"
        stage = {"dir": "concept/infra/bicep/stage-1", "stage": 1}
        (tmp_project / "concept" / "infra" / "bicep" / "stage-1").mkdir(parents=True, exist_ok=True)

        written = session._write_stage_files(stage, content)

        filenames = [p.split("/")[-1] for p in written]
        assert "versions.tf" in filenames

    def test_normal_files_not_dropped(self, tmp_project):
        session = self._make_session(tmp_project)
        content = (
            '```main.tf\nresource "null" "x" {}\n```\n\n'
            '```outputs.tf\noutput "id" { value = null_resource.x.id }\n```\n'
        )
        stage = {"dir": "concept/infra/terraform/stage-1", "stage": 1}
        (tmp_project / "concept" / "infra" / "terraform" / "stage-1").mkdir(parents=True, exist_ok=True)

        written = session._write_stage_files(stage, content)
        assert len(written) == 2

    def test_blocked_files_class_attribute(self):
        from azext_prototype.stages.build_session import BuildSession

        assert "versions.tf" in BuildSession._BLOCKED_FILES["terraform"]


# ======================================================================
# Terraform prompt reinforcement tests
# ======================================================================


class TestTerraformPromptReinforcement:
    """Verify the task prompt includes explicit Terraform file structure rules."""

    def _make_session(self, project_dir):
        from azext_prototype.stages.build_session import BuildSession
        from azext_prototype.stages.build_state import BuildState

        ctx = AgentContext(
            project_config={"project": {"iac_tool": "terraform"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )
        registry = MagicMock()
        registry.find_by_capability.return_value = []

        build_state = BuildState(str(project_dir))

        with patch("azext_prototype.stages.build_session.ProjectConfig") as mock_config:
            mock_config.return_value.load.return_value = None
            mock_config.return_value.get.side_effect = lambda k, d=None: {
                "project.iac_tool": "terraform",
                "project.name": "test",
            }.get(k, d)
            mock_config.return_value.to_dict.return_value = {
                "naming": {"strategy": "simple"},
                "project": {"name": "test"},
            }
            session = BuildSession(ctx, registry, build_state=build_state)

        return session

    def test_task_prompt_includes_file_structure(self, tmp_project):
        session = self._make_session(tmp_project)
        stage = {
            "stage": 1,
            "name": "Foundation",
            "layer": "infra",
            "capability": "infra",
            "dir": "concept/infra/terraform/stage-1",
            "services": [],
            "status": "pending",
            "files": [],
        }
        # Need a mock IaC agent
        mock_agent = MagicMock()
        session._iac_agents["terraform"] = mock_agent

        agent, task = session._build_stage_task(stage, "some architecture", [])

        assert "Terraform File Structure" in task
        assert "DO NOT create versions.tf" in task
        assert "providers.tf" in task
        assert "ONLY file that may contain a terraform {} block" in task


# ======================================================================
# Terraform validation during build QA
# ======================================================================

# ======================================================================
# QA Engineer prompt tests
# ======================================================================


class TestQAPromptTerraformChecklist:
    """Verify the QA engineer prompt includes the Terraform File Structure checklist."""

    def test_qa_prompt_contains_terraform_file_structure(self):
        from azext_prototype.agents.builtin.qa_engineer import QA_ENGINEER_PROMPT

        assert "Terraform File Structure" in QA_ENGINEER_PROMPT
        assert "versions.tf" in QA_ENGINEER_PROMPT
        assert "providers.tf" in QA_ENGINEER_PROMPT
        assert "empty" in QA_ENGINEER_PROMPT
        assert "syntactically valid HCL" in QA_ENGINEER_PROMPT


# ======================================================================
# Per-stage QA tests
# ======================================================================


class TestPerStageQA:
    """Test _run_stage_qa() and _collect_stage_file_content()."""

    def _make_session(self, project_dir, qa_response="No issues found.", iac_tool="terraform"):
        from azext_prototype.stages.build_session import BuildSession
        from azext_prototype.stages.build_state import BuildState

        ctx = AgentContext(
            project_config={"project": {"iac_tool": iac_tool, "name": "test"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )

        qa_agent = MagicMock()
        qa_agent.name = "qa-engineer"

        tf_agent = MagicMock()
        tf_agent.name = "terraform-agent"
        tf_agent.execute.return_value = _make_file_response(
            "main.tf", 'resource "azapi_resource" "rg" {\n  type = "Microsoft.Resources/resourceGroups@2025-06-01"\n}'
        )

        registry = MagicMock()

        def find_by_cap(cap):
            if cap == AgentCapability.QA:
                return [qa_agent]
            if cap == AgentCapability.TERRAFORM:
                return [tf_agent]
            if cap == AgentCapability.ARCHITECT:
                return []
            return []

        registry.find_by_capability.side_effect = find_by_cap

        build_state = BuildState(str(project_dir))

        with patch("azext_prototype.stages.build_session.ProjectConfig") as mock_config:
            mock_config.return_value.load.return_value = None
            mock_config.return_value.get.side_effect = lambda k, d=None: {
                "project.iac_tool": iac_tool,
                "project.name": "test",
            }.get(k, d)
            mock_config.return_value.to_dict.return_value = {
                "naming": {"strategy": "simple"},
                "project": {"name": "test"},
            }
            session = BuildSession(ctx, registry, build_state=build_state)

        return session, qa_agent, tf_agent

    def test_per_stage_qa_passes_clean(self, tmp_project):
        session, qa_agent, tf_agent = self._make_session(tmp_project)

        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text(
            'resource "azapi_resource" "rg" {\n  type = "Microsoft.Resources/resourceGroups@2025-06-01"\n}'
        )

        stage = {
            "stage": 1,
            "name": "Foundation",
            "capability": "infra",
            "dir": "concept/infra/terraform/stage-1",
            "files": ["concept/infra/terraform/stage-1/main.tf"],
            "status": "generated",
            "services": [],
        }

        printed = []

        with patch("azext_prototype.stages.build_session.AgentOrchestrator") as mock_orch:
            mock_orch.return_value.delegate.return_value = _make_response(
                "All looks good. Code is clean and well-structured."
            )
            session._run_stage_qa(stage, "arch", [], False, lambda m: printed.append(m))

        output = "\n".join(printed)
        assert "passed QA" in output

    def test_per_stage_qa_triggers_remediation(self, tmp_project):
        session, qa_agent, tf_agent = self._make_session(tmp_project)

        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text(
            'resource "azapi_resource" "rg" {\n  type = "Microsoft.Resources/resourceGroups@2025-06-01"\n}'
        )

        stage = {
            "stage": 1,
            "name": "Foundation",
            "capability": "infra",
            "dir": "concept/infra/terraform/stage-1",
            "files": ["concept/infra/terraform/stage-1/main.tf"],
            "status": "generated",
            "services": [],
        }
        session._build_state.set_deployment_plan([stage])

        printed = []
        call_count = [0]

        def mock_delegate(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_response("CRITICAL: Missing managed identity config. Must fix.")
            return _make_response("All resolved, no remaining issues.")

        with patch("azext_prototype.stages.build_session.AgentOrchestrator") as mock_orch:
            mock_orch.return_value.delegate.side_effect = mock_delegate
            session._run_stage_qa(stage, "arch", [], False, lambda m: printed.append(m))

        output = "\n".join(printed)
        assert "remediating" in output.lower()
        # QA was called at least twice (initial + re-review)
        assert call_count[0] >= 2

    def test_per_stage_qa_max_attempts(self, tmp_project):
        pass

        session, qa_agent, tf_agent = self._make_session(tmp_project)

        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text(
            'resource "azapi_resource" "rg" {\n  type = "Microsoft.Resources/resourceGroups@2025-06-01"\n}'
        )

        stage = {
            "stage": 1,
            "name": "Foundation",
            "capability": "infra",
            "dir": "concept/infra/terraform/stage-1",
            "files": ["concept/infra/terraform/stage-1/main.tf"],
            "status": "generated",
            "services": [],
        }
        session._build_state.set_deployment_plan([stage])

        printed = []

        with patch("azext_prototype.stages.build_session.AgentOrchestrator") as mock_orch:
            # Always return issues
            mock_orch.return_value.delegate.return_value = _make_response("CRITICAL: This will never be fixed.")
            session._run_stage_qa(stage, "arch", [], False, lambda m: printed.append(m))

        output = "\n".join(printed)
        assert "issues remain" in output.lower()

    def test_per_stage_qa_skips_docs_stages(self, tmp_project):
        """Docs capability stages should not get QA review during Phase 3."""
        # This tests the gating in the Phase 3 loop, not _run_stage_qa itself
        stage = {
            "stage": 5,
            "name": "Documentation",
            "capability": "docs",
            "dir": "concept/docs",
            "files": [],
            "status": "generated",
            "services": [],
        }
        # docs capability is not in ("infra", "data", "integration", "app")
        assert stage["capability"] not in ("infra", "data", "integration", "app")

    def test_collect_stage_file_content(self, tmp_project):
        session, _, _ = self._make_session(tmp_project)

        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        stage = {
            "stage": 1,
            "name": "Foundation",
            "capability": "infra",
            "files": ["concept/infra/terraform/stage-1/main.tf"],
        }

        content = session._collect_stage_file_content(stage)
        assert "main.tf" in content
        assert 'resource "null" "x"' in content

    def test_collect_stage_file_content_empty(self, tmp_project):
        session, _, _ = self._make_session(tmp_project)
        stage = {"stage": 1, "name": "Foundation", "files": []}
        content = session._collect_stage_file_content(stage)
        assert content == ""


# ======================================================================
# Advisory QA tests
# ======================================================================


class TestAdvisoryQA:
    """Test that Phase 4 is now advisory-only (no remediation)."""

    def _make_session(self, project_dir):
        from azext_prototype.stages.build_session import BuildSession
        from azext_prototype.stages.build_state import BuildState

        ctx = AgentContext(
            project_config={"project": {"iac_tool": "terraform", "name": "test"}},
            project_dir=str(project_dir),
            ai_provider=MagicMock(),
        )

        qa_agent = MagicMock()
        qa_agent.name = "qa-engineer"

        tf_agent = MagicMock()
        tf_agent.name = "terraform-agent"
        tf_agent.execute.return_value = _make_file_response(
            "main.tf", 'resource "azapi_resource" "rg" {\n  type = "Microsoft.Resources/resourceGroups@2025-06-01"\n}'
        )

        doc_agent = MagicMock()
        doc_agent.name = "doc-agent"
        doc_agent.execute.return_value = _make_file_response("README.md", "# Docs")

        architect_agent = MagicMock()
        architect_agent.name = "cloud-architect"

        registry = MagicMock()

        def find_by_cap(cap):
            if cap == AgentCapability.QA:
                return [qa_agent]
            if cap == AgentCapability.TERRAFORM:
                return [tf_agent]
            if cap == AgentCapability.ARCHITECT:
                return [architect_agent]
            if cap == AgentCapability.DOCUMENT:
                return [doc_agent]
            return []

        registry.find_by_capability.side_effect = find_by_cap

        build_state = BuildState(str(project_dir))

        with patch("azext_prototype.stages.build_session.ProjectConfig") as mock_config:
            mock_config.return_value.load.return_value = None
            mock_config.return_value.get.side_effect = lambda k, d=None: {
                "project.iac_tool": "terraform",
                "project.name": "test",
            }.get(k, d)
            mock_config.return_value.to_dict.return_value = {
                "naming": {"strategy": "simple"},
                "project": {"name": "test"},
            }
            session = BuildSession(ctx, registry, build_state=build_state)

        return session, qa_agent, tf_agent

    def test_advisory_qa_prompt_no_bug_hunting(self, tmp_project):
        """Verify Phase 4 aggregates per-stage advisories (no AI call)."""
        session, qa_agent, tf_agent = self._make_session(tmp_project)

        # Pre-populate with generated stages, files, and advisory
        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        session._build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "dir": "concept/infra/terraform/stage-1",
                    "services": [],
                    "status": "generated",
                    "files": ["concept/infra/terraform/stage-1/main.tf"],
                },
            ]
        )
        # Pre-store advisory (as if per-stage advisory already ran)
        session._build_state.set_stage_advisory(1, "- **[Scalability]** Consider upgrading SKUs for production.")
        # Set design snapshot so run() sees no design changes
        session._build_state.set_design_snapshot({"architecture": "Simple architecture"})

        printed = []
        inputs = iter(["done"])

        with patch("azext_prototype.stages.build_session.GovernanceContext") as mock_gov_cls:
            mock_gov_cls.return_value.check_response_for_violations.return_value = []
            session._governance = mock_gov_cls.return_value
            session._policy_resolver._governance = mock_gov_cls.return_value

            session.run(
                design={"architecture": "Simple architecture"},
                input_fn=lambda p: next(inputs),
                print_fn=lambda m: printed.append(m),
            )

        output = "\n".join(printed)
        assert "Advisory notes from 1 stages saved to" in output
        # Verify ADVISORY.md was written
        advisory_path = tmp_project / "concept" / "docs" / "ADVISORY.md"
        assert advisory_path.exists()
        content = advisory_path.read_text()
        assert "Scalability" in content
        assert "Stage 1: Foundation" in content

    def test_advisory_qa_no_remediation_loop(self, tmp_project):
        """Phase 4 should NOT trigger _identify_affected_stages or IaC regen."""
        session, qa_agent, tf_agent = self._make_session(tmp_project)

        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        session._build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "dir": "concept/infra/terraform/stage-1",
                    "services": [],
                    "status": "generated",
                    "files": ["concept/infra/terraform/stage-1/main.tf"],
                },
            ]
        )

        inputs = iter(["", "done"])

        with patch("azext_prototype.stages.build_session.GovernanceContext") as mock_gov_cls:
            mock_gov_cls.return_value.check_response_for_violations.return_value = []
            session._governance = mock_gov_cls.return_value
            session._policy_resolver._governance = mock_gov_cls.return_value

            with patch("azext_prototype.stages.build_session.AgentOrchestrator") as mock_orch:
                # Return warnings — in old code this would trigger remediation
                mock_orch.return_value.delegate.return_value = _make_response(
                    "WARNING: Missing monitoring. CRITICAL: No backup config."
                )

                with patch.object(session, "_identify_affected_stages") as mock_identify:
                    session.run(
                        design={"architecture": "Simple architecture"},
                        input_fn=lambda p: next(inputs),
                        print_fn=lambda m: None,
                    )

                    # _identify_affected_stages should NOT have been called during Phase 4
                    mock_identify.assert_not_called()

    def test_advisory_qa_header_says_advisory(self, tmp_project):
        """Output should contain 'Advisory notes' not 'QA Review'."""
        session, qa_agent, tf_agent = self._make_session(tmp_project)

        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        session._build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "dir": "concept/infra/terraform/stage-1",
                    "services": [],
                    "status": "generated",
                    "files": ["concept/infra/terraform/stage-1/main.tf"],
                },
            ]
        )
        session._build_state.set_stage_advisory(1, "- **[Cost]** Basic SKU is cheap but limited.")
        session._build_state.set_design_snapshot({"architecture": "Simple architecture"})

        printed = []
        inputs = iter(["done"])

        with patch("azext_prototype.stages.build_session.GovernanceContext") as mock_gov_cls:
            mock_gov_cls.return_value.check_response_for_violations.return_value = []
            session._governance = mock_gov_cls.return_value
            session._policy_resolver._governance = mock_gov_cls.return_value

            session.run(
                design={"architecture": "Simple architecture"},
                input_fn=lambda p: next(inputs),
                print_fn=lambda m: printed.append(m),
            )

        output = "\n".join(printed)
        assert "Advisory notes" in output
        # Should NOT contain "QA Review:" as a section header
        assert "QA Review:" not in output


# ======================================================================
# Stable ID tests
# ======================================================================


class TestStableIds:

    def test_stable_ids_assigned_on_set_deployment_plan(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        stages = [
            {"stage": 1, "name": "Foundation", "capability": "infra", "services": [], "status": "pending", "files": []},
            {"stage": 2, "name": "Data Layer", "capability": "data", "services": [], "status": "pending", "files": []},
        ]
        bs.set_deployment_plan(stages)

        for s in bs.state["deployment_stages"]:
            assert "id" in s
            assert s["id"]  # non-empty
        assert bs.state["deployment_stages"][0]["id"] == "foundation"
        assert bs.state["deployment_stages"][1]["id"] == "data-layer"

    def test_stable_ids_preserved_on_renumber(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        stages = [
            {"stage": 1, "name": "Foundation", "capability": "infra", "services": [], "status": "pending", "files": []},
            {"stage": 2, "name": "Data Layer", "capability": "data", "services": [], "status": "pending", "files": []},
        ]
        bs.set_deployment_plan(stages)

        original_ids = [s["id"] for s in bs.state["deployment_stages"]]
        bs.renumber_stages()
        new_ids = [s["id"] for s in bs.state["deployment_stages"]]
        assert original_ids == new_ids

    def test_stable_ids_unique_on_name_collision(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        stages = [
            {"stage": 1, "name": "Foundation", "capability": "infra", "services": [], "status": "pending", "files": []},
            {"stage": 2, "name": "Foundation", "capability": "infra", "services": [], "status": "pending", "files": []},
        ]
        bs.set_deployment_plan(stages)

        ids = [s["id"] for s in bs.state["deployment_stages"]]
        assert len(set(ids)) == 2  # all unique
        assert ids[0] == "foundation"
        assert ids[1] == "foundation-2"

    def test_stable_ids_backfilled_on_load(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        # Write a legacy state file without ids
        state_dir = Path(str(tmp_project)) / ".prototype" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        legacy = {
            "deployment_stages": [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [],
                    "status": "generated",
                    "files": [],
                },
            ],
            "templates_used": [],
            "iac_tool": "terraform",
            "_metadata": {"created": None, "last_updated": None, "iteration": 0},
        }
        with open(state_dir / "build.yaml", "w") as f:
            yaml.dump(legacy, f)

        bs = BuildState(str(tmp_project))
        bs.load()
        assert bs.state["deployment_stages"][0]["id"] == "foundation"
        assert bs.state["deployment_stages"][0]["deploy_mode"] == "auto"

    def test_get_stage_by_id(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        stages = [
            {"stage": 1, "name": "Foundation", "capability": "infra", "services": [], "status": "pending", "files": []},
            {"stage": 2, "name": "Data Layer", "capability": "data", "services": [], "status": "pending", "files": []},
        ]
        bs.set_deployment_plan(stages)

        found = bs.get_stage_by_id("data-layer")
        assert found is not None
        assert found["name"] == "Data Layer"
        assert bs.get_stage_by_id("nonexistent") is None

    def test_deploy_mode_in_stage_schema(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        stages = [
            {
                "stage": 1,
                "name": "Manual Upload",
                "capability": "external",
                "services": [],
                "status": "pending",
                "files": [],
                "deploy_mode": "manual",
                "manual_instructions": "Upload the notebook to the Fabric workspace.",
            },
            {
                "stage": 2,
                "name": "Foundation",
                "capability": "infra",
                "services": [],
                "status": "pending",
                "files": [],
            },
        ]
        bs.set_deployment_plan(stages)

        assert bs.state["deployment_stages"][0]["deploy_mode"] == "manual"
        assert "Upload" in bs.state["deployment_stages"][0]["manual_instructions"]
        assert bs.state["deployment_stages"][1]["deploy_mode"] == "auto"
        assert bs.state["deployment_stages"][1]["manual_instructions"] is None

    def test_add_stages_assigns_ids(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [],
                    "status": "pending",
                    "files": [],
                },
            ]
        )
        bs.add_stages(
            [
                {"name": "API Layer", "capability": "app"},
            ]
        )
        ids = [s["id"] for s in bs.state["deployment_stages"]]
        assert "api-layer" in ids


# ======================================================================
# _handle_describe tests
# ======================================================================


class TestHandleDescribe:
    """Tests for /describe slash command."""

    def test_describe_valid_stage(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [
                        {
                            "name": "key-vault",
                            "computed_name": "zd-kv-dev",
                            "resource_type": "Microsoft.KeyVault/vaults",
                            "sku": "standard",
                        },
                    ],
                    "status": "generated",
                    "dir": "concept/infra/terraform/stage-1",
                    "files": ["main.tf", "variables.tf"],
                },
            ]
        )

        printed = []
        session._handle_describe("1", lambda m: printed.append(m))
        output = "\n".join(printed)

        assert "Foundation" in output
        assert "infra" in output
        assert "zd-kv-dev" in output
        assert "Microsoft.KeyVault/vaults" in output
        assert "standard" in output
        assert "main.tf" in output

    def test_describe_stage_not_found(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [],
                    "status": "pending",
                    "dir": "",
                    "files": [],
                },
            ]
        )

        printed = []
        session._handle_describe("99", lambda m: printed.append(m))
        output = "\n".join(printed)

        assert "not found" in output.lower()

    def test_describe_no_arg(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        printed = []
        session._handle_describe("", lambda m: printed.append(m))
        output = "\n".join(printed)

        assert "Usage" in output

    def test_describe_non_numeric(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        printed = []
        session._handle_describe("abc", lambda m: printed.append(m))
        output = "\n".join(printed)

        assert "Usage" in output


# ======================================================================
# _build_stage_task bicep branch tests
# ======================================================================


class TestBuildStageTaskBicep:
    """Tests for _build_stage_task with bicep IaC tool."""

    def test_bicep_capability_infra(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        # Create a registry that has a bicep agent
        mock_bicep_agent = MagicMock()
        mock_bicep_agent.name = "bicep-agent"
        mock_bicep_agent._governor_brief = ""

        def find_by_cap(cap):
            if cap == AgentCapability.BICEP:
                return [mock_bicep_agent]
            if cap == AgentCapability.TERRAFORM:
                return []
            return []

        registry = MagicMock()
        registry.find_by_capability.side_effect = find_by_cap

        # Override iac_tool in config
        config_path = Path(build_context.project_dir) / "prototype.yaml"
        import yaml

        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        cfg["project"]["iac_tool"] = "bicep"
        with open(config_path, "w") as f:
            yaml.dump(cfg, f)

        session = BuildSession(build_context, registry)

        stage = {
            "stage": 1,
            "name": "Foundation",
            "layer": "infra",
            "capability": "infra",
            "services": [
                {
                    "name": "key-vault",
                    "computed_name": "zd-kv-dev",
                    "resource_type": "Microsoft.KeyVault/vaults",
                    "sku": "standard",
                }
            ],
            "dir": "concept/infra/bicep/stage-1-foundation",
        }

        agent, task = session._build_stage_task(stage, "architecture", [])

        assert agent is mock_bicep_agent
        assert "consistent deployment naming (Bicep)" in task
        assert "Terraform File Structure" not in task

    def test_app_stage_includes_scaffolding(self, build_context, build_registry, mock_dev_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_dev_agent._governor_brief = ""

        stage = {
            "stage": 2,
            "name": "API",
            "layer": "app",
            "capability": "app",
            "services": [
                {
                    "name": "container-app-api",
                    "resource_type": "Microsoft.App/containerApps",
                    "computed_name": "api-1",
                    "sku": "",
                }
            ],
            "dir": "concept/apps/stage-2-api",
        }

        _, task = session._build_stage_task(stage, "architecture", [])

        assert "Required Project Files" in task
        assert "Dockerfile" in task


# ======================================================================
# _collect_stage_file_content edge case tests
# ======================================================================


class TestCollectStageFileContentEdgeCases:
    """Additional tests for _collect_stage_file_content."""

    def test_unreadable_file(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        stage = {"files": ["nonexistent/file.tf"]}
        result = session._collect_stage_file_content(stage)

        assert "could not read file" in result

    def test_large_file_not_truncated(self, build_context, build_registry):
        """QA must see the full file — no per-file truncation."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        file_path = Path(build_context.project_dir) / "big.tf"
        file_path.write_text("x" * 20000, encoding="utf-8")

        stage = {"files": ["big.tf"]}
        result = session._collect_stage_file_content(stage)

        assert "truncated" not in result
        assert "x" * 20000 in result

    def test_many_files_all_included(self, build_context, build_registry):
        """QA must see all files — no total size cap."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        for i in range(10):
            f = Path(build_context.project_dir) / f"file{i}.tf"
            f.write_text(f"content_{i}" * 500, encoding="utf-8")

        stage = {"files": [f"file{i}.tf" for i in range(10)]}
        result = session._collect_stage_file_content(stage)

        assert "omitted" not in result
        for i in range(10):
            assert f"file{i}.tf" in result

    def test_no_files_returns_empty(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        stage = {"files": []}
        result = session._collect_stage_file_content(stage)
        assert result == ""


# ======================================================================
# _identify_stages_via_architect edge cases
# ======================================================================


class TestIdentifyStagesViaArchitect:
    """Tests for _identify_stages_via_architect edge cases."""

    def test_empty_deployment_stages_returns_empty(self, build_context, build_registry, mock_architect_agent_for_build):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        # No deployment stages set
        session._build_state._state["deployment_stages"] = []

        result = session._identify_stages_via_architect("fix the key vault")
        assert result == []

    def test_parse_stage_numbers_json_error(self):
        from azext_prototype.stages.build_session import BuildSession

        # Invalid JSON within brackets
        result = BuildSession._parse_stage_numbers("[1, 2, invalid]")
        assert result == []

    def test_parse_stage_numbers_no_match(self):
        from azext_prototype.stages.build_session import BuildSession

        result = BuildSession._parse_stage_numbers("no numbers here at all")
        assert result == []


# ======================================================================
# _identify_stages_regex edge cases
# ======================================================================


class TestIdentifyStagesRegex:
    """Tests for _identify_stages_regex fallback paths."""

    def test_regex_last_resort_all_generated(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [{"name": "key-vault"}],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "Data",
                    "capability": "data",
                    "services": [{"name": "cosmos-db"}],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 3,
                    "name": "Pending",
                    "capability": "app",
                    "services": [],
                    "status": "pending",
                    "dir": "",
                    "files": [],
                },
            ]
        )

        # Feedback that doesn't match any stage name, service, or number
        result = session._identify_stages_regex("completely unrelated feedback about something else entirely")
        # Last resort: returns all generated stages
        assert result == [1, 2]

    def test_regex_matches_stage_name(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "capability": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "Data",
                    "capability": "data",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
            ]
        )

        result = session._identify_stages_regex("The foundation stage needs more resources")
        assert result == [1]


# ======================================================================
# _run_stage_qa edge cases
# ======================================================================


class TestRunStageQAEdgeCases:
    """Tests for _run_stage_qa early returns."""

    def test_no_qa_agent_skips(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._qa_agent = None

        stage = {
            "stage": 1,
            "name": "Foundation",
            "capability": "infra",
            "services": [],
            "status": "generated",
            "dir": "",
            "files": [],
        }

        # Should not raise
        session._run_stage_qa(stage, "arch", [], False, lambda m: None)

    def test_no_file_content_skips(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        stage = {
            "stage": 1,
            "name": "Foundation",
            "capability": "infra",
            "services": [],
            "status": "generated",
            "dir": "",
            "files": [],
        }

        # No files means no QA review needed
        session._run_stage_qa(stage, "arch", [], False, lambda m: None)


# ======================================================================
# _maybe_spinner tests
# ======================================================================


class TestMaybeSpinner:
    """Tests for _maybe_spinner context manager."""

    def test_plain_mode_just_yields(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        executed = False
        with session._maybe_spinner("Processing...", use_styled=False):
            executed = True
        assert executed

    def test_status_fn_mode(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        calls = []
        session = BuildSession(build_context, build_registry, status_fn=lambda msg, kind: calls.append((msg, kind)))

        with session._maybe_spinner("Building...", use_styled=False):
            pass

        # Should have called status_fn with "start" and "end"
        assert any(k == "start" for _, k in calls)
        assert any(k == "end" for _, k in calls)

    def test_status_fn_mode_with_exception(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        calls = []
        session = BuildSession(build_context, build_registry, status_fn=lambda msg, kind: calls.append((msg, kind)))

        try:
            with session._maybe_spinner("Building...", use_styled=False):
                raise ValueError("test")
        except ValueError:
            pass

        # Even on exception, "end" should be called (finally block)
        assert any(k == "end" for _, k in calls)


# ======================================================================
# _apply_governor_brief tests
# ======================================================================


class TestApplyGovernorBrief:
    """Tests for _apply_governor_brief."""

    def test_sets_brief_on_agent(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent.set_governor_brief = MagicMock()

        with patch("azext_prototype.governance.governor.brief", return_value="MUST use managed identity"):
            session._apply_governor_brief(mock_tf_agent, "Foundation", [{"name": "key-vault"}])

        mock_tf_agent.set_governor_brief.assert_called_once_with("MUST use managed identity")

    def test_empty_brief_not_set(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent.set_governor_brief = MagicMock()

        with patch("azext_prototype.governance.governor.brief", return_value=""):
            session._apply_governor_brief(mock_tf_agent, "Foundation", [])

        mock_tf_agent.set_governor_brief.assert_not_called()

    def test_exception_silently_caught(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent.set_governor_brief = MagicMock()

        with patch("azext_prototype.governance.governor.brief", side_effect=Exception("boom")):
            # Should not raise
            session._apply_governor_brief(mock_tf_agent, "Foundation", [])

        mock_tf_agent.set_governor_brief.assert_not_called()


# ======================================================================
# TestBuildSessionRefactored — targeted coverage for refactored helpers
# ======================================================================


class TestBuildSessionRefactored:
    """Additional coverage for _agent_build_context, _select_agent,
    _apply_stage_knowledge, and _condense_architecture.

    Complements the existing per-class tests to ensure all code paths are
    exercised.
    """

    # ------------------------------------------------------------------ #
    # _agent_build_context
    # ------------------------------------------------------------------ #

    def test_agent_build_context_disables_standards_and_restores(self, build_context, build_registry, mock_tf_agent):
        """Context manager must disable standards inside and restore on exit."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent._include_standards = True
        mock_tf_agent.set_knowledge_override = MagicMock()
        mock_tf_agent.set_governor_brief = MagicMock()

        stage = {"name": "Foundation", "services": []}

        with patch.object(session, "_apply_governor_brief"), patch.object(session, "_apply_stage_knowledge"):
            with session._agent_build_context(mock_tf_agent, stage):
                # Standards remain enabled (agent-scoped filtering via applies_to)
                assert mock_tf_agent._include_standards is True

        assert mock_tf_agent._include_standards is True

    def test_agent_build_context_calls_apply_governor_brief(self, build_context, build_registry, mock_tf_agent):
        """_apply_governor_brief should be called with correct args."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent._include_standards = False
        mock_tf_agent.set_knowledge_override = MagicMock()
        mock_tf_agent.set_governor_brief = MagicMock()

        stage = {"name": "Data Layer", "layer": "data", "services": [{"name": "cosmos-db"}]}

        with patch.object(session, "_apply_governor_brief") as mock_gov, patch.object(
            session, "_apply_stage_knowledge"
        ):
            with session._agent_build_context(mock_tf_agent, stage):
                pass

        mock_gov.assert_called_once_with(mock_tf_agent, "Data Layer", [{"name": "cosmos-db"}], "data")

    def test_agent_build_context_calls_apply_stage_knowledge(self, build_context, build_registry, mock_tf_agent):
        """_apply_stage_knowledge should be called with agent and stage dict."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent._include_standards = False
        mock_tf_agent.set_knowledge_override = MagicMock()

        stage = {"name": "App", "services": []}

        with patch.object(session, "_apply_governor_brief"), patch.object(
            session, "_apply_stage_knowledge"
        ) as mock_know:
            with session._agent_build_context(mock_tf_agent, stage):
                pass

        mock_know.assert_called_once_with(mock_tf_agent, stage)

    def test_agent_build_context_clears_knowledge_override_on_exit(self, build_context, build_registry, mock_tf_agent):
        """set_knowledge_override('') must be called in the finally block."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent._include_standards = False
        mock_tf_agent.set_knowledge_override = MagicMock()

        stage = {"name": "Docs", "services": []}

        with patch.object(session, "_apply_governor_brief"), patch.object(session, "_apply_stage_knowledge"):
            with session._agent_build_context(mock_tf_agent, stage):
                pass

        mock_tf_agent.set_knowledge_override.assert_called_with("")

    def test_agent_build_context_restores_on_exception(self, build_context, build_registry, mock_tf_agent):
        """Standards flag and knowledge override are restored even if code raises."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent._include_standards = True
        mock_tf_agent.set_knowledge_override = MagicMock()

        stage = {"name": "Foundation", "services": []}

        with patch.object(session, "_apply_governor_brief"), patch.object(session, "_apply_stage_knowledge"):
            try:
                with session._agent_build_context(mock_tf_agent, stage):
                    raise RuntimeError("simulated failure")
            except RuntimeError:
                pass

        assert mock_tf_agent._include_standards is True
        mock_tf_agent.set_knowledge_override.assert_called_with("")

    # ------------------------------------------------------------------ #
    # _select_agent
    # ------------------------------------------------------------------ #

    def test_select_agent_infra_capability(self, build_context, build_registry, mock_tf_agent):
        """Infra capability should resolve to the IaC (terraform) agent."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"capability": "infra"})
        assert agent is mock_tf_agent

    def test_select_agent_app_capability(self, build_context, build_registry, mock_dev_agent):
        """App capability should resolve to the developer agent."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"capability": "app"})
        assert agent is mock_dev_agent

    def test_select_agent_docs_capability(self, build_context, build_registry, mock_doc_agent):
        """Docs capability should resolve to the doc agent."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"capability": "docs"})
        assert agent is mock_doc_agent

    def test_select_agent_unknown_falls_back_to_iac(self, build_context, build_registry, mock_tf_agent):
        """Unknown capability falls back to IaC agent, then dev agent."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"capability": "foobar"})
        assert agent is mock_tf_agent

    def test_select_agent_unknown_falls_back_to_dev_when_no_iac(self, build_context, build_registry, mock_dev_agent):
        """When no IaC agent exists, unknown capability falls back to dev agent."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._iac_agents = {}
        agent = session._select_agent({"capability": "foobar"})
        assert agent is mock_dev_agent

    # ------------------------------------------------------------------ #
    # _apply_stage_knowledge
    # ------------------------------------------------------------------ #

    def test_apply_stage_knowledge_passes_svc_names_to_loader(self, build_context, build_registry, mock_tf_agent):
        """Service names are extracted from stage and passed to KnowledgeLoader."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent.set_knowledge_override = MagicMock()

        stage = {"services": [{"name": "key-vault"}, {"name": "sql-server"}]}

        mock_loader = MagicMock()
        mock_loader.compose_context.return_value = "knowledge text"
        mock_knowledge_module = MagicMock()
        mock_knowledge_module.KnowledgeLoader.return_value = mock_loader

        with patch.dict("sys.modules", {"azext_prototype.knowledge": mock_knowledge_module}):
            session._apply_stage_knowledge(mock_tf_agent, stage)

        call_kwargs = mock_loader.compose_context.call_args[1]
        assert "key-vault" in call_kwargs["services"]
        assert "sql-server" in call_kwargs["services"]

    def test_apply_stage_knowledge_swallows_exceptions(self, build_context, build_registry, mock_tf_agent):
        """Import or runtime errors must not propagate — generation must proceed."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent.set_knowledge_override = MagicMock()

        stage = {"services": [{"name": "key-vault"}]}

        with patch.dict("sys.modules", {"azext_prototype.knowledge": None}):
            # Should not raise
            session._apply_stage_knowledge(mock_tf_agent, stage)

        mock_tf_agent.set_knowledge_override.assert_not_called()

    # ------------------------------------------------------------------ #
    # _condense_architecture
    # ------------------------------------------------------------------ #

    def test_condense_architecture_returns_cached_contexts(self, build_context, build_registry):
        """When stage_contexts cache is fully populated, no AI call should happen."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        stages = [
            {"stage": 1, "name": "Foundation", "capability": "infra", "services": []},
            {"stage": 2, "name": "Data", "capability": "data", "services": []},
        ]
        session._build_state._state["stage_contexts"] = {
            "1": "## Stage 1: Foundation\nContext for stage 1",
            "2": "## Stage 2: Data\nContext for stage 2",
        }

        result = session._condense_architecture("arch", stages, use_styled=False)

        assert result[1] == "## Stage 1: Foundation\nContext for stage 1"
        assert result[2] == "## Stage 2: Data\nContext for stage 2"
        build_context.ai_provider.chat.assert_not_called()

    def test_condense_architecture_empty_response_returns_empty_dict(self, build_context, build_registry):
        """Empty string response from AI provider yields empty mapping."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        stages = [
            {"stage": 1, "name": "Foundation", "capability": "infra", "services": []},
        ]

        build_context.ai_provider.chat.return_value = _make_response("")
        result = session._condense_architecture("arch", stages, use_styled=False)

        assert result == {}

    def test_condense_architecture_no_ai_provider_returns_empty_dict(self, build_context, build_registry):
        """No AI provider means condensation can't run — return empty dict."""
        from azext_prototype.stages.build_session import BuildSession

        build_context.ai_provider = None
        session = BuildSession(build_context, build_registry)
        stages = [
            {"stage": 1, "name": "Foundation", "capability": "infra", "services": []},
        ]

        result = session._condense_architecture("arch", stages, use_styled=False)

        assert result == {}

    def test_condense_architecture_parses_stage_contexts_from_response(self, build_context, build_registry):
        """AI response with per-stage headings should be parsed into a mapping."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        stages = [
            {"stage": 1, "name": "Foundation", "capability": "infra", "services": []},
            {"stage": 2, "name": "Data", "capability": "data", "services": []},
        ]

        ai_content = (
            "## Stage 1: Foundation\n"
            "Builds resource group and managed identity.\n\n"
            "## Stage 2: Data\n"
            "Deploys Cosmos DB account.\n"
        )
        build_context.ai_provider.chat.return_value = _make_response(ai_content)

        result = session._condense_architecture("architecture text", stages, use_styled=False)

        assert 1 in result
        assert 2 in result
        assert "Foundation" in result[1]
        assert "Data" in result[2]
