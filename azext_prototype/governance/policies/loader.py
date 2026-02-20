"""Convenience functions for policy loading."""

from __future__ import annotations

from pathlib import Path

from azext_prototype.governance.policies import PolicyEngine


def get_policy_engine(project_dir: str | None = None) -> PolicyEngine:
    """Create a PolicyEngine pre-loaded with built-in + project policies.

    Built-in policies ship with the extension under the ``policies/``
    package directory.  Project policies live in
    ``.prototype/policies/`` and can override or extend the built-ins.
    """
    dirs: list[Path] = [Path(__file__).parent]

    if project_dir:
        project_policies = Path(project_dir) / ".prototype" / "policies"
        if project_policies.is_dir():
            dirs.append(project_policies)

    engine = PolicyEngine()
    engine.load(dirs)
    return engine
