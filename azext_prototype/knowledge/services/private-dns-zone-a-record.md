---
service_namespace: Microsoft.Network/privateDnsZones/A
display_name: Private DNS Zone A Record
depends_on:
  - Microsoft.Network/privateDnsZones
---

# Private DNS Zone A Record

> An A record in a private DNS zone that maps a hostname to a private IPv4 address for VNet-internal name resolution. Most commonly auto-created by private endpoint DNS zone groups.

## When to Use
- Manual A records for VNet-internal service discovery (e.g., custom hostnames for VMs)
- Override public DNS resolution with private IPs within a VNet
- Usually auto-managed by private endpoint DNS zone groups — manual creation is the exception
- Custom split-horizon DNS for hybrid connectivity scenarios

## POC Defaults
- **TTL**: 300 seconds
- **Records**: Single private IPv4 address
- **Auto-registration**: Disabled (DNS zone group handles private endpoint records)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "private_dns_a_record" {
  type      = "Microsoft.Network/privateDnsZones/A@2024-06-01"
  name      = var.record_name
  parent_id = azapi_resource.private_dns_zone.id

  body = {
    properties = {
      ttl = 300
      aRecords = [
        { ipv4Address = var.private_ip }
      ]
    }
  }
}
```

### RBAC Assignment
```hcl
# Private DNS Zone Contributor role allows managing records within a private zone.
```

## Bicep Patterns

### Basic Resource
```bicep
param recordName string
param privateIp string
param ttl int = 300

resource aRecord 'Microsoft.Network/privateDnsZones/A@2024-06-01' = {
  parent: privateDnsZone
  name: recordName
  properties: {
    ttl: ttl
    aRecords: [
      { ipv4Address: privateIp }
    ]
  }
}

output fqdn string = '${recordName}.${privateDnsZone.name}'
```

## Application Code

### Python
Infrastructure — transparent to application code

### C#
Infrastructure — transparent to application code

### Node.js
Infrastructure — transparent to application code

## Common Pitfalls
- **Property casing differs from public DNS**: Private DNS zones use lowercase `ttl` and `aRecords`, while public DNS zones use `TTL` and `ARecords`. Mixing casing causes deployment failures.
- **Auto-registration conflicts**: If auto-registration is enabled on a VNet link, manually created records for VM names may conflict with auto-registered records.
- **DNS zone group is preferred**: For private endpoints, use a DNS zone group (private endpoint child resource) instead of manually creating A records. Zone groups auto-manage record lifecycle.
- **VNet link required**: The private DNS zone must be linked to the VNet for resolution to work. Records exist but don't resolve without the link.
- **No alias records**: Private DNS zones do not support alias/targetResource records. Only static A records are supported.

## Production Backlog Items
- DNS zone group automation for all private endpoints
- VNet link management across hub-spoke topologies
- DNS resolution monitoring and health checks
- Record lifecycle automation for VM scale sets
