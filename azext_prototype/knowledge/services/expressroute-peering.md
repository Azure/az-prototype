---
service_namespace: Microsoft.Network/expressRouteCircuits/peerings
display_name: ExpressRoute Peering
depends_on:
  - Microsoft.Network/expressRouteCircuits
---

# ExpressRoute Peering

> BGP peering configuration on an ExpressRoute circuit that establishes routing between on-premises networks and Azure (private peering) or Microsoft services (Microsoft peering).

## When to Use
- **Azure Private Peering** -- access Azure VNet resources (VMs, databases, storage private endpoints) over ExpressRoute
- **Microsoft Peering** -- access Microsoft 365 and Azure PaaS services (Storage, SQL) over ExpressRoute with route filters
- Every ExpressRoute circuit requires at least one peering configuration to route traffic
- Private peering is the most common; Microsoft peering requires route filter approval

Azure Public Peering is deprecated. Use Microsoft Peering with route filters for PaaS service access.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Peering type | AzurePrivatePeering | Most common; direct VNet access |
| Peer ASN | Customer-provided | Your on-premises BGP ASN |
| Primary subnet | /30 | e.g., 10.0.0.0/30 -- 2 usable IPs |
| Secondary subnet | /30 | e.g., 10.0.0.4/30 -- separate from primary |
| VLAN ID | Provider-assigned | Must match provider's circuit configuration |
| Shared key | Optional | MD5 hash for BGP session authentication |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "private_peering" {
  type      = "Microsoft.Network/expressRouteCircuits/peerings@2024-01-01"
  name      = "AzurePrivatePeering"
  parent_id = azapi_resource.expressroute_circuit.id

  body = {
    properties = {
      peeringType                = "AzurePrivatePeering"
      peerASN                    = var.peer_asn  # e.g., 65515
      primaryPeerAddressPrefix   = var.primary_subnet    # e.g., "10.0.0.0/30"
      secondaryPeerAddressPrefix = var.secondary_subnet  # e.g., "10.0.0.4/30"
      vlanId                     = var.vlan_id           # e.g., 200
      sharedKey                  = var.shared_key        # Optional MD5 key
    }
  }
}
```

### RBAC Assignment

```hcl
# Peering management inherits from the parent ExpressRoute circuit RBAC.
# Network Contributor (4d97b98b-1d4f-4787-a291-c67834d212e7) on the circuit or resource group.
```

## Bicep Patterns

### Basic Resource

```bicep
@description('On-premises BGP ASN')
param peerAsn int

@description('Primary peer address prefix (/30)')
param primarySubnet string

@description('Secondary peer address prefix (/30)')
param secondarySubnet string

@description('VLAN ID for the peering')
param vlanId int

resource privatePeering 'Microsoft.Network/expressRouteCircuits/peerings@2024-01-01' = {
  parent: expressRouteCircuit
  name: 'AzurePrivatePeering'
  properties: {
    peeringType: 'AzurePrivatePeering'
    peerASN: peerAsn
    primaryPeerAddressPrefix: primarySubnet
    secondaryPeerAddressPrefix: secondarySubnet
    vlanId: vlanId
  }
}

output peeringId string = privatePeering.id
output peeringState string = privatePeering.properties.state
```

## Application Code

### Python
Infrastructure -- transparent to application code. ExpressRoute peering establishes network-layer connectivity; applications use the same Azure SDK endpoints regardless of whether traffic flows over ExpressRoute or the internet.

### C#
Infrastructure -- transparent to application code. ExpressRoute peering establishes network-layer connectivity; applications use the same Azure SDK endpoints regardless of whether traffic flows over ExpressRoute or the internet.

### Node.js
Infrastructure -- transparent to application code. ExpressRoute peering establishes network-layer connectivity; applications use the same Azure SDK endpoints regardless of whether traffic flows over ExpressRoute or the internet.

## Common Pitfalls

1. **Peering name must be exact** -- The name must be `AzurePrivatePeering` or `MicrosoftPeering` exactly. Custom names cause deployment failures.
2. **Overlapping /30 subnets** -- Primary and secondary subnets must not overlap with each other or with any VNet address space. Use RFC 1918 ranges not in your Azure VNets.
3. **VLAN ID mismatch** -- The VLAN ID must match what the connectivity provider has configured. A mismatch results in the peering staying in a `NotProvisioned` state.
4. **Peer ASN conflicts** -- The ASN must not conflict with Azure's reserved ASNs (12076, 65515, 65520). Using a conflicting ASN causes BGP session failures.
5. **Circuit must be provisioned first** -- The ExpressRoute circuit must be in `Provisioned` state (by the connectivity provider) before peering can be configured. Deploying peering on an `Enabled` circuit will succeed but the BGP session won't establish.
6. **BFD not enabled by default** -- Bidirectional Forwarding Detection speeds up failover but must be explicitly enabled. Without it, BGP failover can take 60-90 seconds.
7. **Microsoft Peering requires route filters** -- Without a route filter attached, no routes are advertised over Microsoft Peering. The peering appears active but no traffic flows.

## Production Backlog Items

- [ ] Enable BFD (Bidirectional Forwarding Detection) for faster failover
- [ ] Configure MD5 authentication (shared key) for BGP session security
- [ ] Set up route filters for Microsoft Peering to limit advertised routes
- [ ] Implement redundant circuits across different peering locations
- [ ] Configure connection monitoring with Network Watcher
- [ ] Document BGP community values for traffic engineering
- [ ] Plan IPv6 peering if dual-stack is required
