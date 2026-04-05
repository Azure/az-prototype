---
service_namespace: Microsoft.Sql/servers/databases
display_name: Azure SQL Database
depends_on:
  - Microsoft.Sql/servers
---

# Azure SQL Database

> Fully managed relational database with built-in intelligence, serverless compute, and auto-pause for cost-effective POCs.

## When to Use
- Applications requiring relational data with ACID transactions
- Workloads with complex queries, joins, stored procedures, or reporting needs
- Migration of existing SQL Server workloads to the cloud

## POC Defaults
- **Compute tier**: Serverless (General Purpose) — auto-pauses after 60 minutes of inactivity
- **Max vCores**: 2 (sufficient for POC workloads)
- **Min vCores**: 0.5 (enables aggressive auto-pause savings)
- **Max storage**: 32 GB
- **SKU**: GP_S_Gen5 (General Purpose Serverless Gen5)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "sql_database" {
  type      = "Microsoft.Sql/servers/databases@2023-08-01-preview"
  name      = var.database_name
  location  = var.location
  parent_id = azapi_resource.sql_server.id

  body = {
    sku = {
      name     = "GP_S_Gen5"              # General Purpose Serverless
      tier     = "GeneralPurpose"
      family   = "Gen5"
      capacity = 2                         # Max 2 vCores
    }
    properties = {
      minCapacity    = 0.5
      autoPauseDelay = 60                  # Pause after 60 min idle
      maxSizeBytes   = 34359738368         # 32 GB
    }
  }

  tags = var.tags
  response_export_values = ["*"]
}
```

### Data-Plane Access (T-SQL — NOT Terraform/Bicep)
```sql
-- CRITICAL: Azure SQL uses contained database users for data access.
-- You CANNOT grant database-level permissions via Terraform or Bicep.
-- Run this T-SQL as the AAD admin after deployment:

CREATE USER [<managed-identity-name>] FROM EXTERNAL PROVIDER;
ALTER ROLE db_datareader ADD MEMBER [<managed-identity-name>];
ALTER ROLE db_datawriter ADD MEMBER [<managed-identity-name>];

-- The <managed-identity-name> is the name of the User-Assigned Managed Identity resource.
```

## Bicep Patterns

### Basic Resource
```bicep
param databaseName string
param location string = resourceGroup().location
param tags object = {}

resource database 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sqlServer
  name: databaseName
  location: location
  sku: {
    name: 'GP_S_Gen5'
    tier: 'GeneralPurpose'
    family: 'Gen5'
    capacity: 2
  }
  properties: {
    minCapacity: json('0.5')
    autoPauseDelay: 60
    maxSizeBytes: 34359738368
  }
  tags: tags
}

output databaseName string = database.name
output databaseId string = database.id
```

## Application Code

### Python
```python
import pyodbc
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
token = credential.get_token("https://database.windows.net/.default")

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
    options: { token: token.token },
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
  if (err) { console.error("Connection failed:", err); return; }
  const request = new Request("SELECT TOP 10 * FROM dbo.MyTable", (err, rowCount) => {
    if (err) console.error(err);
    connection.close();
  });
  request.on("row", (columns) => columns.forEach((col) => console.log(col.value)));
  connection.execSql(request);
});
connection.connect();
```

## Common Pitfalls
- **Trying to use Azure RBAC for data access**: Azure SQL does NOT use `Microsoft.Authorization/roleAssignments` for data-plane access. You MUST create contained database users via T-SQL. This cannot be done in Terraform or Bicep.
- **Forgetting the post-deploy T-SQL step**: Infrastructure deployment creates the database, but application identity access requires a separate T-SQL script run by the AAD admin.
- **Serverless auto-pause latency**: First connection after auto-pause takes 30-60 seconds. Applications need appropriate connection timeout settings.
- **pyodbc token encoding**: The access token must be encoded as UTF-16-LE with a 2-byte length prefix. Common source of auth failures in Python.
- **ODBC driver requirement**: Python and Node.js connectivity requires ODBC Driver 18. Container images must include this driver.

## Production Backlog Items
- Geo-replication (active geo-replication or failover groups) for disaster recovery
- Long-term backup retention (LTR) beyond the default 7-day PITR
- Elastic pools for multi-tenant scenarios with variable workloads
- Connection pooling and retry logic for production resilience
- Database-level firewall rules scoped to specific IP ranges
