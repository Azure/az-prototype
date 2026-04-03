# Azure VPN Gateway
> Managed virtual network gateway providing encrypted site-to-site, point-to-site, and VNet-to-VNet VPN connectivity over IPsec/IKE tunnels.

## When to Use

- **Site-to-site (S2S) VPN** -- connect on-premises networks to Azure VNets over encrypted IPsec tunnels
- **Point-to-site (P2S) VPN** -- remote users connect to Azure VNet from individual devices
- **VNet-to-VNet** -- connect Azure VNets across regions or subscriptions via VPN tunnel
- **Hybrid connectivity (cost-sensitive)** -- when ExpressRoute is too expensive for POC/dev environments
- **Backup path for ExpressRoute** -- secondary connectivity path for ExpressRoute failover
- NOT suitable for: high-bandwidth/low-latency requirements (use ExpressRoute), internet-facing load balancing (use LB/AppGW), or pure cloud architectures without on-premises

Choose VPN Gateway for encrypted hybrid connectivity. Choose ExpressRoute for private, dedicated, high-bandwidth connections.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | VpnGw1 | Lowest production-ready SKU; 650 Mbps, 250 S2S tunnels |
| Gateway type | Vpn | ExpressRoute for ER gateways |
| VPN type | RouteBased | PolicyBased only for legacy compatibility |
| Subnet name | GatewaySubnet | Must be exactly this name (Azure requirement) |
| Subnet size | /27 minimum | /27 supports coexistence with ExpressRoute |
| Generation | Generation2 | Better performance than Generation1 |
| Active-active | Disabled (POC) | Enable for production HA |
| BGP | Disabled (POC) | Enable for dynamic routing with on-premises |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "gateway_subnet" {
  type      = "Microsoft.Network/virtualNetworks/subnets@2024-01-01"
  name      = "GatewaySubnet"  # Must be exactly this name
  parent_id = var.virtual_network_id

  body = {
    properties = {
      addressPrefix = var.gateway_subnet_prefix  # e.g., "10.0.254.0/27"
    }
  }
}

resource "azapi_resource" "vpn_pip" {
  type      = "Microsoft.Network/publicIPAddresses@2024-01-01"
  name      = "pip-${var.name}"
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

resource "azapi_resource" "vpn_gateway" {
  type      = "Microsoft.Network/virtualNetworkGateways@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      gatewayType = "Vpn"
      vpnType     = "RouteBased"
      sku = {
        name = "VpnGw1"
        tier = "VpnGw1"
      }
      vpnGatewayGeneration = "Generation2"
      enableBgp            = false
      activeActive         = false
      ipConfigurations = [
        {
          name = "vpn-ip-config"
          properties = {
            publicIPAddress = {
              id = azapi_resource.vpn_pip.id
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

  response_export_values = ["properties.bgpSettings"]
}
```

### Site-to-Site Connection

```hcl
resource "azapi_resource" "local_network_gateway" {
  type      = "Microsoft.Network/localNetworkGateways@2024-01-01"
  name      = "lgw-${var.onprem_site_name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      gatewayIpAddress = var.onprem_public_ip  # On-premises VPN device public IP
      localNetworkAddressSpace = {
        addressPrefixes = var.onprem_address_prefixes  # e.g., ["192.168.0.0/16"]
      }
    }
  }

  tags = var.tags
}

resource "azapi_resource" "s2s_connection" {
  type      = "Microsoft.Network/connections@2024-01-01"
  name      = "conn-${var.onprem_site_name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      connectionType = "IPsec"
      virtualNetworkGateway1 = {
        id = azapi_resource.vpn_gateway.id
      }
      localNetworkGateway2 = {
        id = azapi_resource.local_network_gateway.id
      }
      sharedKey                  = var.shared_key  # Pre-shared key for IPsec
      connectionProtocol         = "IKEv2"
      enableBgp                  = false
      usePolicyBasedTrafficSelectors = false
    }
  }

  tags = var.tags
}
```

### Point-to-Site Configuration

```hcl
resource "azapi_resource" "vpn_gateway_p2s" {
  type      = "Microsoft.Network/virtualNetworkGateways@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      gatewayType = "Vpn"
      vpnType     = "RouteBased"
      sku = {
        name = "VpnGw1"
        tier = "VpnGw1"
      }
      vpnGatewayGeneration = "Generation2"
      ipConfigurations = [
        {
          name = "vpn-ip-config"
          properties = {
            publicIPAddress = {
              id = azapi_resource.vpn_pip.id
            }
            subnet = {
              id = azapi_resource.gateway_subnet.id
            }
            privateIPAllocationMethod = "Dynamic"
          }
        }
      ]
      vpnClientConfiguration = {
        vpnClientAddressPool = {
          addressPrefixes = ["172.16.0.0/24"]  # P2S client address space
        }
        vpnClientProtocols   = ["OpenVPN"]
        vpnAuthenticationTypes = ["AAD"]
        aadTenant            = "https://login.microsoftonline.com/${var.tenant_id}"
        aadAudience          = "41b23e61-6c1e-4545-b367-cd054e0ed4b4"  # Azure VPN Enterprise App
        aadIssuer            = "https://sts.windows.net/${var.tenant_id}/"
      }
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# Network Contributor for gateway management
resource "azapi_resource" "gw_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.vpn_gateway.id}-${var.admin_principal_id}-network-contributor")
  parent_id = azapi_resource.vpn_gateway.id

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

VPN Gateway does not use private endpoints -- it is deployed into the `GatewaySubnet` and provides the connectivity plane itself.

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the VPN Gateway')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Virtual network ID')
param virtualNetworkId string

@description('Gateway subnet prefix (min /27)')
param gatewaySubnetPrefix string = '10.0.254.0/27'

@description('Tags to apply')
param tags object = {}

resource gatewaySubnet 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' = {
  name: '${split(virtualNetworkId, '/')[8]}/GatewaySubnet'
  properties: {
    addressPrefix: gatewaySubnetPrefix
  }
}

resource vpnPip 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
  name: 'pip-${name}'
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
  tags: tags
}

resource vpnGateway 'Microsoft.Network/virtualNetworkGateways@2024-01-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    gatewayType: 'Vpn'
    vpnType: 'RouteBased'
    sku: {
      name: 'VpnGw1'
      tier: 'VpnGw1'
    }
    vpnGatewayGeneration: 'Generation2'
    enableBgp: false
    activeActive: false
    ipConfigurations: [
      {
        name: 'vpn-ip-config'
        properties: {
          publicIPAddress: {
            id: vpnPip.id
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

output id string = vpnGateway.id
output publicIpAddress string = vpnPip.properties.ipAddress
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Wrong subnet name | Deployment fails | Subnet must be named exactly `GatewaySubnet` |
| Subnet too small | Cannot deploy gateway; no room for active-active | Use /27 minimum; /27 allows coexistence with ExpressRoute |
| Long provisioning time | Gateway takes 30-45 minutes to deploy | Plan for long provisioning; do not cancel and retry |
| PolicyBased VPN type | Limited to 1 S2S tunnel, no P2S, no VNet-to-VNet | Use RouteBased unless legacy device requires PolicyBased |
| Shared key in plain text | Pre-shared key exposed in state files | Store shared key in Key Vault; reference via variable |
| Basic SKU | No BGP, no HA, limited to 10 S2S tunnels | Use VpnGw1 or higher for production-ready capabilities |
| Forgetting on-premises device config | Tunnel stays disconnected | Export VPN config from portal and apply to on-premises device |
| Active-active not enabled | Single point of failure | Enable active-active for production with two public IPs |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Active-active mode | P1 | Enable active-active with two public IPs for high availability |
| BGP enablement | P2 | Enable BGP for dynamic route exchange with on-premises |
| Zone-redundant SKU | P1 | Use VpnGw1AZ or higher for availability zone resilience |
| IPsec/IKE custom policy | P2 | Configure custom IPsec parameters for compliance requirements |
| Connection monitoring | P2 | Enable connection monitor and diagnostic logging to Log Analytics |
| Multi-site S2S | P3 | Configure connections to multiple on-premises sites |
| ExpressRoute coexistence | P2 | Add ExpressRoute gateway in same GatewaySubnet for redundant hybrid connectivity |
| NAT rules | P3 | Configure VPN NAT rules if address spaces overlap with on-premises |
| Forced tunneling | P2 | Route all internet-bound traffic through on-premises for inspection |
| P2S Azure AD authentication | P2 | Configure point-to-site with Azure AD authentication for remote users |
