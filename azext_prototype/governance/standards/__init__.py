"""Design standards — curated principles and reference patterns.

This module provides prescriptive guidance that developers and architects
create proactively.  Unlike governance policies (constraints) or
anti-patterns (detection), standards describe *how to build well*.

Directory layout::

    standards/
        application/          Application code patterns
            dotnet.yaml
            python.yaml
        iac/                  Infrastructure-as-Code patterns
            bicep.yaml
            terraform.yaml
        principles/           Design principles
            coding.yaml
            design.yaml
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from azext_prototype.governance import safe_load_yaml

logger = logging.getLogger(__name__)

_STANDARDS_DIR = Path(__file__).resolve().parent
_cache: list["Standard"] | None = None


@dataclass
class StandardPrinciple:
    """A single design principle or coding standard."""

    id: str
    name: str  # kept for backward compat; new format merges into description
    description: str
    rationale: str = ""
    applies_to: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)


@dataclass
class Standard:
    """A loaded standards document."""

    domain: str
    description: str = ""
    last_updated: str = ""
    principles: list[StandardPrinciple] = field(default_factory=list)


def load(directory: Path | None = None) -> list[Standard]:
    """Load all standards YAML files recursively (cached)."""
    global _cache  # noqa: PLW0603
    if _cache is not None:
        return _cache

    target = directory or _STANDARDS_DIR
    standards: list[Standard] = []

    if not target.is_dir():
        logger.warning("Standards directory not found: %s", target)
        _cache = []
        return _cache

    for yaml_file in sorted(target.rglob("*.yaml")):
        data = safe_load_yaml(yaml_file)
        if not isinstance(data, dict):
            continue

        principles = []
        for entry in data.get("principles", []):
            if not isinstance(entry, dict):
                continue
            principles.append(
                StandardPrinciple(
                    id=entry.get("id", ""),
                    name=entry.get("name", ""),
                    description=entry.get("description", ""),
                    rationale=entry.get("rationale", ""),
                    applies_to=entry.get("applies_to", []),
                    examples=entry.get("examples", []),
                )
            )

        if principles:
            standards.append(
                Standard(
                    domain=data.get("domain", yaml_file.stem),
                    description=data.get("description", ""),
                    last_updated=data.get("last_updated", ""),
                    principles=principles,
                )
            )

    _cache = standards
    return _cache


def format_for_prompt(agent_name: str | None = None, domain: str | None = None) -> str:
    """Format standards as text for injection into agent system prompts."""
    standards = load()
    if not standards:
        return ""

    filtered = standards
    if domain:
        filtered = [s for s in filtered if s.domain == domain]

    if not filtered:
        return ""

    sections: list[str] = ["## Design Standards\n"]

    for standard in filtered:
        sections.append(f"### {standard.domain}")
        if standard.description:
            sections.append(standard.description)
        sections.append("")

        for p in standard.principles:
            if agent_name and p.applies_to and agent_name not in p.applies_to:
                continue
            sections.append(f"- **[{p.id}] {p.name}**: {p.description}")
            for ex in p.examples:
                sections.append(f"  - {ex}")

        sections.append("")

    return "\n".join(sections)


def format_for_qa(iac_tool: str | None = None, layer: str = "infra") -> str:
    """Format standards for QA context injection.

    Returns standards relevant to the stage's technology stack:
    - IaC stages (core/infra/data): IaC-specific + universal coding/design
    - App stages: all application + universal coding/design

    Parameters
    ----------
    iac_tool:
        ``"terraform"`` or ``"bicep"`` — selects IaC standards.
    layer:
        Stage layer — ``"core"``, ``"infra"``, ``"data"``, ``"app"``.
    """
    standards = load()
    if not standards:
        return ""

    # Determine which domains to include
    include_domains: set[str] = {"principles"}  # always include coding/design
    if layer in ("core", "infra", "data"):
        if iac_tool == "terraform":
            include_domains.add("terraform")
        elif iac_tool == "bicep":
            include_domains.add("bicep")
    elif layer == "app":
        include_domains.add("application")

    filtered = [s for s in standards if s.domain in include_domains]
    if not filtered:
        return ""

    sections: list[str] = ["## Applicable Standards\n"]
    for standard in filtered:
        sections.append(f"### {standard.domain}")
        for p in standard.principles:
            sections.append(f"- **[{p.id}]** {p.description}")
        sections.append("")

    return "\n".join(sections)


def reset_cache() -> None:
    """Clear the module-level cache (useful in tests)."""
    global _cache  # noqa: PLW0603
    _cache = None
