---
service_namespace: Microsoft.Automation/automationAccounts/runbooks
display_name: Automation Runbook
depends_on:
  - Microsoft.Automation/automationAccounts
---

# Automation Runbook

> A script (PowerShell, Python, or graphical) hosted in an Azure Automation Account that can be executed on-demand, on a schedule, or triggered by webhooks/alerts.

## When to Use
- Automate operational tasks (start/stop VMs, rotate secrets, clean up resources)
- Remediation actions triggered by Azure Monitor alerts
- Scheduled maintenance scripts (database cleanup, log rotation)
- Cross-resource orchestration that doesn't need real-time execution
- NOT suitable for: sub-second event processing (use Functions), CI/CD (use GitHub Actions/ADO)

## POC Defaults
- **Runbook type**: PowerShell72 (PowerShell 7.2 runtime)
- **State**: Published (New → Published on first publish)
- **Log verbose**: false
- **Log progress**: false

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "runbook" {
  type      = "Microsoft.Automation/automationAccounts/runbooks@2023-11-01"
  name      = var.runbook_name
  parent_id = azapi_resource.automation_account.id
  location  = var.location

  body = {
    properties = {
      runbookType  = "PowerShell72"
      logVerbose   = false
      logProgress  = false
      description  = var.description
      draft        = {}
      publishContentLink = {
        uri = var.script_uri
      }
    }
  }
}
```

### RBAC Assignment
```hcl
# Automation Operator role allows starting runbooks without editing them.
# Automation Contributor role allows full runbook management.
resource "azapi_resource" "runbook_operator" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = var.role_assignment_name
  parent_id = azapi_resource.automation_account.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/d3881f73-407a-4167-8283-e981cbba0404"
      principalId      = var.operator_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource
```bicep
param runbookName string
param location string
param scriptUri string

resource runbook 'Microsoft.Automation/automationAccounts/runbooks@2023-11-01' = {
  parent: automationAccount
  name: runbookName
  location: location
  properties: {
    runbookType: 'PowerShell72'
    logVerbose: false
    logProgress: false
    description: 'Automated operational task'
    publishContentLink: {
      uri: scriptUri
    }
  }
}

output runbookName string = runbook.name
```

## Application Code

### Python
```python
from azure.identity import DefaultAzureCredential
from azure.mgmt.automation import AutomationClient

credential = DefaultAzureCredential()
client = AutomationClient(credential, subscription_id)

# Start a runbook job
job = client.job.create(
    resource_group_name=rg_name,
    automation_account_name=account_name,
    job_name=str(uuid.uuid4()),
    parameters={
        "properties": {
            "runbook": {"name": runbook_name},
            "parameters": {"param1": "value1"}
        }
    }
)
print(f"Job status: {job.status}")
```

### C#
```csharp
using Azure.Identity;
using Azure.ResourceManager;
using Azure.ResourceManager.Automation;

var credential = new DefaultAzureCredential();
var client = new ArmClient(credential);

var automationAccount = client.GetAutomationAccountResource(
    AutomationAccountResource.CreateResourceIdentifier(subscriptionId, rgName, accountName));

var runbook = await automationAccount.GetAutomationRunbookAsync(runbookName);
// Trigger via REST or use the Job resource
```

### Node.js
```typescript
import { DefaultAzureCredential } from "@azure/identity";
import { AutomationClient } from "@azure/arm-automation";

const credential = new DefaultAzureCredential();
const client = new AutomationClient(credential, subscriptionId);

const job = await client.job.create(rgName, accountName, jobName, {
  properties: {
    runbook: { name: runbookName },
    parameters: { param1: "value1" },
  },
});
console.log(`Job status: ${job.status}`);
```

## Common Pitfalls
- **Published vs Draft**: Runbooks start in Draft state. They must be published before they can be executed. The `publishContentLink` approach publishes automatically.
- **Script URI accessibility**: The `publishContentLink.uri` must be publicly accessible or use a SAS token. Private blob storage URIs without SAS fail silently.
- **Module dependencies**: PowerShell runbooks that import modules (Az.Accounts, etc.) require those modules to be installed in the Automation Account first.
- **Location must match**: The runbook location must match the parent Automation Account location.
- **Execution limits**: Runbook jobs have a 3-hour fair-share limit on cloud sandboxes. Use Hybrid Runbook Workers for long-running tasks.

## Production Backlog Items
- Source control integration for runbook versioning
- Hybrid Runbook Worker for on-premises or long-running tasks
- Webhook triggers for event-driven runbook execution
- Error handling and retry logic within runbook scripts
- Monitoring and alerting on runbook job failures
