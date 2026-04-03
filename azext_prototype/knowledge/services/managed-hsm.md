# Azure Managed HSM
> FIPS 140-2 Level 3 validated, fully managed hardware security module for cryptographic key management, providing single-tenant HSM pools with full administrative control over the security domain.

## When to Use

- Regulatory compliance requiring FIPS 140-2 Level 3 (Key Vault standard is Level 2)
- High-throughput cryptographic operations (TLS offloading, database encryption)
- Full control over the security domain (bring your own key, key sovereignty)
- Single-tenant HSM requirement for financial services, healthcare, or government
- Customer-managed key (CMK) encryption for Azure services requiring Level 3
- NOT suitable for: general-purpose secret storage (use Key Vault), certificate management (use Key Vault), low-volume key operations (use Key Vault -- significantly cheaper), or application configuration (use App Configuration)

**Cost warning**: Managed HSM is significantly more expensive than Key Vault ($4+ per HSM pool per hour). Use Key Vault for POCs unless Level 3 compliance is explicitly required.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Standard_B1 | Only available SKU |
| Initial admin count | 3 | Minimum recommended for security domain quorum |
| Security domain quorum | 2 of 3 | Number of keys needed to recover security domain |
| Network ACLs | Default allow | Restrict for production |
| Soft delete | Enabled (always) | Cannot be disabled; 90-day retention |
| Purge protection | Enabled | Recommended; prevents permanent deletion during retention |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "managed_hsm" {
  type      = "Microsoft.KeyVault/managedHSMs@2023-07-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      family = "B"
      name   = "Standard_B1"
    }
    properties = {
      tenantId                   = var.tenant_id
      initialAdminObjectIds      = var.initial_admin_object_ids  # List of AAD object IDs
      enableSoftDelete           = true
      softDeleteRetentionInDays  = 90
      enablePurgeProtection      = true
      publicNetworkAccess        = "Enabled"  # Disable for production
      networkAcls = {
        bypass        = "AzureServices"
        defaultAction = "Allow"  # Deny for production
      }
    }
  }

  tags = var.tags

  response_export_values = ["properties.hsmUri"]
}
```

### RBAC Assignment

```hcl
# Managed HSM Crypto User -- use keys for encrypt/decrypt/sign/verify
resource "azapi_resource" "hsm_crypto_user" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.managed_hsm.id}${var.app_principal_id}hsm-crypto-user")
  parent_id = azapi_resource.managed_hsm.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/21dbd100-6940-42c2-b190-5d6cb909625b"  # Managed HSM Crypto User
      principalId      = var.app_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Managed HSM Crypto Officer -- manage keys (create, delete, rotate)
resource "azapi_resource" "hsm_crypto_officer" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.managed_hsm.id}${var.admin_principal_id}hsm-crypto-officer")
  parent_id = azapi_resource.managed_hsm.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/515eb02d-2335-4d2d-92f2-b1cbdf9c3778"  # Managed HSM Crypto Officer
      principalId      = var.admin_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

```hcl
resource "azapi_resource" "hsm_private_endpoint" {
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
            privateLinkServiceId = azapi_resource.managed_hsm.id
            groupIds             = ["managedhsm"]
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

Private DNS zone: `privatelink.managedhsm.azure.net`

## Bicep Patterns

### Basic Resource

```bicep
param name string
param location string
param tenantId string
param initialAdminObjectIds array
param tags object = {}

resource managedHsm 'Microsoft.KeyVault/managedHSMs@2023-07-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    family: 'B'
    name: 'Standard_B1'
  }
  properties: {
    tenantId: tenantId
    initialAdminObjectIds: initialAdminObjectIds
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

output id string = managedHsm.id
output name string = managedHsm.name
output hsmUri string = managedHsm.properties.hsmUri
```

### RBAC Assignment

```bicep
param appPrincipalId string

// Managed HSM Crypto User
resource hsmCryptoUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(managedHsm.id, appPrincipalId, '21dbd100-6940-42c2-b190-5d6cb909625b')
  scope: managedHsm
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '21dbd100-6940-42c2-b190-5d6cb909625b')
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
  }
}
```

## CRITICAL: Security Domain Activation

After deploying a Managed HSM, it is in a **provisioned but not activated** state. You must download and activate the security domain before any key operations:

```bash
# Download security domain (requires 3 RSA key pairs for quorum)
az keyvault security-domain download \
  --hsm-name <hsm-name> \
  --sd-wrapping-keys key1.cer key2.cer key3.cer \
  --sd-quorum 2 \
  --security-domain-file sd.json
```

The HSM is **NOT usable** until the security domain is downloaded. This is a one-time operation.

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Not downloading security domain after creation | HSM is provisioned but unusable; no key operations work | Download security domain immediately after deployment |
| Using Managed HSM when Key Vault suffices | 50x+ cost difference (~$4/hr vs pennies per operation) | Use Key Vault unless FIPS 140-2 Level 3 is explicitly required |
| Losing security domain backup | HSM is unrecoverable if all admin access is lost | Store security domain file and key pairs in a secure offline location |
| Insufficient initial admin count | Cannot reach quorum for security domain recovery | Use at least 3 initial admins with quorum of 2 |
| Not enabling purge protection | Keys can be permanently deleted, breaking dependent services | Always enable purge protection for production |
| Confusing HSM RBAC with Key Vault RBAC | Different role names and role definition IDs | Use Managed HSM-specific roles (Crypto User, Crypto Officer) |
| Region availability | Managed HSM not available in all regions | Check region availability before planning deployment |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Private endpoint | P1 | Deploy private endpoint and restrict public network access |
| Network ACL lockdown | P1 | Set default action to Deny and allowlist specific subnets/IPs |
| Security domain backup | P1 | Securely store security domain backup and key pairs offline |
| Key rotation policy | P2 | Implement automated key rotation for all keys |
| Logging and monitoring | P2 | Enable diagnostic settings and route to Log Analytics |
| Disaster recovery | P2 | Plan and test security domain restore procedure |
| Audit access patterns | P3 | Review and minimize RBAC role assignments regularly |
| CMK integration | P2 | Configure Azure services to use HSM-backed customer-managed keys |
| Cost monitoring | P3 | Monitor HSM pool hours and key operation counts |
