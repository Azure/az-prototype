---
service_namespace: Microsoft.DBforMySQL/flexibleServers/firewallRules
display_name: MySQL Flexible Server Firewall Rule
depends_on:
  - Microsoft.DBforMySQL/flexibleServers
---

# MySQL Flexible Server Firewall Rule

> Controls which IP addresses can connect to a MySQL Flexible Server over its public endpoint. Required for public access mode servers.

## When to Use
- Allow Azure services to connect via the special 0.0.0.0 rule
- Allow specific developer IPs for administrative access during POC
- Not needed when server uses VNet integration (private access mode)
- Required for any external connectivity to a public-access MySQL server

## POC Defaults
- **AllowAzureServices**: 0.0.0.0 to 0.0.0.0 (enables managed identity access from Azure services)
- **Developer IP rules**: Added as needed for local development

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "mysql_firewall_allow_azure" {
  type      = "Microsoft.DBforMySQL/flexibleServers/firewallRules@2023-12-30"
  name      = "AllowAzureServices"
  parent_id = azapi_resource.mysql_server.id

  body = {
    properties = {
      startIpAddress = "0.0.0.0"
      endIpAddress   = "0.0.0.0"
    }
  }
}

resource "azapi_resource" "mysql_firewall_dev" {
  type      = "Microsoft.DBforMySQL/flexibleServers/firewallRules@2023-12-30"
  name      = "AllowDevIP"
  parent_id = azapi_resource.mysql_server.id

  body = {
    properties = {
      startIpAddress = var.dev_ip
      endIpAddress   = var.dev_ip
    }
  }
}
```

### RBAC Assignment
```hcl
# Firewall rule management requires Contributor role on the parent Flexible Server.
```

## Bicep Patterns

### Basic Resource
```bicep
resource firewallAllowAzure 'Microsoft.DBforMySQL/flexibleServers/firewallRules@2023-12-30' = {
  parent: mysqlServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource firewallDevIp 'Microsoft.DBforMySQL/flexibleServers/firewallRules@2023-12-30' = {
  parent: mysqlServer
  name: 'AllowDevIP'
  properties: {
    startIpAddress: devIpAddress
    endIpAddress: devIpAddress
  }
}
```

## Application Code

### Python
Infrastructure — transparent to application code

### C#
Infrastructure — transparent to application code

### Node.js
Infrastructure — transparent to application code

## Common Pitfalls
- **0.0.0.0 allows ALL Azure services**: This rule permits traffic from any Azure subscription, not just yours. Use VNet integration for production.
- **Public access must be enabled**: Firewall rules only apply when the server is in public access mode. VNet-integrated servers ignore them.
- **IP ranges, not CIDR**: MySQL Flexible Server firewall rules use start/end IP addresses, not CIDR notation.
- **Rule propagation delay**: Firewall rule changes can take several minutes to propagate.
- **No interaction with VNet rules**: If the server is created with private access, you cannot add firewall rules at all — the networking mode is immutable after creation.

## Production Backlog Items
- Remove 0.0.0.0 rule and migrate to VNet integration or private endpoints
- Implement IP range restrictions for admin access via VPN
- Automate firewall rule cleanup tied to developer offboarding
- Enable firewall rule change auditing via Azure Activity Log
