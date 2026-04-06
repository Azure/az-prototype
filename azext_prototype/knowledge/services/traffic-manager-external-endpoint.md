---
service_namespace: Microsoft.Network/trafficManagerProfiles/externalEndpoints
display_name: Traffic Manager External Endpoint
depends_on:
  - Microsoft.Network/trafficManagerProfiles
---

# Traffic Manager External Endpoint

> An endpoint within a Traffic Manager profile that points to an external FQDN or IP address (non-Azure or third-party services) for DNS-based global traffic routing.

## When to Use
- Route traffic to non-Azure endpoints (on-premises servers, other cloud providers)
- Hybrid cloud scenarios with Azure and external backends
- Multi-cloud disaster recovery with failover between Azure and AWS/GCP
- Gradual migration from on-premises to Azure using weighted routing

## POC Defaults
- **Target**: FQDN of the external service
- **Endpoint status**: Enabled
- **Weight**: 1
- **Endpoint location**: Required for performance routing (closest Azure region to the external endpoint)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "tm_external_endpoint" {
  type      = "Microsoft.Network/trafficManagerProfiles/externalEndpoints@2022-04-01"
  name      = var.endpoint_name
  parent_id = azapi_resource.traffic_manager_profile.id

  body = {
    properties = {
      target           = var.external_fqdn
      endpointStatus   = "Enabled"
      weight           = 1
      priority         = 2
      endpointLocation = var.endpoint_region
    }
  }
}
```

### RBAC Assignment
```hcl
# Traffic Manager Contributor role allows endpoint management.
# Inherits from the parent profile RBAC.
```

## Bicep Patterns

### Basic Resource
```bicep
param endpointName string
param externalFqdn string
param priority int = 2
param endpointRegion string = ''

resource externalEndpoint 'Microsoft.Network/trafficManagerProfiles/externalEndpoints@2022-04-01' = {
  parent: trafficManagerProfile
  name: endpointName
  properties: {
    target: externalFqdn
    endpointStatus: 'Enabled'
    weight: 1
    priority: priority
    endpointLocation: !empty(endpointRegion) ? endpointRegion : null
  }
}
```

## Application Code

### Python
Infrastructure — transparent to application code

### C#
Infrastructure — transparent to application code

### Node.js
Infrastructure — transparent to application code

## Common Pitfalls
- **endpointLocation required for performance routing**: If the profile uses performance-based routing, `endpointLocation` must specify the Azure region closest to the external endpoint. Without it, the endpoint is excluded from performance routing decisions.
- **Health probes must be reachable**: Traffic Manager's health probes originate from Azure. External endpoints behind restrictive firewalls must allow traffic from Azure's Traffic Manager probe IP ranges.
- **FQDN only, no paths**: The `target` field accepts only an FQDN (e.g., `api.example.com`), not a URL with path. Health probe path is set at the profile level.
- **No automatic DNS delegation**: Unlike Azure endpoints, external endpoints don't automatically inherit Traffic Manager's DNS. Clients must use the Traffic Manager FQDN.
- **HTTPS probe limitations**: When using HTTPS health probes, the external endpoint's certificate must be valid and trusted by Azure.

## Production Backlog Items
- Geographic routing for data residency compliance
- Performance routing with endpoint location mapping
- Custom headers on health probes for endpoint identification
- Monitoring and alerting for external endpoint health degradation
- Weighted rollout strategy for cloud migration
