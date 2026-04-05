---
service_namespace: Microsoft.Network/localNetworkGateways
display_name: Local Network Gateway
---

# Local Network Gateway

> Representation of an on-premises VPN device in Azure, defining the public IP address and address ranges of the remote network for site-to-site VPN connectivity.

## When to Use
- **Site-to-site VPN** -- every S2S VPN connection requires a local network gateway to represent the on-premises endpoint
- **Multiple on-premises sites** -- create one local network gateway per remote site/branch
- **BGP-enabled VPN** -- specify the on-premises BGP peer address and ASN
- Required companion to `Microsoft.Network/connections` of type `IPsec`

A local network gateway is purely a metadata resource describing the remote network. It does not provision any infrastructure itself.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Gateway IP | On-premises public IP | Must be publicly routable |
| Address prefixes | On-premises CIDR(s) | e.g., 10.1.0.0/16, 192.168.0.0/24 |
| BGP | Disabled | Enable for dynamic routing in production |
| FQDN | Not used | Alternative to IP for dynamic-IP devices |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "local_gw" {
  type      = "Microsoft.Network/localNetworkGateways@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      gatewayIpAddress = var.on_premises_public_ip  # e.g., "203.0.113.1"
      localNetworkAddressSpace = {
        addressPrefixes = var.on_premises_address_prefixes  # e.g., ["10.1.0.0/16"]
      }
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# Network Contributor on the resource group covers local network gateway management.
# Role ID: 4d97b98b-1d4f-4787-a291-c67834d212e7
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the local network gateway')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Public IP of the on-premises VPN device')
param gatewayIpAddress string

@description('On-premises address prefixes')
param addressPrefixes array

param tags object = {}

resource localGw 'Microsoft.Network/localNetworkGateways@2024-01-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    gatewayIpAddress: gatewayIpAddress
    localNetworkAddressSpace: {
      addressPrefixes: addressPrefixes
    }
  }
}

output id string = localGw.id
```

## Application Code

### Python
Infrastructure -- transparent to application code. Local network gateways define network routing metadata; applications are unaware of their existence.

### C#
Infrastructure -- transparent to application code. Local network gateways define network routing metadata; applications are unaware of their existence.

### Node.js
Infrastructure -- transparent to application code. Local network gateways define network routing metadata; applications are unaware of their existence.

## Common Pitfalls

1. **Address prefix overlap with Azure VNet** -- On-premises address prefixes must not overlap with any Azure VNet address space. Overlapping ranges cause asymmetric routing and connection failures.
2. **Gateway IP must be publicly routable** -- Private IPs (10.x, 172.16.x, 192.168.x) are not valid for `gatewayIpAddress`. If the on-premises device is behind NAT, use the NAT public IP.
3. **Updating address prefixes disconnects the tunnel** -- Changing `localNetworkAddressSpace` briefly disrupts the VPN connection while routes reconverge. Plan maintenance windows.
4. **BGP peer address not in address space** -- When using BGP, the `bgpPeeringAddress` must be routable from Azure but should not be in the `localNetworkAddressSpace` prefixes (it is learned via BGP, not static routes).
5. **FQDN vs IP mutual exclusivity** -- You can set either `gatewayIpAddress` or `fqdn`, not both. FQDN is useful when the on-premises public IP is dynamic (resolved via DNS).
6. **Deleting while connection exists** -- A local network gateway cannot be deleted while a connection references it. Delete the connection first.

## Production Backlog Items

- [ ] Enable BGP with on-premises ASN and peering address for dynamic routing
- [ ] Configure FQDN instead of static IP if on-premises public IP is dynamic
- [ ] Document all on-premises address prefixes and keep them synchronized
- [ ] Plan for multiple local network gateways if connecting to multiple branch offices
- [ ] Implement monitoring for gateway IP reachability
- [ ] Add secondary local network gateway for redundant on-premises VPN device
