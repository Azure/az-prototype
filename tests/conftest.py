"""Shared test fixtures for azext_prototype tests."""

import copy
import json
from unittest.mock import MagicMock, patch

import pytest
import yaml

from azext_prototype.ai.provider import AIResponse
from azext_prototype.config import DEFAULT_CONFIG


# ------------------------------------------------------------------
# Global: prevent real telemetry HTTP calls during tests
# ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_telemetry_network():
    """Prevent telemetry from making real HTTP requests during tests.

    The @track decorator fires on every prototype_* command.  With a
    non-empty _BUILTIN_CONNECTION_STRING, _send_envelope() would POST
    to App Insights on every test invocation.  This fixture stubs it.
    """
    with patch("azext_prototype.telemetry._send_envelope", return_value=True):
        yield


def make_ai_response(content="Mock AI response content", model="gpt-4o", usage=None):
    """Convenience factory for AIResponse — reduces boilerplate in tests."""
    return AIResponse(
        content=content,
        model=model,
        usage=usage or {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
    )


@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary project directory with standard scaffold."""
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()

    # Scaffold directories
    (project_dir / "concept" / "apps").mkdir(parents=True)
    (project_dir / "concept" / "infra" / "terraform").mkdir(parents=True)
    (project_dir / "concept" / "infra" / "bicep").mkdir(parents=True)
    (project_dir / "concept" / "db" / "sql").mkdir(parents=True)
    (project_dir / "concept" / "db" / "cosmos").mkdir(parents=True)
    (project_dir / "concept" / "db" / "databricks").mkdir(parents=True)
    (project_dir / "concept" / "db" / "fabric").mkdir(parents=True)
    (project_dir / "concept" / "docs").mkdir(parents=True)
    (project_dir / ".prototype" / "agents").mkdir(parents=True)
    (project_dir / ".prototype" / "state").mkdir(parents=True)

    return project_dir


@pytest.fixture
def sample_config():
    """Return a deep copy of the default config with test values."""
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["project"]["name"] = "test-project"
    config["project"]["location"] = "eastus"
    config["project"]["environment"] = "dev"
    config["naming"]["strategy"] = "microsoft-alz"
    config["naming"]["org"] = "contoso"
    config["naming"]["env"] = "dev"
    config["naming"]["zone_id"] = "zd"
    config["ai"]["provider"] = "github-models"
    return config


@pytest.fixture
def project_with_config(tmp_project, sample_config):
    """Create a project directory with a populated prototype.yaml."""
    config_path = tmp_project / "prototype.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(sample_config, f, default_flow_style=False)
    return tmp_project


@pytest.fixture
def project_with_design(project_with_config):
    """Create a project with design state (design stage completed)."""
    design_state = {
        "architecture": "## Architecture\nSample architecture for testing.",
        "artifacts": [],
        "iterations": 1,
        "timestamp": "2026-01-01T00:00:00",
    }
    state_file = project_with_config / ".prototype" / "state" / "design.json"
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(design_state, f)
    return project_with_config


@pytest.fixture
def project_with_build(project_with_design):
    """Create a project with build state (build stage completed).

    Writes ``build.yaml`` (YAML, matching BuildState format) with realistic
    deployment_stages so that ``deploy_state.load_from_build_state()`` can
    import them.  Also writes ``build.json`` for backward compatibility with
    any legacy tests that reference it.
    """
    build_state_yaml = {
        "iac_tool": "terraform",
        "templates_used": [],
        "deployment_stages": [
            {
                "stage": 1,
                "name": "Foundation",
                "category": "infra",
                "services": [
                    {
                        "name": "key-vault",
                        "computed_name": "zd-kv-api-dev-eus",
                        "resource_type": "Microsoft.KeyVault/vaults",
                        "sku": "standard",
                    },
                ],
                "status": "generated",
                "dir": "concept/infra/terraform/stage-1-foundation",
                "files": [],
            },
            {
                "stage": 2,
                "name": "Application",
                "category": "app",
                "services": [
                    {
                        "name": "web-app",
                        "computed_name": "zd-app-web-dev-eus",
                        "resource_type": "Microsoft.Web/sites",
                        "sku": "B1",
                    },
                ],
                "status": "generated",
                "dir": "concept/apps/stage-2-application",
                "files": [],
            },
        ],
        "policy_checks": [],
        "policy_overrides": [],
        "files_generated": [],
        "resources": [],
        "_metadata": {
            "created": "2026-01-01T00:00:00",
            "last_updated": "2026-01-01T00:00:00",
            "iteration": 1,
        },
    }

    state_dir = project_with_design / ".prototype" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    # Primary: build.yaml (used by DeployState.load_from_build_state)
    with open(state_dir / "build.yaml", "w", encoding="utf-8") as f:
        yaml.dump(build_state_yaml, f, default_flow_style=False)

    # Backward compat: build.json
    build_state_json = {
        "scope": "all",
        "timestamp": "2026-01-01T00:00:00",
        "generated_files": [],
    }
    with open(state_dir / "build.json", "w", encoding="utf-8") as f:
        json.dump(build_state_json, f)

    return project_with_design


@pytest.fixture
def project_with_discovery(project_with_config):
    """Create a project with discovery state but no completed design.

    Has ``discovery.yaml`` with architecture learnings but no ``design.json``.
    Tests the discovery.yaml fallback path in ``_load_design_context()``.
    """
    discovery_state = {
        "project": {
            "summary": "API with Cosmos DB backend",
            "goals": ["Build API for data access"],
        },
        "requirements": {
            "functional": ["REST API", "NoSQL storage"],
            "non_functional": [],
        },
        "constraints": ["Use PaaS only"],
        "confirmed_items": ["Use Container Apps", "Use Cosmos DB"],
        "open_items": [],
        "scope": {
            "in_scope": ["Container Apps API", "Cosmos DB backend"],
            "out_of_scope": [],
            "deferred": [],
        },
        "architecture": {
            "services": ["container-apps", "cosmos-db", "key-vault"],
            "integrations": ["APIM to Container Apps"],
            "data_flow": "API -> Cosmos DB",
        },
        "_metadata": {
            "exchange_count": 3,
            "created": "2026-01-01T00:00:00",
            "last_updated": "2026-01-01T00:00:00",
        },
    }
    state_dir = project_with_config / ".prototype" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "discovery.yaml", "w", encoding="utf-8") as f:
        yaml.dump(discovery_state, f, default_flow_style=False)
    return project_with_config


@pytest.fixture
def mock_ai_provider():
    """Create a mock AI provider."""
    provider = MagicMock()
    provider.provider_name = "github-models"
    provider.default_model = "gpt-4o"
    provider.chat.return_value = make_ai_response()
    return provider


@pytest.fixture
def mock_agent_context(project_with_config, mock_ai_provider, sample_config):
    """Create a mock AgentContext."""
    from azext_prototype.agents.base import AgentContext

    return AgentContext(
        project_config=sample_config,
        project_dir=str(project_with_config),
        ai_provider=mock_ai_provider,
    )


@pytest.fixture
def populated_registry():
    """Create an agent registry with all built-in agents registered."""
    from azext_prototype.agents.registry import AgentRegistry
    from azext_prototype.agents.builtin import register_all_builtin

    registry = AgentRegistry()
    register_all_builtin(registry)
    return registry
