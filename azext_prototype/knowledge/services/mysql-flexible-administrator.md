---
service_namespace: Microsoft.DBforMySQL/flexibleServers/administrators
display_name: MySQL Flexible Server Administrator
depends_on:
  - Microsoft.DBforMySQL/flexibleServers
---

# MySQL Flexible Server Administrator

> Configures Microsoft Entra ID (Azure AD) authentication administrators on a MySQL Flexible Server, enabling passwordless managed identity access.

## When to Use
- Enable Entra ID authentication for passwordless connections from Azure services
- Assign a managed identity or user principal as the MySQL server administrator
- Required before any Entra ID token-based connections can be established
- Use alongside local MySQL password authentication for POC convenience

## POC Defaults
- **Principal type**: ServicePrincipal (for managed identity)
- **Identity type**: The server must have a user-assigned managed identity configured
- **Administrator type**: ActiveDirectory

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "mysql_ad_admin" {
  type      = "Microsoft.DBforMySQL/flexibleServers/administrators@2023-12-30"
  name      = "ActiveDirectory"
  parent_id = azapi_resource.mysql_server.id

  body = {
    properties = {
      administratorType  = "ActiveDirectory"
      identityResourceId = azapi_resource.user_identity.id
      login              = var.admin_login_name
      sid                = var.admin_principal_id
      tenantId           = var.tenant_id
    }
  }
}
```

### RBAC Assignment
```hcl
# The administrator identity is set at the MySQL level, not via Azure RBAC.
# The server needs a user-assigned managed identity for Entra auth to work.
# After deployment, the Entra admin creates additional MySQL users:
#   CREATE AADUSER '<identity-name>' IDENTIFIED BY '<object-id>';
#   GRANT ALL ON mydb.* TO '<identity-name>';
```

## Bicep Patterns

### Basic Resource
```bicep
param adminLogin string
param adminSid string
param identityResourceId string
param tenantId string = tenant().tenantId

resource mysqlAdmin 'Microsoft.DBforMySQL/flexibleServers/administrators@2023-12-30' = {
  parent: mysqlServer
  name: 'ActiveDirectory'
  properties: {
    administratorType: 'ActiveDirectory'
    identityResourceId: identityResourceId
    login: adminLogin
    sid: adminSid
    tenantId: tenantId
  }
}
```

## Application Code

### Python
```python
from azure.identity import DefaultAzureCredential
import mysql.connector

credential = DefaultAzureCredential()
token = credential.get_token("https://ossrdbms-aad.database.windows.net/.default")

conn = mysql.connector.connect(
    host="<server>.mysql.database.azure.com",
    user="<managed-identity-name>",
    password=token.token,
    database="mydb",
    ssl_ca="/path/to/DigiCertGlobalRootCA.crt.pem"
)
cursor = conn.cursor()
cursor.execute("SELECT VERSION()")
print(cursor.fetchone())
conn.close()
```

### C#
```csharp
using Azure.Identity;
using MySqlConnector;

var credential = new DefaultAzureCredential();
var token = await credential.GetTokenAsync(
    new Azure.Core.TokenRequestContext(new[] { "https://ossrdbms-aad.database.windows.net/.default" }));

var connStr = $"Server=<server>.mysql.database.azure.com;Database=mydb;User Id=<identity-name>;Password={token.Token};SslMode=Required";
await using var conn = new MySqlConnection(connStr);
await conn.OpenAsync();
```

### Node.js
```typescript
import { DefaultAzureCredential } from "@azure/identity";
import mysql from "mysql2/promise";

const credential = new DefaultAzureCredential();
const token = await credential.getToken("https://ossrdbms-aad.database.windows.net/.default");

const conn = await mysql.createConnection({
  host: "<server>.mysql.database.azure.com",
  user: "<identity-name>",
  password: token.token,
  database: "mydb",
  ssl: { rejectUnauthorized: true },
});
const [rows] = await conn.execute("SELECT VERSION()");
console.log(rows);
await conn.end();
```

## Common Pitfalls
- **User-assigned identity required**: Unlike PostgreSQL, MySQL Flexible Server requires a user-assigned managed identity on the server resource itself for Entra admin to function.
- **Resource name must be 'ActiveDirectory'**: The administrator resource name is always the literal string `ActiveDirectory`, not an object ID.
- **identityResourceId is mandatory**: The full resource ID of the user-assigned managed identity must be provided; system-assigned identity alone is insufficient.
- **Token scope same as PostgreSQL**: Use `https://ossrdbms-aad.database.windows.net/.default` for both MySQL and PostgreSQL Entra auth.
- **SSL certificate required**: MySQL clients need the DigiCert Global Root CA certificate for SSL connections.

## Production Backlog Items
- Enable Entra-only authentication (disable MySQL native auth)
- Automate MySQL AADUSER creation for application managed identities
- Configure audit logging for Entra admin operations
- Certificate rotation strategy for SSL connections
