"""Tests for the policy-driven template compliance validator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from azext_prototype.templates.validate import (
    ComplianceViolation,
    _as_list,
    _evaluate_check,
    _load_template_checks,
    _resolve_severity,
    main as validate_main,
    validate_template_compliance,
    validate_template_directory,
)


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

BUILTIN_DIR = (
    Path(__file__).resolve().parent.parent
    / "azext_prototype" / "templates" / "workloads"
)

BUILTIN_POLICY_DIR = (
    Path(__file__).resolve().parent.parent
    / "azext_prototype" / "governance" / "policies"
)


def _write_yaml(dest: Path, data: dict | list | str) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(yaml.dump(data, sort_keys=False))
    return dest


def _write_template(dest: Path, data: dict) -> Path:
    return _write_yaml(dest, data)


def _write_policy(dest: Path, data: dict) -> Path:
    return _write_yaml(dest, data)


def _compliant_template(**overrides) -> dict:
    """Return a minimal fully-compliant template (passes all built-in checks)."""
    base: dict = {
        "metadata": {
            "name": "test-tmpl",
            "display_name": "Test",
            "description": "Test template",
            "category": "web-app",
            "tags": ["test"],
        },
        "services": [
            {
                "name": "api",
                "type": "container-apps",
                "tier": "consumption",
                "config": {
                    "ingress": "internal",
                    "identity": "system-assigned",
                },
            },
            {
                "name": "gateway",
                "type": "api-management",
                "tier": "consumption",
                "config": {"identity": "system-assigned", "caching": True},
            },
            {
                "name": "secrets",
                "type": "key-vault",
                "tier": "standard",
                "config": {
                    "rbac_authorization": True,
                    "soft_delete": True,
                    "purge_protection": True,
                    "private_endpoint": True,
                    "diagnostics": True,
                },
            },
            {
                "name": "network",
                "type": "virtual-network",
                "config": {"subnets": [{"name": "apps"}]},
            },
            {
                "name": "logs",
                "type": "log-analytics",
                "tier": "per-gb",
                "config": {"retention_days": 30},
            },
            {
                "name": "monitoring",
                "type": "application-insights",
                "config": {},
            },
        ],
        "iac_defaults": {"tags": {"managed_by": "test"}},
        "requirements": "Build a test app.",
    }
    base.update(overrides)
    return base


def _custom_policy(rule_id: str, **tc_overrides) -> dict:
    """Return a minimal policy with one rule that has a template_check."""
    tc: dict = {
        "scope": ["test-service"],
        "require_config": ["some_key"],
        "error_message": "Service '{service_name}' missing {config_key}",
    }
    tc.update(tc_overrides)
    return {
        "apiVersion": "v1",
        "kind": "policy",
        "metadata": {
            "name": "custom-test",
            "category": "general",
            "services": ["test-service"],
        },
        "rules": [
            {
                "id": rule_id,
                "severity": "required",
                "description": "Custom test rule",
                "applies_to": ["cloud-architect"],
                "template_check": tc,
            },
        ],
    }


# ================================================================== #
# ComplianceViolation dataclass
# ================================================================== #

class TestComplianceViolation:
    def test_str_format(self):
        v = ComplianceViolation(
            template="web-app", rule_id="MI-001",
            severity="error", message="missing identity",
        )
        assert "[ERROR] web-app: MI-001" in str(v)
        assert "missing identity" in str(v)

    def test_warning_format(self):
        v = ComplianceViolation(
            template="t", rule_id="INT-003",
            severity="warning", message="should be internal",
        )
        assert "[WARNING]" in str(v)

    def test_equality(self):
        a = ComplianceViolation("t", "R1", "error", "msg")
        b = ComplianceViolation("t", "R1", "error", "msg")
        assert a == b


# ================================================================== #
# Utility functions
# ================================================================== #

class TestAsListHelper:
    def test_list_passthrough(self):
        assert _as_list(["a", "b"]) == ["a", "b"]

    def test_string_to_list(self):
        assert _as_list("single") == ["single"]

    def test_none_to_empty(self):
        assert _as_list(None) == []

    def test_int_to_empty(self):
        assert _as_list(42) == []


class TestResolveSeverity:
    def test_required_maps_to_error(self):
        assert _resolve_severity("required", {}) == "error"

    def test_recommended_maps_to_warning(self):
        assert _resolve_severity("recommended", {}) == "warning"

    def test_optional_maps_to_warning(self):
        assert _resolve_severity("optional", {}) == "warning"

    def test_override_to_warning(self):
        assert _resolve_severity("required", {"severity": "warning"}) == "warning"

    def test_override_to_error(self):
        assert _resolve_severity("recommended", {"severity": "error"}) == "error"

    def test_invalid_override_ignored(self):
        assert _resolve_severity("required", {"severity": "critical"}) == "error"


# ================================================================== #
# _load_template_checks
# ================================================================== #

class TestLoadTemplateChecks:
    def test_loads_from_builtin_policies(self):
        checks = _load_template_checks([BUILTIN_POLICY_DIR])
        rule_ids = [c["rule_id"] for c in checks]
        assert "MI-001" in rule_ids
        assert "NET-001" in rule_ids
        assert "KV-001" in rule_ids

    def test_skips_rules_without_template_check(self):
        checks = _load_template_checks([BUILTIN_POLICY_DIR])
        rule_ids = [c["rule_id"] for c in checks]
        # MI-002 has no template_check
        assert "MI-002" not in rule_ids

    def test_custom_policy_dir(self, tmp_path):
        pol_dir = tmp_path / "policies"
        _write_policy(pol_dir / "custom.policy.yaml", _custom_policy("X-001"))
        checks = _load_template_checks([pol_dir])
        assert len(checks) == 1
        assert checks[0]["rule_id"] == "X-001"

    def test_nonexistent_dir(self, tmp_path):
        checks = _load_template_checks([tmp_path / "nope"])
        assert checks == []

    def test_invalid_yaml_skipped(self, tmp_path):
        pol_dir = tmp_path / "policies"
        pol_dir.mkdir()
        (pol_dir / "bad.policy.yaml").write_text("key: [unclosed")
        checks = _load_template_checks([pol_dir])
        assert checks == []


# ================================================================== #
# _evaluate_check — core engine
# ================================================================== #

class TestEvaluateCheck:
    def test_require_config_pass(self):
        tc = {"scope": ["container-apps"], "require_config": ["identity"],
              "error_message": "missing {config_key}"}
        services = [{"name": "api", "type": "container-apps", "config": {"identity": "system"}}]
        vs = _evaluate_check("MI-001", "error", tc, "tmpl", services, ["container-apps"])
        assert vs == []

    def test_require_config_fail(self):
        tc = {"scope": ["container-apps"], "require_config": ["identity"],
              "error_message": "missing {config_key}"}
        services = [{"name": "api", "type": "container-apps", "config": {}}]
        vs = _evaluate_check("MI-001", "error", tc, "tmpl", services, ["container-apps"])
        assert len(vs) == 1
        assert vs[0].rule_id == "MI-001"

    def test_require_config_value_pass(self):
        tc = {"scope": ["container-apps"], "require_config_value": {"ingress": "internal"},
              "error_message": "wrong ingress"}
        services = [{"name": "api", "type": "container-apps", "config": {"ingress": "internal"}}]
        vs = _evaluate_check("INT-003", "warning", tc, "tmpl", services, ["container-apps"])
        assert vs == []

    def test_require_config_value_fail(self):
        tc = {"scope": ["container-apps"], "require_config_value": {"ingress": "internal"},
              "error_message": "wrong ingress"}
        services = [{"name": "api", "type": "container-apps", "config": {"ingress": "external"}}]
        vs = _evaluate_check("INT-003", "warning", tc, "tmpl", services, ["container-apps"])
        assert len(vs) == 1

    def test_reject_config_value_pass(self):
        tc = {"scope": ["cosmos-db"], "reject_config_value": {"consistency": "strong"},
              "error_message": "bad consistency"}
        services = [{"name": "db", "type": "cosmos-db", "config": {"consistency": "session"}}]
        vs = _evaluate_check("CDB-002", "warning", tc, "tmpl", services, ["cosmos-db"])
        assert vs == []

    def test_reject_config_value_fail(self):
        tc = {"scope": ["cosmos-db"], "reject_config_value": {"consistency": "strong"},
              "error_message": "bad consistency"}
        services = [{"name": "db", "type": "cosmos-db", "config": {"consistency": "strong"}}]
        vs = _evaluate_check("CDB-002", "warning", tc, "tmpl", services, ["cosmos-db"])
        assert len(vs) == 1

    def test_reject_config_value_case_insensitive(self):
        tc = {"scope": ["cosmos-db"], "reject_config_value": {"consistency": "strong"},
              "error_message": "bad"}
        services = [{"name": "db", "type": "cosmos-db", "config": {"consistency": "Strong"}}]
        vs = _evaluate_check("CDB-002", "warning", tc, "tmpl", services, ["cosmos-db"])
        assert len(vs) == 1

    def test_require_service_pass(self):
        tc = {"require_service": ["virtual-network"], "error_message": "missing vnet"}
        vs = _evaluate_check("NET-002", "error", tc, "tmpl", [], ["virtual-network"])
        assert vs == []

    def test_require_service_fail(self):
        tc = {"require_service": ["virtual-network"], "error_message": "missing vnet"}
        vs = _evaluate_check("NET-002", "error", tc, "tmpl", [], ["container-apps"])
        assert len(vs) == 1
        assert vs[0].rule_id == "NET-002"

    def test_when_services_present_gates(self):
        tc = {"scope": ["container-apps"], "require_config_value": {"ingress": "internal"},
              "when_services_present": ["api-management"], "error_message": "bad ingress"}
        services = [{"name": "api", "type": "container-apps", "config": {"ingress": "external"}}]
        vs = _evaluate_check("INT-003", "warning", tc, "tmpl", services, ["container-apps"])
        # api-management NOT present, so check is skipped
        assert vs == []

    def test_when_services_present_allows(self):
        tc = {"scope": ["container-apps"], "require_config_value": {"ingress": "internal"},
              "when_services_present": ["api-management"], "error_message": "bad ingress"}
        services = [{"name": "api", "type": "container-apps", "config": {"ingress": "external"}}]
        vs = _evaluate_check("INT-003", "warning", tc, "tmpl", services,
                             ["container-apps", "api-management"])
        assert len(vs) == 1

    def test_scope_filters_service_types(self):
        tc = {"scope": ["container-apps"], "require_config": ["identity"],
              "error_message": "missing identity"}
        services = [
            {"name": "api", "type": "container-apps", "config": {"identity": "system"}},
            {"name": "kv", "type": "key-vault", "config": {}},
        ]
        vs = _evaluate_check("MI-001", "error", tc, "tmpl", services, ["container-apps", "key-vault"])
        assert vs == []  # key-vault not in scope

    def test_non_dict_services_skipped(self):
        tc = {"scope": ["container-apps"], "require_config": ["identity"],
              "error_message": "missing"}
        services = ["not-a-dict", {"name": "api", "type": "container-apps", "config": {"identity": "sys"}}]
        vs = _evaluate_check("MI-001", "error", tc, "tmpl", services, ["container-apps"])
        assert vs == []

    def test_missing_config_treated_as_empty(self):
        tc = {"scope": ["container-apps"], "require_config": ["identity"],
              "error_message": "missing {config_key}"}
        services = [{"name": "api", "type": "container-apps"}]  # no 'config' key
        vs = _evaluate_check("MI-001", "error", tc, "tmpl", services, ["container-apps"])
        assert len(vs) == 1

    def test_error_message_placeholders(self):
        tc = {"scope": ["container-apps"], "require_config": ["identity"],
              "error_message": "Service '{service_name}' ({service_type}) missing {config_key}"}
        services = [{"name": "api", "type": "container-apps", "config": {}}]
        vs = _evaluate_check("MI-001", "error", tc, "tmpl", services, ["container-apps"])
        assert "api" in vs[0].message
        assert "container-apps" in vs[0].message
        assert "identity" in vs[0].message


# ================================================================== #
# CA-001, CA-002 — Container Apps checks
# ================================================================== #

class TestContainerAppsChecks:
    """CA-001 — managed identity on container-apps/container-registry.
    CA-002 — VNET required when container-apps present."""

    def test_container_apps_needs_identity(self, tmp_path):
        data = _compliant_template()
        data["services"][0]["config"].pop("identity")
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "CA-001" and "api" in v.message for v in vs)

    def test_container_registry_needs_identity(self, tmp_path):
        data = _compliant_template(services=[
            {"name": "api", "type": "container-apps", "config": {"identity": "system-assigned"}},
            {"name": "acr", "type": "container-registry", "config": {}},
            {"name": "net", "type": "virtual-network", "config": {}},
        ])
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "CA-001" and "acr" in v.message for v in vs)

    def test_container_registry_with_identity_passes(self, tmp_path):
        data = _compliant_template(services=[
            {"name": "api", "type": "container-apps", "config": {"identity": "system-assigned"}},
            {"name": "gw", "type": "api-management", "config": {"identity": "system-assigned", "caching": True}},
            {"name": "acr", "type": "container-registry", "config": {"identity": "user-assigned"}},
            {"name": "secrets", "type": "key-vault", "config": {
                "rbac_authorization": True, "soft_delete": True,
                "purge_protection": True, "private_endpoint": True, "diagnostics": True,
            }},
            {"name": "net", "type": "virtual-network", "config": {}},
        ])
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert not any(v.rule_id == "CA-001" for v in vs)

    def test_missing_vnet_triggers_ca002(self, tmp_path):
        data = _compliant_template()
        data["services"] = [s for s in data["services"] if s["type"] != "virtual-network"]
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "CA-002" for v in vs)

    def test_vnet_present_passes_ca002(self, tmp_path):
        data = _compliant_template()
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert not any(v.rule_id == "CA-002" for v in vs)

    def test_no_container_apps_skips_ca002(self, tmp_path):
        """CA-002 only fires when container-apps are present."""
        data = _compliant_template(services=[
            {"name": "fn", "type": "functions", "config": {"identity": "system-assigned"}},
            {"name": "kv", "type": "key-vault", "config": {
                "rbac_authorization": True, "soft_delete": True,
                "purge_protection": True, "private_endpoint": True, "diagnostics": True,
            }},
        ])
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert not any(v.rule_id == "CA-002" for v in vs)

    def test_compliant_container_apps(self, tmp_path):
        data = _compliant_template()
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        ca_violations = [v for v in vs if v.rule_id.startswith("CA-")]
        assert len(ca_violations) == 0


# ================================================================== #
# MI-001 — managed identity checks (via built-in policies)
# ================================================================== #

class TestManagedIdentityCheck:
    def test_container_apps_needs_identity(self, tmp_path):
        data = _compliant_template()
        data["services"][0]["config"].pop("identity")
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "MI-001" for v in vs)

    def test_functions_needs_identity(self, tmp_path):
        data = _compliant_template(services=[
            {"name": "fn", "type": "functions", "config": {"runtime": "python"}},
            {"name": "net", "type": "virtual-network", "config": {}},
        ])
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "MI-001" and "fn" in v.message for v in vs)

    def test_data_services_dont_need_identity(self, tmp_path):
        """key-vault, sql-database, cosmos-db are NOT in MI-001 scope."""
        data = _compliant_template()
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        mi_violations = [v for v in vs if v.rule_id == "MI-001"]
        assert len(mi_violations) == 0

    def test_apim_needs_identity(self, tmp_path):
        data = _compliant_template()
        data["services"][1]["config"].pop("identity")
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "MI-001" and "gateway" in v.message for v in vs)

    def test_infra_services_skip_identity(self, tmp_path):
        """virtual-network, log-analytics, event-grid are NOT in MI-001 scope."""
        data = _compliant_template(services=[
            {"name": "api", "type": "container-apps", "config": {"identity": "system-assigned"}},
            {"name": "logs", "type": "log-analytics", "config": {}},
            {"name": "events", "type": "event-grid", "config": {}},
            {"name": "net", "type": "virtual-network", "config": {}},
        ])
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        mi_violations = [v for v in vs if v.rule_id == "MI-001"]
        assert len(mi_violations) == 0


# ================================================================== #
# NET-001 — private endpoint checks
# ================================================================== #

class TestPrivateEndpointCheck:
    def test_key_vault_needs_private_endpoint(self, tmp_path):
        data = _compliant_template()
        data["services"][2]["config"].pop("private_endpoint")
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "NET-001" for v in vs)

    def test_sql_needs_private_endpoint(self, tmp_path):
        data = _compliant_template(services=[
            {"name": "api", "type": "container-apps", "config": {"identity": "system-assigned"}},
            {"name": "db", "type": "sql-database", "config": {"entra_auth_only": True, "tde_enabled": True, "threat_protection": True}},
            {"name": "net", "type": "virtual-network", "config": {}},
        ])
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "NET-001" and "db" in v.message for v in vs)

    def test_cosmos_needs_private_endpoint(self, tmp_path):
        data = _compliant_template(services=[
            {"name": "api", "type": "container-apps", "config": {"identity": "system-assigned"}},
            {"name": "store", "type": "cosmos-db", "config": {"entra_rbac": True, "local_auth_disabled": True}},
            {"name": "net", "type": "virtual-network", "config": {}},
        ])
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "NET-001" and "store" in v.message for v in vs)

    def test_storage_needs_private_endpoint(self, tmp_path):
        data = _compliant_template(services=[
            {"name": "blob", "type": "storage", "config": {}},
            {"name": "net", "type": "virtual-network", "config": {}},
        ])
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "NET-001" and "blob" in v.message for v in vs)

    def test_compliant_private_endpoint(self, tmp_path):
        data = _compliant_template()
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert not any(v.rule_id == "NET-001" for v in vs)


# ================================================================== #
# NET-002 — VNET presence check
# ================================================================== #

class TestVnetPresenceCheck:
    def test_missing_vnet(self, tmp_path):
        data = _compliant_template()
        data["services"] = [s for s in data["services"] if s["type"] != "virtual-network"]
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "NET-002" for v in vs)

    def test_vnet_present(self, tmp_path):
        data = _compliant_template()
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert not any(v.rule_id == "NET-002" for v in vs)


# ================================================================== #
# KV-001, KV-002, KV-004, KV-005 — Key Vault checks
# ================================================================== #

class TestKeyVaultChecks:
    def test_missing_soft_delete(self, tmp_path):
        data = _compliant_template()
        data["services"][2]["config"].pop("soft_delete")
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "KV-001" and "soft_delete" in v.message for v in vs)

    def test_missing_purge_protection(self, tmp_path):
        data = _compliant_template()
        data["services"][2]["config"].pop("purge_protection")
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "KV-001" and "purge_protection" in v.message for v in vs)

    def test_missing_rbac(self, tmp_path):
        data = _compliant_template()
        data["services"][2]["config"].pop("rbac_authorization")
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "KV-002" for v in vs)

    def test_missing_diagnostics(self, tmp_path):
        data = _compliant_template()
        data["services"][2]["config"].pop("diagnostics")
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "KV-004" and "diagnostics" in v.message for v in vs)

    def test_missing_kv_private_endpoint(self, tmp_path):
        data = _compliant_template()
        data["services"][2]["config"].pop("private_endpoint")
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "KV-005" and "private_endpoint" in v.message for v in vs)

    def test_compliant_key_vault(self, tmp_path):
        data = _compliant_template()
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        kv_violations = [v for v in vs if v.rule_id.startswith("KV-")]
        assert len(kv_violations) == 0


# ================================================================== #
# SQL-001, SQL-002, SQL-003 — SQL Database checks
# ================================================================== #

class TestSqlDatabaseChecks:
    def _sql_template(self, **config_overrides):
        base_config = {
            "entra_auth_only": True, "tde_enabled": True,
            "threat_protection": True, "private_endpoint": True,
        }
        base_config.update(config_overrides)
        return _compliant_template(services=[
            {"name": "api", "type": "container-apps", "config": {"identity": "system-assigned"}},
            {"name": "db", "type": "sql-database", "config": base_config},
            {"name": "net", "type": "virtual-network", "config": {}},
        ])

    def test_missing_entra_auth(self, tmp_path):
        data = self._sql_template(entra_auth_only=False)
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "SQL-001" for v in vs)

    def test_missing_tde(self, tmp_path):
        data = self._sql_template(tde_enabled=False)
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "SQL-002" for v in vs)

    def test_missing_threat_protection(self, tmp_path):
        data = self._sql_template(threat_protection=False)
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "SQL-003" for v in vs)

    def test_compliant_sql(self, tmp_path):
        data = self._sql_template()
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        sql_violations = [v for v in vs if v.rule_id.startswith("SQL-")]
        assert len(sql_violations) == 0


# ================================================================== #
# CDB-001..004 — Cosmos DB checks
# ================================================================== #

class TestCosmosDbChecks:
    def _cosmos_template(self, **config_overrides):
        base_config = {
            "entra_rbac": True, "local_auth_disabled": True,
            "consistency": "session", "private_endpoint": True,
            "autoscale": True, "partition_key": "/id",
        }
        base_config.update(config_overrides)
        return _compliant_template(services=[
            {"name": "api", "type": "container-apps", "config": {"identity": "system-assigned"}},
            {"name": "store", "type": "cosmos-db", "config": base_config},
            {"name": "net", "type": "virtual-network", "config": {}},
        ])

    def test_missing_entra_rbac(self, tmp_path):
        data = self._cosmos_template(entra_rbac=False)
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "CDB-001" and "entra_rbac" in v.message for v in vs)

    def test_missing_local_auth_disabled(self, tmp_path):
        data = self._cosmos_template(local_auth_disabled=False)
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "CDB-001" and "local_auth_disabled" in v.message for v in vs)

    def test_strong_consistency_warning(self, tmp_path):
        data = self._cosmos_template(consistency="strong")
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        warnings = [v for v in vs if v.rule_id == "CDB-002"]
        assert len(warnings) == 1
        assert warnings[0].severity == "warning"

    def test_session_consistency_ok(self, tmp_path):
        data = self._cosmos_template(consistency="session")
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert not any(v.rule_id == "CDB-002" for v in vs)

    def test_missing_autoscale(self, tmp_path):
        data = self._cosmos_template(autoscale=False)
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "CDB-003" and "autoscale" in v.message for v in vs)

    def test_autoscale_present_passes(self, tmp_path):
        data = self._cosmos_template()
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert not any(v.rule_id == "CDB-003" for v in vs)

    def test_missing_partition_key(self, tmp_path):
        data = self._cosmos_template()
        data["services"][1]["config"].pop("partition_key")
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "CDB-004" and "partition_key" in v.message for v in vs)

    def test_partition_key_present_passes(self, tmp_path):
        data = self._cosmos_template()
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert not any(v.rule_id == "CDB-004" for v in vs)

    def test_compliant_cosmos(self, tmp_path):
        data = self._cosmos_template()
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        cosmos_violations = [v for v in vs if v.rule_id.startswith("CDB-")]
        assert len(cosmos_violations) == 0


# ================================================================== #
# INT-001..004 — APIM integration checks
# ================================================================== #

class TestApimIntegrationChecks:
    def test_container_apps_needs_internal_ingress_with_apim(self, tmp_path):
        data = _compliant_template()
        data["services"][0]["config"]["ingress"] = "external"
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "INT-003" for v in vs)

    def test_internal_ingress_is_compliant(self, tmp_path):
        data = _compliant_template()
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert not any(v.rule_id == "INT-003" for v in vs)

    def test_no_apim_skips_int_check(self, tmp_path):
        """Without APIM, ingress mode doesn't matter for INT-003."""
        data = _compliant_template(services=[
            {"name": "api", "type": "container-apps", "config": {"identity": "system-assigned", "ingress": "external"}},
            {"name": "net", "type": "virtual-network", "config": {}},
        ])
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert not any(v.rule_id == "INT-003" for v in vs)

    def test_container_apps_without_apim_warns(self, tmp_path):
        data = _compliant_template(services=[
            {"name": "api", "type": "container-apps", "config": {"identity": "system-assigned"}},
            {"name": "net", "type": "virtual-network", "config": {}},
        ])
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "INT-001" for v in vs)

    def test_apim_needs_identity_with_container_apps(self, tmp_path):
        data = _compliant_template()
        data["services"][1]["config"].pop("identity")
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "INT-002" for v in vs)

    def test_apim_identity_present_passes(self, tmp_path):
        data = _compliant_template()
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert not any(v.rule_id == "INT-002" for v in vs)

    def test_no_container_apps_skips_int002(self, tmp_path):
        """INT-002 only fires when container-apps are present."""
        data = _compliant_template(services=[
            {"name": "gw", "type": "api-management", "config": {}},
            {"name": "net", "type": "virtual-network", "config": {}},
        ])
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert not any(v.rule_id == "INT-002" for v in vs)

    def test_apim_needs_caching_with_container_apps(self, tmp_path):
        data = _compliant_template()
        data["services"][1]["config"].pop("caching")
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "INT-004" for v in vs)

    def test_apim_caching_present_passes(self, tmp_path):
        data = _compliant_template()
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert not any(v.rule_id == "INT-004" for v in vs)

    def test_no_container_apps_skips_int004(self, tmp_path):
        """INT-004 only fires when container-apps are present."""
        data = _compliant_template(services=[
            {"name": "gw", "type": "api-management", "config": {}},
            {"name": "net", "type": "virtual-network", "config": {}},
        ])
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert not any(v.rule_id == "INT-004" for v in vs)

    def test_compliant_apim_integration(self, tmp_path):
        data = _compliant_template()
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        int_violations = [v for v in vs if v.rule_id.startswith("INT-")]
        assert len(int_violations) == 0


# ================================================================== #
# Edge cases — parse errors, non-YAML, etc.
# ================================================================== #

class TestEdgeCases:
    def test_invalid_yaml(self, tmp_path):
        path = tmp_path / "bad.template.yaml"
        path.write_text("key: [unclosed\n  - item")
        vs = validate_template_compliance(path)
        assert len(vs) == 1
        assert vs[0].rule_id == "PARSE"

    def test_non_dict_root(self, tmp_path):
        path = tmp_path / "list.template.yaml"
        path.write_text("- item1\n- item2")
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "PARSE" for v in vs)

    def test_services_not_a_list(self, tmp_path):
        data = {"metadata": {"name": "bad"}, "services": "not-a-list"}
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "SCHEMA" for v in vs)

    def test_non_dict_service_ignored(self, tmp_path):
        data = _compliant_template()
        data["services"].insert(0, "not-a-dict")
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        # Should not crash
        errors = [v for v in vs if v.severity == "error"]
        assert all(v.rule_id != "PARSE" for v in errors)

    def test_empty_yaml(self, tmp_path):
        path = tmp_path / "empty.template.yaml"
        path.write_text("")
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "NET-002" for v in vs)

    def test_missing_config_key(self, tmp_path):
        """Service with no 'config' key should still be checked."""
        data = _compliant_template(services=[
            {"name": "api", "type": "container-apps"},
            {"name": "net", "type": "virtual-network"},
        ])
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert any(v.rule_id == "MI-001" for v in vs)

    def test_file_not_found(self, tmp_path):
        path = tmp_path / "nonexistent.template.yaml"
        vs = validate_template_compliance(path)
        assert len(vs) == 1
        assert vs[0].rule_id == "IO"


# ================================================================== #
# Custom policy dirs — the dynamic engine
# ================================================================== #

class TestCustomPolicyDirs:
    """Demonstrate that new policies are automatically enforced."""

    def test_custom_require_config_enforced(self, tmp_path):
        """A brand-new policy with template_check is enforced with zero code changes."""
        pol_dir = tmp_path / "policies"
        _write_policy(
            pol_dir / "widget.policy.yaml",
            _custom_policy(
                "WIDGET-001",
                scope=["widget-service"],
                require_config=["encryption"],
                error_message="Service '{service_name}' missing {config_key}",
            ),
        )
        tmpl = {
            "metadata": {"name": "test"},
            "services": [
                {"name": "w", "type": "widget-service", "config": {}},
                {"name": "net", "type": "virtual-network", "config": {}},
            ],
        }
        path = _write_template(tmp_path / "t.template.yaml", tmpl)
        vs = validate_template_compliance(path, policy_dirs=[pol_dir])
        assert any(v.rule_id == "WIDGET-001" for v in vs)

    def test_custom_require_service_enforced(self, tmp_path):
        pol_dir = tmp_path / "policies"
        _write_policy(
            pol_dir / "logging.policy.yaml",
            _custom_policy(
                "LOG-001",
                scope=None,
                require_config=None,
                require_service=["log-analytics"],
                error_message="Template must include log-analytics service",
            ),
        )
        # Remove scope/require_config from template_check
        data = yaml.safe_load((pol_dir / "logging.policy.yaml").read_text())
        tc = data["rules"][0]["template_check"]
        tc.pop("scope", None)
        tc.pop("require_config", None)
        (pol_dir / "logging.policy.yaml").write_text(yaml.dump(data, sort_keys=False))

        tmpl = {"metadata": {"name": "test"}, "services": []}
        path = _write_template(tmp_path / "t.template.yaml", tmpl)
        vs = validate_template_compliance(path, policy_dirs=[pol_dir])
        assert any(v.rule_id == "LOG-001" for v in vs)

    def test_rule_without_template_check_not_enforced(self, tmp_path):
        """Rules lacking template_check are guidance-only."""
        pol_dir = tmp_path / "policies"
        policy = {
            "metadata": {"name": "guidance", "category": "general", "services": ["any"]},
            "rules": [{"id": "G-001", "severity": "required",
                        "description": "Think before you code",
                        "applies_to": ["cloud-architect"]}],
        }
        _write_policy(pol_dir / "guidance.policy.yaml", policy)
        tmpl = {"metadata": {"name": "test"}, "services": []}
        path = _write_template(tmp_path / "t.template.yaml", tmpl)
        vs = validate_template_compliance(path, policy_dirs=[pol_dir])
        assert not any(v.rule_id == "G-001" for v in vs)

    def test_custom_reject_config_value(self, tmp_path):
        pol_dir = tmp_path / "policies"
        policy = _custom_policy("SEC-001")
        policy["rules"][0]["template_check"] = {
            "scope": ["redis"],
            "reject_config_value": {"tls": "disabled"},
            "error_message": "Service '{service_name}' must not disable TLS",
        }
        _write_policy(pol_dir / "sec.policy.yaml", policy)
        tmpl = {
            "metadata": {"name": "test"},
            "services": [{"name": "cache", "type": "redis", "config": {"tls": "disabled"}}],
        }
        path = _write_template(tmp_path / "t.template.yaml", tmpl)
        vs = validate_template_compliance(path, policy_dirs=[pol_dir])
        assert any(v.rule_id == "SEC-001" for v in vs)


# ================================================================== #
# Directory validation
# ================================================================== #

class TestDirectoryValidation:
    def test_empty_directory(self, tmp_path):
        vs = validate_template_directory(tmp_path)
        assert vs == []

    def test_nonexistent_directory(self, tmp_path):
        vs = validate_template_directory(tmp_path / "nope")
        assert vs == []

    def test_multiple_templates(self, tmp_path):
        _write_template(tmp_path / "a.template.yaml", _compliant_template())
        _write_template(tmp_path / "b.template.yaml", _compliant_template())
        vs = validate_template_directory(tmp_path)
        assert vs == []

    def test_violation_across_templates(self, tmp_path):
        good = _compliant_template()
        bad = _compliant_template()
        bad["services"] = [s for s in bad["services"] if s["type"] != "virtual-network"]
        _write_template(tmp_path / "good.template.yaml", good)
        _write_template(tmp_path / "bad.template.yaml", bad)
        vs = validate_template_directory(tmp_path)
        assert len(vs) > 0

    def test_custom_policy_dirs_applied(self, tmp_path):
        """Directory validation can use custom policy dirs."""
        pol_dir = tmp_path / "policies"
        _write_policy(pol_dir / "x.policy.yaml", _custom_policy(
            "X-001", scope=["my-svc"], require_config=["foo"],
            error_message="missing {config_key}",
        ))
        tmpl_dir = tmp_path / "templates"
        _write_template(tmpl_dir / "t.template.yaml", {
            "metadata": {"name": "x"},
            "services": [{"name": "s", "type": "my-svc", "config": {}}],
        })
        vs = validate_template_directory(tmpl_dir, policy_dirs=[pol_dir])
        assert any(v.rule_id == "X-001" for v in vs)


# ================================================================== #
# Built-in templates — all must pass
# ================================================================== #

class TestBuiltinCompliance:
    """All shipped workload templates must comply with all policies."""

    def test_all_builtins_compliant(self):
        violations = validate_template_directory(BUILTIN_DIR)
        errors = [v for v in violations if v.severity == "error"]
        if errors:
            msgs = "\n".join(str(v) for v in errors)
            pytest.fail(f"Built-in templates have compliance errors:\n{msgs}")

    @pytest.mark.parametrize("name", [
        "web-app", "data-pipeline", "ai-app", "microservices", "serverless-api",
    ])
    def test_individual_builtin_compliant(self, name):
        path = BUILTIN_DIR / f"{name}.template.yaml"
        assert path.exists(), f"Missing built-in template: {name}"
        violations = validate_template_compliance(path)
        errors = [v for v in violations if v.severity == "error"]
        if errors:
            msgs = "\n".join(str(v) for v in errors)
            pytest.fail(f"Template '{name}' has compliance errors:\n{msgs}")


# ================================================================== #
# CLI — main()
# ================================================================== #

class TestCli:
    def test_default_validates_builtins(self):
        assert validate_main([]) == 0

    def test_dir_mode(self):
        assert validate_main(["--dir", str(BUILTIN_DIR)]) == 0

    def test_dir_mode_nonexistent(self):
        assert validate_main(["--dir", "/nonexistent/path"]) == 1

    def test_file_mode(self):
        path = BUILTIN_DIR / "web-app.template.yaml"
        assert validate_main([str(path)]) == 0

    def test_file_mode_nonexistent(self):
        assert validate_main(["/nonexistent.template.yaml"]) == 1

    def test_strict_catches_warnings(self, tmp_path):
        """Strict mode should fail on warnings."""
        data = _compliant_template()
        data["services"][0]["config"]["ingress"] = "external"
        path = _write_template(tmp_path / "t.template.yaml", data)
        result = validate_main(["--strict", str(path)])
        assert result == 1

    def test_non_strict_passes_warnings(self, tmp_path):
        data = _compliant_template()
        data["services"][0]["config"]["ingress"] = "external"
        path = _write_template(tmp_path / "t.template.yaml", data)
        result = validate_main([str(path)])
        assert result == 0

    def test_hook_mode_no_staged_files(self):
        with patch(
            "azext_prototype.templates.validate._get_staged_template_files",
            return_value=[],
        ):
            assert validate_main(["--hook"]) == 0

    def test_hook_mode_with_staged_files(self, tmp_path):
        data = _compliant_template()
        path = _write_template(tmp_path / "t.template.yaml", data)
        with patch(
            "azext_prototype.templates.validate._get_staged_template_files",
            return_value=[path],
        ):
            assert validate_main(["--hook"]) == 0

    def test_hook_mode_with_violations(self, tmp_path):
        data = _compliant_template()
        data["services"] = [s for s in data["services"] if s["type"] != "virtual-network"]
        path = _write_template(tmp_path / "bad.template.yaml", data)
        with patch(
            "azext_prototype.templates.validate._get_staged_template_files",
            return_value=[path],
        ):
            assert validate_main(["--hook", "--strict"]) == 1


# ================================================================== #
# Fully compliant template produces zero violations
# ================================================================== #

class TestFullCompliance:
    def test_compliant_template_clean(self, tmp_path):
        data = _compliant_template()
        path = _write_template(tmp_path / "t.template.yaml", data)
        vs = validate_template_compliance(path)
        assert vs == [], f"Expected zero violations, got: {vs}"
