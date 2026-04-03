# Azure SignalR Service
> Fully managed real-time messaging service that enables bi-directional communication between servers and connected clients using WebSockets, Server-Sent Events, and long polling.

## When to Use

- **Real-time web applications** -- chat, live dashboards, collaborative editing, notifications
- **Live data streaming** -- stock tickers, IoT telemetry dashboards, real-time analytics displays
- **Server-to-client push** -- push notifications without client polling
- **Multi-client synchronization** -- gaming leaderboards, whiteboarding, shared document editing
- **Scalable WebSocket management** -- offload connection management from App Service or Container Apps

Prefer SignalR Service over self-hosted SignalR when you need managed scaling, built-in connection management, and don't want to handle sticky sessions. For event-driven messaging between backend services, use Event Hubs or Service Bus instead.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Free | 20 concurrent connections, 20K messages/day; sufficient for POC |
| SKU (with more capacity) | Standard_S1 | 1K concurrent connections, 1M messages/day |
| Service mode | Default | Hub-based routing; Serverless mode for Azure Functions integration |
| Connectivity | Public | Flag private endpoint as production backlog item |
| AAD auth | Enabled | Use managed identity for upstream connections |
| TLS | 1.2 minimum | Enforced by default |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "signalr" {
  type      = "Microsoft.SignalRService/SignalR@2024-03-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name     = "Free_F1"
      capacity = 1
    }
    kind = "SignalR"
    properties = {
      features = [
        {
          flag  = "ServiceMode"
          value = "Default"  # "Default" for hub-based, "Serverless" for Functions
        },
        {
          flag  = "EnableConnectivityLogs"
          value = "true"
        },
        {
          flag  = "EnableMessagingLogs"
          value = "true"
        }
      ]
      tls = {
        clientCertEnabled = false
      }
      publicNetworkAccess = "Enabled"  # Disable for production; flag as backlog item
    }
  }

  tags = var.tags

  response_export_values = ["properties.hostName", "properties.publicPort"]
}
```

### RBAC Assignment

```hcl
# SignalR App Server -- allows the app to negotiate connections and send messages
resource "azapi_resource" "signalr_app_server_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.signalr.id}${var.managed_identity_principal_id}signalr-app-server")
  parent_id = azapi_resource.signalr.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/420fcaa2-552c-430f-98ca-3264be4806c7"  # SignalR App Server
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# SignalR Service Owner -- full data-plane access (negotiate, send, manage groups)
resource "azapi_resource" "signalr_service_owner_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.signalr.id}${var.managed_identity_principal_id}signalr-service-owner")
  parent_id = azapi_resource.signalr.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/7e4f1700-ea5a-4f59-8f37-079cfe29dce3"  # SignalR Service Owner
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the SignalR service')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Tags to apply')
param tags object = {}

@description('Service mode: Default or Serverless')
@allowed(['Default', 'Serverless'])
param serviceMode string = 'Default'

resource signalr 'Microsoft.SignalRService/SignalR@2024-03-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Free_F1'
    capacity: 1
  }
  kind: 'SignalR'
  properties: {
    features: [
      {
        flag: 'ServiceMode'
        value: serviceMode
      }
      {
        flag: 'EnableConnectivityLogs'
        value: 'true'
      }
      {
        flag: 'EnableMessagingLogs'
        value: 'true'
      }
    ]
    tls: {
      clientCertEnabled: false
    }
    publicNetworkAccess: 'Enabled'
  }
}

output id string = signalr.id
output name string = signalr.name
output hostName string = signalr.properties.hostName
```

### RBAC Assignment

```bicep
@description('Principal ID of the managed identity')
param principalId string

// SignalR App Server -- allows negotiate and send
resource signalrAppServerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(signalr.id, principalId, '420fcaa2-552c-430f-98ca-3264be4806c7')
  scope: signalr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '420fcaa2-552c-430f-98ca-3264be4806c7')  // SignalR App Server
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Fix |
|---------|--------|-----|
| Wrong service mode | Default mode requires a hub server; Serverless mode works with Functions but not self-hosted hubs | Match `ServiceMode` to your architecture: Default for App Service/Container Apps, Serverless for Functions |
| Using access keys instead of AAD | Secrets in config, rotation burden | Use `DefaultAzureCredential` with SignalR App Server role |
| Free tier limits | 20 connections max, 20K messages/day; exceeding silently drops connections | Upgrade to Standard for realistic load testing |
| Not enabling connectivity logs | Cannot diagnose connection failures | Enable `EnableConnectivityLogs` feature flag |
| Missing CORS configuration | Browser clients blocked by CORS | Configure allowed origins on the SignalR service or upstream app |
| Sticky sessions with self-hosted | Multiple app instances need sticky sessions or Azure SignalR backplane | Use SignalR Service (managed) to eliminate sticky session requirement |

## Production Backlog Items

- [ ] Upgrade to Standard tier for SLA and higher connection limits
- [ ] Enable private endpoint and disable public network access
- [ ] Configure upstream endpoints for Serverless mode
- [ ] Set up auto-scaling (Standard tier unit count)
- [ ] Enable diagnostic logging to Log Analytics workspace
- [ ] Configure custom domains with TLS
- [ ] Review and tune message size limits
- [ ] Implement connection throttling and rate limiting at the application layer
