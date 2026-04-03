# Azure Bastion
> Fully managed PaaS service providing secure RDP and SSH access to virtual machines over TLS, without exposing public IPs on VMs.

## When to Use

- **Secure VM management** -- access VMs via browser-based RDP/SSH without public IPs
- **Jump box replacement** -- eliminates need for self-managed jump boxes or VPN for VM access
- **Private AKS clusters** -- access API server of private Kubernetes clusters via Bastion host
- **DevOps agent access** -- SSH into self-hosted build agents in private VNets
- **Compliance requirements** -- audit trail for all management sessions (no direct RDP/SSH exposure)

Azure Bastion is a **management-plane service** -- it provides secure access to compute resources but does not carry production application traffic.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Basic | Developer SKU for single connection; Basic for small teams |
| Subnet name | AzureBastionSubnet | Must be exactly this name (Azure requirement) |
| Subnet size | /26 minimum | 64 addresses; /26 is the minimum for Bastion |
| Public IP | Standard SKU, Static | Required for Bastion |
| Scale units | 2 (Basic default) | Each unit supports ~20 concurrent sessions |
| Copy/paste | Enabled | Browser clipboard integration |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "bastion_subnet" {
  type      = "Microsoft.Network/virtualNetworks/subnets@2024-01-01"
  name      = "AzureBastionSubnet"  # Must be exactly this name
  parent_id = var.virtual_network_id

  body = {
    properties = {
      addressPrefix = var.bastion_subnet_prefix  # e.g., "10.0.10.0/26"
    }
  }
}

resource "azapi_resource" "bastion_pip" {
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

resource "azapi_resource" "bastion" {
  type      = "Microsoft.Network/bastionHosts@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Basic"  # "Standard" for tunneling, shareable links, Kerberos
    }
    properties = {
      ipConfigurations = [
        {
          name = "bastion-ip-config"
          properties = {
            publicIPAddress = {
              id = azapi_resource.bastion_pip.id
            }
            subnet = {
              id = azapi_resource.bastion_subnet.id
            }
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

### Standard SKU with Native Client and Tunneling

```hcl
resource "azapi_resource" "bastion_standard" {
  type      = "Microsoft.Network/bastionHosts@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard"
    }
    properties = {
      ipConfigurations = [
        {
          name = "bastion-ip-config"
          properties = {
            publicIPAddress = {
              id = azapi_resource.bastion_pip.id
            }
            subnet = {
              id = azapi_resource.bastion_subnet.id
            }
          }
        }
      ]
      enableTunneling    = true   # az network bastion tunnel
      enableIpConnect    = true   # Connect by IP address
      scaleUnits         = 2
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# Reader role on the VM is required to connect via Bastion
resource "azapi_resource" "vm_reader" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.vm_id}-${var.user_principal_id}-reader")
  parent_id = var.vm_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/acdd72a7-3385-48ef-bd42-f606fba81ae7"  # Reader
      principalId      = var.user_principal_id
      principalType    = "User"
    }
  }
}

# Bastion itself does not require data-plane RBAC
# Users need Reader on the target VM + network connectivity
```

### Private Endpoint

Azure Bastion does not support private endpoints -- it is a public-facing management service by design. The Bastion host uses a public IP to accept browser connections and then connects privately to VMs within the VNet.

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Bastion host')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Virtual network ID')
param virtualNetworkId string

@description('Bastion subnet prefix (min /26)')
param bastionSubnetPrefix string = '10.0.10.0/26'

@description('Tags to apply')
param tags object = {}

resource bastionSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' = {
  name: '${split(virtualNetworkId, '/')[8]}/AzureBastionSubnet'
  properties: {
    addressPrefix: bastionSubnetPrefix
  }
}

resource bastionPip 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
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

resource bastion 'Microsoft.Network/bastionHosts@2024-01-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    ipConfigurations: [
      {
        name: 'bastion-ip-config'
        properties: {
          publicIPAddress: {
            id: bastionPip.id
          }
          subnet: {
            id: bastionSubnet.id
          }
        }
      }
    ]
  }
}

output id string = bastion.id
output dnsName string = bastion.properties.dnsName
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Wrong subnet name | Deployment fails immediately | Subnet must be named exactly `AzureBastionSubnet` |
| Subnet too small | Bastion cannot deploy | Minimum /26 (64 addresses); /26 sufficient for most POCs |
| Using Basic/Dynamic public IP | Bastion requires Standard SKU | Use `Standard` SKU with `Static` allocation |
| Forgetting VM Reader role | Users see Bastion UI but cannot connect to VMs | Assign Reader role on target VMs to connecting users |
| Basic SKU limitations | No native client tunneling, no IP-based connect | Use Standard SKU if `az network bastion tunnel` is needed |
| Deployment time | Bastion takes 10-15 minutes to deploy | Plan for long provisioning in deployment pipelines |
| NSG on AzureBastionSubnet | Connectivity breaks if required rules are missing | Follow Azure docs for required NSG inbound/outbound rules |
| Cost surprise | Bastion incurs hourly charges even when idle | Basic SKU is ~$0.19/hour; Developer SKU is cheaper for single-user |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Standard SKU upgrade | P2 | Upgrade to Standard for native client tunneling and IP-based connect |
| Diagnostic logging | P2 | Enable Bastion session logs to Log Analytics for audit trail |
| NSG hardening | P1 | Apply recommended NSG rules to AzureBastionSubnet per Azure documentation |
| Scale units | P3 | Increase scale units for concurrent session capacity (Standard SKU) |
| Shareable links | P3 | Enable shareable links for time-limited VM access without portal (Standard SKU) |
| Session recording | P2 | Integrate session recording for compliance and security audit |
| Multi-VNet access | P3 | Configure VNet peering for Bastion to reach VMs in peered VNets |
| Kerberos auth | P3 | Enable Kerberos authentication for domain-joined VMs (Standard SKU) |
