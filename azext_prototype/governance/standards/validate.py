#!/usr/bin/env python
"""Validate standards YAML files against the expected schema.

Usage:
    # Validate all built-in standards files
    python -m azext_prototype.governance.standards.validate

    # Validate specific files
    python -m azext_prototype.governance.standards.validate path/to/file.yaml ...

    # Validate a directory recursively
    python -m azext_prototype.governance.standards.validate --dir azext_prototype/governance/standards/

    # Strict mode — warnings are treated as errors
    python -m azext_prototype.governance.standards.validate --strict

    # As a pre-commit hook (validates staged standards YAML files)
    python -m azext_prototype.governance.standards.validate --hook

Exit codes:
    0 — all files valid
    1 — validation errors found
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


# ------------------------------------------------------------------ #
# Validation error
# ------------------------------------------------------------------ #

@dataclass
class ValidationError:
    """A single validation issue found in a standards file."""

    file: str
    message: str
    severity: str = "error"  # error | warning

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.file}: {self.message}"


# ------------------------------------------------------------------ #
# Schema validation
# ------------------------------------------------------------------ #

_STANDARDS_DIR = Path(__file__).resolve().parent

# Valid categories — keep in sync with the standards directory layout
VALID_CATEGORIES = ("principles", "terraform", "bicep", "application")


def validate_standards_file(path: Path) -> list[ValidationError]:
    """Validate a single standards YAML file against the schema.

    Returns a list of validation errors (empty means valid).
    """
    errors: list[ValidationError] = []
    filename = str(path)

    # ---- Parse YAML ----
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        errors.append(ValidationError(filename, f"Invalid YAML: {exc}"))
        return errors
    except OSError as exc:
        errors.append(ValidationError(filename, f"Cannot read file: {exc}"))
        return errors

    if not isinstance(data, dict):
        errors.append(ValidationError(filename, "Root element must be a mapping"))
        return errors

    # ---- domain (required) ----
    if "domain" not in data:
        errors.append(ValidationError(filename, "Missing required key: 'domain'"))
    elif not isinstance(data["domain"], str):
        errors.append(ValidationError(filename, "'domain' must be a string"))

    # ---- category (required) ----
    category = data.get("category")
    if category is None:
        errors.append(ValidationError(filename, "Missing required key: 'category'"))
    elif not isinstance(category, str):
        errors.append(ValidationError(filename, "'category' must be a string"))
    elif category not in VALID_CATEGORIES:
        errors.append(
            ValidationError(
                filename,
                f"category '{category}' is not valid. Allowed: {', '.join(VALID_CATEGORIES)}",
                severity="warning",
            )
        )

    # ---- description (recommended) ----
    if "description" not in data:
        errors.append(
            ValidationError(filename, "Missing 'description' — recommended for documentation", severity="warning")
        )

    # ---- principles (required) ----
    principles = data.get("principles")
    if principles is None:
        errors.append(ValidationError(filename, "Missing required key: 'principles'"))
        return errors

    if not isinstance(principles, list):
        errors.append(ValidationError(filename, "'principles' must be a list"))
        return errors

    if len(principles) == 0:
        errors.append(
            ValidationError(filename, "'principles' is empty — file has no standards", severity="warning")
        )

    principle_ids: set[str] = set()
    for i, entry in enumerate(principles):
        prefix = f"principles[{i}]"
        if not isinstance(entry, dict):
            errors.append(ValidationError(filename, f"{prefix}: must be a mapping"))
            continue

        # id — required, unique within file
        pid = entry.get("id")
        if pid is None:
            errors.append(ValidationError(filename, f"{prefix} missing required key: 'id'"))
        elif not isinstance(pid, str):
            errors.append(ValidationError(filename, f"{prefix}.id must be a string"))
        else:
            if pid in principle_ids:
                errors.append(ValidationError(filename, f"{prefix}: duplicate principle id '{pid}'"))
            principle_ids.add(pid)

        # name — required
        name = entry.get("name")
        if name is None:
            errors.append(ValidationError(filename, f"{prefix} missing required key: 'name'"))
        elif not isinstance(name, str):
            errors.append(ValidationError(filename, f"{prefix}.name must be a string"))

        # description — required
        desc = entry.get("description")
        if desc is None:
            errors.append(ValidationError(filename, f"{prefix} missing required key: 'description'"))
        elif not isinstance(desc, str):
            errors.append(ValidationError(filename, f"{prefix}.description must be a string"))

        # applies_to — optional, must be list of strings
        applies_to = entry.get("applies_to")
        if applies_to is not None:
            if not isinstance(applies_to, list):
                errors.append(ValidationError(filename, f"{prefix}.applies_to must be a list"))
            elif len(applies_to) == 0:
                errors.append(
                    ValidationError(
                        filename,
                        f"{prefix}.applies_to is empty — standard will apply to no agents",
                        severity="warning",
                    )
                )
            else:
                for j, agent in enumerate(applies_to):
                    if not isinstance(agent, str):
                        errors.append(ValidationError(filename, f"{prefix}.applies_to[{j}] must be a string"))

        # examples — optional, must be list of strings
        examples = entry.get("examples")
        if examples is not None:
            if not isinstance(examples, list):
                errors.append(ValidationError(filename, f"{prefix}.examples must be a list"))
            else:
                for j, ex in enumerate(examples):
                    if not isinstance(ex, str):
                        errors.append(ValidationError(filename, f"{prefix}.examples[{j}] must be a string"))

    return errors


def validate_standards_directory(directory: Path) -> list[ValidationError]:
    """Validate all YAML files under a directory recursively.

    Returns a combined list of validation errors across all files.
    """
    all_errors: list[ValidationError] = []
    if not directory.is_dir():
        return all_errors

    for yaml_file in sorted(directory.rglob("*.yaml")):
        all_errors.extend(validate_standards_file(yaml_file))

    return all_errors


# ------------------------------------------------------------------ #
# CLI
# ------------------------------------------------------------------ #

def _get_staged_standards_files() -> list[Path]:
    """Return staged standards YAML files from the git index."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    return [
        Path(f)
        for f in result.stdout.strip().splitlines()
        if f.endswith(".yaml") and "standards" in f and "validate" not in f
    ]


def main(argv: list[str] | None = None) -> int:
    """Entry point for the standards validator."""
    parser = argparse.ArgumentParser(
        description="Validate standards YAML files against the expected schema."
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Specific YAML files to validate.",
    )
    parser.add_argument(
        "--dir",
        type=str,
        default=None,
        help="Validate all YAML files under this directory recursively.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors.",
    )
    parser.add_argument(
        "--hook",
        action="store_true",
        help="Pre-commit hook mode: validate staged standards YAML files.",
    )

    args = parser.parse_args(argv)

    errors: list[ValidationError] = []

    if args.hook:
        staged = _get_staged_standards_files()
        if not staged:
            return 0
        sys.stdout.write(f"Validating {len(staged)} staged standards file(s)...\n")
        for path in staged:
            errors.extend(validate_standards_file(path))

    elif args.dir:
        directory = Path(args.dir)
        if not directory.is_dir():
            sys.stderr.write(f"Error: '{args.dir}' is not a directory\n")
            return 1
        yaml_files = sorted(directory.rglob("*.yaml"))
        sys.stdout.write(f"Validating {len(yaml_files)} standards file(s) in {args.dir}...\n")
        errors.extend(validate_standards_directory(directory))

    elif args.files:
        sys.stdout.write(f"Validating {len(args.files)} standards file(s)...\n")
        for filepath in args.files:
            path = Path(filepath)
            if not path.exists():
                sys.stderr.write(f"Error: '{filepath}' does not exist\n")
                return 1
            errors.extend(validate_standards_file(path))

    else:
        # Default: validate built-in standards
        yaml_files = sorted(_STANDARDS_DIR.rglob("*.yaml"))
        sys.stdout.write(f"Validating {len(yaml_files)} built-in standards file(s)...\n")
        errors.extend(validate_standards_directory(_STANDARDS_DIR))

    # Report results
    if not errors:
        sys.stdout.write("All standards files are valid.\n")
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
