---
service_namespace: Microsoft.Automation/automationAccounts/schedules
display_name: Automation Schedule
depends_on:
  - Microsoft.Automation/automationAccounts
---

# Automation Schedule

> A time-based trigger definition for Azure Automation that specifies when and how often runbooks should execute.

## When to Use
- Run operational tasks on a recurring basis (daily cleanup, weekly reports)
- Schedule maintenance windows (start/stop VMs on business hours)
- One-time future execution of a runbook
- Combine with job schedules to link schedules to specific runbooks

## POC Defaults
- **Frequency**: Day (daily execution)
- **Interval**: 1 (every day)
- **Time zone**: UTC
- **Start time**: Next day at 02:00 UTC (avoids immediate execution)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "schedule" {
  type      = "Microsoft.Automation/automationAccounts/schedules@2023-11-01"
  name      = var.schedule_name
  parent_id = azapi_resource.automation_account.id

  body = {
    properties = {
      description      = var.description
      startTime        = var.start_time
      frequency        = "Day"
      interval         = 1
      timeZone         = "UTC"
      expiryTime       = "9999-12-31T23:59:59+00:00"
      advancedSchedule = {}
    }
  }
}
```

### RBAC Assignment
```hcl
# Schedule management inherits from the Automation Account RBAC.
# Automation Contributor role allows full schedule management.
```

## Bicep Patterns

### Basic Resource
```bicep
param scheduleName string
param startTime string

resource schedule 'Microsoft.Automation/automationAccounts/schedules@2023-11-01' = {
  parent: automationAccount
  name: scheduleName
  properties: {
    description: 'Daily operational task schedule'
    startTime: startTime
    frequency: 'Day'
    interval: 1
    timeZone: 'UTC'
    expiryTime: '9999-12-31T23:59:59+00:00'
  }
}

output scheduleName string = schedule.name
```

## Application Code

### Python
```python
from azure.identity import DefaultAzureCredential
from azure.mgmt.automation import AutomationClient

credential = DefaultAzureCredential()
client = AutomationClient(credential, subscription_id)

schedule = client.schedule.create_or_update(
    resource_group_name=rg_name,
    automation_account_name=account_name,
    schedule_name="daily-cleanup",
    parameters={
        "properties": {
            "startTime": "2025-01-01T02:00:00+00:00",
            "frequency": "Day",
            "interval": 1,
            "timeZone": "UTC"
        }
    }
)
print(f"Schedule: {schedule.name}, Next run: {schedule.next_run}")
```

### C#
```csharp
using Azure.Identity;
using Azure.ResourceManager;
using Azure.ResourceManager.Automation;

var credential = new DefaultAzureCredential();
var client = new ArmClient(credential);

var account = client.GetAutomationAccountResource(
    AutomationAccountResource.CreateResourceIdentifier(subscriptionId, rgName, accountName));
var schedules = account.GetAutomationSchedules();

await schedules.CreateOrUpdateAsync(Azure.WaitUntil.Completed, "daily-cleanup",
    new AutomationScheduleCreateOrUpdateContent("daily-cleanup",
        DateTimeOffset.UtcNow.AddDays(1), AutomationScheduleFrequency.Day) { Interval = 1 });
```

### Node.js
```typescript
import { DefaultAzureCredential } from "@azure/identity";
import { AutomationClient } from "@azure/arm-automation";

const credential = new DefaultAzureCredential();
const client = new AutomationClient(credential, subscriptionId);

const schedule = await client.schedule.createOrUpdate(rgName, accountName, "daily-cleanup", {
  properties: {
    startTime: new Date(Date.now() + 86400000).toISOString(),
    frequency: "Day",
    interval: 1,
    timeZone: "UTC",
  },
});
console.log(`Schedule: ${schedule.name}, Next run: ${schedule.nextRun}`);
```

## Common Pitfalls
- **Start time must be in the future**: The API rejects schedules with a start time in the past. Always compute a future timestamp.
- **Schedule alone doesn't run anything**: A schedule must be linked to a runbook via a job schedule resource to actually trigger execution.
- **Time zone strings**: Use Windows time zone IDs (e.g., `Eastern Standard Time`), not IANA (e.g., `America/New_York`). UTC is the safest default.
- **Expired schedules cannot be reactivated**: Once a schedule passes its `expiryTime`, it must be recreated. Use a far-future expiry for indefinite schedules.
- **One-time vs recurring**: Set `frequency` to `OneTime` for single execution. There's no way to convert a one-time schedule to recurring.

## Production Backlog Items
- Advanced schedule patterns (monthly on specific days, weekly on weekdays)
- Schedule monitoring and alerting for missed runs
- Schedule disable/enable automation for maintenance windows
- Time zone alignment with business operating hours
