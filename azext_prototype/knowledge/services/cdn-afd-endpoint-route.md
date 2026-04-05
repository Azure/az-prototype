---
service_namespace: Microsoft.Cdn/profiles/afdEndpoints/routes
display_name: Front Door / CDN Route
depends_on:
  - Microsoft.Cdn/profiles/afdEndpoints
  - Microsoft.Cdn/profiles/originGroups
---

# Front Door / CDN Route

> Maps incoming requests on an endpoint to an origin group based on path patterns. Controls caching, protocol, and forwarding behavior.

## When to Use
- Every endpoint needs at least one route to forward traffic to origins
- Use multiple routes for different path patterns (e.g., `/api/*` vs `/static/*`)
- Configure caching rules per route

## POC Defaults
- **Patterns to match**: `/*` (catch-all)
- **Forwarding protocol**: HTTPS only
- **Caching**: Disabled for API routes; enabled for static content

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "afd_route" {
  type      = "Microsoft.Cdn/profiles/afdEndpoints/routes@2024-02-01"
  name      = var.route_name
  parent_id = azapi_resource.afd_endpoint.id

  body = {
    properties = {
      originGroup = {
        id = azapi_resource.origin_group.id
      }
      patternsToMatch = ["/*"]
      forwardingProtocol = "HttpsOnly"
      httpsRedirect      = "Enabled"
      linkToDefaultDomain = "Enabled"
    }
  }
}
```

### RBAC Assignment
```hcl
# Route management inherits from the parent CDN profile RBAC.
```

## Bicep Patterns

### Basic Resource
```bicep
param routeName string
param originGroupId string

resource route 'Microsoft.Cdn/profiles/afdEndpoints/routes@2024-02-01' = {
  parent: afdEndpoint
  name: routeName
  properties: {
    originGroup: { id: originGroupId }
    patternsToMatch: ['/*']
    forwardingProtocol: 'HttpsOnly'
    httpsRedirect: 'Enabled'
    linkToDefaultDomain: 'Enabled'
  }
}
```

## Application Code

### Python
```python
# Routes are infrastructure — transparent to application code.
```

### C#
```csharp
// Routes are infrastructure — transparent to application code.
```

### Node.js
```typescript
// Routes are infrastructure — transparent to application code.
```

## Common Pitfalls
- **Route ordering matters**: More specific patterns should be evaluated before catch-all patterns.
- **Origin group required**: A route without an origin group has no backend to forward to.
- **HTTPS redirect**: Always enable `httpsRedirect` to ensure HTTP requests are upgraded.

## Production Backlog Items
- Path-based routing for microservice backends
- Caching rules per route for static vs dynamic content
- Custom rule sets for header manipulation and URL rewrite
