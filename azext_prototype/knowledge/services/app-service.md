# Azure App Service (Web Apps)
> Fully managed platform for building, deploying, and scaling web applications with built-in CI/CD, autoscaling, and high availability.

## When to Use

- Web applications (APIs, SPAs with server-side rendering, full-stack apps)
- RESTful APIs that don't need containerization
- Applications requiring deployment slots for blue/green deployments
- Workloads where the simplicity of PaaS is preferred over containers
- When the team is familiar with App Service and does not need container orchestration
- NOT suitable for: long-running background jobs (use Functions or Container Apps), event-driven microservices, or workloads requiring custom OS-level access

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| OS | Linux | Preferred for Python/Node; Windows for .NET Framework |
| SKU | B1 (Basic) | F1 (Free) acceptable for hello-world; B1 for realistic POC |
| Runtime | Python 3.12 / Node 20 LTS / .NET 8 | Match project requirements |
| Always On | Enabled (B1+) | Not available on F1 |
| HTTPS Only | true | Enforced by policy |
| Minimum TLS | 1.2 | Enforced by policy |
| Health check | /health | Configure in app settings |
| Managed identity | User-assigned | Attached to the web app |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "plan" {
  type      = "Microsoft.Web/serverfarms@2023-12-01"
  name      = var.plan_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    kind = "linux"
    sku = {
      name = var.sku_name  # "B1" for POC
    }
    properties = {
      reserved = true  # Required for Linux
    }
  }

  tags = var.tags
}

resource "azapi_resource" "web_app" {
  type      = "Microsoft.Web/sites@2023-12-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type         = "UserAssigned"
    identity_ids = [var.managed_identity_id]
  }

  body = {
    kind = "app,linux"
    properties = {
      serverFarmId = azapi_resource.plan.id
      httpsOnly    = true
      siteConfig = {
        alwaysOn        = true
        minTlsVersion   = "1.2"
        healthCheckPath = "/health"
        linuxFxVersion  = "PYTHON|3.12"  # or NODE|20-lts, DOTNETCORE|8.0
        appSettings = [
          {
            name  = "AZURE_CLIENT_ID"
            value = var.managed_identity_client_id
          }
          # Use Key Vault references for secrets:
          # { name = "SECRET_NAME", value = "@Microsoft.KeyVault(SecretUri=https://kv-name.vault.azure.net/secrets/secret-name)" }
        ]
      }
    }
  }

  tags = var.tags

  response_export_values = ["properties.defaultHostName"]
}
```

### Windows Web App (for .NET Framework)

```hcl
resource "azapi_resource" "plan" {
  type      = "Microsoft.Web/serverfarms@2023-12-01"
  name      = var.plan_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    kind = "windows"
    sku = {
      name = var.sku_name
    }
    properties = {
      reserved = false
    }
  }

  tags = var.tags
}

resource "azapi_resource" "web_app" {
  type      = "Microsoft.Web/sites@2023-12-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type         = "UserAssigned"
    identity_ids = [var.managed_identity_id]
  }

  body = {
    kind = "app"
    properties = {
      serverFarmId = azapi_resource.plan.id
      httpsOnly    = true
      siteConfig = {
        alwaysOn      = true
        minTlsVersion = "1.2"
        healthCheckPath = "/health"
        netFrameworkVersion = "v8.0"
        appSettings = [
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
# App Service itself does not typically receive RBAC roles;
# instead, its managed identity is granted roles on OTHER resources.
# Example: grant the web app's identity access to Key Vault secrets
resource "azapi_resource" "keyvault_secrets_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.key_vault_id}${var.managed_identity_principal_id}keyvault-secrets-user")
  parent_id = var.key_vault_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/4633458b-17de-408a-b874-0445c86b69e6"  # Key Vault Secrets User
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Example: grant the web app's identity access to Storage
resource "azapi_resource" "storage_blob_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.storage_account_id}${var.managed_identity_principal_id}storage-blob-contributor")
  parent_id = var.storage_account_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"  # Storage Blob Data Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

```hcl
# Unless told otherwise, private endpoint for INBOUND access is required per governance policy
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
            privateLinkServiceId = azapi_resource.web_app.id
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

# VNet integration for OUTBOUND traffic (connects to private endpoints of backend services)
resource "azapi_update_resource" "vnet_integration" {
  count       = var.integration_subnet_id != null ? 1 : 0
  type        = "Microsoft.Web/sites@2023-12-01"
  resource_id = azapi_resource.web_app.id

  body = {
    properties = {
      virtualNetworkSubnetId = var.integration_subnet_id
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
param skuName string = 'B1'
param managedIdentityId string
param managedIdentityClientId string
param runtimeStack string = 'PYTHON|3.12'
param tags object = {}

resource servicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  kind: 'linux'
  sku: {
    name: skuName
  }
  properties: {
    reserved: true  // Required for Linux
  }
  tags: tags
}

resource webApp 'Microsoft.Web/sites@2023-12-01' = {
  name: name
  location: location
  kind: 'app,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  properties: {
    serverFarmId: servicePlan.id
    httpsOnly: true
    siteConfig: {
      alwaysOn: true
      minTlsVersion: '1.2'
      healthCheckPath: '/health'
      linuxFxVersion: runtimeStack
      appSettings: [
        {
          name: 'AZURE_CLIENT_ID'
          value: managedIdentityClientId
        }
      ]
    }
  }
  tags: tags
}

output id string = webApp.id
output name string = webApp.name
output defaultHostName string = webApp.properties.defaultHostName
```

### RBAC Assignment

```bicep
param principalId string
param keyVaultId string

resource kvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVaultId, principalId, '4633458b-17de-408a-b874-0445c86b69e6')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')  // Key Vault Secrets User
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Application Code

### Python (Flask)

```python
import os
from flask import Flask, jsonify
from azure.identity import ManagedIdentityCredential, DefaultAzureCredential

app = Flask(__name__)

def get_credential():
    client_id = os.getenv("AZURE_CLIENT_ID")
    if client_id:
        return ManagedIdentityCredential(client_id=client_id)
    return DefaultAzureCredential()

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

@app.route("/")
def index():
    return jsonify({"message": "Hello from App Service"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
```

### C# (ASP.NET Core)

```csharp
using Azure.Identity;

var builder = WebApplication.CreateBuilder(args);

// Register Azure credential for DI
var clientId = builder.Configuration["AZURE_CLIENT_ID"];
builder.Services.AddSingleton<Azure.Core.TokenCredential>(sp =>
    string.IsNullOrEmpty(clientId)
        ? new DefaultAzureCredential()
        : new ManagedIdentityCredential(clientId));

builder.Services.AddHealthChecks();

var app = builder.Build();

app.MapHealthChecks("/health");
app.MapGet("/", () => Results.Ok(new { message = "Hello from App Service" }));

app.Run();
```

### Node.js (Express)

```javascript
const express = require("express");
const { DefaultAzureCredential, ManagedIdentityCredential } = require("@azure/identity");

const app = express();
const port = process.env.PORT || 8080;

function getCredential() {
  const clientId = process.env.AZURE_CLIENT_ID;
  return clientId
    ? new ManagedIdentityCredential(clientId)
    : new DefaultAzureCredential();
}

app.get("/health", (req, res) => {
  res.json({ status: "healthy" });
});

app.get("/", (req, res) => {
  res.json({ message: "Hello from App Service" });
});

app.listen(port, () => {
  console.log(`Server running on port ${port}`);
});
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Forgetting `always_on` on B1+ | App unloads after idle period, causing cold-start latency | Set `always_on = true` in site_config |
| Using connection strings instead of managed identity | Secrets leaked in config, rotation burden | Always use `AZURE_CLIENT_ID` + RBAC |
| Not setting `WEBSITES_PORT` for custom containers | App Service cannot route traffic to app | Set `WEBSITES_PORT` to the container's listening port |
| F1/D1 SKU limitations | No always-on, no VNet integration, no deployment slots, no custom domains with SSL | Use B1 minimum for realistic POC |
| Missing health check path | No automatic instance replacement on failure | Configure `health_check_path` in site_config |
| Hardcoding secrets in app_settings | Secrets visible in portal and ARM templates | Use Key Vault references: `@Microsoft.KeyVault(SecretUri=...)` |
| Not enabling HTTPS-only | HTTP traffic allowed | Set `https_only = true` |
| Wrong Linux runtime string | App fails to start | Verify runtime string matches: `PYTHON\|3.12`, `NODE\|20-lts`, `DOTNETCORE\|8.0` |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Deployment slots | P2 | Configure staging slot with swap-based deployments for zero-downtime releases |
| Auto-scaling | P2 | Configure auto-scale rules based on CPU, memory, or HTTP queue length |
| Custom domain with TLS | P3 | Bind custom domain and configure managed or imported TLS certificate |
| VNet integration | P1 | Enable VNet integration for outbound traffic to reach private endpoints |
| Private endpoint (inbound) | P1 | Add private endpoint for the web app if it should not be publicly accessible |
| Diagnostic logging | P3 | Enable App Service logs and route to Log Analytics workspace |
| Backup configuration | P2 | Configure automated backups (requires Standard tier or higher) |
| IP restrictions | P1 | Restrict inbound access to known IP ranges or Front Door/APIM only |
| Authentication (Easy Auth) | P3 | Enable built-in authentication for end-user identity if applicable |
| Application Performance Monitoring | P3 | Integrate App Insights with auto-instrumentation for deep diagnostics |
