"""Design standards — curated principles and reference patterns.

This module provides prescriptive guidance that developers and architects
create proactively.  Unlike governance policies (constraints) or
anti-patterns (detection), standards describe *how to build well*.

Directory layout::

    standards/
        principles/       Design principles (DRY, SOLID, etc.)
            design.yaml
            coding.yaml
        terraform/        Reference patterns per service type
        bicep/            Reference patterns per service type
        application/      Code patterns per language/framework
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_STANDARDS_DIR = Path(__file__).resolve().parent
_cache: list["Standard"] | None = None


@dataclass
class StandardPrinciple:
    """A single design principle or coding standard."""

    id: str
    name: str
    description: str
    applies_to: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)


@dataclass
class Standard:
    """A loaded standards document."""

    domain: str
    category: str
    description: str = ""
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
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("Could not load standards file %s: %s", yaml_file.name, exc)
            continue

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
                    applies_to=entry.get("applies_to", []),
                    examples=entry.get("examples", []),
                )
            )

        if principles:
            standards.append(
                Standard(
                    domain=data.get("domain", yaml_file.stem),
                    category=data.get("category", str(yaml_file.parent.relative_to(target))),
                    description=data.get("description", ""),
                    principles=principles,
                )
            )

    _cache = standards
    return _cache


def format_for_prompt(agent_name: str | None = None, category: str | None = None) -> str:
    """Format standards as text for injection into agent system prompts."""
    standards = load()
    if not standards:
        return ""

    filtered = standards
    if category:
        filtered = [s for s in filtered if s.category == category]

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


def reset_cache() -> None:
    """Clear the module-level cache (useful in tests)."""
    global _cache  # noqa: PLW0603
    _cache = None
