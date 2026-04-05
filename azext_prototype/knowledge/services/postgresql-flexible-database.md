---
service_namespace: Microsoft.DBforPostgreSQL/flexibleServers/databases
display_name: PostgreSQL Flexible Server Database
depends_on:
  - Microsoft.DBforPostgreSQL/flexibleServers
---

# PostgreSQL Flexible Server Database

> A database within a PostgreSQL Flexible Server instance.

## When to Use
- Every PostgreSQL application needs at least one database
- Use separate databases for different application domains or tenants
- Default databases (postgres, azure_maintenance) should not be used for application data

## POC Defaults
- **Charset**: UTF8
- **Collation**: en_US.utf8

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "pg_database" {
  type      = "Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-06-01-preview"
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
```hcl
# PostgreSQL database access uses PostgreSQL-native roles, not Azure RBAC.
# After deployment, connect as the Entra admin and run:
#   CREATE ROLE <identity_name> LOGIN;
#   GRANT ALL ON DATABASE <db_name> TO <identity_name>;
```

## Bicep Patterns

### Basic Resource
```bicep
param databaseName string

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-06-01-preview' = {
  parent: pgServer
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

output databaseName string = database.name
```

## Application Code

### Python
```python
import psycopg2
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
token = credential.get_token("https://ossrdbms-aad.database.windows.net/.default")

conn = psycopg2.connect(
    host="<server>.postgres.database.azure.com",
    dbname=database_name,
    user="<managed-identity-name>",
    password=token.token,
    sslmode="require"
)
cursor = conn.cursor()
cursor.execute("SELECT version()")
print(cursor.fetchone())
conn.close()
```

### C#
```csharp
using Azure.Identity;
using Npgsql;

var credential = new DefaultAzureCredential();
var token = await credential.GetTokenAsync(
    new Azure.Core.TokenRequestContext(new[] { "https://ossrdbms-aad.database.windows.net/.default" }));

var connectionString = $"Host=<server>.postgres.database.azure.com;Database={databaseName};Username=<identity-name>;Password={token.Token};SSL Mode=Require";

await using var conn = new NpgsqlConnection(connectionString);
await conn.OpenAsync();
await using var cmd = new NpgsqlCommand("SELECT version()", conn);
Console.WriteLine(await cmd.ExecuteScalarAsync());
```

### Node.js
```typescript
import { DefaultAzureCredential } from "@azure/identity";
import { Client } from "pg";

const credential = new DefaultAzureCredential();
const token = await credential.getToken("https://ossrdbms-aad.database.windows.net/.default");

const client = new Client({
  host: "<server>.postgres.database.azure.com",
  database: databaseName,
  user: "<identity-name>",
  password: token.token,
  ssl: { rejectUnauthorized: true },
  port: 5432,
});
await client.connect();
const res = await client.query("SELECT version()");
console.log(res.rows[0]);
await client.end();
```

## Common Pitfalls
- **Token scope differs from SQL**: PostgreSQL uses `https://ossrdbms-aad.database.windows.net/.default`, not `https://database.windows.net/.default`.
- **Role creation required**: Like Azure SQL, database-level access requires native PostgreSQL role creation via SQL commands after deployment.
- **Default databases**: Don't use the `postgres` or `azure_maintenance` databases for application data.

## Production Backlog Items
- Connection pooling via PgBouncer (built into Flexible Server)
- Automated backup and point-in-time restore
- Read replicas for read-heavy workloads
- Schema migration automation
