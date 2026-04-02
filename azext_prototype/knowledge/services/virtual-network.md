# Azure Virtual Network
> Foundation networking service providing isolated network environments, subnets, network security groups, and private DNS resolution for Azure resources.

## When to Use

- **Every production architecture** -- Virtual Network is the networking foundation for private connectivity
- **Private endpoint connectivity** -- required for private access to PaaS services (Storage, Key Vault, Cosmos DB, SQL, Redis, etc.)
- **VNet integration** -- required for Container Apps, App Service, and Functions to communicate with private resources
- **Network segmentation** -- isolate workload tiers (compute, data, management) via subnets and NSGs
- **Hybrid connectivity** -- connect to on-premises networks via VPN Gateway or ExpressRoute

Virtual Network is a **Stage 1 foundation service** -- it is created first and referenced by all other resources that need network connectivity.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Address space | /16 (e.g., `10.0.0.0/16`) | 65,536 addresses; ample for POC |
| Compute subnet | /24 (e.g., `10.0.1.0/24`) | App Service / Container Apps VNet integration |
| Data subnet | /24 (e.g., `10.0.2.0/24`) | Databases, caches, storage private endpoints |
| Private endpoint subnet | /24 (e.g., `10.0.3.0/24`) | Dedicated subnet for private endpoints |
| Management subnet | /24 (e.g., `10.0.4.0/24`) | Bastion, jump boxes, DevOps agents |
| Default NSG | Deny all inbound | Allow only necessary traffic per subnet |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "virtual_network" {
  type      = "Microsoft.Network/virtualNetworks@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = azapi_resource.resource_group.id

  body = {
    properties = {
      addressSpace = {
        addressPrefixes = [var.address_space]  # e.g., "10.0.0.0/16"
      }
    }
  }

  tags = var.tags
}

# Compute subnet -- for App Service / Container Apps VNet integration
resource "azapi_resource" "compute_subnet" {
  type      = "Microsoft.Network/virtualNetworks/subnets@2024-01-01"
  name      = "snet-compute"
  parent_id = azapi_resource.virtual_network.id

  body = {
    properties = {
      addressPrefix = var.compute_subnet_prefix  # e.g., "10.0.1.0/24"
      delegations = [
        {
          name = "app-service-delegation"
          properties = {
            serviceName = "Microsoft.Web/serverFarms"
          }
        }
      ]
    }
  }
}

# Data subnet -- for private endpoints to data services
resource "azapi_resource" "data_subnet" {
  type      = "Microsoft.Network/virtualNetworks/subnets@2024-01-01"
  name      = "snet-data"
  parent_id = azapi_resource.virtual_network.id

  body = {
    properties = {
      addressPrefix = var.data_subnet_prefix  # e.g., "10.0.2.0/24"
    }
  }

  depends_on = [azapi_resource.compute_subnet]
}

# Private endpoint subnet -- dedicated for all private endpoints
resource "azapi_resource" "private_endpoints_subnet" {
  type      = "Microsoft.Network/virtualNetworks/subnets@2024-01-01"
  name      = "snet-private-endpoints"
  parent_id = azapi_resource.virtual_network.id

  body = {
    properties = {
      addressPrefix = var.pe_subnet_prefix  # e.g., "10.0.3.0/24"
    }
  }

  depends_on = [azapi_resource.data_subnet]
}
```

### Network Security Groups

```hcl
resource "azapi_resource" "nsg_compute" {
  type      = "Microsoft.Network/networkSecurityGroups@2024-01-01"
  name      = "nsg-compute"
  location  = var.location
  parent_id = azapi_resource.resource_group.id

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
        {
          name = "AllowHTTPS"
          properties = {
            priority                 = 100
            direction                = "Inbound"
            access                   = "Allow"
            protocol                 = "Tcp"
            sourcePortRange          = "*"
            destinationPortRange     = "443"
            sourceAddressPrefix      = "Internet"
            destinationAddressPrefix = "*"
          }
        }
      ]
    }
  }

  tags = var.tags
}

# Associate NSG with compute subnet by updating the subnet
resource "azapi_update_resource" "compute_subnet_nsg" {
  type        = "Microsoft.Network/virtualNetworks/subnets@2024-01-01"
  resource_id = azapi_resource.compute_subnet.id

  body = {
    properties = {
      addressPrefix = var.compute_subnet_prefix
      networkSecurityGroup = {
        id = azapi_resource.nsg_compute.id
      }
    }
  }
}

resource "azapi_resource" "nsg_data" {
  type      = "Microsoft.Network/networkSecurityGroups@2024-01-01"
  name      = "nsg-data"
  location  = var.location
  parent_id = azapi_resource.resource_group.id

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
        {
          name = "AllowVNetInbound"
          properties = {
            priority                 = 100
            direction                = "Inbound"
            access                   = "Allow"
            protocol                 = "*"
            sourcePortRange          = "*"
            destinationPortRange     = "*"
            sourceAddressPrefix      = "VirtualNetwork"
            destinationAddressPrefix = "VirtualNetwork"
          }
        }
      ]
    }
  }

  tags = var.tags
}

# Associate NSG with data subnet by updating the subnet
resource "azapi_update_resource" "data_subnet_nsg" {
  type        = "Microsoft.Network/virtualNetworks/subnets@2024-01-01"
  resource_id = azapi_resource.data_subnet.id

  body = {
    properties = {
      addressPrefix = var.data_subnet_prefix
      networkSecurityGroup = {
        id = azapi_resource.nsg_data.id
      }
    }
  }
}
```

### Private DNS Zones

Create one private DNS zone per service type and link to the VNet:

```hcl
locals {
  # Map of service to private DNS zone name
  private_dns_zones = {
    blob         = "privatelink.blob.core.windows.net"
    key_vault    = "privatelink.vaultcore.azure.net"
    cosmos_db    = "privatelink.documents.azure.com"
    sql          = "privatelink.database.windows.net"
    redis        = "privatelink.redis.cache.windows.net"
    service_bus  = "privatelink.servicebus.windows.net"
    event_grid   = "privatelink.eventgrid.azure.net"
    acr          = "privatelink.azurecr.io"
    openai       = "privatelink.openai.azure.com"
    search       = "privatelink.search.windows.net"
    web_apps     = "privatelink.azurewebsites.net"
    api_mgmt     = "privatelink.azure-api.net"
  }
}

resource "azapi_resource" "private_dns_zones" {
  for_each = var.private_dns_zones  # Pass subset of the map above based on services used

  type      = "Microsoft.Network/privateDnsZones@2020-06-01"
  name      = each.value
  location  = "global"
  parent_id = azapi_resource.resource_group.id

  tags = var.tags
}

resource "azapi_resource" "private_dns_zone_links" {
  for_each = azapi_resource.private_dns_zones

  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01"
  name      = "link-${each.key}"
  location  = "global"
  parent_id = each.value.id

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
# Network Contributor -- manage networks but not access
resource "azapi_resource" "network_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("sha1", "${azapi_resource.virtual_network.id}-${var.managed_identity_principal_id}-4d97b98b-1d4f-4787-a291-c67834d212e7")
  parent_id = azapi_resource.virtual_network.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/4d97b98b-1d4f-4787-a291-c67834d212e7"  # Network Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

Virtual Network does not use private endpoints -- it **provides** the subnet infrastructure that other services use for their private endpoints.

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the virtual network')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Address space for the VNet')
param addressSpace string = '10.0.0.0/16'

@description('Tags to apply')
param tags object = {}

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        addressSpace
      ]
    }
    subnets: [
      {
        name: 'snet-compute'
        properties: {
          addressPrefix: '10.0.1.0/24'
          delegations: [
            {
              name: 'app-service-delegation'
              properties: {
                serviceName: 'Microsoft.Web/serverFarms'
              }
            }
          ]
        }
      }
      {
        name: 'snet-data'
        properties: {
          addressPrefix: '10.0.2.0/24'
        }
      }
      {
        name: 'snet-private-endpoints'
        properties: {
          addressPrefix: '10.0.3.0/24'
        }
      }
    ]
  }
}

output id string = vnet.id
output name string = vnet.name
output computeSubnetId string = vnet.properties.subnets[0].id
output dataSubnetId string = vnet.properties.subnets[1].id
output peSubnetId string = vnet.properties.subnets[2].id
```

### RBAC Assignment

```bicep
@description('Principal ID for network management')
param principalId string

var networkContributorRoleId = '4d97b98b-1d4f-4787-a291-c67834d212e7'

resource networkRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vnet.id, principalId, networkContributorRoleId)
  scope: vnet
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', networkContributorRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Application Code

No application code patterns -- Azure Virtual Network is a pure infrastructure service. Applications do not interact with VNet directly; they benefit from it transparently via private endpoints and VNet integration.

## Common Pitfalls

1. **Address space conflicts** -- When planning for VNet peering or hybrid connectivity, ensure address spaces do not overlap with on-premises networks or other VNets.
2. **Subnet sizing** -- Each subnet reserves 5 addresses (Azure platform). A /24 gives 251 usable addresses. Private endpoint subnets can fill up in large deployments.
3. **Subnet delegation conflicts** -- A subnet can only have one delegation type. Do not mix App Service delegation with Container Apps delegation in the same subnet.
4. **Forgetting private DNS zones** -- Private endpoints require DNS resolution. Without a linked private DNS zone, applications cannot resolve the private endpoint hostname.
5. **NSG on private endpoint subnets** -- NSGs on subnets with private endpoints require special handling. Network policies for private endpoints must be enabled: `privateEndpointNetworkPolicies = "Enabled"` in the subnet properties (Terraform azapi) or `privateEndpointNetworkPolicies: 'Enabled'` (Bicep).
6. **Not creating separate subnets** -- Putting all resources in one subnet limits NSG granularity and causes delegation conflicts. Always use dedicated subnets per tier.
7. **DNS zone link registration** -- Set `registration_enabled = false` for private DNS zone VNet links unless you specifically need auto-registration of VM DNS records.

## Production Backlog Items

- [ ] Implement hub-spoke network topology for multi-VNet architectures
- [ ] Deploy Azure Firewall for centralized egress filtering and logging
- [ ] Enable DDoS Protection Standard on the VNet
- [ ] Deploy Network Watcher for diagnostics and monitoring
- [ ] Enable NSG flow logs for traffic analysis and auditing
- [ ] Configure VPN Gateway or ExpressRoute for hybrid connectivity
- [ ] Implement Azure Bastion for secure management access (replaces jump boxes)
- [ ] Review and tighten NSG rules based on actual traffic patterns
- [ ] Configure service endpoints as an alternative to private endpoints where appropriate
- [ ] Implement IP address management (IPAM) for large-scale deployments
