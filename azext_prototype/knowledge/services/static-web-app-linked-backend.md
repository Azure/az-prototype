---
service_namespace: Microsoft.Web/staticSites/linkedBackends
display_name: Static Web App Linked Backend
depends_on:
  - Microsoft.Web/staticSites
---

# Static Web App Linked Backend

> Links an Azure Static Web App to a backend API hosted on a separate Azure service (App Service, Azure Functions, Container Apps, or API Management), enabling seamless API proxying.

## When to Use
- Connect a Static Web App's `/api` route to an existing Azure Functions app
- Link to an App Service or Container App backend for full API flexibility
- Replace the built-in managed Functions backend with a separately managed API
- Enable authentication passthrough from the Static Web App to the backend
- Required when the built-in Functions plan is insufficient (need custom runtime, VNet, etc.)

## POC Defaults
- **Backend region**: Same as the Static Web App
- **Backend resource**: Azure Functions app or App Service
- **Path prefix**: `/api` (default proxy path)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "swa_linked_backend" {
  type      = "Microsoft.Web/staticSites/linkedBackends@2024-04-01"
  name      = var.backend_name
  parent_id = azapi_resource.static_web_app.id

  body = {
    properties = {
      backendResourceId = azapi_resource.function_app.id
      region            = var.location
    }
  }
}
```

### RBAC Assignment
```hcl
# The Static Web App needs the ability to configure the backend.
# The linked backend automatically sets up authentication between SWA and the backend.
# No additional RBAC is needed for the link itself, but the backend
# service may need its own identity and role assignments.
```

## Bicep Patterns

### Basic Resource
```bicep
param backendName string
param backendResourceId string
param location string

resource linkedBackend 'Microsoft.Web/staticSites/linkedBackends@2024-04-01' = {
  parent: staticWebApp
  name: backendName
  properties: {
    backendResourceId: backendResourceId
    region: location
  }
}
```

## Application Code

### Python
```python
# The Static Web App proxies /api/* to the linked backend.
# Backend code runs in the linked service (e.g., Azure Functions):
import azure.functions as func

app = func.FunctionApp()

@app.route(route="hello", auth_level=func.AuthLevel.ANONYMOUS)
def hello(req: func.HttpRequest) -> func.HttpResponse:
    # Accessible via https://<swa-domain>/api/hello
    user = req.headers.get("x-ms-client-principal-name", "anonymous")
    return func.HttpResponse(f"Hello, {user}!")
```

### C#
```csharp
// Backend Function accessible via SWA proxy at /api/*
using Microsoft.Azure.Functions.Worker;
using Microsoft.Azure.Functions.Worker.Http;

[Function("hello")]
public HttpResponseData Run(
    [HttpTrigger(AuthorizationLevel.Anonymous, "get")] HttpRequestData req)
{
    // x-ms-client-principal headers are forwarded from SWA auth
    var user = req.Headers.GetValues("x-ms-client-principal-name").FirstOrDefault() ?? "anonymous";
    var response = req.CreateResponse(HttpStatusCode.OK);
    response.WriteString($"Hello, {user}!");
    return response;
}
```

### Node.js
```typescript
import { app, HttpRequest, HttpResponseInit } from "@azure/functions";

// Backend Function accessible via SWA proxy at /api/*
app.http("hello", {
  methods: ["GET"],
  authLevel: "anonymous",
  handler: async (req: HttpRequest): Promise<HttpResponseInit> => {
    // SWA forwards authentication headers
    const user = req.headers.get("x-ms-client-principal-name") ?? "anonymous";
    return { body: `Hello, ${user}!` };
  },
});
```

## Common Pitfalls
- **One linked backend per region**: A Static Web App can have one linked backend per region. Linking a second backend in the same region replaces the first.
- **Backend must be publicly accessible**: The linked backend must have a publicly accessible endpoint. Private endpoint-only backends cannot be linked.
- **Authentication forwarding**: SWA forwards `x-ms-client-principal` headers to the backend. The backend should not have its own authentication layer on top, or it may reject proxied requests.
- **Region alignment**: The backend and Static Web App should be in the same region for lowest latency. Cross-region linking works but adds latency.
- **Managed Functions conflict**: If the Static Web App has a built-in managed Functions backend (`/api` folder in the repo), linking an external backend may conflict. Remove the managed functions first.

## Production Backlog Items
- VNet integration between Static Web App and backend
- Custom authentication configuration for enterprise SSO
- API route configuration beyond the default `/api` prefix
- Backend health monitoring and failover
- Load testing the SWA-to-backend proxy path
