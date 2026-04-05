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
      - id: "<ANTI-DOMAIN-NNN>"
        search_patterns: [<substrings to look for, case-insensitive>]
        safe_patterns:   [<substrings that exempt the match>]
        warning_message: "<human-readable warning>"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from azext_prototype.governance import safe_load_yaml

logger = logging.getLogger(__name__)

_ANTI_PATTERNS_DIR = Path(__file__).resolve().parent

# Module-level cache — populated on first load, cleared by reset_cache().
_cache: list["AntiPatternCheck"] | None = None


@dataclass
class AntiPatternCheck:
    """A single anti-pattern detection rule."""

    id: str
    domain: str
    search_patterns: list[str] = field(default_factory=list)
    safe_patterns: list[str] = field(default_factory=list)
    correct_patterns: list[str] = field(default_factory=list)
    warning_message: str = ""
    description: str = ""
    rationale: str = ""
    applies_to: list[str] = field(default_factory=list)  # agent names
    targets: list = field(default_factory=list)  # [{"services": [...], "search_patterns": [...], ...}]


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
        data = safe_load_yaml(yaml_file)
        if not isinstance(data, dict):
            continue

        domain = data.get("domain", yaml_file.stem)
        patterns_list = data.get("patterns", [])

        for idx, entry in enumerate(patterns_list, 1):
            if not isinstance(entry, dict):
                continue

            message = entry.get("warning_message", "")
            check_id = entry.get("id", f"{domain.upper()}-{idx:03d}")

            check_applies_to = entry.get("applies_to", [])
            if not isinstance(check_applies_to, list):
                check_applies_to = []

            targets_raw = entry.get("targets", [])
            if isinstance(targets_raw, dict):
                targets_raw = [targets_raw]
            if not isinstance(targets_raw, list):
                targets_raw = []

            # Aggregate search/safe/correct across all target entries
            search: list[str] = []
            safe: list[str] = []
            correct: list[str] = []
            for t in targets_raw:
                if isinstance(t, dict):
                    search.extend(t.get("search_patterns", []))
                    safe.extend(t.get("safe_patterns", []))
                    correct.extend(t.get("correct_patterns", []))

            if not search or not message:
                continue

            checks.append(
                AntiPatternCheck(
                    id=check_id,
                    domain=domain,
                    search_patterns=[s.lower() for s in search],
                    safe_patterns=[s.lower() for s in safe],
                    correct_patterns=correct,
                    warning_message=message,
                    description=str(entry.get("description", "")),
                    rationale=str(entry.get("rationale", "")),
                    applies_to=check_applies_to,
                    targets=targets_raw,
                )
            )

    _cache = checks
    return _cache


def scan(
    text: str,
    iac_tool: str | None = None,
    agent_name: str | None = None,
) -> list[str]:
    """Scan *text* for anti-pattern matches.

    Parameters
    ----------
    text:
        The AI-generated output to scan.
    iac_tool:
        If provided (e.g., ``"terraform"`` or ``"bicep"``), skip checks
        whose ``applies_to`` list is non-empty and does not contain
        this tool.  Backward compatible — for new format files, use
        *agent_name* instead.
    agent_name:
        If provided, skip checks whose ``applies_to`` list is non-empty
        and does not contain this agent name.

    Returns a list of human-readable warning strings (empty = clean).
    """
    checks = load()
    warnings: list[str] = []

    # Strip design notes — these explain WHY choices were made and contain
    # terms (e.g., "InstrumentationKey", "Blob Delegator") that trigger
    # false positives when scanned out of context.
    _DESIGN_MARKERS = (
        "## Key Design Decisions",
        "## Design Notes",
        "## Key Design Notes",
        "## Design Decisions",
    )
    scan_text = text
    for marker in _DESIGN_MARKERS:
        idx = scan_text.find(marker)
        if idx > 0:
            scan_text = scan_text[:idx]
            break

    lower = scan_text.lower()

    # Map iac_tool shorthand to agent name for filtering
    _TOOL_TO_AGENT = {"terraform": "terraform-agent", "bicep": "bicep-agent"}
    effective_agent = agent_name or _TOOL_TO_AGENT.get(iac_tool or "", "")

    for check in checks:
        # Skip checks not applicable to this agent
        if check.applies_to and effective_agent and effective_agent not in check.applies_to:
            continue

        for pattern in check.search_patterns:
            if pattern in lower:
                # Check safe patterns — if any match, skip this check
                if check.safe_patterns and any(s in lower for s in check.safe_patterns):
                    continue
                warnings.append(f"[{check.id}] {check.warning_message}")
                break  # one match per check is enough

    return warnings


def reset_cache() -> None:
    """Clear the module-level cache (useful in tests)."""
    global _cache  # noqa: PLW0603
    _cache = None
