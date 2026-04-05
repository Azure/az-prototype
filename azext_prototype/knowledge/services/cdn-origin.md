---
service_namespace: Microsoft.Cdn/profiles/originGroups/origins
display_name: Front Door / CDN Origin
depends_on:
  - Microsoft.Cdn/profiles/originGroups
---

# Front Door / CDN Origin

> A backend server or Azure service that serves content. Origins are grouped into origin groups for load balancing.

## When to Use
- Point to Azure services (App Service, Container Apps, Storage) or custom hostnames
- Each origin group needs at least one origin
- Multiple origins in a group enable failover and load balancing

## POC Defaults
- **HTTP port**: 80
- **HTTPS port**: 443
- **Priority**: 1 (all origins equal in POC)
- **Weight**: 1000 (equal weight)
- **Private Link**: Not enabled for POC

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "origin" {
  type      = "Microsoft.Cdn/profiles/originGroups/origins@2024-02-01"
  name      = var.origin_name
  parent_id = azapi_resource.origin_group.id

  body = {
    properties = {
      hostName          = var.origin_hostname  # e.g., myapp.azurewebsites.net
      httpPort          = 80
      httpsPort         = 443
      originHostHeader  = var.origin_hostname
      priority          = 1
      weight            = 1000
      enabledState      = "Enabled"
    }
  }
}
```

### RBAC Assignment
```hcl
# Origin management inherits from the parent CDN profile RBAC.
```

## Bicep Patterns

### Basic Resource
```bicep
param originName string
param originHostname string

resource origin 'Microsoft.Cdn/profiles/originGroups/origins@2024-02-01' = {
  parent: originGroup
  name: originName
  properties: {
    hostName: originHostname
    httpPort: 80
    httpsPort: 443
    originHostHeader: originHostname
    priority: 1
    weight: 1000
    enabledState: 'Enabled'
  }
}
```

## Application Code

### Python
```python
# Origins are infrastructure — transparent to application code.
```

### C#
```csharp
// Origins are infrastructure — transparent to application code.
```

### Node.js
```typescript
// Origins are infrastructure — transparent to application code.
```

## Common Pitfalls
- **Origin host header**: Must match the backend's expected Host header. For App Service, use the `.azurewebsites.net` hostname.
- **HTTPS required**: Always use HTTPS for origin connections when possible.
- **Private Link for private origins**: Origins behind private endpoints need Private Link integration (Premium SKU).

## Production Backlog Items
- Private Link origin connections for VNet-isolated backends
- Multi-region origins with priority-based failover
- Origin shield for reduced origin load
