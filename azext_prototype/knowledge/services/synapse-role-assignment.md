---
service_namespace: Microsoft.Synapse/workspaces/roleAssignments
display_name: Synapse RBAC Role Assignment
depends_on:
  - Microsoft.Synapse/workspaces
---

# Synapse RBAC Role Assignment

> Workspace-level role assignment in the Synapse RBAC system (separate from Azure RBAC) that grants permissions to manage Synapse artifacts, execute Spark jobs, and access SQL pools within the workspace.

## When to Use
- **Synapse Studio access** -- grant users/groups roles to work in Synapse Studio (notebooks, pipelines, SQL scripts)
- **Spark job execution** -- assign Synapse Apache Spark Administrator for submitting Spark jobs
- **SQL administration** -- assign Synapse SQL Administrator for managing dedicated SQL pools
- **Pipeline management** -- assign roles for managing and running integration pipelines

**Important:** Synapse RBAC is a separate permission system from Azure RBAC. Azure RBAC controls resource management (ARM); Synapse RBAC controls workspace operations (Studio, data plane).

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Admin role | Synapse Administrator | Full access for POC team |
| Scope | Workspace | Apply at workspace level for simplicity |
| Principal type | User or Group | Groups preferred for team access |

## Terraform Patterns

### Basic Resource

```hcl
# Note: Synapse RBAC role assignments use the Synapse data-plane API,
# which is different from Azure RBAC (Microsoft.Authorization/roleAssignments).
# The ARM resource type is Microsoft.Synapse/workspaces/roleAssignments.

resource "azapi_resource" "synapse_admin" {
  type      = "Microsoft.Synapse/workspaces/roleAssignments@2020-12-01"
  name      = var.role_assignment_id  # GUID
  parent_id = azapi_resource.synapse_workspace.id

  body = {
    properties = {
      roleDefinitionId = "${azapi_resource.synapse_workspace.id}/roleDefinitions/6e4bf58a-b8e1-4cc3-bbf9-d73143322b78"  # Synapse Administrator
      principalId      = var.principal_id
    }
  }
}

# Synapse Contributor (limited management, no credential access)
resource "azapi_resource" "synapse_contributor" {
  type      = "Microsoft.Synapse/workspaces/roleAssignments@2020-12-01"
  name      = var.contributor_assignment_id  # GUID
  parent_id = azapi_resource.synapse_workspace.id

  body = {
    properties = {
      roleDefinitionId = "${azapi_resource.synapse_workspace.id}/roleDefinitions/7af0c69a-a548-47d6-aea3-d00e69bd83aa"  # Synapse Contributor
      principalId      = var.contributor_principal_id
    }
  }
}
```

### RBAC Assignment

```hcl
# To manage Synapse RBAC assignments, you need either:
# 1. Synapse Administrator role within the workspace, OR
# 2. Owner/Contributor Azure RBAC on the workspace resource
# Azure RBAC role for workspace resource management:
resource "azapi_resource" "azure_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.synapse_workspace.id}-${var.principal_id}-contributor")
  parent_id = azapi_resource.synapse_workspace.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/b24988ac-6180-42a0-ab88-20f7382dd24c"
      principalId      = var.principal_id
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Role assignment GUID')
param roleAssignmentId string = newGuid()

@description('Principal ID to assign the role to')
param principalId string

@description('Synapse role definition ID')
param roleDefinitionId string = '6e4bf58a-b8e1-4cc3-bbf9-d73143322b78'  // Synapse Administrator

resource roleAssignment 'Microsoft.Synapse/workspaces/roleAssignments@2020-12-01' = {
  parent: synapseWorkspace
  name: roleAssignmentId
  properties: {
    roleDefinitionId: '${synapseWorkspace.id}/roleDefinitions/${roleDefinitionId}'
    principalId: principalId
  }
}
```

## Application Code

### Python
Infrastructure -- transparent to application code. Synapse RBAC role assignments control who can manage workspace resources; applications authenticate to Synapse using Azure AD tokens and the workspace's identity controls.

### C#
Infrastructure -- transparent to application code. Synapse RBAC role assignments control who can manage workspace resources; applications authenticate to Synapse using Azure AD tokens and the workspace's identity controls.

### Node.js
Infrastructure -- transparent to application code. Synapse RBAC role assignments control who can manage workspace resources; applications authenticate to Synapse using Azure AD tokens and the workspace's identity controls.

## Common Pitfalls

1. **Synapse RBAC vs Azure RBAC** -- Synapse has its own RBAC system. Azure RBAC Contributor on the workspace does NOT grant Synapse Studio access. Both are needed for full management.
2. **Role assignment name must be a GUID** -- Like Azure RBAC, the `name` must be a valid GUID. Human-readable names are not supported.
3. **Role definition IDs are workspace-scoped** -- The `roleDefinitionId` must include the full workspace path prefix, not just the GUID.
4. **Propagation delay** -- Synapse RBAC changes can take up to 10 minutes to propagate. Users may get "Access Denied" errors temporarily after assignment.
5. **Scope levels** -- Roles can be scoped to workspace, Spark pool, or integration runtime. Workspace-scoped roles grant broader access. Use more specific scopes for least privilege.
6. **No deny assignments** -- Like Azure RBAC, Synapse RBAC only supports allow assignments. Use the principle of least privilege by assigning the narrowest role.
7. **API version confusion** -- The `2020-12-01` API version is for Synapse RBAC role assignments. Using a workspace management API version may not expose this resource type.

Synapse built-in role GUIDs:
- Synapse Administrator: `6e4bf58a-b8e1-4cc3-bbf9-d73143322b78`
- Synapse Contributor: `7af0c69a-a548-47d6-aea3-d00e69bd83aa`
- Synapse Artifact Publisher: `05930394-6e46-4869-a946-0d76e36ec53c`
- Synapse Compute Operator: `e4470ace-7e6c-4654-be0a-35df6e0e4d10`
- Synapse SQL Administrator: `7af0c69a-a548-47d6-aea3-d00e69bd83aa`
- Synapse Apache Spark Administrator: `c3a6d2f1-a26f-4810-9b0f-591308d5cbf1`

## Production Backlog Items

- [ ] Replace Synapse Administrator with more specific roles (Contributor, SQL Admin, Spark Admin)
- [ ] Scope roles to specific pools and integration runtimes instead of workspace-wide
- [ ] Assign roles to Azure AD groups instead of individual users
- [ ] Implement regular access reviews for Synapse RBAC assignments
- [ ] Document the mapping between Azure RBAC and Synapse RBAC for the team
- [ ] Configure Just-In-Time access via PIM for Synapse Administrator role
