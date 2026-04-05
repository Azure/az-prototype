---
service_namespace: Microsoft.Network/trafficManagerProfiles/azureEndpoints
display_name: Traffic Manager Azure Endpoint
depends_on:
  - Microsoft.Network/trafficManagerProfiles
---

# Traffic Manager Azure Endpoint

> An endpoint within a Traffic Manager profile that points to an Azure resource (App Service, Public IP, Cloud Service) for DNS-based global traffic routing.

## When to Use
- Route traffic to Azure App Service instances in multiple regions
- Load balance across Azure Public IP addresses
- Blue/green or canary deployments using weighted routing
- Disaster recovery failover between Azure regions
- Priority-based routing with automatic failover

## POC Defaults
- **Target resource**: App Service or Public IP
- **Endpoint status**: Enabled
- **Weight**: 1 (equal distribution in weighted routing)
- **Priority**: 1 for primary, 2+ for failover targets

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "tm_azure_endpoint" {
  type      = "Microsoft.Network/trafficManagerProfiles/azureEndpoints@2022-04-01"
  name      = var.endpoint_name
  parent_id = azapi_resource.traffic_manager_profile.id

  body = {
    properties = {
      targetResourceId = azapi_resource.app_service.id
      endpointStatus   = "Enabled"
      weight           = 1
      priority         = 1
    }
  }
}

# Secondary endpoint for failover
resource "azapi_resource" "tm_azure_endpoint_secondary" {
  type      = "Microsoft.Network/trafficManagerProfiles/azureEndpoints@2022-04-01"
  name      = "${var.endpoint_name}-secondary"
  parent_id = azapi_resource.traffic_manager_profile.id

  body = {
    properties = {
      targetResourceId = azapi_resource.app_service_secondary.id
      endpointStatus   = "Enabled"
      weight           = 1
      priority         = 2
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
param targetResourceId string
param priority int = 1

resource azureEndpoint 'Microsoft.Network/trafficManagerProfiles/azureEndpoints@2022-04-01' = {
  parent: trafficManagerProfile
  name: endpointName
  properties: {
    targetResourceId: targetResourceId
    endpointStatus: 'Enabled'
    weight: 1
    priority: priority
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
- **Target must support Traffic Manager**: Only Public IPs (Standard SKU), App Services, and Cloud Services can be Azure endpoints. Internal resources or Private IPs cannot be used.
- **App Service requires custom domain**: Traffic Manager's FQDN (*.trafficmanager.net) must be added as a custom domain on the App Service, or HTTPS validation fails.
- **Health probe path**: The Traffic Manager profile's health probe must return 200 from the endpoint. If the probe path returns 404, the endpoint is marked degraded.
- **DNS TTL affects failover speed**: Traffic Manager uses DNS-based routing. Clients cache DNS for the profile's TTL. Shorter TTLs enable faster failover but increase DNS query volume.
- **Weight vs Priority confusion**: Weighted routing distributes traffic by ratio. Priority routing sends all traffic to the lowest-numbered priority. Don't mix routing intentions.

## Production Backlog Items
- Geographic routing for data sovereignty requirements
- Performance routing for latency-based endpoint selection
- Nested profiles for complex multi-region topologies
- Custom health probe paths per application
- Endpoint monitoring and alerting for degraded states
