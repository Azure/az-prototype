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
resource "azurerm_log_analytics_workspace" "this" {
  name                = "${var.project_name}-logs"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = 30

  tags = var.tags
}

resource "azurerm_container_app_environment" "this" {
  name                       = "${var.project_name}-env"
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  tags = var.tags
}

resource "azurerm_container_registry" "this" {
  name                = var.acr_name   # 5-50 chars, alphanumeric only
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "Basic"
  admin_enabled       = false   # Use managed identity, not admin credentials

  tags = var.tags
}

resource "azurerm_user_assigned_identity" "app" {
  name                = "${var.project_name}-app-id"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
}

# Grant the managed identity AcrPull on the container registry
resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_container_app" "this" {
  name                         = var.app_name
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  template {
    min_replicas = 0
    max_replicas = 3

    container {
      name   = var.app_name
      image  = "${azurerm_container_registry.this.login_server}/${var.image_name}:${var.image_tag}"
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.app.client_id
      }

      liveness_probe {
        transport = "HTTP"
        path      = "/health"
        port      = 8080
      }

      readiness_probe {
        transport = "HTTP"
        path      = "/ready"
        port      = 8080
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8080
    transport        = "auto"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  tags = var.tags

  depends_on = [azurerm_role_assignment.acr_pull]
}
```

### RBAC Assignment
```hcl
# Container Apps uses standard Azure RBAC for control-plane operations.
# For data-plane access to OTHER services, assign roles to the app's managed identity.

# AcrPull — required for pulling images from Container Registry
# Role ID: 7f951dda-4ed3-4680-a7ca-43fe172d538d
resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# Example: grant access to Key Vault secrets
resource "azurerm_role_assignment" "kv_secrets_user" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# Example: grant access to Storage blobs
resource "azurerm_role_assignment" "storage_blob_contributor" {
  scope                = azurerm_storage_account.this.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}
```

### Private Endpoint
```hcl
# Container Apps does NOT use private endpoints.
# Instead, use VNet integration via the Container Apps Environment.

resource "azurerm_container_app_environment" "this" {
  name                           = "${var.project_name}-env"
  location                       = azurerm_resource_group.this.location
  resource_group_name            = azurerm_resource_group.this.name
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.this.id
  infrastructure_subnet_id       = azurerm_subnet.container_apps.id   # VNet integration
  internal_load_balancer_enabled = false                              # true = internal only

  tags = var.tags
}

# The subnet must be delegated to Microsoft.App/environments and sized /23 or larger
resource "azurerm_subnet" "container_apps" {
  name                 = "snet-container-apps"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = ["10.0.16.0/23"]   # Minimum /23 for Container Apps

  delegation {
    name = "container-apps"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
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
- **No private endpoints**: Container Apps does NOT support private endpoints. Network isolation is achieved through VNet integration on the Container Apps Environment. Set `internal_load_balancer_enabled = true` for internal-only access.
- **Subnet sizing**: The VNet-integrated subnet must be at least /23 (512 addresses). A /27 or /28 will fail. The subnet must be delegated to `Microsoft.App/environments`.
- **AcrPull role timing**: The role assignment must propagate before the container app tries to pull the image. Use `depends_on` to ensure ordering. Propagation can take up to 10 minutes.
- **Admin credentials on ACR**: Never set `admin_enabled = true`. Use managed identity with AcrPull role instead.
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
