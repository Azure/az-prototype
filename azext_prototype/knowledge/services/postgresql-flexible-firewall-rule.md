---
service_namespace: Microsoft.DBforPostgreSQL/flexibleServers/firewallRules
display_name: PostgreSQL Flexible Server Firewall Rule
depends_on:
  - Microsoft.DBforPostgreSQL/flexibleServers
---

# PostgreSQL Flexible Server Firewall Rule

> Controls which IP addresses can connect to a PostgreSQL Flexible Server over its public endpoint. Required for non-VNet-integrated servers.

## When to Use
- Allow Azure services to connect via the special 0.0.0.0 rule
- Allow specific developer IPs for administrative access during POC
- Not needed when server uses VNet integration (private access mode)
- Complement private endpoint connectivity with selective public access

## POC Defaults
- **AllowAzureServices**: 0.0.0.0 to 0.0.0.0 (enables managed identity access from Azure services)
- **Developer IP rules**: Added as needed for local development

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "pg_firewall_allow_azure" {
  type      = "Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-06-01-preview"
  name      = "AllowAzureServices"
  parent_id = azapi_resource.pg_server.id

  body = {
    properties = {
      startIpAddress = "0.0.0.0"
      endIpAddress   = "0.0.0.0"
    }
  }
}

resource "azapi_resource" "pg_firewall_dev" {
  type      = "Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-06-01-preview"
  name      = "AllowDevIP"
  parent_id = azapi_resource.pg_server.id

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
resource firewallAllowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-06-01-preview' = {
  parent: pgServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource firewallDevIp 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-06-01-preview' = {
  parent: pgServer
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
- **0.0.0.0 allows ALL Azure services**: This rule allows any Azure service in any subscription, not just yours. Use VNet integration or private endpoints for production.
- **Public access must be enabled**: Firewall rules only work when the server is in public access mode. VNet-integrated servers ignore firewall rules entirely.
- **IP ranges, not CIDR**: PostgreSQL Flexible Server firewall rules use start/end IP addresses, not CIDR notation.
- **No DNS names**: Firewall rules only accept IP addresses, not FQDNs or DNS names.
- **Rule propagation delay**: Firewall rule changes can take up to 5 minutes to propagate.

## Production Backlog Items
- Remove 0.0.0.0 rule and switch to VNet integration or private endpoints
- Implement IP range restrictions for administrative access via VPN
- Automate firewall rule lifecycle tied to developer onboarding/offboarding
- Monitor and audit firewall rule changes
