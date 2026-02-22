#!/usr/bin/env python
"""Validate .policy.yaml files against the governance schema.

Usage:
    # Validate all built-in policies
    python -m azext_prototype.governance.policies.validate

    # Validate specific files
    python -m azext_prototype.governance.policies.validate path/to/policy.yaml ...

    # Validate a directory recursively
    python -m azext_prototype.governance.policies.validate --dir azext_prototype/policies/

    # Strict mode — warnings are treated as errors
    python -m azext_prototype.governance.policies.validate --strict

    # As a pre-commit hook (validates staged .policy.yaml files)
    python -m azext_prototype.governance.policies.validate --hook

Exit codes:
    0 — all files valid
    1 — validation errors found
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from azext_prototype.governance.policies import (
    validate_policy_directory,
    validate_policy_file,
)


def _get_staged_policy_files() -> list[Path]:
    """Return staged .policy.yaml files from the git index."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    return [Path(f) for f in result.stdout.strip().splitlines() if f.endswith(".policy.yaml")]


def main(argv: list[str] | None = None) -> int:
    """Entry point for the policy validator."""
    parser = argparse.ArgumentParser(description="Validate .policy.yaml files against the governance schema.")
    parser.add_argument(
        "files",
        nargs="*",
        help="Specific .policy.yaml files to validate.",
    )
    parser.add_argument(
        "--dir",
        type=str,
        default=None,
        help="Validate all .policy.yaml files under this directory recursively.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors.",
    )
    parser.add_argument(
        "--hook",
        action="store_true",
        help="Pre-commit hook mode: validate staged .policy.yaml files.",
    )

    args = parser.parse_args(argv)

    errors = []

    if args.hook:
        # Pre-commit mode — only check staged files
        staged = _get_staged_policy_files()
        if not staged:
            return 0
        sys.stdout.write(f"Validating {len(staged)} staged policy file(s)...\n")
        for path in staged:
            errors.extend(validate_policy_file(path))

    elif args.dir:
        # Directory mode
        directory = Path(args.dir)
        if not directory.is_dir():
            sys.stderr.write(f"Error: '{args.dir}' is not a directory\n")
            return 1
        policy_files = sorted(directory.rglob("*.policy.yaml"))
        sys.stdout.write(f"Validating {len(policy_files)} policy file(s) in {args.dir}...\n")
        errors.extend(validate_policy_directory(directory))

    elif args.files:
        # Explicit file list
        sys.stdout.write(f"Validating {len(args.files)} policy file(s)...\n")
        for filepath in args.files:
            path = Path(filepath)
            if not path.exists():
                sys.stderr.write(f"Error: '{filepath}' does not exist\n")
                return 1
            errors.extend(validate_policy_file(path))

    else:
        # Default: validate built-in policies
        builtin_dir = Path(__file__).parent
        policy_files = sorted(builtin_dir.rglob("*.policy.yaml"))
        sys.stdout.write(f"Validating {len(policy_files)} built-in policy file(s)...\n")
        errors.extend(validate_policy_directory(builtin_dir))

    # Report results
    if not errors:
        sys.stdout.write("All policy files are valid.\n")
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
