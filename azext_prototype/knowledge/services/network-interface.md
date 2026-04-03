# Azure Network Interface
> Virtual network interface card (NIC) that connects Azure virtual machines and other compute resources to a virtual network for network communication.

## When to Use

- **Virtual machine networking** -- every Azure VM requires at least one NIC for network connectivity
- **Multiple NICs per VM** -- separate management, application, and data traffic on different subnets
- **Network virtual appliances** -- firewalls and routers that need multiple NICs with IP forwarding
- **Custom IP configuration** -- static private IPs, multiple IP configurations, or secondary IPs
- **Accelerated networking** -- high-performance networking for latency-sensitive workloads

NICs are companion resources -- they are always created alongside VMs or other compute resources. You rarely deploy a NIC standalone; it accompanies a VM, VMSS, or network virtual appliance deployment.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| IP allocation | Dynamic | Static for production servers needing stable IPs |
| Public IP | None | Use Azure Bastion for management access |
| Accelerated networking | Enabled | Supported on most D/E/F/M series VMs |
| DNS servers | Inherited from VNet | Custom DNS only if required |
| NSG | Attached at subnet level | Prefer subnet-level NSG over NIC-level |
| IP forwarding | Disabled | Enable only for NVA/firewall scenarios |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "nic" {
  type      = "Microsoft.Network/networkInterfaces@2023-11-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      enableAcceleratedNetworking = true  # Supported on D2s_v5 and larger
      ipConfigurations = [
        {
          name = "ipconfig1"
          properties = {
            primary                   = true
            privateIPAllocationMethod = "Dynamic"  # "Static" with privateIPAddress for fixed IP
            subnet = {
              id = var.subnet_id
            }
          }
        }
      ]
    }
  }

  tags = var.tags

  response_export_values = ["properties.ipConfigurations[0].properties.privateIPAddress"]
}
```

### With Static IP

```hcl
resource "azapi_resource" "nic_static" {
  type      = "Microsoft.Network/networkInterfaces@2023-11-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      enableAcceleratedNetworking = true
      ipConfigurations = [
        {
          name = "ipconfig1"
          properties = {
            primary                   = true
            privateIPAllocationMethod = "Static"
            privateIPAddress          = var.private_ip_address
            subnet = {
              id = var.subnet_id
            }
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

### With Public IP (not recommended for production)

```hcl
resource "azapi_resource" "public_ip" {
  type      = "Microsoft.Network/publicIPAddresses@2023-11-01"
  name      = "pip-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard"
    }
    properties = {
      publicIPAllocationMethod = "Static"
      publicIPAddressVersion   = "IPv4"
    }
  }

  tags = var.tags
}

resource "azapi_resource" "nic_public" {
  type      = "Microsoft.Network/networkInterfaces@2023-11-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      enableAcceleratedNetworking = true
      ipConfigurations = [
        {
          name = "ipconfig1"
          properties = {
            primary                   = true
            privateIPAllocationMethod = "Dynamic"
            subnet = {
              id = var.subnet_id
            }
            publicIPAddress = {
              id = azapi_resource.public_ip.id
            }
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

### Multiple IP Configurations

```hcl
resource "azapi_resource" "nic_multi_ip" {
  type      = "Microsoft.Network/networkInterfaces@2023-11-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      enableAcceleratedNetworking = true
      ipConfigurations = [
        {
          name = "ipconfig-primary"
          properties = {
            primary                   = true
            privateIPAllocationMethod = "Dynamic"
            subnet = {
              id = var.subnet_id
            }
          }
        },
        {
          name = "ipconfig-secondary"
          properties = {
            primary                   = false
            privateIPAllocationMethod = "Dynamic"
            subnet = {
              id = var.subnet_id
            }
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

### With NSG Attachment

```hcl
resource "azapi_resource" "nic_with_nsg" {
  type      = "Microsoft.Network/networkInterfaces@2023-11-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      enableAcceleratedNetworking = true
      networkSecurityGroup = {
        id = var.nsg_id
      }
      ipConfigurations = [
        {
          name = "ipconfig1"
          properties = {
            primary                   = true
            privateIPAllocationMethod = "Dynamic"
            subnet = {
              id = var.subnet_id
            }
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# NICs are typically managed through resource group-level RBAC.
# Network Contributor role on the resource group or subscription scope.
resource "azapi_resource" "network_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.resource_group_id}${var.admin_principal_id}network-contributor")
  parent_id = var.resource_group_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/4d97b98b-1d4f-4787-a291-c67834d212e7"  # Network Contributor
      principalId      = var.admin_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

NICs do not support private endpoints -- they are themselves the network interface for VMs and other resources. Private endpoints create their own managed NICs automatically.

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the network interface')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Subnet ID')
param subnetId string

@description('Enable accelerated networking')
param enableAcceleratedNetworking bool = true

@description('Tags to apply')
param tags object = {}

resource nic 'Microsoft.Network/networkInterfaces@2023-11-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    enableAcceleratedNetworking: enableAcceleratedNetworking
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          primary: true
          privateIPAllocationMethod: 'Dynamic'
          subnet: {
            id: subnetId
          }
        }
      }
    ]
  }
}

output id string = nic.id
output name string = nic.name
output privateIPAddress string = nic.properties.ipConfigurations[0].properties.privateIPAddress
```

### With Load Balancer Backend Pool

```bicep
@description('Load balancer backend pool ID')
param lbBackendPoolId string = ''

resource nic 'Microsoft.Network/networkInterfaces@2023-11-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    enableAcceleratedNetworking: true
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          primary: true
          privateIPAllocationMethod: 'Dynamic'
          subnet: {
            id: subnetId
          }
          loadBalancerBackendAddressPools: !empty(lbBackendPoolId) ? [
            {
              id: lbBackendPoolId
            }
          ] : []
        }
      }
    ]
  }
}
```

### RBAC Assignment

NICs inherit RBAC from the resource group. Use Network Contributor role at the resource group scope for NIC management.

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Accelerated networking on unsupported VM size | NIC creation or VM attachment fails | Check VM size supports accelerated networking before enabling |
| NIC-level and subnet-level NSG conflict | Unexpected traffic blocking from dual evaluation | Prefer subnet-level NSGs; use NIC-level only when per-VM rules are needed |
| Static IP outside subnet range | NIC creation fails | Verify the static IP falls within the subnet address space |
| Deleting NIC attached to VM | Deletion fails with dependency error | Detach NIC from VM or delete VM first |
| IP forwarding disabled on NVA | NVA cannot route traffic between subnets | Enable `enableIPForwarding = true` for firewall/router NICs |
| Public IP on production VMs | Direct internet exposure; security risk | Use Azure Bastion, VPN, or Load Balancer instead of public IPs |
| Subnet full | NIC creation fails with no available IPs | Monitor subnet IP utilization; plan subnet sizing for growth |
| DNS server misconfiguration | VM cannot resolve hostnames | Inherit VNet DNS settings unless custom DNS is explicitly required |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Remove public IPs | P1 | Migrate to Azure Bastion for management access; remove public IPs |
| NSG hardening | P1 | Review and restrict NSG rules to minimum required traffic |
| Static IPs for servers | P2 | Assign static private IPs to servers that need stable addresses |
| Accelerated networking | P2 | Verify and enable accelerated networking on all supported VMs |
| Application security groups | P2 | Use ASGs for logical grouping and simplified NSG rules |
| DNS configuration | P3 | Configure custom DNS servers if using private DNS zones |
| Network monitoring | P2 | Enable NSG flow logs and Traffic Analytics |
| IP address planning | P3 | Document and plan IP address allocation across subnets |
| Multiple NICs for NVAs | P3 | Configure multi-NIC setups for network virtual appliance deployments |
| Diagnostic logging | P3 | Enable NIC diagnostic settings for network troubleshooting |
