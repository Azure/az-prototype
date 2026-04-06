---
service_namespace: Microsoft.Automation/automationAccounts/jobSchedules
display_name: Automation Job Schedule
depends_on:
  - Microsoft.Automation/automationAccounts
  - Microsoft.Automation/automationAccounts/runbooks
  - Microsoft.Automation/automationAccounts/schedules
---

# Automation Job Schedule

> Links an Automation runbook to a schedule, causing the runbook to execute automatically at the times defined by the schedule.

## When to Use
- Connect a schedule to a runbook for automated execution
- Pass parameters to a runbook on a scheduled basis
- Run the same runbook on different schedules with different parameters
- Every scheduled runbook execution requires this linking resource

## POC Defaults
- **Name**: Auto-generated GUID (required by the API)
- **Parameters**: Empty or task-specific
- **Run on**: Azure sandbox (not Hybrid Worker)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "job_schedule" {
  type      = "Microsoft.Automation/automationAccounts/jobSchedules@2023-11-01"
  name      = var.job_schedule_guid
  parent_id = azapi_resource.automation_account.id

  body = {
    properties = {
      runbook = {
        name = azapi_resource.runbook.name
      }
      schedule = {
        name = azapi_resource.schedule.name
      }
      parameters = {
        resourceGroupName = var.target_resource_group
        action            = "stop"
      }
    }
  }
}
```

### RBAC Assignment
```hcl
# Job schedule management inherits from the Automation Account RBAC.
# Automation Contributor role allows full job schedule management.
```

## Bicep Patterns

### Basic Resource
```bicep
param jobScheduleGuid string = newGuid()

resource jobSchedule 'Microsoft.Automation/automationAccounts/jobSchedules@2023-11-01' = {
  parent: automationAccount
  name: jobScheduleGuid
  properties: {
    runbook: {
      name: runbook.name
    }
    schedule: {
      name: schedule.name
    }
    parameters: {
      resourceGroupName: targetResourceGroup
      action: 'stop'
    }
  }
}
```

## Application Code

### Python
```python
from azure.identity import DefaultAzureCredential
from azure.mgmt.automation import AutomationClient
import uuid

credential = DefaultAzureCredential()
client = AutomationClient(credential, subscription_id)

job_schedule = client.job_schedule.create(
    resource_group_name=rg_name,
    automation_account_name=account_name,
    job_schedule_id=str(uuid.uuid4()),
    parameters={
        "properties": {
            "runbook": {"name": runbook_name},
            "schedule": {"name": schedule_name},
            "parameters": {"param1": "value1"}
        }
    }
)
print(f"Linked runbook '{runbook_name}' to schedule '{schedule_name}'")
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

// Job schedule linking via REST or SDK
```

### Node.js
```typescript
import { DefaultAzureCredential } from "@azure/identity";
import { AutomationClient } from "@azure/arm-automation";
import { v4 as uuidv4 } from "uuid";

const credential = new DefaultAzureCredential();
const client = new AutomationClient(credential, subscriptionId);

await client.jobSchedule.create(rgName, accountName, uuidv4(), {
  properties: {
    runbook: { name: runbookName },
    schedule: { name: scheduleName },
    parameters: { param1: "value1" },
  },
});
```

## Common Pitfalls
- **Name must be a GUID**: The job schedule resource name must be a valid GUID, not a human-readable name. Using a non-GUID name causes a 400 error.
- **One runbook per schedule link**: A job schedule links exactly one runbook to one schedule. To run multiple runbooks on the same schedule, create multiple job schedules.
- **Parameters are strings**: All parameter values are passed as strings, even if the runbook parameter type is int or bool. The runbook must handle type conversion.
- **Deletion before recreation**: You cannot update a job schedule — you must delete and recreate it. This is important for Terraform's lifecycle management.
- **Runbook must be published**: The runbook must be in the Published state before it can be linked to a schedule. Draft runbooks cannot be scheduled.

## Production Backlog Items
- Parameterized scheduling for different environments (dev, staging, prod)
- Hybrid Worker targeting for on-premises or network-restricted tasks
- Job schedule auditing and drift detection
- Alerting on job schedule failures or missed runs
