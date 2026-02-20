"""Tests for the template registry and built-in templates."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from azext_prototype.templates.registry import (
    ProjectTemplate,
    TemplateRegistry,
    TemplateService,
)


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "azext_prototype" / "templates" / "workloads"

EXPECTED_BUILTIN_NAMES = sorted([
    "ai-app",
    "data-pipeline",
    "microservices",
    "serverless-api",
    "web-app",
])


def _write_template(dest: Path, data: dict) -> Path:
    """Write a template dict as YAML and return the path."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(yaml.dump(data, sort_keys=False))
    return dest


def _minimal_template(**overrides) -> dict:
    """Return a minimal valid template dict."""
    base = {
        "metadata": {
            "name": "test-tmpl",
            "display_name": "Test Template",
            "description": "A test template.",
            "category": "web-app",
            "tags": ["test"],
        },
        "services": [
            {
                "name": "api",
                "type": "container-apps",
                "tier": "consumption",
                "config": {"identity": "system-assigned"},
            },
        ],
        "iac_defaults": {
            "resource_group_name": "rg-{project}-{env}",
            "tags": {"managed_by": "az-prototype"},
        },
        "requirements": "Build a test application.",
    }
    base.update(overrides)
    return base


# ================================================================== #
# TemplateService dataclass
# ================================================================== #

class TestTemplateService:
    """Tests for the TemplateService dataclass."""

    def test_basic_creation(self):
        svc = TemplateService(name="api", type="container-apps")
        assert svc.name == "api"
        assert svc.type == "container-apps"
        assert svc.tier == ""
        assert svc.config == {}

    def test_with_tier_and_config(self):
        svc = TemplateService(
            name="db",
            type="sql-database",
            tier="serverless",
            config={"entra_auth_only": True},
        )
        assert svc.tier == "serverless"
        assert svc.config["entra_auth_only"] is True

    def test_equality(self):
        a = TemplateService(name="x", type="y", tier="z", config={"k": 1})
        b = TemplateService(name="x", type="y", tier="z", config={"k": 1})
        assert a == b

    def test_default_config_independent(self):
        a = TemplateService(name="a", type="t")
        b = TemplateService(name="b", type="t")
        a.config["new_key"] = True
        assert "new_key" not in b.config


# ================================================================== #
# ProjectTemplate dataclass
# ================================================================== #

class TestProjectTemplate:
    """Tests for the ProjectTemplate dataclass."""

    def test_basic_creation(self):
        t = ProjectTemplate(
            name="test",
            display_name="Test",
            description="desc",
            category="web-app",
        )
        assert t.name == "test"
        assert t.services == []
        assert t.tags == []
        assert t.iac_defaults == {}
        assert t.requirements == ""

    def test_service_names(self):
        t = ProjectTemplate(
            name="test",
            display_name="Test",
            description="desc",
            category="web-app",
            services=[
                TemplateService(name="api", type="container-apps"),
                TemplateService(name="db", type="sql-database"),
            ],
        )
        assert t.service_names() == ["container-apps", "sql-database"]

    def test_service_names_empty(self):
        t = ProjectTemplate(
            name="test",
            display_name="Test",
            description="desc",
            category="web-app",
        )
        assert t.service_names() == []

    def test_full_roundtrip(self):
        svc = TemplateService(name="api", type="container-apps", tier="consumption")
        t = ProjectTemplate(
            name="full",
            display_name="Full Template",
            description="A full template",
            category="web-app",
            services=[svc],
            iac_defaults={"tags": {"env": "dev"}},
            requirements="Build something",
            tags=["web", "container-apps"],
        )
        assert t.name == "full"
        assert len(t.services) == 1
        assert t.services[0].type == "container-apps"
        assert t.tags == ["web", "container-apps"]
        assert t.requirements == "Build something"


# ================================================================== #
# TemplateRegistry — loading
# ================================================================== #

class TestRegistryLoading:
    """Tests for template loading from YAML files."""

    def test_load_single_template(self, tmp_path):
        _write_template(
            tmp_path / "my.template.yaml",
            _minimal_template(),
        )
        reg = TemplateRegistry()
        reg.load([tmp_path])
        assert reg.list_names() == ["test-tmpl"]

    def test_load_multiple_templates(self, tmp_path):
        for n in ("alpha", "beta", "gamma"):
            _write_template(
                tmp_path / f"{n}.template.yaml",
                _minimal_template(metadata={
                    "name": n,
                    "display_name": n.title(),
                    "description": f"{n} template",
                    "category": "web-app",
                }),
            )
        reg = TemplateRegistry()
        reg.load([tmp_path])
        assert reg.list_names() == ["alpha", "beta", "gamma"]

    def test_load_ignores_non_template_yaml(self, tmp_path):
        (tmp_path / "random.yaml").write_text("key: value")
        _write_template(tmp_path / "ok.template.yaml", _minimal_template())
        reg = TemplateRegistry()
        reg.load([tmp_path])
        assert reg.list_names() == ["test-tmpl"]

    def test_load_ignores_missing_directory(self, tmp_path):
        reg = TemplateRegistry()
        reg.load([tmp_path / "nonexistent"])
        assert reg.list_names() == []

    def test_load_skips_invalid_yaml(self, tmp_path):
        bad = tmp_path / "bad.template.yaml"
        bad.write_text("key: [unclosed\n  - item")
        _write_template(tmp_path / "ok.template.yaml", _minimal_template())
        reg = TemplateRegistry()
        reg.load([tmp_path])
        assert reg.list_names() == ["test-tmpl"]

    def test_load_handles_missing_metadata(self, tmp_path):
        """Template without metadata key still loads using stem as name."""
        _write_template(
            tmp_path / "no-meta.template.yaml",
            {"services": [{"name": "x", "type": "y"}]},
        )
        _write_template(tmp_path / "ok.template.yaml", _minimal_template())
        reg = TemplateRegistry()
        reg.load([tmp_path])
        names = reg.list_names()
        assert "test-tmpl" in names
        assert len(names) == 2

    def test_load_skips_non_dict_metadata(self, tmp_path):
        _write_template(
            tmp_path / "str-meta.template.yaml",
            {"metadata": "just a string", "services": []},
        )
        reg = TemplateRegistry()
        reg.load([tmp_path])
        assert reg.list_names() == []

    def test_load_multiple_directories(self, tmp_path):
        d1 = tmp_path / "builtin"
        d2 = tmp_path / "custom"
        _write_template(
            d1 / "a.template.yaml",
            _minimal_template(metadata={
                "name": "a", "display_name": "A",
                "description": "A", "category": "web-app",
            }),
        )
        _write_template(
            d2 / "b.template.yaml",
            _minimal_template(metadata={
                "name": "b", "display_name": "B",
                "description": "B", "category": "data-pipeline",
            }),
        )
        reg = TemplateRegistry()
        reg.load([d1, d2])
        assert reg.list_names() == ["a", "b"]

    def test_load_recursive(self, tmp_path):
        nested = tmp_path / "sub" / "deep"
        _write_template(nested / "deep.template.yaml", _minimal_template())
        reg = TemplateRegistry()
        reg.load([tmp_path])
        assert reg.list_names() == ["test-tmpl"]

    def test_last_write_wins_on_name_collision(self, tmp_path):
        _write_template(
            tmp_path / "a.template.yaml",
            _minimal_template(metadata={
                "name": "same", "display_name": "First",
                "description": "First", "category": "web-app",
            }),
        )
        _write_template(
            tmp_path / "b.template.yaml",
            _minimal_template(metadata={
                "name": "same", "display_name": "Second",
                "description": "Second", "category": "web-app",
            }),
        )
        reg = TemplateRegistry()
        reg.load([tmp_path])
        assert len(reg.list_names()) == 1
        t = reg.get("same")
        assert t is not None
        assert t.display_name == "Second"

    def test_empty_yaml_file(self, tmp_path):
        """Empty YAML still produces a template with stem-based name."""
        (tmp_path / "empty.template.yaml").write_text("")
        reg = TemplateRegistry()
        reg.load([tmp_path])
        names = reg.list_names()
        assert len(names) == 1
        assert names[0] == "empty.template"


# ================================================================== #
# TemplateRegistry — get / list
# ================================================================== #

class TestRegistryAccess:
    """Tests for get/list operations."""

    def test_get_existing(self, tmp_path):
        _write_template(tmp_path / "t.template.yaml", _minimal_template())
        reg = TemplateRegistry()
        reg.load([tmp_path])
        t = reg.get("test-tmpl")
        assert t is not None
        assert t.name == "test-tmpl"
        assert t.display_name == "Test Template"
        assert t.description == "A test template."

    def test_get_nonexistent_returns_none(self, tmp_path):
        _write_template(tmp_path / "t.template.yaml", _minimal_template())
        reg = TemplateRegistry()
        reg.load([tmp_path])
        assert reg.get("nope") is None

    def test_list_templates_returns_objects(self, tmp_path):
        _write_template(tmp_path / "t.template.yaml", _minimal_template())
        reg = TemplateRegistry()
        reg.load([tmp_path])
        templates = reg.list_templates()
        assert len(templates) == 1
        assert isinstance(templates[0], ProjectTemplate)

    def test_auto_load_on_first_get(self):
        reg = TemplateRegistry()
        # Should trigger auto-load from default directory
        result = reg.get("web-app")
        # web-app is a built-in template
        assert result is not None
        assert result.name == "web-app"

    def test_auto_load_on_list_names(self):
        reg = TemplateRegistry()
        names = reg.list_names()
        assert len(names) >= 5

    def test_auto_load_on_list_templates(self):
        reg = TemplateRegistry()
        templates = reg.list_templates()
        assert len(templates) >= 5


# ================================================================== #
# Template parsing — field extraction
# ================================================================== #

class TestTemplateParsing:
    """Tests for correct field extraction from YAML."""

    def test_services_parsed(self, tmp_path):
        data = _minimal_template(services=[
            {"name": "api", "type": "container-apps", "tier": "consumption",
             "config": {"ingress": "internal"}},
            {"name": "db", "type": "sql-database"},
        ])
        _write_template(tmp_path / "t.template.yaml", data)
        reg = TemplateRegistry()
        reg.load([tmp_path])
        t = reg.get("test-tmpl")
        assert t is not None
        assert len(t.services) == 2
        assert t.services[0].type == "container-apps"
        assert t.services[0].config["ingress"] == "internal"
        assert t.services[1].tier == ""

    def test_iac_defaults_parsed(self, tmp_path):
        data = _minimal_template(iac_defaults={
            "resource_group_name": "rg-test-{env}",
            "tags": {"managed_by": "test"},
        })
        _write_template(tmp_path / "t.template.yaml", data)
        reg = TemplateRegistry()
        reg.load([tmp_path])
        t = reg.get("test-tmpl")
        assert t is not None
        assert t.iac_defaults["resource_group_name"] == "rg-test-{env}"

    def test_requirements_parsed(self, tmp_path):
        data = _minimal_template(requirements="Build something cool.\n")
        _write_template(tmp_path / "t.template.yaml", data)
        reg = TemplateRegistry()
        reg.load([tmp_path])
        t = reg.get("test-tmpl")
        assert t is not None
        assert "Build something cool" in t.requirements

    def test_tags_parsed(self, tmp_path):
        data = _minimal_template()
        _write_template(tmp_path / "t.template.yaml", data)
        reg = TemplateRegistry()
        reg.load([tmp_path])
        t = reg.get("test-tmpl")
        assert t is not None
        assert t.tags == ["test"]

    def test_non_dict_service_skipped(self, tmp_path):
        data = _minimal_template(services=["just-a-string", {"name": "ok", "type": "x"}])
        _write_template(tmp_path / "t.template.yaml", data)
        reg = TemplateRegistry()
        reg.load([tmp_path])
        t = reg.get("test-tmpl")
        assert t is not None
        assert len(t.services) == 1
        assert t.services[0].name == "ok"

    def test_missing_iac_defaults_gives_empty_dict(self, tmp_path):
        data = _minimal_template()
        del data["iac_defaults"]
        _write_template(tmp_path / "t.template.yaml", data)
        reg = TemplateRegistry()
        reg.load([tmp_path])
        t = reg.get("test-tmpl")
        assert t is not None
        assert t.iac_defaults == {}

    def test_missing_requirements_gives_empty_string(self, tmp_path):
        data = _minimal_template()
        del data["requirements"]
        _write_template(tmp_path / "t.template.yaml", data)
        reg = TemplateRegistry()
        reg.load([tmp_path])
        t = reg.get("test-tmpl")
        assert t is not None
        assert t.requirements == ""

    def test_missing_tags_gives_empty_list(self, tmp_path):
        data = _minimal_template()
        data["metadata"].pop("tags", None)
        _write_template(tmp_path / "t.template.yaml", data)
        reg = TemplateRegistry()
        reg.load([tmp_path])
        t = reg.get("test-tmpl")
        assert t is not None
        assert t.tags == []

    def test_name_falls_back_to_stem(self, tmp_path):
        data = _minimal_template()
        del data["metadata"]["name"]
        _write_template(tmp_path / "custom.template.yaml", data)
        reg = TemplateRegistry()
        reg.load([tmp_path])
        # Falls back to path.stem which is "custom.template"
        # Actually the stem of "custom.template.yaml" is "custom.template"
        names = reg.list_names()
        assert len(names) == 1


# ================================================================== #
# Built-in templates — integrity checks
# ================================================================== #

class TestBuiltinTemplates:
    """Verify all shipped templates parse correctly and meet standards."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.reg = TemplateRegistry()
        self.reg.load([BUILTIN_DIR])

    def test_all_expected_templates_exist(self):
        assert self.reg.list_names() == EXPECTED_BUILTIN_NAMES

    @pytest.mark.parametrize("name", EXPECTED_BUILTIN_NAMES)
    def test_template_has_required_fields(self, name):
        t = self.reg.get(name)
        assert t is not None
        assert t.name == name
        assert t.display_name
        assert t.description
        assert t.category
        assert len(t.services) > 0
        assert t.requirements

    @pytest.mark.parametrize("name", EXPECTED_BUILTIN_NAMES)
    def test_template_has_iac_defaults(self, name):
        t = self.reg.get(name)
        assert t is not None
        assert "resource_group_name" in t.iac_defaults
        assert "tags" in t.iac_defaults
        assert t.iac_defaults["tags"]["managed_by"] == "az-prototype"

    @pytest.mark.parametrize("name", EXPECTED_BUILTIN_NAMES)
    def test_template_services_have_name_and_type(self, name):
        t = self.reg.get(name)
        assert t is not None
        for svc in t.services:
            assert svc.name, f"Service missing name in {name}"
            assert svc.type, f"Service missing type in {name}"

    @pytest.mark.parametrize("name", EXPECTED_BUILTIN_NAMES)
    def test_template_has_tags(self, name):
        t = self.reg.get(name)
        assert t is not None
        assert len(t.tags) > 0, f"Template '{name}' should have tags"

    @pytest.mark.parametrize("name", EXPECTED_BUILTIN_NAMES)
    def test_template_has_managed_identity(self, name):
        """All templates must use managed identity on at least one service."""
        t = self.reg.get(name)
        assert t is not None
        has_identity = any(
            "identity" in svc.config or svc.type == "managed-identity"
            for svc in t.services
        )
        assert has_identity, f"Template '{name}' lacks managed identity"

    @pytest.mark.parametrize("name", EXPECTED_BUILTIN_NAMES)
    def test_template_has_network_isolation(self, name):
        """All templates should include a virtual-network service."""
        t = self.reg.get(name)
        assert t is not None
        has_vnet = any(svc.type == "virtual-network" for svc in t.services)
        assert has_vnet, f"Template '{name}' missing virtual-network"

    @pytest.mark.parametrize("name", EXPECTED_BUILTIN_NAMES)
    def test_template_yaml_valid(self, name):
        """Each built-in YAML file should parse without error."""
        path = BUILTIN_DIR / f"{name}.template.yaml"
        assert path.exists(), f"Expected file {path}"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "metadata" in data
        assert "services" in data


# ================================================================== #
# Specific built-in template checks
# ================================================================== #

class TestWebAppTemplate:
    def test_services(self):
        reg = TemplateRegistry()
        reg.load([BUILTIN_DIR])
        t = reg.get("web-app")
        assert t is not None
        types = t.service_names()
        assert "container-apps" in types
        assert "sql-database" in types
        assert "key-vault" in types
        assert "api-management" in types

    def test_category(self):
        reg = TemplateRegistry()
        reg.load([BUILTIN_DIR])
        t = reg.get("web-app")
        assert t is not None
        assert t.category == "web-app"


class TestDataPipelineTemplate:
    def test_services(self):
        reg = TemplateRegistry()
        reg.load([BUILTIN_DIR])
        t = reg.get("data-pipeline")
        assert t is not None
        types = t.service_names()
        assert "functions" in types
        assert "cosmos-db" in types
        assert "storage" in types
        assert "event-grid" in types

    def test_cosmos_session_consistency(self):
        reg = TemplateRegistry()
        reg.load([BUILTIN_DIR])
        t = reg.get("data-pipeline")
        assert t is not None
        cosmos = [s for s in t.services if s.type == "cosmos-db"][0]
        assert cosmos.config.get("consistency") == "session"


class TestAiAppTemplate:
    def test_services(self):
        reg = TemplateRegistry()
        reg.load([BUILTIN_DIR])
        t = reg.get("ai-app")
        assert t is not None
        types = t.service_names()
        assert "container-apps" in types
        assert "cognitive-services" in types
        assert "cosmos-db" in types
        assert "api-management" in types

    def test_openai_model(self):
        reg = TemplateRegistry()
        reg.load([BUILTIN_DIR])
        t = reg.get("ai-app")
        assert t is not None
        ai = [s for s in t.services if s.type == "cognitive-services"][0]
        assert ai.config.get("kind") == "openai"
        assert len(ai.config.get("models", [])) > 0


class TestMicroservicesTemplate:
    def test_services(self):
        reg = TemplateRegistry()
        reg.load([BUILTIN_DIR])
        t = reg.get("microservices")
        assert t is not None
        types = t.service_names()
        assert types.count("container-apps") >= 3
        assert "service-bus" in types
        assert "api-management" in types

    def test_user_assigned_identity(self):
        reg = TemplateRegistry()
        reg.load([BUILTIN_DIR])
        t = reg.get("microservices")
        assert t is not None
        has_ua = any(svc.type == "managed-identity" for svc in t.services)
        assert has_ua, "Microservices template should have user-assigned MI"


class TestServerlessApiTemplate:
    def test_services(self):
        reg = TemplateRegistry()
        reg.load([BUILTIN_DIR])
        t = reg.get("serverless-api")
        assert t is not None
        types = t.service_names()
        assert "functions" in types
        assert "sql-database" in types
        assert "key-vault" in types
        assert "api-management" in types

    def test_sql_auto_pause(self):
        reg = TemplateRegistry()
        reg.load([BUILTIN_DIR])
        t = reg.get("serverless-api")
        assert t is not None
        sql = [s for s in t.services if s.type == "sql-database"][0]
        assert sql.config.get("auto_pause_delay") == 60


# ================================================================== #
# Schema file existence
# ================================================================== #

class TestTemplateSchema:
    """Verify the JSON schema file exists and is valid JSON."""

    SCHEMA_PATH = (
        Path(__file__).resolve().parent.parent
        / "azext_prototype" / "templates" / "template.schema.json"
    )

    def test_schema_file_exists(self):
        assert self.SCHEMA_PATH.exists()

    def test_schema_is_valid_json(self):
        import json
        data = json.loads(self.SCHEMA_PATH.read_text(encoding="utf-8"))
        assert data.get("title") == "Project Template"
        assert "metadata" in data.get("properties", {})
        assert "services" in data.get("properties", {})

    def test_template_files_reference_schema(self):
        """Built-in templates should reference template.schema.json."""
        for path in sorted(BUILTIN_DIR.rglob("*.template.yaml")):
            text = path.read_text(encoding="utf-8")
            assert "template.schema.json" in text, (
                f"{path.name} missing schema reference"
            )
