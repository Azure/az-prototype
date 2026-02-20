"""Tests for azext_prototype.config — ProjectConfig and validation."""


import pytest
import yaml
from knack.util import CLIError

from azext_prototype.config import (
    DEFAULT_CONFIG,
    ProjectConfig,
    SECRET_KEY_PREFIXES,
    _ALLOWED_AI_PROVIDERS,
    _BLOCKED_AI_PROVIDERS,
    _safe_load_yaml,
    _sanitize_for_yaml,
)


class TestDefaultConfig:
    """Verify DEFAULT_CONFIG structure."""

    def test_has_project_section(self):
        assert "project" in DEFAULT_CONFIG
        assert "name" in DEFAULT_CONFIG["project"]
        assert "location" in DEFAULT_CONFIG["project"]

    def test_has_naming_section(self):
        assert "naming" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["naming"]["strategy"] == "microsoft-alz"
        assert DEFAULT_CONFIG["naming"]["zone_id"] == "zd"

    def test_has_ai_section(self):
        assert "ai" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["ai"]["provider"] == "copilot"

    def test_has_stages_section(self):
        assert "stages" in DEFAULT_CONFIG
        for stage in ("init", "design", "build", "deploy"):
            assert stage in DEFAULT_CONFIG["stages"]
            assert DEFAULT_CONFIG["stages"][stage]["completed"] is False

    def test_has_backlog_section(self):
        assert "backlog" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["backlog"]["provider"] == "github"
        assert "org" in DEFAULT_CONFIG["backlog"]
        assert "project" in DEFAULT_CONFIG["backlog"]
        assert "token" in DEFAULT_CONFIG["backlog"]


class TestProjectConfig:
    """Test ProjectConfig load/save/get/set."""

    def test_create_default(self, tmp_project):
        config = ProjectConfig(str(tmp_project))
        result = config.create_default({"project": {"name": "my-app"}})

        assert result["project"]["name"] == "my-app"
        assert (tmp_project / "prototype.yaml").exists()

    def test_load_existing(self, project_with_config):
        config = ProjectConfig(str(project_with_config))
        data = config.load()

        assert data["project"]["name"] == "test-project"
        assert data["project"]["location"] == "eastus"

    def test_load_missing_raises(self, tmp_project):
        config = ProjectConfig(str(tmp_project))

        with pytest.raises(CLIError, match="Configuration file not found"):
            config.load()

    def test_get_dot_notation(self, project_with_config):
        config = ProjectConfig(str(project_with_config))
        config.load()

        assert config.get("project.name") == "test-project"
        assert config.get("ai.provider") == "github-models"
        assert config.get("nonexistent.key", "fallback") == "fallback"

    def test_set_dot_notation(self, project_with_config):
        config = ProjectConfig(str(project_with_config))
        config.load()
        config.set("project.location", "westus2")

        # Reload to verify persistence
        config2 = ProjectConfig(str(project_with_config))
        config2.load()
        assert config2.get("project.location") == "westus2"

    def test_set_creates_intermediate_dicts(self, project_with_config):
        config = ProjectConfig(str(project_with_config))
        config.load()
        config.set("new.nested.key", "value")

        assert config.get("new.nested.key") == "value"

    def test_exists(self, project_with_config, tmp_path):
        config_exists = ProjectConfig(str(project_with_config))
        assert config_exists.exists() is True

        # Use a fresh directory with no prototype.yaml
        fresh_dir = tmp_path / "empty-project"
        fresh_dir.mkdir()
        config_missing = ProjectConfig(str(fresh_dir))
        assert config_missing.exists() is False

    def test_to_dict_returns_copy(self, project_with_config):
        config = ProjectConfig(str(project_with_config))
        config.load()
        d = config.to_dict()

        # Shallow copy — nested mutation may propagate, so just verify top-level independence
        original_name = config.get("project.name")
        assert original_name == "test-project"
        assert "project" in d


class TestConfigValidation:
    """Test security constraints on config values."""

    def test_allowed_ai_providers(self, project_with_config):
        config = ProjectConfig(str(project_with_config))
        config.load()

        for provider in _ALLOWED_AI_PROVIDERS:
            config.set("ai.provider", provider)  # Should not raise

    def test_blocked_ai_providers(self, project_with_config):
        config = ProjectConfig(str(project_with_config))
        config.load()

        for provider in _BLOCKED_AI_PROVIDERS:
            with pytest.raises(CLIError, match="not permitted"):
                config.set("ai.provider", provider)

    def test_unknown_ai_provider(self, project_with_config):
        config = ProjectConfig(str(project_with_config))
        config.load()

        with pytest.raises(CLIError, match="Unknown AI provider"):
            config.set("ai.provider", "some-random-provider")

    def test_valid_azure_openai_endpoint(self, project_with_config):
        config = ProjectConfig(str(project_with_config))
        config.load()
        config.set("ai.azure_openai.endpoint", "https://my-resource.openai.azure.com/")

    def test_public_openai_endpoint_blocked(self, project_with_config):
        config = ProjectConfig(str(project_with_config))
        config.load()

        with pytest.raises(CLIError, match="not permitted"):
            config.set("ai.azure_openai.endpoint", "https://api.openai.com/v1")

    def test_invalid_azure_openai_endpoint(self, project_with_config):
        config = ProjectConfig(str(project_with_config))
        config.load()

        with pytest.raises(CLIError, match="Invalid Azure OpenAI endpoint"):
            config.set("ai.azure_openai.endpoint", "https://example.com/not-azure")

    def test_api_key_rejected(self, project_with_config):
        config = ProjectConfig(str(project_with_config))
        config.load()

        with pytest.raises(CLIError, match="API-key authentication is not supported"):
            config.set("ai.azure_openai.api_key", "sk-abc123")

    def test_change_model_via_config_set(self, project_with_config):
        """Verify that az prototype config set --key ai.model works."""
        config = ProjectConfig(str(project_with_config))
        config.load()

        config.set("ai.model", "gpt-4o")
        assert config.get("ai.model") == "gpt-4o"

        config.set("ai.model", "claude-sonnet-4-5-20250514")
        assert config.get("ai.model") == "claude-sonnet-4-5-20250514"

    def test_change_provider_to_copilot(self, project_with_config):
        """Verify that switching to the copilot provider is allowed."""
        config = ProjectConfig(str(project_with_config))
        config.load()

        config.set("ai.provider", "copilot")
        assert config.get("ai.provider") == "copilot"

    # --- IaC tool validation ---

    def test_valid_iac_tools(self, project_with_config):
        config = ProjectConfig(str(project_with_config))
        config.load()
        for tool in ("terraform", "bicep"):
            config.set("project.iac_tool", tool)
            assert config.get("project.iac_tool") == tool

    def test_invalid_iac_tool_raises(self, project_with_config):
        config = ProjectConfig(str(project_with_config))
        config.load()
        with pytest.raises(CLIError, match="Unknown IaC tool"):
            config.set("project.iac_tool", "pulumi")

    # --- Location validation ---

    def test_valid_azure_locations(self, project_with_config):
        config = ProjectConfig(str(project_with_config))
        config.load()
        for region in ("eastus", "westus2", "northeurope", "swedencentral"):
            config.set("project.location", region)
            assert config.get("project.location") == region

    def test_invalid_azure_location_raises(self, project_with_config):
        config = ProjectConfig(str(project_with_config))
        config.load()
        with pytest.raises(CLIError, match="Unknown Azure region"):
            config.set("project.location", "narnia-west")


class TestSecretsFile:
    """Test prototype.secrets.yaml separation."""

    def test_secret_key_prefixes_defined(self):
        """SECRET_KEY_PREFIXES should contain only truly sensitive keys."""
        assert "ai.azure_openai.api_key" in SECRET_KEY_PREFIXES
        assert "deploy.subscription" in SECRET_KEY_PREFIXES
        assert "backlog.token" in SECRET_KEY_PREFIXES
        # Non-sensitive values must NOT be in the list
        assert "ai.azure_openai.endpoint" not in SECRET_KEY_PREFIXES
        assert "deploy.resource_group" not in SECRET_KEY_PREFIXES
        assert "backlog.provider" not in SECRET_KEY_PREFIXES
        assert "backlog.org" not in SECRET_KEY_PREFIXES

    def test_is_secret_key(self):
        assert ProjectConfig._is_secret_key("deploy.subscription") is True
        assert ProjectConfig._is_secret_key("ai.azure_openai.api_key") is True
        assert ProjectConfig._is_secret_key("backlog.token") is True
        assert ProjectConfig._is_secret_key("ai.provider") is False
        assert ProjectConfig._is_secret_key("project.name") is False
        assert ProjectConfig._is_secret_key("ai.azure_openai.endpoint") is False
        assert ProjectConfig._is_secret_key("deploy.resource_group") is False
        assert ProjectConfig._is_secret_key("backlog.provider") is False

    def test_set_secret_key_creates_secrets_file(self, project_with_config):
        """Setting a secret key should write to prototype.secrets.yaml."""
        config = ProjectConfig(str(project_with_config))
        config.load()

        config.set("deploy.subscription", "00000000-0000-0000-0000-000000000001")

        secrets_path = project_with_config / "prototype.secrets.yaml"
        assert secrets_path.exists()

        with open(secrets_path, "r", encoding="utf-8") as f:
            secrets = yaml.safe_load(f)
        assert secrets["deploy"]["subscription"] == "00000000-0000-0000-0000-000000000001"

    def test_secret_stripped_from_main_config(self, project_with_config):
        """Secret values should be empty in prototype.yaml on disk."""
        config = ProjectConfig(str(project_with_config))
        config.load()

        config.set("deploy.subscription", "sub-id-12345")

        # Read the main config file directly
        with open(project_with_config / "prototype.yaml", "r", encoding="utf-8") as f:
            main_data = yaml.safe_load(f)
        assert main_data["deploy"]["subscription"] == ""

    def test_secret_available_in_memory(self, project_with_config):
        """In-memory config should still have the secret value."""
        config = ProjectConfig(str(project_with_config))
        config.load()

        config.set("deploy.subscription", "sub-id-12345")
        assert config.get("deploy.subscription") == "sub-id-12345"

    def test_load_merges_secrets(self, project_with_config):
        """Loading should merge secrets from prototype.secrets.yaml."""
        secrets_path = project_with_config / "prototype.secrets.yaml"
        secrets = {"deploy": {"subscription": "my-secret-sub-id"}}
        with open(secrets_path, "w", encoding="utf-8") as f:
            yaml.dump(secrets, f)

        config = ProjectConfig(str(project_with_config))
        config.load()

        assert config.get("deploy.subscription") == "my-secret-sub-id"

    def test_load_without_secrets_file(self, project_with_config):
        """Loading without prototype.secrets.yaml should work normally."""
        secrets_path = project_with_config / "prototype.secrets.yaml"
        assert not secrets_path.exists()

        config = ProjectConfig(str(project_with_config))
        config.load()

        assert config.get("deploy.subscription") == ""

    def test_non_secret_key_stays_in_main_config(self, project_with_config):
        """Non-secret keys should NOT create or write to secrets file."""
        config = ProjectConfig(str(project_with_config))
        config.load()

        config.set("project.name", "new-name")

        secrets_path = project_with_config / "prototype.secrets.yaml"
        assert not secrets_path.exists()

    def test_create_default_separates_secrets(self, tmp_project):
        """create_default should route secret overrides to secrets file."""
        config = ProjectConfig(str(tmp_project))
        config.create_default({
            "project": {"name": "my-app"},
            "deploy": {"subscription": "sub-123", "resource_group": "rg-test"},
        })

        # Main file should have resource_group but empty subscription
        with open(tmp_project / "prototype.yaml", "r", encoding="utf-8") as f:
            main_data = yaml.safe_load(f)
        assert main_data["deploy"]["resource_group"] == "rg-test"
        assert main_data["deploy"]["subscription"] == ""

        # Secrets file should have the subscription
        secrets_path = tmp_project / "prototype.secrets.yaml"
        assert secrets_path.exists()
        with open(secrets_path, "r", encoding="utf-8") as f:
            secrets = yaml.safe_load(f)
        assert secrets["deploy"]["subscription"] == "sub-123"

    def test_create_default_no_secrets_when_empty(self, tmp_project):
        """create_default should not create secrets file if no secret values."""
        config = ProjectConfig(str(tmp_project))
        config.create_default({"project": {"name": "my-app"}})

        secrets_path = tmp_project / "prototype.secrets.yaml"
        assert not secrets_path.exists()

    def test_secrets_path_attribute(self, tmp_project):
        """ProjectConfig should expose secrets_path."""
        config = ProjectConfig(str(tmp_project))
        assert config.secrets_path == tmp_project / "prototype.secrets.yaml"

    def test_roundtrip_secret_through_reload(self, project_with_config):
        """Secret should survive a save/load cycle."""
        config = ProjectConfig(str(project_with_config))
        config.load()
        config.set("deploy.subscription", "roundtrip-sub-id")

        config2 = ProjectConfig(str(project_with_config))
        config2.load()
        assert config2.get("deploy.subscription") == "roundtrip-sub-id"


# ------------------------------------------------------------------ #
# _sanitize_for_yaml                                                  #
# ------------------------------------------------------------------ #

class TestSanitizeForYaml:
    """Verify _sanitize_for_yaml strips non-standard types."""

    def test_plain_values_unchanged(self):
        data = {"key": "value", "number": 42, "flag": True, "pi": 3.14}
        assert _sanitize_for_yaml(data) == data

    def test_nested_dict_and_list(self):
        data = {"a": {"b": [1, "two", 3.0]}}
        assert _sanitize_for_yaml(data) == data

    def test_str_subclass_converted(self):
        """knack.validators.DefaultStr (a str subclass) must become plain str."""

        class FakeDefaultStr(str):
            """Mimic knack.validators.DefaultStr."""

        data = {"name": FakeDefaultStr("my-app"), "loc": FakeDefaultStr("eastus")}
        result = _sanitize_for_yaml(data)

        assert result == {"name": "my-app", "loc": "eastus"}
        assert type(result["name"]) is str
        assert type(result["loc"]) is str

    def test_bool_not_promoted_to_int(self):
        """bool is a subclass of int — must stay bool."""
        data = {"flag": True, "count": 5}
        result = _sanitize_for_yaml(data)
        assert result["flag"] is True
        assert type(result["flag"]) is bool

    def test_none_passthrough(self):
        assert _sanitize_for_yaml(None) is None

    def test_list_of_str_subclasses(self):
        class Tag(str):
            pass

        result = _sanitize_for_yaml([Tag("a"), Tag("b")])
        assert result == ["a", "b"]
        assert all(type(v) is str for v in result)


class TestDefaultStrRoundTrip:
    """End-to-end: config with DefaultStr values survives save → load."""

    def test_create_default_with_str_subclass(self, tmp_project):
        class DefaultStr(str):
            """Mimic knack.validators.DefaultStr."""

        config = ProjectConfig(str(tmp_project))
        config.create_default({
            "project": {
                "name": DefaultStr("demo"),
                "location": DefaultStr("westus"),
            },
        })

        # Must be loadable with yaml.safe_load (via config.load())
        config2 = ProjectConfig(str(tmp_project))
        data = config2.load()
        assert data["project"]["name"] == "demo"
        assert data["project"]["location"] == "westus"

    def test_set_with_str_subclass(self, project_with_config):
        class DefaultStr(str):
            """Mimic knack.validators.DefaultStr."""

        config = ProjectConfig(str(project_with_config))
        config.load()
        config.set("project.location", DefaultStr("northeurope"))

        config2 = ProjectConfig(str(project_with_config))
        config2.load()
        assert config2.get("project.location") == "northeurope"


class TestSafeLoadYaml:
    """Verify _safe_load_yaml handles corrupted DefaultStr tags."""

    def test_clean_yaml_loads_normally(self):
        clean = "project:\n  name: demo\n  location: eastus\n"
        result = _safe_load_yaml(clean)
        assert result == {"project": {"name": "demo", "location": "eastus"}}

    def test_corrupted_default_str_simple_sequence(self):
        """Simple sequence form: !!python/object/new:...\n    - value."""
        corrupted = (
            "project:\n"
            "  name: demo\n"
            "  location: !!python/object/new:knack.validators.DefaultStr\n"
            "    - eastus\n"
        )
        result = _safe_load_yaml(corrupted)
        assert result["project"]["location"] == "eastus"
        assert type(result["project"]["location"]) is str

    def test_corrupted_default_str_full_object(self):
        """Real-world form with args + state (from yaml.dump of DefaultStr)."""
        corrupted = (
            "ai:\n"
            "  provider: !!python/object/new:knack.validators.DefaultStr\n"
            "    args:\n"
            "    - github-models\n"
            "    state:\n"
            "      is_default: true\n"
            "  model: gpt-4o\n"
        )
        result = _safe_load_yaml(corrupted)
        assert result["ai"]["provider"] == "github-models"
        assert type(result["ai"]["provider"]) is str
        # Non-tagged values must survive intact
        assert result["ai"]["model"] == "gpt-4o"

    def test_corrupted_yaml_from_file_stream(self, tmp_path):
        """Fallback should work with file streams (seekable)."""
        corrupted = (
            "ai:\n"
            "  provider: !!python/object/new:knack.validators.DefaultStr\n"
            "    args:\n"
            "    - github-models\n"
            "    state:\n"
            "      is_default: true\n"
        )
        f = tmp_path / "test.yaml"
        f.write_text(corrupted, encoding="utf-8")

        with open(f, "r", encoding="utf-8") as stream:
            result = _safe_load_yaml(stream)

        assert result["ai"]["provider"] == "github-models"

    def test_load_corrupted_config_end_to_end(self, tmp_project):
        """ProjectConfig.load() should survive the exact real-world file."""
        corrupted = (
            "project:\n"
            "  name: my-demo\n"
            "  location: westus3\n"
            "  iac_tool: bicep\n"
            "ai:\n"
            "  provider: !!python/object/new:knack.validators.DefaultStr\n"
            "    args:\n"
            "    - github-models\n"
            "    state:\n"
            "      is_default: true\n"
            "  model: claude-sonnet-4-5-20250514\n"
            "stages:\n"
            "  init:\n"
            "    completed: true\n"
        )
        (tmp_project / "prototype.yaml").write_text(corrupted, encoding="utf-8")

        config = ProjectConfig(str(tmp_project))
        data = config.load()

        assert data["project"]["name"] == "my-demo"
        assert data["project"]["location"] == "westus3"
        assert data["ai"]["provider"] == "github-models"
        assert data["ai"]["model"] == "claude-sonnet-4-5-20250514"
        assert data["stages"]["init"]["completed"] is True