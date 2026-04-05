---
service_namespace: Microsoft.Cdn/profiles/afdEndpoints
display_name: Front Door / CDN Endpoint
depends_on:
  - Microsoft.Cdn/profiles
---

# Front Door / CDN Endpoint

> An endpoint within an Azure Front Door or CDN profile that receives traffic on a custom or default domain.

## When to Use
- Every Front Door profile needs at least one endpoint to receive traffic
- Endpoints define the public-facing FQDN for the application
- Multiple endpoints per profile for different applications or environments

## POC Defaults
- **Enabled**: true
- **Domain**: Default Azure-provided FQDN (custom domain for production)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "afd_endpoint" {
  type      = "Microsoft.Cdn/profiles/afdEndpoints@2024-02-01"
  name      = var.endpoint_name
  location  = "global"
  parent_id = azapi_resource.cdn_profile.id

  body = {
    properties = {
      enabledState = "Enabled"
    }
  }

  tags = var.tags
  response_export_values = ["properties.hostName"]
}
```

### RBAC Assignment
```hcl
# Endpoint management inherits from the parent CDN profile RBAC.
# CDN Endpoint Contributor: 426e0c7f-0c7e-4658-b36f-ff54d6c29b45
```

## Bicep Patterns

### Basic Resource
```bicep
param endpointName string

resource endpoint 'Microsoft.Cdn/profiles/afdEndpoints@2024-02-01' = {
  parent: cdnProfile
  name: endpointName
  location: 'global'
  properties: {
    enabledState: 'Enabled'
  }
}

output hostName string = endpoint.properties.hostName
```

## Application Code

### Python
```python
# CDN endpoints are infrastructure — applications are served through the endpoint URL.
# Configure the application's base URL to use the endpoint hostname.
```

### C#
```csharp
// CDN endpoints are infrastructure — configure base URL in appsettings.json.
```

### Node.js
```typescript
// CDN endpoints are infrastructure — configure base URL in environment variables.
```

## Common Pitfalls
- **Location must be "global"**: All Front Door / CDN endpoint resources are global.
- **Endpoint name becomes DNS**: The endpoint name is part of the default FQDN (e.g., `myendpoint.z01.azurefd.net`).
- **Routes required**: An endpoint without routes receives no traffic. Create routes to connect origins.

## Production Backlog Items
- Custom domain with managed TLS certificate
- WAF policy association for application protection
- Multiple endpoints for multi-application profiles
