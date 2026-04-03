# Azure Automation
> Cloud-based automation service for process automation, configuration management, and update management using PowerShell and Python runbooks.

## When to Use

- Scheduled operational tasks (start/stop VMs, rotate keys, clean up resources)
- Configuration management with Azure Automation State Configuration (DSC)
- Runbook-based remediation triggered by Azure Monitor alerts
- Update management for OS patching across VMs
- Hybrid worker scenarios bridging on-premises and cloud automation
- NOT suitable for: event-driven real-time processing (use Azure Functions), CI/CD pipelines (use Azure DevOps/GitHub Actions), or complex workflow orchestration (use Logic Apps)

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Basic | Free tier allows 500 minutes/month of job runtime |
| Identity | System-assigned managed identity | For accessing Azure resources from runbooks |
| Public network access | Enabled | Disable for production with private endpoints |
| Runbook type | PowerShell 7.2 | Python 3.8 also supported |
| Encryption | Platform-managed keys | CMK available for production |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "automation_account" {
  type      = "Microsoft.Automation/automationAccounts@2023-11-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {
      sku = {
        name = "Basic"
      }
      publicNetworkAccess  = true  # Set false for production
      disableLocalAuth     = false # Set true for production (Entra-only auth)
      encryption = {
        keySource = "Microsoft.Automation"
      }
    }
  }

  tags = var.tags
}
```

### With Runbook

```hcl
resource "azapi_resource" "runbook" {
  type      = "Microsoft.Automation/automationAccounts/runbooks@2023-11-01"
  name      = var.runbook_name
  location  = var.location
  parent_id = azapi_resource.automation_account.id

  body = {
    properties = {
      runbookType  = "PowerShell72"
      logProgress  = true
      logVerbose   = false
      description  = var.runbook_description
      publishContentLink = {
        uri = var.runbook_script_uri  # URI to the .ps1 script
      }
    }
  }

  tags = var.tags
}
```

### With Schedule

```hcl
resource "azapi_resource" "schedule" {
  type      = "Microsoft.Automation/automationAccounts/schedules@2023-11-01"
  name      = var.schedule_name
  parent_id = azapi_resource.automation_account.id

  body = {
    properties = {
      frequency    = "Day"
      interval     = 1
      startTime    = var.start_time  # ISO 8601 format
      timeZone     = "UTC"
      description  = "Daily scheduled task"
    }
  }
}

resource "azapi_resource" "job_schedule" {
  type      = "Microsoft.Automation/automationAccounts/jobSchedules@2023-11-01"
  name      = var.job_schedule_guid  # Must be a GUID
  parent_id = azapi_resource.automation_account.id

  body = {
    properties = {
      runbook = {
        name = azapi_resource.runbook.name
      }
      schedule = {
        name = azapi_resource.schedule.name
      }
    }
  }
}
```

### RBAC Assignment

```hcl
# Grant the automation account's managed identity Contributor on a resource group
resource "azapi_resource" "automation_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.target_resource_group_id}${azapi_resource.automation_account.identity[0].principal_id}contributor")
  parent_id = var.target_resource_group_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/b24988ac-6180-42a0-ab88-20f7382dd24c"  # Contributor
      principalId      = azapi_resource.automation_account.identity[0].principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

```hcl
resource "azapi_resource" "automation_private_endpoint" {
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
            privateLinkServiceId = azapi_resource.automation_account.id
            groupIds             = ["DSCAndHybridWorker"]
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

Private DNS zone: `privatelink.azure-automation.net`

## Bicep Patterns

### Basic Resource

```bicep
param name string
param location string
param tags object = {}

resource automationAccount 'Microsoft.Automation/automationAccounts@2023-11-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    sku: {
      name: 'Basic'
    }
    publicNetworkAccess: true
    disableLocalAuth: false
    encryption: {
      keySource: 'Microsoft.Automation'
    }
  }
}

output id string = automationAccount.id
output name string = automationAccount.name
output principalId string = automationAccount.identity.principalId
```

### With Runbook

```bicep
param runbookName string
param runbookType string = 'PowerShell72'
param scriptUri string

resource runbook 'Microsoft.Automation/automationAccounts/runbooks@2023-11-01' = {
  parent: automationAccount
  name: runbookName
  location: location
  properties: {
    runbookType: runbookType
    logProgress: true
    logVerbose: false
    publishContentLink: {
      uri: scriptUri
    }
  }
}
```

### RBAC Assignment

```bicep
param targetResourceGroupId string

// Contributor role for automation managed identity
resource contributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(targetResourceGroupId, automationAccount.identity.principalId, 'b24988ac-6180-42a0-ab88-20f7382dd24c')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c')
    principalId: automationAccount.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Using classic Run As accounts | Deprecated since Sept 2023; certificates expire | Use system-assigned managed identity instead |
| Not assigning RBAC to managed identity | Runbooks fail with authorization errors at runtime | Grant appropriate roles to the automation account's identity |
| Free tier job minute limits | Jobs fail after 500 min/month exceeded | Monitor job runtime; upgrade to Basic for production |
| PowerShell version mismatch | Runbook cmdlets behave differently across PS versions | Explicitly specify `PowerShell72` runbook type |
| Storing credentials as Automation variables | Less secure, harder to rotate | Use managed identity or Key Vault references |
| Schedule time zone confusion | Jobs run at unexpected times | Always set `timeZone` explicitly (e.g., "UTC") |
| Not enabling logging on runbooks | Difficult to troubleshoot failed jobs | Set `logProgress = true` and review job streams |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Private endpoint | P1 | Deploy private endpoint and disable public network access |
| Disable local auth | P1 | Set `disableLocalAuth = true` to enforce Entra-only authentication |
| Customer-managed keys | P3 | Enable CMK encryption for automation account data |
| Hybrid runbook workers | P3 | Deploy hybrid workers for on-premises or cross-cloud automation |
| Webhook integration | P3 | Create webhooks for runbooks to enable external triggering |
| Diagnostic settings | P2 | Route automation logs to Log Analytics for monitoring |
| Source control integration | P2 | Connect runbooks to Git for version control and CI/CD |
| Update management | P3 | Enable update management for automated OS patching |
