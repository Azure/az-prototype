# Azure Virtual Machines
> IaaS compute for running custom workloads, legacy applications, and specialized software that requires full OS control.

## When to Use

- **Lift-and-shift migrations** -- move on-premises VMs to Azure with minimal changes
- **Custom software requirements** -- applications that need specific OS configurations, drivers, or kernel modules
- **GPU workloads** -- ML training, rendering, or HPC with NVIDIA GPUs
- **Windows Server workloads** -- Active Directory, SQL Server on VMs, legacy .NET Framework apps
- **Self-managed databases** -- PostgreSQL, MySQL, MongoDB when managed services don't meet requirements
- **Jump boxes / bastion hosts** -- secure access points for private network administration

Choose VMs only when PaaS alternatives (App Service, Container Apps, Functions) cannot meet the requirement. VMs require more operational overhead (patching, monitoring, scaling, security hardening).

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| VM size | Standard_B2s | 2 vCPU, 4 GiB; burstable, lowest practical cost |
| OS | Ubuntu 22.04 LTS | Or Windows Server 2022 for Windows workloads |
| OS disk | Standard SSD, 30 GiB | Managed disk; sufficient for POC |
| Data disk | None (POC) | Add for database or storage workloads |
| Public IP | None (POC) | Use Azure Bastion for access |
| Availability | Single VM | No availability set/zone for POC |
| Auto-shutdown | Enabled (7 PM) | Prevent overnight idle costs |
| Authentication | SSH key (Linux) | Never use password auth for SSH |

## Terraform Patterns

### Basic Resource (Linux)

```hcl
resource "azapi_resource" "nic" {
  type      = "Microsoft.Network/networkInterfaces@2023-11-01"
  name      = "nic-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      ipConfigurations = [
        {
          name = "internal"
          properties = {
            subnet = {
              id = var.subnet_id
            }
            privateIPAllocationMethod = "Dynamic"
          }
        }
      ]
    }
  }

  tags = var.tags
}

resource "azapi_resource" "this" {
  type      = "Microsoft.Compute/virtualMachines@2024-03-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {
      hardwareProfile = {
        vmSize = "Standard_B2s"
      }
      osProfile = {
        computerName  = var.name
        adminUsername  = var.admin_username
        linuxConfiguration = {
          disablePasswordAuthentication = true
          ssh = {
            publicKeys = [
              {
                path    = "/home/${var.admin_username}/.ssh/authorized_keys"
                keyData = var.ssh_public_key  # Never use password auth
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
          createOption = "FromImage"
          caching      = "ReadWrite"
          managedDisk = {
            storageAccountType = "StandardSSD_LRS"
          }
          diskSizeGB = 30
        }
      }
      networkProfile = {
        networkInterfaces = [
          {
            id = azapi_resource.nic.id
          }
        ]
      }
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}
```

### Basic Resource (Windows)

```hcl
resource "azapi_resource" "windows_vm" {
  type      = "Microsoft.Compute/virtualMachines@2024-03-01"
  name      = var.name  # Max 15 chars for Windows
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {
      hardwareProfile = {
        vmSize = "Standard_B2s"
      }
      osProfile = {
        computerName  = var.name
        adminUsername  = var.admin_username
        adminPassword = var.admin_password  # Store in Key Vault
      }
      storageProfile = {
        imageReference = {
          publisher = "MicrosoftWindowsServer"
          offer     = "WindowsServer"
          sku       = "2022-datacenter-g2"
          version   = "latest"
        }
        osDisk = {
          createOption = "FromImage"
          caching      = "ReadWrite"
          managedDisk = {
            storageAccountType = "StandardSSD_LRS"
          }
          diskSizeGB = 128
        }
      }
      networkProfile = {
        networkInterfaces = [
          {
            id = azapi_resource.nic.id
          }
        ]
      }
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}
```

### Auto-Shutdown (Cost Control)

```hcl
resource "azapi_resource" "auto_shutdown" {
  type      = "Microsoft.DevTestLab/schedules@2018-09-15"
  name      = "shutdown-computevm-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      status           = "Enabled"
      taskType         = "ComputeVmShutdownTask"
      dailyRecurrence = {
        time = "1900"  # 7 PM
      }
      timeZoneId       = "UTC"
      targetResourceId = azapi_resource.this.id
      notificationSettings = {
        status = "Disabled"
      }
    }
  }
}
```

### RBAC Assignment

```hcl
# Virtual Machine Contributor -- manage VMs (not login access)
resource "azapi_resource" "vm_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.this.id}-vm-contributor")
  parent_id = azapi_resource.this.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/9980e02c-c2be-4d73-94e8-173b1dc7cf3c"
      principalId      = var.admin_identity_principal_id
    }
  }
}

# Virtual Machine Administrator Login -- AAD-based SSH/RDP access
resource "azapi_resource" "vm_admin_login" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.this.id}-vm-admin-login")
  parent_id = azapi_resource.this.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/1c0163c0-47e6-4577-8991-ea5c82e286e4"
      principalId      = var.admin_identity_principal_id
    }
  }
}

# Grant VM's managed identity access to other resources
resource "azapi_resource" "vm_blob_reader" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.storage_account_id}-vm-blob-reader")
  parent_id = var.storage_account_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"
      principalId      = azapi_resource.this.output.identity.principalId
    }
  }
}
```

RBAC role IDs:
- Virtual Machine Contributor: `9980e02c-c2be-4d73-94e8-173b1dc7cf3c`
- Virtual Machine Administrator Login: `1c0163c0-47e6-4577-8991-ea5c82e286e4`
- Virtual Machine User Login: `fb879df8-f326-4884-b1cf-06f3ad86be52`

### Private Endpoint

VMs don't use private endpoints -- they are deployed directly into subnets. Network security is controlled via NSGs and Azure Bastion:

```hcl
# Public IP for Azure Bastion
resource "azapi_resource" "bastion_pip" {
  type      = "Microsoft.Network/publicIPAddresses@2023-11-01"
  name      = "pip-bastion"
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

# Azure Bastion for secure VM access (no public IPs needed)
resource "azapi_resource" "bastion" {
  type      = "Microsoft.Network/bastionHosts@2023-11-01"
  name      = "bastion-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      ipConfigurations = [
        {
          name = "configuration"
          properties = {
            subnet = {
              id = var.bastion_subnet_id  # Must be named "AzureBastionSubnet"
            }
            publicIPAddress = {
              id = azapi_resource.bastion_pip.id
            }
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

## Bicep Patterns

### Basic Resource (Linux)

```bicep
@description('Name of the virtual machine')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Admin username')
param adminUsername string

@description('SSH public key')
param sshPublicKey string

@description('Subnet ID')
param subnetId string

@description('Tags to apply')
param tags object = {}

resource nic 'Microsoft.Network/networkInterfaces@2023-11-01' = {
  name: 'nic-${name}'
  location: location
  tags: tags
  properties: {
    ipConfigurations: [
      {
        name: 'internal'
        properties: {
          subnet: {
            id: subnetId
          }
          privateIPAllocationMethod: 'Dynamic'
        }
      }
    ]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-03-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    hardwareProfile: {
      vmSize: 'Standard_B2s'
    }
    osProfile: {
      computerName: name
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
        createOption: 'FromImage'
        managedDisk: {
          storageAccountType: 'StandardSSD_LRS'
        }
        diskSizeGB: 30
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: nic.id
        }
      ]
    }
  }
}

output id string = vm.id
output privateIp string = nic.properties.ipConfigurations[0].properties.privateIPAddress
output principalId string = vm.identity.principalId
```

### RBAC Assignment

```bicep
@description('Admin principal ID for VM login')
param adminPrincipalId string

// Virtual Machine Administrator Login
var vmAdminLoginRoleId = '1c0163c0-47e6-4577-8991-ea5c82e286e4'

resource vmAdminAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vm.id, adminPrincipalId, vmAdminLoginRoleId)
  scope: vm
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', vmAdminLoginRoleId)
    principalId: adminPrincipalId
    principalType: 'ServicePrincipal'
  }
}
```

## Application Code

VMs run standard application code -- no Azure-specific SDK patterns. The key integration points are:

### Cloud-Init (Linux Provisioning)

```yaml
#cloud-config
package_update: true
packages:
  - docker.io
  - python3-pip
runcmd:
  - systemctl enable docker
  - systemctl start docker
  - pip3 install azure-identity azure-storage-blob
write_files:
  - path: /etc/environment
    append: true
    content: |
      AZURE_CLIENT_ID=<managed-identity-client-id>
```

### Custom Script Extension (Terraform)

```hcl
resource "azapi_resource" "setup_script" {
  type      = "Microsoft.Compute/virtualMachines/extensions@2024-03-01"
  name      = "setup-script"
  location  = var.location
  parent_id = azapi_resource.this.id

  body = {
    properties = {
      publisher               = "Microsoft.Azure.Extensions"
      type                    = "CustomScript"
      typeHandlerVersion      = "2.1"
      autoUpgradeMinorVersion = true
      settings = {
        commandToExecute = "apt-get update && apt-get install -y docker.io && systemctl enable docker"
      }
    }
  }
}
```

### Python — Access Azure Services from VM

```python
# DefaultAzureCredential automatically uses the VM's managed identity
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

credential = DefaultAzureCredential()
client = BlobServiceClient(
    account_url="https://mystorageaccount.blob.core.windows.net",
    credential=credential,
)

# Upload file
with open("/data/report.csv", "rb") as f:
    client.get_blob_client("reports", "report.csv").upload_blob(f, overwrite=True)
```

## Common Pitfalls

1. **Password authentication for SSH** -- Never use passwords for Linux VMs. Always use SSH keys. Terraform enforces this by default with `disable_password_authentication = true`.
2. **No auto-shutdown configured** -- VMs without auto-shutdown run 24/7 and incur continuous costs. Always configure shutdown schedules for non-production VMs.
3. **Public IP on VMs** -- Avoid assigning public IPs directly to VMs. Use Azure Bastion for management access and load balancers/Front Door for application traffic.
4. **Windows VM name > 15 characters** -- Windows computer names are limited to 15 characters. Terraform will fail if the name exceeds this.
5. **Managed identity not enabled** -- Without managed identity, applications on the VM must use connection strings or keys. Always enable system-assigned or user-assigned managed identity.
6. **OS disk size too small** -- Default Ubuntu image disk is ~30 GiB. Docker images and application data can fill this quickly. Add a data disk for application storage.
7. **Burstable VMs throttled under sustained load** -- B-series VMs have CPU credits that deplete under continuous load. Use D-series or E-series for sustained compute.
8. **NSG not attached** -- VMs in subnets without NSGs are open to all VNet traffic. Always attach an NSG with explicit allow/deny rules.

## Production Backlog Items

- [ ] Remove public IPs and configure Azure Bastion for management access
- [ ] Upgrade from Burstable to General Purpose VM size
- [ ] Enable availability zones or availability sets for HA
- [ ] Configure Azure Backup for VM disaster recovery
- [ ] Enable Azure Monitor agent for metrics and logs
- [ ] Enable Microsoft Defender for Servers for threat detection
- [ ] Configure automatic OS patching (Update Management)
- [ ] Add data disks with Premium SSD for application storage
- [ ] Enable Azure Disk Encryption with customer-managed keys
- [ ] Configure NSG flow logs for network traffic analysis
- [ ] Set up VM scale sets for auto-scaling scenarios
