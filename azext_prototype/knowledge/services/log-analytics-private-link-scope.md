---
service_namespace: Microsoft.Insights/privateLinkScopes
display_name: Azure Monitor Private Link Scope
---

# Azure Monitor Private Link Scope (AMPLS)

> Network isolation boundary that groups Azure Monitor resources (Log Analytics workspaces, Application Insights) behind a single private endpoint, controlling data ingestion and query access over private network.

## When to Use
- **Private network monitoring** -- send telemetry from VNet-connected VMs to Log Analytics/App Insights over private endpoints instead of public internet
- **Compliance requirements** -- data must not traverse public networks (PCI-DSS, HIPAA)
- **Centralized private link management** -- one AMPLS with one private endpoint covers multiple monitoring resources
- Required when Log Analytics workspaces or App Insights resources have public network access disabled

An AMPLS acts as a grouping mechanism. You create one AMPLS, add scoped resources (workspaces, App Insights), then create a private endpoint to the AMPLS.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Ingestion access mode | Open | Allows mixed public/private ingestion for POC |
| Query access mode | Open | Allows mixed public/private queries for POC |
| Scoped resources | 1-2 workspaces | Add as needed |

**Important:** Using `PrivateOnly` access mode blocks ALL public access to scoped resources, including Azure portal queries. Use `Open` for POC.

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "ampls" {
  type      = "Microsoft.Insights/privateLinkScopes@2021-07-01-preview"
  name      = var.name
  location  = "global"  # AMPLS is a global resource
  parent_id = var.resource_group_id

  body = {
    properties = {
      accessModeSettings = {
        ingestionAccessMode = "Open"   # "PrivateOnly" for production
        queryAccessMode     = "Open"   # "PrivateOnly" for production
      }
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}
```

### RBAC Assignment

```hcl
# Monitoring Contributor for managing the AMPLS
resource "azapi_resource" "monitoring_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.ampls.id}-${var.principal_id}-monitoring-contributor")
  parent_id = azapi_resource.ampls.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/749f88d5-cbae-40b8-bcfc-e573ddc772fa"
      principalId      = var.principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Private Link Scope')
param name string

@description('Ingestion access mode')
@allowed(['Open', 'PrivateOnly'])
param ingestionAccessMode string = 'Open'

@description('Query access mode')
@allowed(['Open', 'PrivateOnly'])
param queryAccessMode string = 'Open'

param tags object = {}

resource ampls 'Microsoft.Insights/privateLinkScopes@2021-07-01-preview' = {
  name: name
  location: 'global'
  tags: tags
  properties: {
    accessModeSettings: {
      ingestionAccessMode: ingestionAccessMode
      queryAccessMode: queryAccessMode
    }
  }
}

output id string = ampls.id
output name string = ampls.name
```

## Application Code

### Python
Infrastructure -- transparent to application code. AMPLS controls network routing for monitoring data; applications send telemetry using the same SDKs and endpoints regardless of AMPLS configuration.

### C#
Infrastructure -- transparent to application code. AMPLS controls network routing for monitoring data; applications send telemetry using the same SDKs and endpoints regardless of AMPLS configuration.

### Node.js
Infrastructure -- transparent to application code. AMPLS controls network routing for monitoring data; applications send telemetry using the same SDKs and endpoints regardless of AMPLS configuration.

## Common Pitfalls

1. **Location must be `"global"`** -- AMPLS is a global resource. Specifying a region causes deployment failure.
2. **PrivateOnly locks out portal access** -- Setting `queryAccessMode` to `PrivateOnly` blocks Azure portal log queries unless the portal is accessed from a VNet-connected machine.
3. **One AMPLS per VNet** -- A VNet should connect to at most one AMPLS via private endpoint. Multiple AMPLS connections from the same VNet cause DNS conflicts.
4. **Scoped resource limits** -- An AMPLS supports up to 50 scoped resources. Plan capacity for large environments.
5. **DNS configuration is complex** -- AMPLS private endpoints require DNS records for multiple Azure Monitor sub-domains (`ods.opinsights.azure.com`, `oms.opinsights.azure.com`, `agentsvc.azure-automation.net`, etc.).
6. **Access mode applies to ALL scoped resources** -- Setting `PrivateOnly` affects every workspace/App Insights in the scope. You cannot mix public and private per resource within one AMPLS.
7. **Existing data collection may break** -- Switching from `Open` to `PrivateOnly` immediately blocks public ingestion. Ensure all agents are configured for private endpoints first.

## Production Backlog Items

- [ ] Switch access modes to `PrivateOnly` for both ingestion and queries
- [ ] Create private endpoint in the monitoring VNet connected to the AMPLS
- [ ] Configure DNS (private DNS zones) for all Azure Monitor sub-domains
- [ ] Add all Log Analytics workspaces and App Insights resources as scoped resources
- [ ] Verify agent connectivity over private link
- [ ] Test Azure portal query access via VPN/ExpressRoute
- [ ] Document network architecture for monitoring data flow
