---
service_namespace: Microsoft.Sql/servers/firewallRules
display_name: SQL Server Firewall Rule
depends_on:
  - Microsoft.Sql/servers
---

# SQL Server Firewall Rule

> Controls which IP addresses can connect to an Azure SQL server. The special 0.0.0.0 rule allows all Azure services.

## When to Use
- Allow Azure services to connect (managed identity access requires this)
- Allow specific client IP ranges for administrative access
- Not needed when using private endpoints exclusively

## POC Defaults
- **AllowAzureServices**: Enabled (0.0.0.0 to 0.0.0.0) — required for managed identity access without private endpoints
- **Client IP rules**: Add as needed for development

## Terraform Patterns

### Basic Resource (Allow Azure Services)
```hcl
resource "azapi_resource" "sql_firewall_allow_azure" {
  type      = "Microsoft.Sql/servers/firewallRules@2023-08-01-preview"
  name      = "AllowAzureServices"
  parent_id = azapi_resource.sql_server.id

  body = {
    properties = {
      startIpAddress = "0.0.0.0"
      endIpAddress   = "0.0.0.0"
    }
  }
}
```

### Client IP Rule
```hcl
resource "azapi_resource" "sql_firewall_client" {
  type      = "Microsoft.Sql/servers/firewallRules@2023-08-01-preview"
  name      = "AllowClientIP"
  parent_id = azapi_resource.sql_server.id

  body = {
    properties = {
      startIpAddress = var.client_ip
      endIpAddress   = var.client_ip
    }
  }
}
```

### RBAC Assignment
```hcl
# Firewall rule management requires SQL Server Contributor role
# on the parent SQL server resource.
```

## Bicep Patterns

### Basic Resource
```bicep
resource firewallRule 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  parent: sqlServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}
```

## Application Code

### Python
```python
# Firewall rules are infrastructure — transparent to application code.
# Applications connect via the server FQDN and authentication handles access.
```

### C#
```csharp
// Firewall rules are infrastructure — transparent to application code.
```

### Node.js
```typescript
// Firewall rules are infrastructure — transparent to application code.
```

## Common Pitfalls
- **0.0.0.0 allows ALL Azure services**: The "Allow Azure Services" rule allows any Azure service in any subscription, not just your own. Use private endpoints for tighter control.
- **Firewall blocks private endpoint traffic**: If the server has firewall rules AND private endpoints, ensure the firewall allows the PE subnet or set `publicNetworkAccess = "Disabled"`.
- **IP ranges, not CIDR**: Firewall rules use start/end IP addresses, not CIDR notation.

## Production Backlog Items
- Remove 0.0.0.0 rule and use private endpoints exclusively
- IP-based rules for administrative access via VPN or bastion
- Firewall rule auditing and monitoring
