# Azure Database for PostgreSQL - Flexible Server
> Fully managed PostgreSQL database service with zone-resilient high availability, intelligent performance tuning, and fine-grained control over server configuration and maintenance windows.

## When to Use

- **Relational data with PostgreSQL** -- teams with PostgreSQL expertise or existing PostgreSQL applications
- **Open-source ecosystem** -- leverage PostgreSQL extensions (PostGIS, pg_trgm, pgvector, TimescaleDB, etc.)
- **Vector search with pgvector** -- lightweight RAG scenarios without a separate vector database
- **Zone-resilient HA** -- built-in zone-redundant or same-zone HA with automatic failover
- **Custom maintenance windows** -- control when patching happens to minimize impact
- **Cost-optimized development** -- burstable tier with stop/start capability for non-production

Flexible Server is the recommended PostgreSQL service on Azure. It replaces Single Server (deprecated). Choose Flexible Server over Azure SQL when the team prefers PostgreSQL, needs specific extensions, or has existing PostgreSQL tooling. Choose Azure SQL for .NET-heavy stacks or SQL Server feature parity (temporal tables, columnstore).

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Burstable B1ms | 1 vCore, 2 GiB RAM; lowest cost for POC |
| Storage | 32 GiB (P4) | Minimum; auto-grow enabled |
| PostgreSQL version | 16 | Latest stable |
| High availability | Disabled | Zone-redundant HA not needed for POC |
| Backup retention | 7 days | Default; sufficient for POC |
| Geo-redundant backup | Disabled | Enable for production DR |
| Authentication | Azure AD + password | AAD for applications, password for admin bootstrap |
| Public network access | Disabled (unless user overrides) | Use VNet integration or private endpoint |
| PgBouncer | Enabled | Built-in connection pooling |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "pg_flexible" {
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
        mode = "Disabled"  # "ZoneRedundant" or "SameZone" for production
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
```

### Firewall Rule (Allow Azure Services)

```hcl
resource "azapi_resource" "firewall_azure_services" {
  type      = "Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview"
  name      = "AllowAzureServices"
  parent_id = azapi_resource.pg_flexible.id

  body = {
    properties = {
      startIpAddress = "0.0.0.0"
      endIpAddress   = "0.0.0.0"
    }
  }
}
```

### Database

```hcl
resource "azapi_resource" "database" {
  type      = "Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview"
  name      = var.database_name
  parent_id = azapi_resource.pg_flexible.id

  body = {
    properties = {
      charset   = "UTF8"
      collation = "en_US.utf8"
    }
  }
}
```

### Server Configuration (PgBouncer & Extensions)

```hcl
resource "azapi_resource" "pgbouncer_enabled" {
  type      = "Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-12-01-preview"
  name      = "pgbouncer.enabled"
  parent_id = azapi_resource.pg_flexible.id

  body = {
    properties = {
      value  = "True"
      source = "user-override"
    }
  }
}

resource "azapi_resource" "extensions" {
  type      = "Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-12-01-preview"
  name      = "azure.extensions"
  parent_id = azapi_resource.pg_flexible.id

  body = {
    properties = {
      value  = "VECTOR,PG_TRGM,POSTGIS"  # Comma-separated list of allowed extensions
      source = "user-override"
    }
  }
}
```

### AAD Administrator

```hcl
resource "azapi_resource" "aad_admin" {
  type      = "Microsoft.DBforPostgreSQL/flexibleServers/administrators@2023-12-01-preview"
  name      = var.aad_admin_object_id
  parent_id = azapi_resource.pg_flexible.id

  body = {
    properties = {
      principalName = var.aad_admin_display_name
      principalType = "ServicePrincipal"  # or "User", "Group"
      tenantId      = data.azurerm_client_config.current.tenant_id
    }
  }
}
```

### RBAC Assignment

PostgreSQL Flexible Server uses **Azure AD authentication** at the database level, not Azure RBAC role assignments on the ARM resource. After deployment, grant database access via SQL:

```sql
-- Run as AAD admin after server creation
-- Create AAD role for a managed identity
CREATE ROLE "my-app-identity" LOGIN IN ROLE azure_ad_user;

-- Grant permissions on the application database
GRANT ALL ON DATABASE appdb TO "my-app-identity";
GRANT ALL ON ALL TABLES IN SCHEMA public TO "my-app-identity";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO "my-app-identity";

-- Read-only role
CREATE ROLE "my-reader-identity" LOGIN IN ROLE azure_ad_user;
GRANT CONNECT ON DATABASE appdb TO "my-reader-identity";
GRANT USAGE ON SCHEMA public TO "my-reader-identity";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO "my-reader-identity";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO "my-reader-identity";
```

ARM-level RBAC (for management operations only):

```hcl
resource "azapi_resource" "pg_contributor_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.pg_flexible.id}${var.admin_identity_principal_id}contributor")
  parent_id = azapi_resource.pg_flexible.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/b24988ac-6180-42a0-ab88-20f7382dd24c"  # Contributor
      principalId      = var.admin_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### VNet Integration (Delegated Subnet)

```hcl
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

# When using VNet integration, add these to the server properties:
#   delegatedSubnetResourceId   = azapi_resource.postgres_subnet.id
#   privateDnsZoneArmResourceId = azapi_resource.postgres_dns_zone.id
# Note: VNet integration must be set at creation time.
```

### Private Endpoint (Alternative to VNet Integration)

```hcl
resource "azapi_resource" "pg_private_endpoint" {
  count     = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints@2023-11-01"
  name      = "pe-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      subnet = {
        id = var.subnet_id
      }
      privateLinkServiceConnections = [
        {
          name = "psc-${var.name}"
          properties = {
            privateLinkServiceId = azapi_resource.pg_flexible.id
            groupIds             = ["postgresqlServer"]
          }
        }
      ]
    }
  }

  tags = var.tags
}

resource "azapi_resource" "pg_pe_dns_zone_group" {
  count     = var.enable_private_endpoint && var.subnet_id != null && var.private_dns_zone_id != null ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01"
  name      = "dns-zone-group"
  parent_id = azapi_resource.pg_private_endpoint[0].id

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "config"
          properties = {
            privateDnsZoneId = var.private_dns_zone_id
          }
        }
      ]
    }
  }
}
```

Private DNS zones:
- VNet integration: `<servername>.private.postgres.database.azure.com`
- Private endpoint: `privatelink.postgres.database.azure.com`

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the PostgreSQL Flexible Server')
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

resource pgBouncer 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-12-01-preview' = {
  parent: pgServer
  name: 'pgbouncer.enabled'
  properties: {
    value: 'True'
    source: 'user-override'
  }
}

output id string = pgServer.id
output fqdn string = pgServer.properties.fullyQualifiedDomainName
output databaseName string = database.name
```

### AAD Administrator

```bicep
@description('AAD admin object ID')
param aadAdminObjectId string

@description('AAD admin display name')
param aadAdminName string

resource aadAdmin 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2023-12-01-preview' = {
  parent: pgServer
  name: aadAdminObjectId
  properties: {
    principalName: aadAdminName
    principalType: 'ServicePrincipal'
    tenantId: subscription().tenantId
  }
}
```

### RBAC Assignment

No ARM RBAC for data access -- use AAD database roles (see Terraform section SQL commands above).

## Application Code

### Python -- psycopg with Azure AD

```python
from azure.identity import DefaultAzureCredential
import psycopg

credential = DefaultAzureCredential()
token = credential.get_token("https://ossrdbms-aad.database.windows.net/.default")

# Use PgBouncer port (6432) when PgBouncer is enabled
conn = psycopg.connect(
    host="<server-name>.postgres.database.azure.com",
    port=6432,  # PgBouncer port; use 5432 for direct connection
    dbname="appdb",
    user="my-app-identity",  # AAD principal name
    password=token.token,
    sslmode="require",
)

with conn.cursor() as cur:
    cur.execute("SELECT * FROM items WHERE category = %s", ("electronics",))
    rows = cur.fetchall()
```

### Python -- pgvector for embeddings

```python
from azure.identity import DefaultAzureCredential
import psycopg
from pgvector.psycopg import register_vector

credential = DefaultAzureCredential()
token = credential.get_token("https://ossrdbms-aad.database.windows.net/.default")

conn = psycopg.connect(
    host="<server-name>.postgres.database.azure.com",
    dbname="appdb",
    user="my-app-identity",
    password=token.token,
    sslmode="require",
)
register_vector(conn)

with conn.cursor() as cur:
    # Create vector extension and table
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id SERIAL PRIMARY KEY,
            content TEXT,
            embedding vector(1536)
        )
    """)

    # Similarity search
    embedding = [0.1] * 1536  # From Azure OpenAI
    cur.execute(
        "SELECT id, content FROM documents ORDER BY embedding <=> %s::vector LIMIT 5",
        (embedding,),
    )
    results = cur.fetchall()
```

### C# -- Npgsql with Azure AD

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
    Port = 6432,  // PgBouncer port
    Database = "appdb",
    Username = "my-app-identity",
    Password = token.Token,
    SslMode = SslMode.Require,
}.ConnectionString;

await using var conn = new NpgsqlConnection(connString);
await conn.OpenAsync();
```

### Node.js -- pg with Azure AD

```javascript
const { DefaultAzureCredential } = require("@azure/identity");
const { Client } = require("pg");

const credential = new DefaultAzureCredential();
const token = await credential.getToken("https://ossrdbms-aad.database.windows.net/.default");

const client = new Client({
  host: "<server-name>.postgres.database.azure.com",
  port: 6432,  // PgBouncer port
  database: "appdb",
  user: "my-app-identity",
  password: token.token,
  ssl: { rejectUnauthorized: true },
});

await client.connect();
const res = await client.query("SELECT * FROM items WHERE category = $1", ["electronics"]);
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| VNet integration set at creation time | Cannot switch between public access and VNet integration after creation | Decide networking model before deploying; POC can start with public access |
| AAD role creation requires admin SQL | Cannot create AAD database roles via Terraform/Bicep | Post-deployment step: connect as AAD admin and run SQL to create roles |
| Token refresh for long connections | AAD tokens expire after ~1 hour; connections fail | Implement token refresh callbacks or use short-lived connections with pooling |
| PgBouncer port vs direct port | Using wrong port causes connection failures | Use port 6432 for PgBouncer (recommended); port 5432 for direct connections |
| Extensions not allowlisted | `CREATE EXTENSION` fails even for supported extensions | Set `azure.extensions` server configuration before creating extensions |
| Burstable tier limitations | Limited IOPS; credits deplete under sustained load | Monitor CPU credits; upgrade to General Purpose for production loads |
| Storage auto-grow is one-way | Storage can grow but never shrink | Start with 32 GiB for POC to minimize committed storage |
| Firewall 0.0.0.0 rule scope | Allows ALL Azure services, not just your subscription | Use VNet integration or private endpoints for production isolation |
| Stopped server auto-start | Server auto-starts after 7 days if stopped | Schedule stops in automation; cannot stop indefinitely |
| Missing `GRANT DEFAULT PRIVILEGES` | New tables not accessible to AAD roles | Always run `ALTER DEFAULT PRIVILEGES` when granting schema access |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| VNet integration or private endpoint | P1 | Migrate to delegated subnet or private endpoint and disable public access |
| Zone-redundant HA | P1 | Enable zone-redundant high availability for automatic failover |
| Upgrade to General Purpose tier | P2 | Move from Burstable to D-series for consistent performance |
| Disable password authentication | P1 | Switch to AAD-only authentication after setup |
| Enable PgBouncer | P2 | Enable built-in PgBouncer for connection pooling (port 6432) |
| Read replicas | P3 | Configure read replicas for read-heavy workloads and reporting |
| Geo-redundant backup | P2 | Enable geo-redundant backups for cross-region disaster recovery |
| Diagnostic settings | P2 | Route PostgreSQL logs and metrics to Log Analytics |
| Maintenance window | P3 | Schedule maintenance to low-traffic periods |
| Server parameter tuning | P3 | Tune work_mem, shared_buffers, max_connections based on workload |
| Connection pooling optimization | P3 | Tune PgBouncer pool_mode and connection limits |
| pgvector index strategy | P3 | Create HNSW or IVFFlat indexes for vector similarity search performance |
