# Azure Resource Groups
> Logical container for Azure resources that share a common lifecycle, enabling unified management, access control, cost tracking, and deployment scoping.

## When to Use

- Grouping resources by lifecycle (e.g., all resources for a workload, environment, or stage)
- Scoping RBAC permissions (grant access at the resource group level)
- Cost management and tagging (all resources inherit resource group tags by policy)
- Deployment target for ARM/Bicep templates and Terraform configurations
- NOT suitable for: security boundary enforcement (use subscriptions for hard isolation), cross-region resource grouping without intent (resources can be in any region regardless of RG location), or deep hierarchy (use management groups)

**Foundation resource**: Resource groups are always created first and referenced by all other resources. They are the deployment target for IaC.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Naming | `rg-<project>-<env>` | Follow naming convention (e.g., `rg-myapp-poc`) |
| Location | Project default region | Metadata location only; resources inside can be in any region |
| Tags | environment, project, owner | Minimum tagging for POC |
| Resource groups per POC | 1-3 | Single RG for simple POC; separate for networking/shared/workload if needed |
| Lock | None | Add CanNotDelete lock for production |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "resource_group" {
  type     = "Microsoft.Resources/resourceGroups@2024-03-01"
  name     = var.name
  location = var.location

  # Resource groups are top-level; parent_id is the subscription
  parent_id = "/subscriptions/${var.subscription_id}"

  body = {}

  tags = var.tags
}
```

### Multiple Resource Groups (Staged Architecture)

```hcl
# Foundation resource group (networking, shared services)
resource "azapi_resource" "rg_shared" {
  type      = "Microsoft.Resources/resourceGroups@2024-03-01"
  name      = "${var.prefix}-rg-shared"
  location  = var.location
  parent_id = "/subscriptions/${var.subscription_id}"

  body = {}

  tags = merge(var.tags, {
    purpose = "shared-services"
  })
}

# Workload resource group (application resources)
resource "azapi_resource" "rg_workload" {
  type      = "Microsoft.Resources/resourceGroups@2024-03-01"
  name      = "${var.prefix}-rg-workload"
  location  = var.location
  parent_id = "/subscriptions/${var.subscription_id}"

  body = {}

  tags = merge(var.tags, {
    purpose = "workload"
  })
}
```

### Resource Group Lock

```hcl
resource "azapi_resource" "rg_lock" {
  type      = "Microsoft.Authorization/locks@2020-05-01"
  name      = "lock-${var.name}"
  parent_id = azapi_resource.resource_group.id

  body = {
    properties = {
      level = "CanNotDelete"
      notes = "Prevent accidental deletion of resource group"
    }
  }
}
```

### RBAC Assignment

```hcl
# Grant Contributor at the resource group level
resource "azapi_resource" "rg_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.resource_group.id}${var.contributor_principal_id}contributor")
  parent_id = azapi_resource.resource_group.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/b24988ac-6180-42a0-ab88-20f7382dd24c"  # Contributor
      principalId      = var.contributor_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Grant Reader at the resource group level
resource "azapi_resource" "rg_reader" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.resource_group.id}${var.reader_principal_id}reader")
  parent_id = azapi_resource.resource_group.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/acdd72a7-3385-48ef-bd42-f606fba81ae7"  # Reader
      principalId      = var.reader_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource (Subscription-Scoped Deployment)

```bicep
targetScope = 'subscription'

param name string
param location string
param tags object = {}

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: name
  location: location
  tags: tags
}

output id string = resourceGroup.id
output name string = resourceGroup.name
```

### Multiple Resource Groups

```bicep
targetScope = 'subscription'

param prefix string
param location string
param tags object = {}

resource rgShared 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: '${prefix}-rg-shared'
  location: location
  tags: union(tags, { purpose: 'shared-services' })
}

resource rgWorkload 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: '${prefix}-rg-workload'
  location: location
  tags: union(tags, { purpose: 'workload' })
}

output sharedRgId string = rgShared.id
output workloadRgId string = rgWorkload.id
```

### Resource Group Lock

```bicep
resource rgLock 'Microsoft.Authorization/locks@2020-05-01' = {
  name: 'lock-${resourceGroupName}'
  properties: {
    level: 'CanNotDelete'
    notes: 'Prevent accidental deletion of resource group'
  }
}
```

### RBAC Assignment

```bicep
targetScope = 'resourceGroup'

param principalId string

// Contributor at resource group scope
resource contributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, principalId, 'b24988ac-6180-42a0-ab88-20f7382dd24c')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c')
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## CRITICAL: Resource Group as Deployment Target

Resource groups serve as `parent_id` for almost all other Azure resources in `azapi_resource`. The resource group must exist before any child resource is created:

```hcl
# Correct: reference the resource group ID as parent_id
resource "azapi_resource" "storage" {
  type      = "Microsoft.Storage/storageAccounts@2023-05-01"
  parent_id = azapi_resource.resource_group.id  # Reference the RG
  # ...
}
```

In Bicep, resources deployed within a resource-group-scoped template automatically target that resource group.

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Confusing RG location with resource location | RG location is metadata only; resources can be anywhere | Set RG location to your primary region; set each resource's location independently |
| Too many resource groups | Management overhead; cross-RG references add complexity | Use 1 RG for simple POCs; 2-3 for staged architecture |
| Single RG for everything in production | Blast radius too large; deleting RG destroys all resources | Separate by lifecycle (networking, shared, workload) |
| Not tagging resource groups | Cannot track costs or ownership | Apply minimum tags (environment, project, owner) to all RGs |
| Deleting RG without checking contents | All resources inside are permanently deleted | Use resource locks in production; always review before deletion |
| Subscription-scoped vs RG-scoped Bicep confusion | Bicep templates must use correct `targetScope` for RG creation | Use `targetScope = 'subscription'` when creating resource groups |
| RBAC inheritance not understood | Permissions granted at RG scope apply to all resources inside | Be intentional about RG-level RBAC; it cascades down |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Resource locks | P1 | Add CanNotDelete locks on production resource groups |
| Tag policies | P2 | Enforce required tags via Azure Policy (environment, owner, cost-center) |
| Tag inheritance | P3 | Configure Azure Policy to inherit tags from resource group to resources |
| Naming convention enforcement | P2 | Use Azure Policy to enforce naming patterns on resource groups |
| Cost management budgets | P2 | Create budgets and alerts at the resource group level |
| RBAC review | P3 | Regularly audit and minimize resource group RBAC assignments |
| Move resources | P4 | Plan resource moves between resource groups if restructuring |
| Activity log alerts | P3 | Set up alerts for resource group-level operations (delete, update) |
