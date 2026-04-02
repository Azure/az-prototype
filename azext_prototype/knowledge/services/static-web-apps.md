# Azure Static Web Apps
> Fully managed hosting for static frontends (SPA, SSG, SSR) with integrated serverless API backends, global CDN distribution, and built-in authentication.

## When to Use

- **Single-page applications (SPA)** -- React, Angular, Vue, Svelte frontends with API backend
- **Static site generators** -- Hugo, Gatsby, Next.js (static export), Astro
- **Full-stack web apps** -- Frontend + managed Azure Functions API in a single resource
- **Documentation sites** -- Docusaurus, MkDocs, VuePress with CI/CD from GitHub/Azure DevOps
- **Jamstack architectures** -- Pre-rendered content with dynamic API endpoints

Choose Static Web Apps over App Service when the frontend is static/SPA and the backend is serverless APIs. Choose App Service for server-rendered applications (Django, Rails, Express with SSR) or when you need WebSockets, background workers, or persistent connections.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Free | 2 custom domains, 0.5 GiB storage, 100 GiB bandwidth/month |
| SKU (with auth) | Standard | Custom auth providers, private endpoints, SLA |
| API backend | Managed Functions | Built-in; no separate Function App needed |
| Build preset | Auto-detected | Based on framework in repo |
| Staging environments | Automatic | PR-based preview environments on Free tier |
| Authentication | Built-in providers (POC) | GitHub, Azure AD, Twitter; no config needed |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "this" {
  type      = "Microsoft.Web/staticSites@2023-12-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Free"
      tier = "Free"
    }
    properties = {
      stagingEnvironmentPolicy  = "Enabled"
      allowConfigFileUpdates    = true
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}

# Output the default hostname
output "default_hostname" {
  value = azapi_resource.this.output.properties.defaultHostname
}
```

### With Linked Backend (Bring Your Own Functions)

```hcl
# Standard tier required for linked backends
resource "azapi_resource" "this" {
  type      = "Microsoft.Web/staticSites@2023-12-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard"
      tier = "Standard"
    }
    properties = {
      stagingEnvironmentPolicy = "Enabled"
      allowConfigFileUpdates   = true
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}

# Link to existing Function App (Standard tier only)
resource "azapi_resource" "linked_backend" {
  type      = "Microsoft.Web/staticSites/linkedBackends@2023-12-01"
  name      = "default"
  parent_id = azapi_resource.this.id

  body = {
    properties = {
      backendResourceId = var.function_app_id
      region            = var.location
    }
  }
}
```

### RBAC Assignment

Static Web Apps doesn't use ARM RBAC for data-plane access. Deployment is managed via the deployment token (API key). For CI/CD:

```hcl
# Store deployment token in Key Vault for CI/CD pipelines
resource "azapi_resource" "swa_token" {
  type      = "Microsoft.KeyVault/vaults/secrets@2023-07-01"
  name      = "swa-deployment-token"
  parent_id = var.key_vault_id

  body = {
    properties = {
      value = azapi_resource.this.output.properties.apiKey
    }
  }
}
```

### Private Endpoint

```hcl
# Private endpoint requires Standard tier
resource "azapi_resource" "swa_pe" {
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
            privateLinkServiceId = azapi_resource.this.id
            groupIds             = ["staticSites"]
          }
        }
      ]
    }
  }

  tags = var.tags
}

resource "azapi_resource" "swa_pe_dns" {
  count     = var.enable_private_endpoint && var.private_dns_zone_id != null ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01"
  name      = "dns-zone-group"
  parent_id = azapi_resource.swa_pe[0].id

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

Private DNS zone: `privatelink.azurestaticapps.net`

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Static Web App')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Tags to apply')
param tags object = {}

resource staticWebApp 'Microsoft.Web/staticSites@2023-12-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {
    stagingEnvironmentPolicy: 'Enabled'
    allowConfigFileUpdates: true
    buildProperties: {
      skipGithubActionWorkflowGeneration: true  // Manage CI/CD separately
    }
  }
}

output id string = staticWebApp.id
output name string = staticWebApp.name
output defaultHostname string = staticWebApp.properties.defaultHostname
output deploymentToken string = listSecrets(staticWebApp.id, staticWebApp.apiVersion).properties.apiKey
```

### RBAC Assignment

No ARM RBAC for data-plane -- deployment uses the API key (deployment token).

### Private Endpoint

```bicep
@description('Subnet ID for private endpoint')
param subnetId string = ''

@description('Private DNS zone ID')
param privateDnsZoneId string = ''

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = if (!empty(subnetId)) {
  name: 'pe-${name}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'psc-${name}'
        properties: {
          privateLinkServiceId: staticWebApp.id
          groupIds: ['staticSites']
        }
      }
    ]
  }
}
```

## Application Code

Static Web Apps is a hosting platform -- application code patterns are framework-specific. The key integration points are:

### staticwebapp.config.json — Routing and Auth

```json
{
  "routes": [
    {
      "route": "/api/*",
      "allowedRoles": ["authenticated"]
    },
    {
      "route": "/admin/*",
      "allowedRoles": ["admin"]
    }
  ],
  "navigationFallback": {
    "rewrite": "/index.html",
    "exclude": ["/api/*", "/images/*"]
  },
  "responseOverrides": {
    "401": {
      "redirect": "/.auth/login/aad",
      "statusCode": 302
    }
  },
  "platform": {
    "apiRuntime": "node:18"
  }
}
```

### Managed Functions API (api/ directory)

```javascript
// api/src/functions/items.js
const { app } = require("@azure/functions");

app.http("items", {
  methods: ["GET"],
  authLevel: "anonymous",  // Auth handled by SWA routing
  route: "items",
  handler: async (request, context) => {
    const items = [{ id: 1, name: "Item 1" }];
    return { jsonBody: items };
  },
});
```

### Frontend API Calls (no CORS needed)

```javascript
// SWA proxies /api/* to the managed Functions backend
// No CORS configuration needed -- same origin
const response = await fetch("/api/items");
const items = await response.json();

// Authentication info available at /.auth/me
const authResponse = await fetch("/.auth/me");
const { clientPrincipal } = await authResponse.json();
```

## Common Pitfalls

1. **Navigation fallback is required for SPAs** -- Without `navigationFallback` in `staticwebapp.config.json`, direct URL access to client-side routes returns 404.
2. **Managed Functions limited to Node.js and Python** -- Managed Functions (in `api/` directory) only support Node.js and Python runtimes. For C# or other runtimes, use a linked backend (Standard tier).
3. **Free tier has no SLA and limited features** -- No custom auth providers, no private endpoints, no linked backends, no password-protected staging environments. Standard tier needed for production features.
4. **API route prefix is mandatory** -- All API routes must start with `/api/`. This cannot be changed.
5. **Build configuration confusion** -- `app_location`, `api_location`, and `output_location` in the GitHub Action / Azure DevOps task must match your project structure. Misconfiguration causes blank deployments.
6. **Custom domains require DNS validation** -- CNAME or TXT record must be set before custom domain can be added. DNS propagation can take time.
7. **Environment variables vs app settings** -- Frontend environment variables are baked in at build time. Runtime configuration for the API uses app settings (configured in Azure portal or CLI).

## Production Backlog Items

- [ ] Upgrade to Standard tier for SLA and advanced features
- [ ] Configure custom domain with managed SSL certificate
- [ ] Enable private endpoint for internal-only access (Standard tier)
- [ ] Configure custom authentication providers (Azure AD B2C, Auth0)
- [ ] Set up password-protected staging environments
- [ ] Link dedicated Function App backend for production API scaling
- [ ] Configure response headers (CSP, HSTS, X-Frame-Options)
- [ ] Set up monitoring with Application Insights
- [ ] Implement A/B testing with split traffic between environments
