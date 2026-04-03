# Azure Recovery Services
> Unified vault for Azure Backup (VMs, SQL, Files, SAP HANA) and Azure Site Recovery (disaster recovery replication), providing centralized management of backup policies, replication, and restore operations.

## When to Use

- Backing up Azure VMs (full VM or disk-level)
- Backing up SQL Server in Azure VMs or Azure SQL managed instances
- Backing up Azure Files shares
- Backing up SAP HANA databases in VMs
- Azure Site Recovery for VM replication and disaster recovery failover
- On-premises to Azure disaster recovery
- NOT suitable for: Azure Managed Disk snapshots, Blob Storage backup, or PostgreSQL Flexible Server backup (use Backup Vault instead)

**Key distinction**: Recovery Services vault (`Microsoft.RecoveryServices/vaults`) handles classic workloads. Backup Vault (`Microsoft.DataProtection/backupVaults`) handles newer workloads. Check the support matrix for your specific workload.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Storage type | LocallyRedundant | GeoRedundant for production |
| Soft delete | Enabled (14 days) | Default; enhanced soft delete available |
| Cross-region restore | Disabled | Requires GeoRedundant storage |
| Identity | System-assigned | For RBAC-based access to protected resources |
| Immutability | Disabled | Enable for production compliance |
| Public network access | Enabled | Use private endpoints in production |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "recovery_vault" {
  type      = "Microsoft.RecoveryServices/vaults@2024-04-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type = "SystemAssigned"
  }

  body = {
    sku = {
      name = "Standard"
    }
    properties = {
      publicNetworkAccess = "Enabled"  # Disable for production
      securitySettings = {
        softDeleteSettings = {
          softDeleteState                         = "Enabled"
          softDeleteRetentionPeriodInDays         = 14
          enhancedSecurityState                    = "Enabled"
        }
      }
    }
  }

  tags = var.tags
}
```

### Storage Replication Configuration

```hcl
resource "azapi_resource" "vault_storage_config" {
  type      = "Microsoft.RecoveryServices/vaults/backupstorageconfig@2024-04-01"
  name      = "vaultstorageconfig"
  parent_id = azapi_resource.recovery_vault.id

  body = {
    properties = {
      storageModelType     = "LocallyRedundant"  # GeoRedundant for production
      crossRegionRestoreFlag = false              # true requires GeoRedundant
    }
  }
}
```

### VM Backup Policy

```hcl
resource "azapi_resource" "vm_backup_policy" {
  type      = "Microsoft.RecoveryServices/vaults/backupPolicies@2024-04-01"
  name      = var.policy_name
  parent_id = azapi_resource.recovery_vault.id

  body = {
    properties = {
      backupManagementType = "AzureIaasVM"
      instantRpRetentionRangeInDays = 2
      schedulePolicy = {
        schedulePolicyType    = "SimpleSchedulePolicy"
        scheduleRunFrequency  = "Daily"
        scheduleRunTimes      = ["2024-01-01T02:00:00Z"]
      }
      retentionPolicy = {
        retentionPolicyType = "LongTermRetentionPolicy"
        dailySchedule = {
          retentionTimes    = ["2024-01-01T02:00:00Z"]
          retentionDuration = {
            count        = 7  # 7-day retention for POC
            durationType = "Days"
          }
        }
      }
      timeZone = "UTC"
    }
  }
}
```

### RBAC Assignment

```hcl
# Grant the vault identity VM Backup Contributor on the target resource group
resource "azapi_resource" "backup_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.vm_resource_group_id}${azapi_resource.recovery_vault.identity[0].principal_id}backup-contributor")
  parent_id = var.vm_resource_group_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/5e467623-bb1f-42f4-a55d-6e525e11384b"  # Backup Contributor
      principalId      = azapi_resource.recovery_vault.identity[0].principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Grant VM Contributor to allow snapshot operations
resource "azapi_resource" "vm_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.vm_resource_group_id}${azapi_resource.recovery_vault.identity[0].principal_id}vm-contributor")
  parent_id = var.vm_resource_group_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/9980e02c-c2be-4d73-94e8-173b1dc7cf3c"  # Virtual Machine Contributor
      principalId      = azapi_resource.recovery_vault.identity[0].principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

```hcl
resource "azapi_resource" "vault_private_endpoint" {
  count     = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints@2023-11-01"
  name      = "pe-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      subnet = {
        id = var.subnet_id
      }
      privateLinkServiceConnections = [
        {
          name = "psc-${var.name}"
          properties = {
            privateLinkServiceId = azapi_resource.recovery_vault.id
            groupIds             = ["AzureBackup"]
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

Private DNS zones: `privatelink.<geo>.backup.windowsazure.com` (varies by region)

## Bicep Patterns

### Basic Resource

```bicep
param name string
param location string
param tags object = {}

resource recoveryVault 'Microsoft.RecoveryServices/vaults@2024-04-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publicNetworkAccess: 'Enabled'
    securitySettings: {
      softDeleteSettings: {
        softDeleteState: 'Enabled'
        softDeleteRetentionPeriodInDays: 14
        enhancedSecurityState: 'Enabled'
      }
    }
  }
}

output id string = recoveryVault.id
output name string = recoveryVault.name
output principalId string = recoveryVault.identity.principalId
```

### VM Backup Policy

```bicep
param policyName string

resource backupPolicy 'Microsoft.RecoveryServices/vaults/backupPolicies@2024-04-01' = {
  parent: recoveryVault
  name: policyName
  properties: {
    backupManagementType: 'AzureIaasVM'
    instantRpRetentionRangeInDays: 2
    schedulePolicy: {
      schedulePolicyType: 'SimpleSchedulePolicy'
      scheduleRunFrequency: 'Daily'
      scheduleRunTimes: ['2024-01-01T02:00:00Z']
    }
    retentionPolicy: {
      retentionPolicyType: 'LongTermRetentionPolicy'
      dailySchedule: {
        retentionTimes: ['2024-01-01T02:00:00Z']
        retentionDuration: {
          count: 7
          durationType: 'Days'
        }
      }
    }
    timeZone: 'UTC'
  }
}
```

### RBAC Assignment

```bicep
param vmResourceGroupId string
param principalId string

// Backup Contributor
resource backupContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vmResourceGroupId, principalId, '5e467623-bb1f-42f4-a55d-6e525e11384b')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e467623-bb1f-42f4-a55d-6e525e11384b')
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Confusing Recovery Services vault with Backup Vault | Wrong vault type; deployment or backup jobs fail | Use Recovery Services for VMs/SQL/Files; Backup Vault for disks/blobs/PostgreSQL |
| Changing storage type after first backup | Cannot change redundancy after backups begin | Set storage type before enabling any backup |
| Missing RBAC for vault identity | Backup jobs fail with access denied | Grant Backup Contributor + VM Contributor before configuring protection |
| Not testing restore procedure | Backup works but restore fails in emergency | Regularly test restore to validate backup integrity |
| Soft delete blocking vault deletion | Cannot delete vault with soft-deleted items | Disable soft delete, then undelete and delete items before vault removal |
| LRS in production | No cross-region protection | Use GRS for production to survive regional failures |
| Storage config after backup | Storage redundancy cannot be changed post-backup | Configure `backupstorageconfig` before enabling any protection |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Geo-redundant storage | P1 | Switch to GeoRedundant storage for cross-region resilience |
| Cross-region restore | P2 | Enable CRR for disaster recovery to paired region |
| Private endpoints | P1 | Deploy private endpoints and disable public network access |
| Immutability | P1 | Enable vault immutability for ransomware protection and compliance |
| Multi-user authorization | P2 | Require Resource Guard approval for critical operations (disable soft delete, stop protection) |
| Monitoring and alerts | P2 | Configure Azure Monitor alerts for backup job failures and RPO violations |
| Long-term retention | P3 | Configure weekly, monthly, and yearly retention policies |
| Azure Site Recovery | P2 | Enable ASR for VM replication and disaster recovery failover |
| Backup reports | P3 | Enable Backup Reports via Log Analytics for compliance tracking |
| Encryption with CMK | P3 | Enable customer-managed key encryption for backup data |
