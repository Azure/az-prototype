# Azure Cache for Redis
> Managed in-memory data store for caching, session management, and real-time analytics powered by open-source Redis.

## When to Use

- **Session caching** -- offload session state from stateless web apps (Container Apps, App Service)
- **Data caching** -- reduce latency and database load for frequently accessed data
- **Message broker** -- lightweight pub/sub messaging between microservices
- **Rate limiting** -- distributed counters for API throttling
- **Leaderboards / sorted sets** -- real-time ranking and scoring scenarios
- **Distributed locking** -- coordination across horizontally scaled instances

Prefer Redis over Cosmos DB when data is ephemeral, latency-sensitive, and does not require durable persistence. For durable NoSQL storage, use Cosmos DB.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Basic C0 | Lowest cost; no SLA, no replication |
| SKU (with replication) | Standard C0 | 2 replicas, 99.9% SLA |
| AAD auth | Enabled | `aad_auth_enabled = true` in redis_configuration |
| Access keys | Disabled (preview) | Prefer AAD auth; set `access_key_authentication_disabled = true` |
| TLS | 1.2 minimum | `minimum_tls_version = "1.2"` |
| Public network access | Allowed (POC) | Flag private endpoint as production backlog item |

## Terraform Patterns

### Basic Resource

```hcl
resource "azurerm_redis_cache" "this" {
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  capacity                      = 0
  family                        = "C"
  sku_name                      = "Basic"
  minimum_tls_version           = "1.2"
  public_network_access_enabled = true  # Set false when using private endpoint

  # CRITICAL: Enable AAD authentication
  redis_configuration {
    active_directory_authentication_enabled = true
  }

  tags = var.tags
}
```

### RBAC Assignment

Redis uses its own data-plane RBAC roles (not standard Azure resource RBAC). Assign via `azurerm_redis_cache_access_policy_assignment`:

```hcl
# Redis Data Owner -- full read/write access
resource "azurerm_redis_cache_access_policy_assignment" "app" {
  name               = "app-identity-data-owner"
  redis_cache_id     = azurerm_redis_cache.this.id
  access_policy_name = "Data Owner"
  object_id          = var.managed_identity_principal_id
  object_id_alias    = "app-identity"
}

# Redis Data Contributor -- read/write, no admin commands
resource "azurerm_redis_cache_access_policy_assignment" "app_contributor" {
  name               = "app-identity-data-contributor"
  redis_cache_id     = azurerm_redis_cache.this.id
  access_policy_name = "Data Contributor"
  object_id          = var.managed_identity_principal_id
  object_id_alias    = "app-identity-contributor"
}
```

### Private Endpoint

```hcl
resource "azurerm_private_endpoint" "redis" {
  count = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0

  name                = "pe-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id

  private_service_connection {
    name                           = "psc-${var.name}"
    private_connection_resource_id = azurerm_redis_cache.this.id
    subresource_names              = ["redisCache"]
    is_manual_connection           = false
  }

  dynamic "private_dns_zone_group" {
    for_each = var.private_dns_zone_id != null ? [1] : []
    content {
      name                 = "dns-zone-group"
      private_dns_zone_ids = [var.private_dns_zone_id]
    }
  }

  tags = var.tags
}
```

Private DNS zone: `privatelink.redis.cache.windows.net`

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Redis cache')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Tags to apply')
param tags object = {}

resource redis 'Microsoft.Cache/redis@2024-03-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'Basic'
      family: 'C'
      capacity: 0
    }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'  // Set 'Disabled' when using private endpoint
    redisConfiguration: {
      'aad-enabled': 'true'
    }
  }
}

output id string = redis.id
output name string = redis.name
output hostName string = redis.properties.hostName
output sslPort int = redis.properties.sslPort
```

### RBAC Assignment

```bicep
@description('Principal ID of the managed identity')
param principalId string

// Redis Data Owner -- assign via access policy, not standard Azure RBAC
// Use Microsoft.Cache/redis/accessPolicyAssignments for data-plane RBAC
resource accessPolicy 'Microsoft.Cache/redis/accessPolicyAssignments@2024-03-01' = {
  parent: redis
  name: 'app-identity-data-owner'
  properties: {
    accessPolicyName: 'Data Owner'
    objectId: principalId
    objectIdAlias: 'app-identity'
  }
}
```

## Application Code

### Python

```python
import redis
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential(managed_identity_client_id="<client-id>")

def get_redis_client(host: str, port: int = 6380) -> redis.Redis:
    """Create Redis client with AAD token-based authentication."""
    token = credential.get_token("https://redis.azure.com/.default")
    return redis.Redis(
        host=host,
        port=port,
        ssl=True,
        username=credential.get_token("https://redis.azure.com/.default").token,
        password=token.token,
        decode_responses=True,
    )

# Usage
client = get_redis_client("myredis.redis.cache.windows.net")
client.set("key", "value", ex=3600)
value = client.get("key")
```

**Note:** Redis AAD tokens expire. For long-lived connections, use a token refresh callback or re-create the client periodically.

### C# / .NET

```csharp
using Azure.Identity;
using StackExchange.Redis;

var credential = new DefaultAzureCredential(new DefaultAzureCredentialOptions
{
    ManagedIdentityClientId = "<client-id>"
});

// Configure with AAD token
var configOptions = await ConfigurationOptions.Parse("myredis.redis.cache.windows.net:6380")
    .ConfigureForAzureWithTokenCredentialAsync(credential);

var connection = await ConnectionMultiplexer.ConnectAsync(configOptions);
var db = connection.GetDatabase();

// Usage
await db.StringSetAsync("key", "value", TimeSpan.FromHours(1));
var value = await db.StringGetAsync("key");
```

### Node.js

```typescript
import { DefaultAzureCredential } from "@azure/identity";
import Redis from "ioredis";

const credential = new DefaultAzureCredential({
  managedIdentityClientId: "<client-id>",
});

async function getRedisClient(host: string): Promise<Redis> {
  const token = await credential.getToken("https://redis.azure.com/.default");
  return new Redis({
    host,
    port: 6380,
    tls: { servername: host },
    username: token.token,
    password: token.token,
  });
}

// Usage
const client = await getRedisClient("myredis.redis.cache.windows.net");
await client.set("key", "value", "EX", 3600);
const value = await client.get("key");
```

## Common Pitfalls

1. **Forgetting to enable AAD auth** -- `active_directory_authentication_enabled = true` in `redis_configuration` is required for token-based authentication. Without it, only access key auth works.
2. **Using access keys instead of AAD** -- Access keys are prohibited per governance policies. Always use `DefaultAzureCredential` with the `https://redis.azure.com/.default` scope.
3. **Token expiration** -- Redis AAD tokens expire (typically 1 hour). Long-lived connections must refresh tokens. StackExchange.Redis handles this automatically with `ConfigureForAzureWithTokenCredentialAsync`; Python and Node.js require manual refresh logic.
4. **Basic tier limitations** -- Basic C0 has no SLA, no replication, and a 250 MB cache size limit. Suitable for POC only.
5. **Non-SSL port** -- Always disable the non-SSL port (`enable_non_ssl_port = false`). All connections must use TLS on port 6380.
6. **Redis data-plane RBAC vs Azure RBAC** -- Redis uses its own access policy system (Data Owner, Data Contributor, Data Reader) via `accessPolicyAssignments`, not standard `Microsoft.Authorization/roleAssignments`.
7. **Firewall rules with private endpoints** -- When using private endpoints, set `public_network_access_enabled = false` to prevent bypassing the private link.

## Production Backlog Items

- [ ] Upgrade to Standard or Premium tier (replication, SLA, persistence)
- [ ] Enable private endpoint and disable public network access
- [ ] Configure clustering for horizontal scaling (Premium tier)
- [ ] Enable data persistence (RDB or AOF) for durability (Premium tier)
- [ ] Configure geo-replication for disaster recovery (Premium tier)
- [ ] Set up monitoring alerts (cache hit ratio, memory usage, connected clients)
- [ ] Configure maxmemory eviction policy appropriate to workload
- [ ] Review and right-size cache capacity based on actual usage patterns
- [ ] Implement connection pooling in application code
