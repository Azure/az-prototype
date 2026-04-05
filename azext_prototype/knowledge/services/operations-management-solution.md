---
service_namespace: Microsoft.OperationsManagement/solutions
display_name: Operations Management Solution
---

# Operations Management Solution

> Gallery solution deployed on a Log Analytics workspace that adds specialized monitoring capabilities such as Microsoft Sentinel, Change Tracking, Update Management, or Container Insights.

## When to Use
- **Enable Microsoft Sentinel** -- deploy `SecurityInsights` solution to activate SIEM/SOAR on a workspace
- **Container Insights** -- deploy `ContainerInsights` for AKS monitoring dashboards and log collection
- **Change Tracking** -- deploy `ChangeTracking` for tracking configuration changes on VMs
- **Update Management** -- deploy `Updates` for OS patch compliance tracking
- **Service Map** -- deploy `ServiceMap` for application dependency mapping
- **VM Insights** -- deploy `VMInsights` for VM performance monitoring

Solutions extend Log Analytics with pre-built views, saved queries, and dashboards for specific scenarios.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Publisher | Microsoft | All first-party solutions |
| Plan product | OMSGallery/<solution> | Naming convention for gallery solutions |
| Name format | `<Solution>(<WorkspaceName>)` | Must follow this exact format |

## Terraform Patterns

### Basic Resource

```hcl
# Microsoft Sentinel solution
resource "azapi_resource" "sentinel_solution" {
  type      = "Microsoft.OperationsManagement/solutions@2015-11-01-preview"
  name      = "SecurityInsights(${var.workspace_name})"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      workspaceResourceId = var.workspace_id
    }
    plan = {
      name      = "SecurityInsights(${var.workspace_name})"
      publisher = "Microsoft"
      product   = "OMSGallery/SecurityInsights"
    }
  }

  tags = var.tags
}

# Container Insights solution
resource "azapi_resource" "container_insights" {
  type      = "Microsoft.OperationsManagement/solutions@2015-11-01-preview"
  name      = "ContainerInsights(${var.workspace_name})"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      workspaceResourceId = var.workspace_id
    }
    plan = {
      name      = "ContainerInsights(${var.workspace_name})"
      publisher = "Microsoft"
      product   = "OMSGallery/ContainerInsights"
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# Log Analytics Contributor on the workspace for solution management
resource "azapi_resource" "la_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.workspace_id}-${var.principal_id}-la-contributor")
  parent_id = var.workspace_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/92aaf0da-9dab-42b6-94a3-d43ce8d16293"
      principalId      = var.principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Log Analytics workspace name')
param workspaceName string

@description('Log Analytics workspace resource ID')
param workspaceId string

@description('Azure region')
param location string = resourceGroup().location

param tags object = {}

resource sentinelSolution 'Microsoft.OperationsManagement/solutions@2015-11-01-preview' = {
  name: 'SecurityInsights(${workspaceName})'
  location: location
  tags: tags
  properties: {
    workspaceResourceId: workspaceId
  }
  plan: {
    name: 'SecurityInsights(${workspaceName})'
    publisher: 'Microsoft'
    product: 'OMSGallery/SecurityInsights'
  }
}

output id string = sentinelSolution.id
```

## Application Code

### Python
Infrastructure -- transparent to application code. Solutions add monitoring capabilities to the workspace; applications send telemetry through standard diagnostic settings and SDKs.

### C#
Infrastructure -- transparent to application code. Solutions add monitoring capabilities to the workspace; applications send telemetry through standard diagnostic settings and SDKs.

### Node.js
Infrastructure -- transparent to application code. Solutions add monitoring capabilities to the workspace; applications send telemetry through standard diagnostic settings and SDKs.

## Common Pitfalls

1. **Name format must be exact** -- The name must follow `<SolutionName>(<WorkspaceName>)` exactly. Deviations cause deployment failures or orphaned resources.
2. **Plan name must match resource name** -- The `plan.name` and resource `name` must be identical. Mismatches cause cryptic ARM errors.
3. **API version is old but stable** -- The `2015-11-01-preview` API is the only available version. Despite being a preview API from 2015, it is the production API for solutions.
4. **Location must match workspace** -- The solution location must match the Log Analytics workspace location. Cross-region deployment fails.
5. **Duplicate solutions** -- Deploying the same solution type twice on a workspace creates conflicts. Check for existing solutions before deploying.
6. **Deletion removes data views, not data** -- Removing a solution removes its dashboards and saved queries but does not delete the underlying log data in the workspace.
7. **Some solutions are deprecated** -- Microsoft is migrating from OMS solutions to newer patterns (DCR-based monitoring, Sentinel content hub). Check whether a newer alternative exists.

## Production Backlog Items

- [ ] Audit deployed solutions and remove unused ones to reduce complexity
- [ ] Migrate from legacy solutions to DCR-based monitoring where available
- [ ] Implement Sentinel content hub solutions instead of manual OMS solution deployment
- [ ] Configure solution-specific settings (e.g., Container Insights data collection rules)
- [ ] Set up alerts for solution health and data freshness
- [ ] Document which solutions are deployed per workspace for operations team
