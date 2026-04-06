---
service_namespace: Microsoft.Insights/privateLinkScopes/scopedResources
display_name: AMPLS Scoped Resource
depends_on:
  - Microsoft.Insights/privateLinkScopes
---

# AMPLS Scoped Resource

> Association between an Azure Monitor Private Link Scope (AMPLS) and a specific monitoring resource (Log Analytics workspace or Application Insights), enabling that resource to be accessed through the AMPLS private endpoint.

## When to Use
- **Add workspace to private link** -- include a Log Analytics workspace in the AMPLS for private ingestion and query
- **Add App Insights to private link** -- include an Application Insights resource for private telemetry collection
- Every monitoring resource that should be accessible over the private endpoint must be added as a scoped resource

Each scoped resource creates a link between the AMPLS and the target monitoring resource. Without this link, the monitoring resource is not reachable via the AMPLS private endpoint.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Linked resource | Log Analytics workspace ID | Or Application Insights resource ID |
| Name | Descriptive (e.g., `workspace-link`) | Must be unique within the AMPLS |

## Terraform Patterns

### Basic Resource

```hcl
# Link a Log Analytics workspace to the AMPLS
resource "azapi_resource" "scoped_workspace" {
  type      = "Microsoft.Insights/privateLinkScopes/scopedResources@2021-07-01-preview"
  name      = "workspace-${var.workspace_name}"
  parent_id = azapi_resource.ampls.id

  body = {
    properties = {
      linkedResourceId = var.workspace_id
    }
  }
}

# Link an Application Insights resource to the AMPLS
resource "azapi_resource" "scoped_appinsights" {
  type      = "Microsoft.Insights/privateLinkScopes/scopedResources@2021-07-01-preview"
  name      = "appinsights-${var.appinsights_name}"
  parent_id = azapi_resource.ampls.id

  body = {
    properties = {
      linkedResourceId = var.appinsights_id
    }
  }
}
```

### RBAC Assignment

```hcl
# Scoped resource management inherits from the parent AMPLS RBAC.
# Monitoring Contributor (749f88d5-cbae-40b8-bcfc-e573ddc772fa) on the AMPLS is sufficient.
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name for the scoped resource link')
param scopedResourceName string

@description('Resource ID of the Log Analytics workspace or App Insights')
param linkedResourceId string

resource scopedResource 'Microsoft.Insights/privateLinkScopes/scopedResources@2021-07-01-preview' = {
  parent: ampls
  name: scopedResourceName
  properties: {
    linkedResourceId: linkedResourceId
  }
}

output id string = scopedResource.id
output provisioningState string = scopedResource.properties.provisioningState
```

## Application Code

### Python
Infrastructure -- transparent to application code. Scoped resources define which monitoring resources are accessible over private link; applications are unaware of this configuration.

### C#
Infrastructure -- transparent to application code. Scoped resources define which monitoring resources are accessible over private link; applications are unaware of this configuration.

### Node.js
Infrastructure -- transparent to application code. Scoped resources define which monitoring resources are accessible over private link; applications are unaware of this configuration.

## Common Pitfalls

1. **50 resource limit per AMPLS** -- Each AMPLS supports a maximum of 50 scoped resources. Plan for this limit in large environments.
2. **Resource can be in only 5 AMPLS** -- A single workspace or App Insights resource can be linked to at most 5 AMPLS. Exceeding this causes deployment failure.
3. **Linked resource must exist** -- The `linkedResourceId` must point to an existing Log Analytics workspace or Application Insights resource. Deploying with a non-existent ID fails.
4. **Removing breaks private access** -- Deleting a scoped resource immediately removes private endpoint access to that monitoring resource. If `PrivateOnly` mode is active, all data ingestion and queries stop.
5. **Name must be unique within AMPLS** -- Two scoped resources in the same AMPLS cannot share a name. Use a naming convention that includes the target resource name.
6. **Cross-subscription links** -- Scoped resources can link to monitoring resources in different subscriptions, but the deploying identity needs Reader access on the target resource.

## Production Backlog Items

- [ ] Inventory all Log Analytics workspaces and App Insights resources that need private access
- [ ] Add all required monitoring resources as scoped resources
- [ ] Verify private endpoint DNS resolution for each scoped resource
- [ ] Monitor provisioning state for successful linkage
- [ ] Plan for the 50-resource limit if the environment is large
- [ ] Document which resources are scoped per AMPLS for the networking team
