# Azure SQL Database

> Fully managed relational database engine with built-in intelligence, high availability, and serverless compute for cost-effective POCs.

## When to Use
- Applications requiring relational data with ACID transactions
- Workloads with complex queries, joins, stored procedures, or reporting needs
- Migration of existing SQL Server applications to the cloud

## POC Defaults
- **Compute tier**: Serverless (General Purpose) -- auto-pauses after 60 minutes of inactivity
- **Max vCores**: 2 (sufficient for POC workloads)
- **Min vCores**: 0.5 (enables aggressive auto-pause savings)
- **Max storage**: 32 GB
- **Authentication**: Azure AD-only (no SQL authentication)

## Terraform Patterns

### Basic Resource
```hcl
data "azurerm_client_config" "current" {}

resource "azurerm_mssql_server" "this" {
  name                         = var.sql_server_name
  resource_group_name          = azurerm_resource_group.this.name
  location                     = azurerm_resource_group.this.location
  version                      = "12.0"
  minimum_tls_version          = "1.2"

  azuread_administrator {
    login_username              = var.aad_admin_login
    object_id                   = var.aad_admin_object_id
    tenant_id                   = data.azurerm_client_config.current.tenant_id
    azuread_authentication_only = true   # CRITICAL: Disable SQL authentication entirely
  }

  tags = var.tags
}

resource "azurerm_mssql_database" "this" {
  name      = var.database_name
  server_id = azurerm_mssql_server.this.id

  # Serverless configuration
  sku_name                    = "GP_S_Gen5_2"   # General Purpose Serverless, Gen5, max 2 vCores
  min_capacity                = 0.5
  auto_pause_delay_in_minutes = 60              # Pause after 60 min idle

  max_size_gb = 32

  tags = var.tags
}

# Allow Azure services to connect (for managed identity access)
resource "azurerm_mssql_firewall_rule" "allow_azure" {
  name             = "AllowAzureServices"
  server_id        = azurerm_mssql_server.this.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}
```

### RBAC Assignment
```hcl
# CRITICAL: Azure SQL uses contained database users, NOT standard Azure RBAC for data access.
# You CANNOT grant database-level permissions via Terraform or Bicep.
# After deployment, run T-SQL to create contained users:
#
#   CREATE USER [<identity-name>] FROM EXTERNAL PROVIDER;
#   ALTER ROLE db_datareader ADD MEMBER [<identity-name>];
#   ALTER ROLE db_datawriter ADD MEMBER [<identity-name>];
#
# The identity-name is the name of the User-Assigned Managed Identity resource.

# For CONTROL PLANE operations only (not data access):
resource "azurerm_role_assignment" "sql_contributor" {
  scope                = azurerm_mssql_server.this.id
  role_definition_name = "SQL Server Contributor"
  principal_id         = azurerm_user_assigned_identity.this.principal_id
}
```

### Private Endpoint
```hcl
resource "azurerm_private_endpoint" "sql" {
  name                = "${var.sql_server_name}-pe"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  subnet_id           = azurerm_subnet.private_endpoints.id

  private_service_connection {
    name                           = "${var.sql_server_name}-psc"
    private_connection_resource_id = azurerm_mssql_server.this.id
    is_manual_connection           = false
    subresource_names              = ["sqlServer"]
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [azurerm_private_dns_zone.sql.id]
  }
}

resource "azurerm_private_dns_zone" "sql" {
  name                = "privatelink.database.windows.net"
  resource_group_name = azurerm_resource_group.this.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "sql" {
  name                  = "sql-dns-link"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.sql.name
  virtual_network_id    = azurerm_virtual_network.this.id
}
```

## Bicep Patterns

### Basic Resource
```bicep
param sqlServerName string
param databaseName string
param location string = resourceGroup().location
param aadAdminLogin string
param aadAdminObjectId string
param tags object = {}

resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: sqlServerName
  location: location
  properties: {
    minimalTlsVersion: '1.2'
    administrators: {
      administratorType: 'ActiveDirectory'
      principalType: 'Group'              // or 'User', 'Application'
      login: aadAdminLogin
      sid: aadAdminObjectId
      tenantId: subscription().tenantId
      azureADOnlyAuthentication: true     // CRITICAL: Disable SQL authentication
    }
  }
  tags: tags
}

resource database 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sqlServer
  name: databaseName
  location: location
  sku: {
    name: 'GP_S_Gen5'                    // General Purpose Serverless
    tier: 'GeneralPurpose'
    family: 'Gen5'
    capacity: 2                           // Max 2 vCores
  }
  properties: {
    minCapacity: json('0.5')             // Min vCores (use json() for decimal)
    autoPauseDelay: 60                    // Pause after 60 min idle
    maxSizeBytes: 34359738368            // 32 GB
  }
  tags: tags
}

// Allow Azure services
resource firewallRule 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  parent: sqlServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

output sqlServerFqdn string = sqlServer.properties.fullyQualifiedDomainName
output databaseName string = database.name
```

### RBAC Assignment
```bicep
// CRITICAL: Azure SQL data-plane access uses T-SQL contained users, NOT Azure RBAC.
// After deployment, execute the following T-SQL against the database:
//
//   CREATE USER [<identity-name>] FROM EXTERNAL PROVIDER;
//   ALTER ROLE db_datareader ADD MEMBER [<identity-name>];
//   ALTER ROLE db_datawriter ADD MEMBER [<identity-name>];
//
// This must be run by the AAD admin configured on the server.
// There is no Bicep/ARM resource that can do this.

// Control-plane contributor role only (does NOT grant data access):
param principalId string

var sqlContributorRoleId = '6d8ee4ec-f05a-4a1d-8b00-a9b17e38b437'

resource sqlContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(sqlServer.id, principalId, sqlContributorRoleId)
  scope: sqlServer
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', sqlContributorRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Application Code

### Python
```python
import pyodbc
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()

# Get access token for Azure SQL
token = credential.get_token("https://database.windows.net/.default")

# Build connection string with access token
server = "<server-name>.database.windows.net"
database = "<database-name>"
conn_str = (
    f"Driver={{ODBC Driver 18 for SQL Server}};"
    f"Server=tcp:{server},1433;"
    f"Database={database};"
    f"Encrypt=yes;"
    f"TrustServerCertificate=no;"
)

# pyodbc uses SQL_COPT_SS_ACCESS_TOKEN for token-based auth
token_bytes = token.token.encode("utf-16-le")
token_struct = bytes([len(token_bytes) & 0xFF, (len(token_bytes) >> 8) & 0xFF]) + token_bytes

conn = pyodbc.connect(conn_str, attrs_before={1256: token_struct})

cursor = conn.cursor()
cursor.execute("SELECT TOP 10 * FROM dbo.MyTable")
rows = cursor.fetchall()
for row in rows:
    print(row)

conn.close()
```

### C#
```csharp
using Azure.Identity;
using Microsoft.Data.SqlClient;

var credential = new DefaultAzureCredential();

var connectionString = new SqlConnectionStringBuilder
{
    DataSource = "tcp:<server-name>.database.windows.net,1433",
    InitialCatalog = "<database-name>",
    Encrypt = true,
    TrustServerCertificate = false
}.ConnectionString;

await using var connection = new SqlConnection(connectionString);

// Use Azure AD token for authentication
var token = await credential.GetTokenAsync(
    new Azure.Core.TokenRequestContext(new[] { "https://database.windows.net/.default" })
);
connection.AccessToken = token.Token;

await connection.OpenAsync();

await using var command = new SqlCommand("SELECT TOP 10 * FROM dbo.MyTable", connection);
await using var reader = await command.ExecuteReaderAsync();

while (await reader.ReadAsync())
{
    Console.WriteLine(reader[0]);
}
```

### Node.js
```typescript
import { DefaultAzureCredential } from "@azure/identity";
import { Connection, Request } from "tedious";

const credential = new DefaultAzureCredential();

const token = await credential.getToken("https://database.windows.net/.default");

const config = {
  server: "<server-name>.database.windows.net",
  authentication: {
    type: "azure-active-directory-access-token" as const,
    options: {
      token: token.token,
    },
  },
  options: {
    database: "<database-name>",
    encrypt: true,
    port: 1433,
    trustServerCertificate: false,
  },
};

const connection = new Connection(config);

connection.on("connect", (err) => {
  if (err) {
    console.error("Connection failed:", err);
    return;
  }

  const request = new Request("SELECT TOP 10 * FROM dbo.MyTable", (err, rowCount) => {
    if (err) console.error(err);
    console.log(`${rowCount} rows returned`);
    connection.close();
  });

  request.on("row", (columns) => {
    columns.forEach((column) => console.log(column.value));
  });

  connection.execSql(request);
});

connection.connect();
```

## Common Pitfalls
- **Trying to use Azure RBAC for data access**: Azure SQL does NOT use `Microsoft.Authorization/roleAssignments` for data-plane access. You MUST create contained database users via T-SQL (`CREATE USER [name] FROM EXTERNAL PROVIDER`). This cannot be done in Terraform or Bicep.
- **Leaving SQL authentication enabled**: Always set `azuread_authentication_only = true` on the server. Without this, password-based SQL logins remain available.
- **Forgetting the post-deploy T-SQL step**: Infrastructure deployment creates the server and database, but application identity access requires a separate T-SQL script run by the AAD admin.
- **Serverless auto-pause latency**: First connection after auto-pause takes 30-60 seconds to resume. Applications need appropriate connection timeout settings.
- **pyodbc token encoding**: The access token must be encoded as UTF-16-LE with a 2-byte length prefix. This is a common source of authentication failures in Python.
- **ODBC driver requirement**: Python and Node.js connectivity requires ODBC Driver 18 for SQL Server installed on the host. Container images must include this driver.
- **Firewall for Azure services**: The `0.0.0.0` to `0.0.0.0` firewall rule allows all Azure services, not just your own. Use private endpoints for tighter control.

## Production Backlog Items
- Geo-replication (active geo-replication or failover groups) for disaster recovery
- Long-term backup retention (LTR) beyond the default 7-day PITR
- Advanced Threat Protection and vulnerability assessments
- Elastic pools for multi-tenant scenarios with variable workloads
- Transparent Data Encryption with customer-managed keys (CMK)
- Auditing to Log Analytics or Storage Account
- Private endpoint with DNS integration (remove public firewall rules)
- Connection pooling and retry logic for production resilience
- Database-level firewall rules scoped to specific IP ranges
