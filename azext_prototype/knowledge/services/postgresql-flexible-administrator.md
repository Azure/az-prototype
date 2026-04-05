---
service_namespace: Microsoft.DBforPostgreSQL/flexibleServers/administrators
display_name: PostgreSQL Flexible Server Administrator
depends_on:
  - Microsoft.DBforPostgreSQL/flexibleServers
---

# PostgreSQL Flexible Server Administrator

> Configures Microsoft Entra ID (Azure AD) authentication administrators on a PostgreSQL Flexible Server, enabling passwordless managed identity access.

## When to Use
- Enable Entra ID authentication for passwordless connections from Azure services
- Assign a managed identity or user principal as the PostgreSQL server administrator
- Required before any Entra ID token-based connections can be established
- Use alongside (or instead of) local PostgreSQL password authentication

## POC Defaults
- **Principal type**: ServicePrincipal (for managed identity) or User (for dev access)
- **Auth type**: ActiveDirectory
- **Entra-only auth**: Disabled (allows both Entra and password auth for POC flexibility)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "pg_ad_admin" {
  type      = "Microsoft.DBforPostgreSQL/flexibleServers/administrators@2023-06-01-preview"
  name      = data.azurerm_client_config.current.object_id
  parent_id = azapi_resource.pg_server.id

  body = {
    properties = {
      principalName = var.admin_principal_name
      principalType = "ServicePrincipal"
      tenantId      = data.azurerm_client_config.current.tenant_id
    }
  }
}
```

### RBAC Assignment
```hcl
# The administrator identity is set at the PostgreSQL level, not via Azure RBAC.
# The principalName must match the display name of the Entra identity.
# After deployment, the Entra admin can create additional PostgreSQL roles:
#   SELECT * FROM pgaadauth_create_principal('<identity-name>', false, false);
```

## Bicep Patterns

### Basic Resource
```bicep
param principalName string
param principalObjectId string
param tenantId string = tenant().tenantId

resource pgAdmin 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2023-06-01-preview' = {
  parent: pgServer
  name: principalObjectId
  properties: {
    principalName: principalName
    principalType: 'ServicePrincipal'
    tenantId: tenantId
  }
}
```

## Application Code

### Python
```python
from azure.identity import DefaultAzureCredential
import psycopg2

# Once an Entra admin is configured, managed identities can authenticate
credential = DefaultAzureCredential()
token = credential.get_token("https://ossrdbms-aad.database.windows.net/.default")

conn = psycopg2.connect(
    host="<server>.postgres.database.azure.com",
    dbname="mydb",
    user="<managed-identity-name>",
    password=token.token,
    sslmode="require"
)
```

### C#
```csharp
using Azure.Identity;
using Npgsql;

var credential = new DefaultAzureCredential();
var token = await credential.GetTokenAsync(
    new Azure.Core.TokenRequestContext(new[] { "https://ossrdbms-aad.database.windows.net/.default" }));

var connStr = $"Host=<server>.postgres.database.azure.com;Database=mydb;Username=<identity-name>;Password={token.Token};SSL Mode=Require";
await using var conn = new NpgsqlConnection(connStr);
await conn.OpenAsync();
```

### Node.js
```typescript
import { DefaultAzureCredential } from "@azure/identity";
import { Client } from "pg";

const credential = new DefaultAzureCredential();
const token = await credential.getToken("https://ossrdbms-aad.database.windows.net/.default");

const client = new Client({
  host: "<server>.postgres.database.azure.com",
  database: "mydb",
  user: "<identity-name>",
  password: token.token,
  ssl: { rejectUnauthorized: true },
  port: 5432,
});
await client.connect();
```

## Common Pitfalls
- **Name must be the object ID**: The resource name for the administrator must be the Entra object ID of the principal, not a friendly name.
- **pgaadauth extension required**: The `azure.extensions` server parameter must include `pgaadauth` for Entra authentication to work. Ensure the server configuration enables it.
- **Principal name must match exactly**: The `principalName` must match the exact display name of the managed identity or user in Entra ID.
- **Only one Entra admin at a time**: PostgreSQL Flexible Server supports one Entra administrator. Setting a new one replaces the previous.
- **Token scope differs from SQL**: Use `https://ossrdbms-aad.database.windows.net/.default`, not `https://database.windows.net/.default`.

## Production Backlog Items
- Enable Entra-only authentication (disable password auth)
- Rotate administrator principal on identity lifecycle changes
- Audit Entra admin actions via PostgreSQL audit logging
- Configure additional PostgreSQL roles via pgaadauth_create_principal
