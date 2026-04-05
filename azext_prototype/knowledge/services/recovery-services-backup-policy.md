---
service_namespace: Microsoft.RecoveryServices/vaults/backupPolicies
display_name: Recovery Services Backup Policy
depends_on:
  - Microsoft.RecoveryServices/vaults
---

# Recovery Services Backup Policy

> Defines the backup schedule, retention rules, and backup type (full, incremental, log) for resources protected by an Azure Recovery Services vault.

## When to Use
- Define backup frequency for Azure VMs (daily, weekly)
- Configure retention policies (daily, weekly, monthly, yearly)
- Set up backup policies for SQL Server in VMs, SAP HANA, or Azure Files
- Customize backup windows to avoid peak hours
- Every protected resource needs an associated backup policy

## POC Defaults
- **Schedule**: Daily at 02:00 UTC
- **Instant restore retention**: 2 days
- **Daily retention**: 30 days
- **Weekly retention**: Disabled
- **Backup type**: DefaultPolicy equivalent

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "backup_policy_vm" {
  type      = "Microsoft.RecoveryServices/vaults/backupPolicies@2024-04-30-preview"
  name      = var.policy_name
  parent_id = azapi_resource.recovery_vault.id

  body = {
    properties = {
      backupManagementType = "AzureIaasVM"
      instantRpRetentionRangeInDays = 2
      schedulePolicy = {
        schedulePolicyType   = "SimpleSchedulePolicy"
        scheduleRunFrequency = "Daily"
        scheduleRunTimes     = ["2025-01-01T02:00:00Z"]
      }
      retentionPolicy = {
        retentionPolicyType = "LongTermRetentionPolicy"
        dailySchedule = {
          retentionTimes    = ["2025-01-01T02:00:00Z"]
          retentionDuration = {
            count        = 30
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
# Backup Contributor role allows managing backup policies.
# Scoped at the Recovery Services vault level.
resource "azapi_resource" "backup_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = var.role_assignment_name
  parent_id = azapi_resource.recovery_vault.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/5e467623-bb1f-42f4-a55d-6e525e11384b"
      principalId      = var.operator_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource
```bicep
param policyName string

resource backupPolicy 'Microsoft.RecoveryServices/vaults/backupPolicies@2024-04-30-preview' = {
  parent: recoveryVault
  name: policyName
  properties: {
    backupManagementType: 'AzureIaasVM'
    instantRpRetentionRangeInDays: 2
    schedulePolicy: {
      schedulePolicyType: 'SimpleSchedulePolicy'
      scheduleRunFrequency: 'Daily'
      scheduleRunTimes: ['2025-01-01T02:00:00Z']
    }
    retentionPolicy: {
      retentionPolicyType: 'LongTermRetentionPolicy'
      dailySchedule: {
        retentionTimes: ['2025-01-01T02:00:00Z']
        retentionDuration: {
          count: 30
          durationType: 'Days'
        }
      }
    }
    timeZone: 'UTC'
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
- **backupManagementType must match workload**: Use `AzureIaasVM` for VMs, `AzureSql` for SQL in VMs, `AzureStorage` for Azure Files. Wrong type causes policy creation to fail.
- **Schedule times are date-time, not time-only**: The `scheduleRunTimes` array requires full ISO 8601 datetime strings. Only the time portion matters; the date is ignored.
- **Default policy exists**: Recovery Services vaults create a `DefaultPolicy` automatically. You can modify it or create additional policies.
- **Retention hierarchy**: Weekly retention must be >= daily, monthly >= weekly, yearly >= monthly. Violations cause validation errors.
- **Enhanced policy vs standard**: Enhanced policies support multiple backups per day and longer instant restore retention, but require the vault to be configured for enhanced backup.

## Production Backlog Items
- Weekly and monthly retention policies for compliance
- Enhanced backup policy for multiple daily backups
- Cross-region backup (GRS vault) for disaster recovery
- Policy assignment automation for new VMs
- Backup compliance monitoring and reporting
