#!/usr/bin/env python
"""Validate anti-pattern YAML files against the unified schema.

Usage:
    python -m azext_prototype.governance.anti_patterns.validate
    python -m azext_prototype.governance.anti_patterns.validate --strict
    python -m azext_prototype.governance.anti_patterns.validate --dir path/to/dir

Exit codes:
    0 — all files valid
    1 — validation errors found
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

_AP_DIR = Path(__file__).resolve().parent

_REQUIRED_TOP_KEYS = {"kind", "category", "description", "last_updated", "patterns"}


@dataclass
class ValidationError:
    """A single validation issue found in an anti-pattern file."""

    file: str
    message: str
    severity: str = "error"

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.file}: {self.message}"


def validate_anti_pattern_file(path: Path) -> list[ValidationError]:
    """Validate a single anti-pattern YAML file."""
    errors: list[ValidationError] = []
    filename = str(path)

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

    for key in _REQUIRED_TOP_KEYS:
        if key not in data:
            errors.append(ValidationError(filename, f"Missing required key: '{key}'"))

    if data.get("kind") != "anti-pattern":
        errors.append(ValidationError(filename, f"kind must be 'anti-pattern', got '{data.get('kind')}'"))

    patterns = data.get("patterns", [])
    if not isinstance(patterns, list):
        errors.append(ValidationError(filename, "'patterns' must be a list"))
        return errors

    pattern_ids: set[str] = set()
    for i, entry in enumerate(patterns):
        prefix = f"patterns[{i}]"
        if not isinstance(entry, dict):
            errors.append(ValidationError(filename, f"{prefix}: must be a mapping"))
            continue

        pid = entry.get("id", "")
        if not pid:
            errors.append(ValidationError(filename, f"{prefix}: missing 'id'"))
        elif pid in pattern_ids:
            errors.append(ValidationError(filename, f"{prefix}: duplicate id '{pid}'"))
        else:
            pattern_ids.add(pid)

        if not entry.get("description"):
            errors.append(ValidationError(filename, f"{prefix} ({pid}): missing 'description'"))

        if not entry.get("warning_message"):
            errors.append(ValidationError(filename, f"{prefix} ({pid}): missing 'warning_message'"))

        targets = entry.get("targets")
        if not isinstance(targets, dict):
            errors.append(ValidationError(filename, f"{prefix} ({pid}): missing or invalid 'targets'"))
        elif not targets.get("search_patterns"):
            errors.append(ValidationError(filename, f"{prefix} ({pid}): missing 'targets.search_patterns'"))

        applies_to = entry.get("applies_to")
        if applies_to is not None and not isinstance(applies_to, list):
            errors.append(ValidationError(filename, f"{prefix} ({pid}): 'applies_to' must be a list"))

    return errors


def validate_anti_pattern_directory(directory: Path) -> list[ValidationError]:
    """Validate all anti-pattern YAML files in a directory."""
    all_errors: list[ValidationError] = []
    if not directory.is_dir():
        return all_errors
    for f in sorted(directory.glob("*.yaml")):
        all_errors.extend(validate_anti_pattern_file(f))
    return all_errors


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Validate anti-pattern YAML files.")
    parser.add_argument("files", nargs="*", help="Specific files to validate")
    parser.add_argument("--dir", type=str, help="Directory to validate")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    args = parser.parse_args(argv)

    if args.dir:
        errors = validate_anti_pattern_directory(Path(args.dir))
    elif args.files:
        errors = []
        for f in args.files:
            errors.extend(validate_anti_pattern_file(Path(f)))
    else:
        errors = validate_anti_pattern_directory(_AP_DIR)

    if not errors:
        print("All anti-pattern files valid.")
        return 0

    for e in errors:
        print(e)

    actual = [e for e in errors if e.severity == "error"]
    warnings = [e for e in errors if e.severity == "warning"]

    if actual or (args.strict and warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
