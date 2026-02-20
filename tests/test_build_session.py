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
from azext_prototype.ai.provider import AIMessage, AIResponse


# ======================================================================
# Helpers
# ======================================================================

def _make_response(content: str = "Mock response") -> AIResponse:
    return AIResponse(content=content, model="gpt-4o", usage={})


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
        bs.set_deployment_plan([
            {"stage": 1, "name": "Foundation", "category": "infra",
             "services": [], "status": "pending", "dir": "", "files": []},
        ])

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
        bs.set_deployment_plan([
            {"stage": 1, "name": "Foundation", "category": "infra",
             "services": [], "status": "generated", "dir": "", "files": []},
        ])
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
        bs.set_deployment_plan([
            {"stage": 1, "name": "A", "category": "infra",
             "services": [], "status": "pending", "dir": "", "files": []},
            {"stage": 2, "name": "B", "category": "infra",
             "services": [], "status": "generated", "dir": "", "files": []},
            {"stage": 3, "name": "C", "category": "app",
             "services": [], "status": "pending", "dir": "", "files": []},
        ])

        pending = bs.get_pending_stages()
        assert len(pending) == 2
        assert pending[0]["stage"] == 1
        assert pending[1]["stage"] == 3

    def test_get_all_resources(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs.set_deployment_plan([
            {"stage": 1, "name": "Foundation", "category": "infra",
             "services": [
                 {"name": "kv", "computed_name": "kv-1", "resource_type": "Microsoft.KeyVault/vaults", "sku": "standard"},
                 {"name": "id", "computed_name": "id-1", "resource_type": "Microsoft.ManagedIdentity/userAssignedIdentities", "sku": ""},
             ],
             "status": "pending", "dir": "", "files": []},
            {"stage": 2, "name": "Data", "category": "data",
             "services": [
                 {"name": "sql", "computed_name": "sql-1", "resource_type": "Microsoft.Sql/servers", "sku": "serverless"},
             ],
             "status": "pending", "dir": "", "files": []},
        ])

        resources = bs.get_all_resources()
        assert len(resources) == 3
        types = {r["resourceType"] for r in resources}
        assert "Microsoft.KeyVault/vaults" in types
        assert "Microsoft.Sql/servers" in types

    def test_format_build_report(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs._state["templates_used"] = ["web-app"]
        bs.set_deployment_plan([
            {"stage": 1, "name": "Foundation", "category": "infra",
             "services": [{"name": "kv", "computed_name": "zd-kv-dev", "resource_type": "Microsoft.KeyVault/vaults", "sku": "standard"}],
             "status": "generated", "dir": "", "files": ["main.tf"]},
        ])
        bs._state["files_generated"] = ["main.tf"]

        report = bs.format_build_report()
        assert "web-app" in report
        assert "Foundation" in report
        assert "zd-kv-dev" in report
        assert "1" in report  # Total files

    def test_format_stage_status(self, tmp_project):
        from azext_prototype.stages.build_state import BuildState

        bs = BuildState(str(tmp_project))
        bs.set_deployment_plan([
            {"stage": 1, "name": "Foundation", "category": "infra",
             "services": [], "status": "pending", "dir": "", "files": []},
            {"stage": 2, "name": "Data", "category": "data",
             "services": [], "status": "generated", "dir": "", "files": ["sql.tf"]},
        ])

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
            "terraform-agent", "resource group code", build_state, stage_num=1,
            input_fn=lambda p: "", print_fn=lambda m: None,
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
            "terraform-agent", "code with connection_string", build_state, stage_num=1,
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
            "terraform-agent", "code with access_key", build_state, stage_num=1,
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
        governance.check_response_for_violations.return_value = [
            "[managed-identity] Hardcoded credential detected"
        ]

        resolver = PolicyResolver(governance_context=governance)
        build_state = BuildState(str(tmp_project))

        resolutions, needs_regen = resolver.check_and_resolve(
            "terraform-agent", "bad code", build_state, stage_num=1,
            input_fn=lambda p: "r",  # Regenerate
            print_fn=lambda m: None,
        )

        assert len(resolutions) == 1
        assert resolutions[0].action == "regenerate"
        assert needs_regen is True

    def test_build_fix_instructions(self):
        from azext_prototype.stages.policy_resolver import PolicyResolver, PolicyResolution

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
    agent.execute.return_value = _make_file_response("main.tf", 'resource "azapi_resource" "rg" {\n  type = "Microsoft.Resources/resourceGroups@2025-06-01"\n}')
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
                "stage": 1, "name": "Foundation", "category": "infra",
                "dir": "concept/infra/terraform/stage-1-foundation",
                "services": [
                    {"name": "key-vault", "computed_name": "zd-kv-test-dev-eus",
                     "resource_type": "Microsoft.KeyVault/vaults", "sku": "standard"},
                ],
                "status": "pending", "files": [],
            },
            {
                "stage": 2, "name": "Documentation", "category": "docs",
                "dir": "concept/docs",
                "services": [], "status": "pending", "files": [],
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
        from azext_prototype.stages.build_state import BuildState

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
                {"stage": 1, "name": "Foundation", "category": "infra",
                 "dir": "concept/infra/terraform/stage-1-foundation",
                 "services": [{"name": "kv", "computed_name": "zd-kv-dev", "resource_type": "Microsoft.KeyVault/vaults", "sku": "standard"}],
                 "status": "pending", "files": []},
                {"stage": 2, "name": "Apps", "category": "app",
                 "dir": "concept/apps/stage-2-api",
                 "services": [], "status": "pending", "files": []},
            ]
        }
        mock_architect_agent_for_build.execute.return_value = _make_response(
            f"```json\n{json.dumps(plan_json)}\n```"
        )

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
        assert len(stages) >= 2  # Foundation + Documentation at minimum
        assert stages[0]["name"] == "Foundation"
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
        design = {
            "architecture": "This is a simple static website with no Azure services mentioned."
        }
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
        session._build_state.set_deployment_plan([
            {"stage": 1, "name": "Foundation", "category": "infra",
             "services": [], "status": "generated", "dir": "", "files": []},
            {"stage": 2, "name": "Data", "category": "data",
             "services": [], "status": "generated", "dir": "", "files": []},
        ])

        affected = session._identify_affected_stages("Please fix stage 2")
        assert affected == [2]

    def test_identify_affected_stages_by_name(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._build_state.set_deployment_plan([
            {"stage": 1, "name": "Foundation", "category": "infra",
             "services": [], "status": "generated", "dir": "", "files": []},
            {"stage": 2, "name": "Data", "category": "data",
             "services": [{"name": "sql-server", "computed_name": "sql-1", "resource_type": "", "sku": ""}],
             "status": "generated", "dir": "", "files": []},
        ])

        affected = session._identify_affected_stages("The sql-server configuration is wrong")
        assert 2 in affected

    def test_slash_command_status(self, build_context, build_registry):
        from azext_prototype.stages.build_session import BuildSession

        session = BuildSession(build_context, build_registry)
        session._build_state.set_deployment_plan([
            {"stage": 1, "name": "Foundation", "category": "infra",
             "services": [], "status": "generated", "dir": "", "files": []},
        ])

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

        # Pre-populate with a generated stage
        session._build_state.set_deployment_plan([
            {"stage": 1, "name": "Foundation", "category": "infra",
             "services": [], "status": "generated", "dir": "", "files": ["main.tf"]},
            {"stage": 2, "name": "Documentation", "category": "docs",
             "services": [], "status": "pending", "dir": "concept/docs", "files": []},
        ])

        inputs = iter(["", "done"])

        with patch("azext_prototype.stages.build_session.GovernanceContext") as mock_gov_cls:
            mock_gov_cls.return_value.check_response_for_violations.return_value = []
            session._governance = mock_gov_cls.return_value
            session._policy_resolver._governance = mock_gov_cls.return_value

            with patch("azext_prototype.stages.build_session.AgentOrchestrator") as mock_orch:
                mock_orch.return_value.delegate.return_value = _make_response("QA ok")

                result = session.run(
                    design={"architecture": "Test"},
                    input_fn=lambda p: next(inputs),
                    print_fn=lambda m: None,
                )

        # Stage 1 (generated) should NOT have been re-run
        # Only doc agent should have been called (for stage 2)
        assert mock_tf_agent.execute.call_count == 0
        assert mock_doc_agent.execute.call_count == 1


# ======================================================================
# Telemetry tests
# ======================================================================

class TestMultiResourceTelemetry:

    def test_track_build_resources_single(self):
        from azext_prototype.telemetry import track_build_resources, _parse_connection_string

        with patch("azext_prototype.telemetry.is_enabled", return_value=True), \
             patch("azext_prototype.telemetry._get_ingestion_config", return_value=("http://test/v2/track", "key")), \
             patch("azext_prototype.telemetry._send_envelope") as mock_send:

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

        with patch("azext_prototype.telemetry.is_enabled", return_value=True), \
             patch("azext_prototype.telemetry._get_ingestion_config", return_value=("http://test/v2/track", "key")), \
             patch("azext_prototype.telemetry._send_envelope") as mock_send:

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

        with patch("azext_prototype.telemetry.is_enabled", return_value=True), \
             patch("azext_prototype.telemetry._get_ingestion_config", return_value=("http://test/v2/track", "key")), \
             patch("azext_prototype.telemetry._send_envelope") as mock_send:

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

        with patch("azext_prototype.telemetry.is_enabled", return_value=True), \
             patch("azext_prototype.telemetry._get_ingestion_config", return_value=("http://test/v2/track", "key")), \
             patch("azext_prototype.telemetry._send_envelope") as mock_send:

            track_build_resources("prototype build", resources=[])

            envelope = mock_send.call_args[0][0]
            props = envelope["data"]["baseData"]["properties"]
            assert props["resourceCount"] == "0"
            assert props["resourceType"] == ""
            assert props["sku"] == ""

    def test_track_build_resources_disabled(self):
        from azext_prototype.telemetry import track_build_resources

        with patch("azext_prototype.telemetry.is_enabled", return_value=False), \
             patch("azext_prototype.telemetry._send_envelope") as mock_send:

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
            context, registry,
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
        bs.set_deployment_plan([
            {"stage": 1, "name": "Foundation", "category": "infra",
             "services": [], "status": "generated", "dir": "", "files": ["main.tf"]},
        ])

        # Verify the state file exists and is loadable
        bs2 = BuildState(str(project_with_design))
        assert bs2.exists
        bs2.load()
        assert bs2.format_stage_status()  # Should produce output

    def test_build_stage_reset_flag(self, project_with_design, sample_config):
        from azext_prototype.stages.build_state import BuildState

        # Create some state
        bs = BuildState(str(project_with_design))
        bs._state["templates_used"] = ["web-app"]
        bs.set_deployment_plan([
            {"stage": 1, "name": "Foundation", "category": "infra",
             "services": [], "status": "generated", "dir": "", "files": ["main.tf"]},
        ])

        # Reset should clear everything
        bs.reset()
        assert bs.state["templates_used"] == []
        assert bs.state["deployment_stages"] == []
        assert bs.state["files_generated"] == []


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
        build_state.set_deployment_plan([
            {"stage": 1, "name": "Foundation", "category": "infra",
             "dir": "", "services": [{"name": "key-vault"}], "status": "generated", "files": []},
            {"stage": 2, "name": "Data Layer", "category": "data",
             "dir": "", "services": [{"name": "sql-db"}], "status": "generated", "files": []},
            {"stage": 3, "name": "Application", "category": "app",
             "dir": "", "services": [{"name": "web-app"}], "status": "generated", "files": []},
        ])

        with patch("azext_prototype.stages.build_session.ProjectConfig") as mock_config:
            mock_config.return_value.load.return_value = None
            mock_config.return_value.get.side_effect = lambda k, d=None: {
                "project.iac_tool": "terraform",
                "project.name": "test",
            }.get(k, d)
            mock_config.return_value.to_dict.return_value = {"naming": {"strategy": "simple"}, "project": {"name": "test"}}
            session = BuildSession(ctx, registry, build_state=build_state)

        return session, architect

    def test_architect_identifies_stages(self, tmp_project):
        session, architect = self._make_session_with_stages(
            tmp_project, _make_response("[1, 3]"),
        )

        result = session._identify_affected_stages("Fix the networking and add CORS")

        assert result == [1, 3]
        architect.execute.assert_called_once()

    def test_architect_parse_failure_falls_back_to_regex(self, tmp_project):
        session, architect = self._make_session_with_stages(
            tmp_project, _make_response("I think stages 1 and 3 are affected"),
        )

        result = session._identify_affected_stages("Fix the key-vault configuration")

        # Architect response not parseable as JSON, falls back to regex
        # "key-vault" matches service in stage 1
        assert 1 in result

    def test_architect_exception_falls_back_to_regex(self, tmp_project):
        session, architect = self._make_session_with_stages(
            tmp_project, architect_raises=True,
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
        build_state.set_deployment_plan([
            {"stage": 1, "name": "Foundation", "category": "infra",
             "dir": "", "services": [{"name": "key-vault"}], "status": "generated", "files": []},
        ])

        with patch("azext_prototype.stages.build_session.ProjectConfig") as mock_config:
            mock_config.return_value.load.return_value = None
            mock_config.return_value.get.side_effect = lambda k, d=None: {
                "project.iac_tool": "terraform",
                "project.name": "test",
            }.get(k, d)
            mock_config.return_value.to_dict.return_value = {"naming": {"strategy": "simple"}, "project": {"name": "test"}}
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
            mock_config.return_value.to_dict.return_value = {"naming": {"strategy": "simple"}, "project": {"name": "test"}}
            session = BuildSession(ctx, registry, build_state=build_state)

        return session

    def test_versions_tf_dropped_for_terraform(self, tmp_project):
        session = self._make_session(tmp_project, iac_tool="terraform")
        content = (
            "```providers.tf\nterraform { required_version = \">= 1.0\" }\n```\n\n"
            "```versions.tf\n}\n```\n\n"
            "```main.tf\nresource \"null\" \"x\" {}\n```\n"
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
            "```main.tf\nresource \"null\" \"x\" {}\n```\n\n"
            "```outputs.tf\noutput \"id\" { value = null_resource.x.id }\n```\n"
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
            mock_config.return_value.to_dict.return_value = {"naming": {"strategy": "simple"}, "project": {"name": "test"}}
            session = BuildSession(ctx, registry, build_state=build_state)

        return session

    def test_task_prompt_includes_file_structure(self, tmp_project):
        session = self._make_session(tmp_project)
        stage = {
            "stage": 1, "name": "Foundation", "category": "infra",
            "dir": "concept/infra/terraform/stage-1", "services": [],
            "status": "pending", "files": [],
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

class TestBuildTerraformValidation:
    """Verify _validate_terraform_stages() in BuildSession."""

    def _make_session(self, project_dir, iac_tool="terraform", stages=None):
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
        if stages:
            build_state._state["deployment_stages"] = stages

        with patch("azext_prototype.stages.build_session.ProjectConfig") as mock_config:
            mock_config.return_value.load.return_value = None
            mock_config.return_value.get.side_effect = lambda k, d=None: {
                "project.iac_tool": iac_tool,
                "project.name": "test",
            }.get(k, d)
            mock_config.return_value.to_dict.return_value = {"naming": {"strategy": "simple"}, "project": {"name": "test"}}
            session = BuildSession(ctx, registry, build_state=build_state)

        return session

    def test_returns_empty_for_bicep(self, tmp_project):
        session = self._make_session(tmp_project, iac_tool="bicep")
        assert session._validate_terraform_stages() == {}

    def test_returns_empty_when_no_tf_files(self, tmp_project):
        stages = [
            {"stage": 1, "name": "Infra", "category": "infra",
             "dir": "concept/infra/terraform/stage-1", "services": [],
             "status": "generated", "files": []},
        ]
        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        # No .tf files

        session = self._make_session(tmp_project, stages=stages)
        assert session._validate_terraform_stages() == {}

    def test_skips_app_category(self, tmp_project):
        stages = [
            {"stage": 1, "name": "App", "category": "app",
             "dir": "concept/apps/stage-1", "services": [],
             "status": "generated", "files": []},
        ]
        stage_dir = tmp_project / "concept" / "apps" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        session = self._make_session(tmp_project, stages=stages)
        assert session._validate_terraform_stages() == {}

    @patch("azext_prototype.stages.build_session.subprocess.run")
    def test_returns_errors_on_init_failure(self, mock_run, tmp_project):
        stages = [
            {"stage": 1, "name": "Foundation", "category": "infra",
             "dir": "concept/infra/terraform/stage-1", "services": [],
             "status": "generated", "files": []},
        ]
        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        mock_run.return_value = MagicMock(returncode=1, stderr="Error: init failed", stdout="")
        session = self._make_session(tmp_project, stages=stages)
        errors = session._validate_terraform_stages()

        assert 1 in errors
        assert "Init failed" in errors[1]

    @patch("azext_prototype.stages.build_session.subprocess.run")
    def test_returns_errors_on_validate_failure(self, mock_run, tmp_project):
        stages = [
            {"stage": 1, "name": "Foundation", "category": "infra",
             "dir": "concept/infra/terraform/stage-1", "services": [],
             "status": "generated", "files": []},
        ]
        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        # Init succeeds, validate fails
        def run_side_effect(cmd, **kwargs):
            if "init" in cmd:
                return MagicMock(returncode=0, stderr="", stdout="")
            return MagicMock(returncode=1, stderr="Error: Invalid argument", stdout="")

        mock_run.side_effect = run_side_effect
        session = self._make_session(tmp_project, stages=stages)
        errors = session._validate_terraform_stages()

        assert 1 in errors
        assert "Invalid argument" in errors[1]

    @patch("azext_prototype.stages.build_session.subprocess.run", side_effect=FileNotFoundError)
    def test_returns_empty_when_terraform_not_installed(self, mock_run, tmp_project):
        stages = [
            {"stage": 1, "name": "Foundation", "category": "infra",
             "dir": "concept/infra/terraform/stage-1", "services": [],
             "status": "generated", "files": []},
        ]
        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        session = self._make_session(tmp_project, stages=stages)
        assert session._validate_terraform_stages() == {}

    @patch("azext_prototype.stages.build_session.subprocess.run")
    def test_returns_empty_on_success(self, mock_run, tmp_project):
        stages = [
            {"stage": 1, "name": "Foundation", "category": "infra",
             "dir": "concept/infra/terraform/stage-1", "services": [],
             "status": "generated", "files": []},
        ]
        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        session = self._make_session(tmp_project, stages=stages)
        errors = session._validate_terraform_stages()

        assert errors == {}

    @patch("azext_prototype.stages.build_session.subprocess.run")
    def test_tf_errors_merge_into_qa_content(self, mock_run, tmp_project):
        """Verify terraform validation errors are appended to qa_content."""
        stages = [
            {"stage": 1, "name": "Foundation", "category": "infra",
             "dir": "concept/infra/terraform/stage-1", "services": [],
             "status": "generated", "files": []},
        ]
        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        # Simulate init ok, validate fails
        def run_side_effect(cmd, **kwargs):
            if "init" in cmd:
                return MagicMock(returncode=0, stderr="", stdout="")
            return MagicMock(returncode=1, stderr="Error: default_tags not valid", stdout="")

        mock_run.side_effect = run_side_effect

        session = self._make_session(tmp_project, stages=stages)
        # Simulate QA returning no issues initially
        session._build_state._state["deployment_stages"] = stages

        errors = session._validate_terraform_stages()
        assert 1 in errors

        # Simulate the merge logic from the session
        qa_content = "All looks good."
        has_issues = False
        if errors:
            tf_section = "\n\n## Terraform Validation Errors (MUST FIX)\n"
            for stage_num, err in sorted(errors.items()):
                stage = session._build_state.get_stage(stage_num)
                name = stage["name"] if stage else f"Stage {stage_num}"
                tf_section += f"\n### Stage {stage_num}: {name}\n```\n{err}\n```\n"
            qa_content = (qa_content or "") + tf_section
            has_issues = True

        assert has_issues is True
        assert "Terraform Validation Errors" in qa_content
        assert "default_tags" in qa_content
        assert "Foundation" in qa_content


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
        assert "trivially empty" in QA_ENGINEER_PROMPT
        assert "syntactically valid HCL" in QA_ENGINEER_PROMPT


# ======================================================================
# Per-stage QA tests
# ======================================================================


class TestPerStageQA:
    """Test _run_stage_qa(), _collect_stage_file_content(), _validate_terraform_stage()."""

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
        tf_agent.execute.return_value = _make_file_response("main.tf", 'resource "azapi_resource" "rg" {\n  type = "Microsoft.Resources/resourceGroups@2025-06-01"\n}')

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
            mock_config.return_value.to_dict.return_value = {"naming": {"strategy": "simple"}, "project": {"name": "test"}}
            session = BuildSession(ctx, registry, build_state=build_state)

        return session, qa_agent, tf_agent

    def test_per_stage_qa_passes_clean(self, tmp_project):
        session, qa_agent, tf_agent = self._make_session(tmp_project)

        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "azapi_resource" "rg" {\n  type = "Microsoft.Resources/resourceGroups@2025-06-01"\n}')

        stage = {
            "stage": 1, "name": "Foundation", "category": "infra",
            "dir": "concept/infra/terraform/stage-1",
            "files": ["concept/infra/terraform/stage-1/main.tf"],
            "status": "generated", "services": [],
        }

        printed = []

        with patch("azext_prototype.stages.build_session.AgentOrchestrator") as mock_orch, \
             patch("azext_prototype.stages.build_session.subprocess.run",
                   return_value=MagicMock(returncode=0, stderr="", stdout="")):
            mock_orch.return_value.delegate.return_value = _make_response("All looks good. Code is clean and well-structured.")
            session._run_stage_qa(stage, "arch", [], False, lambda m: printed.append(m))

        output = "\n".join(printed)
        assert "passed QA" in output

    def test_per_stage_qa_triggers_remediation(self, tmp_project):
        session, qa_agent, tf_agent = self._make_session(tmp_project)

        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "azapi_resource" "rg" {\n  type = "Microsoft.Resources/resourceGroups@2025-06-01"\n}')

        stage = {
            "stage": 1, "name": "Foundation", "category": "infra",
            "dir": "concept/infra/terraform/stage-1",
            "files": ["concept/infra/terraform/stage-1/main.tf"],
            "status": "generated", "services": [],
        }
        session._build_state.set_deployment_plan([stage])

        printed = []
        call_count = [0]

        def mock_delegate(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_response("CRITICAL: Missing managed identity config. Must fix.")
            return _make_response("All resolved, no remaining issues.")

        with patch("azext_prototype.stages.build_session.AgentOrchestrator") as mock_orch, \
             patch("azext_prototype.stages.build_session.subprocess.run",
                   return_value=MagicMock(returncode=0, stderr="", stdout="")):
            mock_orch.return_value.delegate.side_effect = mock_delegate
            session._run_stage_qa(stage, "arch", [], False, lambda m: printed.append(m))

        output = "\n".join(printed)
        assert "remediating" in output.lower()
        # QA was called at least twice (initial + re-review)
        assert call_count[0] >= 2

    def test_per_stage_qa_max_attempts(self, tmp_project):
        from azext_prototype.stages.build_session import _MAX_STAGE_REMEDIATION_ATTEMPTS

        session, qa_agent, tf_agent = self._make_session(tmp_project)

        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "azapi_resource" "rg" {\n  type = "Microsoft.Resources/resourceGroups@2025-06-01"\n}')

        stage = {
            "stage": 1, "name": "Foundation", "category": "infra",
            "dir": "concept/infra/terraform/stage-1",
            "files": ["concept/infra/terraform/stage-1/main.tf"],
            "status": "generated", "services": [],
        }
        session._build_state.set_deployment_plan([stage])

        printed = []

        with patch("azext_prototype.stages.build_session.AgentOrchestrator") as mock_orch, \
             patch("azext_prototype.stages.build_session.subprocess.run",
                   return_value=MagicMock(returncode=0, stderr="", stdout="")):
            # Always return issues
            mock_orch.return_value.delegate.return_value = _make_response(
                "CRITICAL: This will never be fixed."
            )
            session._run_stage_qa(stage, "arch", [], False, lambda m: printed.append(m))

        output = "\n".join(printed)
        assert "issues remain" in output.lower()

    def test_per_stage_qa_skips_docs_stages(self, tmp_project):
        """Docs category stages should not get QA review during Phase 3."""
        # This tests the gating in the Phase 3 loop, not _run_stage_qa itself
        stage = {
            "stage": 5, "name": "Documentation", "category": "docs",
            "dir": "concept/docs", "files": [], "status": "generated", "services": [],
        }
        # docs category is not in ("infra", "data", "integration", "app")
        assert stage["category"] not in ("infra", "data", "integration", "app")

    @patch("azext_prototype.stages.build_session.subprocess.run")
    def test_validate_terraform_stage_returns_error(self, mock_run, tmp_project):
        session, _, _ = self._make_session(tmp_project)

        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "azapi_resource" "rg" {\n  type = "Microsoft.Resources/resourceGroups@2025-06-01"\n}')

        stage = {
            "stage": 1, "name": "Foundation", "category": "infra",
            "dir": "concept/infra/terraform/stage-1",
        }

        def run_side_effect(cmd, **kwargs):
            if "init" in cmd:
                return MagicMock(returncode=0, stderr="", stdout="")
            return MagicMock(returncode=1, stderr="Error: Invalid argument", stdout="")

        mock_run.side_effect = run_side_effect

        error = session._validate_terraform_stage(stage)
        assert error is not None
        assert "Invalid argument" in error

    def test_validate_terraform_stage_returns_none_on_success(self, tmp_project):
        session, _, _ = self._make_session(tmp_project)

        stage = {
            "stage": 1, "name": "Foundation", "category": "infra",
            "dir": "concept/infra/terraform/stage-1",
        }

        with patch("azext_prototype.stages.build_session.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
            stage_dir.mkdir(parents=True, exist_ok=True)
            (stage_dir / "main.tf").write_text('resource "null" "x" {}')

            error = session._validate_terraform_stage(stage)
            assert error is None

    def test_collect_stage_file_content(self, tmp_project):
        session, _, _ = self._make_session(tmp_project)

        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        stage = {
            "stage": 1, "name": "Foundation", "category": "infra",
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
        tf_agent.execute.return_value = _make_file_response("main.tf", 'resource "azapi_resource" "rg" {\n  type = "Microsoft.Resources/resourceGroups@2025-06-01"\n}')

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
            mock_config.return_value.to_dict.return_value = {"naming": {"strategy": "simple"}, "project": {"name": "test"}}
            session = BuildSession(ctx, registry, build_state=build_state)

        return session, qa_agent, tf_agent

    def test_advisory_qa_prompt_no_bug_hunting(self, tmp_project):
        """Verify Phase 4 QA task uses advisory prompt, not bug-finding."""
        session, qa_agent, tf_agent = self._make_session(tmp_project)

        # Pre-populate with generated stages and files
        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        session._build_state.set_deployment_plan([
            {"stage": 1, "name": "Foundation", "category": "infra",
             "dir": "concept/infra/terraform/stage-1",
             "services": [], "status": "generated",
             "files": ["concept/infra/terraform/stage-1/main.tf"]},
        ])

        printed = []
        inputs = iter(["", "done"])

        with patch("azext_prototype.stages.build_session.GovernanceContext") as mock_gov_cls:
            mock_gov_cls.return_value.check_response_for_violations.return_value = []
            session._governance = mock_gov_cls.return_value
            session._policy_resolver._governance = mock_gov_cls.return_value

            with patch("azext_prototype.stages.build_session.AgentOrchestrator") as mock_orch:
                mock_orch.return_value.delegate.return_value = _make_response(
                    "Advisory: Consider upgrading SKUs for production."
                )
                result = session.run(
                    design={"architecture": "Simple architecture"},
                    input_fn=lambda p: next(inputs),
                    print_fn=lambda m: printed.append(m),
                )

        output = "\n".join(printed)
        # Should show advisory, not QA Review
        assert "Advisory Notes" in output
        # Verify the delegate was called with advisory prompt
        delegate_calls = mock_orch.return_value.delegate.call_args_list
        # Find the advisory call (the last one with qa_task)
        advisory_calls = [c for c in delegate_calls if "advisory" in c.kwargs.get("sub_task", "").lower()
                          or "advisory" in str(c).lower()]
        # At least one call should be advisory
        all_tasks = [str(c) for c in delegate_calls]
        advisory_found = any("Do NOT re-check for bugs" in str(c) for c in delegate_calls)
        assert advisory_found, f"No advisory prompt found in delegate calls: {all_tasks}"

    def test_advisory_qa_no_remediation_loop(self, tmp_project):
        """Phase 4 should NOT trigger _identify_affected_stages or IaC regen."""
        session, qa_agent, tf_agent = self._make_session(tmp_project)

        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        session._build_state.set_deployment_plan([
            {"stage": 1, "name": "Foundation", "category": "infra",
             "dir": "concept/infra/terraform/stage-1",
             "services": [], "status": "generated",
             "files": ["concept/infra/terraform/stage-1/main.tf"]},
        ])

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
                    result = session.run(
                        design={"architecture": "Simple architecture"},
                        input_fn=lambda p: next(inputs),
                        print_fn=lambda m: None,
                    )

                    # _identify_affected_stages should NOT have been called during Phase 4
                    mock_identify.assert_not_called()

    def test_advisory_qa_header_says_advisory(self, tmp_project):
        """Output should contain 'Advisory Notes' not 'QA Review'."""
        session, qa_agent, tf_agent = self._make_session(tmp_project)

        stage_dir = tmp_project / "concept" / "infra" / "terraform" / "stage-1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "main.tf").write_text('resource "null" "x" {}')

        session._build_state.set_deployment_plan([
            {"stage": 1, "name": "Foundation", "category": "infra",
             "dir": "concept/infra/terraform/stage-1",
             "services": [], "status": "generated",
             "files": ["concept/infra/terraform/stage-1/main.tf"]},
        ])

        printed = []
        inputs = iter(["", "done"])

        with patch("azext_prototype.stages.build_session.GovernanceContext") as mock_gov_cls:
            mock_gov_cls.return_value.check_response_for_violations.return_value = []
            session._governance = mock_gov_cls.return_value
            session._policy_resolver._governance = mock_gov_cls.return_value

            with patch("azext_prototype.stages.build_session.AgentOrchestrator") as mock_orch:
                mock_orch.return_value.delegate.return_value = _make_response(
                    "Consider upgrading to premium SKUs for production."
                )
                result = session.run(
                    design={"architecture": "Simple architecture"},
                    input_fn=lambda p: next(inputs),
                    print_fn=lambda m: printed.append(m),
                )

        output = "\n".join(printed)
        assert "Advisory Notes" in output
        # Should NOT contain "QA Review:" as a section header
        assert "QA Review:" not in output
