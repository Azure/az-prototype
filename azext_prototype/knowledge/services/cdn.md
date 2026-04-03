# Azure CDN
> Global content delivery network for caching and accelerating static and dynamic content with edge locations worldwide.

## When to Use

- **Static content delivery** -- images, CSS, JavaScript, fonts served from edge locations close to users
- **Website acceleration** -- reduce latency for global web applications with dynamic site acceleration
- **Video streaming** -- large-scale media delivery with HTTP-based streaming
- **Software distribution** -- large file downloads distributed from edge PoPs
- **API acceleration** -- reduce latency for globally distributed API consumers

Choose Azure CDN (Standard tier) for simple caching scenarios. Choose Azure Front Door (which uses `Microsoft.Cdn/profiles` with Premium tier) when you also need WAF, Private Link origins, or advanced routing. CDN profiles and Front Door profiles share the same ARM resource type.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Standard_AzureFrontDoor | Recommended over classic CDN SKUs |
| Origin type | Storage / App Service | Static or dynamic content origins |
| Caching | Enabled | Default caching rules for static content |
| Compression | Enabled | Gzip/Brotli for text-based content types |
| HTTPS | Required | HTTP-to-HTTPS redirect |
| Custom domain | Optional | Use CDN-provided endpoint for POC |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "cdn_profile" {
  type      = "Microsoft.Cdn/profiles@2024-02-01"
  name      = var.name
  location  = "global"
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard_AzureFrontDoor"  # or "Standard_Microsoft" for classic CDN
    }
  }

  tags = var.tags
}

resource "azapi_resource" "cdn_endpoint" {
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

### Origin Group and Origin

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
        probePath              = "/health"
        probeRequestType       = "HEAD"
        probeProtocol          = "Https"
        probeIntervalInSeconds = 30
      }
    }
  }
}

resource "azapi_resource" "origin" {
  type      = "Microsoft.Cdn/profiles/originGroups/origins@2024-02-01"
  name      = var.origin_name
  parent_id = azapi_resource.origin_group.id

  body = {
    properties = {
      hostName          = var.origin_hostname  # e.g., "mystorageaccount.blob.core.windows.net"
      httpPort          = 80
      httpsPort         = 443
      originHostHeader  = var.origin_hostname
      priority          = 1
      weight            = 1000
      enabledState      = "Enabled"
      enforceCertificateNameCheck = true
    }
  }
}
```

### Route

```hcl
resource "azapi_resource" "route" {
  type      = "Microsoft.Cdn/profiles/afdEndpoints/routes@2024-02-01"
  name      = var.route_name
  parent_id = azapi_resource.cdn_endpoint.id

  body = {
    properties = {
      originGroup = {
        id = azapi_resource.origin_group.id
      }
      supportedProtocols   = ["Http", "Https"]
      patternsToMatch      = ["/*"]
      forwardingProtocol    = "HttpsOnly"
      linkToDefaultDomain   = "Enabled"
      httpsRedirect         = "Enabled"
      cacheConfiguration = {
        queryStringCachingBehavior = "IgnoreQueryString"
        compressionSettings = {
          isCompressionEnabled = true
          contentTypesToCompress = [
            "text/html",
            "text/css",
            "application/javascript",
            "application/json",
            "image/svg+xml",
            "application/xml"
          ]
        }
      }
    }
  }
}
```

### RBAC Assignment

```hcl
# CDN Profile Contributor -- manage profiles, endpoints, and origins
resource "azapi_resource" "cdn_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.cdn_profile.id}${var.managed_identity_principal_id}cdn-contributor")
  parent_id = azapi_resource.cdn_profile.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ec156ff8-a8d1-4d15-830c-5b80698ca432"  # CDN Profile Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

CDN/Front Door Standard tier does not support Private Link origins. Private Link origins require Premium_AzureFrontDoor SKU:

```hcl
# To use Private Link origins, upgrade to Premium_AzureFrontDoor SKU
# and configure the origin with privateLink settings:
#
# resource "azapi_resource" "origin" {
#   body = {
#     properties = {
#       hostName     = var.origin_hostname
#       sharedPrivateLinkResource = {
#         privateLink = {
#           id = var.origin_resource_id
#         }
#         groupId             = "blob"  # or "sites", etc.
#         privateLinkLocation = var.location
#         requestMessage      = "CDN Private Link"
#         status              = "Approved"
#       }
#     }
#   }
# }
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the CDN profile')
param name string

@description('Endpoint name')
param endpointName string

@description('Origin hostname')
param originHostname string

@description('Tags to apply')
param tags object = {}

resource cdnProfile 'Microsoft.Cdn/profiles@2024-02-01' = {
  name: name
  location: 'global'
  tags: tags
  sku: {
    name: 'Standard_AzureFrontDoor'
  }
}

resource endpoint 'Microsoft.Cdn/profiles/afdEndpoints@2024-02-01' = {
  parent: cdnProfile
  name: endpointName
  location: 'global'
  properties: {
    enabledState: 'Enabled'
  }
}

resource originGroup 'Microsoft.Cdn/profiles/originGroups@2024-02-01' = {
  parent: cdnProfile
  name: 'default-origin-group'
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

resource origin 'Microsoft.Cdn/profiles/originGroups/origins@2024-02-01' = {
  parent: originGroup
  name: 'primary-origin'
  properties: {
    hostName: originHostname
    httpPort: 80
    httpsPort: 443
    originHostHeader: originHostname
    priority: 1
    weight: 1000
    enabledState: 'Enabled'
    enforceCertificateNameCheck: true
  }
}

resource route 'Microsoft.Cdn/profiles/afdEndpoints/routes@2024-02-01' = {
  parent: endpoint
  name: 'default-route'
  properties: {
    originGroup: {
      id: originGroup.id
    }
    supportedProtocols: [
      'Http'
      'Https'
    ]
    patternsToMatch: [
      '/*'
    ]
    forwardingProtocol: 'HttpsOnly'
    linkToDefaultDomain: 'Enabled'
    httpsRedirect: 'Enabled'
  }
  dependsOn: [
    origin  // Origin must exist before route
  ]
}

output id string = cdnProfile.id
output endpointHostName string = endpoint.properties.hostName
```

### RBAC Assignment

```bicep
@description('Principal ID for CDN management')
param principalId string

resource cdnContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(cdnProfile.id, principalId, 'cdn-profile-contributor')
  scope: cdnProfile
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ec156ff8-a8d1-4d15-830c-5b80698ca432')  // CDN Profile Contributor
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Origin must exist before route | Deployment fails with dependency error | Use `dependsOn` or deploy origin before route |
| Propagation delay (10-20 min) | Changes not visible immediately at edge | Plan for propagation time during testing |
| Cache invalidation costs | Purge operations have rate limits | Use versioned URLs (`?v=2`) instead of frequent purges |
| Missing origin host header | Origin receives wrong Host header; returns 404 | Set `originHostHeader` to match origin's expected hostname |
| Classic CDN vs Front Door CDN confusion | Different capabilities and API shapes | Use `Standard_AzureFrontDoor` SKU for new deployments |
| CORS not configured on origin | Browser blocks cross-origin requests | Configure CORS headers on the origin, not the CDN |
| Custom domain DNS validation | Domain not verified; HTTPS fails | Complete DNS CNAME validation before enabling custom domain |
| Compression disabled by default | Larger payloads; higher bandwidth costs | Enable compression and specify content types to compress |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Custom domain with TLS | P2 | Bind custom domain with managed or BYOC certificate |
| WAF policy | P1 | Upgrade to Premium and configure WAF rules for OWASP protection |
| Private Link origins | P1 | Use Premium tier to connect to origins via private endpoints |
| Geo-filtering | P3 | Restrict content delivery to specific countries/regions |
| Rules engine | P2 | Configure URL rewrite, redirect, and header modification rules |
| Monitoring and alerts | P2 | Set up alerts for origin health, cache hit ratio, and bandwidth |
| Cache optimization | P3 | Tune caching rules per content type and path patterns |
| Multi-origin failover | P2 | Configure multiple origins with health probes for HA |
| DDoS protection | P2 | Enable Azure DDoS Protection on the CDN profile |
| Analytics | P3 | Enable CDN analytics for traffic patterns and usage reporting |
