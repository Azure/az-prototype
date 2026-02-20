"""Anti-pattern detection — post-generation output scanning.

This module loads domain-specific anti-pattern definitions from YAML files
and provides a scanner that checks AI-generated output for known bad patterns.

Anti-patterns are **independent** from governance policies:

- Policies guide agents during generation ("generate code this way")
- Anti-patterns flag issues in output after generation ("we spotted this")

Some anti-patterns correlate with policies; many do not.  All are surfaced
as recommendations — the user decides whether to accept, override, or
regenerate.

Directory layout::

    anti_patterns/
        security.yaml
        networking.yaml
        authentication.yaml
        storage.yaml
        containers.yaml

Each YAML file follows the schema::

    domain: "<domain name>"
    description: "<what this domain covers>"
    patterns:
      - search_patterns: [<substrings to look for, case-insensitive>]
        safe_patterns:   [<substrings that exempt the match>]
        warning_message: "<human-readable warning>"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_ANTI_PATTERNS_DIR = Path(__file__).resolve().parent

# Module-level cache — populated on first load, cleared by reset_cache().
_cache: list["AntiPatternCheck"] | None = None


@dataclass
class AntiPatternCheck:
    """A single anti-pattern detection rule."""

    domain: str
    search_patterns: list[str] = field(default_factory=list)
    safe_patterns: list[str] = field(default_factory=list)
    warning_message: str = ""


def load(directory: Path | None = None) -> list[AntiPatternCheck]:
    """Load all anti-pattern YAML files from *directory* (cached).

    Falls back to the built-in ``anti_patterns/`` directory shipped with
    the extension.
    """
    global _cache  # noqa: PLW0603
    if _cache is not None:
        return _cache

    target = directory or _ANTI_PATTERNS_DIR
    checks: list[AntiPatternCheck] = []

    if not target.is_dir():
        logger.warning("Anti-patterns directory not found: %s", target)
        _cache = []
        return _cache

    for yaml_file in sorted(target.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("Could not load anti-pattern file %s: %s", yaml_file.name, exc)
            continue

        if not isinstance(data, dict):
            continue

        domain = data.get("domain", yaml_file.stem)
        for entry in data.get("patterns", []):
            if not isinstance(entry, dict):
                continue
            search = entry.get("search_patterns", [])
            safe = entry.get("safe_patterns", [])
            message = entry.get("warning_message", "")
            if not search or not message:
                continue
            checks.append(
                AntiPatternCheck(
                    domain=domain,
                    search_patterns=[s.lower() for s in search],
                    safe_patterns=[s.lower() for s in safe],
                    warning_message=message,
                )
            )

    _cache = checks
    return _cache


def scan(text: str) -> list[str]:
    """Scan *text* for anti-pattern matches.

    Returns a list of human-readable warning strings (empty = clean).
    """
    checks = load()
    warnings: list[str] = []
    lower = text.lower()

    for check in checks:
        for pattern in check.search_patterns:
            if pattern in lower:
                # Check safe patterns — if any match, skip this check
                if check.safe_patterns and any(s in lower for s in check.safe_patterns):
                    continue
                warnings.append(check.warning_message)
                break  # one match per check is enough

    return warnings


def reset_cache() -> None:
    """Clear the module-level cache (useful in tests)."""
    global _cache  # noqa: PLW0603
    _cache = None
