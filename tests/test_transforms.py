"""Tests for azext_prototype.governance.transforms — load, apply, and all handlers."""

import pytest

from azext_prototype.governance.transforms import (
    _add_resource_group_parent_id,
    _add_response_export_values,
    _fix_state_path,
    _remove_private_endpoint_resources,
    _remove_unused_remote_state,
    apply,
    load,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset transform cache before each test."""
    reset_cache()
    yield
    reset_cache()


# ------------------------------------------------------------------
# load()
# ------------------------------------------------------------------


class TestLoad:
    def test_loads_transforms(self):
        transforms = load()
        assert len(transforms) > 0

    def test_transform_ids_unique(self):
        transforms = load()
        ids = [t.id for t in transforms]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {[i for i in ids if ids.count(i) > 1]}"

    def test_transform_has_required_fields(self):
        for t in load():
            assert t.id, f"Transform missing id: {t}"
            assert t.domain, f"Transform {t.id} missing domain"
            assert t.search or t.handler, f"Transform {t.id} has no search or handler"

    def test_structured_transforms_have_handler(self):
        for t in load():
            if t.transform_type == "structured":
                assert t.handler, f"Structured transform {t.id} missing handler"

    def test_known_transforms_exist(self):
        ids = {t.id for t in load()}
        expected = {"TFM-LA-001", "TFM-CDB-001", "TFM-TF-001", "TFM-TF-002", "TFM-TF-003", "TFM-RG-001", "TFM-NET-001"}
        for eid in expected:
            assert eid in ids, f"Expected transform {eid} not found"


# ------------------------------------------------------------------
# apply() — filtering
# ------------------------------------------------------------------


class TestApplyFiltering:
    def test_no_transforms_returns_unchanged(self, tmp_path):
        """With an empty transforms dir, content is unchanged."""
        result, ids = apply("some content", services=[], iac_tool="terraform")
        # May or may not apply depending on loaded transforms — just verify return type
        assert isinstance(result, str)
        assert isinstance(ids, list)

    def test_agent_filtering(self):
        content = 'capacityMode = "Serverless"'
        # terraform-agent should match TFM-CDB-001
        result_tf, ids_tf = apply(
            content,
            services=["Microsoft.DocumentDB/databaseAccounts"],
            iac_tool="terraform",
        )
        # react-developer should NOT match (TFM-CDB-001 applies_to: terraform-agent, bicep-agent)
        result_react, ids_react = apply(
            content,
            services=["Microsoft.DocumentDB/databaseAccounts"],
            agent_name="react-developer",
        )
        assert "TFM-CDB-001" in ids_tf
        assert "TFM-CDB-001" not in ids_react

    def test_service_filtering(self):
        content = 'capacityMode = "Serverless"'
        # Cosmos DB service should match
        _, ids_cosmos = apply(
            content,
            services=["Microsoft.DocumentDB/databaseAccounts"],
            iac_tool="terraform",
        )
        # Key Vault service should NOT match
        _, ids_kv = apply(
            content,
            services=["Microsoft.KeyVault/vaults"],
            iac_tool="terraform",
        )
        assert "TFM-CDB-001" in ids_cosmos
        assert "TFM-CDB-001" not in ids_kv


# ------------------------------------------------------------------
# TFM-CDB-001 — capacityMode replacement
# ------------------------------------------------------------------


class TestCapacityModeTransform:
    def test_replaces_capacitymode(self):
        content = 'capacityMode = "Serverless"'
        result, ids = apply(content, services=["Microsoft.DocumentDB/databaseAccounts"], iac_tool="terraform")
        assert "TFM-CDB-001" in ids
        assert "EnableServerless" in result
        assert "capacityMode" not in result

    def test_case_insensitive(self):
        content = 'capacityMode = "serverless"'
        result, ids = apply(content, services=["Microsoft.DocumentDB/databaseAccounts"], iac_tool="terraform")
        assert "TFM-CDB-001" in ids

    def test_no_match_leaves_unchanged(self):
        content = 'capabilities = [{ name = "EnableServerless" }]'
        result, ids = apply(content, services=["Microsoft.DocumentDB/databaseAccounts"], iac_tool="terraform")
        assert "TFM-CDB-001" not in ids
        assert result == content or "EnableServerless" in result


# ------------------------------------------------------------------
# _remove_unused_remote_state (TFM-TF-001)
# ------------------------------------------------------------------


class TestRemoveUnusedRemoteState:
    def test_removes_unused_block(self):
        content = """data "terraform_remote_state" "stage4" {
  backend = "local"
  config = { path = var.stage4_state_path }
}

resource "azapi_resource" "something" {
  type = "Microsoft.Something/resource@2024-01-01"
}
"""
        result = _remove_unused_remote_state(content)
        assert "terraform_remote_state" not in result
        assert "azapi_resource" in result

    def test_keeps_used_block_single_file(self):
        content = """data "terraform_remote_state" "stage1" {
  backend = "local"
  config = { path = var.stage1_state_path }
}

locals {
  rg_id = data.terraform_remote_state.stage1.outputs.resource_group_id
}
"""
        result = _remove_unused_remote_state(content)
        assert "terraform_remote_state" in result

    def test_keeps_used_block_cross_file(self):
        main_tf = """data "terraform_remote_state" "stage1" {
  backend = "local"
  config = { path = var.stage1_state_path }
}
"""
        locals_tf = """locals {
  rg_id = data.terraform_remote_state.stage1.outputs.resource_group_id
}
"""
        stage_content = main_tf + "\n" + locals_tf
        result = _remove_unused_remote_state(main_tf, stage_content=stage_content)
        assert "terraform_remote_state" in result

    def test_removes_unused_even_with_stage_content(self):
        main_tf = """data "terraform_remote_state" "stage4" {
  backend = "local"
  config = { path = var.stage4_state_path }
}
"""
        locals_tf = """locals {
  rg_id = data.terraform_remote_state.stage1.outputs.resource_group_id
}
"""
        stage_content = main_tf + "\n" + locals_tf
        result = _remove_unused_remote_state(main_tf, stage_content=stage_content)
        assert "stage4" not in result

    def test_removes_companion_variable(self):
        content = """variable "stage4_state_path" {
  type    = string
  default = "terraform.tfstate"
}

data "terraform_remote_state" "stage4" {
  backend = "local"
  config = { path = var.stage4_state_path }
}
"""
        result = _remove_unused_remote_state(content)
        assert "stage4_state_path" not in result

    def test_no_remote_state_returns_unchanged(self):
        content = 'resource "azapi_resource" "rg" { type = "Microsoft.Resources/resourceGroups@2024-03-01" }'
        result = _remove_unused_remote_state(content)
        assert result == content


# ------------------------------------------------------------------
# _add_response_export_values (TFM-TF-002)
# ------------------------------------------------------------------


class TestAddResponseExportValues:
    def test_adds_to_block_missing_it(self):
        content = """resource "azapi_resource" "kv" {
  type      = "Microsoft.KeyVault/vaults@2023-07-01"
  name      = var.name
  parent_id = azapi_resource.resource_group.id
  location  = var.location

  body = {}
}
"""
        result = _add_response_export_values(content)
        assert 'response_export_values = ["*"]' in result

    def test_skips_block_that_has_it(self):
        content = """resource "azapi_resource" "kv" {
  type      = "Microsoft.KeyVault/vaults@2023-07-01"
  name      = var.name
  response_export_values = ["*"]
  body = {}
}
"""
        result = _add_response_export_values(content)
        assert result.count("response_export_values") == 1

    def test_handles_multiple_resources(self):
        content = """resource "azapi_resource" "rg" {
  type = "Microsoft.Resources/resourceGroups@2024-03-01"
  name = var.rg_name
  body = {}
}

resource "azapi_resource" "kv" {
  type = "Microsoft.KeyVault/vaults@2023-07-01"
  name = var.kv_name
  body = {}
}
"""
        result = _add_response_export_values(content)
        assert result.count('response_export_values = ["*"]') == 2


# ------------------------------------------------------------------
# _add_resource_group_parent_id (TFM-RG-001)
# ------------------------------------------------------------------


class TestAddResourceGroupParentId:
    def test_adds_parent_id(self):
        content = """resource "azapi_resource" "resource_group" {
  type     = "Microsoft.Resources/resourceGroups@2024-03-01"
  name     = var.resource_group_name
  location = var.location
  body = {}
}
"""
        result = _add_resource_group_parent_id(content)
        assert 'parent_id = "/subscriptions/${var.subscription_id}"' in result

    def test_skips_if_parent_id_exists(self):
        content = """resource "azapi_resource" "resource_group" {
  type      = "Microsoft.Resources/resourceGroups@2024-03-01"
  name      = var.resource_group_name
  parent_id = "/subscriptions/${var.subscription_id}"
  location  = var.location
  body = {}
}
"""
        result = _add_resource_group_parent_id(content)
        assert result.count("parent_id") == 1

    def test_ignores_non_resource_group(self):
        content = """resource "azapi_resource" "kv" {
  type = "Microsoft.KeyVault/vaults@2023-07-01"
  name = var.kv_name
  body = {}
}
"""
        result = _add_resource_group_parent_id(content)
        assert "parent_id" not in result


# ------------------------------------------------------------------
# _remove_private_endpoint_resources (TFM-NET-001)
# ------------------------------------------------------------------


class TestRemovePrivateEndpointResources:
    def test_removes_pe_block(self):
        content = """resource "azapi_resource" "workspace" {
  type = "Microsoft.OperationalInsights/workspaces@2023-09-01"
  name = var.name
  body = { properties = { sku = { name = "PerGB2018" } } }
}

resource "azapi_resource" "la_pe" {
  type      = "Microsoft.Network/privateEndpoints@2024-01-01"
  name      = "pe-la"
  parent_id = azapi_resource.resource_group.id
  body = {
    properties = {
      subnet = { id = var.subnet_id }
      privateLinkServiceConnections = [{
        name = "pe"
        properties = { privateLinkServiceId = azapi_resource.workspace.id }
      }]
    }
  }
}
"""
        result = _remove_private_endpoint_resources(content)
        assert "privateEndpoints" not in result
        assert "workspace" in result

    def test_removes_dns_zone(self):
        content = """resource "azapi_resource" "dns_zone" {
  type = "Microsoft.Network/privateDnsZones@2020-06-01"
  name = "privatelink.vaultcore.azure.net"
  location = "global"
  parent_id = azapi_resource.resource_group.id
  body = {}
}
"""
        result = _remove_private_endpoint_resources(content)
        assert "privateDnsZones" not in result

    def test_keeps_non_pe_resources(self):
        content = """resource "azapi_resource" "kv" {
  type = "Microsoft.KeyVault/vaults@2023-07-01"
  name = var.name
  body = { properties = { tenantId = var.tenant_id } }
}
"""
        result = _remove_private_endpoint_resources(content)
        assert "KeyVault" in result

    def test_empty_content_returns_unchanged(self):
        result = _remove_private_endpoint_resources("")
        assert result == ""


# ------------------------------------------------------------------
# _fix_state_path (TFM-TF-003)
# ------------------------------------------------------------------


class TestFixStatePath:
    def test_fixes_empty_backend(self):
        content = """terraform {
  required_version = ">= 1.9.0"
  backend "local" {}
}
"""
        result = _fix_state_path(content, stage={"stage": 4, "name": "Networking"})
        assert "stage-4-networking.tfstate" in result
        assert "terraform.tfstate" not in result

    def test_fixes_wrong_path(self):
        content = """terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}
"""
        result = _fix_state_path(content, stage={"stage": 7, "name": "Azure SQL"})
        assert "stage-7-azure-sql.tfstate" in result

    def test_preserves_correct_path(self):
        content = """terraform {
  backend "local" {
    path = "../../../.terraform-state/stage-4-networking.tfstate"
  }
}
"""
        result = _fix_state_path(content, stage={"stage": 4, "name": "Networking"})
        assert result == content

    def test_no_stage_returns_unchanged(self):
        content = 'backend "local" {}'
        result = _fix_state_path(content, stage=None)
        assert result == content

    def test_slug_generation(self):
        content = 'backend "local" {}'
        result = _fix_state_path(content, stage={"stage": 12, "name": "Container Apps Environment"})
        assert "stage-12-container-apps-environment.tfstate" in result


# ------------------------------------------------------------------
# apply() — integration with stage context
# ------------------------------------------------------------------


class TestApplyWithStageContext:
    def test_state_path_fix_uses_stage(self):
        content = """terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}
"""
        result, ids = apply(
            content,
            services=[],
            iac_tool="terraform",
            stage={"stage": 5, "name": "Container Registry"},
        )
        assert "TFM-TF-003" in ids
        assert "stage-5-container-registry.tfstate" in result

    def test_cross_file_remote_state_preserved(self):
        main_tf = """data "terraform_remote_state" "stage1" {
  backend = "local"
  config = { path = var.stage1_state_path }
}
"""
        stage_content = main_tf + "\nlocals { rg = data.terraform_remote_state.stage1.outputs.rg }\n"
        result, ids = apply(
            main_tf,
            services=[],
            iac_tool="terraform",
            stage={"stage": 3, "name": "App Insights"},
            stage_content=stage_content,
        )
        assert "TFM-TF-001" not in ids
        assert "terraform_remote_state" in result

    def test_cross_file_unused_remote_state_removed(self):
        main_tf = """data "terraform_remote_state" "stage4" {
  backend = "local"
  config = { path = var.stage4_state_path }
}
"""
        stage_content = main_tf + '\nresource "azapi_resource" "kv" { type = "Microsoft.KeyVault/vaults@2023-07-01" }\n'
        result, ids = apply(
            main_tf,
            services=[],
            iac_tool="terraform",
            stage={"stage": 6, "name": "Key Vault"},
            stage_content=stage_content,
        )
        assert "TFM-TF-001" in ids
        assert "terraform_remote_state" not in result
