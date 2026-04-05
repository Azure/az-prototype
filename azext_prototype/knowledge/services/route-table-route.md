---
service_namespace: Microsoft.Network/routeTables/routes
display_name: Route Table Route
depends_on:
  - Microsoft.Network/routeTables
---

# Route Table Route

> A user-defined route (UDR) within an Azure route table that overrides default system routes to control traffic flow through network virtual appliances, VPN gateways, or the internet.

## When to Use
- Force traffic through a network virtual appliance (NVA) or Azure Firewall
- Override default internet routing for subnets (forced tunneling)
- Route traffic to a VPN gateway for on-premises connectivity
- Block specific traffic by routing to `None` (blackhole route)
- Implement hub-spoke network topologies with centralized egress

## POC Defaults
- **Address prefix**: Specific to the routing requirement (e.g., `0.0.0.0/0` for default route)
- **Next hop type**: VirtualAppliance (for NVA/firewall) or VnetLocal
- **Has BGP override**: false

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "route_to_firewall" {
  type      = "Microsoft.Network/routeTables/routes@2024-05-01"
  name      = var.route_name
  parent_id = azapi_resource.route_table.id

  body = {
    properties = {
      addressPrefix    = "0.0.0.0/0"
      nextHopType      = "VirtualAppliance"
      nextHopIpAddress = var.firewall_private_ip
    }
  }
}

# Blackhole route to drop traffic
resource "azapi_resource" "route_blackhole" {
  type      = "Microsoft.Network/routeTables/routes@2024-05-01"
  name      = "drop-traffic"
  parent_id = azapi_resource.route_table.id

  body = {
    properties = {
      addressPrefix = "10.99.0.0/16"
      nextHopType   = "None"
    }
  }
}
```

### RBAC Assignment
```hcl
# Network Contributor role on the route table allows route management.
```

## Bicep Patterns

### Basic Resource
```bicep
param routeName string
param addressPrefix string = '0.0.0.0/0'
param firewallPrivateIp string

resource route 'Microsoft.Network/routeTables/routes@2024-05-01' = {
  parent: routeTable
  name: routeName
  properties: {
    addressPrefix: addressPrefix
    nextHopType: 'VirtualAppliance'
    nextHopIpAddress: firewallPrivateIp
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
- **0.0.0.0/0 breaks Azure services**: A default route to an NVA can break Azure PaaS service connectivity (e.g., Azure Monitor, Key Vault). Use service tags or service endpoints to preserve access.
- **Next hop must be reachable**: The `nextHopIpAddress` must be reachable from the subnet. If the NVA is down, all routed traffic is dropped.
- **Route table must be associated**: Routes only take effect when the route table is associated with a subnet. Creating routes without subnet association has no impact.
- **BGP route override**: UDRs take precedence over BGP-learned routes by default. Set `hasBgpOverride` to manage this explicitly.
- **Asymmetric routing**: Ensure return traffic follows the same path. Asymmetric routing through NVAs causes connection failures due to stateful inspection.

## Production Backlog Items
- Service tag routes to preserve Azure PaaS connectivity
- Redundant NVA with health probe-based route failover
- BGP integration for dynamic route propagation
- Route table monitoring and effective routes auditing
