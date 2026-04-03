# Azure Disk Encryption Set
> Resource that binds Azure Managed Disks to a customer-managed key (CMK) in Key Vault or Managed HSM, enabling server-side encryption of OS and data disks with keys you control.

## When to Use

- Encrypting VM managed disks with customer-managed keys (CMK) instead of platform-managed keys
- Regulatory compliance requiring customer key control over data-at-rest encryption
- Double encryption (platform key + customer key) for defense-in-depth
- Confidential disk encryption for confidential VMs
- Centralizing encryption key management across multiple VMs and disks
- NOT suitable for: encrypting blobs/files in Storage (use Storage Account CMK directly), encrypting databases (use database-level TDE with CMK), or client-side encryption (use application-level encryption)

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Encryption type | EncryptionAtRestWithCustomerKey | Most common; platform + customer key also available |
| Key source | Key Vault | Managed HSM for FIPS 140-2 Level 3 |
| Key rotation | Manual | Enable auto-rotation for production |
| Identity | System-assigned | For accessing Key Vault |
| Federated client ID | None | Required for cross-tenant Key Vault access |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "disk_encryption_set" {
  type      = "Microsoft.Compute/diskEncryptionSets@2023-10-02"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {
      encryptionType = "EncryptionAtRestWithCustomerKey"
      activeKey = {
        keyUrl = var.key_vault_key_url  # Full versioned or versionless Key Vault key URL
        sourceVault = {
          id = var.key_vault_id
        }
      }
      rotationToLatestKeyVersionEnabled = true  # Auto-rotate to latest key version
    }
  }

  tags = var.tags
}
```

### Key Vault Key (prerequisite)

```hcl
resource "azapi_resource" "encryption_key" {
  type      = "Microsoft.KeyVault/vaults/keys@2023-07-01"
  name      = var.key_name
  parent_id = var.key_vault_id

  body = {
    properties = {
      kty     = "RSA"
      keySize = 4096
      keyOps  = ["wrapKey", "unwrapKey"]
    }
  }

  response_export_values = ["properties.keyUriWithVersion", "properties.keyUri"]
}
```

### RBAC Assignment (Key Vault Access)

```hcl
# Grant DES identity Key Vault Crypto Service Encryption User
# This role allows the DES to wrap/unwrap keys for disk encryption
resource "azapi_resource" "des_key_vault_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.key_vault_id}${azapi_resource.disk_encryption_set.identity[0].principal_id}crypto-service-encryption")
  parent_id = var.key_vault_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/e147488a-f6f5-4113-8e2d-b22465e65bf6"  # Key Vault Crypto Service Encryption User
      principalId      = azapi_resource.disk_encryption_set.identity[0].principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Using DES with a Managed Disk

```hcl
resource "azapi_resource" "managed_disk" {
  type      = "Microsoft.Compute/disks@2023-10-02"
  name      = var.disk_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Premium_LRS"
    }
    properties = {
      diskSizeGB = var.disk_size_gb
      creationData = {
        createOption = "Empty"
      }
      encryption = {
        diskEncryptionSetId = azapi_resource.disk_encryption_set.id
        type                = "EncryptionAtRestWithCustomerKey"
      }
    }
  }

  tags = var.tags
}
```

### Double Encryption

```hcl
resource "azapi_resource" "des_double_encryption" {
  type      = "Microsoft.Compute/diskEncryptionSets@2023-10-02"
  name      = "${var.name}-double"
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {
      encryptionType = "EncryptionAtRestWithPlatformAndCustomerKeys"
      activeKey = {
        keyUrl = var.key_vault_key_url
        sourceVault = {
          id = var.key_vault_id
        }
      }
      rotationToLatestKeyVersionEnabled = true
    }
  }

  tags = var.tags
}
```

## Bicep Patterns

### Basic Resource

```bicep
param name string
param location string
param keyVaultId string
param keyVaultKeyUrl string
param tags object = {}

resource diskEncryptionSet 'Microsoft.Compute/diskEncryptionSets@2023-10-02' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    encryptionType: 'EncryptionAtRestWithCustomerKey'
    activeKey: {
      keyUrl: keyVaultKeyUrl
      sourceVault: {
        id: keyVaultId
      }
    }
    rotationToLatestKeyVersionEnabled: true
  }
}

output id string = diskEncryptionSet.id
output name string = diskEncryptionSet.name
output principalId string = diskEncryptionSet.identity.principalId
```

### RBAC Assignment

```bicep
param keyVaultId string

// Key Vault Crypto Service Encryption User for DES identity
resource cryptoServiceUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVaultId, diskEncryptionSet.identity.principalId, 'e147488a-f6f5-4113-8e2d-b22465e65bf6')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'e147488a-f6f5-4113-8e2d-b22465e65bf6')
    principalId: diskEncryptionSet.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
```

### Using DES with Managed Disk

```bicep
param diskName string
param diskSizeGB int = 128
param diskEncryptionSetId string

resource managedDisk 'Microsoft.Compute/disks@2023-10-02' = {
  name: diskName
  location: location
  sku: {
    name: 'Premium_LRS'
  }
  properties: {
    diskSizeGB: diskSizeGB
    creationData: {
      createOption: 'Empty'
    }
    encryption: {
      diskEncryptionSetId: diskEncryptionSetId
      type: 'EncryptionAtRestWithCustomerKey'
    }
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Not granting Key Vault Crypto Service Encryption User | DES cannot access the key; disk operations fail | Assign the role before creating disks that reference the DES |
| Using access policies instead of RBAC on Key Vault | Inconsistent with governance policy; harder to manage | Use RBAC authorization on Key Vault, not legacy access policies |
| Key Vault soft delete disabled | Key Vault with encryption keys must have soft delete and purge protection | Enable both before creating the DES |
| Key deleted or expired | All disks encrypted with the DES become inaccessible | Enable key auto-rotation and purge protection |
| DES and Key Vault in different regions | Cross-region latency; some scenarios not supported | Keep DES, Key Vault, and disks in the same region |
| Using versioned key URL without auto-rotation | Disks stuck on old key version after rotation | Use versionless key URL with `rotationToLatestKeyVersionEnabled = true` |
| Circular dependency with Key Vault | DES needs Key Vault, but Key Vault may need DES identity for access policy | Create DES first, then grant RBAC, then create disks |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Key auto-rotation | P1 | Enable `rotationToLatestKeyVersionEnabled` and use versionless key URLs |
| Purge protection on Key Vault | P1 | Ensure Key Vault has purge protection enabled to prevent accidental key deletion |
| Double encryption | P2 | Evaluate EncryptionAtRestWithPlatformAndCustomerKeys for defense-in-depth |
| Managed HSM backend | P3 | Switch from Key Vault to Managed HSM for FIPS 140-2 Level 3 compliance |
| Key rotation monitoring | P2 | Set up alerts for key expiration and rotation failures |
| Cross-region DR | P3 | Plan key replication strategy for disaster recovery |
| Confidential disk encryption | P3 | Evaluate ConfidentialVmEncryptedWithCustomerKey for confidential computing |
| Audit key usage | P2 | Enable Key Vault diagnostic logging to track key operations |
