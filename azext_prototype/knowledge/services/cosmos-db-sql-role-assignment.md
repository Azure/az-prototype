---
service_namespace: Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments
display_name: Cosmos DB SQL Role Assignment
depends_on:
  - Microsoft.DocumentDB/databaseAccounts
  - Microsoft.ManagedIdentity/userAssignedIdentities
---

# Cosmos DB SQL Role Assignment

> Grants data-plane access (read/write) to a Cosmos DB account using Cosmos-specific RBAC — NOT standard Azure RBAC.

## When to Use
- Every application identity that reads or writes Cosmos DB data needs a sqlRoleAssignment
- This is the ONLY way to grant data-plane access when local auth is disabled
- Standard `Microsoft.Authorization/roleAssignments` do NOT work for Cosmos DB data access

## POC Defaults
- **Role**: Data Contributor (`00000000-0000-0000-0000-000000000002`) for read/write
- **Scope**: Account level (covers all databases and containers)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "cosmos_role_assignment" {
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15"
  name      = uuidv5("sha1", "${azapi_resource.cosmos_account.id}-${var.principal_id}-data-contributor")
  parent_id = azapi_resource.cosmos_account.id

  body = {
    properties = {
      roleDefinitionId = "${azapi_resource.cosmos_account.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
      principalId      = var.principal_id
      scope            = azapi_resource.cosmos_account.id
    }
  }
}
```

### Built-in Role Definition IDs
```hcl
# Data Reader:      00000000-0000-0000-0000-000000000001
# Data Contributor:  00000000-0000-0000-0000-000000000002
```

### RBAC Assignment
```hcl
# This IS the RBAC assignment. Cosmos DB uses its own role system,
# not Microsoft.Authorization/roleAssignments.
```

## Bicep Patterns

### Basic Resource
```bicep
param principalId string

var dataContributorRoleId = '00000000-0000-0000-0000-000000000002'

resource cosmosRoleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, principalId, dataContributorRoleId)
  properties: {
    roleDefinitionId: '${cosmosAccount.id}/sqlRoleDefinitions/${dataContributorRoleId}'
    principalId: principalId
    scope: cosmosAccount.id
  }
}
```

## Application Code

### Python
```python
# No application code needed — the role assignment is an infrastructure concern.
# Once assigned, DefaultAzureCredential automatically authenticates:
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
client = CosmosClient(url=endpoint, credential=credential)
# Data access works automatically via the sqlRoleAssignment
```

### C#
```csharp
// No application code needed — once the role is assigned,
// DefaultAzureCredential handles authentication automatically:
var credential = new DefaultAzureCredential();
var client = new CosmosClient(endpoint, credential);
// Data access works automatically via the sqlRoleAssignment
```

### Node.js
```typescript
// No application code needed — once the role is assigned,
// DefaultAzureCredential handles authentication automatically:
const credential = new DefaultAzureCredential();
const client = new CosmosClient({ endpoint, aadCredentials: credential });
```

## Common Pitfalls
- **Using ARM roleAssignments for data access**: `Microsoft.Authorization/roleAssignments` with "Cosmos DB Account Contributor" is a CONTROL PLANE role. It does NOT grant data read/write. You MUST use `sqlRoleAssignments`.
- **roleDefinitionId format**: Must be the full path including the account ID: `{accountId}/sqlRoleDefinitions/{roleId}`.
- **Scope must be account-level or lower**: The scope cannot be a subscription or resource group — it must be the Cosmos account ID or a database/container within it.
- **Duplicate assignments**: Re-applying the same role assignment with a different name creates a duplicate (no upsert). Use deterministic names via `uuidv5`.
- **Propagation delay**: Role assignments can take up to 10 minutes to propagate. Applications may get 403 errors during this window.

## Production Backlog Items
- Scope role assignments to specific databases or containers instead of account level
- Separate reader and contributor roles for different application components
- Custom role definitions for fine-grained access control
