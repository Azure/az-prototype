# Azure API Management
> Managed API gateway for publishing, securing, transforming, and monitoring APIs at scale.

## When to Use

- **API gateway** -- centralized entry point for backend APIs with authentication, rate limiting, and caching
- **API versioning and lifecycle** -- manage multiple API versions, deprecation, and developer portal
- **Backend protection** -- shield backend services from direct internet exposure
- **API composition** -- aggregate multiple microservices behind a single facade
- **Cross-cutting concerns** -- apply policies (throttling, transformation, logging) without modifying backend code
- **Developer portal** -- self-service API documentation and subscription management

Prefer API Management when you have multiple APIs or need centralized governance. For simple single-API scenarios, consider using Container Apps or App Service built-in routing instead.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Consumption | No infrastructure cost when idle; pay per execution |
| SKU (alternative) | Developer | Full feature set for development/testing; single-instance, no SLA |
| Managed identity | System-assigned | For authenticating to backend APIs |
| Public network access | Disabled (unless user overrides) | Flag VNet integration as production backlog item |

**CRITICAL:** Non-Consumption tier deployments take **30-45 minutes**. Plan for this in deployment timelines. The v2 SKUs (BasicV2, StandardV2) offer significantly faster deployment times (5-15 minutes).

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "this" {
  type      = "Microsoft.ApiManagement/service@2023-09-01-preview"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type = "SystemAssigned"
  }

  body = {
    sku = {
      name     = "Consumption"
      capacity = 0  # Or "Developer" with capacity 1 for full features
    }
    properties = {
      publisherName  = var.publisher_name
      publisherEmail = var.publisher_email
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}

# API definition
resource "azapi_resource" "example_api" {
  type      = "Microsoft.ApiManagement/service/apis@2023-09-01-preview"
  name      = "example-api"
  parent_id = azapi_resource.this.id

  body = {
    properties = {
      displayName           = "Example API"
      path                  = "example"
      protocols             = ["https"]
      serviceUrl            = var.backend_url  # Backend API endpoint
      apiRevision           = "1"
      subscriptionRequired  = true
    }
  }
}

# API operation
resource "azapi_resource" "get_items" {
  type      = "Microsoft.ApiManagement/service/apis/operations@2023-09-01-preview"
  name      = "get-items"
  parent_id = azapi_resource.example_api.id

  body = {
    properties = {
      displayName = "Get Items"
      method      = "GET"
      urlTemplate = "/items"
      responses = [
        {
          statusCode = 200
        }
      ]
    }
  }
}
```

### RBAC Assignment

```hcl
# API Management Service Contributor -- manage APIM instance
resource "azapi_resource" "apim_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.this.id}-apim-contributor")
  parent_id = azapi_resource.this.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/312a565d-c81f-4fd8-895a-4e21e48d571c"
      principalId      = var.managed_identity_principal_id
    }
  }
}

# Grant APIM's managed identity access to backend services
resource "azapi_resource" "apim_to_backend" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.backend_resource_id}-apim-backend")
  parent_id = var.backend_resource_id

  body = {
    properties = {
      roleDefinitionId = var.backend_role_definition_id  # e.g., Cognitive Services User role ID
      principalId      = azapi_resource.this.output.identity.principalId
    }
  }
}
```

### Private Endpoint

```hcl
resource "azapi_resource" "apim_pe" {
  count     = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints@2023-11-01"
  name      = "pe-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      subnet = {
        id = var.subnet_id
      }
      privateLinkServiceConnections = [
        {
          name = "psc-${var.name}"
          properties = {
            privateLinkServiceId = azapi_resource.this.id
            groupIds             = ["Gateway"]
          }
        }
      ]
    }
  }

  tags = var.tags
}

resource "azapi_resource" "apim_pe_dns" {
  count     = var.enable_private_endpoint && var.private_dns_zone_id != null ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01"
  name      = "dns-zone-group"
  parent_id = azapi_resource.apim_pe[0].id

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "config"
          properties = {
            privateDnsZoneId = var.private_dns_zone_id
          }
        }
      ]
    }
  }
}
```

Private DNS zone: `privatelink.azure-api.net`

**Note:** Private endpoints are not supported on Consumption tier. Use Developer or higher for private endpoint support.

### Backend Authentication Policy (Managed Identity)

```hcl
resource "azapi_resource" "managed_identity_auth_policy" {
  type      = "Microsoft.ApiManagement/service/apis/policies@2023-09-01-preview"
  name      = "policy"
  parent_id = azapi_resource.example_api.id

  body = {
    properties = {
      format = "xml"
      value  = <<XML
<policies>
  <inbound>
    <base />
    <authentication-managed-identity resource="${var.backend_token_scope}" />
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>
XML
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the API Management instance')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Publisher organization name')
param publisherName string

@description('Publisher email address')
param publisherEmail string

@description('Tags to apply')
param tags object = {}

resource apim 'Microsoft.ApiManagement/service@2023-09-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Consumption'
    capacity: 0
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publisherName: publisherName
    publisherEmail: publisherEmail
  }
}

output id string = apim.id
output name string = apim.name
output gatewayUrl string = apim.properties.gatewayUrl
output principalId string = apim.identity.principalId
```

### RBAC Assignment

```bicep
@description('Backend resource ID for APIM to access')
param backendResourceId string

@description('Role definition ID for backend access')
param backendRoleDefinitionId string

resource backendRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(apim.id, backendResourceId, backendRoleDefinitionId)
  scope: backendResourceId  // Scope to the backend resource
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', backendRoleDefinitionId)
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Application Code

No application code patterns for APIM itself -- it is an infrastructure/gateway service. Applications interact with APIM by calling the gateway URL instead of the backend URL directly. APIM policies handle authentication, transformation, and routing.

### Calling APIs through APIM (Client Side)

```python
# Python -- call API through APIM gateway
import requests

apim_url = "https://myapim.azure-api.net/example/items"
headers = {
    "Ocp-Apim-Subscription-Key": "<subscription-key>",  # For POC only
}
response = requests.get(apim_url, headers=headers)
```

For production, replace subscription keys with OAuth 2.0 / JWT validation policies.

## Common Pitfalls

1. **Long deployment times** -- Non-Consumption tier deployments take 30-45 minutes. The v2 SKUs (BasicV2, StandardV2) deploy in 5-15 minutes. Plan accordingly in automated pipelines.
2. **Consumption tier limitations** -- No VNet integration, no private endpoints, no developer portal customization, no built-in cache. Suitable for POC but not production workloads with those requirements.
3. **Backend authentication** -- Use `<authentication-managed-identity>` policy to authenticate to backends. Never pass secrets through APIM policies.
4. **Subscription key exposure** -- Subscription keys (`Ocp-Apim-Subscription-Key`) are shared secrets. For production, implement OAuth 2.0 with `<validate-jwt>` policy instead.
5. **Policy XML errors** -- APIM policies use XML. Malformed XML silently breaks request processing. Always validate policy XML before deployment.
6. **CORS configuration** -- Forgetting CORS policies blocks browser-based API calls. Add `<cors>` policy in inbound for web frontends.
7. **Rate limiting scope** -- `<rate-limit>` policy counts per subscription key by default. Use `<rate-limit-by-key>` for per-IP or per-user throttling.

## Production Backlog Items

- [ ] Upgrade to Premium or StandardV2 tier for VNet integration and higher throughput
- [ ] Configure VNet integration (internal mode) to hide backend services from internet
- [ ] Set up custom domains with TLS certificates for the gateway and developer portal
- [ ] Implement caching policies to reduce backend load
- [ ] Configure rate limiting and quota policies per product/subscription
- [ ] Enable Application Insights integration for API analytics and diagnostics
- [ ] Implement OAuth 2.0 / JWT validation to replace subscription key authentication
- [ ] Configure named values with Key Vault references for policy secrets
- [ ] Set up CI/CD for API definitions using API Management DevOps Resource Kit
- [ ] Enable developer portal with customized branding and documentation
