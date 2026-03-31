"""Azure resource metadata — API versions and companion requirements.

Pre-fetches correct API versions and companion resource requirements
(RBAC roles, managed identity, data sources) for ARM resource types
before code generation.  Two resolution paths:

1. **Service registry** (fast): ``service-registry.yaml`` already has
   ``bicep_api_version``, ``rbac_roles``, ``rbac_role_ids``, and
   ``authentication`` per service.
2. **Microsoft Learn** (fallback): fetches the Azure ARM template page
   for unregistered resource types and parses the latest API version.

All functions return empty/default results on failure — never raise.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Lazy-loaded module-level cache
_registry_index: dict[str, str] | None = None
_registry_data: dict[str, Any] | None = None


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------


@dataclass
class ResourceMetadata:
    """Resolved metadata for a single ARM resource type."""

    resource_type: str
    api_version: str
    source: str  # "service-registry" | "microsoft-learn" | "default"
    properties_url: str = ""


@dataclass
class CompanionRequirement:
    """Companion resource requirement for a service."""

    display_name: str
    resource_type: str
    auth_method: str
    rbac_roles: dict[str, str] = field(default_factory=dict)
    rbac_role_ids: dict[str, str] = field(default_factory=dict)
    auth_notes: list[str] = field(default_factory=list)
    has_private_endpoint: bool = False
    private_dns_zone: str = ""


# ------------------------------------------------------------------
# Registry index (ARM resource type → service-registry key)
# ------------------------------------------------------------------


def _load_registry() -> tuple[dict[str, str], dict[str, Any]]:
    """Build reverse index and load full registry data.

    Returns ``(index, registry_data)`` where *index* maps lowercase ARM
    resource types to service-registry keys.
    """
    global _registry_index, _registry_data  # noqa: PLW0603
    if _registry_index is not None and _registry_data is not None:
        return _registry_index, _registry_data  # type: ignore[return-value]

    try:
        from azext_prototype.knowledge import KnowledgeLoader

        loader = KnowledgeLoader()
        data = loader.load_service_registry()
    except Exception:
        logger.debug("Could not load service registry")
        _registry_index = {}
        _registry_data = {}
        return _registry_index, _registry_data

    index: dict[str, str] = {}
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        bicep_res = entry.get("bicep_resource", "")
        if not bicep_res:
            continue
        # Some entries are comma-separated (e.g. "Microsoft.App/containerApps, Microsoft.App/managedEnvironments")
        for arm_type in bicep_res.split(","):
            arm_type = arm_type.strip()
            if arm_type:
                index[arm_type.lower()] = key

    _registry_index = index
    _registry_data = data
    return index, data


def reset_cache() -> None:
    """Clear cached registry data (useful for tests)."""
    global _registry_index, _registry_data
    _registry_index = None
    _registry_data = None


# ------------------------------------------------------------------
# API version resolution
# ------------------------------------------------------------------


def resolve_resource_metadata(
    resource_types: list[str],
    search_cache: Any = None,
) -> dict[str, ResourceMetadata]:
    """Resolve API version for each ARM resource type.

    Resolution order:
    1. Service registry (``bicep_api_version`` field) — no HTTP.
    2. Microsoft Learn ARM template page — HTTP fetch + parse.
    3. Default from ``requirements.py``.

    Args:
        resource_types: ARM resource types (e.g. ``["Microsoft.KeyVault/vaults"]``).
        search_cache: Optional ``SearchCache`` instance for HTTP dedup.

    Returns:
        Mapping from resource type to :class:`ResourceMetadata`.
    """
    index, data = _load_registry()
    result: dict[str, ResourceMetadata] = {}

    for rt in resource_types:
        if not rt:
            continue
        rt_lower = rt.lower()

        # 1. Service registry lookup
        service_key = index.get(rt_lower)
        if service_key and service_key in data:
            entry = data[service_key]
            api_ver = entry.get("bicep_api_version", "")
            if api_ver:
                result[rt] = ResourceMetadata(
                    resource_type=rt,
                    api_version=api_ver,
                    source="service-registry",
                    properties_url=_build_learn_url(rt, api_ver),
                )
                continue

        # 2. Microsoft Learn fetch
        meta = _fetch_from_learn(rt, search_cache)
        if meta:
            result[rt] = meta
            continue

        # 3. Default fallback
        result[rt] = _default_metadata(rt)

    return result


def _build_learn_url(resource_type: str, api_version: str = "") -> str:
    """Build the Microsoft Learn ARM template reference URL."""
    # e.g. "Microsoft.KeyVault/vaults" → "microsoft.keyvault/vaults"
    parts = resource_type.lower().split("/")
    if len(parts) >= 2:
        provider = parts[0]  # e.g. "microsoft.keyvault"
        resource = "/".join(parts[1:])  # e.g. "vaults"
        if api_version:
            return f"https://learn.microsoft.com/en-us/azure/templates/{provider}/{api_version}/{resource}"
        return f"https://learn.microsoft.com/en-us/azure/templates/{provider}/{resource}"
    return ""


def _fetch_from_learn(resource_type: str, search_cache: Any) -> ResourceMetadata | None:
    """Fetch API version from the Microsoft Learn ARM templates page."""
    url = _build_learn_url(resource_type)
    if not url:
        return None

    # Check cache first
    cache_key = f"resource_metadata:{resource_type.lower()}"
    if search_cache is not None:
        cached = search_cache.get(cache_key)
        if cached is not None:
            return cached

    try:
        from azext_prototype.knowledge.web_search import fetch_page_content

        content = fetch_page_content(url, max_chars=4000)
        if not content:
            return None

        # Parse API versions from page content
        # Pattern: dates like 2024-03-01, 2023-11-01-preview
        versions = re.findall(r"\b(\d{4}-\d{2}-\d{2}(?:-preview)?)\b", content)
        if not versions:
            return None

        # Prefer latest non-preview, then latest preview
        stable = sorted({v for v in versions if "preview" not in v}, reverse=True)
        preview = sorted({v for v in versions if "preview" in v}, reverse=True)
        api_ver = stable[0] if stable else (preview[0] if preview else None)
        if not api_ver:
            return None

        meta = ResourceMetadata(
            resource_type=resource_type,
            api_version=api_ver,
            source="microsoft-learn",
            properties_url=_build_learn_url(resource_type, api_ver),
        )

        # Cache the result
        if search_cache is not None:
            search_cache.put(cache_key, meta)

        return meta
    except Exception:
        logger.debug("Failed to fetch resource metadata for %s", resource_type)
        return None


def _default_metadata(resource_type: str) -> ResourceMetadata:
    """Return default metadata when registry and Learn both fail."""
    try:
        from azext_prototype.requirements import get_dependency_version

        api_ver = get_dependency_version("azure_api") or "2024-03-01"
    except Exception:
        api_ver = "2024-03-01"

    return ResourceMetadata(
        resource_type=resource_type,
        api_version=api_ver,
        source="default",
    )


# ------------------------------------------------------------------
# Format API version brief for injection into generation prompt
# ------------------------------------------------------------------


def format_api_version_brief(metadata: dict[str, ResourceMetadata]) -> str:
    """Format resolved metadata as a prompt section.

    Returns empty string if no metadata.
    """
    if not metadata:
        return ""

    lines = [
        "## Resource API Versions (MANDATORY — use EXACTLY these versions)",
        "Do NOT use any other API version. These are verified correct.\n",
    ]
    for rt, meta in metadata.items():
        line = f"- {rt}: @{meta.api_version}"
        if meta.properties_url:
            line += f"\n  Reference: {meta.properties_url}"
        lines.append(line)

    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------
# Companion resource requirements
# ------------------------------------------------------------------


def resolve_companion_requirements(
    services: list[dict],
) -> list[CompanionRequirement]:
    """Resolve companion resource requirements for a list of services.

    For each service with a ``resource_type``, looks up RBAC roles,
    authentication method, and private endpoint config from the service
    registry.  Returns only services that have non-trivial auth/RBAC
    requirements.
    """
    index, data = _load_registry()
    requirements: list[CompanionRequirement] = []

    for svc in services:
        rt = svc.get("resource_type", "")
        if not rt:
            continue

        service_key = index.get(rt.lower())
        if not service_key or service_key not in data:
            continue

        entry = data[service_key]
        auth = entry.get("authentication", {}) or {}
        auth_method = auth.get("method", "") or ""
        rbac_roles = entry.get("rbac_roles", {}) or {}
        rbac_role_ids = entry.get("rbac_role_ids", {}) or {}

        # Skip services with no meaningful auth requirements
        if not auth_method and not rbac_roles:
            continue
        # Skip the managed identity service itself
        if "managedidentity" in rt.lower().replace("/", "").replace(".", ""):
            continue

        auth_notes_raw = auth.get("notes", "") or ""
        auth_notes = [
            line.strip("- ").strip() for line in auth_notes_raw.strip().splitlines() if line.strip("- ").strip()
        ]

        pe = entry.get("private_endpoint", {}) or {}
        has_pe = bool(pe.get("dns_zone"))

        requirements.append(
            CompanionRequirement(
                display_name=entry.get("display_name", rt),
                resource_type=rt,
                auth_method=auth_method,
                rbac_roles=rbac_roles,
                rbac_role_ids=rbac_role_ids,
                auth_notes=auth_notes,
                has_private_endpoint=has_pe,
                private_dns_zone=pe.get("dns_zone", "") or "",
            )
        )

    return requirements


def format_companion_brief(
    requirements: list[CompanionRequirement],
    stage_has_identity: bool,
) -> str:
    """Format companion requirements as a prompt section.

    Args:
        requirements: Resolved companion requirements.
        stage_has_identity: Whether the stage already includes a managed identity resource.

    Returns:
        Formatted prompt section, or empty string if no requirements.
    """
    if not requirements:
        return ""

    lines = [
        "## Companion Resource Requirements (MANDATORY)",
        "These are derived from the Azure service registry. Failure to implement",
        "them will result in broken authentication and a failed build.\n",
    ]

    needs_rbac = any(r.rbac_role_ids for r in requirements)
    if needs_rbac and not stage_has_identity:
        lines.append(
            "WARNING: This stage requires RBAC role assignments but does NOT include a "
            "managed identity. You MUST either create a user-assigned managed identity in "
            "this stage OR reference one from a prior stage via terraform_remote_state.\n"
        )

    if needs_rbac:
        lines.append(
            "REQUIRED data source (add to data.tf or providers.tf):\n" '  data "azurerm_client_config" "current" {}\n'
        )

    for req in requirements:
        lines.append(f"### {req.display_name} ({req.resource_type})")
        if req.auth_method:
            lines.append(f"- Authentication: {req.auth_method}")

        if req.rbac_role_ids:
            lines.append("- REQUIRED RBAC role assignments on the managed identity:")
            for role_key, role_id in req.rbac_role_ids.items():
                role_name = req.rbac_roles.get(role_key, role_key)
                lines.append(f"  * {role_name} (GUID: {role_id})")

        if req.auth_notes:
            for note in req.auth_notes:
                lines.append(f"- {note}")

        lines.append("")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Private endpoint detection
# ------------------------------------------------------------------


@dataclass
class PrivateEndpointRequirement:
    """A service that requires a private endpoint."""

    service_name: str
    display_name: str
    resource_type: str
    dns_zone: str
    group_id: str


def get_private_endpoint_services(services: list[dict]) -> list[PrivateEndpointRequirement]:
    """Return services that require private endpoints.

    Checks the service registry for a non-null ``private_endpoint.dns_zone``
    for each service's ``resource_type``.
    """
    index, data = _load_registry()
    results: list[PrivateEndpointRequirement] = []

    for svc in services:
        rt = svc.get("resource_type", "")
        if not rt:
            continue

        service_key = index.get(rt.lower())
        if not service_key or service_key not in data:
            continue

        entry = data[service_key]
        pe = entry.get("private_endpoint", {}) or {}
        dns_zone = pe.get("dns_zone") or ""
        if not dns_zone:
            continue

        results.append(
            PrivateEndpointRequirement(
                service_name=svc.get("name", service_key),
                display_name=entry.get("display_name", rt),
                resource_type=rt,
                dns_zone=dns_zone,
                group_id=pe.get("group_id", "") or "",
            )
        )

    return results
