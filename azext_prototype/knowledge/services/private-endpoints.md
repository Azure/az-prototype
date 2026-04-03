# Azure Private Endpoints
> Network interface that connects you privately and securely to a service powered by Azure Private Link, routing traffic over the Microsoft backbone network instead of the public internet.

## When to Use

- **Every production Azure deployment** -- private endpoints are the standard pattern for securing access to Azure PaaS services
- **Data exfiltration prevention** -- ensure traffic to Storage, SQL, Key Vault, etc. never traverses the public internet
- **Compliance requirements** -- regulations requiring private-only access to data services
- **Hub-spoke network topologies** -- connect spoke workloads to shared services via private IP addresses
- **Hybrid connectivity** -- on-premises clients accessing Azure services through VPN/ExpressRoute via private IPs

Private endpoints are a **production backlog item** in POC deployments. During POC, public access is typically enabled for simplicity, but the private endpoint pattern should be documented and ready for production hardening.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Deployment | Deferred to production | POC uses public endpoints for simplicity |
| DNS integration | Private DNS zone | Required for name resolution of private endpoints |
| Approval | Auto-approved | Use manual approval for cross-tenant scenarios |
| Network policy | Disabled on subnet | NSG/UDR support for PE subnets is preview |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "private_endpoint" {
  type      = "Microsoft.Network/privateEndpoints@2023-11-01"
  name      = "pe-${var.service_name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      subnet = {
        id = var.subnet_id
      }
      privateLinkServiceConnections = [
        {
          name = "psc-${var.service_name}"
          properties = {
            privateLinkServiceId = var.target_resource_id
            groupIds             = [var.group_id]  # e.g., "blob", "vault", "sites", "sqlServer"
          }
        }
      ]
    }
  }

  tags = var.tags
}

resource "azapi_resource" "pe_dns_zone_group" {
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01"
  name      = "dns-zone-group"
  parent_id = azapi_resource.private_endpoint.id

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "config"
          properties = {
            privateDnsZoneId = var.private_dns_zone_id
          }
        }
      ]
    }
  }
}
```

### Private DNS Zone

```hcl
resource "azapi_resource" "private_dns_zone" {
  type      = "Microsoft.Network/privateDnsZones@2024-06-01"
  name      = var.dns_zone_name  # e.g., "privatelink.blob.core.windows.net"
  location  = "global"
  parent_id = var.resource_group_id

  tags = var.tags
}

# Link DNS zone to VNet for name resolution
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
      registrationEnabled = false
    }
  }

  tags = var.tags
}
```

### Common Group IDs and DNS Zones

```hcl
# Reference table for privateLinkServiceConnections groupIds and DNS zones:
#
# Service                  | groupId          | Private DNS Zone
# -------------------------|------------------|------------------------------------------
# Storage (Blob)           | blob             | privatelink.blob.core.windows.net
# Storage (File)           | file             | privatelink.file.core.windows.net
# Storage (Queue)          | queue            | privatelink.queue.core.windows.net
# Storage (Table)          | table            | privatelink.table.core.windows.net
# Key Vault                | vault            | privatelink.vaultcore.azure.net
# SQL Database             | sqlServer        | privatelink.database.windows.net
# PostgreSQL Flexible      | postgresqlServer | privatelink.postgres.database.azure.com
# MySQL Flexible           | mysqlServer      | privatelink.mysql.database.azure.com
# Cosmos DB                | Sql              | privatelink.documents.azure.com
# App Service / Functions  | sites            | privatelink.azurewebsites.net
# Container Registry       | registry         | privatelink.azurecr.io
# Redis Cache              | redisCache       | privatelink.redis.cache.windows.net
# Event Hubs               | namespace        | privatelink.servicebus.windows.net
# Service Bus              | namespace        | privatelink.servicebus.windows.net
# SignalR                  | signalr          | privatelink.service.signalr.net
# Azure OpenAI             | account          | privatelink.openai.azure.com
# Cognitive Services       | account          | privatelink.cognitiveservices.azure.com
# Azure ML Workspace       | amlworkspace     | privatelink.api.azureml.ms
# Azure Search             | searchService    | privatelink.search.windows.net
```

### RBAC Assignment

```hcl
# Private endpoints do not have their own data-plane RBAC.
# RBAC is controlled on the target resource (e.g., Storage, Key Vault).
# The private endpoint simply provides a private network path.
#
# Network Contributor on the subnet is needed for the deploying identity:
resource "azapi_resource" "subnet_network_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.subnet_id}${var.deployer_principal_id}network-contributor")
  parent_id = var.subnet_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/4d97b98b-1d4f-4787-a291-c67834d212e7"  # Network Contributor
      principalId      = var.deployer_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the private endpoint')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Subnet ID for the private endpoint')
param subnetId string

@description('Target resource ID to connect to')
param targetResourceId string

@description('Private link group ID (e.g., blob, vault, sites)')
param groupId string

@description('Private DNS zone ID for DNS registration')
param privateDnsZoneId string

@description('Tags to apply')
param tags object = {}

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'psc-${name}'
        properties: {
          privateLinkServiceId: targetResourceId
          groupIds: [groupId]
        }
      }
    ]
  }
}

resource dnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: privateEndpoint
  name: 'dns-zone-group'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config'
        properties: {
          privateDnsZoneId: privateDnsZoneId
        }
      }
    ]
  }
}

output id string = privateEndpoint.id
output name string = privateEndpoint.name
output networkInterfaceId string = privateEndpoint.properties.networkInterfaces[0].id
```

### RBAC Assignment

```bicep
// Private endpoints rely on RBAC of the target resource.
// No specific PE RBAC roles needed.
// Ensure the deploying identity has Network Contributor on the subnet.
```

## Common Pitfalls

| Pitfall | Impact | Fix |
|---------|--------|-----|
| Missing private DNS zone | Name resolution fails; clients cannot reach the private endpoint by FQDN | Create the correct `privatelink.*` DNS zone and link it to the VNet |
| DNS zone not linked to VNet | DNS queries from VNet do not resolve private endpoint IPs | Create `virtualNetworkLinks` from the DNS zone to each VNet that needs access |
| Not disabling public access on target | Traffic can still reach the service via public endpoint, bypassing the private endpoint | Set `publicNetworkAccess = "Disabled"` on the target resource |
| Wrong `groupId` | Private endpoint creation fails or connects to wrong sub-resource | Use the correct group ID from the reference table above |
| Subnet too small | Cannot create enough private endpoints | Plan subnet size: each PE uses one IP; /28 gives 11 usable IPs |
| Cross-region DNS resolution | Private DNS zones are global, but VNet links are per-VNet | Link DNS zones to all VNets that need resolution, including hub VNets |
| Forgetting on-premises DNS forwarding | On-premises clients cannot resolve `privatelink.*` FQDNs | Configure DNS forwarder in hub VNet; point on-premises DNS conditional forwarders to it |

## Production Backlog Items

- [ ] Create private endpoints for all PaaS services (Storage, Key Vault, SQL, etc.)
- [ ] Disable public network access on all target resources
- [ ] Centralize private DNS zones in hub subscription/resource group
- [ ] Configure DNS forwarding for hybrid (on-premises) connectivity
- [ ] Review subnet sizing for private endpoint capacity
- [ ] Set up monitoring for private endpoint connection status
- [ ] Document the group ID and DNS zone mapping for the architecture
- [ ] Configure NSG rules on private endpoint subnets (preview feature)
- [ ] Implement Azure Policy to enforce private endpoint usage on supported services
