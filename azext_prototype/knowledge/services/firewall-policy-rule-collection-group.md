---
service_namespace: Microsoft.Network/firewallPolicies/ruleCollectionGroups
display_name: Firewall Rule Collection Group
depends_on:
  - Microsoft.Network/firewallPolicies
---

# Firewall Rule Collection Group

> A group of rule collections within a firewall policy. Organizes DNAT, network, and application rules by priority.

## When to Use
- Organize firewall rules by function (e.g., "AllowInfrastructure", "AllowApplications", "DenyAll")
- Each group has a priority that determines processing order relative to other groups
- Groups contain one or more rule collections

## POC Defaults
- **Priority**: 100 (allow rules), 200 (application rules), 65000 (deny all)
- **Action**: Allow for application traffic; Deny for catch-all

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "rule_collection_group" {
  type      = "Microsoft.Network/firewallPolicies/ruleCollectionGroups@2024-01-01"
  name      = var.group_name
  parent_id = azapi_resource.firewall_policy.id

  body = {
    properties = {
      priority = 100
      ruleCollections = [
        {
          ruleCollectionType = "FirewallPolicyFilterRuleCollection"
          name               = "AllowOutbound"
          priority           = 100
          action             = { type = "Allow" }
          rules = [
            {
              ruleType             = "NetworkRule"
              name                 = "AllowDNS"
              ipProtocols          = ["UDP"]
              sourceAddresses      = ["10.0.0.0/16"]
              destinationAddresses = ["*"]
              destinationPorts     = ["53"]
            }
          ]
        }
      ]
    }
  }
}
```

### RBAC Assignment
```hcl
# Rule collection management inherits from the parent firewall policy RBAC.
```

## Bicep Patterns

### Basic Resource
```bicep
param groupName string

resource ruleCollectionGroup 'Microsoft.Network/firewallPolicies/ruleCollectionGroups@2024-01-01' = {
  parent: firewallPolicy
  name: groupName
  properties: {
    priority: 100
    ruleCollections: [
      {
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        name: 'AllowOutbound'
        priority: 100
        action: { type: 'Allow' }
        rules: [
          {
            ruleType: 'NetworkRule'
            name: 'AllowDNS'
            ipProtocols: ['UDP']
            sourceAddresses: ['10.0.0.0/16']
            destinationAddresses: ['*']
            destinationPorts: ['53']
          }
        ]
      }
    ]
  }
}
```

## Application Code

### Python
```python
# Firewall rules are infrastructure — transparent to application code.
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
- **Priority uniqueness**: Each rule collection group must have a unique priority within the policy.
- **Collection type matters**: Use `FirewallPolicyFilterRuleCollection` for Allow/Deny, `FirewallPolicyNatRuleCollection` for DNAT.
- **Sequential creation**: Rule collection groups within the same policy must be created sequentially (not in parallel).

## Production Backlog Items
- Application rule collections for FQDN-based filtering
- DNAT rules for inbound port forwarding
- IP Groups for reusable address sets across rules
- Threat intelligence-based filtering rules
