# Azure Backup Vault
> Purpose-built vault for newer Azure Backup workloads including Azure Disks, Azure Blobs, Azure Database for PostgreSQL, and Azure Kubernetes Service, using Backup policies with immutability and soft delete support.

## When to Use

- Backing up Azure Managed Disks (snapshot-based)
- Backing up Azure Blob Storage (operational and vaulted backups)
- Backing up Azure Database for PostgreSQL Flexible Server
- Backing up AKS clusters
- When you need immutable vaults for ransomware protection
- NOT suitable for: VM backups, SQL Server in VMs, Azure Files, or SAP HANA (use Recovery Services vault instead)

**Key distinction**: Backup Vault (`Microsoft.DataProtection/backupVaults`) supports newer workloads. Recovery Services vault (`Microsoft.RecoveryServices/vaults`) supports classic workloads (VMs, SQL, Files). Some workloads overlap; check current support matrix.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Storage setting | LocallyRedundant | GeoRedundant for production |
| Soft delete | Enabled (14 days) | Built-in, always enabled |
| Immutability | Disabled | Enable for production compliance |
| Cross-region restore | Disabled | Enable with GeoRedundant storage |
| Identity | System-assigned | Required for backup operations |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "backup_vault" {
  type      = "Microsoft.DataProtection/backupVaults@2024-04-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {
      storageSettings = [
        {
          datastoreType = "VaultStore"
          type          = "LocallyRedundant"  # GeoRedundant for production
        }
      ]
      securitySettings = {
        softDeleteSettings = {
          state               = "On"
          retentionDurationInDays = 14
        }
      }
    }
  }

  tags = var.tags
}
```

### Backup Policy for Managed Disks

```hcl
resource "azapi_resource" "disk_backup_policy" {
  type      = "Microsoft.DataProtection/backupVaults/backupPolicies@2024-04-01"
  name      = var.policy_name
  parent_id = azapi_resource.backup_vault.id

  body = {
    properties = {
      policyRules = [
        {
          name         = "BackupDaily"
          objectType   = "AzureBackupRule"
          backupParameters = {
            objectType       = "AzureBackupParams"
            backupType       = "Incremental"
          }
          trigger = {
            objectType       = "ScheduleBasedTriggerContext"
            schedule = {
              repeatingTimeIntervals = ["R/2024-01-01T02:00:00+00:00/P1D"]
            }
            taggingCriteria = [
              {
                isDefault   = true
                tagInfo = {
                  tagName = "Default"
                }
                taggingPriority = 99
              }
            ]
          }
          dataStore = {
            objectType    = "DataStoreInfoBase"
            dataStoreType = "OperationalStore"
          }
        },
        {
          name       = "RetentionDefault"
          objectType = "AzureRetentionRule"
          isDefault  = true
          lifecycles = [
            {
              deleteAfter = {
                objectType = "AbsoluteDeleteOption"
                duration   = "P7D"  # 7-day retention for POC
              }
              sourceDataStore = {
                objectType    = "DataStoreInfoBase"
                dataStoreType = "OperationalStore"
              }
            }
          ]
        }
      ]
      datasourceTypes = ["Microsoft.Compute/disks"]
      objectType      = "BackupPolicy"
    }
  }
}
```

### RBAC Assignment

```hcl
# Grant Backup Vault identity Disk Snapshot Contributor on the disk resource group
resource "azapi_resource" "snapshot_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.disk_resource_group_id}${azapi_resource.backup_vault.identity[0].principal_id}disk-snapshot")
  parent_id = var.disk_resource_group_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/7efff54f-a5b4-42b5-a1c5-5411624893ce"  # Disk Snapshot Contributor
      principalId      = azapi_resource.backup_vault.identity[0].principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Grant Backup Vault identity Disk Backup Reader on the disk
resource "azapi_resource" "disk_backup_reader" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.disk_id}${azapi_resource.backup_vault.identity[0].principal_id}disk-backup-reader")
  parent_id = var.disk_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/3e5e47e6-65f7-47ef-90b5-e5dd4d455f24"  # Disk Backup Reader
      principalId      = azapi_resource.backup_vault.identity[0].principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
param name string
param location string
param tags object = {}

resource backupVault 'Microsoft.DataProtection/backupVaults@2024-04-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    storageSettings: [
      {
        datastoreType: 'VaultStore'
        type: 'LocallyRedundant'
      }
    ]
    securitySettings: {
      softDeleteSettings: {
        state: 'On'
        retentionDurationInDays: 14
      }
    }
  }
}

output id string = backupVault.id
output name string = backupVault.name
output principalId string = backupVault.identity.principalId
```

### RBAC Assignment

```bicep
param diskResourceGroupId string
param principalId string

// Disk Snapshot Contributor
resource snapshotContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(diskResourceGroupId, principalId, '7efff54f-a5b4-42b5-a1c5-5411624893ce')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7efff54f-a5b4-42b5-a1c5-5411624893ce')
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Confusing Backup Vault with Recovery Services vault | Wrong vault type for the workload; deployment fails | Use Backup Vault for disks/blobs/PostgreSQL; Recovery Services for VMs/SQL/Files |
| Missing RBAC on source resources | Backup jobs fail with permission errors | Grant Disk Snapshot Contributor + Disk Backup Reader before configuring backup |
| LocallyRedundant in production | No cross-region protection against regional outage | Use GeoRedundant storage for production workloads |
| Not setting retention policy | Default retention may not meet compliance requirements | Explicitly configure retention duration in backup policy |
| Immutability lock misconfiguration | Cannot delete backups even if needed (locked state) | Start with unlocked immutability; lock only after validation |
| Snapshot resource group not specified | Snapshots created in source disk RG, cluttering it | Specify a dedicated snapshot resource group in backup instance |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Geo-redundant storage | P1 | Switch to GeoRedundant storage for cross-region resilience |
| Immutable vault | P1 | Enable vault immutability for ransomware protection |
| Cross-region restore | P2 | Enable cross-region restore for disaster recovery scenarios |
| Monitoring and alerts | P2 | Configure backup alerts via Azure Monitor for failed backup jobs |
| Multi-user authorization | P2 | Require Resource Guard approval for critical backup operations |
| Extended retention | P3 | Configure long-term retention policies for compliance (monthly/yearly) |
| Backup reports | P3 | Enable Backup Reports via Log Analytics for compliance auditing |
| Cost optimization | P3 | Review and right-size backup frequency and retention based on RPO requirements |
