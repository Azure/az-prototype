# Azure Database for PostgreSQL (Flexible Server)
> Fully managed PostgreSQL database service with built-in high availability, automated backups, and intelligent performance optimization.

## When to Use

- **Relational data with PostgreSQL preference** -- teams with PostgreSQL expertise or existing PostgreSQL applications
- **Open-source ecosystem** -- leverage PostgreSQL extensions (PostGIS, pg_trgm, pgvector, etc.)
- **Vector search with pgvector** -- lightweight RAG scenarios without a separate search service
- **Python / Node.js applications** -- PostgreSQL is the most popular relational DB in these ecosystems
- **Migration from on-premises PostgreSQL** -- near drop-in compatibility

Choose PostgreSQL Flexible Server over Azure SQL when the team prefers PostgreSQL, needs specific extensions, or has existing PostgreSQL tooling. Choose Azure SQL for .NET-heavy stacks or when SQL Server features (temporal tables, columnstore) are needed.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Burstable B1ms | 1 vCore, 2 GiB RAM; lowest cost for POC |
| Storage | 32 GiB | Minimum; auto-grow enabled |
| PostgreSQL version | 16 | Latest stable |
| High availability | Disabled | POC doesn't need zone-redundant HA |
| Backup retention | 7 days | Default; sufficient for POC |
| Authentication | Azure AD + password | AAD for app, password for admin bootstrap |
| Public network access | Disabled (unless user overrides) | Flag private access as production backlog item |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "pg_server" {
  type      = "Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard_B1ms"
      tier = "Burstable"
    }
    properties = {
      version                = "16"
      administratorLogin     = var.admin_username
      administratorLoginPassword = var.admin_password  # Store in Key Vault
      storage = {
        storageSizeGB = 32
        autoGrow      = "Enabled"
      }
      backup = {
        backupRetentionDays = 7
        geoRedundantBackup  = "Disabled"
      }
      highAvailability = {
        mode = "Disabled"
      }
      authConfig = {
        activeDirectoryAuth = "Enabled"
        passwordAuth        = "Enabled"  # Needed for initial admin; disable later
        tenantId            = data.azurerm_client_config.current.tenant_id
      }
    }
  }

  tags = var.tags

  response_export_values = ["properties.fullyQualifiedDomainName"]
}

# Required: Allow Azure services (for managed identity connections)
resource "azapi_resource" "firewall_azure_services" {
  type      = "Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview"
  name      = "AllowAzureServices"
  parent_id = azapi_resource.pg_server.id

  body = {
    properties = {
      startIpAddress = "0.0.0.0"
      endIpAddress   = "0.0.0.0"
    }
  }
}

# Create application database
resource "azapi_resource" "database" {
  type      = "Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview"
  name      = var.database_name
  parent_id = azapi_resource.pg_server.id

  body = {
    properties = {
      charset   = "UTF8"
      collation = "en_US.utf8"
    }
  }
}
```

### RBAC Assignment

PostgreSQL Flexible Server uses **Azure AD authentication** at the database level, not Azure RBAC role assignments on the ARM resource. After deployment, grant database access via SQL:

```sql
-- Run as AAD admin after server creation
-- Grant access to a managed identity
SELECT * FROM pgaad_list_principals(false);

-- Create AAD role for the managed identity
CREATE ROLE "my-app-identity" LOGIN IN ROLE azure_ad_user;

-- Grant permissions
GRANT ALL ON DATABASE appdb TO "my-app-identity";
GRANT ALL ON ALL TABLES IN SCHEMA public TO "my-app-identity";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO "my-app-identity";
```

ARM-level RBAC (for management operations):

```hcl
# Contributor role for managing the server (not data access)
resource "azapi_resource" "pg_contributor_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.pg_server.id}${var.admin_identity_principal_id}contributor")
  parent_id = azapi_resource.pg_server.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/b24988ac-6180-42a0-ab88-20f7382dd24c"  # Contributor
      principalId      = var.admin_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

PostgreSQL Flexible Server supports **VNet integration** (delegated subnet) as the primary private access method, not traditional private endpoints:

```hcl
# Delegated subnet for PostgreSQL
resource "azapi_resource" "postgres_subnet" {
  type      = "Microsoft.Network/virtualNetworks/subnets@2023-11-01"
  name      = "snet-postgres"
  parent_id = var.vnet_id

  body = {
    properties = {
      addressPrefix = var.postgres_subnet_cidr
      delegations = [
        {
          name = "postgresql"
          properties = {
            serviceName = "Microsoft.DBforPostgreSQL/flexibleServers"
          }
        }
      ]
    }
  }
}

# Private DNS zone for VNet-integrated server
resource "azapi_resource" "postgres_dns_zone" {
  type      = "Microsoft.Network/privateDnsZones@2020-06-01"
  name      = "${var.name}.private.postgres.database.azure.com"
  location  = "global"
  parent_id = var.resource_group_id

  body = {}
}

resource "azapi_resource" "postgres_dns_vnet_link" {
  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01"
  name      = "vnet-link"
  location  = "global"
  parent_id = azapi_resource.postgres_dns_zone.id

  body = {
    properties = {
      virtualNetwork = {
        id = var.vnet_id
      }
      registrationEnabled = false
    }
  }
}

# Server with VNet integration
# (same as basic resource above, with these additional properties:)
#   delegatedSubnetResourceId  = azapi_resource.postgres_subnet.id
#   privateDnsZoneArmResourceId = azapi_resource.postgres_dns_zone.id
```

Private DNS zone: `privatelink.postgres.database.azure.com` (for private endpoint) or `<servername>.private.postgres.database.azure.com` (for VNet integration)

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the PostgreSQL server')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Administrator login')
@secure()
param adminLogin string

@description('Administrator password')
@secure()
param adminPassword string

@description('Database name')
param databaseName string = 'appdb'

@description('Tags to apply')
param tags object = {}

resource pgServer 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: adminLogin
    administratorLoginPassword: adminPassword
    storage: {
      storageSizeGB: 32
      autoGrow: 'Enabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Enabled'
      tenantId: subscription().tenantId
    }
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: pgServer
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

resource firewallAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = {
  parent: pgServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

output id string = pgServer.id
output fqdn string = pgServer.properties.fullyQualifiedDomainName
output databaseName string = database.name
```

### RBAC Assignment

No ARM RBAC for data access -- use AAD database roles (see Terraform section above).

### Private Endpoint

```bicep
@description('Delegated subnet ID for PostgreSQL VNet integration')
param delegatedSubnetId string = ''

@description('Private DNS zone ID')
param privateDnsZoneId string = ''

// When using VNet integration, set these on the server properties:
// delegatedSubnetId: delegatedSubnetId
// privateDnsZoneArmResourceId: privateDnsZoneId
// Note: VNet integration must be set at creation time; cannot be changed after.
```

## Application Code

### Python — psycopg2 with Azure AD

```python
from azure.identity import DefaultAzureCredential
import psycopg2

credential = DefaultAzureCredential()
token = credential.get_token("https://ossrdbms-aad.database.windows.net/.default")

conn = psycopg2.connect(
    host="<server-name>.postgres.database.azure.com",
    database="appdb",
    user="my-app-identity",  # AAD principal name
    password=token.token,
    sslmode="require",
)

cursor = conn.cursor()
cursor.execute("SELECT * FROM items WHERE category = %s", ("electronics",))
rows = cursor.fetchall()
```

### Python — asyncpg with Azure AD

```python
from azure.identity.aio import DefaultAzureCredential
import asyncpg

credential = DefaultAzureCredential()
token = await credential.get_token("https://ossrdbms-aad.database.windows.net/.default")

conn = await asyncpg.connect(
    host="<server-name>.postgres.database.azure.com",
    database="appdb",
    user="my-app-identity",
    password=token.token,
    ssl="require",
)

rows = await conn.fetch("SELECT * FROM items WHERE category = $1", "electronics")
```

### C# — Npgsql with Azure AD

```csharp
using Azure.Identity;
using Npgsql;

var credential = new DefaultAzureCredential();
var token = await credential.GetTokenAsync(
    new Azure.Core.TokenRequestContext(new[] { "https://ossrdbms-aad.database.windows.net/.default" })
);

var connString = new NpgsqlConnectionStringBuilder
{
    Host = "<server-name>.postgres.database.azure.com",
    Database = "appdb",
    Username = "my-app-identity",
    Password = token.Token,
    SslMode = SslMode.Require,
}.ConnectionString;

await using var conn = new NpgsqlConnection(connString);
await conn.OpenAsync();

await using var cmd = new NpgsqlCommand("SELECT * FROM items WHERE category = @cat", conn);
cmd.Parameters.AddWithValue("cat", "electronics");
await using var reader = await cmd.ExecuteReaderAsync();
```

### Node.js — pg with Azure AD

```javascript
const { DefaultAzureCredential } = require("@azure/identity");
const { Client } = require("pg");

const credential = new DefaultAzureCredential();
const token = await credential.getToken("https://ossrdbms-aad.database.windows.net/.default");

const client = new Client({
  host: "<server-name>.postgres.database.azure.com",
  database: "appdb",
  user: "my-app-identity",
  password: token.token,
  ssl: { rejectUnauthorized: true },
  port: 5432,
});

await client.connect();
const res = await client.query("SELECT * FROM items WHERE category = $1", ["electronics"]);
```

## Common Pitfalls

1. **VNet integration is set at creation time** -- Cannot switch between public access and VNet integration after server creation. Decide upfront. For POC, start with public access and firewall rules.
2. **AAD role creation requires AAD admin** -- You must first set an AAD administrator on the server, then connect as that admin to create AAD database roles. This is a post-deployment step that cannot be done in Terraform/Bicep alone.
3. **Token refresh for long-running connections** -- Azure AD tokens expire after ~1 hour. Connection pools must refresh tokens. Use libraries that support token callback (Npgsql 8+ has built-in support).
4. **pgvector extension must be explicitly enabled** -- `CREATE EXTENSION vector;` is required before using vector types. Not enabled by default.
5. **Burstable tier limitations** -- B1ms has 1 vCore and limited IOPS. Fine for POC, but production workloads need General Purpose (D-series) or Memory Optimized (E-series).
6. **Storage auto-grow is one-way** -- Storage can grow automatically but cannot shrink. Start with the minimum (32 GiB) for POC.
7. **Firewall rule for Azure services** -- The `0.0.0.0` rule allows all Azure services, not just your subscription. For production, use VNet integration or private endpoint.

## Production Backlog Items

- [ ] Migrate to VNet integration (delegated subnet) and disable public access
- [ ] Enable zone-redundant high availability
- [ ] Upgrade from Burstable to General Purpose tier
- [ ] Disable password authentication (AAD-only)
- [ ] Configure connection pooling with PgBouncer (built-in)
- [ ] Set up read replicas for read-heavy workloads
- [ ] Configure diagnostic settings for query performance insights
- [ ] Implement automated maintenance window scheduling
- [ ] Add geo-redundant backup for disaster recovery
- [ ] Review and tune server parameters (work_mem, shared_buffers, etc.)
