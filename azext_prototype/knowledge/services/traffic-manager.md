# Azure Traffic Manager
> DNS-based global traffic distribution service that routes users to the closest or healthiest endpoint across Azure regions, on-premises, or external services.

## When to Use

- **DNS-level global routing** -- distribute traffic across multiple regions using DNS resolution
- **Active-passive failover** -- route to primary region with automatic failover to secondary
- **Performance-based routing** -- direct users to the nearest endpoint by network latency
- **Weighted distribution** -- canary deployments or gradual traffic shifting between endpoints
- **Geographic routing** -- route users based on geographic location (data sovereignty, compliance)
- NOT suitable for: L7 HTTP features (use Front Door), real-time failover (DNS TTL delay), or TLS termination (use Application Gateway/Front Door)

Choose Traffic Manager for simple DNS-based routing. Choose Front Door for L7 HTTP routing with CDN, WAF, and instant failover.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Routing method | Priority | Primary/secondary failover for POC |
| Protocol | HTTPS | Monitor endpoint health over HTTPS |
| Port | 443 | Health probe port |
| Path | /health | Health probe path |
| DNS TTL | 30 seconds | Lower for faster failover; higher for less DNS load |
| Probing interval | 30 seconds | Fast (10s) available with higher cost |
| Tolerated failures | 3 | Number of probe failures before endpoint is marked degraded |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "traffic_manager" {
  type      = "Microsoft.Network/trafficManagerProfiles@2022-04-01"
  name      = var.name
  location  = "global"
  parent_id = var.resource_group_id

  body = {
    properties = {
      profileStatus        = "Enabled"
      trafficRoutingMethod = "Priority"  # "Performance", "Weighted", "Geographic", "MultiValue", "Subnet"
      dnsConfig = {
        relativeName = var.dns_name  # Creates <name>.trafficmanager.net
        ttl          = 30
      }
      monitorConfig = {
        protocol                    = "HTTPS"
        port                        = 443
        path                        = "/health"
        intervalInSeconds           = 30
        toleratedNumberOfFailures   = 3
        timeoutInSeconds            = 10
      }
    }
  }

  tags = var.tags
}
```

### Endpoints (Priority Routing)

```hcl
# Primary endpoint
resource "azapi_resource" "primary_endpoint" {
  type      = "Microsoft.Network/trafficManagerProfiles/azureEndpoints@2022-04-01"
  name      = "primary"
  parent_id = azapi_resource.traffic_manager.id

  body = {
    properties = {
      targetResourceId = var.primary_app_service_id  # App Service, Public IP, etc.
      endpointStatus   = "Enabled"
      priority         = 1
    }
  }
}

# Secondary endpoint (failover)
resource "azapi_resource" "secondary_endpoint" {
  type      = "Microsoft.Network/trafficManagerProfiles/azureEndpoints@2022-04-01"
  name      = "secondary"
  parent_id = azapi_resource.traffic_manager.id

  body = {
    properties = {
      targetResourceId = var.secondary_app_service_id
      endpointStatus   = "Enabled"
      priority         = 2
    }
  }
}
```

### External Endpoint

```hcl
resource "azapi_resource" "external_endpoint" {
  type      = "Microsoft.Network/trafficManagerProfiles/externalEndpoints@2022-04-01"
  name      = "onprem"
  parent_id = azapi_resource.traffic_manager.id

  body = {
    properties = {
      target         = var.external_fqdn  # e.g., "app.contoso.com"
      endpointStatus = "Enabled"
      priority       = 3
      weight         = 1
    }
  }
}
```

### Weighted Routing (Canary)

```hcl
resource "azapi_resource" "traffic_manager_weighted" {
  type      = "Microsoft.Network/trafficManagerProfiles@2022-04-01"
  name      = var.name
  location  = "global"
  parent_id = var.resource_group_id

  body = {
    properties = {
      profileStatus        = "Enabled"
      trafficRoutingMethod = "Weighted"
      dnsConfig = {
        relativeName = var.dns_name
        ttl          = 30
      }
      monitorConfig = {
        protocol = "HTTPS"
        port     = 443
        path     = "/health"
      }
    }
  }

  tags = var.tags
}

resource "azapi_resource" "stable_endpoint" {
  type      = "Microsoft.Network/trafficManagerProfiles/azureEndpoints@2022-04-01"
  name      = "stable"
  parent_id = azapi_resource.traffic_manager_weighted.id

  body = {
    properties = {
      targetResourceId = var.stable_app_id
      endpointStatus   = "Enabled"
      weight           = 90  # 90% of traffic
    }
  }
}

resource "azapi_resource" "canary_endpoint" {
  type      = "Microsoft.Network/trafficManagerProfiles/azureEndpoints@2022-04-01"
  name      = "canary"
  parent_id = azapi_resource.traffic_manager_weighted.id

  body = {
    properties = {
      targetResourceId = var.canary_app_id
      endpointStatus   = "Enabled"
      weight           = 10  # 10% of traffic
    }
  }
}
```

### RBAC Assignment

```hcl
# Traffic Manager Contributor
resource "azapi_resource" "tm_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.traffic_manager.id}-${var.admin_principal_id}-network-contributor")
  parent_id = azapi_resource.traffic_manager.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/4d97b98b-1d4f-4787-a291-c67834d212e7"  # Network Contributor
      principalId      = var.admin_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

Traffic Manager does not use private endpoints -- it is a DNS-based global service that resolves to the public IP addresses of its endpoints. For private routing, use Azure Private DNS with manual failover or Azure Front Door with Private Link origins.

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Traffic Manager profile')
param name string

@description('DNS relative name')
param dnsName string

@description('Routing method')
@allowed(['Priority', 'Performance', 'Weighted', 'Geographic'])
param routingMethod string = 'Priority'

@description('Primary endpoint resource ID')
param primaryEndpointId string

@description('Secondary endpoint resource ID')
param secondaryEndpointId string

@description('Tags to apply')
param tags object = {}

resource trafficManager 'Microsoft.Network/trafficManagerProfiles@2022-04-01' = {
  name: name
  location: 'global'
  tags: tags
  properties: {
    profileStatus: 'Enabled'
    trafficRoutingMethod: routingMethod
    dnsConfig: {
      relativeName: dnsName
      ttl: 30
    }
    monitorConfig: {
      protocol: 'HTTPS'
      port: 443
      path: '/health'
      intervalInSeconds: 30
      toleratedNumberOfFailures: 3
      timeoutInSeconds: 10
    }
  }
}

resource primaryEndpoint 'Microsoft.Network/trafficManagerProfiles/azureEndpoints@2022-04-01' = {
  parent: trafficManager
  name: 'primary'
  properties: {
    targetResourceId: primaryEndpointId
    endpointStatus: 'Enabled'
    priority: 1
  }
}

resource secondaryEndpoint 'Microsoft.Network/trafficManagerProfiles/azureEndpoints@2022-04-01' = {
  parent: trafficManager
  name: 'secondary'
  properties: {
    targetResourceId: secondaryEndpointId
    endpointStatus: 'Enabled'
    priority: 2
  }
}

output fqdn string = trafficManager.properties.dnsConfig.fqdn
output id string = trafficManager.id
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| DNS TTL caching | Failover is not instant; clients cache DNS for TTL duration | Set TTL to 30s for POC; accept trade-off between failover speed and DNS load |
| Health probe path returns non-200 | Endpoint marked degraded; all traffic shifts | Ensure `/health` returns HTTP 200 on all endpoints |
| DNS name collision | `relativeName` must be globally unique under `.trafficmanager.net` | Use project-specific prefix in the DNS name |
| Wrong endpoint type | Azure endpoints vs. external endpoints vs. nested profiles | Use `azureEndpoints` for Azure resources, `externalEndpoints` for non-Azure |
| Geographic routing misconfiguration | Some regions unmapped; users get no response | Ensure all geographic regions are mapped to an endpoint; use a "World" fallback |
| Performance routing without global endpoints | No benefit if all endpoints are in one region | Performance routing only helps with multi-region deployments |
| Monitoring port mismatch | Probe hits wrong port; endpoint appears unhealthy | Match monitor port to actual application listening port |
| Nested profile complexity | Hard to debug routing decisions | Start with simple flat profiles; use nested only when required |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Real user measurements | P2 | Enable Traffic Manager real user measurements for better performance routing |
| Traffic view | P2 | Enable traffic view for geographic traffic analysis |
| Nested profiles | P3 | Use nested profiles for complex multi-region, multi-method routing |
| Custom health checks | P2 | Configure expected status code ranges and custom headers in health probes |
| Fast probing | P2 | Enable fast probing interval (10s) for quicker failover detection |
| Endpoint monitoring alerts | P1 | Configure alerts for endpoint degradation and failover events |
| Diagnostic logging | P2 | Enable diagnostic logs for probe results and routing decisions |
| Custom domain CNAME | P2 | Map vanity domain to Traffic Manager FQDN via CNAME |
| Subnet routing | P3 | Use subnet routing method for fine-grained client-to-endpoint mapping |
| Migration to Front Door | P3 | Evaluate migration to Front Door for L7 features and instant failover |
