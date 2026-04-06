---
service_namespace: Microsoft.Synapse/workspaces/firewallRules
display_name: Synapse Firewall Rule
depends_on:
  - Microsoft.Synapse/workspaces
---

# Synapse Firewall Rule

> IP-based firewall rule on a Synapse workspace that controls which client IP addresses can connect to the workspace endpoints (SQL, Spark, development).

## When to Use
- **Developer access** -- allow specific developer IP addresses to connect to Synapse Studio and SQL endpoints
- **CI/CD pipelines** -- allow build agent IPs to deploy Synapse artifacts
- **Allow Azure services** -- special rule `0.0.0.0` to `0.0.0.0` permits all Azure service traffic
- **POC access** -- temporarily allow all IPs (`0.0.0.0` to `255.255.255.255`) for quick POC setup

Firewall rules apply to the workspace's public endpoints. For production, use managed VNet and private endpoints instead.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Allow all Azure | Enabled | `AllowAllWindowsAzureIps` rule |
| Developer IPs | Added | Specific IPs for team members |
| Allow all | Optional | `0.0.0.0` to `255.255.255.255` for POC only |

## Terraform Patterns

### Basic Resource

```hcl
# Allow Azure services
resource "azapi_resource" "allow_azure" {
  type      = "Microsoft.Synapse/workspaces/firewallRules@2021-06-01"
  name      = "AllowAllWindowsAzureIps"
  parent_id = azapi_resource.synapse_workspace.id

  body = {
    properties = {
      startIpAddress = "0.0.0.0"
      endIpAddress   = "0.0.0.0"
    }
  }
}

# Allow specific developer IP
resource "azapi_resource" "allow_developer" {
  type      = "Microsoft.Synapse/workspaces/firewallRules@2021-06-01"
  name      = "AllowDeveloper"
  parent_id = azapi_resource.synapse_workspace.id

  body = {
    properties = {
      startIpAddress = var.developer_ip
      endIpAddress   = var.developer_ip
    }
  }
}

# Allow all (POC only)
resource "azapi_resource" "allow_all" {
  type      = "Microsoft.Synapse/workspaces/firewallRules@2021-06-01"
  name      = "AllowAll"
  parent_id = azapi_resource.synapse_workspace.id

  body = {
    properties = {
      startIpAddress = "0.0.0.0"
      endIpAddress   = "255.255.255.255"
    }
  }
}
```

### RBAC Assignment

```hcl
# Firewall rule management inherits from Synapse workspace RBAC.
# Synapse Contributor (6e4bf58a-b8e1-4cc3-bbf9-d73143322b78) or Contributor on the workspace.
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Rule name')
param ruleName string

@description('Start IP address')
param startIpAddress string

@description('End IP address')
param endIpAddress string

resource firewallRule 'Microsoft.Synapse/workspaces/firewallRules@2021-06-01' = {
  parent: synapseWorkspace
  name: ruleName
  properties: {
    startIpAddress: startIpAddress
    endIpAddress: endIpAddress
  }
}

// Allow Azure services (special rule)
resource allowAzure 'Microsoft.Synapse/workspaces/firewallRules@2021-06-01' = {
  parent: synapseWorkspace
  name: 'AllowAllWindowsAzureIps'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}
```

## Application Code

### Python
Infrastructure -- transparent to application code. Firewall rules control network-level access; applications connect to the Synapse endpoint and the firewall allows or denies the connection based on source IP.

### C#
Infrastructure -- transparent to application code. Firewall rules control network-level access; applications connect to the Synapse endpoint and the firewall allows or denies the connection based on source IP.

### Node.js
Infrastructure -- transparent to application code. Firewall rules control network-level access; applications connect to the Synapse endpoint and the firewall allows or denies the connection based on source IP.

## Common Pitfalls

1. **AllowAllWindowsAzureIps is special** -- The name `AllowAllWindowsAzureIps` with `0.0.0.0` to `0.0.0.0` is a special Azure-recognized rule. Any other name with the same IPs does not grant Azure service access.
2. **Rules apply to public endpoints only** -- If the workspace uses managed VNet with private endpoints, firewall rules only affect the public endpoint. Disable the public endpoint for full isolation.
3. **IP must be public** -- Private IP ranges (10.x, 172.16.x, 192.168.x) are invalid for firewall rules. Traffic from private networks uses private endpoints.
4. **Propagation delay** -- Firewall rule changes take up to 5 minutes to propagate. Connections may be denied briefly after adding a new rule.
5. **Allow-all is a security risk** -- The `0.0.0.0` to `255.255.255.255` rule opens the workspace to the entire internet. Use only for initial POC setup and remove before production.
6. **No deny rules** -- Synapse firewall only supports allow rules. To deny specific IPs while allowing others, you must use NSGs on the VNet or Azure Firewall.
7. **Dynamic developer IPs** -- Home office IPs change frequently. Consider using VPN + private endpoint instead of constantly updating firewall rules.

## Production Backlog Items

- [ ] Remove all allow-all firewall rules
- [ ] Enable managed VNet with private endpoints for the workspace
- [ ] Disable public network access entirely
- [ ] Configure managed private endpoints for data sources (ADLS, SQL)
- [ ] Use Azure AD Conditional Access instead of IP-based firewall rules
- [ ] Document approved IP ranges and implement change control
