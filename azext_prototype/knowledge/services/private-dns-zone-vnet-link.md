---
service_namespace: Microsoft.Network/privateDnsZones/virtualNetworkLinks
display_name: Private DNS Zone VNet Link
depends_on:
  - Microsoft.Network/privateDnsZones
  - Microsoft.Network/virtualNetworks
---

# Private DNS Zone VNet Link

> Links a private DNS zone to a VNet, enabling resources in that VNet to resolve private endpoint DNS records.

## When to Use
- Every private DNS zone must be linked to the VNet where resources need resolution
- One link per VNet per DNS zone
- Auto-registration should be disabled for private endpoint DNS zones

## POC Defaults
- **Registration enabled**: false (private endpoints manage their own DNS records)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "dns_vnet_link" {
  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01"
  name      = var.link_name
  location  = "global"
  parent_id = azapi_resource.private_dns_zone.id

  body = {
    properties = {
      virtualNetwork = {
        id = azapi_resource.virtual_network.id
      }
      registrationEnabled = false
    }
  }

  tags = var.tags
}
```

### RBAC Assignment
```hcl
# Managed via the parent DNS zone's RBAC.
```

## Bicep Patterns

### Basic Resource
```bicep
param linkName string
param vnetId string

resource vnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: privateDnsZone
  name: linkName
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnetId
    }
    registrationEnabled: false
  }
}
```

## Application Code

### Python
```python
# VNet links are infrastructure — transparent to application code.
```

### C#
```csharp
// VNet links are infrastructure — transparent to application code.
```

### Node.js
```typescript
// VNet links are infrastructure — transparent to application code.
```

## Common Pitfalls
- **Location must be "global"**: Same as the parent DNS zone — always global.
- **Registration enabled false**: For private endpoint zones, always set `registrationEnabled = false`. Auto-registration is for VM DNS records, not private endpoints.
- **One link per VNet**: You cannot create multiple links from the same DNS zone to the same VNet.
- **Link name uniqueness**: Link names must be unique within the DNS zone.

## Production Backlog Items
- Hub-and-spoke VNet link topology for centralized DNS resolution
- Link monitoring for resolution health
- Cross-subscription VNet links for shared services architecture
