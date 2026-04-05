---
service_namespace: Microsoft.Network/firewallPolicies
display_name: Azure Firewall Policy
depends_on: []
---

# Azure Firewall Policy

> Defines the rule collection groups, threat intelligence settings, and DNS proxy configuration for an Azure Firewall instance.

## When to Use
- Central rule management for one or more Azure Firewalls
- Define DNAT, network, and application rules in organized collections
- Share policies across firewalls in hub-and-spoke topologies

## POC Defaults
- **SKU**: Standard (Premium adds TLS inspection, IDPS)
- **Threat intelligence mode**: Alert (log but don't block for POC)
- **DNS proxy**: Enabled

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "firewall_policy" {
  type      = "Microsoft.Network/firewallPolicies@2024-01-01"
  name      = var.policy_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      sku = { tier = "Standard" }
      threatIntelMode = "Alert"
      dnsSettings = {
        enableProxy = true
      }
    }
  }

  tags = var.tags
}
```

### RBAC Assignment
```hcl
# Network Contributor role for firewall policy management.
```

## Bicep Patterns

### Basic Resource
```bicep
param policyName string
param location string = resourceGroup().location
param tags object = {}

resource firewallPolicy 'Microsoft.Network/firewallPolicies@2024-01-01' = {
  name: policyName
  location: location
  properties: {
    sku: { tier: 'Standard' }
    threatIntelMode: 'Alert'
    dnsSettings: { enableProxy: true }
  }
  tags: tags
}

output policyId string = firewallPolicy.id
```

## Application Code

### Python
```python
# Firewall policies are infrastructure — transparent to application code.
```

### C#
```csharp
// Firewall policies are infrastructure — transparent to application code.
```

### Node.js
```typescript
// Firewall policies are infrastructure — transparent to application code.
```

## Common Pitfalls
- **Policy vs inline rules**: Always use a policy (not inline rules on the firewall). Policies are reusable and support rule collection groups.
- **SKU must match firewall**: A Standard policy can only be associated with a Standard firewall.
- **Rule processing order**: DNAT rules → Network rules → Application rules. Within each type, lower priority numbers are processed first.

## Production Backlog Items
- Premium SKU for TLS inspection and IDPS
- Threat intelligence in Deny mode
- IP Groups for reusable address sets
- Policy inheritance for hub-and-spoke topology
