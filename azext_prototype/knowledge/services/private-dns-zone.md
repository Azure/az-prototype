---
service_namespace: Microsoft.Network/privateDnsZones
display_name: Private DNS Zone
depends_on: []
---

# Private DNS Zone

> Provides name resolution within a VNet for private endpoints. Each Azure service has a specific private DNS zone FQDN (e.g., privatelink.database.windows.net).

## When to Use
- Required for every private endpoint to resolve the service's private IP
- One DNS zone per service type, linked to the VNet
- Created in the Networking stage alongside VNets and private endpoints

## POC Defaults
- **Zone names**: Use exact Microsoft-documented FQDNs (e.g., `privatelink.vaultcore.azure.net`)
- **VNet link**: Auto-registration disabled (private endpoints handle DNS records)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "private_dns_zone" {
  type      = "Microsoft.Network/privateDnsZones@2020-06-01"
  name      = var.zone_name  # e.g., "privatelink.database.windows.net"
  location  = "global"
  parent_id = var.resource_group_id

  tags = var.tags
}
```

### RBAC Assignment
```hcl
# Private DNS Zone Contributor for zone management:
# b12aa53e-6015-4669-85d0-8515ebb5ae50
```

## Bicep Patterns

### Basic Resource
```bicep
param zoneName string
param tags object = {}

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: zoneName
  location: 'global'
  tags: tags
}

output zoneId string = privateDnsZone.id
output zoneName string = privateDnsZone.name
```

## Application Code

### Python
```python
# Private DNS zones are infrastructure — transparent to application code.
# Applications connect using the standard service FQDN (e.g., myserver.database.windows.net)
# and DNS resolution automatically routes to the private IP via the private DNS zone.
```

### C#
```csharp
// Private DNS zones are infrastructure — transparent to application code.
```

### Node.js
```typescript
// Private DNS zones are infrastructure — transparent to application code.
```

## Common Pitfalls
- **Zone names are exact FQDNs**: Use the exact Microsoft-documented zone name. For example, `privatelink.database.windows.net` (not `database.windows.net` or a custom name).
- **Location must be "global"**: Private DNS zones are always global resources. Setting a region will fail.
- **VNet link required**: The DNS zone must be linked to the VNet for resolution to work. Without the link, private endpoint DNS records are invisible.
- **One zone per service type**: Do not create separate zones per resource instance. One `privatelink.vaultcore.azure.net` zone serves all Key Vault private endpoints.

## Production Backlog Items
- Conditional forwarder integration for hybrid DNS (on-premises resolution)
- Multiple VNet links for hub-and-spoke topology
- DNS zone monitoring for resolution failures
- Cross-region DNS zone configuration for geo-redundancy
