---
service_namespace: Microsoft.Network/dnsZones/A
display_name: DNS Zone A Record
depends_on:
  - Microsoft.Network/dnsZones
---

# DNS Zone A Record

> An A (Address) record in a public DNS zone that maps a hostname to one or more IPv4 addresses.

## When to Use
- Map a custom domain to an Azure resource's public IP address
- Point root domain (apex) to an Azure service (use alias record for dynamic IPs)
- Create subdomains pointing to specific IP addresses
- Required for custom domain verification and routing

## POC Defaults
- **TTL**: 300 seconds (5 minutes — short for POC iteration)
- **Records**: Single IPv4 address
- **Alias**: Use targetResource for Azure resources with dynamic IPs

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "dns_a_record" {
  type      = "Microsoft.Network/dnsZones/A@2023-07-01-preview"
  name      = var.record_name
  parent_id = azapi_resource.dns_zone.id

  body = {
    properties = {
      TTL = 300
      ARecords = [
        { ipv4Address = var.target_ip }
      ]
    }
  }
}

# Alias record pointing to an Azure resource
resource "azapi_resource" "dns_a_alias" {
  type      = "Microsoft.Network/dnsZones/A@2023-07-01-preview"
  name      = var.record_name
  parent_id = azapi_resource.dns_zone.id

  body = {
    properties = {
      TTL = 300
      targetResource = {
        id = azapi_resource.public_ip.id
      }
    }
  }
}
```

### RBAC Assignment
```hcl
# DNS Zone Contributor role allows managing records within a zone.
# Scoped at the zone level for least privilege.
```

## Bicep Patterns

### Basic Resource
```bicep
param recordName string
param targetIp string
param ttl int = 300

resource aRecord 'Microsoft.Network/dnsZones/A@2023-07-01-preview' = {
  parent: dnsZone
  name: recordName
  properties: {
    TTL: ttl
    ARecords: [
      { ipv4Address: targetIp }
    ]
  }
}

// Alias record for Azure resource
resource aAlias 'Microsoft.Network/dnsZones/A@2023-07-01-preview' = {
  parent: dnsZone
  name: recordName
  properties: {
    TTL: ttl
    targetResource: {
      id: publicIp.id
    }
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
- **Alias vs static**: Use alias records (`targetResource`) for Azure resources with dynamic IPs (public IPs, Front Door, Traffic Manager). Static A records break when IPs change.
- **Apex record limitations**: CNAME records can't be used at the zone apex. Use A alias records to point the root domain to Azure resources.
- **TTL caching**: DNS clients cache records for the TTL duration. A 3600-second TTL means changes take up to 1 hour to propagate. Use short TTLs during POC.
- **Cannot mix alias and ARecords**: A record set is either alias-based (`targetResource`) or static (`ARecords`), not both. The API rejects mixed configurations.
- **Name '@' for apex**: Use `@` as the record name to create an apex (root) record.

## Production Backlog Items
- Increase TTL to 3600 seconds for reduced DNS query load
- Geographic or latency-based routing via Traffic Manager alias records
- DNSSEC configuration for DNS response integrity
- Automated DNS record lifecycle management
