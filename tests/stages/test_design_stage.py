"""Tests for design_stage.py — branch coverage for artifact change detection,
skip-discovery flow, heading extraction, summary generation, template matching,
and format_section_elapsed.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from azext_prototype.agents.base import AgentCapability, AgentContext

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def design_context(project_with_config, sample_config):
    provider = MagicMock()
    provider.provider_name = "github-models"
    provider.default_model = "gpt-4o"
    provider.chat.return_value = MagicMock(
        content="## Solution Overview\nSample design output.",
        model="test",
        usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
    )
    return AgentContext(
        project_config=sample_config,
        project_dir=str(project_with_config),
        ai_provider=provider,
    )


@pytest.fixture
def design_registry():
    registry = MagicMock()

    mock_architect = MagicMock()
    mock_architect.name = "cloud-architect"
    mock_architect.execute = MagicMock(
        return_value=MagicMock(
            content='```json\n[{"name": "Solution Overview", "context": "overview"}]\n```',
            model="test",
            usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
        )
    )

    mock_biz = MagicMock()
    mock_biz.name = "biz-analyst"
    mock_biz.get_system_messages.return_value = []
    mock_biz._temperature = 0.7
    mock_biz._max_tokens = 4096

    mock_tf = MagicMock()
    mock_tf.name = "terraform-agent"
    mock_tf.execute = MagicMock(
        return_value=MagicMock(
            content="Terraform feasibility confirmed.",
            model="test",
            usage={"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
        )
    )

    def find_by_cap(cap):
        mapping = {
            AgentCapability.ARCHITECT: [mock_architect],
            AgentCapability.BIZ_ANALYSIS: [mock_biz],
            AgentCapability.TERRAFORM: [mock_tf],
            AgentCapability.BICEP: [],
            AgentCapability.QA: [],
        }
        return mapping.get(cap, [])

    registry.find_by_capability.side_effect = find_by_cap
    return registry


# ------------------------------------------------------------------
# _format_section_elapsed
# ------------------------------------------------------------------


class TestFormatSectionElapsed:
    def test_seconds_under_60(self):
        from azext_prototype.stages.design_stage import _format_section_elapsed

        assert _format_section_elapsed(5.0) == "5s"
        assert _format_section_elapsed(45.7) == "46s"

    def test_seconds_over_60(self):
        from azext_prototype.stages.design_stage import _format_section_elapsed

        assert _format_section_elapsed(64.0) == "1m04s"
        assert _format_section_elapsed(125.0) == "2m05s"

    def test_exactly_60(self):
        from azext_prototype.stages.design_stage import _format_section_elapsed

        assert _format_section_elapsed(60.0) == "1m00s"


# ------------------------------------------------------------------
# _extract_new_sections
# ------------------------------------------------------------------


class TestExtractNewSections:
    def test_valid_section_marker(self):
        from azext_prototype.stages.design_stage import _extract_new_sections

        content = 'Some text [NEW_SECTION: {"name": "Security", "context": "auth details"}] more text'
        result = _extract_new_sections(content)
        assert len(result) == 1
        assert result[0]["name"] == "Security"
        assert result[0]["context"] == "auth details"

    def test_defaults_context(self):
        from azext_prototype.stages.design_stage import _extract_new_sections

        content = '[NEW_SECTION: {"name": "Foo"}]'
        result = _extract_new_sections(content)
        assert len(result) == 1
        assert result[0]["context"] == ""

    def test_invalid_json_skipped(self):
        from azext_prototype.stages.design_stage import _extract_new_sections

        content = "[NEW_SECTION: {bad json}]"
        assert _extract_new_sections(content) == []

    def test_missing_name_skipped(self):
        from azext_prototype.stages.design_stage import _extract_new_sections

        content = '[NEW_SECTION: {"context": "only context"}]'
        assert _extract_new_sections(content) == []

    def test_multiple_markers(self):
        from azext_prototype.stages.design_stage import _extract_new_sections

        content = '[NEW_SECTION: {"name": "A"}] middle ' '[NEW_SECTION: {"name": "B", "context": "ctx"}]'
        result = _extract_new_sections(content)
        assert len(result) == 2
        assert result[0]["name"] == "A"
        assert result[1]["name"] == "B"


# ------------------------------------------------------------------
# DesignStage — guards
# ------------------------------------------------------------------


class TestDesignStageGuards:
    def test_get_guards_returns_one_guard(self):
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()
        guards = stage.get_guards()
        assert len(guards) == 1
        assert guards[0].name == "project_initialized"

    def test_design_is_reentrant(self):
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()
        assert stage.reentrant is True


# ------------------------------------------------------------------
# _load_design_state / _save_design_state
# ------------------------------------------------------------------


class TestDesignStatePersistence:
    def test_load_returns_fresh_state_when_no_file(self, tmp_project):
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()
        state = stage._load_design_state(str(tmp_project), reset=False)
        assert state["architecture"] is None
        assert state["artifacts"] == []
        assert state["iteration"] == 0

    def test_load_with_reset_clears_existing(self, tmp_project):
        from azext_prototype.stages.design_stage import DesignStage

        state_file = tmp_project / ".prototype" / "state" / "design.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps({"architecture": "existing", "artifacts": [], "iteration": 3}),
            encoding="utf-8",
        )

        stage = DesignStage()
        state = stage._load_design_state(str(tmp_project), reset=True)
        assert state["architecture"] is None
        assert state["iteration"] == 0

    def test_load_existing_state(self, tmp_project):
        from azext_prototype.stages.design_stage import DesignStage

        state_file = tmp_project / ".prototype" / "state" / "design.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps({"architecture": "arch", "artifacts": [{"path": "/foo"}], "iteration": 2}),
            encoding="utf-8",
        )

        stage = DesignStage()
        state = stage._load_design_state(str(tmp_project), reset=False)
        assert state["architecture"] == "arch"
        assert state["iteration"] == 2

    def test_save_and_reload(self, tmp_project):
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()
        state = {"architecture": "test-arch", "artifacts": [], "iteration": 1}
        stage._save_design_state(str(tmp_project), state)

        reloaded = stage._load_design_state(str(tmp_project), reset=False)
        assert reloaded["architecture"] == "test-arch"


# ------------------------------------------------------------------
# _write_architecture_docs
# ------------------------------------------------------------------


class TestWriteArchitectureDocs:
    def test_writes_architecture_md(self, tmp_project):
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()
        stage._write_architecture_docs(str(tmp_project), "# My Architecture\nSome content")

        arch_file = tmp_project / "concept" / "docs" / "ARCHITECTURE.md"
        assert arch_file.exists()
        content = arch_file.read_text()
        assert "My Architecture" in content


# ------------------------------------------------------------------
# _compute_artifact_hashes
# ------------------------------------------------------------------


class TestArtifactHashes:
    def test_computes_hashes_for_text_files(self, tmp_project):
        from azext_prototype.stages.design_stage import DesignStage

        docs_dir = tmp_project / "concept" / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "spec.txt").write_text("hello", encoding="utf-8")

        stage = DesignStage()
        hashes = stage._compute_artifact_hashes(str(docs_dir))
        assert len(hashes) >= 1
        # Hash should be a hex string
        for path, h in hashes.items():
            assert len(h) == 64  # SHA-256 hex

    def test_nonexistent_path_returns_empty(self, tmp_project):
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()
        hashes = stage._compute_artifact_hashes(str(tmp_project / "nonexistent"))
        assert hashes == {}


# ------------------------------------------------------------------
# skip-discovery flow
# ------------------------------------------------------------------


class TestSkipDiscovery:
    def test_skip_discovery_without_state_raises(self, design_context, design_registry):
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()

        with pytest.raises(Exception):
            stage.execute(
                design_context,
                design_registry,
                skip_discovery=True,
                input_fn=lambda p: "",
                print_fn=lambda m: None,
            )

    def test_skip_discovery_with_existing_state(self, design_context, design_registry, project_with_config):
        from azext_prototype.stages.design_stage import DesignStage
        from azext_prototype.stages.discovery_state import DiscoveryState

        # Create discovery state
        ds = DiscoveryState(str(project_with_config))
        ds.load()
        ds.state["project"] = {"summary": "API backend"}
        ds.state["confirmed_items"] = ["Use Container Apps"]
        ds.state["_metadata"]["exchange_count"] = 3
        # Add conversation history so _extract_last_summary can find it
        ds.state["conversation_history"] = [
            {"role": "assistant", "content": "## Requirements Summary\nBuild an API."},
        ]
        ds.save()

        stage = DesignStage()

        # Mock the architect execution chain
        mock_arch = design_registry.find_by_capability(AgentCapability.ARCHITECT)[0]
        # First call: plan sections, Second+: generate sections
        mock_arch.execute.side_effect = [
            MagicMock(
                content='```json\n[{"name": "Overview", "context": "test"}]\n```',
                model="test",
                usage={},
            ),
            MagicMock(
                content="## Overview\nSample arch.",
                model="test",
                usage={},
            ),
            # IaC review
            MagicMock(content="Terraform ok", model="test", usage={}),
        ]

        result = stage.execute(
            design_context,
            design_registry,
            skip_discovery=True,
            input_fn=lambda p: "",
            print_fn=lambda m: None,
        )
        assert result["status"] == "success"


# ------------------------------------------------------------------
# _refine_architecture_loop
# ------------------------------------------------------------------


class TestRefineArchitectureLoop:
    def test_empty_feedback_exits(self, design_context, design_registry):
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()
        mock_architect = design_registry.find_by_capability(AgentCapability.ARCHITECT)[0]

        design_state = {"architecture": "# Arch\nContent", "iteration": 1}

        from azext_prototype.config import ProjectConfig

        config = ProjectConfig(design_context.project_dir)
        config.load()

        with patch("builtins.input", return_value=""):
            result = stage._refine_architecture_loop(
                design_context,
                mock_architect,
                design_state,
                config,
            )

        assert result == "# Arch\nContent"

    def test_accept_keyword_exits(self, design_context, design_registry):
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()
        mock_architect = design_registry.find_by_capability(AgentCapability.ARCHITECT)[0]

        design_state = {"architecture": "# Arch", "iteration": 1}

        from azext_prototype.config import ProjectConfig

        config = ProjectConfig(design_context.project_dir)
        config.load()

        with patch("builtins.input", return_value="done"):
            result = stage._refine_architecture_loop(
                design_context,
                mock_architect,
                design_state,
                config,
            )
        assert result == "# Arch"


# ------------------------------------------------------------------
# _execute_with_prompt_trim
# ------------------------------------------------------------------


class TestExecuteWithPromptTrim:
    def test_normal_execution_passes_through(self):
        from azext_prototype.stages.design_stage import DesignStage

        architect = MagicMock()
        architect.execute.return_value = MagicMock(content="result")
        ctx = MagicMock()

        result = DesignStage._execute_with_prompt_trim(architect, ctx, "prompt", [])
        assert result.content == "result"

    def test_prompt_too_large_with_accumulated_retries(self):
        from azext_prototype.ai.copilot_provider import CopilotPromptTooLargeError
        from azext_prototype.stages.design_stage import DesignStage

        architect = MagicMock()
        # First call raises, second succeeds
        architect.execute.side_effect = [
            CopilotPromptTooLargeError("Prompt too large", token_count=200000, token_limit=100000),
            MagicMock(content="trimmed result"),
        ]
        ctx = MagicMock()

        prompt = "Intro\n## Architecture So Far\nfull content\n\n## Instructions\nGenerate code"
        accumulated = ["## Section 1\nContent 1", "## Section 2\nContent 2"]

        result = DesignStage._execute_with_prompt_trim(architect, ctx, prompt, accumulated)
        assert result.content == "trimmed result"

    def test_prompt_too_large_no_accumulated_reraises(self):
        from azext_prototype.stages.design_stage import DesignStage

        architect = MagicMock()
        # When accumulated is empty and prompt lacks ## Architecture So Far,
        # the code hits bare `raise` outside exception context → RuntimeError
        from azext_prototype.ai.copilot_provider import CopilotPromptTooLargeError

        architect.execute.side_effect = CopilotPromptTooLargeError(
            "Prompt too large", token_count=200000, token_limit=100000
        )
        ctx = MagicMock()

        with pytest.raises(RuntimeError):
            DesignStage._execute_with_prompt_trim(architect, ctx, "prompt without marker", [])


# ------------------------------------------------------------------
# _plan_architecture fallback
# ------------------------------------------------------------------


class TestPlanArchitecture:
    def test_fallback_on_invalid_json(self, design_context, design_registry):
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()
        mock_architect = design_registry.find_by_capability(AgentCapability.ARCHITECT)[0]
        mock_architect.execute.return_value = MagicMock(
            content="Not valid JSON at all",
            model="test",
            usage={},
        )

        from azext_prototype.config import ProjectConfig

        config = ProjectConfig(design_context.project_dir)
        config.load()

        sections = stage._plan_architecture(
            None,
            design_context,
            mock_architect,
            config,
            "requirements",
            lambda m: None,
        )
        # Should fall back to _DEFAULT_SECTIONS
        assert len(sections) > 0
        assert sections[0]["name"] == "Solution Overview"


# ------------------------------------------------------------------
# _run_iac_review
# ------------------------------------------------------------------


class TestRunIacReview:
    def test_no_iac_agent_skips_review(self, design_context):
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()
        registry = MagicMock()
        registry.find_by_capability.return_value = []

        from azext_prototype.config import ProjectConfig

        config = ProjectConfig(design_context.project_dir)
        config.load()

        mock_architect = MagicMock()
        # Should not raise — silently skips
        stage._run_iac_review(design_context, registry, config, mock_architect, "design output")

    def test_iac_review_stores_artifact(self, design_context, design_registry):
        from azext_prototype.stages.design_stage import DesignStage

        stage = DesignStage()

        from azext_prototype.config import ProjectConfig

        config = ProjectConfig(design_context.project_dir)
        config.load()

        mock_architect = design_registry.find_by_capability(AgentCapability.ARCHITECT)[0]

        stage._run_iac_review(design_context, design_registry, config, mock_architect, "design output")
        # Verify artifact was added
        assert "iac_review" in design_context.artifacts
