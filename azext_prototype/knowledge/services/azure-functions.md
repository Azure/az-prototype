# Azure Functions
> Event-driven serverless compute platform for running code on-demand without managing infrastructure, supporting multiple languages and trigger types.

## When to Use

- Event-driven processing (HTTP requests, queue messages, timer-based jobs, blob triggers)
- Lightweight APIs with sporadic or unpredictable traffic patterns
- Background processing and data transformation tasks
- Integrations between Azure services (e.g., Service Bus to Cosmos DB)
- Microservice endpoints that scale independently
- NOT suitable for: long-running processes over 10 minutes (use Container Apps or Durable Functions), stateful workloads requiring persistent connections, or applications needing full control over the hosting environment

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Plan | Consumption (Y1) | Flex Consumption for preview features; B1 App Service Plan if VNet needed |
| OS | Linux | Preferred for Python/Node; Windows for .NET in-process |
| Runtime | Python 3.12 / Node 20 / .NET 8 (isolated) | Match project requirements |
| HTTPS Only | true | Enforced by policy |
| Minimum TLS | 1.2 | Enforced by policy |
| Managed identity | User-assigned | Attached to the function app |
| Storage account | Required | Separate from any data storage; used for runtime state (AzureWebJobsStorage) |

**CRITICAL**: Azure Functions REQUIRE a dedicated storage account for internal runtime operations (function triggers, bindings state, task hub for Durable Functions). This storage account is separate from any application data storage and must always be provisioned alongside the function app.

## Terraform Patterns

### Basic Resource

```hcl
# Storage account required for Functions runtime
resource "azapi_resource" "functions_storage" {
  type      = "Microsoft.Storage/storageAccounts@2023-05-01"
  name      = var.storage_account_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    kind = "StorageV2"
    sku = {
      name = "Standard_LRS"
    }
    properties = {
      minimumTlsVersion        = "TLS1_2"
      supportsHttpsTrafficOnly = true
    }
  }

  tags = var.tags

  response_export_values = ["properties.primaryEndpoints", "id"]
}

# Consumption plan
resource "azapi_resource" "plan" {
  type      = "Microsoft.Web/serverfarms@2023-12-01"
  name      = var.plan_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    kind = "linux"
    sku = {
      name = "Y1"   # Consumption plan
      tier = "Dynamic"
    }
    properties = {
      reserved = true
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
      serverFarmId = azapi_resource.plan.id
      httpsOnly    = true
      siteConfig = {
        minTlsVersion  = "1.2"
        linuxFxVersion = "PYTHON|3.12"  # or NODE|20, DOTNET-ISOLATED|8.0
        appSettings = [
          {
            name  = "AzureWebJobsStorage"
            value = "DefaultEndpointsProtocol=https;AccountName=${var.storage_account_name};AccountKey=${data.azapi_resource_action.storage_keys.output.keys[0].value};EndpointSuffix=core.windows.net"
          },
          {
            name  = "FUNCTIONS_EXTENSION_VERSION"
            value = "~4"
          },
          {
            name  = "FUNCTIONS_WORKER_RUNTIME"
            value = "python"  # or "node", "dotnet-isolated"
          },
          {
            name  = "AZURE_CLIENT_ID"
            value = var.managed_identity_client_id
          },
          {
            name  = "AzureWebJobsFeatureFlags"
            value = "EnableWorkerIndexing"
          }
        ]
      }
    }
  }

  tags = var.tags

  response_export_values = ["properties.defaultHostName"]
}
```

### Storage Account with Managed Identity (preferred over access keys)

```hcl
# When using managed identity for the functions storage connection:
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
      serverFarmId = azapi_resource.plan.id
      httpsOnly    = true
      siteConfig = {
        minTlsVersion  = "1.2"
        linuxFxVersion = "PYTHON|3.12"
        appSettings = [
          {
            name  = "AZURE_CLIENT_ID"
            value = var.managed_identity_client_id
          },
          {
            name  = "AzureWebJobsStorage__accountName"
            value = var.storage_account_name
          },
          {
            name  = "AzureWebJobsStorage__credential"
            value = "managedidentity"
          },
          {
            name  = "AzureWebJobsStorage__clientId"
            value = var.managed_identity_client_id
          },
          {
            name  = "FUNCTIONS_EXTENSION_VERSION"
            value = "~4"
          },
          {
            name  = "FUNCTIONS_WORKER_RUNTIME"
            value = "python"
          }
        ]
      }
    }
  }

  tags = var.tags

  response_export_values = ["properties.defaultHostName"]
}

# Grant the function app's identity Storage Blob Data Owner on its runtime storage
resource "azapi_resource" "functions_storage_blob_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.functions_storage.id}${var.managed_identity_principal_id}blob-owner")
  parent_id = azapi_resource.functions_storage.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/b7e6dc6d-f1e8-4753-8033-0f276bb0955b"  # Storage Blob Data Owner
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

resource "azapi_resource" "functions_storage_queue_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.functions_storage.id}${var.managed_identity_principal_id}queue-contributor")
  parent_id = azapi_resource.functions_storage.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/974c5e8b-45b9-4653-ba55-5f855dd0fb88"  # Storage Queue Data Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

resource "azapi_resource" "functions_storage_table_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.functions_storage.id}${var.managed_identity_principal_id}table-contributor")
  parent_id = azapi_resource.functions_storage.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3"  # Storage Table Data Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### RBAC Assignment

```hcl
# Function app's managed identity accessing other resources
# Example: grant access to Service Bus for queue-triggered functions
resource "azapi_resource" "servicebus_receiver_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.servicebus_namespace_id}${var.managed_identity_principal_id}sb-receiver")
  parent_id = var.servicebus_namespace_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0"  # Azure Service Bus Data Receiver
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

resource "azapi_resource" "servicebus_sender_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.servicebus_namespace_id}${var.managed_identity_principal_id}sb-sender")
  parent_id = var.servicebus_namespace_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/69a216fc-b8fb-44d8-bc22-1f3c2cd27a39"  # Azure Service Bus Data Sender
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

```hcl
resource "azapi_resource" "private_endpoint" {
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
            privateLinkServiceId = azapi_resource.function_app.id
            groupIds             = ["sites"]
          }
        }
      ]
    }
  }

  tags = var.tags
}

resource "azapi_resource" "dns_zone_group" {
  count     = var.enable_private_endpoint && var.subnet_id != null && var.private_dns_zone_id != null ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01"
  name      = "dns-zone-group"
  parent_id = azapi_resource.private_endpoint[0].id

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "config"
          properties = {
            privateDnsZoneId = var.private_dns_zone_id
          }
        }
      ]
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
param name string
param location string
param planName string
param storageAccountName string
param managedIdentityId string
param managedIdentityClientId string
param runtime string = 'python'
param runtimeVersion string = '3.12'
param tags object = {}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
  tags: tags
}

resource hostingPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  kind: 'linux'
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
    serverFarmId: hostingPlan.id
    httpsOnly: true
    siteConfig: {
      minTlsVersion: '1.2'
      linuxFxVersion: '${toUpper(runtime)}|${runtimeVersion}'
      appSettings: [
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};EndpointSuffix=${environment().suffixes.storage};AccountKey=${storageAccount.listKeys().keys[0].value}'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: runtime
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
param principalId string
param serviceBusNamespaceId string

resource sbReceiverRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespaceId, principalId, '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0')
  scope: serviceBusNamespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0')  // Azure Service Bus Data Receiver
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Application Code

### Python (Azure Functions v2 programming model)

```python
import os
import logging
import azure.functions as func
from azure.identity import ManagedIdentityCredential, DefaultAzureCredential

app = func.FunctionApp()

def get_credential():
    client_id = os.getenv("AZURE_CLIENT_ID")
    if client_id:
        return ManagedIdentityCredential(client_id=client_id)
    return DefaultAzureCredential()

@app.function_name(name="HttpTrigger")
@app.route(route="hello", auth_level=func.AuthLevel.ANONYMOUS)
def hello(req: func.HttpRequest) -> func.HttpResponse:
    name = req.params.get("name", "World")
    return func.HttpResponse(f"Hello, {name}!")

@app.function_name(name="QueueTrigger")
@app.queue_trigger(arg_name="msg", queue_name="my-queue",
                   connection="AzureWebJobsStorage")
def process_queue(msg: func.QueueMessage) -> None:
    logging.info(f"Processing message: {msg.get_body().decode('utf-8')}")

@app.function_name(name="TimerTrigger")
@app.timer_trigger(schedule="0 */5 * * * *", arg_name="timer",
                   run_on_startup=False)
def timer_job(timer: func.TimerRequest) -> None:
    logging.info("Timer trigger executed")
```

### C# (.NET 8 Isolated Worker)

```csharp
using Azure.Identity;
using Microsoft.Azure.Functions.Worker;
using Microsoft.Azure.Functions.Worker.Http;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.DependencyInjection;
using System.Net;

var host = new HostBuilder()
    .ConfigureFunctionsWorkerDefaults()
    .ConfigureServices(services =>
    {
        var clientId = Environment.GetEnvironmentVariable("AZURE_CLIENT_ID");
        services.AddSingleton<Azure.Core.TokenCredential>(sp =>
            string.IsNullOrEmpty(clientId)
                ? new DefaultAzureCredential()
                : new ManagedIdentityCredential(clientId));
    })
    .Build();

host.Run();

// Functions/HelloFunction.cs
public class HelloFunction
{
    [Function("HttpTrigger")]
    public HttpResponseData Run(
        [HttpTrigger(AuthorizationLevel.Anonymous, "get")] HttpRequestData req)
    {
        var response = req.CreateResponse(HttpStatusCode.OK);
        response.WriteString("Hello from Azure Functions!");
        return response;
    }
}
```

### Node.js (Azure Functions v4 programming model)

```javascript
const { app } = require("@azure/functions");
const { DefaultAzureCredential, ManagedIdentityCredential } = require("@azure/identity");

function getCredential() {
  const clientId = process.env.AZURE_CLIENT_ID;
  return clientId
    ? new ManagedIdentityCredential(clientId)
    : new DefaultAzureCredential();
}

app.http("hello", {
  methods: ["GET"],
  authLevel: "anonymous",
  handler: async (request, context) => {
    const name = request.query.get("name") || "World";
    return { body: `Hello, ${name}!` };
  },
});

app.serviceBusQueue("processQueue", {
  queueName: "my-queue",
  connection: "ServiceBusConnection",
  handler: async (message, context) => {
    context.log("Processing message:", message);
  },
});
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Forgetting the runtime storage account | Function app fails to start | Always provision a dedicated storage account for the function runtime |
| Using the same storage account for runtime and data | Lock contention, unexpected behavior | Use separate storage accounts for runtime (AzureWebJobsStorage) and application data |
| Consumption plan + VNet integration | Not supported on Consumption plan | Use Flex Consumption, Premium (EP1+), or dedicated App Service Plan for VNet integration |
| Cold start latency on Consumption plan | First request after idle takes seconds | Accept for POC; use Premium plan or pre-warmed instances for production |
| Wrong `FUNCTIONS_WORKER_RUNTIME` | Functions fail to load | Must match the deployed runtime: `python`, `node`, `dotnet-isolated` |
| Missing `FUNCTIONS_EXTENSION_VERSION` | Defaults to older runtime | Always set to `~4` for Functions v4 |
| Python v1 vs v2 programming model | Code structure incompatibility | Use v2 programming model (decorator-based) for new projects |
| Durable Functions without proper storage | Orchestrations hang or fail | Durable Functions require the runtime storage account with table and queue access |
| HTTP trigger with AuthLevel.Function but no key management | Unauthorized access | Use `Anonymous` for POC behind APIM, or `Function` with keys for direct access |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Premium plan (EP1+) | P1 | Upgrade to Premium plan for VNet integration and private endpoints |
| Dedicated storage account | P2 | Ensure runtime storage is isolated from application data storage |
| VNet integration | P1 | Enable VNet integration for outbound traffic to private endpoints |
| Private endpoint (inbound) | P1 | Add private endpoint if functions should not be publicly accessible |
| CORS configuration | P3 | Configure allowed origins for browser-based consumers |
| Function app slots | P2 | Configure staging slot for zero-downtime deployments |
| Application Insights integration | P3 | Enable distributed tracing and performance monitoring |
| Managed identity for storage | P2 | Replace storage account access key with managed identity connection |
| Scale limits | P3 | Configure maximum instance count to control costs |
| IP restrictions | P1 | Restrict inbound access to known IP ranges or APIM/Front Door only |
