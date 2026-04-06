"""Post-generation transforms — deterministic fixes for known AI fabrications.

Transforms run after file generation and before QA review. They detect
and automatically fix known fabrication patterns (e.g., ARM property
placement errors) without AI calls or token cost.

Directory layout::

    transforms/
    ├── __init__.py              # This module
    ├── monitoring/
    │   └── log-analytics.transform.yaml
    ├── data/
    │   └── cosmos-db.transform.yaml
    └── (future: networking/, compute/, etc.)

Usage::

    from azext_prototype.governance.transforms import apply

    content, applied_ids = apply(
        content=generated_code,
        services=["Microsoft.OperationalInsights/workspaces"],
        iac_tool="terraform",
    )
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from azext_prototype.governance import safe_load_yaml

logger = logging.getLogger(__name__)

_TRANSFORMS_DIR = Path(__file__).parent
_cache: list["Transform"] | None = None


@dataclass
class Transform:
    """A single deterministic transform rule."""

    id: str
    domain: str
    description: str = ""
    rationale: str = ""
    applies_to: list[str] = field(default_factory=list)
    targets: list = field(default_factory=list)
    search: str = ""
    replace: str = ""
    transform_type: str = "regex"  # "regex" or "structured"


def load(directory: Path | None = None) -> list[Transform]:
    """Load all transform YAML files (cached).

    Falls back to the built-in ``transforms/`` directory shipped
    with the extension.
    """
    global _cache  # noqa: PLW0603
    if _cache is not None:
        return _cache

    target = directory or _TRANSFORMS_DIR
    transforms: list[Transform] = []

    if not target.is_dir():
        logger.warning("Transforms directory not found: %s", target)
        _cache = []
        return _cache

    for yaml_file in sorted(target.rglob("*.transform.yaml")):
        data = safe_load_yaml(yaml_file)
        if not isinstance(data, dict):
            continue

        domain = data.get("domain", yaml_file.stem.replace(".transform", ""))
        transform_list = data.get("transforms", [])

        for idx, entry in enumerate(transform_list, 1):
            if not isinstance(entry, dict):
                continue

            transform_id = entry.get("id", f"TFM-{domain.upper()}-{idx:03d}")
            search = entry.get("search", "")
            replace_val = entry.get("replace", "")

            if not search:
                continue

            check_applies_to = entry.get("applies_to", [])
            if not isinstance(check_applies_to, list):
                check_applies_to = []

            targets_raw = entry.get("targets", [])
            if isinstance(targets_raw, dict):
                targets_raw = [targets_raw]
            if not isinstance(targets_raw, list):
                targets_raw = []

            transforms.append(
                Transform(
                    id=transform_id,
                    domain=domain,
                    description=str(entry.get("description", "")),
                    rationale=str(entry.get("rationale", "")),
                    applies_to=check_applies_to,
                    targets=targets_raw,
                    search=search,
                    replace=replace_val,
                    transform_type=str(entry.get("type", "regex")),
                )
            )

    _cache = transforms
    return _cache


def apply(
    content: str,
    services: list[str] | None = None,
    iac_tool: str | None = None,
    agent_name: str | None = None,
) -> tuple[str, list[str]]:
    """Apply transforms to generated content.

    Parameters
    ----------
    content:
        The generated IaC code to transform.
    services:
        ARM resource type namespaces for this stage.
    iac_tool:
        ``"terraform"`` or ``"bicep"``.
    agent_name:
        Agent that generated the content.

    Returns
    -------
    tuple[str, list[str]]:
        ``(transformed_content, list_of_applied_transform_ids)``.
        If no transforms matched, content is returned unchanged.
    """
    transforms = load()
    if not transforms:
        return content, []

    _TOOL_TO_AGENT = {"terraform": "terraform-agent", "bicep": "bicep-agent"}
    effective_agent = agent_name or _TOOL_TO_AGENT.get(iac_tool or "", "")

    svc_set = {s.lower() for s in services} if services else None

    applied: list[str] = []
    result = content

    for tfm in transforms:
        # Filter by agent
        if tfm.applies_to and effective_agent and effective_agent not in tfm.applies_to:
            continue

        # Filter by service namespace
        if svc_set is not None:
            tfm_services: set[str] = set()
            for t in tfm.targets:
                if isinstance(t, dict):
                    tfm_services.update(s.lower() for s in t.get("services", []))
            if tfm_services and not (tfm_services & svc_set):
                continue

        # Apply the transform
        if tfm.transform_type == "regex":
            try:
                new_result, count = re.subn(tfm.search, tfm.replace, result, flags=re.MULTILINE | re.DOTALL)
                if count > 0:
                    result = new_result
                    applied.append(tfm.id)
                    logger.debug("Transform %s applied (%d replacements)", tfm.id, count)
            except re.error as e:
                logger.warning("Transform %s has invalid regex: %s", tfm.id, e)

    return result, applied


def reset_cache() -> None:
    """Clear the module-level cache (useful in tests)."""
    global _cache  # noqa: PLW0603
    _cache = None
