# Azure Managed Identity
> Zero-credential authentication for Azure resources, providing automatically managed service principals in Azure AD that eliminate the need for secrets, keys, or certificates in application code.

## When to Use

- **Every Azure deployment** -- managed identity is the foundation for secret-free authentication across all Azure services
- **Application authentication to Azure services** -- App Service, Container Apps, Functions, VMs authenticating to Key Vault, Storage, databases, etc.
- **Cross-service RBAC** -- grant one Azure resource access to another without shared secrets
- **CI/CD pipelines** -- federated identity credentials for GitHub Actions and Azure DevOps without stored secrets

**User-assigned** is strongly preferred for POCs because: (1) lifecycle is decoupled from the resource, (2) a single identity can be shared across multiple resources, (3) RBAC assignments survive resource recreation.

Use **system-assigned** only when: the identity should be tightly coupled to the resource lifecycle, or the resource does not support user-assigned identities.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Type | User-assigned | Shared across app resources; survives resource recreation |
| Name convention | `id-{project}-{env}` | Follow naming strategy |
| RBAC model | Least privilege | Assign narrowest role per target resource |
| Federated credentials | Disabled | Enable only for CI/CD pipeline authentication |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "managed_identity" {
  type      = "Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  tags = var.tags

  response_export_values = ["properties.principalId", "properties.clientId", "properties.tenantId"]
}
```

### Attach to a Resource

```hcl
# Attach user-assigned identity to an App Service
resource "azapi_resource" "web_app" {
  type      = "Microsoft.Web/sites@2023-12-01"
  name      = var.app_name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type         = "UserAssigned"
    identity_ids = [azapi_resource.managed_identity.id]
  }

  body = {
    properties = {
      serverFarmId = var.plan_id
      siteConfig = {
        appSettings = [
          {
            name  = "AZURE_CLIENT_ID"
            value = azapi_resource.managed_identity.output.properties.clientId
          }
        ]
      }
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# Grant the managed identity a role on a target resource
# Example: Key Vault Secrets User
resource "azapi_resource" "kv_secrets_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.key_vault_id}${azapi_resource.managed_identity.output.properties.principalId}kv-secrets-user")
  parent_id = var.key_vault_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/4633458b-17de-408a-b874-0445c86b69e6"  # Key Vault Secrets User
      principalId      = azapi_resource.managed_identity.output.properties.principalId
      principalType    = "ServicePrincipal"
    }
  }
}

# Example: Storage Blob Data Contributor
resource "azapi_resource" "storage_blob_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.storage_account_id}${azapi_resource.managed_identity.output.properties.principalId}storage-blob-contributor")
  parent_id = var.storage_account_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"  # Storage Blob Data Contributor
      principalId      = azapi_resource.managed_identity.output.properties.principalId
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Federated Identity Credential (GitHub Actions)

```hcl
resource "azapi_resource" "github_federation" {
  type      = "Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31"
  name      = "github-actions"
  parent_id = azapi_resource.managed_identity.id

  body = {
    properties = {
      issuer    = "https://token.actions.githubusercontent.com"
      subject   = "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/main"
      audiences = ["api://AzureADTokenExchange"]
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the managed identity')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Tags to apply')
param tags object = {}

resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: name
  location: location
  tags: tags
}

output id string = managedIdentity.id
output name string = managedIdentity.name
output principalId string = managedIdentity.properties.principalId
output clientId string = managedIdentity.properties.clientId
output tenantId string = managedIdentity.properties.tenantId
```

### RBAC Assignment

```bicep
@description('Principal ID of the managed identity')
param principalId string

@description('Key Vault resource to grant access to')
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// Key Vault Secrets User
resource kvSecretsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, principalId, '4633458b-17de-408a-b874-0445c86b69e6')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')  // Key Vault Secrets User
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Fix |
|---------|--------|-----|
| Using system-assigned when user-assigned is better | RBAC assignments lost when resource is recreated | Default to user-assigned; share across related resources |
| Forgetting `AZURE_CLIENT_ID` app setting | `DefaultAzureCredential` cannot select the correct identity on multi-identity resources | Always set `AZURE_CLIENT_ID` to the user-assigned identity's client ID |
| Over-privileged roles | Identity has more access than needed | Assign narrowest role: Reader, not Contributor; Secrets User, not Secrets Officer |
| Role assignment race conditions | Resource deployed before RBAC propagation completes | Add explicit dependency or `dependsOn` from the consuming resource to the role assignment |
| Missing `principalType` on role assignments | ARM must auto-detect type, causing intermittent failures | Always specify `principalType = "ServicePrincipal"` for managed identities |
| Not scoping RBAC to specific resources | Identity has broad access across resource group or subscription | Scope role assignments to individual resources, not resource groups |
| Orphaned identities | Unused identities clutter the tenant | Tag identities with the project they belong to; clean up during decommission |

## Production Backlog Items

- [ ] Audit all RBAC assignments for least-privilege compliance
- [ ] Configure federated identity credentials for CI/CD pipelines (eliminate stored secrets)
- [ ] Set up Azure Policy to enforce managed identity usage on supported resources
- [ ] Review and consolidate identities (reduce sprawl)
- [ ] Enable diagnostic settings on identity usage (sign-in logs)
- [ ] Document identity-to-resource mapping for operational runbooks
- [ ] Consider Managed Identity per environment (dev, staging, prod) for isolation
