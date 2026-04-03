# Azure DNS Zones
> Managed DNS hosting service for both public domains and private name resolution within Azure Virtual Networks, providing high availability and fast DNS queries using Azure's global anycast network.

## When to Use

- **Private DNS zones** -- name resolution for Azure resources within VNets (e.g., `privatelink.blob.core.windows.net` for private endpoints)
- **Public DNS zones** -- host public domain DNS records (A, AAAA, CNAME, MX, TXT, SRV, etc.)
- **Private endpoint DNS** -- every private endpoint requires a corresponding `privatelink.*` private DNS zone for FQDN resolution
- **Custom domain names** -- map custom domains to Azure services (App Service, Front Door, etc.)
- **Split-horizon DNS** -- different resolution for the same domain from inside vs. outside the VNet

Private DNS zones are the most common use in POC architectures, primarily to support private endpoint name resolution. Public DNS zones are used when the POC needs a custom domain.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Zone type | Private | For private endpoint DNS resolution |
| Location | Global | DNS zones are always global resources |
| Registration enabled | false | Auto-registration is for VM DNS records; not needed for private endpoints |
| VNet links | One per VNet | Link to all VNets needing resolution |

## Terraform Patterns

### Private DNS Zone

```hcl
resource "azapi_resource" "private_dns_zone" {
  type      = "Microsoft.Network/privateDnsZones@2024-06-01"
  name      = var.zone_name  # e.g., "privatelink.blob.core.windows.net"
  location  = "global"       # Private DNS zones are always global
  parent_id = var.resource_group_id

  tags = var.tags
}
```

### VNet Link

```hcl
resource "azapi_resource" "dns_vnet_link" {
  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01"
  name      = "link-${var.vnet_name}"
  location  = "global"
  parent_id = azapi_resource.private_dns_zone.id

  body = {
    properties = {
      virtualNetwork = {
        id = var.vnet_id
      }
      registrationEnabled = false  # true only for VM auto-registration scenarios
    }
  }

  tags = var.tags
}
```

### Public DNS Zone

```hcl
resource "azapi_resource" "public_dns_zone" {
  type      = "Microsoft.Network/dnsZones@2023-07-01-preview"
  name      = var.domain_name  # e.g., "contoso.com"
  location  = "global"
  parent_id = var.resource_group_id

  tags = var.tags

  response_export_values = ["properties.nameServers"]
}

# A record
resource "azapi_resource" "a_record" {
  type      = "Microsoft.Network/dnsZones/A@2023-07-01-preview"
  name      = var.record_name  # e.g., "www"
  parent_id = azapi_resource.public_dns_zone.id

  body = {
    properties = {
      TTL = 300
      ARecords = [
        {
          ipv4Address = var.target_ip
        }
      ]
    }
  }
}

# CNAME record
resource "azapi_resource" "cname_record" {
  type      = "Microsoft.Network/dnsZones/CNAME@2023-07-01-preview"
  name      = var.cname_name  # e.g., "api"
  parent_id = azapi_resource.public_dns_zone.id

  body = {
    properties = {
      TTL = 300
      CNAMERecord = {
        cname = var.target_fqdn  # e.g., "myapp.azurewebsites.net"
      }
    }
  }
}
```

### Private DNS Zone Record (Manual)

```hcl
# Usually records are auto-created by private endpoint DNS zone groups.
# Manual A records are needed for custom private DNS scenarios.
resource "azapi_resource" "private_a_record" {
  type      = "Microsoft.Network/privateDnsZones/A@2024-06-01"
  name      = var.record_name
  parent_id = azapi_resource.private_dns_zone.id

  body = {
    properties = {
      ttl = 300
      aRecords = [
        {
          ipv4Address = var.private_ip
        }
      ]
    }
  }
}
```

### RBAC Assignment

```hcl
# Private DNS Zone Contributor -- manage records in private DNS zones
resource "azapi_resource" "dns_zone_contributor_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.private_dns_zone.id}${var.managed_identity_principal_id}dns-contributor")
  parent_id = azapi_resource.private_dns_zone.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/b12aa53e-6015-4669-85d0-8515ebb3ae7f"  # Private DNS Zone Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# DNS Zone Contributor (public zones)
resource "azapi_resource" "public_dns_contributor_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.public_dns_zone.id}${var.managed_identity_principal_id}public-dns-contributor")
  parent_id = azapi_resource.public_dns_zone.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/befefa01-2a29-4197-83a8-272ff33ce314"  # DNS Zone Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Private DNS Zone with VNet Link

```bicep
@description('Private DNS zone name (e.g., privatelink.blob.core.windows.net)')
param zoneName string

@description('VNet resource ID to link')
param vnetId string

@description('VNet name for the link resource name')
param vnetName string

@description('Tags to apply')
param tags object = {}

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: zoneName
  location: 'global'
  tags: tags
}

resource vnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: privateDnsZone
  name: 'link-${vnetName}'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnetId
    }
    registrationEnabled: false
  }
  tags: tags
}

output zoneId string = privateDnsZone.id
output zoneName string = privateDnsZone.name
```

### Public DNS Zone

```bicep
@description('Domain name for the public DNS zone')
param domainName string

@description('Tags to apply')
param tags object = {}

resource dnsZone 'Microsoft.Network/dnsZones@2023-07-01-preview' = {
  name: domainName
  location: 'global'
  tags: tags
}

output id string = dnsZone.id
output nameServers array = dnsZone.properties.nameServers
```

### RBAC Assignment

```bicep
@description('Principal ID of the managed identity')
param principalId string

// Private DNS Zone Contributor
resource dnsZoneContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(privateDnsZone.id, principalId, 'b12aa53e-6015-4669-85d0-8515ebb3ae7f')
  scope: privateDnsZone
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b12aa53e-6015-4669-85d0-8515ebb3ae7f')  // Private DNS Zone Contributor
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Private DNS Zone Names

| Service | Private DNS Zone |
|---------|-----------------|
| Storage (Blob) | `privatelink.blob.core.windows.net` |
| Storage (File) | `privatelink.file.core.windows.net` |
| Storage (Queue) | `privatelink.queue.core.windows.net` |
| Storage (Table) | `privatelink.table.core.windows.net` |
| Key Vault | `privatelink.vaultcore.azure.net` |
| SQL Database | `privatelink.database.windows.net` |
| PostgreSQL Flexible | `privatelink.postgres.database.azure.com` |
| MySQL Flexible | `privatelink.mysql.database.azure.com` |
| Cosmos DB | `privatelink.documents.azure.com` |
| App Service / Functions | `privatelink.azurewebsites.net` |
| Container Registry | `privatelink.azurecr.io` |
| Redis Cache | `privatelink.redis.cache.windows.net` |
| Event Hubs / Service Bus | `privatelink.servicebus.windows.net` |
| SignalR | `privatelink.service.signalr.net` |
| Azure OpenAI | `privatelink.openai.azure.com` |
| Cognitive Services | `privatelink.cognitiveservices.azure.com` |
| Azure ML | `privatelink.api.azureml.ms` |
| Azure Search | `privatelink.search.windows.net` |

## Common Pitfalls

| Pitfall | Impact | Fix |
|---------|--------|-----|
| Missing VNet link | DNS queries from the VNet do not resolve private endpoint records | Create `virtualNetworkLinks` for every VNet that needs resolution |
| `registrationEnabled = true` on PE zone | Auto-registers VM records into the zone, polluting private endpoint DNS | Set `registrationEnabled = false` for `privatelink.*` zones |
| Duplicate DNS zones | Multiple zones for the same name cause resolution conflicts | Centralize private DNS zones in a shared resource group; link to all VNets |
| Wrong zone name | Private endpoint DNS records not resolved | Use exact `privatelink.*` zone names from the reference table |
| Public DNS zone without NS delegation | External clients cannot resolve records | Update domain registrar NS records to point to Azure DNS name servers |
| TTL too high during migration | DNS changes take too long to propagate | Use low TTL (60-300s) during migration; increase after stabilization |
| Not linking hub VNet | Spoke VNets using hub DNS forwarder cannot resolve private endpoints | Link DNS zones to both hub and spoke VNets |

## Production Backlog Items

- [ ] Centralize private DNS zones in a shared networking resource group or subscription
- [ ] Link DNS zones to all VNets (hub and spokes) that need resolution
- [ ] Configure on-premises DNS forwarding for hybrid scenarios
- [ ] Set up monitoring alerts for DNS query volume and resolution failures
- [ ] Implement Azure Policy to enforce private DNS zone creation with private endpoints
- [ ] Review and consolidate duplicate DNS zones across resource groups
- [ ] Document DNS architecture and zone-to-service mapping
- [ ] Configure DNS zone diagnostic logging
