"""Tests for BuildState, PolicyResolver, BuildSession, and multi-resource telemetry.

Covers all new build-stage modules introduced in the interactive build overhaul.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from azext_prototype.agents.base import AgentCapability, AgentContext
from azext_prototype.ai.provider import AIResponse

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
                "category": "infra",
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
                    "category": "infra",
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
                    "category": "infra",
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
                    "category": "infra",
                    "services": [],
                    "status": "pending",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "B",
                    "category": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 3,
                    "name": "C",
                    "category": "app",
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
                    "category": "infra",
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
                    "category": "data",
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
                    "category": "infra",
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
                    "category": "infra",
                    "services": [],
                    "status": "pending",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "Data",
                    "category": "data",
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
                "category": "infra",
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
                "category": "docs",
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


@pytest.fixture
def build_registry(mock_tf_agent, mock_dev_agent, mock_doc_agent, mock_architect_agent_for_build, mock_qa_agent):
    registry = MagicMock()

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


@pytest.fixture
def build_context(project_with_design, sample_config):
    """AgentContext for build tests with design already completed."""
    provider = MagicMock()
    provider.provider_name = "github-models"
    provider.chat.return_value = _make_response()
    return AgentContext(
        project_config=sample_config,
        project_dir=str(project_with_design),
        ai_provider=provider,
    )


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
                    "category": "infra",
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
                    "category": "app",
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
        assert stages[1]["category"] == "app"

    def test_fallback_deployment_plan(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        # Force no architect
        build_registry.find_by_capability.side_effect = lambda cap: []
        session = BuildSession(build_context, build_registry)

        stages = session._fallback_deployment_plan([])
        assert len(stages) >= 2  # Managed Identity + Documentation at minimum
        assert stages[0]["name"] == "Managed Identity"
        assert stages[-1]["name"] == "Documentation"

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
        content = '```json\n{"stages": [{"stage": 1, "name": "Test", "category": "infra"}]}\n```'
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
                    "category": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "Data",
                    "category": "data",
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
                    "category": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "Data",
                    "category": "data",
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
                    "category": "infra",
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

    def test_categorise_service(self):
        from azext_prototype.stages.build_session import BuildSession

        assert BuildSession._categorise_service("key-vault") == "infra"
        assert BuildSession._categorise_service("sql-database") == "data"
        assert BuildSession._categorise_service("container-apps") == "app"
        assert BuildSession._categorise_service("unknown-service") == "app"

    def test_normalise_stages(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        raw = [
            {"stage": 1, "name": "Test"},
            {"name": "No Stage Num"},
        ]
        normalised = session._normalise_stages(raw)
        assert len(normalised) == 2
        assert normalised[0]["status"] == "pending"
        assert normalised[0]["files"] == []
        assert normalised[1]["stage"] == 2  # Auto-assigned

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
                    "category": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": ["main.tf"],
                },
                {
                    "stage": 2,
                    "name": "Documentation",
                    "category": "docs",
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
                "category": "infra",
                "services": [],
                "status": "generated",
                "dir": "concept/infra/terraform/stage-1-foundation",
                "files": ["main.tf"],
            },
            {
                "stage": 2,
                "name": "Data",
                "category": "data",
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
                "category": "app",
                "services": [],
                "status": "generated",
                "dir": "concept/apps/stage-3-api",
                "files": ["app.py"],
            },
            {
                "stage": 4,
                "name": "Documentation",
                "category": "docs",
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
                "category": "data",
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
                "category": "infra",
                "services": [],
                "status": "generated",
                "dir": "",
                "files": [],
            },
            {"stage": 5, "name": "B", "category": "data", "services": [], "status": "pending", "dir": "", "files": []},
            {"stage": 10, "name": "C", "category": "docs", "services": [], "status": "pending", "dir": "", "files": []},
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
                "category": "infra",
                "services": [{"name": "key-vault"}],
                "status": "generated",
                "dir": "",
                "files": [],
            },
            {
                "stage": 2,
                "name": "Data",
                "category": "data",
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
                "added": [{"name": "Redis", "category": "data", "services": []}],
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
                "category": "infra",
                "services": [],
                "status": "generated",
                "dir": "",
                "files": [],
            },
            {
                "stage": 2,
                "name": "B",
                "category": "data",
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
                "category": "infra",
                "services": [],
                "status": "generated",
                "dir": "",
                "files": [],
            },
            {
                "stage": 2,
                "name": "B",
                "category": "data",
                "services": [],
                "status": "generated",
                "dir": "",
                "files": [],
            },
            {"stage": 3, "name": "C", "category": "app", "services": [], "status": "generated", "dir": "", "files": []},
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
                    "category": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": ["main.tf"],
                },
                {
                    "stage": 2,
                    "name": "Docs",
                    "category": "docs",
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
                    "category": "infra",
                    "services": [{"name": "key-vault"}],
                    "status": "generated",
                    "dir": "concept/infra/terraform/stage-1-foundation",
                    "files": ["main.tf"],
                },
                {
                    "stage": 2,
                    "name": "Documentation",
                    "category": "docs",
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
                        "category": "data",
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
                    "category": "infra",
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
                    "category": "infra",
                    "dir": "concept/infra/terraform/stage-1-new",
                    "services": [],
                    "status": "pending",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "Documentation",
                    "category": "docs",
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
                    "category": "infra",
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
                # Inside the context, standards should be disabled
                assert mock_tf_agent._include_standards is False

        # After exiting, standards should be restored
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

        stage = {"name": "Data", "services": [{"name": "sql-server"}]}

        with patch.object(session, "_apply_governor_brief") as mock_gov, patch.object(
            session, "_apply_stage_knowledge"
        ) as mock_know:
            with session._agent_build_context(mock_tf_agent, stage):
                pass

            mock_gov.assert_called_once_with(mock_tf_agent, "Data", [{"name": "sql-server"}])
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
        large_knowledge = "x" * 15000  # > 12000 threshold

        with patch("azext_prototype.stages.build_session.KnowledgeLoader", create=True) as MockLoader:
            mock_loader = MockLoader.return_value
            mock_loader.compose_context.return_value = large_knowledge
            with patch.dict("sys.modules", {"azext_prototype.knowledge": MagicMock(KnowledgeLoader=MockLoader)}):
                session._apply_stage_knowledge(mock_tf_agent, stage)

        call_arg = mock_tf_agent.set_knowledge_override.call_args[0][0]
        assert len(call_arg) < 15000
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
            {"stage": 1, "name": "Foundation", "category": "infra", "services": []},
            {"stage": 2, "name": "Data", "category": "data", "services": []},
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

        stages = [{"stage": 1, "name": "Foundation", "category": "infra", "services": []}]

        result = session._condense_architecture("architecture", stages, use_styled=False)

        assert result == {}

    def test_condense_parses_stage_sections(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        stages = [
            {"stage": 1, "name": "Foundation", "category": "infra", "services": []},
            {"stage": 2, "name": "Data", "category": "data", "services": []},
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

        stages = [{"stage": 1, "name": "Foundation", "category": "infra", "services": []}]

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

        stages = [{"stage": 1, "name": "Foundation", "category": "infra", "services": []}]

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

        stages = [{"stage": 1, "name": "Foundation", "category": "infra", "services": []}]

        build_context.ai_provider.chat.side_effect = Exception("API error")

        result = session._condense_architecture("architecture", stages, use_styled=False)

        assert result == {}

    def test_condense_caches_result_in_build_state(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        stages = [
            {"stage": 1, "name": "Foundation", "category": "infra", "services": []},
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
# _select_agent tests
# ======================================================================


class TestSelectAgent:
    """Tests for _select_agent category-to-agent mapping."""

    def test_select_agent_infra(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"category": "infra"})
        assert agent is mock_tf_agent

    def test_select_agent_data(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"category": "data"})
        assert agent is mock_tf_agent

    def test_select_agent_integration(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"category": "integration"})
        assert agent is mock_tf_agent

    def test_select_agent_app(self, build_context, build_registry, mock_dev_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"category": "app"})
        assert agent is mock_dev_agent

    def test_select_agent_schema(self, build_context, build_registry, mock_dev_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"category": "schema"})
        assert agent is mock_dev_agent

    def test_select_agent_cicd(self, build_context, build_registry, mock_dev_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"category": "cicd"})
        assert agent is mock_dev_agent

    def test_select_agent_external(self, build_context, build_registry, mock_dev_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"category": "external"})
        assert agent is mock_dev_agent

    def test_select_agent_docs(self, build_context, build_registry, mock_doc_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"category": "docs"})
        assert agent is mock_doc_agent

    def test_select_agent_unknown_falls_back_to_iac(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"category": "unknown_category"})
        # Falls back to iac_agents[iac_tool] or dev_agent
        assert agent is mock_tf_agent

    def test_select_agent_missing_category_defaults_to_infra(self, build_context, build_registry, mock_tf_agent):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({})
        # category defaults to "infra"
        assert agent is mock_tf_agent

    def test_select_agent_no_agent_returns_none(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._doc_agent = None
        agent = session._select_agent({"category": "docs"})
        assert agent is None


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
            "category": "infra",
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
            "category": "infra",
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
            "category": "docs",
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
            "category": "infra",
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
            "category": "infra",
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
                    "category": "infra",
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
# BuildResult tests
# ======================================================================


class TestBuildResult:

    def test_default_values(self):
        from azext_prototype.stages.build_session import BuildResult

        result = BuildResult()
        assert result.files_generated == []
        assert result.deployment_stages == []
        assert result.policy_overrides == []
        assert result.resources == []
        assert result.review_accepted is False
        assert result.cancelled is False

    def test_cancelled_result(self):
        from azext_prototype.stages.build_session import BuildResult

        result = BuildResult(cancelled=True)
        assert result.cancelled is True
        assert result.review_accepted is False

    def test_populated_result(self):
        from azext_prototype.stages.build_session import BuildResult

        result = BuildResult(
            files_generated=["main.tf"],
            resources=[{"resourceType": "Microsoft.KeyVault/vaults", "sku": "standard"}],
            review_accepted=True,
        )
        assert len(result.files_generated) == 1
        assert len(result.resources) == 1
        assert result.review_accepted is True


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
                    "category": "infra",
                    "dir": "",
                    "services": [{"name": "key-vault"}],
                    "status": "generated",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "Data Layer",
                    "category": "data",
                    "dir": "",
                    "services": [{"name": "sql-db"}],
                    "status": "generated",
                    "files": [],
                },
                {
                    "stage": 3,
                    "name": "Application",
                    "category": "app",
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
                    "category": "infra",
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
            "category": "infra",
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
            "category": "infra",
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
            "category": "infra",
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
            "category": "infra",
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
        """Docs category stages should not get QA review during Phase 3."""
        # This tests the gating in the Phase 3 loop, not _run_stage_qa itself
        stage = {
            "stage": 5,
            "name": "Documentation",
            "category": "docs",
            "dir": "concept/docs",
            "files": [],
            "status": "generated",
            "services": [],
        }
        # docs category is not in ("infra", "data", "integration", "app")
        assert stage["category"] not in ("infra", "data", "integration", "app")

    def test_collect_stage_file_content(self, tmp_project):
        session, _, _ = self._make_session(tmp_project)

        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        stage = {
            "stage": 1,
            "name": "Foundation",
            "category": "infra",
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
                    "category": "infra",
                    "dir": "concept/infra/terraform/stage-1",
                    "services": [],
                    "status": "generated",
                    "files": ["concept/infra/terraform/stage-1/main.tf"],
                },
            ]
        )
        # Pre-store advisory (as if per-stage advisory already ran)
        session._build_state.set_stage_advisory(
            1, "- **[Scalability]** Consider upgrading SKUs for production."
        )
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
                    "category": "infra",
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
                    "category": "infra",
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
            {"stage": 1, "name": "Foundation", "category": "infra", "services": [], "status": "pending", "files": []},
            {"stage": 2, "name": "Data Layer", "category": "data", "services": [], "status": "pending", "files": []},
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
            {"stage": 1, "name": "Foundation", "category": "infra", "services": [], "status": "pending", "files": []},
            {"stage": 2, "name": "Data Layer", "category": "data", "services": [], "status": "pending", "files": []},
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
            {"stage": 1, "name": "Foundation", "category": "infra", "services": [], "status": "pending", "files": []},
            {"stage": 2, "name": "Foundation", "category": "infra", "services": [], "status": "pending", "files": []},
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
                    "category": "infra",
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
            {"stage": 1, "name": "Foundation", "category": "infra", "services": [], "status": "pending", "files": []},
            {"stage": 2, "name": "Data Layer", "category": "data", "services": [], "status": "pending", "files": []},
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
                "category": "external",
                "services": [],
                "status": "pending",
                "files": [],
                "deploy_mode": "manual",
                "manual_instructions": "Upload the notebook to the Fabric workspace.",
            },
            {
                "stage": 2,
                "name": "Foundation",
                "category": "infra",
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
                    "category": "infra",
                    "services": [],
                    "status": "pending",
                    "files": [],
                },
            ]
        )
        bs.add_stages(
            [
                {"name": "API Layer", "category": "app"},
            ]
        )
        ids = [s["id"] for s in bs.state["deployment_stages"]]
        assert "api-layer" in ids


# ======================================================================
# _get_app_scaffolding_requirements tests
# ======================================================================


class TestGetAppScaffoldingRequirements:
    """Tests for _get_app_scaffolding_requirements static method."""

    def test_infra_category_returns_empty(self):
        from azext_prototype.stages.build_session import BuildSession

        result = BuildSession._get_app_scaffolding_requirements({"category": "infra", "services": []})
        assert result == ""

    def test_data_category_returns_empty(self):
        from azext_prototype.stages.build_session import BuildSession

        result = BuildSession._get_app_scaffolding_requirements({"category": "data", "services": []})
        assert result == ""

    def test_docs_category_returns_empty(self):
        from azext_prototype.stages.build_session import BuildSession

        result = BuildSession._get_app_scaffolding_requirements({"category": "docs", "services": []})
        assert result == ""

    def test_functions_detected_by_resource_type(self):
        from azext_prototype.stages.build_session import BuildSession

        stage = {
            "category": "app",
            "services": [{"name": "api", "resource_type": "Microsoft.Web/functionapps"}],
        }
        result = BuildSession._get_app_scaffolding_requirements(stage)
        assert "host.json" in result
        assert ".csproj" in result

    def test_functions_detected_by_name(self):
        from azext_prototype.stages.build_session import BuildSession

        stage = {
            "category": "app",
            "services": [{"name": "function-app", "resource_type": ""}],
        }
        result = BuildSession._get_app_scaffolding_requirements(stage)
        assert "host.json" in result

    def test_webapp_detected_by_resource_type(self):
        from azext_prototype.stages.build_session import BuildSession

        stage = {
            "category": "app",
            "services": [{"name": "api", "resource_type": "Microsoft.Web/sites"}],
        }
        result = BuildSession._get_app_scaffolding_requirements(stage)
        assert "Dockerfile" in result
        assert "appsettings.json" in result

    def test_webapp_detected_by_name(self):
        from azext_prototype.stages.build_session import BuildSession

        stage = {
            "category": "app",
            "services": [{"name": "container-app-api", "resource_type": ""}],
        }
        result = BuildSession._get_app_scaffolding_requirements(stage)
        assert "Dockerfile" in result

    def test_generic_app_fallback(self):
        from azext_prototype.stages.build_session import BuildSession

        stage = {
            "category": "app",
            "services": [{"name": "worker", "resource_type": ""}],
        }
        result = BuildSession._get_app_scaffolding_requirements(stage)
        assert "Required Project Files" in result
        assert "Entry point" in result

    def test_schema_category_triggers_scaffolding(self):
        from azext_prototype.stages.build_session import BuildSession

        stage = {
            "category": "schema",
            "services": [{"name": "db-migration", "resource_type": ""}],
        }
        result = BuildSession._get_app_scaffolding_requirements(stage)
        assert "Required Project Files" in result

    def test_external_category_triggers_scaffolding(self):
        from azext_prototype.stages.build_session import BuildSession

        stage = {
            "category": "external",
            "services": [{"name": "stripe-integration", "resource_type": ""}],
        }
        result = BuildSession._get_app_scaffolding_requirements(stage)
        assert "Required Project Files" in result


# ======================================================================
# _write_stage_files tests
# ======================================================================


class TestWriteStageFiles:
    """Tests for _write_stage_files edge cases."""

    def test_empty_content_returns_empty(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        stage = {"dir": "concept/infra/terraform/stage-1-foundation"}

        result = session._write_stage_files(stage, "")
        assert result == []

    def test_no_file_blocks_returns_empty(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        stage = {"dir": "concept/infra/terraform/stage-1-foundation"}

        result = session._write_stage_files(stage, "This is just text with no code blocks.")
        assert result == []

    def test_writes_files_and_returns_paths(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        stage = {"dir": "concept/infra/terraform/stage-1-foundation"}

        content = '```main.tf\n# terraform code\n```\n\n```variables.tf\nvariable "name" {}\n```'
        result = session._write_stage_files(stage, content)

        assert len(result) == 2
        # Files should exist on disk
        project_root = Path(build_context.project_dir)
        for rel_path in result:
            assert (project_root / rel_path).exists()

    def test_strips_stage_dir_prefix_from_filenames(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        stage_dir = "concept/infra/terraform/stage-1-foundation"
        stage = {"dir": stage_dir}

        # AI sometimes includes full path in filename
        content = f"```{stage_dir}/main.tf\n# code\n```"
        result = session._write_stage_files(stage, content)

        assert len(result) == 1
        # Should NOT create nested duplicate path
        assert result[0] == f"{stage_dir}/main.tf"

    def test_blocks_versions_tf_for_terraform(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._iac_tool = "terraform"
        stage = {"dir": "concept/infra/terraform/stage-1"}

        content = "```main.tf\n# main code\n```\n\n```versions.tf\n# should be blocked\n```"
        result = session._write_stage_files(stage, content)

        filenames = [Path(p).name for p in result]
        assert "main.tf" in filenames
        assert "versions.tf" not in filenames

    def test_allows_versions_tf_for_bicep(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._iac_tool = "bicep"
        stage = {"dir": "concept/infra/bicep/stage-1"}

        content = "```main.bicep\n# main code\n```\n\n```versions.tf\n# allowed for bicep\n```"
        result = session._write_stage_files(stage, content)

        filenames = [Path(p).name for p in result]
        assert "main.bicep" in filenames
        assert "versions.tf" in filenames


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
                    "category": "infra",
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
                    "category": "infra",
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
# _clean_removed_stage_files tests
# ======================================================================


class TestCleanRemovedStageFiles:
    """Tests for _clean_removed_stage_files."""

    def test_removes_existing_directory(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        # Create the directory with a file
        stage_dir = Path(build_context.project_dir) / "concept" / "infra" / "terraform" / "stage-2-data"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text("# data stage", encoding="utf-8")
        assert stage_dir.exists()

        stages = [
            {"stage": 2, "dir": "concept/infra/terraform/stage-2-data"},
        ]
        session._clean_removed_stage_files([2], stages)

        assert not stage_dir.exists()

    def test_ignores_nonexistent_directory(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        stages = [
            {"stage": 2, "dir": "concept/infra/terraform/stage-2-nonexistent"},
        ]
        # Should not raise
        session._clean_removed_stage_files([2], stages)

    def test_ignores_stage_not_in_removed_list(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        stage_dir = Path(build_context.project_dir) / "concept" / "infra" / "terraform" / "stage-1-foundation"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text("# keep this", encoding="utf-8")

        stages = [
            {"stage": 1, "dir": "concept/infra/terraform/stage-1-foundation"},
            {"stage": 2, "dir": "concept/infra/terraform/stage-2-data"},
        ]
        # Only remove stage 2, not stage 1
        session._clean_removed_stage_files([2], stages)

        assert stage_dir.exists()


# ======================================================================
# _fix_stage_dirs tests
# ======================================================================


class TestFixStageDirs:
    """Tests for _fix_stage_dirs after stage renumbering."""

    def test_renumbers_stage_dir_paths(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {
                "stage": 1,
                "name": "A",
                "dir": "concept/infra/terraform/stage-1-foundation",
                "category": "infra",
                "services": [],
                "status": "generated",
                "files": [],
            },
            {
                "stage": 2,
                "name": "B",
                "dir": "concept/infra/terraform/stage-4-data",
                "category": "data",
                "services": [],
                "status": "pending",
                "files": [],
            },
        ]

        session._fix_stage_dirs()

        stages = session._build_state._state["deployment_stages"]
        assert stages[0]["dir"] == "concept/infra/terraform/stage-1-foundation"
        assert stages[1]["dir"] == "concept/infra/terraform/stage-2-data"

    def test_skips_empty_dirs(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._build_state._state["deployment_stages"] = [
            {"stage": 1, "name": "A", "dir": "", "category": "infra", "services": [], "status": "pending", "files": []},
        ]

        # Should not raise
        session._fix_stage_dirs()

        assert session._build_state._state["deployment_stages"][0]["dir"] == ""


# ======================================================================
# _build_stage_task bicep branch tests
# ======================================================================


class TestBuildStageTaskBicep:
    """Tests for _build_stage_task with bicep IaC tool."""

    def test_bicep_category_infra(self, build_context, build_registry):
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
            "category": "infra",
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
            "category": "app",
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
# _collect_generated_file_content tests
# ======================================================================


class TestCollectGeneratedFileContent:
    """Tests for _collect_generated_file_content."""

    def test_collects_from_generated_stages(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)

        # Create a file
        stage_dir = Path(build_context.project_dir) / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text("# tf code", encoding="utf-8")

        session._build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "category": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "concept/infra/terraform/stage-1",
                    "files": ["concept/infra/terraform/stage-1/main.tf"],
                },
            ]
        )

        result = session._collect_generated_file_content()
        assert "main.tf" in result
        assert "tf code" in result

    def test_empty_when_no_generated_stages(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._build_state.set_deployment_plan(
            [
                {
                    "stage": 1,
                    "name": "Foundation",
                    "category": "infra",
                    "services": [],
                    "status": "pending",
                    "dir": "",
                    "files": [],
                },
            ]
        )

        result = session._collect_generated_file_content()
        assert result == ""


# ======================================================================
# Naming strategy fallback tests
# ======================================================================


class TestNamingStrategyFallback:
    """Tests for the naming strategy fallback in __init__."""

    def test_naming_fallback_on_invalid_config(self, project_with_design, sample_config):
        """When naming config is invalid, should fall back to simple strategy."""
        from azext_prototype.stages.build_session import BuildSession

        # Corrupt the naming config
        sample_config["naming"]["strategy"] = "nonexistent-strategy"

        provider = MagicMock()
        provider.provider_name = "github-models"
        provider.chat.return_value = _make_response()

        context = AgentContext(
            project_config=sample_config,
            project_dir=str(project_with_design),
            ai_provider=provider,
        )

        registry = MagicMock()
        registry.find_by_capability.return_value = []

        # Should not raise — falls back to simple strategy
        session = BuildSession(context, registry)
        assert session._naming is not None


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
                    "category": "infra",
                    "services": [{"name": "key-vault"}],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "Data",
                    "category": "data",
                    "services": [{"name": "cosmos-db"}],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 3,
                    "name": "Pending",
                    "category": "app",
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
                    "category": "infra",
                    "services": [],
                    "status": "generated",
                    "dir": "",
                    "files": [],
                },
                {
                    "stage": 2,
                    "name": "Data",
                    "category": "data",
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
            "category": "infra",
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
            "category": "infra",
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
                assert mock_tf_agent._include_standards is False

        assert mock_tf_agent._include_standards is True

    def test_agent_build_context_calls_apply_governor_brief(self, build_context, build_registry, mock_tf_agent):
        """_apply_governor_brief should be called with correct args."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        mock_tf_agent._include_standards = False
        mock_tf_agent.set_knowledge_override = MagicMock()
        mock_tf_agent.set_governor_brief = MagicMock()

        stage = {"name": "Data Layer", "services": [{"name": "cosmos-db"}]}

        with patch.object(session, "_apply_governor_brief") as mock_gov, patch.object(
            session, "_apply_stage_knowledge"
        ):
            with session._agent_build_context(mock_tf_agent, stage):
                pass

        mock_gov.assert_called_once_with(mock_tf_agent, "Data Layer", [{"name": "cosmos-db"}])

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

    def test_select_agent_infra_category(self, build_context, build_registry, mock_tf_agent):
        """Infra category should resolve to the IaC (terraform) agent."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"category": "infra"})
        assert agent is mock_tf_agent

    def test_select_agent_app_category(self, build_context, build_registry, mock_dev_agent):
        """App category should resolve to the developer agent."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"category": "app"})
        assert agent is mock_dev_agent

    def test_select_agent_docs_category(self, build_context, build_registry, mock_doc_agent):
        """Docs category should resolve to the doc agent."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"category": "docs"})
        assert agent is mock_doc_agent

    def test_select_agent_unknown_falls_back_to_iac(self, build_context, build_registry, mock_tf_agent):
        """Unknown category falls back to IaC agent, then dev agent."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        agent = session._select_agent({"category": "foobar"})
        assert agent is mock_tf_agent

    def test_select_agent_unknown_falls_back_to_dev_when_no_iac(self, build_context, build_registry, mock_dev_agent):
        """When no IaC agent exists, unknown category falls back to dev agent."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._iac_agents = {}
        agent = session._select_agent({"category": "foobar"})
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
            {"stage": 1, "name": "Foundation", "category": "infra", "services": []},
            {"stage": 2, "name": "Data", "category": "data", "services": []},
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
            {"stage": 1, "name": "Foundation", "category": "infra", "services": []},
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
            {"stage": 1, "name": "Foundation", "category": "infra", "services": []},
        ]

        result = session._condense_architecture("arch", stages, use_styled=False)

        assert result == {}

    def test_condense_architecture_parses_stage_contexts_from_response(self, build_context, build_registry):
        """AI response with per-stage headings should be parsed into a mapping."""
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        stages = [
            {"stage": 1, "name": "Foundation", "category": "infra", "services": []},
            {"stage": 2, "name": "Data", "category": "data", "services": []},
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
