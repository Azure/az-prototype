---
service_namespace: Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers
display_name: Cosmos DB SQL Container
depends_on:
  - Microsoft.DocumentDB/databaseAccounts/sqlDatabases
---

# Cosmos DB SQL Container

> A schema-free JSON container within a Cosmos DB SQL database. The fundamental unit of scalability — partition key design determines performance and cost.

## When to Use
- Every Cosmos DB application stores data in containers
- Each container has a partition key that determines data distribution
- Use separate containers for different data access patterns

## POC Defaults
- **Partition key**: Choose based on the most common query filter (e.g., `/tenantId`, `/userId`)
- **Indexing**: Default (automatic indexing of all properties)
- **TTL**: Not enabled unless data has a natural expiry

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "cosmos_container" {
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15"
  name      = var.container_name
  parent_id = azapi_resource.cosmos_database.id

  body = {
    properties = {
      resource = {
        id           = var.container_name
        partitionKey = {
          paths = [var.partition_key_path]
          kind  = "Hash"
          version = 2
        }
      }
    }
  }

  response_export_values = ["*"]
}
```

### RBAC Assignment
```hcl
# Data-plane access to containers is granted at the database account level
# via Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments.
# See the sqlRoleAssignments knowledge file.
```

## Bicep Patterns

### Basic Resource
```bicep
param containerName string
param partitionKeyPath string

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDatabase
  name: containerName
  properties: {
    resource: {
      id: containerName
      partitionKey: {
        paths: [partitionKeyPath]
        kind: 'Hash'
        version: 2
      }
    }
  }
}

output containerId string = container.id
output containerName string = container.name
```

## Application Code

### Python
```python
database = client.get_database_client(database_name)
container = database.get_container_client(container_name)

# Create item
container.create_item(body={"id": "1", "name": "example", "partitionKey": "tenant1"})

# Query items
items = container.query_items(
    query="SELECT * FROM c WHERE c.partitionKey = @pk",
    parameters=[{"name": "@pk", "value": "tenant1"}],
    partition_key="tenant1"
)
```

### C#
```csharp
var container = database.GetContainer(containerName);

// Create item
await container.CreateItemAsync(new { id = "1", name = "example", partitionKey = "tenant1" });

// Query items
var query = new QueryDefinition("SELECT * FROM c WHERE c.partitionKey = @pk")
    .WithParameter("@pk", "tenant1");
using var iterator = container.GetItemQueryIterator<dynamic>(query, requestOptions: new QueryRequestOptions
{
    PartitionKey = new PartitionKey("tenant1")
});
```

### Node.js
```typescript
const container = database.container(containerName);

// Create item
await container.items.create({ id: "1", name: "example", partitionKey: "tenant1" });

// Query items
const { resources } = await container.items
  .query({
    query: "SELECT * FROM c WHERE c.partitionKey = @pk",
    parameters: [{ name: "@pk", value: "tenant1" }],
  })
  .fetchAll();
```

## Common Pitfalls
- **Partition key is immutable**: Once set, the partition key path cannot be changed. Choose carefully based on query patterns.
- **Cross-partition queries are expensive**: Queries without the partition key filter fan out to all partitions. Always include the partition key in queries.
- **Partition key version**: Use version 2 (supports large partition keys up to 2KB). Version 1 is limited to 100 bytes.
- **Name is the resource ID**: The `resource.id` property MUST match the `name` parameter.
- **Hot partitions**: If one partition key value receives disproportionate traffic, it becomes a bottleneck. Design for even distribution.

## Production Backlog Items
- Custom indexing policy to optimize for specific query patterns and reduce RU cost
- Time-to-live (TTL) for automatic data expiration
- Unique key constraints for data integrity
- Change feed for event-driven processing
- Composite indexes for multi-field ORDER BY queries
