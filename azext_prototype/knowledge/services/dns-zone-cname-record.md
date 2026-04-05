---
service_namespace: Microsoft.Network/dnsZones/CNAME
display_name: DNS Zone CNAME Record
depends_on:
  - Microsoft.Network/dnsZones
---

# DNS Zone CNAME Record

> A CNAME (Canonical Name) record in a public DNS zone that maps an alias hostname to another domain name (the canonical name).

## When to Use
- Map subdomains to Azure service FQDNs (e.g., `www` to `myapp.azurewebsites.net`)
- Create vanity URLs pointing to Azure-managed endpoints
- Custom domain verification for App Service, Front Door, or CDN
- NOT usable at the zone apex (use an A alias record instead)

## POC Defaults
- **TTL**: 300 seconds (5 minutes — short for POC iteration)
- **CNAME**: Points to the Azure service FQDN

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "dns_cname_record" {
  type      = "Microsoft.Network/dnsZones/CNAME@2023-07-01-preview"
  name      = var.record_name
  parent_id = azapi_resource.dns_zone.id

  body = {
    properties = {
      TTL = 300
      CNAMERecord = {
        cname = var.target_fqdn
      }
    }
  }
}

# Alias CNAME pointing to an Azure resource
resource "azapi_resource" "dns_cname_alias" {
  type      = "Microsoft.Network/dnsZones/CNAME@2023-07-01-preview"
  name      = var.record_name
  parent_id = azapi_resource.dns_zone.id

  body = {
    properties = {
      TTL = 300
      targetResource = {
        id = azapi_resource.cdn_endpoint.id
      }
    }
  }
}
```

### RBAC Assignment
```hcl
# DNS Zone Contributor role allows managing records within a zone.
```

## Bicep Patterns

### Basic Resource
```bicep
param recordName string
param targetFqdn string
param ttl int = 300

resource cnameRecord 'Microsoft.Network/dnsZones/CNAME@2023-07-01-preview' = {
  parent: dnsZone
  name: recordName
  properties: {
    TTL: ttl
    CNAMERecord: {
      cname: targetFqdn
    }
  }
}

output fqdn string = '${recordName}.${dnsZone.name}'
```

## Application Code

### Python
Infrastructure — transparent to application code

### C#
Infrastructure — transparent to application code

### Node.js
Infrastructure — transparent to application code

## Common Pitfalls
- **Cannot use at zone apex**: CNAME records are prohibited at the zone root (e.g., `contoso.com`). Use an A alias record for apex domains.
- **Only one CNAME per name**: A CNAME record set can only contain a single record. Multiple CNAMEs for the same name are invalid per DNS RFC.
- **Cannot coexist with other record types**: If a CNAME exists for a name, no other record types (A, MX, TXT) can exist for that same name.
- **Custom domain validation**: Services like App Service require a TXT or CNAME verification record before accepting the custom domain binding. Create the verification record first.
- **Trailing dot**: Azure DNS normalizes FQDNs. You don't need to include the trailing dot in the `cname` value, but it's accepted.

## Production Backlog Items
- Increase TTL to 3600 seconds for reduced DNS query load
- Custom domain SSL certificate automation (App Service managed certificates)
- CNAME flattening considerations if migrating to apex records
- DNS record inventory and drift detection
