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
resource "azapi_resource" "cosmos_account" {
  type      = "Microsoft.DocumentDB/databaseAccounts@2024-05-15"
  name      = var.cosmos_account_name
  location  = azapi_resource.resource_group.output.location
  parent_id = azapi_resource.resource_group.id

  body = {
    kind = "GlobalDocumentDB"
    properties = {
      databaseAccountOfferType = "Standard"
      disableLocalAuth         = true   # Enforce RBAC-only access
      capabilities = [
        {
          name = "EnableServerless"
        }
      ]
      consistencyPolicy = {
        defaultConsistencyLevel = "Session"
      }
      locations = [
        {
          locationName     = azapi_resource.resource_group.output.location
          failoverPriority = 0
        }
      ]
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}

resource "azapi_resource" "cosmos_sql_database" {
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
}

resource "azapi_resource" "cosmos_sql_container" {
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/sqlContainers@2024-05-15"
  name      = var.container_name
  parent_id = azapi_resource.cosmos_sql_database.id

  body = {
    properties = {
      resource = {
        id           = var.container_name
        partitionKey = {
          paths   = ["/partitionKey"]
          kind    = "Hash"
          version = 2
        }
        indexingPolicy = {
          indexingMode  = "consistent"
          includedPaths = [
            { path = "/*" }
          ]
          excludedPaths = [
            { path = "/\"_etag\"/?" }
          ]
        }
      }
    }
  }
}
```

### RBAC Assignment
```hcl
# CRITICAL: Cosmos DB uses its OWN sqlRoleAssignment resource, NOT Microsoft.Authorization/roleAssignments.
# The built-in role definition IDs are:
#   Reader:      00000000-0000-0000-0000-000000000001
#   Contributor: 00000000-0000-0000-0000-000000000002

resource "azapi_resource" "cosmos_data_contributor" {
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15"
  name      = uuidv5("sha1", "${azapi_resource.cosmos_account.id}-contributor-${azapi_resource.user_assigned_identity.output.properties.principalId}")
  parent_id = azapi_resource.cosmos_account.id

  body = {
    properties = {
      roleDefinitionId = "${azapi_resource.cosmos_account.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
      principalId      = azapi_resource.user_assigned_identity.output.properties.principalId
      scope            = azapi_resource.cosmos_account.id
    }
  }
}

resource "azapi_resource" "cosmos_data_reader" {
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15"
  name      = uuidv5("sha1", "${azapi_resource.cosmos_account.id}-reader-${azapi_resource.user_assigned_identity.output.properties.principalId}")
  parent_id = azapi_resource.cosmos_account.id

  body = {
    properties = {
      roleDefinitionId = "${azapi_resource.cosmos_account.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000001"
      principalId      = azapi_resource.user_assigned_identity.output.properties.principalId
      scope            = azapi_resource.cosmos_account.id
    }
  }
}
```

### Private Endpoint
```hcl
resource "azapi_resource" "cosmos_private_endpoint" {
  type      = "Microsoft.Network/privateEndpoints@2023-11-01"
  name      = "${var.cosmos_account_name}-pe"
  location  = azapi_resource.resource_group.output.location
  parent_id = azapi_resource.resource_group.id

  body = {
    properties = {
      subnet = {
        id = azapi_resource.private_endpoints_subnet.id
      }
      privateLinkServiceConnections = [
        {
          name = "${var.cosmos_account_name}-psc"
          properties = {
            privateLinkServiceId = azapi_resource.cosmos_account.id
            groupIds             = ["Sql"]   # Capital 'S' — this is required
          }
        }
      ]
    }
  }

  tags = var.tags
}

resource "azapi_resource" "cosmos_dns_zone" {
  type      = "Microsoft.Network/privateDnsZones@2020-06-01"
  name      = "privatelink.documents.azure.com"
  location  = "global"
  parent_id = azapi_resource.resource_group.id

  tags = var.tags
}

resource "azapi_resource" "cosmos_dns_zone_link" {
  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01"
  name      = "cosmos-dns-link"
  location  = "global"
  parent_id = azapi_resource.cosmos_dns_zone.id

  body = {
    properties = {
      virtualNetwork = {
        id = azapi_resource.virtual_network.id
      }
      registrationEnabled = false
    }
  }

  tags = var.tags
}

resource "azapi_resource" "cosmos_pe_dns_zone_group" {
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01"
  name      = "default"
  parent_id = azapi_resource.cosmos_private_endpoint.id

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "config"
          properties = {
            privateDnsZoneId = azapi_resource.cosmos_dns_zone.id
          }
        }
      ]
    }
  }
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

## CRITICAL: Serverless Configuration
- Serverless mode is enabled via `capabilities`, **NOT** a `capacityMode` property
- CORRECT: `capabilities = [{ name = "EnableServerless" }]`
- WRONG: `capacityMode = "Serverless"` (this property does **NOT** exist in the ARM schema)
- For serverless accounts, **omit** the `backupPolicy` block entirely and let Azure use the
  default. Specifying an incompatible backup type causes ARM deployment errors.
- For provisioned accounts, use `backupPolicy = { type = "Continuous", continuousModeProperties = { tier = "Continuous7Days" } }` for POC

## Common Pitfalls
- **MOST COMMON MISTAKE**: Using `Microsoft.Authorization/roleAssignments` for data-plane RBAC. Cosmos DB requires `Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments` with its own built-in role definition IDs (`00000000-0000-0000-0000-000000000001` for reader, `00000000-0000-0000-0000-000000000002` for contributor). The scope must be the Cosmos account ID, not a resource group.
- **Forgetting to disable local auth**: Set `disableLocalAuth = true` in the `body.properties` block (Terraform azapi) or `disableLocalAuth: true` (Bicep) to enforce RBAC-only. Without this, key-based access remains available.
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
