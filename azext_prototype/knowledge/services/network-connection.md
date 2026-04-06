---
service_namespace: Microsoft.Network/connections
display_name: Virtual Network Gateway Connection
---

# Virtual Network Gateway Connection

> Logical link between a Virtual Network Gateway and another gateway (VPN site-to-site, VNet-to-VNet) or an ExpressRoute circuit, establishing encrypted tunnel or private circuit connectivity.

## When to Use
- **Site-to-site VPN** -- connect on-premises network to Azure VNet over IPsec/IKE tunnel
- **VNet-to-VNet** -- connect two Azure VNets across regions or subscriptions via VPN gateways
- **ExpressRoute connection** -- link a VNet gateway to an ExpressRoute circuit for private connectivity
- Every VPN or ExpressRoute gateway requires at least one connection resource to route traffic

Choose S2S VPN for cost-effective hybrid POC connectivity. Choose ExpressRoute connections for production-grade bandwidth and latency. VNet-to-VNet connections are alternatives to VNet peering when encryption is required.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Connection type | IPsec (S2S) | Most common for hybrid POC |
| IPsec/IKE policy | Default | Azure-managed; custom for compliance |
| Shared key | Strong random | Pre-shared key for IPsec authentication |
| Connection protocol | IKEv2 | Preferred over IKEv1 |
| Enable BGP | false | Static routes for simple POC; BGP for production |
| DPD timeout | 45 seconds | Dead Peer Detection default |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "vpn_connection" {
  type      = "Microsoft.Network/connections@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      connectionType                 = "IPsec"  # or "Vnet2Vnet", "ExpressRoute"
      virtualNetworkGateway1 = {
        id = var.vnet_gateway_id
      }
      localNetworkGateway2 = {
        id = var.local_network_gateway_id  # For S2S only
      }
      sharedKey                      = var.shared_key  # Store in Key Vault
      enableBgp                      = false
      useLocalAzureIpAddress         = false
      usePolicyBasedTrafficSelectors = false
      connectionProtocol             = "IKEv2"
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# Network Contributor on the resource group covers connection management
resource "azapi_resource" "network_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.resource_group_id}-${var.principal_id}-network-contributor")
  parent_id = var.resource_group_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/4d97b98b-1d4f-4787-a291-c67834d212e7"
      principalId      = var.principal_id
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Connection name')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('VNet gateway resource ID')
param vnetGatewayId string

@description('Local network gateway resource ID')
param localNetworkGatewayId string

@secure()
@description('Pre-shared key for IPsec')
param sharedKey string

param tags object = {}

resource vpnConnection 'Microsoft.Network/connections@2024-01-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    connectionType: 'IPsec'
    virtualNetworkGateway1: {
      id: vnetGatewayId
    }
    localNetworkGateway2: {
      id: localNetworkGatewayId
    }
    sharedKey: sharedKey
    enableBgp: false
    connectionProtocol: 'IKEv2'
  }
}

output id string = vpnConnection.id
output connectionStatus string = vpnConnection.properties.connectionStatus
```

## Application Code

### Python
Infrastructure -- transparent to application code. VPN/ExpressRoute connections operate at the network layer; applications connect to Azure resources using their standard endpoints.

### C#
Infrastructure -- transparent to application code. VPN/ExpressRoute connections operate at the network layer; applications connect to Azure resources using their standard endpoints.

### Node.js
Infrastructure -- transparent to application code. VPN/ExpressRoute connections operate at the network layer; applications connect to Azure resources using their standard endpoints.

## Common Pitfalls

1. **Shared key mismatch** -- The pre-shared key must match exactly on both the Azure connection and the on-premises VPN device. Even trailing whitespace causes the tunnel to fail.
2. **Connection type cannot be changed** -- Once created, the connection type (IPsec, Vnet2Vnet, ExpressRoute) is immutable. Delete and recreate to change.
3. **Gateway SKU limits connections** -- Basic VPN gateway supports only 10 S2S tunnels. VpnGw1 supports 30. Check SKU limits before adding connections.
4. **IKE version mismatch** -- If the on-premises device only supports IKEv1, set `connectionProtocol` to `IKEv1`. The default IKEv2 causes negotiation failures with older devices.
5. **Policy-based vs route-based** -- Policy-based traffic selectors (`usePolicyBasedTrafficSelectors: true`) are needed for some on-premises devices but limit you to a single tunnel and no BGP.
6. **Shared key in state file** -- The `sharedKey` is stored in plain text in Terraform state. Use a remote backend with encryption, or reference Key Vault secrets.
7. **BGP requires compatible ASNs** -- When `enableBgp: true`, both the Azure VPN gateway and on-premises device must be configured with non-conflicting ASNs.

## Production Backlog Items

- [ ] Configure custom IPsec/IKE policy for compliance (AES256, SHA256, DH Group 14+)
- [ ] Enable BGP for dynamic route propagation
- [ ] Set up active-active VPN gateway for high availability
- [ ] Implement connection monitoring and alerts via Network Watcher
- [ ] Add redundant connections to secondary on-premises VPN device
- [ ] Store pre-shared key in Key Vault with rotation policy
- [ ] Configure DPD (Dead Peer Detection) timeout appropriate for the on-premises device
- [ ] Plan ExpressRoute as primary with VPN as backup (coexistence)
