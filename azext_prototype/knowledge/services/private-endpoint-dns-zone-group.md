---
service_namespace: Microsoft.Network/privateEndpoints/privateDnsZoneGroups
display_name: Private Endpoint DNS Zone Group
depends_on:
  - Microsoft.Network/privateEndpoints
  - Microsoft.Network/privateDnsZones
---

# Private Endpoint DNS Zone Group

> Associates a private endpoint with one or more private DNS zones, automatically creating DNS A records that map the service FQDN to the private IP.

## When to Use
- Every private endpoint needs a DNS zone group for name resolution
- Links the private endpoint's private IP to the correct DNS zone
- Without this, applications must use the private IP directly (fragile)

## POC Defaults
- **Name**: "default" (convention)
- **DNS zone configs**: One per service's required DNS zone

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "pe_dns_zone_group" {
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01"
  name      = "default"
  parent_id = azapi_resource.private_endpoint.id

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "config"
          properties = {
            privateDnsZoneId = azapi_resource.private_dns_zone.id
          }
        }
      ]
    }
  }
}
```

### RBAC Assignment
```hcl
# Managed via the parent private endpoint's RBAC.
# Requires Network Contributor on both the PE and the DNS zone.
```

## Bicep Patterns

### Basic Resource
```bicep
resource dnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config'
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
}
```

## Application Code

### Python
```python
# DNS zone groups are infrastructure — transparent to application code.
# Once configured, the service FQDN (e.g., myserver.database.windows.net)
# resolves to the private IP automatically.
```

### C#
```csharp
// DNS zone groups are infrastructure — transparent to application code.
```

### Node.js
```typescript
// DNS zone groups are infrastructure — transparent to application code.
```

## Common Pitfalls
- **Name should be "default"**: While other names work, "default" is the convention and some Azure portal features expect it.
- **DNS zone must be linked to VNet**: The DNS zone group creates A records, but resolution only works if the DNS zone is also linked to the VNet via a VNet link.
- **One zone group per PE**: Each private endpoint has exactly one DNS zone group. Multiple DNS zone configs can be in the same group.
- **Config name uniqueness**: Each `privateDnsZoneConfigs` entry must have a unique name.

## Production Backlog Items
- Multi-zone configurations for services with multiple DNS zones (e.g., Cosmos DB multi-API)
- Cross-region DNS zone group configuration for geo-redundant private endpoints
