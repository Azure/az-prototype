"""Tests for azext_prototype.custom — CLI command implementations."""

import json
import os

import pytest
from unittest.mock import MagicMock, patch

from knack.util import CLIError


# All command functions call _get_project_dir() internally (uses Path.cwd()),
# so we mock it to point at our tmp fixture directories.

_CUSTOM_MODULE = "azext_prototype.custom"


class TestGetProjectDir:
    """Test the _get_project_dir helper."""

    def test_returns_resolved_cwd(self):
        from azext_prototype.custom import _get_project_dir

        result = _get_project_dir()
        assert os.path.isabs(result)


class TestLoadConfig:
    """Test the _load_config helper."""

    def test_loads_existing_config(self, project_with_config):
        from azext_prototype.custom import _load_config

        config = _load_config(str(project_with_config))
        assert config.get("project.name") == "test-project"

    def test_missing_config_raises(self, tmp_project):
        from azext_prototype.custom import _load_config

        with pytest.raises(CLIError):
            _load_config(str(tmp_project))


class TestPrototypeStatus:
    """Test az prototype status command."""

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_status_with_config(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_status

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        result = prototype_status(cmd, json_output=True)
        assert isinstance(result, dict)
        assert "project" in result
        assert "environment" in result
        assert "naming_strategy" in result
        assert "project_id" in result
        assert "stages" in result
        assert "design" in result["stages"]
        assert "build" in result["stages"]
        assert "deploy" in result["stages"]

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_status_without_config(self, mock_dir, tmp_project):
        from azext_prototype.custom import prototype_status

        mock_dir.return_value = str(tmp_project)
        cmd = MagicMock()

        result = prototype_status(cmd, json_output=True)
        assert isinstance(result, dict)
        assert result.get("status") == "not_initialized"


class TestPrototypeConfigShow:
    """Test az prototype config show command."""

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_config_show(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_config_show

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        result = prototype_config_show(cmd, json_output=True)
        assert result["project"]["name"] == "test-project"


class TestPrototypeConfigSet:
    """Test az prototype config set command."""

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_config_set(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_config_set

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        result = prototype_config_set(cmd, key="project.location", value="westus2", json_output=True)
        assert result is not None
        assert result["status"] == "updated"

    def test_config_set_missing_key_raises(self):
        from azext_prototype.custom import prototype_config_set

        cmd = MagicMock()
        with pytest.raises(CLIError, match="--key"):
            prototype_config_set(cmd, key=None, value="test")


class TestPrototypeAgentList:
    """Test az prototype agent list command."""

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_agent_list(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_list

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        result = prototype_agent_list(cmd, json_output=True)
        assert isinstance(result, list)
        assert len(result) >= 8  # 8 built-in agents

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_agent_list_no_builtin(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_list

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        result = prototype_agent_list(cmd, show_builtin=False, json_output=True)
        # With no custom agents, should return empty
        assert isinstance(result, list)


class TestPrototypeAgentShow:
    """Test az prototype agent show command."""

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_agent_show_builtin(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_show

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        result = prototype_agent_show(cmd, name="cloud-architect", json_output=True)
        assert result is not None
        assert "cloud-architect" in str(result)

    def test_agent_show_missing_name_raises(self):
        from azext_prototype.custom import prototype_agent_show

        cmd = MagicMock()
        with pytest.raises(CLIError, match="--name"):
            prototype_agent_show(cmd, name=None)


class TestPrototypeAgentAdd:
    """Test az prototype agent add command — all three modes."""

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_add_default_template(self, mock_dir, project_with_config):
        """Mode 1: --name only with interactive input → creates agent from prompts."""
        from azext_prototype.custom import prototype_agent_add

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        # Interactive mode: description, role, capabilities, constraints(end), prompt, END, examples(skip)
        inputs = ["My data agent", "analyst", "analyze", "", "You analyze data.", "END", ""]
        with patch("builtins.input", side_effect=inputs):
            result = prototype_agent_add(cmd, name="my-data-agent", json_output=True)
        assert result["status"] == "added"
        assert result["name"] == "my-data-agent"
        assert "my-data-agent.yaml" in result["file"]

        # Verify the file was created
        agent_file = project_with_config / ".prototype" / "agents" / "my-data-agent.yaml"
        assert agent_file.exists()

        import yaml as _yaml
        content = _yaml.safe_load(agent_file.read_text(encoding="utf-8"))
        assert content["name"] == "my-data-agent"

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_add_from_builtin_definition(self, mock_dir, project_with_config):
        """Mode 2: --name + --definition → copies named builtin definition."""
        from azext_prototype.custom import prototype_agent_add

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        result = prototype_agent_add(cmd, name="my-architect", definition="cloud_architect", json_output=True)
        assert result["status"] == "added"
        assert result["name"] == "my-architect"
        assert result["based_on"] == "cloud_architect"

        agent_file = project_with_config / ".prototype" / "agents" / "my-architect.yaml"
        assert agent_file.exists()

        import yaml as _yaml

        content = _yaml.safe_load(agent_file.read_text(encoding="utf-8"))
        assert content["name"] == "my-architect"

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_add_from_user_file(self, mock_dir, project_with_config):
        """Mode 3: --name + --file → copies user-supplied file."""
        from azext_prototype.custom import prototype_agent_add

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        # Create a custom agent YAML in a temp location
        custom_yaml = project_with_config / "tmp-agent.yaml"
        custom_yaml.write_text(
            "name: tmp\ndescription: temp agent\nrole: architect\n"
            "capabilities:\n  - architect\nsystem_prompt: You are a test agent.\n",
            encoding="utf-8",
        )

        result = prototype_agent_add(cmd, name="my-custom", file=str(custom_yaml), json_output=True)
        assert result["status"] == "added"
        assert result["name"] == "my-custom"

        agent_file = project_with_config / ".prototype" / "agents" / "my-custom.yaml"
        assert agent_file.exists()

    def test_add_missing_name_raises(self):
        from azext_prototype.custom import prototype_agent_add

        cmd = MagicMock()
        with pytest.raises(CLIError, match="--name"):
            prototype_agent_add(cmd, name=None)

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_add_file_and_definition_mutually_exclusive(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_add

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        with pytest.raises(CLIError, match="mutually exclusive"):
            prototype_agent_add(cmd, name="x", file="./a.yaml", definition="cloud_architect")

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_add_unknown_definition_raises(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_add

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        with pytest.raises(CLIError, match="Unknown definition"):
            prototype_agent_add(cmd, name="x", definition="nonexistent_agent")

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_add_duplicate_name_raises(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_add

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        prototype_agent_add(cmd, name="dup-agent", definition="cloud_architect")
        with pytest.raises(CLIError, match="already exists"):
            prototype_agent_add(cmd, name="dup-agent", definition="cloud_architect")

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_add_records_config_manifest(self, mock_dir, project_with_config):
        """Verify the agent is recorded in prototype.yaml."""
        from azext_prototype.custom import prototype_agent_add, _load_config

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        prototype_agent_add(cmd, name="manifest-test", definition="bicep_agent")

        config = _load_config(str(project_with_config))
        custom = config.get("agents.custom", {})
        assert "manifest-test" in custom
        assert custom["manifest-test"]["based_on"] == "bicep_agent"
        assert "file" in custom["manifest-test"]
        assert "capabilities" in custom["manifest-test"]

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_add_file_not_found_raises(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_agent_add

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        with pytest.raises(CLIError, match="File not found"):
            prototype_agent_add(cmd, name="x", file="./does_not_exist.yaml")


class TestResolveDefinition:
    """Test the _resolve_definition helper."""

    def test_resolves_known_definition(self):
        from pathlib import Path
        from azext_prototype.custom import _resolve_definition

        defs_dir = Path(__file__).parent.parent / "azext_prototype" / "agents" / "builtin" / "definitions"
        result = _resolve_definition(defs_dir, "cloud_architect")
        assert result.exists()
        assert "cloud_architect" in result.name

    def test_resolves_with_extension(self):
        from pathlib import Path
        from azext_prototype.custom import _resolve_definition

        defs_dir = Path(__file__).parent.parent / "azext_prototype" / "agents" / "builtin" / "definitions"
        result = _resolve_definition(defs_dir, "cloud_architect.yaml")
        assert result.exists()

    def test_unknown_definition_raises(self):
        from pathlib import Path
        from azext_prototype.custom import _resolve_definition

        defs_dir = Path(__file__).parent.parent / "azext_prototype" / "agents" / "builtin" / "definitions"
        with pytest.raises(CLIError, match="Unknown definition"):
            _resolve_definition(defs_dir, "nonexistent")


class TestCopyYamlWithName:
    """Test the _copy_yaml_with_name helper."""

    def test_rewrites_name_field(self, tmp_path):
        from azext_prototype.custom import _copy_yaml_with_name

        source = tmp_path / "source.yaml"
        source.write_text("name: original\ndescription: test\n", encoding="utf-8")
        dest = tmp_path / "dest.yaml"

        _copy_yaml_with_name(source, dest, "new-name")

        import yaml as _yaml

        content = _yaml.safe_load(dest.read_text(encoding="utf-8"))
        assert content["name"] == "new-name"


class TestPrototypeGenerateDocs:
    """Test az prototype generate docs command."""

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_generate_docs_creates_files(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_generate_docs

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        out_dir = str(project_with_config / "docs")
        result = prototype_generate_docs(cmd, path=out_dir, json_output=True)
        assert result is not None
        assert result["status"] == "generated"

        docs_path = project_with_config / "docs"
        assert docs_path.is_dir()
        md_files = list(docs_path.glob("*.md"))
        assert len(md_files) >= 1

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_generate_docs_default_output(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_generate_docs

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        result = prototype_generate_docs(cmd, json_output=True)
        assert result is not None
        assert result["status"] == "generated"


class TestPrototypeGenerateSpeckit:
    """Test az prototype generate speckit command."""

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_generate_speckit_creates_files(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_generate_speckit

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        out_dir = str(project_with_config / "concept" / ".specify")
        result = prototype_generate_speckit(cmd, path=out_dir, json_output=True)
        assert result is not None
        assert result["status"] == "generated"

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_generate_speckit_manifest(self, mock_dir, project_with_config):
        from azext_prototype.custom import prototype_generate_speckit

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        out_dir = str(project_with_config / "concept" / ".specify")
        prototype_generate_speckit(cmd, path=out_dir)

        speckit_path = project_with_config / "concept" / ".specify"
        assert speckit_path.is_dir()
        manifest_path = speckit_path / "manifest.json"
        assert manifest_path.exists()

        with open(manifest_path) as f:
            manifest = json.load(f)
        assert "templates" in manifest
        assert "project" in manifest


class TestPrototypeGenerateBacklog:
    """Test az prototype generate backlog command."""

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_generate_backlog_github(self, mock_dir, project_with_design, mock_ai_provider):
        """Backlog session runs and returns result for github provider."""
        from azext_prototype.custom import prototype_generate_backlog
        from azext_prototype.stages.backlog_session import BacklogResult

        mock_dir.return_value = str(project_with_design)
        cmd = MagicMock()

        mock_result = BacklogResult(items_generated=3, items_pushed=0)

        with patch(f"{_CUSTOM_MODULE}._build_context") as mock_ctx, \
             patch("azext_prototype.stages.backlog_session.BacklogSession") as MockSession:
            from azext_prototype.agents.base import AgentContext

            ctx = AgentContext(
                project_config={"project": {"name": "test"}},
                project_dir=str(project_with_design),
                ai_provider=mock_ai_provider,
            )
            mock_ctx.return_value = ctx
            MockSession.return_value.run.return_value = mock_result

            result = prototype_generate_backlog(cmd, provider="github", org="myorg", project="myrepo", json_output=True)

        assert result["status"] == "generated"
        assert result["provider"] == "github"
        assert result["items_generated"] == 3

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_generate_backlog_devops(self, mock_dir, project_with_design, mock_ai_provider):
        """Backlog session runs for devops provider."""
        from azext_prototype.custom import prototype_generate_backlog
        from azext_prototype.stages.backlog_session import BacklogResult

        mock_dir.return_value = str(project_with_design)
        cmd = MagicMock()

        mock_result = BacklogResult(items_generated=2, items_pushed=0)

        with patch(f"{_CUSTOM_MODULE}._build_context") as mock_ctx, \
             patch("azext_prototype.stages.backlog_session.BacklogSession") as MockSession:
            from azext_prototype.agents.base import AgentContext

            ctx = AgentContext(
                project_config={"project": {"name": "test"}},
                project_dir=str(project_with_design),
                ai_provider=mock_ai_provider,
            )
            mock_ctx.return_value = ctx
            MockSession.return_value.run.return_value = mock_result

            result = prototype_generate_backlog(cmd, provider="devops", org="myorg", project="myproj", json_output=True)

        assert result["status"] == "generated"
        assert result["provider"] == "devops"

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_generate_backlog_invalid_provider_raises(self, mock_dir, project_with_design, mock_ai_provider):
        from azext_prototype.custom import prototype_generate_backlog

        mock_dir.return_value = str(project_with_design)
        cmd = MagicMock()

        with patch(f"{_CUSTOM_MODULE}._build_context") as mock_ctx:
            from azext_prototype.agents.base import AgentContext

            ctx = AgentContext(
                project_config={"project": {"name": "test"}},
                project_dir=str(project_with_design),
                ai_provider=mock_ai_provider,
            )
            mock_ctx.return_value = ctx

            with pytest.raises(CLIError, match="Unsupported backlog provider"):
                prototype_generate_backlog(cmd, provider="jira", org="x", project="y")

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_generate_backlog_no_design_raises(self, mock_dir, project_with_config, mock_ai_provider):
        from azext_prototype.custom import prototype_generate_backlog

        mock_dir.return_value = str(project_with_config)
        cmd = MagicMock()

        with patch(f"{_CUSTOM_MODULE}._build_context") as mock_ctx:
            from azext_prototype.agents.base import AgentContext

            ctx = AgentContext(
                project_config={"project": {"name": "test"}},
                project_dir=str(project_with_config),
                ai_provider=mock_ai_provider,
            )
            mock_ctx.return_value = ctx

            with pytest.raises(CLIError, match="No architecture design found"):
                prototype_generate_backlog(cmd, provider="github", org="x", project="y")

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_generate_backlog_defaults_from_config(self, mock_dir, project_with_design, mock_ai_provider):
        """Backlog provider/org/project fall back to prototype.yaml values."""
        from azext_prototype.custom import prototype_generate_backlog
        from azext_prototype.stages.backlog_session import BacklogResult
        import yaml as _yaml

        # Update config with backlog section
        config_path = project_with_design / "prototype.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = _yaml.safe_load(f)
        cfg["backlog"] = {"provider": "devops", "org": "contoso", "project": "myproj", "token": ""}
        with open(config_path, "w", encoding="utf-8") as f:
            _yaml.dump(cfg, f)

        mock_dir.return_value = str(project_with_design)
        cmd = MagicMock()

        mock_result = BacklogResult(items_generated=1, items_pushed=0)

        with patch(f"{_CUSTOM_MODULE}._build_context") as mock_ctx, \
             patch("azext_prototype.stages.backlog_session.BacklogSession") as MockSession:
            from azext_prototype.agents.base import AgentContext

            ctx = AgentContext(
                project_config=cfg,
                project_dir=str(project_with_design),
                ai_provider=mock_ai_provider,
            )
            mock_ctx.return_value = ctx
            MockSession.return_value.run.return_value = mock_result

            result = prototype_generate_backlog(cmd, json_output=True)

        assert result["provider"] == "devops"

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_generate_backlog_json_format(self, mock_dir, project_with_design, mock_ai_provider):
        """Format field in result reflects output_format parameter."""
        from azext_prototype.custom import prototype_generate_backlog
        from azext_prototype.stages.backlog_session import BacklogResult

        mock_dir.return_value = str(project_with_design)
        cmd = MagicMock()

        mock_result = BacklogResult(items_generated=1, items_pushed=0)

        with patch(f"{_CUSTOM_MODULE}._build_context") as mock_ctx, \
             patch("azext_prototype.stages.backlog_session.BacklogSession") as MockSession:
            from azext_prototype.agents.base import AgentContext

            ctx = AgentContext(
                project_config={"project": {"name": "test"}},
                project_dir=str(project_with_design),
                ai_provider=mock_ai_provider,
            )
            mock_ctx.return_value = ctx
            MockSession.return_value.run.return_value = mock_result

            result = prototype_generate_backlog(cmd, provider="github", org="o", project="p", output_format="json", json_output=True)

        assert result["format"] == "json"

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_generate_backlog_prompts_when_unconfigured(self, mock_dir, project_with_design, mock_ai_provider):
        """When provider/org/project are missing, prompt interactively and save."""
        from azext_prototype.custom import prototype_generate_backlog
        from azext_prototype.stages.backlog_session import BacklogResult

        mock_dir.return_value = str(project_with_design)
        cmd = MagicMock()

        mock_result = BacklogResult(items_generated=1, items_pushed=0)

        with patch(f"{_CUSTOM_MODULE}._build_context") as mock_ctx, \
             patch(f"{_CUSTOM_MODULE}._prompt_backlog_config") as mock_prompt, \
             patch("azext_prototype.stages.backlog_session.BacklogSession") as MockSession:
            from azext_prototype.agents.base import AgentContext

            mock_prompt.return_value = {
                "provider": "github",
                "org": "prompted-org",
                "project": "prompted-repo",
            }

            ctx = AgentContext(
                project_config={"project": {"name": "test"}},
                project_dir=str(project_with_design),
                ai_provider=mock_ai_provider,
            )
            mock_ctx.return_value = ctx
            MockSession.return_value.run.return_value = mock_result

            result = prototype_generate_backlog(cmd, json_output=True)

        assert result["provider"] == "github"
        mock_prompt.assert_called_once()

        # Verify config was saved
        import yaml as _yaml
        config_path = project_with_design / "prototype.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            saved = _yaml.safe_load(f)
        assert saved["backlog"]["provider"] == "github"
        assert saved["backlog"]["org"] == "prompted-org"
        assert saved["backlog"]["project"] == "prompted-repo"

    @patch(f"{_CUSTOM_MODULE}._get_project_dir")
    def test_generate_backlog_no_prompt_when_fully_configured(self, mock_dir, project_with_design, mock_ai_provider):
        """No prompt when all three values are supplied via CLI args."""
        from azext_prototype.custom import prototype_generate_backlog
        from azext_prototype.stages.backlog_session import BacklogResult

        mock_dir.return_value = str(project_with_design)
        cmd = MagicMock()

        mock_result = BacklogResult(items_generated=1, items_pushed=0)

        with patch(f"{_CUSTOM_MODULE}._build_context") as mock_ctx, \
             patch(f"{_CUSTOM_MODULE}._prompt_backlog_config") as mock_prompt, \
             patch("azext_prototype.stages.backlog_session.BacklogSession") as MockSession:
            from azext_prototype.agents.base import AgentContext

            ctx = AgentContext(
                project_config={"project": {"name": "test"}},
                project_dir=str(project_with_design),
                ai_provider=mock_ai_provider,
            )
            mock_ctx.return_value = ctx
            MockSession.return_value.run.return_value = mock_result

            prototype_generate_backlog(cmd, provider="devops", org="myorg", project="myproj")

        mock_prompt.assert_not_called()


class TestPromptBacklogConfig:
    """Test the _prompt_backlog_config interactive helper."""

    def test_prompt_github(self):
        from azext_prototype.custom import _prompt_backlog_config

        with patch("builtins.input", side_effect=["1", "my-org", "my-repo"]):
            result = _prompt_backlog_config()

        assert result["provider"] == "github"
        assert result["org"] == "my-org"
        assert result["project"] == "my-repo"

    def test_prompt_devops(self):
        from azext_prototype.custom import _prompt_backlog_config

        with patch("builtins.input", side_effect=["2", "contoso", "my-project"]):
            result = _prompt_backlog_config()

        assert result["provider"] == "devops"
        assert result["org"] == "contoso"
        assert result["project"] == "my-project"

    def test_skips_already_configured_fields(self):
        from azext_prototype.custom import _prompt_backlog_config

        # Only project is missing — should only prompt for project
        with patch("builtins.input", side_effect=["my-repo"]):
            result = _prompt_backlog_config(
                current_provider="github",
                current_org="existing-org",
            )

        assert result["provider"] == "github"
        assert result["org"] == "existing-org"
        assert result["project"] == "my-repo"

    def test_preserves_all_existing_values(self):
        from azext_prototype.custom import _prompt_backlog_config

        # All configured — no prompts needed
        result = _prompt_backlog_config(
            current_provider="devops",
            current_org="contoso",
            current_project="myproj",
        )

        assert result["provider"] == "devops"
        assert result["org"] == "contoso"
        assert result["project"] == "myproj"

    def test_invalid_choice_retries(self):
        from azext_prototype.custom import _prompt_backlog_config

        with patch("builtins.input", side_effect=["3", "bad", "1", "org", "repo"]):
            result = _prompt_backlog_config()

        assert result["provider"] == "github"
