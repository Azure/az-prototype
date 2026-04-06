---
service_namespace: Microsoft.ApiManagement/service/apis
display_name: API Management API
depends_on:
  - Microsoft.ApiManagement/service
---

# API Management API

> An API definition within an Azure API Management (APIM) instance that exposes backend services through a managed gateway with policies for authentication, rate limiting, transformation, and caching.

## When to Use
- Expose backend APIs (Azure Functions, App Service, AKS) through a managed gateway
- Apply cross-cutting concerns (auth, rate limiting, CORS, caching) without changing backend code
- Provide a unified API surface for multiple backend services
- Generate developer portal documentation from OpenAPI specs
- Version and revision management for API lifecycle

## POC Defaults
- **API type**: HTTP (REST)
- **Subscription required**: true (API key authentication)
- **Protocols**: HTTPS only
- **Path**: API-specific prefix (e.g., `/orders`, `/products`)
- **Backend URL**: The actual backend service URL

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "apim_api" {
  type      = "Microsoft.ApiManagement/service/apis@2024-05-01"
  name      = var.api_name
  parent_id = azapi_resource.apim.id

  body = {
    properties = {
      displayName          = var.display_name
      path                 = var.api_path
      protocols            = ["https"]
      subscriptionRequired = true
      serviceUrl           = var.backend_url
      apiType              = "http"
      description          = var.description
      subscriptionKeyParameterNames = {
        header = "Ocp-Apim-Subscription-Key"
        query  = "subscription-key"
      }
    }
  }
}

# Import from OpenAPI specification
resource "azapi_resource" "apim_api_openapi" {
  type      = "Microsoft.ApiManagement/service/apis@2024-05-01"
  name      = var.api_name
  parent_id = azapi_resource.apim.id

  body = {
    properties = {
      displayName = var.display_name
      path        = var.api_path
      protocols   = ["https"]
      format      = "openapi+json"
      value       = file("${path.module}/openapi.json")
      serviceUrl  = var.backend_url
    }
  }
}
```

### RBAC Assignment
```hcl
# API Management Service Contributor role allows API management.
# For developer portal access, use API Management Developer Portal Content Editor.
```

## Bicep Patterns

### Basic Resource
```bicep
param apiName string
param displayName string
param apiPath string
param backendUrl string

resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apimService
  name: apiName
  properties: {
    displayName: displayName
    path: apiPath
    protocols: ['https']
    subscriptionRequired: true
    serviceUrl: backendUrl
    apiType: 'http'
    subscriptionKeyParameterNames: {
      header: 'Ocp-Apim-Subscription-Key'
      query: 'subscription-key'
    }
  }
}

output apiId string = api.id
```

## Application Code

### Python
```python
import requests

# Calling an API through APIM gateway
apim_url = "https://<apim-name>.azure-api.net/<api-path>/endpoint"
headers = {
    "Ocp-Apim-Subscription-Key": subscription_key,
    "Content-Type": "application/json"
}

response = requests.get(apim_url, headers=headers)
print(response.json())
```

### C#
```csharp
using System.Net.Http;

var client = new HttpClient();
client.BaseAddress = new Uri("https://<apim-name>.azure-api.net/<api-path>/");
client.DefaultRequestHeaders.Add("Ocp-Apim-Subscription-Key", subscriptionKey);

var response = await client.GetAsync("endpoint");
var content = await response.Content.ReadAsStringAsync();
Console.WriteLine(content);
```

### Node.js
```typescript
const response = await fetch(
  "https://<apim-name>.azure-api.net/<api-path>/endpoint",
  {
    headers: {
      "Ocp-Apim-Subscription-Key": subscriptionKey,
      "Content-Type": "application/json",
    },
  }
);
const data = await response.json();
console.log(data);
```

## Common Pitfalls
- **Path uniqueness**: Each API must have a unique `path` within the APIM instance. Duplicate paths cause deployment failures.
- **ServiceUrl trailing slash**: Be consistent with trailing slashes on `serviceUrl`. Mismatches can cause double slashes or missing path segments in backend requests.
- **Subscription key header**: The default header `Ocp-Apim-Subscription-Key` must be included in requests. Forgetting it returns 401 Unauthorized.
- **API import replaces operations**: Importing from OpenAPI replaces all existing operations. Incremental updates require careful version management.
- **Backend authentication**: APIM subscription keys authenticate the client to APIM, not to the backend. Configure backend policies (managed identity, certificates) separately.
- **APIM provisioning time**: The Consumption and Developer tiers deploy in minutes, but Premium tier can take 30-45 minutes.

## Production Backlog Items
- OAuth 2.0 / OpenID Connect authentication policies
- Rate limiting and quota policies per subscription
- Request/response transformation policies
- API versioning strategy (URL path, header, or query string)
- Developer portal customization and API documentation
