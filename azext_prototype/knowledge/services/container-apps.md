---
service_namespace: Microsoft.App/containerApps
display_name: Azure Container Apps
depends_on:
  - Microsoft.App/managedEnvironments
  - Microsoft.ContainerRegistry/registries
  - Microsoft.ManagedIdentity/userAssignedIdentities
---

# Azure Container Apps

> Serverless container platform for running microservices and containerized applications with built-in autoscaling, HTTPS ingress, and Dapr integration.

## When to Use
- Running containerized web APIs, background processors, or event-driven microservices
- Applications that need automatic scaling (including scale to zero)
- Microservice architectures that benefit from Dapr sidecars

## POC Defaults
- **Min replicas**: 0 (scale to zero for cost savings)
- **Max replicas**: 3 (sufficient for POC load)
- **Ingress**: External (public HTTPS endpoint)
- **CPU**: 0.5 vCPU per container
- **Memory**: 1 GiB per container
- **Identity**: UserAssigned managed identity (required for ACR pull)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "container_app" {
  type      = "Microsoft.App/containerApps@2024-03-01"
  name      = var.app_name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type         = "UserAssigned"
    identity_ids = [var.managed_identity_id]
  }

  body = {
    properties = {
      managedEnvironmentId = var.container_app_environment_id
      configuration = {
        registries = [
          {
            server   = var.acr_login_server
            identity = var.managed_identity_id
          }
        ]
        ingress = {
          external    = true
          targetPort  = 8080
          transport   = "auto"
          traffic = [
            {
              weight         = 100
              latestRevision = true
            }
          ]
        }
      }
      template = {
        containers = [
          {
            name  = var.app_name
            image = "${var.acr_login_server}/${var.image_name}:${var.image_tag}"
            resources = {
              cpu    = 0.5
              memory = "1Gi"
            }
            env = [
              {
                name  = "AZURE_CLIENT_ID"
                value = var.managed_identity_client_id
              },
              {
                name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
                value = var.app_insights_connection_string
              }
            ]
            probes = [
              {
                type = "Liveness"
                httpGet = {
                  path = "/health"
                  port = 8080
                }
              },
              {
                type = "Readiness"
                httpGet = {
                  path = "/ready"
                  port = 8080
                }
              }
            ]
          }
        ]
        scale = {
          minReplicas = 0
          maxReplicas = 3
        }
      }
    }
  }

  tags = var.tags
  response_export_values = ["properties.configuration.ingress.fqdn"]
}
```

### RBAC Assignments (for app identity to access other services)
```hcl
# AcrPull — required for pulling images from Container Registry
resource "azapi_resource" "acr_pull" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("sha1", "${var.acr_id}-${var.principal_id}-7f951dda")
  parent_id = var.acr_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"
      principalId      = var.principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Key Vault Secrets User — for reading secrets
resource "azapi_resource" "kv_secrets_user" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("sha1", "${var.key_vault_id}-${var.principal_id}-4633458b")
  parent_id = var.key_vault_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/4633458b-17de-408a-b874-0445c86b69e6"
      principalId      = var.principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### KEDA Scaler Configuration
```hcl
# Service Bus KEDA scaler — identity is a SIBLING of type and metadata
scale = {
  minReplicas = 0
  maxReplicas = 10
  rules = [
    {
      name = "servicebus-rule"
      custom = {
        type = "azure-servicebus"
        metadata = {
          namespace    = var.servicebus_namespace_name  # short name, NOT FQDN
          queueName    = var.servicebus_queue_name
          messageCount = "5"
        }
        identity = var.managed_identity_id  # UAMI resource ID
      }
    }
  ]
}
```

## Bicep Patterns

### Basic Resource
```bicep
param appName string
param location string = resourceGroup().location
param environmentId string
param acrLoginServer string
param imageName string
param imageTag string
param identityId string
param identityClientId string
param tags object = {}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      registries: [
        {
          server: acrLoginServer
          identity: identityId
        }
      ]
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        traffic: [{ weight: 100, latestRevision: true }]
      }
    }
    template: {
      containers: [
        {
          name: appName
          image: '${acrLoginServer}/${imageName}:${imageTag}'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'AZURE_CLIENT_ID', value: identityClientId }
          ]
          probes: [
            { type: 'Liveness', httpGet: { path: '/health', port: 8080 } }
            { type: 'Readiness', httpGet: { path: '/ready', port: 8080 } }
          ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 3 }
    }
  }
  tags: tags
}

output fqdn string = containerApp.properties.configuration.ingress.fqdn
output appUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
```

## Application Code

### Python
```python
from azure.identity import DefaultAzureCredential
import os

# AZURE_CLIENT_ID env var set on the container for UAMI disambiguation
credential = DefaultAzureCredential(
    managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID")
)

# Use with any Azure SDK client (Key Vault, Storage, Service Bus, etc.)
from azure.keyvault.secrets import SecretClient
secret_client = SecretClient(
    vault_url="https://<vault-name>.vault.azure.net/",
    credential=credential
)
```

### C#
```csharp
using Azure.Identity;

var builder = WebApplication.CreateBuilder(args);

var clientId = builder.Configuration["AZURE_CLIENT_ID"];
var credential = new DefaultAzureCredential(new DefaultAzureCredentialOptions
{
    ManagedIdentityClientId = clientId
});

builder.Services.AddSingleton<Azure.Core.TokenCredential>(credential);

var app = builder.Build();
app.MapGet("/health", () => Results.Ok(new { status = "healthy" }));
app.MapGet("/ready", () => Results.Ok(new { status = "ready" }));
app.Run();
```

### Node.js
```typescript
import { DefaultAzureCredential } from "@azure/identity";
import express from "express";

const credential = new DefaultAzureCredential({
  managedIdentityClientId: process.env.AZURE_CLIENT_ID,
});

const app = express();
app.get("/health", (req, res) => res.json({ status: "healthy" }));
app.get("/ready", (req, res) => res.json({ status: "ready" }));
app.listen(8080, "0.0.0.0");
```

## Common Pitfalls
- **No private endpoints**: Container Apps does NOT support private endpoints. Network isolation is via VNet integration on the Container Apps Environment (`internal = true`).
- **AcrPull role timing**: The RBAC assignment must propagate before the container app pulls the image. Propagation can take up to 10 minutes. Use `depends_on` to ensure ordering.
- **SystemAssigned-only identity fails on first deploy**: Use UserAssigned (or SystemAssigned,UserAssigned) for ACR pull. SystemAssigned alone doesn't exist until after the resource is created, causing the initial image pull to fail.
- **Missing health probes**: Without liveness and readiness probes, Container Apps cannot properly manage rolling deployments.
- **Secrets in plain env vars**: Do not put secrets in environment variables. Use Key Vault references with managed identity.
- **Scale-to-zero cold start**: First request after scale-down triggers container pull + startup (30-60s). Set `minReplicas = 1` if latency is critical.
- **KEDA scaler identity**: The `identity` field is a sibling of `type` and `metadata`. Do NOT put `clientId` in `metadata`.
- **Service Bus KEDA namespace**: Use the short namespace name, NOT the FQDN.
- **Duplicate RBAC**: Do NOT re-create RBAC assignments already created in upstream service stages (causes ARM 409 Conflict).
- **ACR image reference**: Use upstream stage output for ACR login server, NEVER hardcode.

## Production Backlog Items
- Custom domain with managed TLS certificate
- Revision management with traffic splitting for blue/green deployments
- Dapr sidecar configuration for service-to-service communication
- Horizontal scaling rules based on HTTP concurrency or custom metrics
- Volume mounts for persistent storage (Azure Files)
- Init containers for startup dependencies
- Integration with Azure Front Door or Application Gateway for WAF
