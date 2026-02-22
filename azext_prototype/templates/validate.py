"""Validate that workload templates comply with governance policies.

This module dynamically loads policy rules from ``*.policy.yaml`` files
and evaluates the ``template_check`` blocks against service configurations
in ``*.template.yaml`` files.  **No hard-coded checks** — adding a new
policy rule with a ``template_check`` block automatically enforces it.

Supported ``template_check`` directives:
    scope               — service types to check (per-service)
    require_config      — config keys that must be truthy
    require_config_value — config key-value pairs that must match
    reject_config_value — config key-value pairs that must NOT match
    require_service     — service types that must exist (template-level)
    when_services_present — conditional gate
    severity            — override violation severity (error | warning)
    error_message       — templated message with {service_name} etc.

Usage:
    # Validate all built-in workload templates
    python -m azext_prototype.templates.validate

    # Validate specific template files
    python -m azext_prototype.templates.validate path/to/template.yaml

    # Validate a directory recursively
    python -m azext_prototype.templates.validate --dir azext_prototype/templates/workloads/

    # Strict mode — warnings are treated as errors
    python -m azext_prototype.templates.validate --strict

    # As a pre-commit hook (validates staged .template.yaml files)
    python -m azext_prototype.templates.validate --hook

Exit codes:
    0 — all templates compliant
    1 — compliance violations found
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# ------------------------------------------------------------------ #
# Compliance violation
# ------------------------------------------------------------------ #


@dataclass
class ComplianceViolation:
    """A policy compliance issue found in a workload template."""

    template: str
    rule_id: str
    severity: str  # error | warning
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.template}: {self.rule_id} — {self.message}"


# ------------------------------------------------------------------ #
# Policy-driven check engine
# ------------------------------------------------------------------ #

_SEVERITY_MAP = {"required": "error", "recommended": "warning", "optional": "warning"}

_DEFAULT_POLICY_DIR = Path(__file__).resolve().parent.parent / "governance" / "policies"


def _load_template_checks(policy_dirs: list[Path]) -> list[dict[str, Any]]:
    """Scan policy files and return rules that contain a ``template_check`` block.

    Each returned dict has:
      - rule_id: str
      - policy_severity: str  (the rule's own severity)
      - template_check: dict  (the template_check block)
    """
    checks: list[dict[str, Any]] = []
    for directory in policy_dirs:
        if not directory.is_dir():
            continue
        for policy_file in sorted(directory.rglob("*.policy.yaml")):
            try:
                data = yaml.safe_load(policy_file.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            for rule in data.get("rules", []):
                if not isinstance(rule, dict):
                    continue
                tc = rule.get("template_check")
                if not isinstance(tc, dict):
                    continue
                checks.append(
                    {
                        "rule_id": str(rule.get("id", "")),
                        "policy_severity": str(rule.get("severity", "optional")),
                        "template_check": tc,
                    }
                )
    return checks


def _resolve_severity(policy_severity: str, template_check: dict[str, Any]) -> str:
    """Determine violation severity from the policy rule + template_check override."""
    override = template_check.get("severity")
    if override in ("error", "warning"):
        return override
    return _SEVERITY_MAP.get(policy_severity, "error")


def _as_list(value: Any) -> list[Any]:
    """Normalise a scalar-or-list field into a list."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [value]
    return []


def _format_message(template: str, **kwargs: Any) -> str:
    """Format a ``template_check.error_message`` with placeholder values."""
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        return template


def _evaluate_check(
    rule_id: str,
    severity: str,
    tc: dict[str, Any],
    template_name: str,
    services: list[dict[str, Any]],
    all_types: list[str],
) -> list[ComplianceViolation]:
    """Evaluate a single ``template_check`` against a parsed template."""
    violations: list[ComplianceViolation] = []
    error_msg_tpl: str = tc.get("error_message", "Policy rule {rule_id} violated")

    # --- when_services_present gate ---
    when = _as_list(tc.get("when_services_present"))
    if when and not all(svc_type in all_types for svc_type in when):
        return violations  # condition not met, skip this check entirely

    # --- require_service (template-level) ---
    for required_type in _as_list(tc.get("require_service")):
        if required_type not in all_types:
            msg = _format_message(
                error_msg_tpl,
                rule_id=rule_id,
                service_type=required_type,
                service_name="",
                config_key="",
            )
            violations.append(
                ComplianceViolation(
                    template=template_name,
                    rule_id=rule_id,
                    severity=severity,
                    message=msg,
                )
            )

    # --- per-service checks (scope-based) ---
    scope = _as_list(tc.get("scope"))
    if not scope:
        return violations  # no per-service checks for this rule

    require_config = _as_list(tc.get("require_config"))
    require_config_value = tc.get("require_config_value", {})
    if not isinstance(require_config_value, dict):
        require_config_value = {}
    reject_config_value = tc.get("reject_config_value", {})
    if not isinstance(reject_config_value, dict):
        reject_config_value = {}

    for svc in services:
        if not isinstance(svc, dict):
            continue
        svc_type = svc.get("type", "")
        if svc_type not in scope:
            continue
        svc_name = svc.get("name", "unknown")
        config: dict[str, Any] = svc.get("config") or {}

        # require_config — keys that must be truthy
        for key in require_config:
            if not config.get(key):
                msg = _format_message(
                    error_msg_tpl,
                    rule_id=rule_id,
                    service_name=svc_name,
                    service_type=svc_type,
                    config_key=key,
                )
                violations.append(
                    ComplianceViolation(
                        template=template_name,
                        rule_id=rule_id,
                        severity=severity,
                        message=msg,
                    )
                )

        # require_config_value — keys that must equal a specific value
        for key, expected in require_config_value.items():
            actual = config.get(key, "")
            if str(actual) != str(expected):
                msg = _format_message(
                    error_msg_tpl,
                    rule_id=rule_id,
                    service_name=svc_name,
                    service_type=svc_type,
                    config_key=key,
                    expected_value=expected,
                    actual_value=actual,
                )
                violations.append(
                    ComplianceViolation(
                        template=template_name,
                        rule_id=rule_id,
                        severity=severity,
                        message=msg,
                    )
                )

        # reject_config_value — keys that must NOT equal a specific value
        for key, rejected in reject_config_value.items():
            actual = config.get(key, "")
            if actual and str(actual).lower() == str(rejected).lower():
                msg = _format_message(
                    error_msg_tpl,
                    rule_id=rule_id,
                    service_name=svc_name,
                    service_type=svc_type,
                    config_key=key,
                    rejected_value=rejected,
                    actual_value=actual,
                )
                violations.append(
                    ComplianceViolation(
                        template=template_name,
                        rule_id=rule_id,
                        severity=severity,
                        message=msg,
                    )
                )

    return violations


# ------------------------------------------------------------------ #
# Public API
# ------------------------------------------------------------------ #


def validate_template_compliance(
    path: Path,
    policy_dirs: list[Path] | None = None,
    *,
    _checks: list[dict[str, Any]] | None = None,
) -> list[ComplianceViolation]:
    """Validate a single .template.yaml against governance policies.

    Args:
        path: Path to the template file.
        policy_dirs: Directories to scan for ``*.policy.yaml`` files.
            Defaults to the built-in policies shipped with the extension.
        _checks: Pre-loaded checks (internal optimisation for directory
            validation to avoid re-reading policy files per template).

    Returns a list of compliance violations (empty means compliant).
    """
    violations: list[ComplianceViolation] = []
    filename = str(path)

    # Parse YAML
    try:
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        violations.append(
            ComplianceViolation(
                template=filename,
                rule_id="PARSE",
                severity="error",
                message=f"Invalid YAML: {exc}",
            )
        )
        return violations
    except OSError as exc:
        violations.append(
            ComplianceViolation(
                template=filename,
                rule_id="IO",
                severity="error",
                message=f"Cannot read file: {exc}",
            )
        )
        return violations

    if not isinstance(data, dict):
        violations.append(
            ComplianceViolation(
                template=filename,
                rule_id="PARSE",
                severity="error",
                message="Root element must be a mapping",
            )
        )
        return violations

    metadata = data.get("metadata", {})
    template_name = metadata.get("name", path.stem) if isinstance(metadata, dict) else path.stem

    services: list[dict[str, Any]] = data.get("services", [])
    if not isinstance(services, list):
        violations.append(
            ComplianceViolation(
                template=template_name,
                rule_id="SCHEMA",
                severity="error",
                message="'services' must be a list",
            )
        )
        return violations

    all_types = [s.get("type", "") for s in services if isinstance(s, dict)]

    # Load template_check rules from policies (or use pre-loaded checks)
    checks = _checks if _checks is not None else _load_template_checks(policy_dirs or [_DEFAULT_POLICY_DIR])

    # Evaluate each check
    for check_info in checks:
        rule_id = check_info["rule_id"]
        tc = check_info["template_check"]
        severity = _resolve_severity(check_info["policy_severity"], tc)
        violations.extend(_evaluate_check(rule_id, severity, tc, template_name, services, all_types))

    return violations


def validate_template_directory(
    directory: Path,
    policy_dirs: list[Path] | None = None,
) -> list[ComplianceViolation]:
    """Validate all .template.yaml files under a directory.

    Returns a combined list of violations across all templates.
    """
    all_violations: list[ComplianceViolation] = []
    if not directory.is_dir():
        return all_violations

    # Load checks once and reuse across all templates
    checks = _load_template_checks(policy_dirs or [_DEFAULT_POLICY_DIR])

    for template_file in sorted(directory.rglob("*.template.yaml")):
        all_violations.extend(validate_template_compliance(template_file, policy_dirs, _checks=checks))

    return all_violations


# ------------------------------------------------------------------ #
# CLI
# ------------------------------------------------------------------ #


def _get_staged_template_files() -> list[Path]:
    """Return staged .template.yaml files from the git index."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    return [Path(f) for f in result.stdout.strip().splitlines() if f.endswith(".template.yaml")]


def main(argv: list[str] | None = None) -> int:
    """Entry point for the template compliance validator."""
    parser = argparse.ArgumentParser(description="Validate workload templates against governance policies.")
    parser.add_argument(
        "files",
        nargs="*",
        help="Specific .template.yaml files to validate.",
    )
    parser.add_argument(
        "--dir",
        type=str,
        default=None,
        help="Validate all .template.yaml files under this directory.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors.",
    )
    parser.add_argument(
        "--hook",
        action="store_true",
        help="Pre-commit hook mode: validate staged .template.yaml files.",
    )

    args = parser.parse_args(argv)

    violations: list[ComplianceViolation] = []

    if args.hook:
        staged = _get_staged_template_files()
        if not staged:
            return 0
        sys.stdout.write(f"Validating {len(staged)} staged template(s) against policies...\n")
        for path in staged:
            violations.extend(validate_template_compliance(path))

    elif args.dir:
        directory = Path(args.dir)
        if not directory.is_dir():
            sys.stderr.write(f"Error: '{args.dir}' is not a directory\n")
            return 1
        template_files = sorted(directory.rglob("*.template.yaml"))
        sys.stdout.write(f"Validating {len(template_files)} template(s) in {args.dir}...\n")
        violations.extend(validate_template_directory(directory))

    elif args.files:
        sys.stdout.write(f"Validating {len(args.files)} template(s)...\n")
        for filepath in args.files:
            path = Path(filepath)
            if not path.exists():
                sys.stderr.write(f"Error: '{filepath}' does not exist\n")
                return 1
            violations.extend(validate_template_compliance(path))

    else:
        # Default: validate built-in workload templates
        builtin_dir = Path(__file__).parent / "workloads"
        template_files = sorted(builtin_dir.rglob("*.template.yaml"))
        sys.stdout.write(f"Validating {len(template_files)} built-in template(s)...\n")
        violations.extend(validate_template_directory(builtin_dir))

    # Report results
    if not violations:
        sys.stdout.write("All templates comply with governance policies.\n")
        return 0

    actual_errors = [v for v in violations if v.severity == "error"]
    warnings = [v for v in violations if v.severity == "warning"]

    for v in violations:
        sys.stdout.write(f"{v}\n")

    sys.stdout.write(f"\n{len(actual_errors)} error(s), {len(warnings)} warning(s)\n")

    if actual_errors:
        return 1
    if args.strict and warnings:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
