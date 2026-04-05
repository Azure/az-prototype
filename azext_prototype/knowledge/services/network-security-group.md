---
service_namespace: Microsoft.Network/networkSecurityGroups
display_name: Network Security Group
depends_on: []
---

# Network Security Group

> Stateful packet filter that controls inbound and outbound traffic to Azure resources. Attached to subnets or NICs to enforce network segmentation.

## When to Use
- Every subnet should have an NSG attached for traffic filtering
- Control traffic between subnets (east-west) and to/from the internet (north-south)
- Enforce micro-segmentation between application tiers

## POC Defaults
- **Default rules**: Allow VNet-to-VNet, deny all inbound from internet
- **Priority**: Start at 100, increment by 10 for readability
- **Diagnostic settings**: NSGs do NOT support diagnostic settings (unlike VNets)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "nsg" {
  type      = "Microsoft.Network/networkSecurityGroups@2024-01-01"
  name      = var.nsg_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      securityRules = [
        {
          name = "DenyAllInbound"
          properties = {
            priority                 = 4096
            direction                = "Inbound"
            access                   = "Deny"
            protocol                 = "*"
            sourcePortRange          = "*"
            destinationPortRange     = "*"
            sourceAddressPrefix      = "*"
            destinationAddressPrefix = "*"
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

### RBAC Assignment
```hcl
# Network Contributor role for NSG management:
# 4d97b98b-1d4f-4787-a291-c67834d212e7
```

## Bicep Patterns

### Basic Resource
```bicep
param nsgName string
param location string = resourceGroup().location
param tags object = {}

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: nsgName
  location: location
  properties: {
    securityRules: [
      {
        name: 'DenyAllInbound'
        properties: {
          priority: 4096
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: '*'
          destinationAddressPrefix: '*'
        }
      }
    ]
  }
  tags: tags
}

output nsgId string = nsg.id
output nsgName string = nsg.name
```

## Application Code

### Python
```python
# NSGs are infrastructure — no application code. Traffic filtering
# happens at the network level, transparent to applications.
```

### C#
```csharp
// NSGs are infrastructure — no application code.
```

### Node.js
```typescript
// NSGs are infrastructure — no application code.
```

## Common Pitfalls
- **NSGs do NOT support diagnostic settings**: Unlike VNets, NSGs have no diagnostic categories. Do not create diagnostic settings for NSGs — ARM will reject with HTTP 400.
- **Wildcard source/destination**: Rules with `sourceAddressPrefix = "*"` allow all traffic. Use service tags (VirtualNetwork, AzureLoadBalancer) or specific CIDR ranges.
- **Rule priority conflicts**: Lower priority numbers are evaluated first. Ensure allow rules have lower priority than deny rules.
- **GatewaySubnet NSG restrictions**: NSGs on GatewaySubnet must allow Azure Gateway Manager ports (65200-65535) or VPN/ExpressRoute health probes will fail.
- **Stateful behavior**: NSG rules are stateful — if inbound traffic is allowed, the return traffic is automatically allowed without an explicit outbound rule.

## Production Backlog Items
- NSG flow logs for traffic analysis and threat detection
- Application Security Groups (ASGs) for role-based network rules
- Network Watcher integration for topology visualization
- Automated NSG rule auditing for compliance
