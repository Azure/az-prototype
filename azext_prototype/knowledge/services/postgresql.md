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
| Public network access | Enabled (POC) | Flag private access as production backlog item |

## Terraform Patterns

### Basic Resource

```hcl
resource "azurerm_postgresql_flexible_server" "this" {
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  version                       = "16"
  sku_name                      = "B_Standard_B1ms"  # Burstable tier
  storage_mb                    = 32768               # 32 GiB
  auto_grow_enabled             = true
  backup_retention_days         = 7
  geo_redundant_backup_enabled  = false
  public_network_access_enabled = true  # Set false when using private access

  authentication {
    active_directory_auth_enabled = true
    password_auth_enabled         = true  # Needed for initial admin; disable later
    tenant_id                     = data.azurerm_client_config.current.tenant_id
  }

  administrator_login    = var.admin_username
  administrator_password = var.admin_password  # Store in Key Vault

  tags = var.tags
}

# Required: Allow Azure services (for managed identity connections)
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure_services" {
  name      = "AllowAzureServices"
  server_id = azurerm_postgresql_flexible_server.this.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

# Create application database
resource "azurerm_postgresql_flexible_server_database" "app" {
  name      = var.database_name
  server_id = azurerm_postgresql_flexible_server.this.id
  charset   = "UTF8"
  collation = "en_US.utf8"
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
resource "azurerm_role_assignment" "pg_contributor" {
  scope                = azurerm_postgresql_flexible_server.this.id
  role_definition_name = "Contributor"
  principal_id         = var.admin_identity_principal_id
}
```

### Private Endpoint

PostgreSQL Flexible Server supports **VNet integration** (delegated subnet) as the primary private access method, not traditional private endpoints:

```hcl
# Delegated subnet for PostgreSQL
resource "azurerm_subnet" "postgres" {
  name                 = "snet-postgres"
  resource_group_name  = var.resource_group_name
  virtual_network_name = var.vnet_name
  address_prefixes     = [var.postgres_subnet_cidr]

  delegation {
    name = "postgresql"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

# Private DNS zone for VNet-integrated server
resource "azurerm_private_dns_zone" "postgres" {
  name                = "${var.name}.private.postgres.database.azure.com"
  resource_group_name = var.resource_group_name
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                  = "vnet-link"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.postgres.name
  virtual_network_id    = var.vnet_id
}

# Server with VNet integration
resource "azurerm_postgresql_flexible_server" "this" {
  # ... (same as basic, plus:)
  delegated_subnet_id = azurerm_subnet.postgres.id
  private_dns_zone_id = azurerm_private_dns_zone.postgres.id
  public_network_access_enabled = false
}
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
