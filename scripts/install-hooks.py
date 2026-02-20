#!/usr/bin/env python
"""Install git hooks for the azext-prototype project.

Usage:
    python scripts/install-hooks.py

This copies the pre-commit hook into .git/hooks/ so that governance
validation runs automatically before every commit.  The hook validates:
  1. Policies (.policy.yaml)
  2. Workload templates (.template.yaml)
  3. Anti-patterns (anti_patterns/*.yaml)
  4. Standards (standards/**/*.yaml)
"""

import os
import shutil
import stat
import sys
from pathlib import Path


def main() -> int:
    """Install the pre-commit hook."""
    repo_root = Path(__file__).resolve().parent.parent
    hooks_dir = repo_root / ".git" / "hooks"
    src = repo_root / "scripts" / "pre-commit"

    if not hooks_dir.exists():
        print(f"Error: {hooks_dir} not found. Is this a git repository?")
        return 1

    if not src.exists():
        print(f"Error: {src} not found.")
        return 1

    dest = hooks_dir / "pre-commit"

    # Back up existing hook
    if dest.exists():
        backup = dest.with_suffix(".bak")
        shutil.copy2(dest, backup)
        print(f"Backed up existing hook to {backup}")

    shutil.copy2(src, dest)

    # Make executable on Unix
    if os.name != "nt":
        dest.chmod(dest.stat().st_mode | stat.S_IEXEC)

    print(f"Installed pre-commit hook to {dest}")
    print("Governance validation (policies, templates, anti-patterns, standards) will run before every commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
