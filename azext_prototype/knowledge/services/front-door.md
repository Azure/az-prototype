# Azure Front Door
> Global load balancer and CDN with built-in WAF, SSL offloading, and intelligent traffic routing for web applications.

## When to Use

- **Global traffic distribution** -- route users to the nearest backend across regions
- **Web Application Firewall (WAF)** -- DDoS protection, bot mitigation, OWASP rule sets
- **Custom domains with managed SSL** -- automated certificate provisioning and renewal
- **CDN for static assets** -- cache static content at edge locations worldwide
- **Multi-backend failover** -- health probes with automatic failover between origins

Choose Front Door over Azure Application Gateway when you need global (multi-region) distribution or CDN capabilities. Choose Application Gateway for single-region, VNet-internal load balancing with more granular L7 routing.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Tier | Standard | CDN + basic routing; Premium adds WAF + Private Link |
| WAF | Disabled (POC) | Enable with managed rule sets for production |
| Caching | Enabled for static | Cache CSS/JS/images; bypass for API routes |
| Origin response timeout | 60 seconds | Default; increase for long-running APIs |
| Health probe | Enabled | HEAD requests every 30 seconds |
| Session affinity | Disabled | Stateless backends preferred for POC |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "profile" {
  type      = "Microsoft.Cdn/profiles@2024-02-01"
  name      = var.name
  location  = "global"
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard_AzureFrontDoor"  # or "Premium_AzureFrontDoor"
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}

resource "azapi_resource" "endpoint" {
  type      = "Microsoft.Cdn/profiles/afdEndpoints@2024-02-01"
  name      = var.endpoint_name
  location  = "global"
  parent_id = azapi_resource.profile.id

  body = {
    properties = {
      enabledState = "Enabled"
    }
  }
}

resource "azapi_resource" "origin_group" {
  type      = "Microsoft.Cdn/profiles/originGroups@2024-02-01"
  name      = "default-origin-group"
  parent_id = azapi_resource.profile.id

  body = {
    properties = {
      loadBalancingSettings = {
        sampleSize                = 4
        successfulSamplesRequired = 3
      }
      healthProbeSettings = {
        probePath              = "/health"
        probeProtocol          = "Https"
        probeRequestType       = "HEAD"
        probeIntervalInSeconds = 30
      }
    }
  }
}

resource "azapi_resource" "origin" {
  type      = "Microsoft.Cdn/profiles/originGroups/origins@2024-02-01"
  name      = "primary-origin"
  parent_id = azapi_resource.origin_group.id

  body = {
    properties = {
      hostName                      = var.origin_hostname  # e.g., "myapp.azurewebsites.net"
      httpPort                      = 80
      httpsPort                     = 443
      originHostHeader              = var.origin_hostname
      enforceCertificateNameCheck   = true
      priority                      = 1
      weight                        = 1000
      enabledState                  = "Enabled"
    }
  }
}

resource "azapi_resource" "route" {
  type      = "Microsoft.Cdn/profiles/afdEndpoints/routes@2024-02-01"
  name      = "default-route"
  parent_id = azapi_resource.endpoint.id

  body = {
    properties = {
      originGroup = {
        id = azapi_resource.origin_group.id
      }
      supportedProtocols   = ["Http", "Https"]
      patternsToMatch      = ["/*"]
      forwardingProtocol   = "HttpsOnly"
      httpsRedirect        = "Enabled"
      cacheConfiguration = {
        queryStringCachingBehavior = "IgnoreQueryString"
        compressionSettings = {
          isCompressionEnabled    = true
          contentTypesToCompress  = [
            "text/html", "text/css", "application/javascript",
            "application/json", "image/svg+xml"
          ]
        }
      }
    }
  }
}
```

### WAF Policy (Premium tier)

```hcl
resource "azapi_resource" "waf_policy" {
  type      = "Microsoft.Network/FrontDoorWebApplicationFirewallPolicies@2024-02-01"
  name      = replace(var.name, "-", "")  # No hyphens allowed
  location  = "global"
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Premium_AzureFrontDoor"
    }
    properties = {
      policySettings = {
        mode = "Prevention"
      }
      managedRules = {
        managedRuleSets = [
          {
            ruleSetType    = "Microsoft_DefaultRuleSet"
            ruleSetVersion = "2.1"
            ruleSetAction  = "Block"
          },
          {
            ruleSetType    = "Microsoft_BotManagerRuleSet"
            ruleSetVersion = "1.1"
            ruleSetAction  = "Block"
          }
        ]
      }
    }
  }

  tags = var.tags
}

resource "azapi_resource" "security_policy" {
  type      = "Microsoft.Cdn/profiles/securityPolicies@2024-02-01"
  name      = "waf-policy"
  parent_id = azapi_resource.profile.id

  body = {
    properties = {
      parameters = {
        type = "WebApplicationFirewall"
        wafPolicy = {
          id = azapi_resource.waf_policy.id
        }
        associations = [
          {
            domains = [
              {
                id = azapi_resource.endpoint.id
              }
            ]
            patternsToMatch = ["/*"]
          }
        ]
      }
    }
  }
}
```

### RBAC Assignment

Front Door is typically managed by infrastructure teams. No data-plane RBAC needed -- traffic flows through without authentication at the Front Door level.

```hcl
# CDN Profile Contributor -- manage Front Door configuration
resource "azapi_resource" "fd_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.profile.id}-cdn-contributor")
  parent_id = azapi_resource.profile.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/ec156ff8-a8d1-4d15-830c-5b80698ca432"
      principalId      = var.admin_identity_principal_id
    }
  }
}
```

### Private Endpoint

Front Door Premium supports **Private Link origins** -- connecting to backends via private endpoints:

```hcl
# Premium tier required for Private Link origins
resource "azapi_resource" "private_origin" {
  type      = "Microsoft.Cdn/profiles/originGroups/origins@2024-02-01"
  name      = "private-origin"
  parent_id = azapi_resource.origin_group.id

  body = {
    properties = {
      hostName                    = var.private_origin_hostname
      originHostHeader            = var.private_origin_hostname
      enforceCertificateNameCheck = true
      priority                    = 1
      weight                      = 1000
      enabledState                = "Enabled"
      sharedPrivateLinkResource = {
        privateLink = {
          id = var.app_service_id  # or other PL-supported resource
        }
        privateLinkLocation  = var.location
        requestMessage       = "Front Door Private Link"
        groupId              = "sites"  # Depends on origin type
      }
    }
  }
}
```

**Note:** Private Link origins require manual approval on the backend resource. The `request_message` appears in the backend's private endpoint connections for approval.

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Front Door profile')
param name string

@description('Origin hostname (e.g., myapp.azurewebsites.net)')
param originHostname string

@description('Tags to apply')
param tags object = {}

resource profile 'Microsoft.Cdn/profiles@2024-02-01' = {
  name: name
  location: 'global'
  tags: tags
  sku: {
    name: 'Standard_AzureFrontDoor'
  }
}

resource endpoint 'Microsoft.Cdn/profiles/afdEndpoints@2024-02-01' = {
  parent: profile
  name: 'default-endpoint'
  location: 'global'
  properties: {
    enabledState: 'Enabled'
  }
}

resource originGroup 'Microsoft.Cdn/profiles/originGroups@2024-02-01' = {
  parent: profile
  name: 'default-origin-group'
  properties: {
    loadBalancingSettings: {
      sampleSize: 4
      successfulSamplesRequired: 3
    }
    healthProbeSettings: {
      probePath: '/health'
      probeProtocol: 'Https'
      probeRequestType: 'HEAD'
      probeIntervalInSeconds: 30
    }
  }
}

resource origin 'Microsoft.Cdn/profiles/originGroups/origins@2024-02-01' = {
  parent: originGroup
  name: 'primary'
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
    supportedProtocols: ['Http', 'Https']
    patternsToMatch: ['/*']
    forwardingProtocol: 'HttpsOnly'
    httpsRedirect: 'Enabled'
    cacheConfiguration: {
      queryStringCachingBehavior: 'IgnoreQueryString'
      compressionSettings: {
        isCompressionEnabled: true
        contentTypesToCompress: [
          'text/html'
          'text/css'
          'application/javascript'
          'application/json'
        ]
      }
    }
  }
}

output endpointHostname string = endpoint.properties.hostName
output profileId string = profile.id
```

### RBAC Assignment

No data-plane RBAC needed -- management-plane only.

## Application Code

Front Door is transparent to application code -- requests are proxied without modification. Key integration points:

### Health Probe Endpoint

```python
# Python (FastAPI) -- health endpoint for Front Door probes
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
async def health():
    return {"status": "healthy"}
```

### Extracting Client IP Behind Front Door

```python
# The real client IP is in X-Forwarded-For header
from fastapi import Request

@app.get("/api/info")
async def info(request: Request):
    client_ip = request.headers.get("X-Azure-ClientIP")  # Front Door-specific
    forwarded_for = request.headers.get("X-Forwarded-For")
    return {"client_ip": client_ip, "forwarded_for": forwarded_for}
```

### Restricting Origin to Front Door Only

```python
# Verify requests come from Front Door using X-Azure-FDID header
FRONT_DOOR_ID = "<your-front-door-id>"

@app.middleware("http")
async def verify_front_door(request, call_next):
    fd_id = request.headers.get("X-Azure-FDID")
    if fd_id != FRONT_DOOR_ID:
        return JSONResponse(status_code=403, content={"error": "Direct access forbidden"})
    return await call_next(request)
```

## Common Pitfalls

1. **DNS CNAME validation required for custom domains** -- Must create a `_dnsauth` TXT record or CNAME before Front Door accepts the domain. Propagation delays cause frustrating failures.
2. **WAF policy name cannot contain hyphens** -- Policy names must be alphanumeric only. Use `replace()` in Terraform/Bicep to strip hyphens from the base name.
3. **Caching API responses accidentally** -- Default route caches everything. Add a separate route for `/api/*` with caching disabled, or use `Cache-Control: no-store` headers.
4. **Origin host header mismatch** -- If the origin hostname differs from the custom domain, App Service may reject the request. Set `origin_host_header` to match the backend's expected hostname.
5. **Private Link approval is manual** -- Premium tier Private Link origins require manual approval on the backend. Automate with `az network private-endpoint-connection approve` in deployment scripts.
6. **Standard vs Premium tier confusion** -- Standard = CDN + routing. Premium = CDN + routing + WAF + Private Link origins. WAF is Premium-only.
7. **Long propagation times** -- Profile and endpoint changes can take 10-20 minutes to propagate globally. Plan for this in deployment pipelines.

## Production Backlog Items

- [ ] Upgrade to Premium tier for WAF and Private Link origins
- [ ] Enable WAF with Microsoft_DefaultRuleSet and BotManagerRuleSet
- [ ] Configure custom domain with managed SSL certificate
- [ ] Restrict backend origins to accept traffic only from Front Door (X-Azure-FDID validation)
- [ ] Enable Private Link origins for all backends
- [ ] Configure rate limiting rules in WAF policy
- [ ] Set up geo-filtering rules if needed
- [ ] Enable diagnostic logging to Log Analytics
- [ ] Configure custom error pages (403, 502, 503)
- [ ] Implement cache purge automation for deployments
