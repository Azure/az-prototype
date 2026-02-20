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
resource "azurerm_service_plan" "this" {
  name                = var.plan_name
  location            = var.location
  resource_group_name = var.resource_group_name
  os_type             = "Linux"
  sku_name            = var.sku_name  # "B1" for POC

  tags = var.tags
}

resource "azurerm_linux_web_app" "this" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  service_plan_id     = azurerm_service_plan.this.id
  https_only          = true

  identity {
    type         = "UserAssigned"
    identity_ids = [var.managed_identity_id]
  }

  site_config {
    always_on        = true
    minimum_tls_version = "1.2"
    health_check_path   = "/health"

    application_stack {
      python_version = "3.12"  # or node_version, dotnet_version
    }
  }

  app_settings = merge(var.app_settings, {
    "AZURE_CLIENT_ID" = var.managed_identity_client_id
    # Use Key Vault references for secrets:
    # "SECRET_NAME" = "@Microsoft.KeyVault(SecretUri=https://kv-name.vault.azure.net/secrets/secret-name)"
  })

  tags = var.tags
}
```

### Windows Web App (for .NET Framework)

```hcl
resource "azurerm_service_plan" "this" {
  name                = var.plan_name
  location            = var.location
  resource_group_name = var.resource_group_name
  os_type             = "Windows"
  sku_name            = var.sku_name

  tags = var.tags
}

resource "azurerm_windows_web_app" "this" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  service_plan_id     = azurerm_service_plan.this.id
  https_only          = true

  identity {
    type         = "UserAssigned"
    identity_ids = [var.managed_identity_id]
  }

  site_config {
    always_on           = true
    minimum_tls_version = "1.2"
    health_check_path   = "/health"

    application_stack {
      dotnet_version = "v8.0"
    }
  }

  app_settings = merge(var.app_settings, {
    "AZURE_CLIENT_ID" = var.managed_identity_client_id
  })

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# App Service itself does not typically receive RBAC roles;
# instead, its managed identity is granted roles on OTHER resources.
# Example: grant the web app's identity access to Key Vault secrets
resource "azurerm_role_assignment" "keyvault_secrets" {
  scope                = var.key_vault_id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = var.managed_identity_principal_id
}

# Example: grant the web app's identity access to Storage
resource "azurerm_role_assignment" "storage_blob" {
  scope                = var.storage_account_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.managed_identity_principal_id
}
```

### Private Endpoint

```hcl
# Private endpoint for INBOUND access to the web app (not commonly needed for POC)
resource "azurerm_private_endpoint" "this" {
  count = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0

  name                = "pe-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id

  private_service_connection {
    name                           = "psc-${var.name}"
    private_connection_resource_id = azurerm_linux_web_app.this.id
    subresource_names              = ["sites"]
    is_manual_connection           = false
  }

  dynamic "private_dns_zone_group" {
    for_each = var.private_dns_zone_id != null ? [1] : []
    content {
      name                 = "dns-zone-group"
      private_dns_zone_ids = [var.private_dns_zone_id]
    }
  }

  tags = var.tags
}

# VNet integration for OUTBOUND traffic (connects to private endpoints of backend services)
resource "azurerm_app_service_virtual_network_swift_connection" "this" {
  count          = var.integration_subnet_id != null ? 1 : 0
  app_service_id = azurerm_linux_web_app.this.id
  subnet_id      = var.integration_subnet_id
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
