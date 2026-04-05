#!/usr/bin/env python
"""Validate governance YAML files: policies, anti-patterns, and standards.

Usage:
    # Validate everything
    python -m azext_prototype.governance.validate --all --strict

    # Validate individual areas
    python -m azext_prototype.governance.validate --policies --strict
    python -m azext_prototype.governance.validate --anti-patterns --strict
    python -m azext_prototype.governance.validate --standards --strict

    # Combine flags
    python -m azext_prototype.governance.validate --policies --anti-patterns --strict

Exit codes:
    0 -- all files valid
    1 -- validation errors found
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

_GOVERNANCE_DIR = Path(__file__).resolve().parent


# ------------------------------------------------------------------ #
# Shared validation result
# ------------------------------------------------------------------ #


@dataclass
class ValidationError:
    """A single validation issue."""

    file: str
    message: str
    severity: str = "error"

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.file}: {self.message}"


# ------------------------------------------------------------------ #
# Policy validation (delegates to existing engine)
# ------------------------------------------------------------------ #


def validate_policies() -> list[ValidationError]:
    """Validate all policy YAML files."""
    from azext_prototype.governance.policies import (
        validate_policy_directory,
    )

    policy_dir = _GOVERNANCE_DIR / "policies"
    if not policy_dir.is_dir():
        return []

    policy_errors = validate_policy_directory(policy_dir)

    # Convert to our ValidationError type
    return [ValidationError(file=e.file, message=e.message, severity=e.severity) for e in policy_errors]


# ------------------------------------------------------------------ #
# Anti-pattern validation
# ------------------------------------------------------------------ #


def validate_anti_patterns() -> list[ValidationError]:
    """Validate all anti-pattern YAML files against the unified schema."""
    ap_dir = _GOVERNANCE_DIR / "anti_patterns"
    if not ap_dir.is_dir():
        return []

    errors: list[ValidationError] = []
    all_ids: dict[str, str] = {}

    required_top = {"kind", "domain", "description", "last_updated", "patterns"}

    for yaml_file in sorted(ap_dir.glob("*.yaml")):
        fname = yaml_file.name
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            errors.append(ValidationError(fname, f"Could not load: {exc}"))
            continue

        if not isinstance(data, dict):
            errors.append(ValidationError(fname, "Root must be a mapping"))
            continue

        for key in required_top:
            if key not in data:
                errors.append(ValidationError(fname, f"Missing required field '{key}'"))

        if data.get("kind") != "anti-pattern":
            errors.append(ValidationError(fname, f"kind must be 'anti-pattern', got '{data.get('kind')}'"))

        patterns = data.get("patterns", [])
        if not isinstance(patterns, list):
            errors.append(ValidationError(fname, "'patterns' must be a list"))
            continue

        for idx, entry in enumerate(patterns, 1):
            if not isinstance(entry, dict):
                errors.append(ValidationError(fname, f"Pattern {idx}: must be a mapping"))
                continue

            check_id = entry.get("id")
            if not check_id:
                errors.append(ValidationError(fname, f"Pattern {idx}: missing 'id'"))

            if not entry.get("description"):
                errors.append(ValidationError(fname, f"Pattern {idx} ({check_id}): missing 'description'"))

            if not entry.get("warning_message"):
                errors.append(ValidationError(fname, f"Pattern {idx} ({check_id}): missing 'warning_message'"))

            # targets: list of target blocks, each with services and search_patterns
            targets = entry.get("targets")
            if isinstance(targets, dict):
                # Single target block — normalize to list
                targets = [targets]
            if not isinstance(targets, list) or not targets:
                errors.append(ValidationError(fname, f"Pattern {idx} ({check_id}): missing or invalid 'targets'"))
            else:
                # At least one target block must have search_patterns
                has_search = any(isinstance(t, dict) and t.get("search_patterns") for t in targets)
                if not has_search:
                    errors.append(
                        ValidationError(fname, f"Pattern {idx} ({check_id}): no target block has 'search_patterns'")
                    )

            if check_id and check_id in all_ids:
                errors.append(ValidationError(fname, f"Duplicate id '{check_id}' (also in {all_ids[check_id]})"))
            elif check_id:
                all_ids[check_id] = fname

    return errors


# ------------------------------------------------------------------ #
# Standards validation
# ------------------------------------------------------------------ #


def validate_standards() -> list[ValidationError]:
    """Validate all standards YAML files against the unified schema."""
    std_dir = _GOVERNANCE_DIR / "standards"
    if not std_dir.is_dir():
        return []

    errors: list[ValidationError] = []
    all_ids: dict[str, str] = {}

    required_top = {"kind", "domain", "description", "last_updated", "principles"}

    for yaml_file in sorted(std_dir.rglob("*.yaml")):
        fname = str(yaml_file.relative_to(std_dir))
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            errors.append(ValidationError(fname, f"Could not load: {exc}"))
            continue

        if not isinstance(data, dict):
            errors.append(ValidationError(fname, "Root must be a mapping"))
            continue

        for key in required_top:
            if key not in data:
                errors.append(ValidationError(fname, f"Missing required field '{key}'"))

        if data.get("kind") != "standard":
            errors.append(ValidationError(fname, f"kind must be 'standard', got '{data.get('kind')}'"))

        principles = data.get("principles", [])
        if not isinstance(principles, list):
            errors.append(ValidationError(fname, "'principles' must be a list"))
            continue

        for idx, entry in enumerate(principles, 1):
            if not isinstance(entry, dict):
                errors.append(ValidationError(fname, f"Principle {idx}: must be a mapping"))
                continue

            pid = entry.get("id")
            if not pid:
                errors.append(ValidationError(fname, f"Principle {idx}: missing 'id'"))

            if not entry.get("description"):
                errors.append(ValidationError(fname, f"Principle {idx} ({pid}): missing 'description'"))

            if pid and pid in all_ids:
                errors.append(ValidationError(fname, f"Duplicate id '{pid}' (also in {all_ids[pid]})"))
            elif pid:
                all_ids[pid] = fname

            applies_to = entry.get("applies_to")
            if applies_to is not None and not isinstance(applies_to, list):
                errors.append(ValidationError(fname, f"Principle {idx} ({pid}): 'applies_to' must be a list"))

    return errors


# ------------------------------------------------------------------ #
# Workload template validation
# ------------------------------------------------------------------ #


def validate_workloads() -> list[ValidationError]:
    """Validate all workload template YAML files against policies."""
    from azext_prototype.templates.validate import validate_template_directory

    template_dir = Path(__file__).resolve().parent.parent / "templates" / "workloads"
    if not template_dir.is_dir():
        return []

    violations = validate_template_directory(template_dir)

    return [
        ValidationError(
            file=v.template,
            message=f"{v.rule_id} — {v.message}",
            severity=v.severity,
        )
        for v in violations
    ]


# ------------------------------------------------------------------ #
# Taxonomy validation
# ------------------------------------------------------------------ #


def validate_taxonomy() -> list[ValidationError]:
    """Validate taxonomy.yaml structure and consistency with governance files."""
    knowledge_dir = Path(__file__).resolve().parent.parent / "knowledge"
    taxonomy_path = knowledge_dir / "taxonomy.yaml"

    if not taxonomy_path.exists():
        return [ValidationError("taxonomy.yaml", "File not found")]

    errors: list[ValidationError] = []

    try:
        data = yaml.safe_load(taxonomy_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return [ValidationError("taxonomy.yaml", f"Could not load: {exc}")]

    layers = data.get("layers")
    if not isinstance(layers, dict):
        return [ValidationError("taxonomy.yaml", "'layers' must be a mapping")]

    # Collect all valid capabilities and components
    all_capabilities: dict[str, str] = {}  # capability → layer
    all_components: dict[str, str] = {}  # component → capability

    for layer_key, layer_data in layers.items():
        if not isinstance(layer_data, dict):
            errors.append(ValidationError("taxonomy.yaml", f"Layer '{layer_key}' must be a mapping"))
            continue

        if "display_name" not in layer_data:
            errors.append(ValidationError("taxonomy.yaml", f"Layer '{layer_key}': missing 'display_name'"))

        caps = layer_data.get("capabilities")
        if not isinstance(caps, dict):
            errors.append(ValidationError("taxonomy.yaml", f"Layer '{layer_key}': 'capabilities' must be a mapping"))
            continue

        for cap_key, cap_data in caps.items():
            if cap_key in all_capabilities:
                errors.append(
                    ValidationError(
                        "taxonomy.yaml",
                        f"Duplicate capability '{cap_key}' in layers '{all_capabilities[cap_key]}' and '{layer_key}'",
                    )
                )
            all_capabilities[cap_key] = layer_key

            if not isinstance(cap_data, dict):
                errors.append(
                    ValidationError("taxonomy.yaml", f"Capability '{cap_key}' in '{layer_key}' must be a mapping")
                )
                continue

            components = cap_data.get("components")
            if not isinstance(components, list):
                errors.append(ValidationError("taxonomy.yaml", f"Capability '{cap_key}': 'components' must be a list"))
                continue

            for comp in components:
                if not isinstance(comp, str):
                    errors.append(
                        ValidationError("taxonomy.yaml", f"Capability '{cap_key}': component must be a string")
                    )
                elif comp in all_components:
                    errors.append(
                        ValidationError(
                            "taxonomy.yaml",
                            f"Duplicate component '{comp}' in capabilities "
                            f"'{all_components[comp]}' and '{cap_key}'",
                        )
                    )
                else:
                    all_components[comp] = cap_key

    if not errors:
        # Validate that governance targets reference valid taxonomy services
        # (check that targets.services entries map to known ARM namespaces — not taxonomy components)
        pass  # Service validation deferred to namespace-level checks

    return errors


# ------------------------------------------------------------------ #
# CLI entry point
# ------------------------------------------------------------------ #


def main(argv: list[str] | None = None) -> int:
    """Entry point for the governance validator."""
    parser = argparse.ArgumentParser(description="Validate governance YAML files.")
    parser.add_argument("--all", action="store_true", help="Validate all governance areas.")
    parser.add_argument("--policies", action="store_true", help="Validate policy files.")
    parser.add_argument("--anti-patterns", dest="anti_patterns", action="store_true", help="Validate anti-patterns.")
    parser.add_argument("--standards", action="store_true", help="Validate standards files.")
    parser.add_argument("--workloads", action="store_true", help="Validate workload templates against policies.")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors.")

    args = parser.parse_args(argv)

    # Default to --all if no specific flags
    if not args.all and not args.policies and not args.anti_patterns and not args.standards and not args.workloads:
        args.all = True

    errors: list[ValidationError] = []
    areas: list[str] = []

    if args.all or args.policies:
        areas.append("policies")
        errors.extend(validate_policies())

    if args.all or args.anti_patterns:
        areas.append("anti-patterns")
        errors.extend(validate_anti_patterns())

    if args.all or args.standards:
        areas.append("standards")
        errors.extend(validate_standards())

    if args.all or args.workloads:
        areas.append("workloads")
        errors.extend(validate_workloads())

    # Taxonomy is always validated (part of governance structure)
    areas.append("taxonomy")
    errors.extend(validate_taxonomy())

    sys.stdout.write(f"Validating: {', '.join(areas)}\n")

    if not errors:
        sys.stdout.write("All governance files are valid.\n")
        return 0

    actual_errors = [e for e in errors if e.severity == "error"]
    warnings = [e for e in errors if e.severity == "warning"]

    for err in errors:
        sys.stdout.write(f"{err}\n")

    sys.stdout.write(f"\n{len(actual_errors)} error(s), {len(warnings)} warning(s)\n")

    if actual_errors:
        return 1
    if args.strict and warnings:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
