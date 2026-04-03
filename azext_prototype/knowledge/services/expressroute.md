# Azure ExpressRoute
> Private, dedicated, high-bandwidth connection between on-premises networks and Azure, bypassing the public internet for consistent latency, higher throughput, and enhanced security.

## When to Use

- **High-bandwidth hybrid connectivity** -- 50 Mbps to 100 Gbps dedicated circuits
- **Latency-sensitive workloads** -- predictable latency without internet variability
- **Regulatory compliance** -- data never traverses the public internet
- **Large data transfers** -- bulk data migration, backup/replication, big data workloads
- **Microsoft 365 connectivity** -- direct peering to Microsoft services (with Microsoft peering)
- NOT suitable for: cost-constrained POC (use VPN Gateway), internet-only workloads, or single-developer remote access (use P2S VPN)

Choose ExpressRoute for production hybrid connectivity. Choose VPN Gateway for POC/dev scenarios or as an ExpressRoute backup.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Standard | Premium for cross-region, >4000 routes |
| Bandwidth | 50 Mbps | Minimum; sufficient for POC validation |
| Peering type | Azure Private | Direct access to VNet resources |
| Provider | Varies | Must contract with connectivity provider |
| Gateway SKU | ErGw1AZ | Zone-redundant; matches ExpressRoute circuit |
| Subnet name | GatewaySubnet | Shared with VPN Gateway if coexisting |
| Subnet size | /27 minimum | /27 supports coexistence with VPN Gateway |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "expressroute_circuit" {
  type      = "Microsoft.Network/expressRouteCircuits@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name   = "Standard_MeteredData"  # or "Premium_MeteredData", "Standard_UnlimitedData"
      tier   = "Standard"
      family = "MeteredData"  # or "UnlimitedData"
    }
    properties = {
      serviceProviderProperties = {
        serviceProviderName = var.provider_name       # e.g., "Equinix"
        peeringLocation     = var.peering_location    # e.g., "Washington DC"
        bandwidthInMbps     = var.bandwidth            # e.g., 50
      }
      allowClassicOperations = false
    }
  }

  tags = var.tags

  response_export_values = ["properties.serviceKey", "properties.serviceProviderProvisioningState"]
}
```

### ExpressRoute Gateway

```hcl
resource "azapi_resource" "gateway_subnet" {
  type      = "Microsoft.Network/virtualNetworks/subnets@2024-01-01"
  name      = "GatewaySubnet"
  parent_id = var.virtual_network_id

  body = {
    properties = {
      addressPrefix = var.gateway_subnet_prefix  # e.g., "10.0.254.0/27"
    }
  }
}

resource "azapi_resource" "er_pip" {
  type      = "Microsoft.Network/publicIPAddresses@2024-01-01"
  name      = "pip-ergw-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard"
    }
    properties = {
      publicIPAllocationMethod = "Static"
    }
  }

  tags = var.tags
}

resource "azapi_resource" "er_gateway" {
  type      = "Microsoft.Network/virtualNetworkGateways@2024-01-01"
  name      = "ergw-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      gatewayType = "ExpressRoute"
      sku = {
        name = "ErGw1AZ"
        tier = "ErGw1AZ"
      }
      ipConfigurations = [
        {
          name = "er-ip-config"
          properties = {
            publicIPAddress = {
              id = azapi_resource.er_pip.id
            }
            subnet = {
              id = azapi_resource.gateway_subnet.id
            }
            privateIPAllocationMethod = "Dynamic"
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

### ExpressRoute Connection

```hcl
resource "azapi_resource" "er_connection" {
  type      = "Microsoft.Network/connections@2024-01-01"
  name      = "conn-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      connectionType = "ExpressRoute"
      virtualNetworkGateway1 = {
        id = azapi_resource.er_gateway.id
      }
      peer = {
        id = azapi_resource.expressroute_circuit.id
      }
      authorizationKey = var.authorization_key  # null if same subscription
    }
  }

  tags = var.tags
}
```

### Private Peering

```hcl
resource "azapi_resource" "private_peering" {
  type      = "Microsoft.Network/expressRouteCircuits/peerings@2024-01-01"
  name      = "AzurePrivatePeering"
  parent_id = azapi_resource.expressroute_circuit.id

  body = {
    properties = {
      peeringType        = "AzurePrivatePeering"
      peerASN            = var.peer_asn           # On-premises BGP ASN
      primaryPeerAddressPrefix   = var.primary_peer_prefix    # e.g., "192.168.1.0/30"
      secondaryPeerAddressPrefix = var.secondary_peer_prefix  # e.g., "192.168.2.0/30"
      vlanId             = var.vlan_id             # e.g., 100
    }
  }
}
```

### RBAC Assignment

```hcl
# Network Contributor for ExpressRoute management
resource "azapi_resource" "er_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.expressroute_circuit.id}-${var.admin_principal_id}-network-contributor")
  parent_id = azapi_resource.expressroute_circuit.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/4d97b98b-1d4f-4787-a291-c67834d212e7"  # Network Contributor
      principalId      = var.admin_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

ExpressRoute does not use private endpoints -- it provides the private connectivity layer that enables access to resources with private endpoints from on-premises networks.

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the ExpressRoute circuit')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Connectivity provider name')
param providerName string

@description('Peering location')
param peeringLocation string

@description('Bandwidth in Mbps')
param bandwidthInMbps int = 50

@description('Tags to apply')
param tags object = {}

resource expressRouteCircuit 'Microsoft.Network/expressRouteCircuits@2024-01-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard_MeteredData'
    tier: 'Standard'
    family: 'MeteredData'
  }
  properties: {
    serviceProviderProperties: {
      serviceProviderName: providerName
      peeringLocation: peeringLocation
      bandwidthInMbps: bandwidthInMbps
    }
    allowClassicOperations: false
  }
}

output id string = expressRouteCircuit.id
output serviceKey string = expressRouteCircuit.properties.serviceKey
```

### ExpressRoute Gateway

```bicep
@description('Virtual network ID')
param virtualNetworkId string

@description('Gateway subnet prefix')
param gatewaySubnetPrefix string = '10.0.254.0/27'

resource gatewaySubnet 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' = {
  name: '${split(virtualNetworkId, '/')[8]}/GatewaySubnet'
  properties: {
    addressPrefix: gatewaySubnetPrefix
  }
}

resource erPip 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
  name: 'pip-ergw-${name}'
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
  tags: tags
}

resource erGateway 'Microsoft.Network/virtualNetworkGateways@2024-01-01' = {
  name: 'ergw-${name}'
  location: location
  tags: tags
  properties: {
    gatewayType: 'ExpressRoute'
    sku: {
      name: 'ErGw1AZ'
      tier: 'ErGw1AZ'
    }
    ipConfigurations: [
      {
        name: 'er-ip-config'
        properties: {
          publicIPAddress: {
            id: erPip.id
          }
          subnet: {
            id: gatewaySubnet.id
          }
          privateIPAllocationMethod: 'Dynamic'
        }
      }
    ]
  }
}

output gatewayId string = erGateway.id
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Provisioning delay | Circuit requires provider-side provisioning (days to weeks) | Initiate provider provisioning early; circuit is not usable until provider completes |
| Wrong peering location | Cannot connect to provider | Verify provider supports the chosen peering location |
| GatewaySubnet too small | Cannot deploy ER gateway; no room for coexistence | Use /27 minimum to support ER + VPN coexistence |
| Forgetting private peering | No VNet connectivity even with circuit provisioned | Configure Azure Private Peering with correct BGP parameters |
| Service key exposure | Anyone with the key can connect to your circuit | Treat service key as a secret; use authorization keys for cross-subscription |
| Standard SKU route limits | Maximum 4,000 routes per peering | Use Premium SKU if on-premises advertises >4,000 routes |
| Gateway deployment time | ER gateway takes 30-45 minutes to deploy | Plan for long provisioning; do not cancel |
| No redundancy | Single circuit is a single point of failure | Deploy two circuits in different peering locations for production |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Redundant circuits | P1 | Deploy second circuit via different provider/location for HA |
| Premium SKU | P2 | Upgrade for global reach, >4,000 routes, and cross-region VNet linking |
| FastPath | P2 | Enable FastPath on ErGw3AZ for reduced latency to private endpoints |
| ExpressRoute Global Reach | P3 | Enable branch-to-branch connectivity across circuits |
| BFD enablement | P2 | Enable Bidirectional Forwarding Detection for faster failover |
| Connection monitoring | P1 | Enable ExpressRoute connection monitor and diagnostic logging |
| VPN backup | P2 | Configure VPN Gateway as backup path with automatic failover |
| Microsoft peering | P3 | Add Microsoft peering for Microsoft 365 and Azure PaaS public IPs |
| Route filters | P2 | Configure route filters to control which Azure regions/services are advertised |
| Bandwidth upgrade | P3 | Increase circuit bandwidth based on observed utilization |
