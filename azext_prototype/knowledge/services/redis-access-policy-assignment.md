---
service_namespace: Microsoft.Cache/redis/accessPolicyAssignments
display_name: Redis Access Policy Assignment
depends_on:
  - Microsoft.Cache/redis
  - Microsoft.ManagedIdentity/userAssignedIdentities
---

# Redis Access Policy Assignment

> Grants data-plane access to Azure Cache for Redis using Microsoft Entra RBAC. Replaces shared access keys for authentication.

## When to Use
- Every application identity that accesses Redis needs an access policy assignment
- Required when `aad-enabled = true` in Redis configuration
- Replaces connection string / access key authentication

## POC Defaults
- **Access policy**: Data Owner (full read/write access)
- **Principal type**: ServicePrincipal (for managed identities)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "redis_access_policy" {
  type      = "Microsoft.Cache/redis/accessPolicyAssignments@2024-03-01"
  name      = "worker-data-access"
  parent_id = azapi_resource.redis_cache.id

  body = {
    properties = {
      accessPolicyName = "Data Owner"
      objectId         = var.managed_identity_principal_id
      objectIdAlias    = var.managed_identity_name
    }
  }
}
```

### RBAC Assignment
```hcl
# This IS the data-plane RBAC assignment for Redis.
# Redis uses its own access policy system, similar to Cosmos DB.
# Standard Microsoft.Authorization/roleAssignments are for control-plane only.
```

## Bicep Patterns

### Basic Resource
```bicep
param principalId string
param principalName string

resource accessPolicy 'Microsoft.Cache/redis/accessPolicyAssignments@2024-03-01' = {
  parent: redisCache
  name: 'worker-data-access'
  properties: {
    accessPolicyName: 'Data Owner'
    objectId: principalId
    objectIdAlias: principalName
  }
}
```

## Application Code

### Python
```python
import redis
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
token = credential.get_token("https://redis.azure.com/.default")

r = redis.Redis(
    host="<cache>.redis.cache.windows.net",
    port=6380,
    ssl=True,
    username=principal_id,  # Object ID of the managed identity
    password=token.token,
)
r.set("key", "value")
print(r.get("key"))
```

### C#
```csharp
using Azure.Identity;
using StackExchange.Redis;

var credential = new DefaultAzureCredential();
var token = await credential.GetTokenAsync(
    new Azure.Core.TokenRequestContext(new[] { "https://redis.azure.com/.default" }));

var connection = ConnectionMultiplexer.Connect(new ConfigurationOptions
{
    EndPoints = { "<cache>.redis.cache.windows.net:6380" },
    Ssl = true,
    User = principalId,
    Password = token.Token,
});

var db = connection.GetDatabase();
db.StringSet("key", "value");
Console.WriteLine(db.StringGet("key"));
```

### Node.js
```typescript
import { DefaultAzureCredential } from "@azure/identity";
import { createClient } from "redis";

const credential = new DefaultAzureCredential();
const token = await credential.getToken("https://redis.azure.com/.default");

const client = createClient({
  url: "rediss://<cache>.redis.cache.windows.net:6380",
  username: principalId,
  password: token.token,
});
await client.connect();
await client.set("key", "value");
console.log(await client.get("key"));
```

## Common Pitfalls
- **Not standard RBAC**: Redis uses its own access policy system (`Microsoft.Cache/redis/accessPolicyAssignments`), NOT `Microsoft.Authorization/roleAssignments` for data access.
- **AAD must be enabled**: The Redis cache must have `aad-enabled = true` in `redisConfiguration` for access policies to work.
- **Built-in policies**: Use "Data Owner", "Data Contributor", or "Data Reader" — these are built-in and cannot be created manually.
- **Token scope**: The OAuth token scope for Redis is `https://redis.azure.com/.default`, not the cache endpoint.
- **Premium tier required for AAD**: Entra ID authentication requires Premium tier or higher.

## Production Backlog Items
- Separate Data Reader and Data Contributor policies for least-privilege
- Token caching and refresh logic for long-running connections
- Connection pooling with token rotation
