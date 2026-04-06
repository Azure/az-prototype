---
service_namespace: Microsoft.Compute/disks
display_name: Azure Managed Disk
---

# Azure Managed Disk

> Block-level storage volume managed by Azure, used as OS disks and data disks for Virtual Machines with built-in redundancy, encryption, and snapshot capabilities.

## When to Use
- **VM OS disk** -- every Azure VM requires an OS disk (automatically created with the VM)
- **VM data disks** -- additional persistent storage for databases, file shares, application data
- **Standalone snapshots** -- create managed disks from snapshots for backup/restore
- **Disk-based migration** -- import VHDs from on-premises as managed disks
- **Shared disks** -- multi-attach scenarios for Windows Server Failover Clustering

Managed disks are typically created alongside VMs, but standalone disk resources are used for pre-provisioning, cross-VM attachment, or creating from snapshots/images.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Standard_LRS | Lowest cost; HDD-based, sufficient for POC |
| SKU (alternative) | StandardSSD_LRS | Better IOPS than HDD; recommended for most POC workloads |
| Size | 32 GB (P4/E4/S4) | Minimum useful size; disks smaller than 32 GB may have throttled IOPS |
| Encryption | Platform-managed keys | Default SSE; CMK for compliance |
| OS disk type | From image | Created with VM from marketplace image |
| Bursting | Disabled | Enable on-demand for P20+ Premium SSD |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "data_disk" {
  type      = "Microsoft.Compute/disks@2024-03-02"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "StandardSSD_LRS"  # or "Premium_LRS", "Standard_LRS", "UltraSSD_LRS"
    }
    properties = {
      diskSizeGB   = var.disk_size_gb  # e.g., 64
      creationData = {
        createOption = "Empty"  # or "FromImage", "Copy", "Upload"
      }
      encryption = {
        type = "EncryptionAtRestWithPlatformKey"  # Default SSE
      }
    }
    zones = var.availability_zone != null ? [var.availability_zone] : null
  }

  tags = var.tags

  response_export_values = ["*"]
}
```

### RBAC Assignment

```hcl
# Disk Backup Reader -- for backup scenarios
resource "azapi_resource" "disk_backup_reader" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.data_disk.id}-${var.principal_id}-disk-backup")
  parent_id = azapi_resource.data_disk.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/3e5e47e6-65f7-47ef-90b5-e5dd4d455f24"
      principalId      = var.principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Disk name')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Disk size in GB')
param diskSizeGB int = 64

@description('Disk SKU')
@allowed(['Standard_LRS', 'StandardSSD_LRS', 'Premium_LRS', 'UltraSSD_LRS', 'PremiumV2_LRS', 'StandardSSD_ZRS', 'Premium_ZRS'])
param skuName string = 'StandardSSD_LRS'

param tags object = {}

resource disk 'Microsoft.Compute/disks@2024-03-02' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: skuName
  }
  properties: {
    diskSizeGB: diskSizeGB
    creationData: {
      createOption: 'Empty'
    }
    encryption: {
      type: 'EncryptionAtRestWithPlatformKey'
    }
  }
}

output id string = disk.id
output name string = disk.name
```

## Application Code

### Python
Infrastructure -- transparent to application code. Managed disks appear as block devices (`/dev/sdc`, `D:\`) to the OS; applications use standard file I/O operations.

### C#
Infrastructure -- transparent to application code. Managed disks appear as block devices to the OS; applications use standard `System.IO` file operations.

### Node.js
Infrastructure -- transparent to application code. Managed disks appear as block devices to the OS; applications use standard `fs` module operations.

## Common Pitfalls

1. **Disk SKU determines IOPS/throughput** -- Standard_LRS (HDD) has 500 IOPS max. StandardSSD_LRS has 500-6000 IOPS depending on size. Premium_LRS has 120-20000 IOPS. Under-provisioned disks cause I/O bottlenecks.
2. **Disk size determines performance tier** -- Larger disks within the same SKU get higher IOPS/throughput baselines. A 32 GB Premium SSD (P4) gets 120 IOPS; a 256 GB (P15) gets 1100 IOPS.
3. **Zone must match VM** -- A disk in zone 1 cannot be attached to a VM in zone 2. Always deploy disks in the same zone as the target VM.
4. **Cannot resize down** -- Disk size can only be increased, never decreased. Over-provisioning wastes cost permanently.
5. **Detach before deleting** -- Deleting a disk attached to a running VM fails. Stop/deallocate the VM and detach the disk first.
6. **UltraSSD requires opt-in** -- Ultra disks require enabling `UltraSSDEnabled` on the VM and are only available in specific regions and zones.
7. **Encryption at host vs SSE** -- `EncryptionAtRestWithPlatformKey` (SSE) encrypts data at rest on the storage backend. For end-to-end encryption (including temp disks and caches), enable encryption at host on the VM.
8. **Snapshot cost** -- Snapshots are billed based on used size, not provisioned size. Frequent snapshots of large disks can accumulate significant storage costs.

## Production Backlog Items

- [ ] Upgrade to Premium SSD or Premium SSD v2 for production IOPS requirements
- [ ] Enable customer-managed key (CMK) encryption via Key Vault
- [ ] Configure Azure Backup with appropriate retention policies
- [ ] Enable encryption at host on VMs for end-to-end encryption
- [ ] Implement snapshot-based backup strategy with lifecycle management
- [ ] Enable zone-redundant storage (ZRS) SKU for cross-zone resilience
- [ ] Right-size disk SKU and size based on observed I/O patterns
- [ ] Plan disk bursting strategy for intermittent high-I/O workloads
