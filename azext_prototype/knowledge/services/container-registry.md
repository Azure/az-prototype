# Azure Container Registry
> Managed Docker container registry for storing, managing, and serving container images and OCI artifacts.

## When to Use

- **Container image hosting** -- store and serve Docker images for Container Apps, App Service, Functions, and Kubernetes
- **CI/CD artifact storage** -- push images from build pipelines, pull from deployment targets
- **Helm chart registry** -- store and distribute Helm charts as OCI artifacts
- **Supply chain security** -- image signing, vulnerability scanning, content trust

Container Registry is a foundational infrastructure service. Any architecture using containers (Container Apps, Azure Functions with custom containers, AKS) requires a registry.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Basic | Lowest cost; 10 GiB storage |
| SKU (with geo-replication) | Standard | 100 GiB storage, webhooks |
| Admin user | Disabled | Always use managed identity with AcrPull role |
| Public network access | Enabled (POC) | Flag private endpoint as production backlog item |
| Anonymous pull | Disabled | Require authentication for all image pulls |

## Terraform Patterns

### Basic Resource

```hcl
resource "azurerm_container_registry" "this" {
  name                          = var.name  # Must be globally unique, alphanumeric only
  location                      = var.location
  resource_group_name           = var.resource_group_name
  sku                           = "Basic"
  admin_enabled                 = false  # CRITICAL: Never enable admin user
  public_network_access_enabled = true   # Set false when using private endpoint

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# AcrPull -- allows container runtimes to pull images
resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = var.managed_identity_principal_id
}

# AcrPush -- allows CI/CD pipelines to push images
resource "azurerm_role_assignment" "acr_push" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPush"
  principal_id         = var.ci_identity_principal_id
}
```

RBAC role IDs:
- AcrPull: `7f951dda-4ed3-4680-a7ca-43fe172d538d`
- AcrPush: `8311e382-0749-4cb8-b61a-304f252e45ec`

### Private Endpoint

```hcl
resource "azurerm_private_endpoint" "acr" {
  count = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0

  name                = "pe-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id

  private_service_connection {
    name                           = "psc-${var.name}"
    private_connection_resource_id = azurerm_container_registry.this.id
    subresource_names              = ["registry"]
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

Private DNS zone: `privatelink.azurecr.io`

**Note:** Private endpoints require Premium tier. For POC with Basic/Standard tier, use IP firewall rules or public access.

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the container registry (globally unique, alphanumeric only)')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Tags to apply')
param tags object = {}

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false  // CRITICAL: Never enable admin user
    publicNetworkAccess: 'Enabled'  // Set 'Disabled' when using private endpoint
  }
}

output id string = acr.id
output name string = acr.name
output loginServer string = acr.properties.loginServer
```

### RBAC Assignment

```bicep
@description('Principal ID of the managed identity for image pulling')
param pullPrincipalId string

// AcrPull role
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, pullPrincipalId, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: pullPrincipalId
    principalType: 'ServicePrincipal'
  }
}
```

### Private Endpoint

```bicep
@description('Subnet ID for private endpoint')
param subnetId string = ''

@description('Private DNS zone ID for ACR')
param privateDnsZoneId string = ''

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = if (!empty(subnetId)) {
  name: 'pe-${name}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'psc-${name}'
        properties: {
          privateLinkServiceId: acr.id
          groupIds: ['registry']
        }
      }
    ]
  }
}

resource dnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = if (!empty(subnetId) && !empty(privateDnsZoneId)) {
  parent: privateEndpoint
  name: 'dns-zone-group'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config'
        properties: {
          privateDnsZoneId: privateDnsZoneId
        }
      }
    ]
  }
}
```

Private DNS zone: `privatelink.azurecr.io`

**Note:** Private endpoints require Premium tier. For POC with Basic/Standard tier, use IP firewall rules or public access.

## Application Code

No application code patterns -- Azure Container Registry is a pure infrastructure service. Interaction happens via:

- **Docker CLI**: `docker push`, `docker pull` (with `az acr login` for AAD auth)
- **Azure CLI**: `az acr build` for cloud builds, `az acr login` for authentication
- **CI/CD pipelines**: Push images during build, pull during deployment

### Docker Authentication with Managed Identity

```bash
# Local development (uses Azure CLI credential)
az acr login --name myregistry

# CI/CD pipeline (uses service principal or managed identity)
az acr login --name myregistry --expose-token
```

### Container Apps Integration

Container Apps pull images using the managed identity assigned to the Container App with `AcrPull` role. No explicit login is needed -- configure the registry in the Container App environment:

```hcl
# Terraform: Container App referencing ACR
resource "azurerm_container_app" "this" {
  # ...
  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.this.id
  }
}
```

## Common Pitfalls

1. **Enabling admin user** -- Admin credentials are shared secrets and violate governance policies. Always use managed identity with `AcrPull` role instead.
2. **Registry name constraints** -- Must be globally unique, 5-50 characters, alphanumeric only (no hyphens or underscores). Use the naming strategy to generate valid names.
3. **SKU limitations for private endpoints** -- Private endpoints require Premium tier. Basic and Standard tiers only support IP firewall rules.
4. **Forgetting AcrPull role for container runtimes** -- Container Apps, App Service, and Functions need the `AcrPull` role on the registry to pull images. Without it, deployments fail with authentication errors.
5. **Image tag mutability** -- By default, tags are mutable (`:latest` can be overwritten). For supply chain security, use immutable tags or content trust.
6. **Storage limits by tier** -- Basic: 10 GiB, Standard: 100 GiB, Premium: 500 GiB. Monitor storage usage and clean up untagged manifests.

## Production Backlog Items

- [ ] Upgrade to Premium tier for private endpoints and geo-replication
- [ ] Enable private endpoint and disable public network access
- [ ] Configure geo-replication for multi-region availability
- [ ] Enable content trust for image signing
- [ ] Enable vulnerability scanning with Microsoft Defender for Containers
- [ ] Configure retention policies for untagged manifests
- [ ] Set up webhook notifications for image push/delete events
- [ ] Implement image quarantine workflow (push, scan, approve, release)
- [ ] Configure repository-scoped tokens for granular access control
