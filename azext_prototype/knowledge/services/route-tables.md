# Azure Route Tables
> User-defined routing (UDR) resource that controls network traffic flow within and between Azure virtual network subnets, enabling traffic steering through network virtual appliances, firewalls, and VPN gateways.

## When to Use

- Forcing traffic through Azure Firewall or a network virtual appliance (NVA)
- Overriding Azure's default system routes for custom traffic paths
- Implementing hub-spoke network topology with centralized egress
- Routing traffic between subnets through a security appliance
- Preventing direct internet egress from workload subnets (force tunneling)
- NOT suitable for: filtering traffic by port/protocol (use NSGs), DNS resolution (use Private DNS Zones), or load balancing (use Azure Load Balancer or Application Gateway)

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| BGP route propagation | Enabled | Disable if using forced tunneling via NVA/firewall |
| Routes | 0-0-0-0 to firewall | Common pattern for centralized egress |
| Association | Per subnet | Route tables are associated with subnets, not VNets |
| Location | Same as VNet | Must be in the same region as the virtual network |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "route_table" {
  type      = "Microsoft.Network/routeTables@2023-11-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      disableBgpRoutePropagation = false  # Set true for forced tunneling
      routes = [
        {
          name = "default-to-firewall"
          properties = {
            addressPrefix    = "0.0.0.0/0"
            nextHopType      = "VirtualAppliance"
            nextHopIpAddress = var.firewall_private_ip
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

### With Multiple Routes

```hcl
resource "azapi_resource" "route_table" {
  type      = "Microsoft.Network/routeTables@2023-11-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      disableBgpRoutePropagation = true  # Disable BGP for forced tunneling
      routes = [
        {
          name = "internet-to-firewall"
          properties = {
            addressPrefix    = "0.0.0.0/0"
            nextHopType      = "VirtualAppliance"
            nextHopIpAddress = var.firewall_private_ip
          }
        },
        {
          name = "to-shared-services"
          properties = {
            addressPrefix    = var.shared_services_cidr  # e.g., "10.1.0.0/16"
            nextHopType      = "VirtualAppliance"
            nextHopIpAddress = var.firewall_private_ip
          }
        },
        {
          name = "to-on-premises"
          properties = {
            addressPrefix = var.on_premises_cidr  # e.g., "172.16.0.0/12"
            nextHopType   = "VirtualNetworkGateway"
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

### Individual Route (Child Resource)

```hcl
resource "azapi_resource" "route" {
  type      = "Microsoft.Network/routeTables/routes@2023-11-01"
  name      = var.route_name
  parent_id = azapi_resource.route_table.id

  body = {
    properties = {
      addressPrefix    = var.address_prefix
      nextHopType      = "VirtualAppliance"  # VirtualAppliance, VirtualNetworkGateway, VnetLocal, Internet, None
      nextHopIpAddress = var.next_hop_ip     # Required when nextHopType = VirtualAppliance
    }
  }
}
```

### Subnet Association

```hcl
# Associate route table with a subnet by updating the subnet
resource "azapi_update_resource" "subnet_route_association" {
  type        = "Microsoft.Network/virtualNetworks/subnets@2023-11-01"
  resource_id = var.subnet_id

  body = {
    properties = {
      routeTable = {
        id = azapi_resource.route_table.id
      }
    }
  }
}
```

### Forced Tunneling (Block Direct Internet)

```hcl
resource "azapi_resource" "forced_tunnel_rt" {
  type      = "Microsoft.Network/routeTables@2023-11-01"
  name      = "rt-forced-tunnel"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      disableBgpRoutePropagation = true
      routes = [
        {
          name = "force-all-to-firewall"
          properties = {
            addressPrefix    = "0.0.0.0/0"
            nextHopType      = "VirtualAppliance"
            nextHopIpAddress = var.firewall_private_ip
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

## Bicep Patterns

### Basic Resource

```bicep
param name string
param location string
param firewallPrivateIp string
param disableBgpRoutePropagation bool = false
param tags object = {}

resource routeTable 'Microsoft.Network/routeTables@2023-11-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    disableBgpRoutePropagation: disableBgpRoutePropagation
    routes: [
      {
        name: 'default-to-firewall'
        properties: {
          addressPrefix: '0.0.0.0/0'
          nextHopType: 'VirtualAppliance'
          nextHopIpAddress: firewallPrivateIp
        }
      }
    ]
  }
}

output id string = routeTable.id
output name string = routeTable.name
```

### With Multiple Routes

```bicep
param name string
param location string
param firewallPrivateIp string
param sharedServicesCidr string
param tags object = {}

resource routeTable 'Microsoft.Network/routeTables@2023-11-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    disableBgpRoutePropagation: true
    routes: [
      {
        name: 'internet-to-firewall'
        properties: {
          addressPrefix: '0.0.0.0/0'
          nextHopType: 'VirtualAppliance'
          nextHopIpAddress: firewallPrivateIp
        }
      }
      {
        name: 'to-shared-services'
        properties: {
          addressPrefix: sharedServicesCidr
          nextHopType: 'VirtualAppliance'
          nextHopIpAddress: firewallPrivateIp
        }
      }
    ]
  }
}

output id string = routeTable.id
```

### Subnet Association

```bicep
param vnetName string
param subnetName string
param subnetAddressPrefix string
param routeTableId string

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' existing = {
  name: vnetName
}

resource subnet 'Microsoft.Network/virtualNetworks/subnets@2023-11-01' = {
  parent: vnet
  name: subnetName
  properties: {
    addressPrefix: subnetAddressPrefix
    routeTable: {
      id: routeTableId
    }
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Forgetting to associate route table with subnet | Routes defined but never applied; traffic uses default system routes | Always associate the route table with the target subnet(s) |
| Breaking Azure PaaS connectivity | Services like Storage, SQL, Key Vault lose connectivity | Add service tag routes or use service endpoints / private endpoints |
| Disabling BGP propagation unintentionally | VPN/ExpressRoute learned routes disappear; on-premises connectivity lost | Only disable BGP propagation when explicitly doing forced tunneling |
| Route table in wrong region | Cannot associate with subnets in different regions | Keep route table in the same region as the virtual network |
| Overlapping address prefixes | Most specific route wins; unclear which route is active | Use non-overlapping prefixes; review effective routes in portal |
| Next hop appliance is down | All routed traffic is black-holed | Ensure NVA/firewall has HA (availability zones, scale sets) |
| Not accounting for AKS/Container Apps requirements | Breaking cluster networking or container egress | Review service-specific networking docs before adding UDRs to workload subnets |

## CRITICAL: Special Subnet Considerations

Some Azure services have restrictions on route tables applied to their subnets:
- **AKS**: Custom route tables must include routes for node/pod CIDRs; some CNI modes require specific configurations
- **Application Gateway**: Route tables on the AppGW subnet must NOT include 0.0.0.0/0 to a virtual appliance
- **Azure Firewall**: Firewall subnet does not support route tables (it IS the next hop)
- **API Management**: Internal VNet mode requires specific routes for management traffic
- **Azure Bastion**: The AzureBastionSubnet does not support user-defined routes

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Hub-spoke route design | P1 | Design complete route table strategy for hub-spoke network topology |
| NVA high availability | P1 | Ensure next-hop NVA/firewall has zone redundancy and health probes |
| Service endpoint routes | P2 | Add routes for Azure PaaS services that bypass the firewall if needed |
| Route monitoring | P2 | Monitor effective routes and set up alerts for route changes |
| BGP route propagation review | P2 | Review and document BGP propagation settings per subnet |
| Route table per subnet documentation | P3 | Document which route tables apply to which subnets and why |
| Asymmetric routing prevention | P2 | Validate that return traffic follows the same path to avoid dropped packets |
| Network Watcher integration | P3 | Use Network Watcher next-hop diagnostic to validate routing |
