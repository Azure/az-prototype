# Azure Logic Apps
> Low-code workflow orchestration service for automating business processes and integrating with hundreds of connectors across cloud and on-premises systems.

## When to Use

- **System integration** -- connect SaaS applications, on-premises systems, and Azure services with pre-built connectors
- **Business process automation** -- approval workflows, document processing, data transformation pipelines
- **Event-driven orchestration** -- trigger workflows from Event Grid, Service Bus, HTTP, schedules, or file events
- **B2B integration** -- EDI, AS2, and enterprise application integration scenarios
- **API orchestration** -- fan-out/fan-in patterns, retry with backoff, conditional branching

Prefer Logic Apps over Azure Functions when the workflow is connector-heavy and benefits from visual design. Use Functions for custom compute-intensive logic or sub-second latency requirements. Logic Apps (Standard) runs on App Service plan for VNet integration and dedicated compute.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Plan type | Consumption | Pay-per-execution; lowest cost for POC |
| Plan type (with VNet) | Standard (WS1) | Runs on App Service plan; supports VNet, stateful workflows |
| Managed identity | System-assigned | For connector authentication |
| State | Enabled | Workflow active on creation |
| Trigger | HTTP (manual) or Recurrence | Simplest trigger for POC |

## Terraform Patterns

### Basic Resource (Consumption)

```hcl
resource "azapi_resource" "logic_app" {
  type      = "Microsoft.Logic/workflows@2019-05-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {
      state = "Enabled"
      definition = {
        "$schema"      = "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#"
        contentVersion = "1.0.0.0"
        triggers = {
          manual = {
            type = "Request"
            kind = "Http"
            inputs = {
              schema = {}
            }
          }
        }
        actions = {}
        outputs = {}
      }
    }
  }

  tags = var.tags

  response_export_values = ["properties.accessEndpoint"]
}
```

### Basic Resource (Standard)

```hcl
resource "azapi_resource" "logic_app_plan" {
  type      = "Microsoft.Web/serverfarms@2023-12-01"
  name      = var.plan_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    kind = "elastic"
    sku = {
      name = "WS1"
      tier = "WorkflowStandard"
    }
    properties = {
      reserved = true
    }
  }

  tags = var.tags
}

resource "azapi_resource" "logic_app_standard" {
  type      = "Microsoft.Web/sites@2023-12-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type = "SystemAssigned"
  }

  body = {
    kind = "functionapp,workflowapp"
    properties = {
      serverFarmId = azapi_resource.logic_app_plan.id
      httpsOnly    = true
      siteConfig = {
        minTlsVersion = "1.2"
        appSettings = [
          {
            name  = "FUNCTIONS_EXTENSION_VERSION"
            value = "~4"
          },
          {
            name  = "FUNCTIONS_WORKER_RUNTIME"
            value = "node"
          },
          {
            name  = "AzureWebJobsStorage"
            value = var.storage_connection_string
          }
        ]
      }
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# Logic App's system-assigned identity accessing other resources
# Example: grant Logic App access to Service Bus
resource "azapi_resource" "servicebus_sender_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.servicebus_namespace_id}${azapi_resource.logic_app.identity[0].principal_id}servicebus-sender")
  parent_id = var.servicebus_namespace_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/69a216fc-b8fb-44d8-bc22-1f3c2cd27a39"  # Azure Service Bus Data Sender
      principalId      = azapi_resource.logic_app.identity[0].principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource (Consumption)

```bicep
@description('Name of the Logic App')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Tags to apply')
param tags object = {}

resource logicApp 'Microsoft.Logic/workflows@2019-05-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      triggers: {
        manual: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            schema: {}
          }
        }
      }
      actions: {}
      outputs: {}
    }
  }
}

output id string = logicApp.id
output name string = logicApp.name
output accessEndpoint string = logicApp.properties.accessEndpoint
output principalId string = logicApp.identity.principalId
```

### RBAC Assignment

```bicep
@description('Principal ID of the Logic App managed identity')
param principalId string

@description('Service Bus namespace to grant access to')
param serviceBusNamespaceId string

// Grant Logic App access to send messages to Service Bus
resource serviceBusSenderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespaceId, principalId, '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39')
  scope: serviceBus
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39')  // Azure Service Bus Data Sender
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Fix |
|---------|--------|-----|
| Consumption vs Standard confusion | Consumption is serverless (pay-per-run); Standard needs App Service plan and storage account | Choose Consumption for simple POC, Standard for VNet or stateful workflows |
| Connector authentication with keys | Secrets embedded in workflow definition | Use managed identity for connectors that support it |
| Infinite trigger loops | Workflow triggers itself repeatedly, consuming massive run costs | Add conditions to prevent re-triggering; use concurrency limits |
| Missing retry policies | Transient failures cause workflow to fail | Configure retry policies on actions (fixed, exponential, or custom intervals) |
| Large message handling | Consumption tier has 100 MB message limit | Use chunking or blob storage for large payloads |
| Not using managed connectors | Custom HTTP calls lose built-in retry, pagination, and throttling | Use managed connectors where available for built-in reliability |

## Production Backlog Items

- [ ] Migrate to Standard tier for VNet integration and dedicated compute
- [ ] Enable private endpoint for Standard tier workflows
- [ ] Configure diagnostic logging to Log Analytics workspace
- [ ] Set up monitoring alerts (failed runs, throttled actions, latency)
- [ ] Implement integration account for B2B scenarios (maps, schemas, partners)
- [ ] Configure concurrency and debatching limits for high-throughput triggers
- [ ] Review and optimize connector usage for cost (premium connectors cost more)
- [ ] Set up automated deployment pipeline for workflow definitions
- [ ] Enable Application Insights integration for end-to-end tracing
