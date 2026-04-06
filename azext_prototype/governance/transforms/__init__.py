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
from typing import Callable

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
    handler: str = ""  # Python function name for structured transforms


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
                    handler=str(entry.get("handler", "")),
                )
            )

    _cache = transforms
    return _cache


def apply(
    content: str,
    services: list[str] | None = None,
    iac_tool: str | None = None,
    agent_name: str | None = None,
    stage: dict | None = None,
    stage_content: str | None = None,
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
    stage:
        Stage dict with ``stage`` (number), ``name``, ``dir``, etc.
        Used by structured handlers that need stage context.

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
        elif tfm.transform_type == "structured" and tfm.handler:
            handler_fn = _STRUCTURED_HANDLERS.get(tfm.handler)
            if handler_fn:
                import inspect

                params = inspect.signature(handler_fn).parameters
                kwargs: dict = {}
                if "stage" in params:
                    kwargs["stage"] = stage
                if "stage_content" in params:
                    kwargs["stage_content"] = stage_content
                new_result = handler_fn(result, **kwargs) if kwargs else handler_fn(result)
                if new_result != result:
                    result = new_result
                    applied.append(tfm.id)
                    logger.debug("Transform %s applied (structured handler: %s)", tfm.id, tfm.handler)
            else:
                logger.warning("Transform %s references unknown handler: %s", tfm.id, tfm.handler)

    return result, applied


# ------------------------------------------------------------------
# Structured transform handlers
# ------------------------------------------------------------------


def _remove_unused_remote_state(content: str, stage_content: str | None = None) -> str:
    """Remove terraform_remote_state blocks that are never referenced.

    Scans for ``data "terraform_remote_state" "name"`` blocks and checks
    if ``data.terraform_remote_state.name`` appears anywhere in the full
    stage content (all files).  Removes unreferenced blocks and their
    state path variables.

    Parameters
    ----------
    stage_content:
        Concatenated content of ALL files in the stage.  Used for
        cross-file reference checking (e.g., remote state declared in
        main.tf but referenced in locals.tf).
    """
    # Find all remote state block names
    rs_pattern = re.compile(
        r'data\s+"terraform_remote_state"\s+"(\w+)"\s*\{[^}]*\}',
        re.DOTALL,
    )
    matches = list(rs_pattern.finditer(content))
    if not matches:
        return content

    # Use full stage content for reference checking if available
    reference_text = stage_content or content

    result = content
    for match in reversed(matches):  # reverse to preserve offsets
        name = match.group(1)
        ref = f"data.terraform_remote_state.{name}"

        # Count references in full stage content, excluding the declaration block
        ref_count = reference_text.count(ref)
        block_self_refs = match.group(0).count(ref)
        external_refs = ref_count - block_self_refs

        if external_refs <= 0:
            # Remove the block from this file's content
            result = result[: match.start()] + result[match.end() :]
            logger.debug("Removed unused terraform_remote_state.%s", name)

            # Remove corresponding state path variable
            var_pattern = re.compile(
                rf'variable\s+"{name}_state_path"\s*\{{[^}}]*\}}\s*\n?',
                re.DOTALL,
            )
            result = var_pattern.sub("", result)

    return result


def _remove_private_endpoint_resources(content: str) -> str:
    """Remove private endpoint and DNS zone resources from non-networking stages.

    Matches ``azapi_resource`` blocks whose type contains:
    - ``Microsoft.Network/privateEndpoints``
    - ``Microsoft.Network/privateDnsZones``
    - ``privateDnsZoneGroups``
    - ``virtualNetworkLinks`` under privateDnsZones

    Also removes associated locals, variables, and outputs that reference
    the removed resources.
    """
    pe_types = (
        "microsoft.network/privateendpoints",
        "microsoft.network/privatednszones",
        "privatednszonegroups",
        "virtualnetworklinks",
    )

    # Find resource block starts and use brace counting to find the end
    block_start_pattern = re.compile(
        r'resource\s+"azapi_resource"\s+"(\w+)"\s*\{',
    )

    removed_names: list[str] = []
    result = content

    for match in reversed(list(block_start_pattern.finditer(result))):
        resource_name = match.group(1)
        # Find the matching closing brace using brace counting
        start = match.start()
        brace_start = match.end() - 1  # position of opening {
        depth = 1
        pos = brace_start + 1
        while pos < len(result) and depth > 0:
            if result[pos] == "{":
                depth += 1
            elif result[pos] == "}":
                depth -= 1
            pos += 1
        if depth != 0:
            continue  # malformed block, skip

        block_text = result[start:pos]
        # Check if this block's type is a PE/DNS type
        type_match = re.search(r'type\s*=\s*"([^"]+)"', block_text)
        if not type_match:
            continue
        resource_type = type_match.group(1).lower()
        if any(pt in resource_type for pt in pe_types):
            # Remove the block plus any trailing whitespace/newlines
            end = pos
            while end < len(result) and result[end] in ("\n", "\r", " "):
                end += 1
            result = result[:start] + result[end:]
            removed_names.append(resource_name)
            logger.debug("Removed PE/DNS resource: azapi_resource.%s", resource_name)

    if not removed_names:
        return content

    # Remove outputs referencing removed resources
    for name in removed_names:
        output_pattern = re.compile(
            rf'output\s+"\w*{re.escape(name)}\w*"\s*\{{[^}}]*\}}\s*\n?',
            re.DOTALL,
        )
        result = output_pattern.sub("", result)

    # Remove variables for PE/DNS (common patterns)
    for var_name in ("private_endpoint_subnet_id", "private_dns_zone_id", "enable_private_endpoint"):
        var_pattern = re.compile(
            rf'variable\s+"{var_name}"\s*\{{[^}}]*\}}\s*\n?',
            re.DOTALL,
        )
        result = var_pattern.sub("", result)

    return result


def _add_response_export_values(content: str) -> str:
    """Add ``response_export_values = ["*"]`` to azapi_resource blocks missing it.

    Finds each ``resource "azapi_resource" "name" { ... }`` block and checks
    if ``response_export_values`` appears inside it.  If missing, inserts it
    after the ``parent_id`` line (or after ``type`` if no ``parent_id``).
    """
    # Match azapi_resource blocks
    block_pattern = re.compile(
        r'(resource\s+"azapi_resource"\s+"\w+"\s*\{)(.*?\n)((?:.*?\n)*?)(})',
        re.DOTALL,
    )

    def _inject(match: re.Match) -> str:  # type: ignore[type-arg]
        full = match.group(0)
        if "response_export_values" in full:
            return full  # already has it

        header = match.group(1)
        first_line = match.group(2)
        body = match.group(3)
        closing = match.group(4)

        # Find insertion point: after parent_id, or after location, or after type
        lines = (first_line + body).splitlines(keepends=True)
        insert_idx = len(lines)  # fallback: before closing brace
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("parent_id"):
                insert_idx = i + 1
                break
            if stripped.startswith("location"):
                insert_idx = i + 1
            elif stripped.startswith("type") and insert_idx == len(lines):
                insert_idx = i + 1

        # Detect indentation from the type/parent_id line
        indent = "  "
        if insert_idx > 0 and insert_idx <= len(lines):
            prev_line = lines[insert_idx - 1]
            leading = len(prev_line) - len(prev_line.lstrip())
            indent = " " * leading

        lines.insert(insert_idx, f'\n{indent}response_export_values = ["*"]\n')
        return header + "".join(lines) + closing

    new_content = block_pattern.sub(_inject, content)
    if new_content != content:
        logger.debug("Added response_export_values to azapi_resource blocks")
    return new_content


def _add_resource_group_parent_id(content: str) -> str:
    """Add ``parent_id`` to resource group azapi_resource blocks missing it.

    Finds ``azapi_resource`` blocks whose type contains
    ``Microsoft.Resources/resourceGroups`` and injects
    ``parent_id = "/subscriptions/${var.subscription_id}"``
    after the ``name`` line.
    """
    # Match azapi_resource blocks with resourceGroups type
    block_pattern = re.compile(
        r'(resource\s+"azapi_resource"\s+"\w+"\s*\{)(.*?)(})',
        re.DOTALL,
    )

    def _inject(match: re.Match) -> str:  # type: ignore[type-arg]
        full = match.group(0)
        if "resourcegroups" not in full.lower():
            return full
        if "parent_id" in full:
            return full  # already has it

        header = match.group(1)
        body = match.group(2)
        closing = match.group(3)

        # Insert after the name line
        lines = body.splitlines(keepends=True)
        insert_idx = len(lines)
        for i, line in enumerate(lines):
            if line.strip().startswith("name"):
                insert_idx = i + 1
                break

        # Detect indentation
        indent = "  "
        if insert_idx > 0 and insert_idx <= len(lines):
            prev_line = lines[insert_idx - 1]
            leading = len(prev_line) - len(prev_line.lstrip())
            indent = " " * leading

        lines.insert(insert_idx, f'{indent}parent_id = "/subscriptions/${{var.subscription_id}}"\n')
        return header + "".join(lines) + closing

    new_content = block_pattern.sub(_inject, content)
    if new_content != content:
        logger.debug("Added parent_id to resource group azapi_resource")
    return new_content


_STRUCTURED_HANDLERS: dict[str, Callable] = {
    "remove_unused_remote_state": _remove_unused_remote_state,
    "remove_private_endpoint_resources": _remove_private_endpoint_resources,
    "add_response_export_values": _add_response_export_values,
    "add_resource_group_parent_id": _add_resource_group_parent_id,
}


def reset_cache() -> None:
    """Clear the module-level cache (useful in tests)."""
    global _cache  # noqa: PLW0603
    _cache = None
