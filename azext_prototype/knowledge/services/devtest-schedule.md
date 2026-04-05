---
service_namespace: Microsoft.DevTestLab/schedules
display_name: Auto-Shutdown Schedule
depends_on:
  - Microsoft.Compute/virtualMachines
---

# Auto-Shutdown Schedule

> Scheduled action (typically auto-shutdown) applied to Azure VMs to automatically stop compute at a specified time, reducing costs for non-production environments.

## When to Use
- **Cost optimization** -- automatically shut down dev/test VMs outside business hours
- **POC environments** -- prevent forgotten VMs from running 24/7
- **Compliance** -- enforce shutdown policies for non-production workloads
- Applies to individual VMs; for scale set schedules, use autoscale settings instead

Despite the `DevTestLab` namespace, auto-shutdown schedules work on any Azure VM, not just DevTest Labs VMs. The resource is deployed as a child of the resource group but references a specific VM.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Name | `shutdown-computevm-{vmName}` | Must follow this exact naming convention |
| Task type | ComputeVmShutdownTask | Only supported task type |
| Daily recurrence | 19:00 | 7 PM local time |
| Time zone | User's time zone | e.g., `Eastern Standard Time` |
| Status | Enabled | Active by default |
| Notification | 30 minutes before | Email/webhook before shutdown |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "auto_shutdown" {
  type      = "Microsoft.DevTestLab/schedules@2018-09-15"
  name      = "shutdown-computevm-${var.vm_name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      status           = "Enabled"
      taskType         = "ComputeVmShutdownTask"
      dailyRecurrence = {
        time = var.shutdown_time  # e.g., "1900" (24-hour format, no colon)
      }
      timeZoneId       = var.time_zone  # e.g., "Eastern Standard Time"
      targetResourceId = var.vm_id
      notificationSettings = {
        status        = "Enabled"
        timeInMinutes = 30
        emailRecipient = var.notification_email
        notificationLocale = "en"
      }
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# DevTest Labs User role on the resource group for schedule management
resource "azapi_resource" "devtest_user" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.resource_group_id}-${var.principal_id}-devtest-user")
  parent_id = var.resource_group_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/76283e04-6283-4c54-8f91-bcf1374a3c64"
      principalId      = var.principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('VM name (used in schedule resource name)')
param vmName string

@description('VM resource ID')
param vmId string

@description('Azure region')
param location string = resourceGroup().location

@description('Shutdown time in 24-hour format (e.g., 1900)')
param shutdownTime string = '1900'

@description('Time zone ID')
param timeZoneId string = 'Eastern Standard Time'

@description('Notification email')
param notificationEmail string = ''

param tags object = {}

resource autoShutdown 'Microsoft.DevTestLab/schedules@2018-09-15' = {
  name: 'shutdown-computevm-${vmName}'
  location: location
  tags: tags
  properties: {
    status: 'Enabled'
    taskType: 'ComputeVmShutdownTask'
    dailyRecurrence: {
      time: shutdownTime
    }
    timeZoneId: timeZoneId
    targetResourceId: vmId
    notificationSettings: {
      status: notificationEmail != '' ? 'Enabled' : 'Disabled'
      timeInMinutes: 30
      emailRecipient: notificationEmail
      notificationLocale: 'en'
    }
  }
}

output id string = autoShutdown.id
```

## Application Code

### Python
Infrastructure -- transparent to application code. Auto-shutdown operates at the VM level; applications running on the VM are stopped along with the VM.

### C#
Infrastructure -- transparent to application code. Auto-shutdown operates at the VM level; applications running on the VM are stopped along with the VM.

### Node.js
Infrastructure -- transparent to application code. Auto-shutdown operates at the VM level; applications running on the VM are stopped along with the VM.

## Common Pitfalls

1. **Name must follow exact convention** -- The resource name must be `shutdown-computevm-{vmName}` where `{vmName}` matches the VM name exactly. Any other name silently fails.
2. **Time format has no colon** -- The time is `"1900"` not `"19:00"`. Using a colon format causes deployment failure.
3. **Time zone ID must be Windows format** -- Use Windows time zone IDs (`Eastern Standard Time`, not `America/New_York`). Invalid IDs cause the schedule to never fire.
4. **Auto-shutdown does not auto-start** -- VMs are stopped but not deallocated by default. They still incur compute charges. Use the `deallocate` approach or Azure Automation for auto-start.
5. **Notification delay** -- The notification fires 30 minutes before shutdown by default. Users can delay shutdown from the notification email, but this is a one-time delay, not a permanent skip.
6. **Parent is resource group, not VM** -- Despite being conceptually tied to a VM, the schedule resource's parent is the resource group. The VM is referenced via `targetResourceId`.
7. **One schedule per VM** -- Each VM can have only one auto-shutdown schedule. Deploying a second overwrites the first.

## Production Backlog Items

- [ ] Configure auto-start schedules via Azure Automation for morning startup
- [ ] Adjust shutdown time based on actual usage patterns
- [ ] Add webhook notifications for integration with Slack/Teams
- [ ] Implement Azure Policy to enforce auto-shutdown on all dev/test VMs
- [ ] Configure different schedules for different environments (dev vs staging)
- [ ] Set up exception process for VMs that need to run 24/7
