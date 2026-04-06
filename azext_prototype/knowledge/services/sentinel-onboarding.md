---
service_namespace: Microsoft.SecurityInsights/onboardingStates
display_name: Sentinel Onboarding State
depends_on:
  - Microsoft.OperationalInsights/workspaces
---

# Sentinel Onboarding State

> Activation resource that finalizes Microsoft Sentinel on a Log Analytics workspace, enabling the full Sentinel experience including data connectors, analytics rules, and incident management.

## When to Use
- **Always required after enabling Sentinel** -- deploying the `SecurityInsights` solution alone is insufficient; the onboarding state completes the activation
- Enables Sentinel-specific features: incidents, entity pages, hunting, notebooks, UEBA
- Must be deployed once per workspace where Sentinel is enabled
- Without this resource, data connectors and analytics rules may fail silently

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Name | `default` | Must always be `default` |
| Customer managed key | false | Use platform keys for POC |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "sentinel_onboarding" {
  type      = "Microsoft.SecurityInsights/onboardingStates@2024-03-01"
  name      = "default"
  parent_id = var.workspace_id  # Log Analytics workspace resource ID

  body = {
    properties = {
      customerManagedKey = false
    }
  }

  depends_on = [azapi_resource.sentinel_solution]
}
```

### RBAC Assignment

```hcl
# Microsoft Sentinel Contributor -- required to manage onboarding
resource "azapi_resource" "sentinel_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.workspace_id}-${var.principal_id}-sentinel-contributor")
  parent_id = var.workspace_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/ab8e14d6-4a74-4a29-9ba8-549422addade"
      principalId      = var.principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Log Analytics workspace resource ID')
param workspaceId string

@description('Log Analytics workspace name')
param workspaceName string

// Sentinel solution must be deployed first
resource sentinelSolution 'Microsoft.OperationsManagement/solutions@2015-11-01-preview' = {
  name: 'SecurityInsights(${workspaceName})'
  location: resourceGroup().location
  properties: {
    workspaceResourceId: workspaceId
  }
  plan: {
    name: 'SecurityInsights(${workspaceName})'
    publisher: 'Microsoft'
    product: 'OMSGallery/SecurityInsights'
  }
}

resource onboarding 'Microsoft.SecurityInsights/onboardingStates@2024-03-01' = {
  name: 'default'
  scope: workspace
  properties: {
    customerManagedKey: false
  }
  dependsOn: [sentinelSolution]
}
```

## Application Code

### Python
Infrastructure -- transparent to application code. Sentinel onboarding is a one-time activation step; applications interact with Sentinel via the Security Insights REST API or Azure SDK if needed.

### C#
Infrastructure -- transparent to application code. Sentinel onboarding is a one-time activation step; applications interact with Sentinel via the Security Insights REST API or Azure SDK if needed.

### Node.js
Infrastructure -- transparent to application code. Sentinel onboarding is a one-time activation step; applications interact with Sentinel via the Security Insights REST API or Azure SDK if needed.

## Common Pitfalls

1. **Name must be `"default"`** -- The onboarding state resource name must always be `default`. Any other name causes a deployment error.
2. **Deploy after the solution** -- The `Microsoft.OperationsManagement/solutions` resource (SecurityInsights) must be fully deployed before creating the onboarding state. Missing `depends_on` causes failures.
3. **Parent is the workspace, not the resource group** -- The `parent_id` must be the Log Analytics workspace resource ID, not the resource group.
4. **Idempotent but not deletable** -- Creating the onboarding state is idempotent (safe to re-run), but deleting it effectively disables Sentinel on the workspace.
5. **Features blocked without onboarding** -- Without this resource, Sentinel appears enabled (solution deployed) but data connectors, analytics rules, and incidents may not function correctly.
6. **Customer managed key requires Key Vault** -- Setting `customerManagedKey: true` requires a pre-configured Key Vault with the appropriate key. Don't enable for POC.

## Production Backlog Items

- [ ] Evaluate customer-managed key encryption for compliance requirements
- [ ] Verify onboarding state after initial deployment via the Sentinel portal
- [ ] Configure workspace retention settings appropriate for security data (90-730 days)
- [ ] Enable UEBA and entity behavior analytics after onboarding
- [ ] Document the workspace-to-Sentinel relationship for operations team
