#!/usr/bin/env python
"""Validate standards YAML files against the unified schema.

Usage:
    python -m azext_prototype.governance.standards.validate
    python -m azext_prototype.governance.standards.validate --strict
    python -m azext_prototype.governance.standards.validate --dir path/to/dir

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

_STANDARDS_DIR = Path(__file__).resolve().parent

_REQUIRED_TOP_KEYS = {"kind", "domain", "description", "last_updated", "principles"}


@dataclass
class ValidationError:
    """A single validation issue found in a standards file."""

    file: str
    message: str
    severity: str = "error"

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.file}: {self.message}"


def validate_standards_file(path: Path) -> list[ValidationError]:
    """Validate a single standards YAML file."""
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

    if data.get("kind") != "standard":
        errors.append(ValidationError(filename, f"kind must be 'standard', got '{data.get('kind')}'"))

    principles = data.get("principles", [])
    if not isinstance(principles, list):
        errors.append(ValidationError(filename, "'principles' must be a list"))
        return errors

    principle_ids: set[str] = set()
    for i, entry in enumerate(principles):
        prefix = f"principles[{i}]"
        if not isinstance(entry, dict):
            errors.append(ValidationError(filename, f"{prefix}: must be a mapping"))
            continue

        pid = entry.get("id", "")
        if not pid:
            errors.append(ValidationError(filename, f"{prefix}: missing 'id'"))
        elif pid in principle_ids:
            errors.append(ValidationError(filename, f"{prefix}: duplicate id '{pid}'"))
        else:
            principle_ids.add(pid)

        if not entry.get("description"):
            errors.append(ValidationError(filename, f"{prefix} ({pid}): missing 'description'"))

        applies_to = entry.get("applies_to")
        if applies_to is not None and not isinstance(applies_to, list):
            errors.append(ValidationError(filename, f"{prefix} ({pid}): 'applies_to' must be a list"))

    return errors


def validate_standards_directory(directory: Path) -> list[ValidationError]:
    """Validate all standards YAML files under a directory recursively."""
    all_errors: list[ValidationError] = []
    if not directory.is_dir():
        return all_errors
    for f in sorted(directory.rglob("*.yaml")):
        all_errors.extend(validate_standards_file(f))
    return all_errors


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Validate standards YAML files.")
    parser.add_argument("files", nargs="*", help="Specific files to validate")
    parser.add_argument("--dir", type=str, help="Directory to validate recursively")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    args = parser.parse_args(argv)

    if args.dir:
        errors = validate_standards_directory(Path(args.dir))
    elif args.files:
        errors = []
        for f in args.files:
            errors.extend(validate_standards_file(Path(f)))
    else:
        errors = validate_standards_directory(_STANDARDS_DIR)

    if not errors:
        print("All standards files valid.")
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
