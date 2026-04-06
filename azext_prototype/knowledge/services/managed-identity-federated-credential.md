---
service_namespace: Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials
display_name: Federated Identity Credential
depends_on:
  - Microsoft.ManagedIdentity/userAssignedIdentities
---

# Federated Identity Credential

> Establishes a trust relationship between a user-assigned managed identity and an external identity provider (GitHub Actions, Kubernetes, etc.) for workload identity federation.

## When to Use
- CI/CD pipelines (GitHub Actions, Azure DevOps) that need to authenticate to Azure without storing secrets
- Kubernetes pods using workload identity to access Azure resources
- Any external workload that needs Azure access via OIDC token exchange

## POC Defaults
- **Issuer**: GitHub Actions (`https://token.actions.githubusercontent.com`) or AKS OIDC issuer
- **Subject**: Repository and environment-specific (e.g., `repo:org/repo:ref:refs/heads/main`)
- **Audiences**: `["api://AzureADTokenExchange"]`

## Terraform Patterns

### GitHub Actions Federation
```hcl
resource "azapi_resource" "github_federation" {
  type      = "Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-07-31-preview"
  name      = "github-actions-main"
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

### AKS Workload Identity
```hcl
resource "azapi_resource" "aks_federation" {
  type      = "Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-07-31-preview"
  name      = "aks-workload-identity"
  parent_id = azapi_resource.managed_identity.id

  body = {
    properties = {
      issuer    = var.aks_oidc_issuer_url
      subject   = "system:serviceaccount:${var.k8s_namespace}:${var.k8s_service_account}"
      audiences = ["api://AzureADTokenExchange"]
    }
  }
}
```

### RBAC Assignment
```hcl
# The federated credential enables authentication. RBAC must still be
# assigned to the parent managed identity for resource access.
```

## Bicep Patterns

### GitHub Actions Federation
```bicep
param githubOrg string
param githubRepo string

resource federation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-07-31-preview' = {
  parent: managedIdentity
  name: 'github-actions-main'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubOrg}/${githubRepo}:ref:refs/heads/main'
    audiences: ['api://AzureADTokenExchange']
  }
}
```

## Application Code

### Python
```python
# Federated credentials are used by CI/CD pipelines, not application code.
# In GitHub Actions, use azure/login with OIDC:
# - uses: azure/login@v2
#   with:
#     client-id: ${{ secrets.AZURE_CLIENT_ID }}
#     tenant-id: ${{ secrets.AZURE_TENANT_ID }}
#     subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
```

### C#
```csharp
// Not used in application code directly. See Python example for CI/CD usage.
```

### Node.js
```typescript
// Not used in application code directly. See Python example for CI/CD usage.
```

## Common Pitfalls
- **Subject must be exact**: The subject claim must exactly match what the external identity provider sends. For GitHub Actions, this includes the ref (branch/tag).
- **Max 20 federated credentials**: Each managed identity supports up to 20 federated credentials.
- **Audience must match**: The audience must be `api://AzureADTokenExchange` for Azure AD token exchange.
- **Not for application runtime**: Federated credentials are for CI/CD and external workloads, not for application code running in Azure (use managed identity directly).

## Production Backlog Items
- Environment-specific federation (main, staging, production branches)
- Conditional access policies on the federated identity
- Monitoring of federated credential usage and token exchange failures
