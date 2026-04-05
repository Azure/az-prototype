---
service_namespace: Microsoft.Synapse/workspaces/sqlPools
display_name: Synapse Dedicated SQL Pool
depends_on:
  - Microsoft.Synapse/workspaces
---

# Synapse Dedicated SQL Pool

> Massively Parallel Processing (MPP) data warehouse engine within a Synapse workspace, providing high-performance analytics on structured data with T-SQL compatibility and columnar storage.

## When to Use
- **Enterprise data warehousing** -- large-scale analytics on terabytes to petabytes of structured data
- **Complex SQL workloads** -- star/snowflake schema queries, complex joins, window functions
- **Power BI integration** -- direct query mode for real-time dashboards on warehouse data
- **ETL/ELT target** -- landing zone for data pipelines with high-throughput parallel loading
- **Predictable performance** -- dedicated compute resources with guaranteed query performance

Choose dedicated SQL pools over serverless SQL when you need consistently fast queries on large datasets, or when workloads run continuously. Choose serverless SQL for ad-hoc queries on data lake files without provisioning compute.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| DWU | DW100c | Smallest tier; 1 compute node |
| Collation | SQL_Latin1_General_CP1_CI_AS | Default; match source data collation |
| Geo-backup | Enabled | Default; can disable for cost savings |
| Pause when idle | Manual | Pause to stop billing; no auto-pause on dedicated pools |

**Important:** Dedicated SQL pools bill continuously while running. **Pause** the pool when not in use during POC to avoid significant charges.

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "sql_pool" {
  type      = "Microsoft.Synapse/workspaces/sqlPools@2021-06-01"
  name      = var.name
  location  = var.location
  parent_id = azapi_resource.synapse_workspace.id

  body = {
    sku = {
      name     = "DW100c"
      capacity = 0  # Determined by SKU name
    }
    properties = {
      collation        = "SQL_Latin1_General_CP1_CI_AS"
      createMode       = "Default"  # or "Recovery", "PointInTimeRestore"
      storageAccountType = "GRS"    # or "LRS" to save cost
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}
```

### RBAC Assignment

```hcl
# Synapse SQL pools use workspace-level RBAC and T-SQL permissions.
# Synapse Contributor on the workspace for pool management:
resource "azapi_resource" "synapse_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.synapse_workspace.id}-${var.principal_id}-synapse-contributor")
  parent_id = azapi_resource.synapse_workspace.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/6e4bf58a-b8e1-4cc3-bbf9-d73143322b78"
      principalId      = var.principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('SQL pool name')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Data warehouse units (DW100c is smallest)')
@allowed(['DW100c', 'DW200c', 'DW300c', 'DW400c', 'DW500c', 'DW1000c', 'DW1500c', 'DW2000c'])
param skuName string = 'DW100c'

param tags object = {}

resource sqlPool 'Microsoft.Synapse/workspaces/sqlPools@2021-06-01' = {
  parent: synapseWorkspace
  name: name
  location: location
  tags: tags
  sku: {
    name: skuName
  }
  properties: {
    collation: 'SQL_Latin1_General_CP1_CI_AS'
    createMode: 'Default'
    storageAccountType: 'GRS'
  }
}

output id string = sqlPool.id
output name string = sqlPool.name
```

## Application Code

### Python

```python
# Connect using pyodbc or sqlalchemy with Azure AD token
import pyodbc
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
token = credential.get_token("https://database.windows.net/.default")

conn_str = (
    "DRIVER={ODBC Driver 18 for SQL Server};"
    f"SERVER={synapse_workspace_name}.sql.azuresynapse.net;"
    f"DATABASE={sql_pool_name};"
    "Encrypt=yes;TrustServerCertificate=no;"
)
conn = pyodbc.connect(conn_str, attrs_before={1256: token.token.encode("UTF-16-LE")})

cursor = conn.cursor()
cursor.execute("SELECT TOP 10 * FROM dbo.FactSales")
rows = cursor.fetchall()
```

### C#

```csharp
using Microsoft.Data.SqlClient;
using Azure.Identity;

var credential = new DefaultAzureCredential();
var token = await credential.GetTokenAsync(
    new TokenRequestContext(new[] { "https://database.windows.net/.default" }));

var connectionString =
    $"Server={synapseWorkspaceName}.sql.azuresynapse.net;" +
    $"Database={sqlPoolName};Encrypt=True;TrustServerCertificate=False;";

using var connection = new SqlConnection(connectionString);
connection.AccessToken = token.Token;
await connection.OpenAsync();

using var command = new SqlCommand("SELECT TOP 10 * FROM dbo.FactSales", connection);
using var reader = await command.ExecuteReaderAsync();
while (await reader.ReadAsync())
{
    Console.WriteLine(reader[0]);
}
```

### Node.js

```typescript
import { DefaultAzureCredential } from "@azure/identity";
import * as sql from "mssql";

const credential = new DefaultAzureCredential();
const token = await credential.getToken("https://database.windows.net/.default");

const config: sql.config = {
  server: `${synapseWorkspaceName}.sql.azuresynapse.net`,
  database: sqlPoolName,
  options: { encrypt: true, trustServerCertificate: false },
  authentication: {
    type: "azure-active-directory-access-token",
    options: { token: token.token },
  },
};

const pool = await sql.connect(config);
const result = await pool.request().query("SELECT TOP 10 * FROM dbo.FactSales");
console.log(result.recordset);
```

## Common Pitfalls

1. **Continuous billing** -- Dedicated SQL pools bill per hour while running. Always **pause** the pool when not in use. Forgetting to pause during POC generates significant unexpected charges.
2. **No auto-pause** -- Unlike Spark pools, dedicated SQL pools do not auto-pause. You must pause manually (portal, API, or automation).
3. **DWU scaling causes brief disconnect** -- Scaling up/down (changing DWU) causes a brief outage (typically 1-2 minutes). Plan scaling during maintenance windows.
4. **Distribution key selection** -- Choosing the wrong distribution key (`HASH`, `ROUND_ROBIN`, `REPLICATE`) causes data skew and poor query performance. Hash distribute large fact tables on the most commonly joined column.
5. **Workload management** -- Without configuring workload groups, all queries share the same resources. Heavy queries can starve smaller ones.
6. **T-SQL limitations** -- Not all T-SQL features are supported (e.g., cursors, cross-database queries, certain built-in functions). Test existing scripts before migration.
7. **Result set caching** -- Dedicated pools support result set caching, but it's disabled by default. Enable for repeated queries on relatively static data.

## Production Backlog Items

- [ ] Right-size DWU based on actual query performance and concurrency needs
- [ ] Configure workload isolation and workload groups for query prioritization
- [ ] Implement automatic pause/resume via Azure Automation or Logic Apps
- [ ] Optimize table distributions (hash, round-robin, replicate) based on query patterns
- [ ] Enable result set caching for frequently-run reports
- [ ] Configure auditing and threat detection on the SQL pool
- [ ] Set up monitoring for DWU utilization, query performance, and data skew
- [ ] Plan geo-backup and restore procedures for disaster recovery
