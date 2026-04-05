---
service_namespace: Microsoft.RecoveryServices/vaults/backupstorageconfig
display_name: Recovery Services Backup Storage Config
depends_on:
  - Microsoft.RecoveryServices/vaults
---

# Recovery Services Backup Storage Config

> Configures the storage redundancy type (LRS, GRS, ZRS) and cross-region restore settings for a Recovery Services vault's backup storage.

## When to Use
- Set storage replication type when creating a new vault (LRS for POC, GRS for production)
- Enable cross-region restore for disaster recovery scenarios
- Storage config should be set before any backup items are registered
- Changing replication type after backups exist requires data migration

## POC Defaults
- **Storage type**: LocallyRedundant (LRS — cheapest option for POC)
- **Cross-region restore**: Disabled (requires GRS)
- **Soft delete**: Enabled by default (14-day retention)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "backup_storage_config" {
  type      = "Microsoft.RecoveryServices/vaults/backupstorageconfig@2024-04-30-preview"
  name      = "vaultstorageconfig"
  parent_id = azapi_resource.recovery_vault.id

  body = {
    properties = {
      storageModelType         = "LocallyRedundant"
      crossRegionRestoreFlag   = false
      dedupState               = "Disabled"
    }
  }
}
```

### RBAC Assignment
```hcl
# Backup Contributor role on the vault allows storage config changes.
# Storage config is typically set once during vault provisioning.
```

## Bicep Patterns

### Basic Resource
```bicep
resource backupStorageConfig 'Microsoft.RecoveryServices/vaults/backupstorageconfig@2024-04-30-preview' = {
  parent: recoveryVault
  name: 'vaultstorageconfig'
  properties: {
    storageModelType: 'LocallyRedundant'
    crossRegionRestoreFlag: false
    dedupState: 'Disabled'
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
- **Resource name must be 'vaultstorageconfig'**: The resource name is always the literal string `vaultstorageconfig`. Using any other name causes a 404 error.
- **Set before protecting items**: Storage replication type should be configured before registering any backup items. Changing it afterward is complex and may require data reprotection.
- **Cross-region restore requires GRS**: The `crossRegionRestoreFlag` can only be `true` when `storageModelType` is `GeoRedundant`. Enabling it with LRS fails.
- **ZRS availability**: Zone-redundant storage (ZRS) is not available in all regions. Check regional availability before selecting ZRS.
- **Cost implications**: GRS costs approximately 2x LRS. For POC, LRS is sufficient. Plan for GRS in production for disaster recovery.
- **Immutability**: Once a vault has protected items, changing from GRS to LRS or vice versa may not be possible without unprotecting and reprotecting all items.

## Production Backlog Items
- Upgrade to GRS for cross-region disaster recovery
- Enable cross-region restore for critical workloads
- Soft delete and multi-user authorization for ransomware protection
- Backup storage cost optimization reviews
- Compliance reporting for backup storage redundancy
