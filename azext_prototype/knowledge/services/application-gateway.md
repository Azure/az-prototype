# Azure Application Gateway
> Regional Layer 7 load balancer with SSL termination, URL-based routing, cookie-based session affinity, and optional Web Application Firewall (WAF) for web traffic.

## When to Use

- **Single-region L7 load balancing** -- route HTTP/HTTPS traffic to backend pools within a VNet
- **URL-based routing** -- route `/api/*` to one backend pool and `/static/*` to another
- **SSL termination** -- offload TLS at the gateway to reduce compute burden on backends
- **WAF protection (v2)** -- OWASP 3.2 rule sets for inbound traffic protection within a region
- **WebSocket and HTTP/2** -- full support for real-time and modern protocols
- NOT suitable for: global traffic distribution (use Front Door), TCP/UDP load balancing (use Load Balancer), or non-HTTP protocols

Choose Application Gateway over Front Door when all backends are in a single region and you need VNet-internal L7 routing. Choose Front Door for multi-region or CDN scenarios.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Tier | Standard_v2 | WAF_v2 for WAF; v1 is legacy |
| SKU capacity | 1 (manual) | Auto-scale 1-2 for POC |
| Subnet | Dedicated /24 | AppGW requires its own subnet, no other resources |
| Frontend IP | Public | Private frontend for internal-only apps |
| Backend protocol | HTTPS | End-to-end TLS recommended |
| Health probe | /health | Custom probe path on backends |
| HTTP to HTTPS redirect | Enabled | Redirect listener on port 80 |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "public_ip" {
  type      = "Microsoft.Network/publicIPAddresses@2024-01-01"
  name      = "pip-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard"  # Required for AppGW v2
    }
    properties = {
      publicIPAllocationMethod = "Static"
    }
  }

  tags = var.tags
}

resource "azapi_resource" "application_gateway" {
  type      = "Microsoft.Network/applicationGateways@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      sku = {
        name     = "Standard_v2"  # or "WAF_v2"
        tier     = "Standard_v2"
        capacity = 1
      }
      gatewayIPConfigurations = [
        {
          name = "appgw-ip-config"
          properties = {
            subnet = {
              id = var.appgw_subnet_id  # Dedicated subnet for AppGW
            }
          }
        }
      ]
      frontendIPConfigurations = [
        {
          name = "appgw-frontend-ip"
          properties = {
            publicIPAddress = {
              id = azapi_resource.public_ip.id
            }
          }
        }
      ]
      frontendPorts = [
        {
          name = "port-443"
          properties = {
            port = 443
          }
        }
        {
          name = "port-80"
          properties = {
            port = 80
          }
        }
      ]
      backendAddressPools = [
        {
          name = "default-backend-pool"
          properties = {
            backendAddresses = [
              {
                fqdn = var.backend_fqdn  # e.g., "myapp.azurewebsites.net"
              }
            ]
          }
        }
      ]
      backendHttpSettingsCollection = [
        {
          name = "default-http-settings"
          properties = {
            port                = 443
            protocol            = "Https"
            cookieBasedAffinity = "Disabled"
            requestTimeout      = 30
            pickHostNameFromBackendAddress = true
            probe = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/applicationGateways/${var.name}/probes/health-probe"
            }
          }
        }
      ]
      httpListeners = [
        {
          name = "https-listener"
          properties = {
            frontendIPConfiguration = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/applicationGateways/${var.name}/frontendIPConfigurations/appgw-frontend-ip"
            }
            frontendPort = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/applicationGateways/${var.name}/frontendPorts/port-443"
            }
            protocol            = "Https"
            sslCertificate = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/applicationGateways/${var.name}/sslCertificates/default-cert"
            }
          }
        }
        {
          name = "http-listener"
          properties = {
            frontendIPConfiguration = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/applicationGateways/${var.name}/frontendIPConfigurations/appgw-frontend-ip"
            }
            frontendPort = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/applicationGateways/${var.name}/frontendPorts/port-80"
            }
            protocol = "Http"
          }
        }
      ]
      requestRoutingRules = [
        {
          name = "https-rule"
          properties = {
            priority = 100
            ruleType = "Basic"
            httpListener = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/applicationGateways/${var.name}/httpListeners/https-listener"
            }
            backendAddressPool = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/applicationGateways/${var.name}/backendAddressPools/default-backend-pool"
            }
            backendHttpSettings = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/applicationGateways/${var.name}/backendHttpSettingsCollection/default-http-settings"
            }
          }
        }
        {
          name = "http-redirect-rule"
          properties = {
            priority = 200
            ruleType = "Basic"
            httpListener = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/applicationGateways/${var.name}/httpListeners/http-listener"
            }
            redirectConfiguration = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/applicationGateways/${var.name}/redirectConfigurations/http-to-https"
            }
          }
        }
      ]
      redirectConfigurations = [
        {
          name = "http-to-https"
          properties = {
            redirectType        = "Permanent"
            targetListener = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/applicationGateways/${var.name}/httpListeners/https-listener"
            }
            includePath         = true
            includeQueryString  = true
          }
        }
      ]
      probes = [
        {
          name = "health-probe"
          properties = {
            protocol                          = "Https"
            path                              = "/health"
            interval                          = 30
            timeout                           = 30
            unhealthyThreshold                = 3
            pickHostNameFromBackendHttpSettings = true
          }
        }
      ]
      sslCertificates = [
        {
          name = "default-cert"
          properties = {
            keyVaultSecretId = var.ssl_certificate_secret_id  # Key Vault certificate URI
          }
        }
      ]
    }
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [var.managed_identity_id]  # For Key Vault certificate access
  }

  tags = var.tags

  response_export_values = ["properties.frontendIPConfigurations[0].properties.publicIPAddress.id"]
}
```

### RBAC Assignment

```hcl
# Grant AppGW managed identity access to Key Vault certificates
resource "azapi_resource" "keyvault_secrets_user" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.key_vault_id}${var.managed_identity_principal_id}keyvault-secrets-user")
  parent_id = var.key_vault_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/4633458b-17de-408a-b874-0445c86b69e6"  # Key Vault Secrets User
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

Application Gateway does not use private endpoints -- it **is** the entry point. For private/internal-only deployments, use a private frontend IP configuration:

```hcl
# Replace the public frontend IP with a private one
resource "azapi_resource" "application_gateway_internal" {
  # Same as above, but replace frontendIPConfigurations with:
  # frontendIPConfigurations = [
  #   {
  #     name = "appgw-frontend-ip"
  #     properties = {
  #       subnet = {
  #         id = var.appgw_subnet_id
  #       }
  #       privateIPAllocationMethod = "Static"
  #       privateIPAddress          = "10.0.5.10"
  #     }
  #   }
  # ]
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Application Gateway')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Subnet ID for Application Gateway (dedicated subnet)')
param subnetId string

@description('Backend FQDN')
param backendFqdn string

@description('Key Vault certificate secret ID')
param sslCertificateSecretId string

@description('User-assigned managed identity ID for Key Vault access')
param managedIdentityId string

@description('Tags to apply')
param tags object = {}

resource publicIp 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
  name: 'pip-${name}'
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
  tags: tags
}

resource appGateway 'Microsoft.Network/applicationGateways@2024-01-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  properties: {
    sku: {
      name: 'Standard_v2'
      tier: 'Standard_v2'
      capacity: 1
    }
    gatewayIPConfigurations: [
      {
        name: 'appgw-ip-config'
        properties: {
          subnet: {
            id: subnetId
          }
        }
      }
    ]
    frontendIPConfigurations: [
      {
        name: 'appgw-frontend-ip'
        properties: {
          publicIPAddress: {
            id: publicIp.id
          }
        }
      }
    ]
    frontendPorts: [
      {
        name: 'port-443'
        properties: {
          port: 443
        }
      }
      {
        name: 'port-80'
        properties: {
          port: 80
        }
      }
    ]
    backendAddressPools: [
      {
        name: 'default-backend-pool'
        properties: {
          backendAddresses: [
            {
              fqdn: backendFqdn
            }
          ]
        }
      }
    ]
    backendHttpSettingsCollection: [
      {
        name: 'default-http-settings'
        properties: {
          port: 443
          protocol: 'Https'
          cookieBasedAffinity: 'Disabled'
          requestTimeout: 30
          pickHostNameFromBackendAddress: true
        }
      }
    ]
    httpListeners: [
      {
        name: 'https-listener'
        properties: {
          frontendIPConfiguration: {
            id: resourceId('Microsoft.Network/applicationGateways/frontendIPConfigurations', name, 'appgw-frontend-ip')
          }
          frontendPort: {
            id: resourceId('Microsoft.Network/applicationGateways/frontendPorts', name, 'port-443')
          }
          protocol: 'Https'
          sslCertificate: {
            id: resourceId('Microsoft.Network/applicationGateways/sslCertificates', name, 'default-cert')
          }
        }
      }
      {
        name: 'http-listener'
        properties: {
          frontendIPConfiguration: {
            id: resourceId('Microsoft.Network/applicationGateways/frontendIPConfigurations', name, 'appgw-frontend-ip')
          }
          frontendPort: {
            id: resourceId('Microsoft.Network/applicationGateways/frontendPorts', name, 'port-80')
          }
          protocol: 'Http'
        }
      }
    ]
    requestRoutingRules: [
      {
        name: 'https-rule'
        properties: {
          priority: 100
          ruleType: 'Basic'
          httpListener: {
            id: resourceId('Microsoft.Network/applicationGateways/httpListeners', name, 'https-listener')
          }
          backendAddressPool: {
            id: resourceId('Microsoft.Network/applicationGateways/backendAddressPools', name, 'default-backend-pool')
          }
          backendHttpSettings: {
            id: resourceId('Microsoft.Network/applicationGateways/backendHttpSettingsCollection', name, 'default-http-settings')
          }
        }
      }
      {
        name: 'http-redirect-rule'
        properties: {
          priority: 200
          ruleType: 'Basic'
          httpListener: {
            id: resourceId('Microsoft.Network/applicationGateways/httpListeners', name, 'http-listener')
          }
          redirectConfiguration: {
            id: resourceId('Microsoft.Network/applicationGateways/redirectConfigurations', name, 'http-to-https')
          }
        }
      }
    ]
    redirectConfigurations: [
      {
        name: 'http-to-https'
        properties: {
          redirectType: 'Permanent'
          targetListener: {
            id: resourceId('Microsoft.Network/applicationGateways/httpListeners', name, 'https-listener')
          }
          includePath: true
          includeQueryString: true
        }
      }
    ]
    probes: [
      {
        name: 'health-probe'
        properties: {
          protocol: 'Https'
          path: '/health'
          interval: 30
          timeout: 30
          unhealthyThreshold: 3
          pickHostNameFromBackendHttpSettings: true
        }
      }
    ]
    sslCertificates: [
      {
        name: 'default-cert'
        properties: {
          keyVaultSecretId: sslCertificateSecretId
        }
      }
    ]
  }
}

output id string = appGateway.id
output publicIpAddress string = publicIp.properties.ipAddress
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Not using a dedicated subnet | Deployment fails; AppGW requires exclusive subnet | Create a `/24` subnet with no other resources or delegations |
| Using Standard (v1) SKU | Missing auto-scale, zone redundancy, Key Vault integration | Always use `Standard_v2` or `WAF_v2` |
| Forgetting health probe customization | Default probe uses `/` which may return 404 on backends | Set probe path to `/health` and configure backend app accordingly |
| Self-referencing resource IDs | Complex nested ID references are error-prone | Use `resourceId()` in Bicep or construct IDs carefully in Terraform |
| SSL certificate Key Vault access | AppGW cannot fetch cert; deployment fails with 403 | Grant managed identity Key Vault Secrets User role |
| Not enabling HTTP-to-HTTPS redirect | Insecure HTTP traffic reaches backends | Add redirect configuration from port 80 listener to port 443 |
| Backend health showing unhealthy | Probe fails because backend rejects AppGW hostname | Set `pickHostNameFromBackendAddress = true` in probe and HTTP settings |
| Subnet too small | Cannot scale out AppGW instances | Use at least `/26` (59 usable IPs); `/24` recommended |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| WAF_v2 upgrade | P1 | Switch to WAF_v2 SKU and enable OWASP 3.2 managed rule sets |
| Auto-scaling | P2 | Configure auto-scale with min/max instance count instead of fixed capacity |
| Zone redundancy | P1 | Deploy across availability zones for 99.95% SLA |
| Custom domain + TLS | P2 | Bind custom domain and automate certificate rotation via Key Vault |
| Diagnostic logging | P2 | Enable access logs, firewall logs, and metrics to Log Analytics |
| URL-based routing | P3 | Separate API and static content to different backend pools |
| Connection draining | P2 | Enable connection draining for graceful backend removal during updates |
| Rewrite rules | P3 | Configure header rewrites for security headers (HSTS, CSP, etc.) |
| Private frontend | P2 | Add private frontend IP for internal-only traffic if needed |
| Backend authentication | P3 | Configure end-to-end TLS with trusted root certificates |
