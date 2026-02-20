# Azure Cosmos DB (NoSQL API)

> Globally distributed, multi-model database with single-digit millisecond latency and automatic scaling.

## When to Use
- Applications needing low-latency reads/writes with global distribution
- Document-oriented or key-value data models with flexible schemas
- Event-driven architectures requiring change feed for real-time processing

## POC Defaults
- **Capacity mode**: Serverless (no provisioned throughput to manage, pay-per-request)
- **Consistency level**: Session (best balance of consistency and performance for POCs)
- **API**: NoSQL (SQL-like query syntax, broadest SDK support)
- **Backup**: Continuous (default for serverless)

## Terraform Patterns

### Basic Resource
```hcl
resource "azurerm_cosmosdb_account" "this" {
  name                          = var.cosmos_account_name
  location                      = azurerm_resource_group.this.location
  resource_group_name           = azurerm_resource_group.this.name
  offer_type                    = "Standard"
  kind                          = "GlobalDocumentDB"
  local_authentication_disabled = true   # Enforce RBAC-only access

  capabilities {
    name = "EnableServerless"
  }

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.this.location
    failover_priority = 0
  }

  tags = var.tags
}

resource "azurerm_cosmosdb_sql_database" "this" {
  name                = var.database_name
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
}

resource "azurerm_cosmosdb_sql_container" "this" {
  name                = var.container_name
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
  database_name       = azurerm_cosmosdb_sql_database.this.name
  partition_key_paths = ["/partitionKey"]

  indexing_policy {
    indexing_mode = "consistent"

    included_path {
      path = "/*"
    }

    excluded_path {
      path = "/\"_etag\"/?"
    }
  }
}
```

### RBAC Assignment
```hcl
# CRITICAL: Cosmos DB uses its OWN role assignment resource, NOT azurerm_role_assignment.
# The built-in role definition IDs are:
#   Reader:      00000000-0000-0000-0000-000000000001
#   Contributor: 00000000-0000-0000-0000-000000000002

resource "azurerm_cosmosdb_sql_role_assignment" "data_contributor" {
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
  role_definition_id  = "${azurerm_cosmosdb_account.this.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = azurerm_user_assigned_identity.this.principal_id
  scope               = azurerm_cosmosdb_account.this.id
}

resource "azurerm_cosmosdb_sql_role_assignment" "data_reader" {
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
  role_definition_id  = "${azurerm_cosmosdb_account.this.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000001"
  principal_id        = azurerm_user_assigned_identity.this.principal_id
  scope               = azurerm_cosmosdb_account.this.id
}
```

### Private Endpoint
```hcl
resource "azurerm_private_endpoint" "cosmos" {
  name                = "${var.cosmos_account_name}-pe"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  subnet_id           = azurerm_subnet.private_endpoints.id

  private_service_connection {
    name                           = "${var.cosmos_account_name}-psc"
    private_connection_resource_id = azurerm_cosmosdb_account.this.id
    is_manual_connection           = false
    subresource_names              = ["Sql"]   # Capital 'S' — this is required
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [azurerm_private_dns_zone.cosmos.id]
  }
}

resource "azurerm_private_dns_zone" "cosmos" {
  name                = "privatelink.documents.azure.com"
  resource_group_name = azurerm_resource_group.this.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "cosmos" {
  name                  = "cosmos-dns-link"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.cosmos.name
  virtual_network_id    = azurerm_virtual_network.this.id
}
```

## Bicep Patterns

### Basic Resource
```bicep
param cosmosAccountName string
param location string = resourceGroup().location
param databaseName string
param containerName string
param tags object = {}

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: cosmosAccountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    disableLocalAuth: true   // Enforce RBAC-only access
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
      }
    ]
  }
  tags: tags
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmosAccount
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/sqlContainers@2024-05-15' = {
  parent: database
  name: containerName
  properties: {
    resource: {
      id: containerName
      partitionKey: {
        paths: ['/partitionKey']
        kind: 'Hash'
        version: 2
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [
          { path: '/*' }
        ]
        excludedPaths: [
          { path: '/"_etag"/?' }
        ]
      }
    }
  }
}
```

### RBAC Assignment
```bicep
// CRITICAL: Cosmos DB uses its own sqlRoleAssignment, NOT Microsoft.Authorization/roleAssignments.
// Built-in role definition IDs:
//   Reader:      00000000-0000-0000-0000-000000000001
//   Contributor: 00000000-0000-0000-0000-000000000002

param principalId string

var dataContributorRoleId = '00000000-0000-0000-0000-000000000002'

resource roleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
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
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
client = CosmosClient(
    url="https://<account-name>.documents.azure.com:443/",
    credential=credential
)

database = client.get_database_client("<database-name>")
container = database.get_container_client("<container-name>")

# Create item
container.create_item(body={"id": "1", "partitionKey": "pk1", "name": "example"})

# Read item
item = container.read_item(item="1", partition_key="pk1")

# Query items
items = list(container.query_items(
    query="SELECT * FROM c WHERE c.partitionKey = @pk",
    parameters=[{"name": "@pk", "value": "pk1"}],
    enable_cross_partition_query=False
))
```

### C#
```csharp
using Azure.Identity;
using Microsoft.Azure.Cosmos;

var credential = new DefaultAzureCredential();
var client = new CosmosClient(
    accountEndpoint: "https://<account-name>.documents.azure.com:443/",
    tokenCredential: credential
);

var database = client.GetDatabase("<database-name>");
var container = database.GetContainer("<container-name>");

// Create item
var item = new { id = "1", partitionKey = "pk1", name = "example" };
await container.CreateItemAsync(item, new PartitionKey("pk1"));

// Read item
var response = await container.ReadItemAsync<dynamic>("1", new PartitionKey("pk1"));

// Query items
var query = new QueryDefinition("SELECT * FROM c WHERE c.partitionKey = @pk")
    .WithParameter("@pk", "pk1");

using var iterator = container.GetItemQueryIterator<dynamic>(query);
while (iterator.HasMoreResults)
{
    var results = await iterator.ReadNextAsync();
    foreach (var result in results)
    {
        Console.WriteLine(result);
    }
}
```

### Node.js
```typescript
import { CosmosClient } from "@azure/cosmos";
import { DefaultAzureCredential } from "@azure/identity";

const credential = new DefaultAzureCredential();
const client = new CosmosClient({
  endpoint: "https://<account-name>.documents.azure.com:443/",
  aadCredentials: credential,
});

const database = client.database("<database-name>");
const container = database.container("<container-name>");

// Create item
await container.items.create({ id: "1", partitionKey: "pk1", name: "example" });

// Read item
const { resource } = await container.item("1", "pk1").read();

// Query items
const { resources } = await container.items
  .query({
    query: "SELECT * FROM c WHERE c.partitionKey = @pk",
    parameters: [{ name: "@pk", value: "pk1" }],
  })
  .fetchAll();
```

## Common Pitfalls
- **MOST COMMON MISTAKE**: Using `azurerm_role_assignment` for data-plane RBAC. Cosmos DB requires `azurerm_cosmosdb_sql_role_assignment` with its own built-in role definition IDs (`00000000-0000-0000-0000-000000000001` for reader, `00000000-0000-0000-0000-000000000002` for contributor). The scope must be the Cosmos account ID, not a resource group.
- **Forgetting to disable local auth**: Set `local_authentication_disabled = true` (Terraform) or `disableLocalAuth: true` (Bicep) to enforce RBAC-only. Without this, key-based access remains available.
- **Private endpoint subresource**: The group ID is `Sql` with a capital `S`, not `sql` or `SQL`.
- **Partition key immutability**: Once a container is created with a partition key, it cannot be changed. Choose carefully before creating containers.
- **Serverless limitations**: Serverless accounts are single-region only and have a 1 MB max document size. Cannot convert between serverless and provisioned after creation.
- **Consistency level confusion**: Account-level consistency is the default; clients can relax (weaken) but not strengthen it per request.

## Production Backlog Items
- Geo-replication with multi-region writes for high availability
- Autoscale throughput (switch from serverless to provisioned autoscale for predictable workloads)
- Custom backup policy with point-in-time restore configuration
- Partition key optimization based on actual query patterns
- Analytical store (HTAP) for large-scale analytics without impacting transactional workload
- Diagnostic settings for monitoring RU consumption and throttling
- Network restrictions (IP firewall rules, VNet service endpoints)
