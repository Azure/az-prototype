"""Tests for the policy engine, loader, and validator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from azext_prototype.governance.policies import (
    Policy,
    PolicyEngine,
    PolicyPattern,
    PolicyRule,
    ValidationError,
    validate_policy_directory,
    validate_policy_file,
)
from azext_prototype.governance.policies.loader import get_policy_engine
from azext_prototype.governance.policies.validate import main as validate_main


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _write_policy(dest: Path, data: dict) -> Path:
    """Write a policy dict as YAML to *dest* and return the path."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(yaml.dump(data, sort_keys=False))
    return dest


def _minimal_policy(**overrides) -> dict:
    """Return a minimal valid policy dict, with optional overrides."""
    base = {
        "apiVersion": "v1",
        "kind": "policy",
        "metadata": {
            "name": "test-service",
            "category": "azure",
            "services": ["container-apps"],
            "last_reviewed": "2025-01-01",
        },
        "rules": [
            {
                "id": "T-001",
                "severity": "required",
                "description": "Use managed identity",
                "rationale": "Security best practice",
                "applies_to": ["cloud-architect", "terraform"],
            },
        ],
    }
    base.update(overrides)
    return base


# ================================================================== #
# Data-class tests
# ================================================================== #


class TestPolicyRule:
    """PolicyRule dataclass."""

    def test_defaults(self) -> None:
        rule = PolicyRule(id="R-001", severity="required", description="test")
        assert rule.id == "R-001"
        assert rule.rationale == ""
        assert rule.applies_to == []

    def test_full(self) -> None:
        rule = PolicyRule(
            id="R-002",
            severity="recommended",
            description="do this",
            rationale="because",
            applies_to=["cloud-architect"],
        )
        assert rule.applies_to == ["cloud-architect"]
        assert rule.rationale == "because"


class TestPolicyPattern:
    """PolicyPattern dataclass."""

    def test_defaults(self) -> None:
        pattern = PolicyPattern(name="p1", description="desc")
        assert pattern.example == ""

    def test_with_example(self) -> None:
        pattern = PolicyPattern(name="p1", description="desc", example="code")
        assert pattern.example == "code"


class TestPolicy:
    """Policy dataclass."""

    def test_defaults(self) -> None:
        policy = Policy(name="test", category="azure")
        assert policy.services == []
        assert policy.rules == []
        assert policy.patterns == []
        assert policy.anti_patterns == []
        assert policy.references == []
        assert policy.last_reviewed == ""


# ================================================================== #
# Validation tests
# ================================================================== #


class TestValidatePolicyFile:
    """Tests for validate_policy_file()."""

    def test_valid_file(self, tmp_path: Path) -> None:
        f = _write_policy(tmp_path / "ok.policy.yaml", _minimal_policy())
        errors = validate_policy_file(f)
        assert errors == []

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.policy.yaml"
        f.write_text("key: [unclosed\n  - item")
        errors = validate_policy_file(f)
        assert len(errors) == 1
        assert "Invalid YAML" in errors[0].message

    def test_non_dict_root(self, tmp_path: Path) -> None:
        f = tmp_path / "list.policy.yaml"
        f.write_text("- item1\n- item2\n")
        errors = validate_policy_file(f)
        assert any("Root element" in e.message for e in errors)

    def test_missing_metadata(self, tmp_path: Path) -> None:
        f = _write_policy(tmp_path / "no-meta.policy.yaml", {"rules": []})
        errors = validate_policy_file(f)
        assert any("metadata" in e.message for e in errors)

    def test_metadata_not_dict(self, tmp_path: Path) -> None:
        f = _write_policy(
            tmp_path / "bad-meta.policy.yaml",
            {"metadata": "not-a-dict", "rules": []},
        )
        errors = validate_policy_file(f)
        assert any("must be a mapping" in e.message for e in errors)

    def test_missing_metadata_keys(self, tmp_path: Path) -> None:
        data = _minimal_policy()
        del data["metadata"]["name"]
        del data["metadata"]["services"]
        f = _write_policy(tmp_path / "missing-keys.policy.yaml", data)
        errors = validate_policy_file(f)
        msgs = " ".join(e.message for e in errors)
        assert "'name'" in msgs
        assert "'services'" in msgs

    def test_invalid_category_is_warning(self, tmp_path: Path) -> None:
        data = _minimal_policy()
        data["metadata"]["category"] = "nonsense"
        f = _write_policy(tmp_path / "bad-cat.policy.yaml", data)
        errors = validate_policy_file(f)
        warnings = [e for e in errors if e.severity == "warning"]
        assert any("category" in w.message for w in warnings)

    def test_services_not_list(self, tmp_path: Path) -> None:
        data = _minimal_policy()
        data["metadata"]["services"] = "not-a-list"
        f = _write_policy(tmp_path / "svc.policy.yaml", data)
        errors = validate_policy_file(f)
        assert any("services must be a list" in e.message for e in errors)

    def test_unsupported_api_version(self, tmp_path: Path) -> None:
        data = _minimal_policy(apiVersion="v99")
        f = _write_policy(tmp_path / "api.policy.yaml", data)
        errors = validate_policy_file(f)
        assert any("apiVersion" in e.message for e in errors)

    def test_unsupported_kind(self, tmp_path: Path) -> None:
        data = _minimal_policy(kind="something-else")
        f = _write_policy(tmp_path / "kind.policy.yaml", data)
        errors = validate_policy_file(f)
        assert any("kind" in e.message for e in errors)

    def test_rules_not_list(self, tmp_path: Path) -> None:
        data = _minimal_policy(rules="not-a-list")
        f = _write_policy(tmp_path / "rules.policy.yaml", data)
        errors = validate_policy_file(f)
        assert any("'rules' must be a list" in e.message for e in errors)

    def test_rule_not_dict(self, tmp_path: Path) -> None:
        data = _minimal_policy(rules=["not-a-dict"])
        f = _write_policy(tmp_path / "rule-str.policy.yaml", data)
        errors = validate_policy_file(f)
        assert any("must be a mapping" in e.message for e in errors)

    def test_rule_missing_keys(self, tmp_path: Path) -> None:
        data = _minimal_policy(rules=[{"id": "R-001"}])
        f = _write_policy(tmp_path / "rule-keys.policy.yaml", data)
        errors = validate_policy_file(f)
        msgs = " ".join(e.message for e in errors)
        assert "'severity'" in msgs
        assert "'description'" in msgs
        assert "'applies_to'" in msgs

    def test_duplicate_rule_id(self, tmp_path: Path) -> None:
        data = _minimal_policy(
            rules=[
                {"id": "DUP-001", "severity": "required", "description": "a", "applies_to": ["terraform"]},
                {"id": "DUP-001", "severity": "required", "description": "b", "applies_to": ["terraform"]},
            ]
        )
        f = _write_policy(tmp_path / "dup.policy.yaml", data)
        errors = validate_policy_file(f)
        assert any("duplicate" in e.message for e in errors)

    def test_invalid_severity(self, tmp_path: Path) -> None:
        data = _minimal_policy(
            rules=[{"id": "S-001", "severity": "critical", "description": "a", "applies_to": ["terraform"]}]
        )
        f = _write_policy(tmp_path / "sev.policy.yaml", data)
        errors = validate_policy_file(f)
        assert any("severity" in e.message for e in errors)

    def test_applies_to_not_list(self, tmp_path: Path) -> None:
        data = _minimal_policy(
            rules=[{"id": "A-001", "severity": "required", "description": "a", "applies_to": "terraform"}]
        )
        f = _write_policy(tmp_path / "at.policy.yaml", data)
        errors = validate_policy_file(f)
        assert any("applies_to must be a list" in e.message for e in errors)

    def test_empty_applies_to_is_warning(self, tmp_path: Path) -> None:
        data = _minimal_policy(
            rules=[{"id": "E-001", "severity": "required", "description": "a", "applies_to": []}]
        )
        f = _write_policy(tmp_path / "empty-at.policy.yaml", data)
        errors = validate_policy_file(f)
        warnings = [e for e in errors if e.severity == "warning"]
        assert any("applies_to is empty" in w.message for w in warnings)

    def test_patterns_not_list(self, tmp_path: Path) -> None:
        data = _minimal_policy(patterns="not-a-list")
        f = _write_policy(tmp_path / "pat.policy.yaml", data)
        errors = validate_policy_file(f)
        assert any("'patterns' must be a list" in e.message for e in errors)

    def test_pattern_missing_keys(self, tmp_path: Path) -> None:
        data = _minimal_policy(patterns=[{"example": "code"}])
        f = _write_policy(tmp_path / "pat-keys.policy.yaml", data)
        errors = validate_policy_file(f)
        msgs = " ".join(e.message for e in errors)
        assert "'name'" in msgs
        assert "'description'" in msgs

    def test_pattern_not_dict(self, tmp_path: Path) -> None:
        data = _minimal_policy(patterns=["string-item"])
        f = _write_policy(tmp_path / "pat-str.policy.yaml", data)
        errors = validate_policy_file(f)
        assert any("must be a mapping" in e.message for e in errors)

    def test_anti_patterns_not_list(self, tmp_path: Path) -> None:
        data = _minimal_policy(anti_patterns="not-a-list")
        f = _write_policy(tmp_path / "ap.policy.yaml", data)
        errors = validate_policy_file(f)
        assert any("'anti_patterns' must be a list" in e.message for e in errors)

    def test_anti_pattern_missing_description(self, tmp_path: Path) -> None:
        data = _minimal_policy(anti_patterns=[{"instead": "do this"}])
        f = _write_policy(tmp_path / "ap-key.policy.yaml", data)
        errors = validate_policy_file(f)
        assert any("missing 'description'" in e.message for e in errors)

    def test_anti_pattern_not_dict(self, tmp_path: Path) -> None:
        data = _minimal_policy(anti_patterns=["string-item"])
        f = _write_policy(tmp_path / "ap-str.policy.yaml", data)
        errors = validate_policy_file(f)
        assert any("must be a mapping" in e.message for e in errors)

    def test_references_not_list(self, tmp_path: Path) -> None:
        data = _minimal_policy(references="not-a-list")
        f = _write_policy(tmp_path / "ref.policy.yaml", data)
        errors = validate_policy_file(f)
        assert any("'references' must be a list" in e.message for e in errors)

    def test_reference_missing_keys(self, tmp_path: Path) -> None:
        data = _minimal_policy(references=[{"title": "only title"}])
        f = _write_policy(tmp_path / "ref-key.policy.yaml", data)
        errors = validate_policy_file(f)
        assert any("missing 'url'" in e.message for e in errors)

    def test_reference_not_dict(self, tmp_path: Path) -> None:
        data = _minimal_policy(references=["string-ref"])
        f = _write_policy(tmp_path / "ref-str.policy.yaml", data)
        errors = validate_policy_file(f)
        assert any("must be a mapping" in e.message for e in errors)

    def test_file_not_found(self, tmp_path: Path) -> None:
        errors = validate_policy_file(tmp_path / "missing.policy.yaml")
        assert len(errors) == 1
        assert "Cannot read" in errors[0].message

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.policy.yaml"
        f.write_text("")
        errors = validate_policy_file(f)
        # Empty YAML = None → missing metadata
        assert any("metadata" in e.message for e in errors)

    def test_valid_all_sections(self, tmp_path: Path) -> None:
        data = _minimal_policy(
            patterns=[{"name": "p1", "description": "d1", "example": "e1"}],
            anti_patterns=[{"description": "bad thing", "instead": "good thing"}],
            references=[{"title": "doc", "url": "https://example.com"}],
        )
        f = _write_policy(tmp_path / "full.policy.yaml", data)
        errors = validate_policy_file(f)
        assert errors == []


class TestValidatePolicyDirectory:
    """Tests for validate_policy_directory()."""

    def test_empty_dir(self, tmp_path: Path) -> None:
        errors = validate_policy_directory(tmp_path)
        assert errors == []

    def test_nonexistent_dir(self) -> None:
        errors = validate_policy_directory(Path("/nonexistent"))
        assert errors == []

    def test_mixed_valid_invalid(self, tmp_path: Path) -> None:
        _write_policy(tmp_path / "good.policy.yaml", _minimal_policy())
        _write_policy(
            tmp_path / "bad.policy.yaml",
            {"rules": [{"id": "X-001"}]},  # missing metadata
        )
        errors = validate_policy_directory(tmp_path)
        assert len(errors) > 0

    def test_nested_dirs(self, tmp_path: Path) -> None:
        sub = tmp_path / "azure"
        sub.mkdir()
        _write_policy(sub / "nested.policy.yaml", _minimal_policy())
        errors = validate_policy_directory(tmp_path)
        assert errors == []

    def test_non_policy_files_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "readme.md").write_text("not a policy")
        (tmp_path / "config.yaml").write_text("not a policy")
        errors = validate_policy_directory(tmp_path)
        assert errors == []


class TestValidationError:
    """Tests for the ValidationError dataclass."""

    def test_str(self) -> None:
        err = ValidationError(file="test.yaml", message="broken")
        assert str(err) == "[ERROR] test.yaml: broken"

    def test_warning_str(self) -> None:
        err = ValidationError(file="test.yaml", message="meh", severity="warning")
        assert str(err) == "[WARNING] test.yaml: meh"


# ================================================================== #
# Engine tests
# ================================================================== #


class TestPolicyEngine:
    """Tests for PolicyEngine loading and resolution."""

    @pytest.fixture()
    def policy_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "policies"
        d.mkdir()
        return d

    @pytest.fixture()
    def sample_policy_file(self, policy_dir: Path) -> Path:
        return _write_policy(
            policy_dir / "test-service.policy.yaml",
            _minimal_policy(
                rules=[
                    {
                        "id": "T-001",
                        "severity": "required",
                        "description": "Use managed identity",
                        "rationale": "Security best practice",
                        "applies_to": ["cloud-architect", "terraform"],
                    },
                    {
                        "id": "T-002",
                        "severity": "recommended",
                        "description": "Enable logging",
                        "rationale": "",
                        "applies_to": ["cloud-architect"],
                    },
                    {
                        "id": "T-003",
                        "severity": "optional",
                        "description": "Use custom domains",
                        "rationale": "",
                        "applies_to": ["app-developer"],
                    },
                ],
                patterns=[
                    {
                        "name": "Identity pattern",
                        "description": "System-assigned identity",
                        "example": "identity { type = SystemAssigned }",
                    }
                ],
                anti_patterns=[
                    {"description": "Do not use keys", "instead": "Use managed identity"},
                ],
                references=[
                    {"title": "Docs", "url": "https://example.com"},
                ],
            ),
        )

    def test_load_empty_dir(self, policy_dir: Path) -> None:
        engine = PolicyEngine()
        engine.load([policy_dir])
        assert engine.list_policies() == []

    def test_load_policy(self, policy_dir: Path, sample_policy_file: Path) -> None:
        engine = PolicyEngine()
        engine.load([policy_dir])
        policies = engine.list_policies()
        assert len(policies) == 1
        assert policies[0].name == "test-service"
        assert len(policies[0].rules) == 3

    def test_load_nonexistent_dir(self) -> None:
        engine = PolicyEngine()
        engine.load([Path("/nonexistent/path")])
        assert engine.list_policies() == []

    def test_load_invalid_yaml(self, policy_dir: Path) -> None:
        bad = policy_dir / "bad.policy.yaml"
        bad.write_text("key: [unclosed\n  - item")
        engine = PolicyEngine()
        engine.load([policy_dir])
        assert engine.list_policies() == []

    def test_load_missing_metadata(self, policy_dir: Path) -> None:
        _write_policy(policy_dir / "no-meta.policy.yaml", {"rules": []})
        engine = PolicyEngine()
        engine.load([policy_dir])
        # Should still load — parser defaults metadata gracefully
        policies = engine.list_policies()
        assert len(policies) == 1

    def test_load_metadata_not_dict(self, policy_dir: Path) -> None:
        _write_policy(
            policy_dir / "bad-meta.policy.yaml",
            {"metadata": "not-a-dict", "rules": []},
        )
        engine = PolicyEngine()
        engine.load([policy_dir])
        # _parse_policy returns None when metadata is not a dict
        assert engine.list_policies() == []

    def test_resolve_by_agent(
        self, policy_dir: Path, sample_policy_file: Path
    ) -> None:
        engine = PolicyEngine()
        engine.load([policy_dir])
        policies = engine.resolve("cloud-architect")
        assert len(policies) == 1
        rule_ids = [r.id for r in policies[0].rules]
        assert "T-001" in rule_ids
        assert "T-002" in rule_ids
        assert "T-003" not in rule_ids  # app-developer only

    def test_resolve_by_agent_and_service(
        self, policy_dir: Path, sample_policy_file: Path
    ) -> None:
        engine = PolicyEngine()
        engine.load([policy_dir])
        policies = engine.resolve("terraform", services=["container-apps"])
        assert len(policies) == 1
        rule_ids = [r.id for r in policies[0].rules]
        assert "T-001" in rule_ids

    def test_resolve_no_service_match(
        self, policy_dir: Path, sample_policy_file: Path
    ) -> None:
        engine = PolicyEngine()
        engine.load([policy_dir])
        policies = engine.resolve("terraform", services=["redis"])
        assert len(policies) == 0

    def test_resolve_severity_filter_required(
        self, policy_dir: Path, sample_policy_file: Path
    ) -> None:
        engine = PolicyEngine()
        engine.load([policy_dir])
        policies = engine.resolve("cloud-architect", severity="required")
        assert len(policies) == 1
        assert all(r.severity == "required" for r in policies[0].rules)

    def test_resolve_severity_filter_recommended(
        self, policy_dir: Path, sample_policy_file: Path
    ) -> None:
        engine = PolicyEngine()
        engine.load([policy_dir])
        policies = engine.resolve("cloud-architect", severity="recommended")
        assert len(policies) == 1
        # Should include required + recommended
        severities = {r.severity for r in policies[0].rules}
        assert "required" in severities
        assert "recommended" in severities

    def test_resolve_auto_loads(self, policy_dir: Path, sample_policy_file: Path) -> None:
        engine = PolicyEngine()
        engine.load([policy_dir])
        policies = engine.resolve("cloud-architect")
        assert len(policies) >= 1

    def test_format_for_prompt_empty(self, policy_dir: Path) -> None:
        engine = PolicyEngine()
        engine.load([policy_dir])
        result = engine.format_for_prompt("unknown-agent")
        assert result == ""

    def test_format_for_prompt_content(
        self, policy_dir: Path, sample_policy_file: Path
    ) -> None:
        engine = PolicyEngine()
        engine.load([policy_dir])
        result = engine.format_for_prompt("cloud-architect")
        assert "Governance Policies" in result
        assert "MUST" in result
        assert "T-001" in result
        assert "Anti-patterns to avoid" in result
        assert "DO NOT" in result
        assert "Patterns to follow" in result

    def test_format_includes_patterns(
        self, policy_dir: Path, sample_policy_file: Path
    ) -> None:
        engine = PolicyEngine()
        engine.load([policy_dir])
        result = engine.format_for_prompt("cloud-architect")
        assert "Identity pattern" in result

    def test_format_includes_rationale(
        self, policy_dir: Path, sample_policy_file: Path
    ) -> None:
        engine = PolicyEngine()
        engine.load([policy_dir])
        result = engine.format_for_prompt("cloud-architect")
        assert "Rationale:" in result
        assert "Security best practice" in result

    def test_format_includes_instead(
        self, policy_dir: Path, sample_policy_file: Path
    ) -> None:
        engine = PolicyEngine()
        engine.load([policy_dir])
        result = engine.format_for_prompt("cloud-architect")
        assert "INSTEAD:" in result

    def test_load_multiple_dirs(self, tmp_path: Path) -> None:
        d1 = tmp_path / "dir1"
        d1.mkdir()
        d2 = tmp_path / "dir2"
        d2.mkdir()

        for i, d in enumerate([d1, d2]):
            _write_policy(
                d / f"policy-{i}.policy.yaml",
                _minimal_policy(
                    metadata={
                        "name": f"policy-{i}",
                        "category": "azure",
                        "services": ["storage"],
                    },
                    rules=[
                        {
                            "id": f"P{i}-001",
                            "severity": "required",
                            "description": f"Rule from dir {i}",
                            "applies_to": ["terraform"],
                        }
                    ],
                ),
            )

        engine = PolicyEngine()
        engine.load([d1, d2])
        assert len(engine.list_policies()) == 2

    def test_load_nested_dirs(self, policy_dir: Path) -> None:
        nested = policy_dir / "azure"
        nested.mkdir()
        _write_policy(
            nested / "nested.policy.yaml",
            _minimal_policy(
                metadata={
                    "name": "nested",
                    "category": "azure",
                    "services": ["functions"],
                },
                rules=[
                    {
                        "id": "N-001",
                        "severity": "required",
                        "description": "Nested rule",
                        "applies_to": ["cloud-architect"],
                    }
                ],
            ),
        )
        engine = PolicyEngine()
        engine.load([policy_dir])
        policies = engine.list_policies()
        assert any(p.name == "nested" for p in policies)

    def test_list_policies_auto_loads(self) -> None:
        """list_policies() should trigger load if not already loaded."""
        engine = PolicyEngine()
        # Default load path = built-in policies directory
        policies = engine.list_policies()
        assert len(policies) >= 1  # built-in policies exist


# ================================================================== #
# Loader tests
# ================================================================== #


class TestPolicyLoader:
    """Tests for the convenience loader."""

    def test_get_policy_engine_builtin(self) -> None:
        engine = get_policy_engine()
        policies = engine.list_policies()
        assert len(policies) >= 1

    def test_get_policy_engine_with_project(self, tmp_path: Path) -> None:
        proj_policies = tmp_path / ".prototype" / "policies"
        proj_policies.mkdir(parents=True)
        _write_policy(
            proj_policies / "custom.policy.yaml",
            _minimal_policy(
                metadata={"name": "custom", "category": "azure", "services": ["redis"]},
                rules=[
                    {
                        "id": "C-001",
                        "severity": "required",
                        "description": "Custom rule",
                        "applies_to": ["terraform"],
                    }
                ],
            ),
        )

        engine = get_policy_engine(str(tmp_path))
        policies = engine.list_policies()
        names = [p.name for p in policies]
        assert "custom" in names

    def test_get_policy_engine_no_project_dir(self) -> None:
        engine = get_policy_engine(None)
        assert isinstance(engine, PolicyEngine)

    def test_get_policy_engine_missing_project_policies_dir(self, tmp_path: Path) -> None:
        """Project dir exists but .prototype/policies/ doesn't — should not error."""
        engine = get_policy_engine(str(tmp_path))
        assert isinstance(engine, PolicyEngine)


# ================================================================== #
# Built-in policy validation
# ================================================================== #


class TestBuiltinPolicies:
    """Validate the built-in .policy.yaml files shipped with the extension."""

    def test_builtin_policies_load(self) -> None:
        engine = get_policy_engine()
        policies = engine.list_policies()
        names = [p.name for p in policies]
        assert "container-apps" in names
        assert "key-vault" in names
        assert "sql-database" in names
        assert "cosmos-db" in names
        assert "managed-identity" in names
        assert "network-isolation" in names
        assert "apim-to-container-apps" in names

    def test_all_rules_have_required_fields(self) -> None:
        engine = get_policy_engine()
        for policy in engine.list_policies():
            for rule in policy.rules:
                assert rule.id, f"Rule in {policy.name} missing id"
                assert rule.severity in ("required", "recommended", "optional"), (
                    f"{policy.name}/{rule.id} has invalid severity: {rule.severity}"
                )
                assert rule.description, f"{policy.name}/{rule.id} missing description"

    def test_all_rules_have_applies_to(self) -> None:
        engine = get_policy_engine()
        for policy in engine.list_policies():
            for rule in policy.rules:
                assert isinstance(rule.applies_to, list)
                assert len(rule.applies_to) > 0, (
                    f"{policy.name}/{rule.id} has empty applies_to"
                )

    def test_no_duplicate_rule_ids_within_policy(self) -> None:
        engine = get_policy_engine()
        for policy in engine.list_policies():
            ids = [r.id for r in policy.rules]
            assert len(ids) == len(set(ids)), (
                f"{policy.name} has duplicate rule ids: {ids}"
            )

    def test_builtin_policies_pass_strict_validation(self) -> None:
        """All built-in .policy.yaml files must pass strict validation."""
        builtin_dir = Path(__file__).resolve().parent.parent / "azext_prototype" / "policies"
        errors = validate_policy_directory(builtin_dir)
        actual_errors = [e for e in errors if e.severity == "error"]
        warnings = [e for e in errors if e.severity == "warning"]
        assert actual_errors == [], f"Built-in policy errors: {actual_errors}"
        assert warnings == [], f"Built-in policy warnings: {warnings}"


# ================================================================== #
# CLI validator tests
# ================================================================== #


class TestValidateMain:
    """Tests for the validate.py CLI entry point."""

    def test_default_validates_builtins(self) -> None:
        """Running with no args validates built-in policies."""
        exit_code = validate_main([])
        assert exit_code == 0

    def test_dir_valid(self, tmp_path: Path) -> None:
        _write_policy(tmp_path / "ok.policy.yaml", _minimal_policy())
        exit_code = validate_main(["--dir", str(tmp_path)])
        assert exit_code == 0

    def test_dir_invalid(self, tmp_path: Path) -> None:
        _write_policy(tmp_path / "bad.policy.yaml", {"rules": [{"id": "X"}]})
        exit_code = validate_main(["--dir", str(tmp_path)])
        assert exit_code == 1

    def test_dir_nonexistent(self) -> None:
        exit_code = validate_main(["--dir", "/nonexistent/path"])
        assert exit_code == 1

    def test_file_valid(self, tmp_path: Path) -> None:
        f = _write_policy(tmp_path / "ok.policy.yaml", _minimal_policy())
        exit_code = validate_main([str(f)])
        assert exit_code == 0

    def test_file_invalid(self, tmp_path: Path) -> None:
        f = _write_policy(tmp_path / "bad.policy.yaml", {"rules": [{"id": "X"}]})
        exit_code = validate_main([str(f)])
        assert exit_code == 1

    def test_file_nonexistent(self) -> None:
        exit_code = validate_main(["/nonexistent/file.policy.yaml"])
        assert exit_code == 1

    def test_strict_fails_on_warnings(self, tmp_path: Path) -> None:
        data = _minimal_policy()
        data["metadata"]["category"] = "nonsense"
        f = _write_policy(tmp_path / "warn.policy.yaml", data)
        # Without strict — warning doesn't cause failure
        exit_code_normal = validate_main([str(f)])
        assert exit_code_normal == 0
        # With strict — warning causes failure
        exit_code_strict = validate_main(["--strict", str(f)])
        assert exit_code_strict == 1

    def test_hook_mode_no_git(self, tmp_path: Path) -> None:
        """Hook mode with no git — should return 0 (no staged files)."""
        with patch(
            "azext_prototype.governance.policies.validate._get_staged_policy_files",
            return_value=[],
        ):
            exit_code = validate_main(["--hook"])
        assert exit_code == 0

    def test_hook_mode_with_staged_files(self, tmp_path: Path) -> None:
        f = _write_policy(tmp_path / "staged.policy.yaml", _minimal_policy())
        with patch(
            "azext_prototype.governance.policies.validate._get_staged_policy_files",
            return_value=[f],
        ):
            exit_code = validate_main(["--hook"])
        assert exit_code == 0

    def test_hook_mode_with_invalid_staged(self, tmp_path: Path) -> None:
        f = _write_policy(tmp_path / "bad.policy.yaml", {"rules": [{"id": "X"}]})
        with patch(
            "azext_prototype.governance.policies.validate._get_staged_policy_files",
            return_value=[f],
        ):
            exit_code = validate_main(["--hook", "--strict"])
        assert exit_code == 1

    def test_empty_dir_returns_zero(self, tmp_path: Path) -> None:
        exit_code = validate_main(["--dir", str(tmp_path)])
        assert exit_code == 0
