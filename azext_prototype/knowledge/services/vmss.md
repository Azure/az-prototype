# Azure Virtual Machine Scale Sets
> Managed service for deploying and managing a group of identical, auto-scaling virtual machines with integrated load balancing and availability zone support.

## When to Use

- **Horizontally scalable workloads** -- web servers, API backends, or workers that scale by adding identical VMs
- **Batch processing** -- scale out for periodic compute-intensive jobs, scale in when complete
- **Custom OS or runtime requirements** -- workloads that need full VM-level control over the operating system
- **Legacy application hosting** -- applications that cannot be containerized but need auto-scaling
- **High availability** -- spread VMs across availability zones with automatic instance repair

Choose VMSS over AKS/Container Apps when you need full OS control, custom VM images, or cannot containerize the workload. Choose AKS for container orchestration. Choose App Service for PaaS web app hosting without VM management overhead.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Orchestration mode | Flexible | Recommended over Uniform for new deployments |
| VM size | Standard_B2s | 2 vCPU, 4 GiB; lowest practical for POC |
| Instance count | 1-2 | Minimum; use auto-scale for production |
| OS | Ubuntu 22.04 LTS | Linux preferred; Windows Server 2022 if required |
| OS disk | Standard_LRS, 30 GiB | Managed disk; Premium for production |
| Managed identity | User-assigned | For accessing other Azure resources |
| Upgrade policy | Automatic | Rolling upgrades for seamless updates |
| Public IP per instance | None | Use Load Balancer or NAT Gateway for outbound |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "vmss" {
  type      = "Microsoft.Compute/virtualMachineScaleSets@2024-03-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type         = "UserAssigned"
    identity_ids = [var.managed_identity_id]
  }

  body = {
    sku = {
      name     = "Standard_B2s"
      tier     = "Standard"
      capacity = var.instance_count  # 1-2 for POC
    }
    properties = {
      orchestrationMode = "Flexible"
      platformFaultDomainCount = 1
      singlePlacementGroup     = false
      virtualMachineProfile = {
        osProfile = {
          computerNamePrefix   = var.name_prefix
          adminUsername         = var.admin_username
          linuxConfiguration = {
            disablePasswordAuthentication = true
            ssh = {
              publicKeys = [
                {
                  path    = "/home/${var.admin_username}/.ssh/authorized_keys"
                  keyData = var.ssh_public_key
                }
              ]
            }
          }
        }
        storageProfile = {
          imageReference = {
            publisher = "Canonical"
            offer     = "0001-com-ubuntu-server-jammy"
            sku       = "22_04-lts-gen2"
            version   = "latest"
          }
          osDisk = {
            caching              = "ReadWrite"
            createOption         = "FromImage"
            managedDisk = {
              storageAccountType = "Standard_LRS"
            }
            diskSizeGB = 30
          }
        }
        networkProfile = {
          networkApiVersion = "2020-11-01"
          networkInterfaceConfigurations = [
            {
              name = "nic-${var.name}"
              properties = {
                primary = true
                ipConfigurations = [
                  {
                    name = "ipconfig1"
                    properties = {
                      primary = true
                      subnet = {
                        id = var.subnet_id
                      }
                      loadBalancerBackendAddressPools = var.lb_backend_pool_id != null ? [
                        {
                          id = var.lb_backend_pool_id
                        }
                      ] : []
                    }
                  }
                ]
              }
            }
          ]
        }
      }
      upgradePolicy = {
        mode = "Automatic"
        automaticOSUpgradePolicy = {
          enableAutomaticOSUpgrade = true
          disableAutomaticRollback = false
        }
      }
      automaticRepairsPolicy = {
        enabled     = true
        gracePeriod = "PT10M"
      }
    }
  }

  tags = var.tags
}
```

### Auto-scale Settings

```hcl
resource "azapi_resource" "autoscale" {
  type      = "Microsoft.Insights/autoscaleSettings@2022-10-01"
  name      = "autoscale-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      enabled = true
      targetResourceUri = azapi_resource.vmss.id
      profiles = [
        {
          name = "default"
          capacity = {
            minimum = "1"
            maximum = "4"
            default = "1"
          }
          rules = [
            {
              metricTrigger = {
                metricName        = "Percentage CPU"
                metricResourceUri = azapi_resource.vmss.id
                timeGrain         = "PT1M"
                statistic         = "Average"
                timeWindow        = "PT5M"
                timeAggregation   = "Average"
                operator          = "GreaterThan"
                threshold         = 75
              }
              scaleAction = {
                direction = "Increase"
                type      = "ChangeCount"
                value     = "1"
                cooldown  = "PT5M"
              }
            },
            {
              metricTrigger = {
                metricName        = "Percentage CPU"
                metricResourceUri = azapi_resource.vmss.id
                timeGrain         = "PT1M"
                statistic         = "Average"
                timeWindow        = "PT5M"
                timeAggregation   = "Average"
                operator          = "LessThan"
                threshold         = 25
              }
              scaleAction = {
                direction = "Decrease"
                type      = "ChangeCount"
                value     = "1"
                cooldown  = "PT5M"
              }
            }
          ]
        }
      ]
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# Grant VMSS managed identity access to Key Vault for secrets
resource "azapi_resource" "keyvault_secrets_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.key_vault_id}${var.managed_identity_principal_id}keyvault-secrets-user")
  parent_id = var.key_vault_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/4633458b-17de-408a-b874-0445c86b69e6"  # Key Vault Secrets User
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Virtual Machine Contributor for management operations
resource "azapi_resource" "vm_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.vmss.id}${var.admin_principal_id}vm-contributor")
  parent_id = azapi_resource.vmss.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/9980e02c-c2be-4d73-94e8-173b1dc7cf3c"  # Virtual Machine Contributor
      principalId      = var.admin_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

VMSS does not use private endpoints. VMs are deployed into a VNet subnet directly. Control network access through NSGs and subnet configuration:

```hcl
resource "azapi_resource" "nsg" {
  type      = "Microsoft.Network/networkSecurityGroups@2023-11-01"
  name      = "nsg-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      securityRules = [
        {
          name = "AllowHTTPS"
          properties = {
            priority                 = 100
            direction                = "Inbound"
            access                   = "Allow"
            protocol                 = "Tcp"
            sourcePortRange          = "*"
            destinationPortRange     = "443"
            sourceAddressPrefix      = var.allowed_source_cidr
            destinationAddressPrefix = "*"
          }
        },
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
      ]
    }
  }

  tags = var.tags
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the VMSS')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('VM size')
param vmSize string = 'Standard_B2s'

@description('Number of instances')
param instanceCount int = 1

@description('Subnet ID for VMSS instances')
param subnetId string

@description('Managed identity resource ID')
param managedIdentityId string

@description('Admin username')
param adminUsername string

@description('SSH public key')
@secure()
param sshPublicKey string

@description('Tags to apply')
param tags object = {}

resource vmss 'Microsoft.Compute/virtualMachineScaleSets@2024-03-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  sku: {
    name: vmSize
    tier: 'Standard'
    capacity: instanceCount
  }
  properties: {
    orchestrationMode: 'Flexible'
    platformFaultDomainCount: 1
    singlePlacementGroup: false
    virtualMachineProfile: {
      osProfile: {
        computerNamePrefix: take(name, 9)
        adminUsername: adminUsername
        linuxConfiguration: {
          disablePasswordAuthentication: true
          ssh: {
            publicKeys: [
              {
                path: '/home/${adminUsername}/.ssh/authorized_keys'
                keyData: sshPublicKey
              }
            ]
          }
        }
      }
      storageProfile: {
        imageReference: {
          publisher: 'Canonical'
          offer: '0001-com-ubuntu-server-jammy'
          sku: '22_04-lts-gen2'
          version: 'latest'
        }
        osDisk: {
          caching: 'ReadWrite'
          createOption: 'FromImage'
          managedDisk: {
            storageAccountType: 'Standard_LRS'
          }
          diskSizeGB: 30
        }
      }
      networkProfile: {
        networkApiVersion: '2020-11-01'
        networkInterfaceConfigurations: [
          {
            name: 'nic-${name}'
            properties: {
              primary: true
              ipConfigurations: [
                {
                  name: 'ipconfig1'
                  properties: {
                    primary: true
                    subnet: {
                      id: subnetId
                    }
                  }
                }
              ]
            }
          }
        ]
      }
    }
    upgradePolicy: {
      mode: 'Automatic'
    }
    automaticRepairsPolicy: {
      enabled: true
      gracePeriod: 'PT10M'
    }
  }
}

output id string = vmss.id
output name string = vmss.name
```

### RBAC Assignment

```bicep
@description('Principal ID for VM management')
param adminPrincipalId string

resource vmContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vmss.id, adminPrincipalId, '9980e02c-c2be-4d73-94e8-173b1dc7cf3c')
  scope: vmss
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '9980e02c-c2be-4d73-94e8-173b1dc7cf3c')  // Virtual Machine Contributor
    principalId: adminPrincipalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Uniform vs Flexible mode confusion | Uniform is legacy; lacks some features | Always use `Flexible` orchestration mode for new deployments |
| SSH password auth left enabled | Brute-force attack surface | Set `disablePasswordAuthentication = true`; use SSH keys only |
| No load balancer configured | Traffic cannot be distributed across instances | Deploy Azure Load Balancer or Application Gateway in front of VMSS |
| Auto-scale cooldown too short | Rapid scaling oscillation (thrashing) | Set cooldown to at least 5 minutes between scale actions |
| Custom image not generalized | Instances fail to provision properly | Run `waagent -deprovision+user` (Linux) or Sysprep (Windows) before capturing |
| Insufficient subnet size | Scale-out fails when subnet runs out of IPs | Size subnet for maximum expected instance count plus overhead |
| Missing health probe | Automatic repairs and rolling upgrades cannot function | Configure application health extension or load balancer health probe |
| Forgetting NAT gateway for outbound | Instances cannot reach internet for package updates | Attach NAT Gateway to subnet or use Load Balancer outbound rules |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Availability zones | P1 | Spread instances across zones for zone-redundant HA |
| Application health extension | P1 | Configure health probes for automatic instance repair |
| Custom VM image | P2 | Build golden image with Packer for consistent deployments |
| Azure Bastion | P1 | Deploy Bastion for secure SSH/RDP access without public IPs |
| Auto-scale optimization | P2 | Tune scale rules based on actual workload metrics |
| Premium OS disks | P2 | Upgrade to Premium_LRS for production IOPS requirements |
| NSG hardening | P1 | Restrict inbound/outbound traffic to minimum required |
| Boot diagnostics | P3 | Enable boot diagnostics for troubleshooting instance failures |
| Azure Monitor agent | P2 | Install Azure Monitor agent for log and metric collection |
| Update management | P2 | Configure automatic OS patching schedule |
