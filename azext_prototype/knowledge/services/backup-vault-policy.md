---
service_namespace: Microsoft.DataProtection/backupVaults/backupPolicies
display_name: Backup Vault Policy
depends_on:
  - Microsoft.DataProtection/backupVaults
---

# Backup Vault Policy

> Defines backup schedule and retention rules for resources protected by an Azure Backup vault (newer service supporting Blobs, Disks, PostgreSQL, AKS, and other modern workloads).

## When to Use
- Backup Azure Managed Disks with configurable retention
- Backup Azure Blobs (operational or vaulted)
- Backup Azure Database for PostgreSQL servers
- Backup AKS clusters
- Different from Recovery Services vault policies — Backup vault uses the newer DataProtection API

## POC Defaults
- **Frequency**: Daily
- **Retention**: 30 days default retention rule
- **Data store**: Operational store (for blobs/disks) or Vault store (for vaulted backups)
- **Time zone**: UTC

## Terraform Patterns

### Basic Resource
```hcl
# Disk backup policy
resource "azapi_resource" "backup_vault_policy" {
  type      = "Microsoft.DataProtection/backupVaults/backupPolicies@2024-04-01"
  name      = var.policy_name
  parent_id = azapi_resource.backup_vault.id

  body = {
    properties = {
      datasourceTypes = ["Microsoft.Compute/disks"]
      objectType      = "BackupPolicy"
      policyRules = [
        {
          name       = "BackupDaily"
          objectType = "AzureBackupRule"
          backupParameters = {
            objectType      = "AzureBackupParams"
            backupType      = "Incremental"
          }
          trigger = {
            objectType = "ScheduleBasedTriggerContext"
            schedule = {
              repeatingTimeIntervals = ["R/2025-01-01T02:00:00+00:00/P1D"]
              timeZone               = "UTC"
            }
            taggingCriteria = [
              {
                isDefault       = true
                tagInfo         = { tagName = "Default" }
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
                duration   = "P30D"
              }
              sourceDataStore = {
                objectType    = "DataStoreInfoBase"
                dataStoreType = "OperationalStore"
              }
            }
          ]
        }
      ]
    }
  }
}
```

### RBAC Assignment
```hcl
# Backup Contributor role on the Backup vault allows policy management.
```

## Bicep Patterns

### Basic Resource
```bicep
param policyName string

resource backupPolicy 'Microsoft.DataProtection/backupVaults/backupPolicies@2024-04-01' = {
  parent: backupVault
  name: policyName
  properties: {
    datasourceTypes: ['Microsoft.Compute/disks']
    objectType: 'BackupPolicy'
    policyRules: [
      {
        name: 'BackupDaily'
        objectType: 'AzureBackupRule'
        backupParameters: {
          objectType: 'AzureBackupParams'
          backupType: 'Incremental'
        }
        trigger: {
          objectType: 'ScheduleBasedTriggerContext'
          schedule: {
            repeatingTimeIntervals: ['R/2025-01-01T02:00:00+00:00/P1D']
            timeZone: 'UTC'
          }
          taggingCriteria: [
            {
              isDefault: true
              tagInfo: { tagName: 'Default' }
              taggingPriority: 99
            }
          ]
        }
        dataStore: {
          objectType: 'DataStoreInfoBase'
          dataStoreType: 'OperationalStore'
        }
      }
      {
        name: 'RetentionDefault'
        objectType: 'AzureRetentionRule'
        isDefault: true
        lifecycles: [
          {
            deleteAfter: {
              objectType: 'AbsoluteDeleteOption'
              duration: 'P30D'
            }
            sourceDataStore: {
              objectType: 'DataStoreInfoBase'
              dataStoreType: 'OperationalStore'
            }
          }
        ]
      }
    ]
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
- **Complex policy rule structure**: Backup vault policies use a deeply nested object model with `objectType` discriminators. Missing or wrong `objectType` values cause cryptic validation errors.
- **datasourceTypes must match**: The `datasourceTypes` array must match the workload type exactly (e.g., `Microsoft.Compute/disks`, `Microsoft.Storage/storageAccounts/blobServices`).
- **ISO 8601 repeating intervals**: Schedule uses `R/datetime/interval` format (e.g., `R/2025-01-01T02:00:00+00:00/P1D` for daily). This format differs from Recovery Services vault schedules.
- **Different from Recovery Services**: Backup vault (DataProtection) and Recovery Services vault (RecoveryServices) are different services with different APIs. Don't mix them.
- **Tagging criteria required**: Even for simple policies, the `taggingCriteria` with a default tag is mandatory. Omitting it fails validation.

## Production Backlog Items
- Weekly and monthly retention rules with separate tags
- Vault store backup for long-term retention with cross-region
- Multiple data source support (blobs + disks in one vault)
- Backup instance monitoring and compliance reporting
- Cost optimization reviews for backup storage
