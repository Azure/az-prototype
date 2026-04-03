# Azure Functions
> Serverless compute platform for running event-driven code without managing infrastructure, supporting multiple languages and a rich set of input/output bindings.

## When to Use

- **Event-driven processing** -- respond to HTTP requests, queue messages, blob uploads, timer schedules, Event Grid events
- **Serverless APIs** -- lightweight REST endpoints without managing web servers
- **Background processing** -- async tasks like image resizing, email sending, data transformation
- **Integration glue** -- connect services via bindings (Service Bus, Event Hubs, Cosmos DB, Storage)
- **Scheduled jobs** -- timer-triggered functions for periodic tasks (cron expressions)
- **Stream processing** -- process events from Event Hubs or IoT Hub with automatic scaling

Prefer Functions over App Service for event-driven, short-lived workloads. Use App Service for long-running web applications or when you need always-on compute. Use Container Apps for container-based microservices that need KEDA scaling.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Plan | Consumption (Y1) | Pay-per-execution; free grant of 1M executions/month |
| Plan (with VNet) | Elastic Premium (EP1) | VNet integration, no cold start, VNET triggers |
| OS | Linux | Preferred for Python/Node; Windows for .NET in-process |
| Runtime | Python 3.11 / Node 20 / .NET 8 (isolated) | Match project requirements |
| Managed identity | User-assigned | Shared with other app resources |
| HTTPS Only | true | Enforced by policy |
| Minimum TLS | 1.2 | Enforced by policy |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "function_plan" {
  type      = "Microsoft.Web/serverfarms@2023-12-01"
  name      = var.plan_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    kind = "functionapp"
    sku = {
      name = "Y1"
      tier = "Dynamic"
    }
    properties = {
      reserved = true  # Required for Linux
    }
  }

  tags = var.tags
}

resource "azapi_resource" "function_app" {
  type      = "Microsoft.Web/sites@2023-12-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type         = "UserAssigned"
    identity_ids = [var.managed_identity_id]
  }

  body = {
    kind = "functionapp,linux"
    properties = {
      serverFarmId = azapi_resource.function_plan.id
      httpsOnly    = true
      siteConfig = {
        minTlsVersion       = "1.2"
        linuxFxVersion       = "PYTHON|3.11"  # or NODE|20, DOTNET-ISOLATED|8.0
        appSettings = [
          {
            name  = "FUNCTIONS_EXTENSION_VERSION"
            value = "~4"
          },
          {
            name  = "FUNCTIONS_WORKER_RUNTIME"
            value = "python"  # or "node", "dotnet-isolated"
          },
          {
            name  = "AzureWebJobsStorage__accountName"
            value = var.storage_account_name  # Identity-based connection (no key)
          },
          {
            name  = "AZURE_CLIENT_ID"
            value = var.managed_identity_client_id
          }
        ]
      }
    }
  }

  tags = var.tags

  response_export_values = ["properties.defaultHostName"]
}
```

### RBAC Assignment

```hcl
# Function App's identity needs Storage Blob Data Owner for AzureWebJobsStorage
resource "azapi_resource" "storage_blob_owner_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.storage_account_id}${var.managed_identity_principal_id}storage-blob-owner")
  parent_id = var.storage_account_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/b7e6dc6d-f1e8-4753-8033-0f276bb0955b"  # Storage Blob Data Owner
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Storage Queue Data Contributor -- required for Functions runtime queue triggers
resource "azapi_resource" "storage_queue_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.storage_account_id}${var.managed_identity_principal_id}storage-queue-contributor")
  parent_id = var.storage_account_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/974c5e8b-45b9-4653-ba55-5f855dd0fb88"  # Storage Queue Data Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Storage Table Data Contributor -- required for Functions runtime timer triggers
resource "azapi_resource" "storage_table_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.storage_account_id}${var.managed_identity_principal_id}storage-table-contributor")
  parent_id = var.storage_account_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3"  # Storage Table Data Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Function App')
param name string

@description('Name of the App Service plan')
param planName string

@description('Azure region')
param location string = resourceGroup().location

@description('Managed identity resource ID')
param managedIdentityId string

@description('Managed identity client ID')
param managedIdentityClientId string

@description('Storage account name for Functions runtime')
param storageAccountName string

@description('Tags to apply')
param tags object = {}

resource functionPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  kind: 'functionapp'
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {
    reserved: true
  }
  tags: tags
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: name
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  properties: {
    serverFarmId: functionPlan.id
    httpsOnly: true
    siteConfig: {
      minTlsVersion: '1.2'
      linuxFxVersion: 'PYTHON|3.11'
      appSettings: [
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'AzureWebJobsStorage__accountName'
          value: storageAccountName
        }
        {
          name: 'AZURE_CLIENT_ID'
          value: managedIdentityClientId
        }
      ]
    }
  }
  tags: tags
}

output id string = functionApp.id
output name string = functionApp.name
output defaultHostName string = functionApp.properties.defaultHostName
```

### RBAC Assignment

```bicep
@description('Principal ID of the managed identity')
param principalId string

// Storage Blob Data Owner for AzureWebJobsStorage
resource storageBlobOwnerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, principalId, 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')  // Storage Blob Data Owner
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## CRITICAL: Identity-Based Storage Connection

- **Do NOT use `AzureWebJobsStorage` with a connection string** -- this embeds storage keys in app settings
- Use `AzureWebJobsStorage__accountName` with managed identity RBAC instead
- The identity needs **three** storage roles: Storage Blob Data Owner, Storage Queue Data Contributor, Storage Table Data Contributor
- This is the `__accountName` suffix pattern for identity-based connections in Azure Functions

## Common Pitfalls

| Pitfall | Impact | Fix |
|---------|--------|-----|
| Using storage connection strings | Secrets in config, key rotation burden | Use `AzureWebJobsStorage__accountName` with managed identity |
| Missing storage RBAC roles | Functions runtime fails to start (cannot access queues/blobs/tables) | Assign all three storage roles: Blob Owner, Queue Contributor, Table Contributor |
| Cold start on Consumption plan | First invocation after idle period takes 1-10 seconds | Accept for POC; use Premium plan (EP1) for production if latency matters |
| Exceeding execution time limit | Functions killed after 5 min (Consumption) / 60 min (Premium) | Use Durable Functions for long-running orchestrations |
| Not setting `FUNCTIONS_EXTENSION_VERSION` | Runtime version unpredictable | Always set to `~4` |
| Wrong `linuxFxVersion` format | Function app fails to start | Use exact format: `PYTHON\|3.11`, `NODE\|20`, `DOTNET-ISOLATED\|8.0` |
| Forgetting Consumption plan limits | 1.5 GB memory, 5-minute timeout, no VNet integration | Upgrade to EP1 for VNet, longer timeouts, and more memory |
| Timer trigger duplicate execution | Timer fires on each scaled instance | Use `IsPrimaryHost` check or singleton lock pattern |

## Production Backlog Items

- [ ] Migrate to Elastic Premium (EP1) for VNet integration and no cold starts
- [ ] Enable private endpoint and disable public network access
- [ ] Configure Application Insights integration for monitoring and tracing
- [ ] Set up auto-scaling rules for Premium plan
- [ ] Enable deployment slots for zero-downtime deployments
- [ ] Configure diagnostic logging to Log Analytics workspace
- [ ] Review function timeout settings and implement Durable Functions for long workflows
- [ ] Set up monitoring alerts (execution count, failure rate, duration, queue length)
- [ ] Implement health check endpoint
- [ ] Review and right-size Premium plan SKU based on actual usage
