---
service_namespace: Microsoft.DocumentDB/databaseAccounts/sqlDatabases
display_name: Cosmos DB SQL Database
depends_on:
  - Microsoft.DocumentDB/databaseAccounts
---

# Cosmos DB SQL Database

> A logical container within a Cosmos DB account that groups collections/containers and defines throughput sharing boundaries.

## When to Use
- Every Cosmos DB application needs at least one database
- Use multiple databases to isolate workloads with different throughput or consistency requirements
- Database-level throughput sharing reduces cost when containers have variable load

## POC Defaults
- **Throughput**: Serverless (no provisioned throughput needed for POC)
- **Name**: Application-specific (e.g., `kanflow`, `myapp`)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "cosmos_database" {
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15"
  name      = var.database_name
  parent_id = azapi_resource.cosmos_account.id

  body = {
    properties = {
      resource = {
        id = var.database_name
      }
    }
  }

  response_export_values = ["*"]
}
```

### RBAC Assignment
```hcl
# Cosmos DB data-plane access uses Cosmos-specific RBAC (sqlRoleAssignments),
# NOT Microsoft.Authorization/roleAssignments. See the sqlRoleAssignments
# knowledge file for the correct pattern.
```

## Bicep Patterns

### Basic Resource
```bicep
param databaseName string

resource cosmosDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmosAccount
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

output databaseId string = cosmosDatabase.id
output databaseName string = cosmosDatabase.name
```

## Application Code

### Python
```python
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
client = CosmosClient(url=cosmos_endpoint, credential=credential)
database = client.get_database_client(database_name)
```

### C#
```csharp
using Azure.Identity;
using Microsoft.Azure.Cosmos;

var credential = new DefaultAzureCredential();
var client = new CosmosClient(cosmosEndpoint, credential);
var database = client.GetDatabase(databaseName);
```

### Node.js
```typescript
import { CosmosClient } from "@azure/cosmos";
import { DefaultAzureCredential } from "@azure/identity";

const credential = new DefaultAzureCredential();
const client = new CosmosClient({ endpoint: cosmosEndpoint, aadCredentials: credential });
const database = client.database(databaseName);
```

## Common Pitfalls
- **Database vs container throughput**: If using provisioned throughput, choose database-level sharing carefully — one hot container can starve others.
- **Serverless requires EnableServerless capability**: The parent Cosmos account must have `capabilities = [{ name = "EnableServerless" }]` for serverless databases.
- **Name is the resource ID**: The `resource.id` property MUST match the `name` parameter.

## Production Backlog Items
- Provisioned throughput with autoscale for predictable performance
- Multiple databases for workload isolation
- Backup and restore configuration at the database level
