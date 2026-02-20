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
| Public network access | Enabled (POC) | Flag VNet integration as production backlog item |

**CRITICAL:** Non-Consumption tier deployments take **30-45 minutes**. Plan for this in deployment timelines. The v2 SKUs (BasicV2, StandardV2) offer significantly faster deployment times (5-15 minutes).

## Terraform Patterns

### Basic Resource

```hcl
resource "azurerm_api_management" "this" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  publisher_name      = var.publisher_name
  publisher_email     = var.publisher_email
  sku_name            = "Consumption_0"  # Or "Developer_1" for full features

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

# API definition
resource "azurerm_api_management_api" "example" {
  name                = "example-api"
  resource_group_name = var.resource_group_name
  api_management_name = azurerm_api_management.this.name
  revision            = "1"
  display_name        = "Example API"
  path                = "example"
  protocols           = ["https"]
  service_url         = var.backend_url  # Backend API endpoint

  subscription_required = true
}

# API operation
resource "azurerm_api_management_api_operation" "get_items" {
  operation_id        = "get-items"
  api_name            = azurerm_api_management_api.example.name
  api_management_name = azurerm_api_management.this.name
  resource_group_name = var.resource_group_name
  display_name        = "Get Items"
  method              = "GET"
  url_template        = "/items"

  response {
    status_code = 200
  }
}
```

### RBAC Assignment

```hcl
# API Management Service Contributor -- manage APIM instance
resource "azurerm_role_assignment" "apim_contributor" {
  scope                = azurerm_api_management.this.id
  role_definition_name = "API Management Service Contributor"
  principal_id         = var.managed_identity_principal_id
}

# Grant APIM's managed identity access to backend services
resource "azurerm_role_assignment" "apim_to_backend" {
  scope                = var.backend_resource_id
  role_definition_name = var.backend_role_name  # e.g., "Cognitive Services User"
  principal_id         = azurerm_api_management.this.identity[0].principal_id
}
```

### Private Endpoint

```hcl
resource "azurerm_private_endpoint" "apim" {
  count = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0

  name                = "pe-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id

  private_service_connection {
    name                           = "psc-${var.name}"
    private_connection_resource_id = azurerm_api_management.this.id
    subresource_names              = ["Gateway"]
    is_manual_connection           = false
  }

  dynamic "private_dns_zone_group" {
    for_each = var.private_dns_zone_id != null ? [1] : []
    content {
      name                 = "dns-zone-group"
      private_dns_zone_ids = [var.private_dns_zone_id]
    }
  }

  tags = var.tags
}
```

Private DNS zone: `privatelink.azure-api.net`

**Note:** Private endpoints are not supported on Consumption tier. Use Developer or higher for private endpoint support.

### Backend Authentication Policy (Managed Identity)

```hcl
resource "azurerm_api_management_api_policy" "managed_identity_auth" {
  api_name            = azurerm_api_management_api.example.name
  api_management_name = azurerm_api_management.this.name
  resource_group_name = var.resource_group_name

  xml_content = <<XML
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
