# Azure Container Apps

> Serverless container platform for running microservices and containerized applications with built-in autoscaling, HTTPS ingress, and Dapr integration.

## When to Use
- Running containerized web APIs, background processors, or event-driven microservices
- Applications that need automatic scaling (including scale to zero)
- Microservice architectures that benefit from Dapr sidecars for service-to-service communication

## POC Defaults
- **Environment plan**: Consumption (serverless, pay-per-use)
- **Min replicas**: 0 (scale to zero for cost savings)
- **Max replicas**: 3 (sufficient for POC load)
- **Ingress**: External (public HTTPS endpoint)
- **Container Registry**: Basic SKU (lowest cost for POC)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "log_analytics" {
  type      = "Microsoft.OperationalInsights/workspaces@2023-09-01"
  name      = "${var.project_name}-logs"
  location  = azapi_resource.resource_group.output.location
  parent_id = azapi_resource.resource_group.id

  body = {
    properties = {
      sku = {
        name = "PerGB2018"
      }
      retentionInDays = 30
    }
  }

  tags = var.tags

  response_export_values = ["properties.customerId"]
}

resource "azapi_resource" "container_app_env" {
  type      = "Microsoft.App/managedEnvironments@2024-03-01"
  name      = "${var.project_name}-env"
  location  = azapi_resource.resource_group.output.location
  parent_id = azapi_resource.resource_group.id

  body = {
    properties = {
      appLogsConfiguration = {
        destination = "log-analytics"
        logAnalyticsConfiguration = {
          customerId = azapi_resource.log_analytics.output.properties.customerId
          sharedKey  = jsondecode(azapi_resource_action.log_analytics_keys.output).primarySharedKey
        }
      }
    }
  }

  tags = var.tags
}

resource "azapi_resource_action" "log_analytics_keys" {
  type        = "Microsoft.OperationalInsights/workspaces@2023-09-01"
  resource_id = azapi_resource.log_analytics.id
  action      = "sharedKeys"
  method      = "POST"

  response_export_values = ["*"]
}

resource "azapi_resource" "acr" {
  type      = "Microsoft.ContainerRegistry/registries@2023-11-01-preview"
  name      = var.acr_name   # 5-50 chars, alphanumeric only
  location  = azapi_resource.resource_group.output.location
  parent_id = azapi_resource.resource_group.id

  body = {
    sku = {
      name = "Basic"
    }
    properties = {
      adminUserEnabled = false   # Use managed identity, not admin credentials
    }
  }

  tags = var.tags

  response_export_values = ["properties.loginServer"]
}

resource "azapi_resource" "app_identity" {
  type      = "Microsoft.ManagedIdentity/userAssignedIdentities@2023-07-31-preview"
  name      = "${var.project_name}-app-id"
  location  = azapi_resource.resource_group.output.location
  parent_id = azapi_resource.resource_group.id

  response_export_values = ["properties.principalId", "properties.clientId"]
}

# Grant the managed identity AcrPull on the container registry
resource "azapi_resource" "acr_pull" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("sha1", "${azapi_resource.acr.id}-${azapi_resource.app_identity.output.properties.principalId}-7f951dda-4ed3-4680-a7ca-43fe172d538d")
  parent_id = azapi_resource.acr.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"  # AcrPull
      principalId      = azapi_resource.app_identity.output.properties.principalId
      principalType    = "ServicePrincipal"
    }
  }
}

resource "azapi_resource" "container_app" {
  type      = "Microsoft.App/containerApps@2024-03-01"
  name      = var.app_name
  location  = azapi_resource.resource_group.output.location
  parent_id = azapi_resource.resource_group.id

  identity {
    type         = "UserAssigned"
    identity_ids = [azapi_resource.app_identity.id]
  }

  body = {
    properties = {
      managedEnvironmentId = azapi_resource.container_app_env.id
      configuration = {
        registries = [
          {
            server   = azapi_resource.acr.output.properties.loginServer
            identity = azapi_resource.app_identity.id
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
            image = "${azapi_resource.acr.output.properties.loginServer}/${var.image_name}:${var.image_tag}"
            resources = {
              cpu    = 0.5
              memory = "1Gi"
            }
            env = [
              {
                name  = "AZURE_CLIENT_ID"
                value = azapi_resource.app_identity.output.properties.clientId
              }
            ]
            probes = [
              {
                type = "Liveness"
                httpGet = {
                  path = "/health"
                  port = 8080
                }
              }
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

  depends_on = [azapi_resource.acr_pull]

  response_export_values = ["properties.configuration.ingress.fqdn"]
}
```

### RBAC Assignment
```hcl
# Container Apps uses standard Azure RBAC for control-plane operations.
# For data-plane access to OTHER services, assign roles to the app's managed identity.

# AcrPull -- required for pulling images from Container Registry
# Role ID: 7f951dda-4ed3-4680-a7ca-43fe172d538d
resource "azapi_resource" "acr_pull" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("sha1", "${azapi_resource.acr.id}-${azapi_resource.app_identity.output.properties.principalId}-7f951dda-4ed3-4680-a7ca-43fe172d538d")
  parent_id = azapi_resource.acr.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"  # AcrPull
      principalId      = azapi_resource.app_identity.output.properties.principalId
      principalType    = "ServicePrincipal"
    }
  }
}

# Example: grant access to Key Vault secrets
resource "azapi_resource" "kv_secrets_user" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("sha1", "${azapi_resource.key_vault.id}-${azapi_resource.app_identity.output.properties.principalId}-4633458b-17de-408a-b874-0445c86b69e6")
  parent_id = azapi_resource.key_vault.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/4633458b-17de-408a-b874-0445c86b69e6"  # Key Vault Secrets User
      principalId      = azapi_resource.app_identity.output.properties.principalId
      principalType    = "ServicePrincipal"
    }
  }
}

# Example: grant access to Storage blobs
resource "azapi_resource" "storage_blob_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("sha1", "${azapi_resource.storage_account.id}-${azapi_resource.app_identity.output.properties.principalId}-ba92f5b4-2d11-453d-a403-e96b0029c9fe")
  parent_id = azapi_resource.storage_account.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"  # Storage Blob Data Contributor
      principalId      = azapi_resource.app_identity.output.properties.principalId
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint
```hcl
# Container Apps does NOT use private endpoints.
# Instead, use VNet integration via the Container Apps Environment.

resource "azapi_resource" "container_app_env_vnet" {
  type      = "Microsoft.App/managedEnvironments@2024-03-01"
  name      = "${var.project_name}-env"
  location  = azapi_resource.resource_group.output.location
  parent_id = azapi_resource.resource_group.id

  body = {
    properties = {
      vnetConfiguration = {
        infrastructureSubnetId = azapi_resource.container_apps_subnet.id
        internal               = false   # true = internal only
      }
      appLogsConfiguration = {
        destination = "log-analytics"
        logAnalyticsConfiguration = {
          customerId = azapi_resource.log_analytics.output.properties.customerId
          sharedKey  = jsondecode(azapi_resource_action.log_analytics_keys.output).primarySharedKey
        }
      }
    }
  }

  tags = var.tags
}

# The subnet must be delegated to Microsoft.App/environments and sized /23 or larger
resource "azapi_resource" "container_apps_subnet" {
  type      = "Microsoft.Network/virtualNetworks/subnets@2024-01-01"
  name      = "snet-container-apps"
  parent_id = azapi_resource.virtual_network.id

  body = {
    properties = {
      addressPrefix = "10.0.16.0/23"   # Minimum /23 for Container Apps
      delegations = [
        {
          name = "container-apps"
          properties = {
            serviceName = "Microsoft.App/environments"
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
param projectName string
param location string = resourceGroup().location
param appName string
param acrName string
param imageName string
param imageTag string = 'latest'
param tags object = {}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${projectName}-logs'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
  tags: tags
}

resource containerAppEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${projectName}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
  tags: tags
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
  tags: tags
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-07-31-preview' = {
  name: '${projectName}-app-id'
  location: location
}

// AcrPull role assignment
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identity.id, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      registries: [
        {
          server: acr.properties.loginServer
          identity: identity.id
        }
      ]
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        traffic: [
          {
            weight: 100
            latestRevision: true
          }
        ]
      }
    }
    template: {
      containers: [
        {
          name: appName
          image: '${acr.properties.loginServer}/${imageName}:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'AZURE_CLIENT_ID'
              value: identity.properties.clientId
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8080
              }
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/ready'
                port: 8080
              }
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 3
      }
    }
  }
  tags: tags
  dependsOn: [
    acrPullRole
  ]
}

output fqdn string = containerApp.properties.configuration.ingress.fqdn
output appUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
```

### RBAC Assignment
```bicep
// Container Apps uses standard Azure RBAC.
// Assign roles to the app's managed identity for access to other services.

param principalId string

// Example: Key Vault Secrets User
var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource kvSecretsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, principalId, kvSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Application Code

### Python
```python
# The application runs INSIDE the container.
# Use DefaultAzureCredential with the user-assigned managed identity.

from azure.identity import DefaultAzureCredential
import os

# AZURE_CLIENT_ID is set as an environment variable on the container
credential = DefaultAzureCredential(
    managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID")
)

# Use this credential with any Azure SDK client.
# Example with Key Vault:
from azure.keyvault.secrets import SecretClient
secret_client = SecretClient(
    vault_url="https://<vault-name>.vault.azure.net/",
    credential=credential
)

# Example health endpoint (Flask)
from flask import Flask
app = Flask(__name__)

@app.route("/health")
def health():
    return {"status": "healthy"}, 200

@app.route("/ready")
def ready():
    return {"status": "ready"}, 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
```

### C#
```csharp
// The application runs INSIDE the container.
// Use DefaultAzureCredential with the user-assigned managed identity.

using Azure.Identity;

var builder = WebApplication.CreateBuilder(args);

// Configure managed identity credential
var clientId = builder.Configuration["AZURE_CLIENT_ID"];
var credential = new DefaultAzureCredential(new DefaultAzureCredentialOptions
{
    ManagedIdentityClientId = clientId
});

// Register credential for DI
builder.Services.AddSingleton<Azure.Core.TokenCredential>(credential);

// Example: register Key Vault client
builder.Services.AddSingleton(sp =>
{
    var cred = sp.GetRequiredService<Azure.Core.TokenCredential>();
    return new Azure.Security.KeyVault.Secrets.SecretClient(
        new Uri("https://<vault-name>.vault.azure.net/"), cred);
});

var app = builder.Build();

// Health probes
app.MapGet("/health", () => Results.Ok(new { status = "healthy" }));
app.MapGet("/ready", () => Results.Ok(new { status = "ready" }));

app.Run();
```

### Node.js
```typescript
// The application runs INSIDE the container.
// Use DefaultAzureCredential with the user-assigned managed identity.

import { DefaultAzureCredential } from "@azure/identity";
import { SecretClient } from "@azure/keyvault-secrets";
import express from "express";

const credential = new DefaultAzureCredential({
  managedIdentityClientId: process.env.AZURE_CLIENT_ID,
});

// Example: Key Vault client
const secretClient = new SecretClient(
  "https://<vault-name>.vault.azure.net/",
  credential
);

const app = express();

// Health probes
app.get("/health", (req, res) => {
  res.json({ status: "healthy" });
});

app.get("/ready", (req, res) => {
  res.json({ status: "ready" });
});

app.listen(8080, "0.0.0.0", () => {
  console.log("Server running on port 8080");
});
```

## Common Pitfalls
- **No private endpoints**: Container Apps does NOT support private endpoints. Network isolation is achieved through VNet integration on the Container Apps Environment. Set `vnetConfiguration.internal = true` for internal-only access.
- **Subnet sizing**: The VNet-integrated subnet must be at least /23 (512 addresses). A /27 or /28 will fail. The subnet must be delegated to `Microsoft.App/environments`.
- **AcrPull role timing**: The role assignment must propagate before the container app tries to pull the image. Use `depends_on` to ensure ordering. Propagation can take up to 10 minutes.
- **Admin credentials on ACR**: Never set `adminUserEnabled = true`. Use managed identity with AcrPull role instead.
- **Missing health probes**: Without liveness and readiness probes, Container Apps cannot properly manage rolling deployments and traffic routing.
- **Secrets via environment variables**: Do not put secrets directly in environment variables. Use Key Vault references with the managed identity, or use Container Apps' built-in secrets store that pulls from Key Vault.
- **Scale-to-zero cold start**: When min replicas is 0, the first request after scale-down triggers a cold start (container pull + startup). Set `min_replicas = 1` if latency is critical.
- **CPU/memory constraints**: Consumption plan allows max 4 vCPUs and 8 GiB memory per container. Dedicated plan required for larger workloads.
- **Log Analytics required**: The Container Apps Environment requires a Log Analytics workspace. You cannot create the environment without one.

## Production Backlog Items
- Custom domain with managed TLS certificate
- VNet integration with internal-only ingress for private workloads
- Dapr sidecar configuration for service-to-service communication
- Dedicated workload profile plan (instead of Consumption) for predictable performance
- Horizontal scaling rules based on HTTP concurrency, KEDA scalers, or custom metrics
- Revision management with traffic splitting for blue/green deployments
- Volume mounts for persistent storage (Azure Files)
- Init containers for startup dependencies
- Managed certificate with custom domain and DNS validation
- Integration with Azure Front Door or Application Gateway for WAF

## CRITICAL: Container Apps Identity for ACR Pull

- Container Apps pulling from ACR **MUST** use `UserAssigned` (or `SystemAssigned, UserAssigned`) identity with the UAMI attached in the `identity.userAssignedIdentities` map.
- ACR `AcrPull` RBAC **MUST** be assigned to the UAMI _before_ the container app is created. Use implicit dependency via the identity resource, **NOT** `depends_on` on the RBAC resource (that creates circular dependencies).
- When multiple managed identities are attached, set `AZURE_CLIENT_ID` env var to the UAMI's `client_id` for DefaultAzureCredential disambiguation.

## CRITICAL: Log Analytics Shared Key Retrieval

Use `data "azapi_resource_action"` (**NOT** `resource "azapi_resource_action"`) for
read-only operations like fetching the Log Analytics workspace shared key.
Using `resource` causes unnecessary re-execution on every `terraform apply`.

```hcl
data "azapi_resource_action" "log_analytics_keys" {
  type        = "Microsoft.OperationalInsights/workspaces@2023-09-01"
  resource_id = local.workspace_id
  action      = "sharedKeys"
  method      = "POST"

  response_export_values = ["primarySharedKey"]
}
```

## CRITICAL: KEDA Scaler Configuration

- The `identity` field is a **sibling** of `type` and `metadata` on the custom scale rule.
  It accepts a UAMI resource ID or `"system"` for system-assigned identity.
- Do **NOT** put `clientId` in `metadata` — that is a raw KEDA concept **NOT** recognized
  by the Container Apps ARM layer. It will be silently dropped.
- Service Bus KEDA scalers **MUST** use the `namespace` short name (e.g., `"my-sb-namespace"`),
  **NOT** the FQDN (`"my-sb-namespace.servicebus.windows.net"`)
- Source: https://learn.microsoft.com/en-us/azure/container-apps/scale-app

```hcl
# CORRECT — identity is a sibling of type and metadata
scale = {
  minReplicas = 0
  maxReplicas = 10
  rules = [
    {
      name = "servicebus-rule"
      custom = {
        type = "azure-servicebus"
        metadata = {
          namespace    = local.servicebus_namespace_name  # short name, NOT FQDN
          queueName    = local.servicebus_queue_name
          messageCount = "5"
        }
        identity = local.worker_identity_id  # UAMI resource ID
      }
    }
  ]
}
```

## CRITICAL: ACR Image Reference

- ACR login server **MUST** come from upstream stage output via `terraform_remote_state`,
  **NOT** hardcoded (e.g., `"zdacrkanflowdevwus3.azurecr.io"` is **WRONG**)
- Container image defaults should use a placeholder (`mcr.microsoft.com/k8se/quickstart:latest`)
  that gets overridden at deploy time with the actual image from the private ACR

## CRITICAL: No Duplicate RBAC Assignments

Do **NOT** re-create RBAC role assignments that were already created in an upstream
service stage (e.g., Stage 8 Cosmos DB already assigns Data Contributor to the worker
identity). Duplicate role assignments cause ARM HTTP 409 Conflict errors on re-apply.
Only create RBAC assignments for roles that are **NOT** already assigned upstream.
