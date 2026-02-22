#!/usr/bin/env python
"""Validate anti-pattern YAML files against the expected schema.

Usage:
    # Validate all built-in anti-pattern files
    python -m azext_prototype.governance.anti_patterns.validate

    # Validate specific files
    python -m azext_prototype.governance.anti_patterns.validate path/to/file.yaml ...

    # Validate a directory
    python -m azext_prototype.governance.anti_patterns.validate --dir azext_prototype/governance/anti_patterns/

    # Strict mode — warnings are treated as errors
    python -m azext_prototype.governance.anti_patterns.validate --strict

    # As a pre-commit hook (validates staged anti-pattern YAML files)
    python -m azext_prototype.governance.anti_patterns.validate --hook

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
    """A single validation issue found in an anti-pattern file."""

    file: str
    message: str
    severity: str = "error"  # error | warning

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.file}: {self.message}"


# ------------------------------------------------------------------ #
# Schema validation
# ------------------------------------------------------------------ #

_ANTI_PATTERNS_DIR = Path(__file__).resolve().parent


def validate_anti_pattern_file(path: Path) -> list[ValidationError]:
    """Validate a single anti-pattern YAML file against the schema.

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

    # ---- description (recommended) ----
    if "description" not in data:
        errors.append(
            ValidationError(filename, "Missing 'description' — recommended for documentation", severity="warning")
        )

    # ---- patterns (required) ----
    patterns = data.get("patterns")
    if patterns is None:
        errors.append(ValidationError(filename, "Missing required key: 'patterns'"))
        return errors

    if not isinstance(patterns, list):
        errors.append(ValidationError(filename, "'patterns' must be a list"))
        return errors

    if len(patterns) == 0:
        errors.append(
            ValidationError(filename, "'patterns' is empty — file has no detection rules", severity="warning")
        )

    for i, entry in enumerate(patterns):
        prefix = f"patterns[{i}]"
        if not isinstance(entry, dict):
            errors.append(ValidationError(filename, f"{prefix}: must be a mapping"))
            continue

        # search_patterns — required, non-empty list of strings
        search = entry.get("search_patterns")
        if search is None:
            errors.append(ValidationError(filename, f"{prefix} missing required key: 'search_patterns'"))
        elif not isinstance(search, list):
            errors.append(ValidationError(filename, f"{prefix}.search_patterns must be a list"))
        elif len(search) == 0:
            errors.append(ValidationError(filename, f"{prefix}.search_patterns is empty"))
        else:
            for j, s in enumerate(search):
                if not isinstance(s, str):
                    errors.append(ValidationError(filename, f"{prefix}.search_patterns[{j}] must be a string"))

        # safe_patterns — optional, must be list of strings if present
        safe = entry.get("safe_patterns")
        if safe is not None:
            if not isinstance(safe, list):
                errors.append(ValidationError(filename, f"{prefix}.safe_patterns must be a list"))
            else:
                for j, s in enumerate(safe):
                    if not isinstance(s, str):
                        errors.append(ValidationError(filename, f"{prefix}.safe_patterns[{j}] must be a string"))

        # warning_message — required, non-empty string
        msg = entry.get("warning_message")
        if msg is None:
            errors.append(ValidationError(filename, f"{prefix} missing required key: 'warning_message'"))
        elif not isinstance(msg, str):
            errors.append(ValidationError(filename, f"{prefix}.warning_message must be a string"))
        elif not msg.strip():
            errors.append(ValidationError(filename, f"{prefix}.warning_message is empty"))

    return errors


def validate_anti_pattern_directory(directory: Path) -> list[ValidationError]:
    """Validate all YAML files under a directory.

    Returns a combined list of validation errors across all files.
    """
    all_errors: list[ValidationError] = []
    if not directory.is_dir():
        return all_errors

    for yaml_file in sorted(directory.glob("*.yaml")):
        all_errors.extend(validate_anti_pattern_file(yaml_file))

    return all_errors


# ------------------------------------------------------------------ #
# CLI
# ------------------------------------------------------------------ #


def _get_staged_anti_pattern_files() -> list[Path]:
    """Return staged anti-pattern YAML files from the git index."""
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
        if f.endswith(".yaml") and "anti_patterns" in f and "validate" not in f
    ]


def main(argv: list[str] | None = None) -> int:
    """Entry point for the anti-pattern validator."""
    parser = argparse.ArgumentParser(description="Validate anti-pattern YAML files against the expected schema.")
    parser.add_argument(
        "files",
        nargs="*",
        help="Specific YAML files to validate.",
    )
    parser.add_argument(
        "--dir",
        type=str,
        default=None,
        help="Validate all YAML files under this directory.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors.",
    )
    parser.add_argument(
        "--hook",
        action="store_true",
        help="Pre-commit hook mode: validate staged anti-pattern YAML files.",
    )

    args = parser.parse_args(argv)

    errors: list[ValidationError] = []

    if args.hook:
        staged = _get_staged_anti_pattern_files()
        if not staged:
            return 0
        sys.stdout.write(f"Validating {len(staged)} staged anti-pattern file(s)...\n")
        for path in staged:
            errors.extend(validate_anti_pattern_file(path))

    elif args.dir:
        directory = Path(args.dir)
        if not directory.is_dir():
            sys.stderr.write(f"Error: '{args.dir}' is not a directory\n")
            return 1
        yaml_files = sorted(directory.glob("*.yaml"))
        sys.stdout.write(f"Validating {len(yaml_files)} anti-pattern file(s) in {args.dir}...\n")
        errors.extend(validate_anti_pattern_directory(directory))

    elif args.files:
        sys.stdout.write(f"Validating {len(args.files)} anti-pattern file(s)...\n")
        for filepath in args.files:
            path = Path(filepath)
            if not path.exists():
                sys.stderr.write(f"Error: '{filepath}' does not exist\n")
                return 1
            errors.extend(validate_anti_pattern_file(path))

    else:
        # Default: validate built-in anti-patterns
        yaml_files = sorted(_ANTI_PATTERNS_DIR.glob("*.yaml"))
        sys.stdout.write(f"Validating {len(yaml_files)} built-in anti-pattern file(s)...\n")
        errors.extend(validate_anti_pattern_directory(_ANTI_PATTERNS_DIR))

    # Report results
    if not errors:
        sys.stdout.write("All anti-pattern files are valid.\n")
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
