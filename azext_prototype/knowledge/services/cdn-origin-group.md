---
service_namespace: Microsoft.Cdn/profiles/originGroups
display_name: Front Door / CDN Origin Group
depends_on:
  - Microsoft.Cdn/profiles
---

# Front Door / CDN Origin Group

> A logical group of backend origins that Front Door load-balances across. Defines health probes and load balancing settings.

## When to Use
- Group multiple origins for load balancing and failover
- Configure health probes to detect unhealthy origins
- One origin group per backend tier (e.g., API backends, static content)

## POC Defaults
- **Health probe**: Enabled, HTTPS, path `/health`, interval 30s
- **Load balancing**: Round robin with 50ms latency sensitivity

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "origin_group" {
  type      = "Microsoft.Cdn/profiles/originGroups@2024-02-01"
  name      = var.origin_group_name
  parent_id = azapi_resource.cdn_profile.id

  body = {
    properties = {
      loadBalancingSettings = {
        sampleSize                = 4
        successfulSamplesRequired = 3
        additionalLatencyInMilliseconds = 50
      }
      healthProbeSettings = {
        probePath        = "/health"
        probeRequestType = "HEAD"
        probeProtocol    = "Https"
        probeIntervalInSeconds = 30
      }
    }
  }
}
```

### RBAC Assignment
```hcl
# Origin group management inherits from the parent CDN profile RBAC.
```

## Bicep Patterns

### Basic Resource
```bicep
param originGroupName string

resource originGroup 'Microsoft.Cdn/profiles/originGroups@2024-02-01' = {
  parent: cdnProfile
  name: originGroupName
  properties: {
    loadBalancingSettings: {
      sampleSize: 4
      successfulSamplesRequired: 3
      additionalLatencyInMilliseconds: 50
    }
    healthProbeSettings: {
      probePath: '/health'
      probeRequestType: 'HEAD'
      probeProtocol: 'Https'
      probeIntervalInSeconds: 30
    }
  }
}

output originGroupId string = originGroup.id
```

## Application Code

### Python
```python
# Origin groups are infrastructure — transparent to application code.
# Ensure the application exposes a /health endpoint for health probes.
```

### C#
```csharp
// Ensure the application exposes a /health endpoint for health probes.
```

### Node.js
```typescript
// Ensure the application exposes a /health endpoint for health probes.
```

## Common Pitfalls
- **Health probe endpoint must exist**: If the probe path returns 4xx/5xx, the origin is marked unhealthy and receives no traffic.
- **Latency sensitivity**: The `additionalLatencyInMilliseconds` setting controls how much latency difference is acceptable before routing to a different origin.

## Production Backlog Items
- Multi-region origin groups for geo-redundancy
- Weighted load balancing for gradual traffic migration
- Private Link origin connections for secure backend access
