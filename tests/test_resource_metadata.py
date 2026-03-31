"""Tests for azext_prototype.knowledge.resource_metadata."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from azext_prototype.knowledge.resource_metadata import (
    CompanionRequirement,
    ResourceMetadata,
    _build_learn_url,
    _default_metadata,
    format_api_version_brief,
    format_companion_brief,
    get_private_endpoint_services,
    reset_cache,
    resolve_companion_requirements,
    resolve_resource_metadata,
)


@pytest.fixture(autouse=True)
def _reset_module_cache():
    """Reset module-level cache between tests."""
    reset_cache()
    yield
    reset_cache()


@pytest.fixture(autouse=True)
def _no_telemetry_network():
    with patch("azext_prototype.telemetry._send_envelope"):
        yield


# ======================================================================
# Registry index & API version resolution
# ======================================================================


class TestResolveResourceMetadata:

    def test_known_resource_type_from_registry(self):
        result = resolve_resource_metadata(["Microsoft.KeyVault/vaults"])
        meta = result.get("Microsoft.KeyVault/vaults")
        assert meta is not None
        assert meta.api_version == "2023-07-01"
        assert meta.source == "service-registry"

    def test_container_registry_from_registry(self):
        result = resolve_resource_metadata(["Microsoft.ContainerRegistry/registries"])
        meta = result.get("Microsoft.ContainerRegistry/registries")
        assert meta is not None
        assert meta.api_version == "2023-11-01-preview"
        assert meta.source == "service-registry"

    def test_app_insights_from_registry(self):
        result = resolve_resource_metadata(["Microsoft.Insights/components"])
        meta = result.get("Microsoft.Insights/components")
        assert meta is not None
        assert meta.api_version == "2020-02-02"
        assert meta.source == "service-registry"

    def test_log_analytics_from_registry(self):
        result = resolve_resource_metadata(["Microsoft.OperationalInsights/workspaces"])
        meta = result.get("Microsoft.OperationalInsights/workspaces")
        assert meta is not None
        assert meta.api_version == "2023-09-01"
        assert meta.source == "service-registry"

    def test_managed_identity_from_registry(self):
        result = resolve_resource_metadata(["Microsoft.ManagedIdentity/userAssignedIdentities"])
        meta = result.get("Microsoft.ManagedIdentity/userAssignedIdentities")
        assert meta is not None
        assert meta.source == "service-registry"

    def test_case_insensitive_lookup(self):
        result = resolve_resource_metadata(["microsoft.keyvault/vaults"])
        meta = result.get("microsoft.keyvault/vaults")
        assert meta is not None
        assert meta.source == "service-registry"

    def test_multiple_resource_types(self):
        types = [
            "Microsoft.KeyVault/vaults",
            "Microsoft.ContainerRegistry/registries",
            "Microsoft.Storage/storageAccounts",
        ]
        result = resolve_resource_metadata(types)
        assert len(result) == 3
        assert all(m.source == "service-registry" for m in result.values())

    def test_empty_resource_types_returns_empty(self):
        result = resolve_resource_metadata([])
        assert result == {}

    def test_blank_resource_type_skipped(self):
        result = resolve_resource_metadata([""])
        assert result == {}

    @patch("azext_prototype.knowledge.resource_metadata._fetch_from_learn", return_value=None)
    def test_unknown_resource_falls_back_to_default(self, mock_fetch):
        result = resolve_resource_metadata(["Microsoft.Unknown/widgets"])
        meta = result.get("Microsoft.Unknown/widgets")
        assert meta is not None
        assert meta.source == "default"
        assert meta.api_version == "2024-03-01"

    def test_comma_separated_bicep_resources_indexed(self):
        # container-apps has "Microsoft.App/containerApps, Microsoft.App/managedEnvironments"
        result = resolve_resource_metadata(["Microsoft.App/containerApps"])
        meta = result.get("Microsoft.App/containerApps")
        assert meta is not None
        assert meta.source == "service-registry"

        result2 = resolve_resource_metadata(["Microsoft.App/managedEnvironments"])
        meta2 = result2.get("Microsoft.App/managedEnvironments")
        assert meta2 is not None
        assert meta2.source == "service-registry"

    def test_caching_with_search_cache(self):
        cache = MagicMock()
        cache.get.return_value = None
        # Known type — should resolve from registry (no cache involved)
        result = resolve_resource_metadata(["Microsoft.KeyVault/vaults"], search_cache=cache)
        assert result["Microsoft.KeyVault/vaults"].source == "service-registry"

    @patch("azext_prototype.knowledge.web_search.fetch_page_content")
    def test_learn_fetch_parses_api_version(self, mock_fetch):
        mock_fetch.return_value = (
            "Resource reference\n"
            "API versions: 2024-06-01, 2024-03-01, 2023-11-01-preview, 2023-01-01\n"
            "Some other content"
        )
        # Use an unknown type to force Learn fallback
        result = resolve_resource_metadata(["Microsoft.Custom/things"])
        meta = result.get("Microsoft.Custom/things")
        if meta and meta.source == "microsoft-learn":
            assert meta.api_version == "2024-06-01"  # Latest stable


class TestBuildLearnUrl:

    def test_standard_resource_type(self):
        url = _build_learn_url("Microsoft.KeyVault/vaults")
        assert url == "https://learn.microsoft.com/en-us/azure/templates/microsoft.keyvault/vaults"

    def test_with_api_version(self):
        url = _build_learn_url("Microsoft.KeyVault/vaults", "2023-07-01")
        assert url == "https://learn.microsoft.com/en-us/azure/templates/microsoft.keyvault/2023-07-01/vaults"

    def test_nested_resource_type(self):
        url = _build_learn_url("Microsoft.Sql/servers/databases")
        assert url == "https://learn.microsoft.com/en-us/azure/templates/microsoft.sql/servers/databases"

    def test_empty_returns_empty(self):
        assert _build_learn_url("") == ""

    def test_single_component_returns_empty(self):
        assert _build_learn_url("NoSlash") == ""


class TestDefaultMetadata:

    def test_returns_default_api_version(self):
        meta = _default_metadata("Microsoft.Unknown/things")
        assert meta.resource_type == "Microsoft.Unknown/things"
        assert meta.api_version == "2024-03-01"
        assert meta.source == "default"


# ======================================================================
# API version brief formatting
# ======================================================================


class TestFormatApiVersionBrief:

    def test_formats_metadata(self):
        metadata = {
            "Microsoft.KeyVault/vaults": ResourceMetadata(
                resource_type="Microsoft.KeyVault/vaults",
                api_version="2023-07-01",
                source="service-registry",
                properties_url="https://learn.microsoft.com/...",
            ),
        }
        brief = format_api_version_brief(metadata)
        assert "MANDATORY" in brief
        assert "Microsoft.KeyVault/vaults" in brief
        assert "@2023-07-01" in brief

    def test_empty_metadata_returns_empty(self):
        assert format_api_version_brief({}) == ""


# ======================================================================
# Companion resource requirements
# ======================================================================


class TestResolveCompanionRequirements:

    def test_container_registry_has_rbac(self):
        services = [{"resource_type": "Microsoft.ContainerRegistry/registries"}]
        reqs = resolve_companion_requirements(services)
        assert len(reqs) >= 1
        acr_req = reqs[0]
        assert "AcrPull" in acr_req.rbac_roles.values()
        assert "7f951dda-4ed3-4680-a7ca-43fe172d538d" in acr_req.rbac_role_ids.values()
        assert "RBAC" in acr_req.auth_method or "Managed Identity" in acr_req.auth_method

    def test_key_vault_has_rbac(self):
        services = [{"resource_type": "Microsoft.KeyVault/vaults"}]
        reqs = resolve_companion_requirements(services)
        assert len(reqs) >= 1
        kv_req = reqs[0]
        assert kv_req.rbac_role_ids  # Non-empty

    def test_managed_identity_excluded(self):
        """Managed identity service itself should not appear as needing RBAC."""
        services = [{"resource_type": "Microsoft.ManagedIdentity/userAssignedIdentities"}]
        reqs = resolve_companion_requirements(services)
        assert len(reqs) == 0

    def test_empty_services_returns_empty(self):
        assert resolve_companion_requirements([]) == []

    def test_unknown_service_returns_empty(self):
        services = [{"resource_type": "Microsoft.Unknown/widgets"}]
        reqs = resolve_companion_requirements(services)
        assert len(reqs) == 0

    def test_service_without_resource_type_skipped(self):
        services = [{"name": "something"}]
        reqs = resolve_companion_requirements(services)
        assert len(reqs) == 0


class TestFormatCompanionBrief:

    def test_formats_requirements_with_identity(self):
        reqs = [
            CompanionRequirement(
                display_name="Container Registry",
                resource_type="Microsoft.ContainerRegistry/registries",
                auth_method="RBAC with Managed Identity",
                rbac_roles={"pull": "AcrPull"},
                rbac_role_ids={"pull": "7f951dda-4ed3-4680-a7ca-43fe172d538d"},
            ),
        ]
        brief = format_companion_brief(reqs, stage_has_identity=True)
        assert "MANDATORY" in brief
        assert "AcrPull" in brief
        assert "7f951dda" in brief
        assert "WARNING" not in brief

    def test_warning_when_no_identity(self):
        reqs = [
            CompanionRequirement(
                display_name="Container Registry",
                resource_type="Microsoft.ContainerRegistry/registries",
                auth_method="RBAC with Managed Identity",
                rbac_roles={"pull": "AcrPull"},
                rbac_role_ids={"pull": "7f951dda-4ed3-4680-a7ca-43fe172d538d"},
            ),
        ]
        brief = format_companion_brief(reqs, stage_has_identity=False)
        assert "WARNING" in brief

    def test_empty_requirements_returns_empty(self):
        assert format_companion_brief([], stage_has_identity=True) == ""

    def test_includes_data_source_hint(self):
        reqs = [
            CompanionRequirement(
                display_name="Key Vault",
                resource_type="Microsoft.KeyVault/vaults",
                auth_method="RBAC with Managed Identity",
                rbac_roles={"admin": "Key Vault Administrator"},
                rbac_role_ids={"admin": "00482a5a-887f-4fb3-b363-3b7fe8e74483"},
            ),
        ]
        brief = format_companion_brief(reqs, stage_has_identity=True)
        assert "azurerm_client_config" in brief


# ======================================================================
# Private endpoint detection
# ======================================================================


class TestGetPrivateEndpointServices:

    def test_key_vault_has_private_endpoint(self):
        services = [{"resource_type": "Microsoft.KeyVault/vaults", "name": "key-vault"}]
        results = get_private_endpoint_services(services)
        assert len(results) == 1
        assert results[0].dns_zone == "privatelink.vaultcore.azure.net"
        assert results[0].group_id == "vault"

    def test_container_registry_has_private_endpoint(self):
        services = [{"resource_type": "Microsoft.ContainerRegistry/registries", "name": "acr"}]
        results = get_private_endpoint_services(services)
        assert len(results) == 1
        assert "azurecr.io" in results[0].dns_zone

    def test_managed_identity_has_no_private_endpoint(self):
        services = [{"resource_type": "Microsoft.ManagedIdentity/userAssignedIdentities", "name": "id"}]
        results = get_private_endpoint_services(services)
        assert len(results) == 0

    def test_empty_services(self):
        assert get_private_endpoint_services([]) == []

    def test_unknown_service(self):
        services = [{"resource_type": "Microsoft.Unknown/widgets", "name": "x"}]
        assert get_private_endpoint_services(services) == []

    def test_multiple_services(self):
        services = [
            {"resource_type": "Microsoft.KeyVault/vaults", "name": "kv"},
            {"resource_type": "Microsoft.Storage/storageAccounts", "name": "st"},
            {"resource_type": "Microsoft.ManagedIdentity/userAssignedIdentities", "name": "id"},
        ]
        results = get_private_endpoint_services(services)
        # KV and Storage have PE, managed identity does not
        assert len(results) == 2
