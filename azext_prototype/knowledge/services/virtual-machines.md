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
resource "azurerm_linux_virtual_machine" "this" {
  name                  = var.name
  location              = var.location
  resource_group_name   = var.resource_group_name
  size                  = "Standard_B2s"
  admin_username        = var.admin_username
  network_interface_ids = [azurerm_network_interface.this.id]

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key  # Never use password auth
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 30
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "latest"
  }

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

resource "azurerm_network_interface" "this" {
  name                = "nic-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name

  ip_configuration {
    name                          = "internal"
    subnet_id                     = var.subnet_id
    private_ip_address_allocation = "Dynamic"
  }

  tags = var.tags
}
```

### Basic Resource (Windows)

```hcl
resource "azurerm_windows_virtual_machine" "this" {
  name                  = var.name  # Max 15 chars for Windows
  location              = var.location
  resource_group_name   = var.resource_group_name
  size                  = "Standard_B2s"
  admin_username        = var.admin_username
  admin_password        = var.admin_password  # Store in Key Vault
  network_interface_ids = [azurerm_network_interface.this.id]

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 128
  }

  source_image_reference {
    publisher = "MicrosoftWindowsServer"
    offer     = "WindowsServer"
    sku       = "2022-datacenter-g2"
    version   = "latest"
  }

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}
```

### Auto-Shutdown (Cost Control)

```hcl
resource "azurerm_dev_test_global_vm_shutdown_schedule" "this" {
  virtual_machine_id = azurerm_linux_virtual_machine.this.id
  location           = var.location
  enabled            = true

  daily_recurrence_time = "1900"  # 7 PM
  timezone              = "UTC"

  notification_settings {
    enabled = false
  }
}
```

### RBAC Assignment

```hcl
# Virtual Machine Contributor -- manage VMs (not login access)
resource "azurerm_role_assignment" "vm_contributor" {
  scope                = azurerm_linux_virtual_machine.this.id
  role_definition_name = "Virtual Machine Contributor"
  principal_id         = var.admin_identity_principal_id
}

# Virtual Machine Administrator Login -- AAD-based SSH/RDP access
resource "azurerm_role_assignment" "vm_admin_login" {
  scope                = azurerm_linux_virtual_machine.this.id
  role_definition_name = "Virtual Machine Administrator Login"
  principal_id         = var.admin_identity_principal_id
}

# Grant VM's managed identity access to other resources
resource "azurerm_role_assignment" "vm_blob_reader" {
  scope                = var.storage_account_id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_linux_virtual_machine.this.identity[0].principal_id
}
```

RBAC role IDs:
- Virtual Machine Contributor: `9980e02c-c2be-4d73-94e8-173b1dc7cf3c`
- Virtual Machine Administrator Login: `1c0163c0-47e6-4577-8991-ea5c82e286e4`
- Virtual Machine User Login: `fb879df8-f326-4884-b1cf-06f3ad86be52`

### Private Endpoint

VMs don't use private endpoints -- they are deployed directly into subnets. Network security is controlled via NSGs and Azure Bastion:

```hcl
# Azure Bastion for secure VM access (no public IPs needed)
resource "azurerm_bastion_host" "this" {
  name                = "bastion-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name

  ip_configuration {
    name                 = "configuration"
    subnet_id            = var.bastion_subnet_id  # Must be named "AzureBastionSubnet"
    public_ip_address_id = azurerm_public_ip.bastion.id
  }

  tags = var.tags
}

resource "azurerm_public_ip" "bastion" {
  name                = "pip-bastion"
  location            = var.location
  resource_group_name = var.resource_group_name
  allocation_method   = "Static"
  sku                 = "Standard"

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
resource "azurerm_virtual_machine_extension" "setup" {
  name                 = "setup-script"
  virtual_machine_id   = azurerm_linux_virtual_machine.this.id
  publisher            = "Microsoft.Azure.Extensions"
  type                 = "CustomScript"
  type_handler_version = "2.1"

  settings = jsonencode({
    commandToExecute = "apt-get update && apt-get install -y docker.io && systemctl enable docker"
  })
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
