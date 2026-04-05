---
service_namespace: Microsoft.Storage/storageAccounts/blobServices
display_name: Storage Blob Service
depends_on:
  - Microsoft.Storage/storageAccounts
---

# Storage Blob Service

> The blob service endpoint within a storage account. Configures default access tier, CORS, delete retention, and versioning.

## When to Use
- Required parent for blob containers
- Configure account-level blob policies (soft delete, versioning, change feed)
- Set default access tier and CORS rules

## POC Defaults
- **Delete retention**: 7 days (enables recovery from accidental deletion)
- **Versioning**: Disabled (not needed for POC)
- **Default access tier**: Hot

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "blob_service" {
  type      = "Microsoft.Storage/storageAccounts/blobServices@2023-05-01"
  name      = "default"
  parent_id = azapi_resource.storage_account.id

  body = {
    properties = {
      deleteRetentionPolicy = {
        enabled = true
        days    = 7
      }
    }
  }

  response_export_values = ["*"]
}
```

### RBAC Assignment
```hcl
# Blob service access is granted at the storage account level.
# See the storage-account knowledge file for role assignment patterns.
```

## Bicep Patterns

### Basic Resource
```bicep
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

output blobServiceId string = blobService.id
```

## Application Code

### Python
```python
# Blob service configuration is infrastructure — no app code needed.
# Application code interacts with containers and blobs directly.
# See the storage-blob-container knowledge file.
```

### C#
```csharp
// Blob service configuration is infrastructure — no app code needed.
// Application code interacts with containers and blobs directly.
```

### Node.js
```typescript
// Blob service configuration is infrastructure — no app code needed.
// Application code interacts with containers and blobs directly.
```

## Common Pitfalls
- **Name must be "default"**: The blob service child resource name is always `default`. There is only one blob service per storage account.
- **Diagnostic settings parent**: Use the blob service resource ID (not a string interpolation) as the parent for blob diagnostic settings.
- **Delete retention vs versioning**: Soft delete protects against deletion. Versioning protects against overwrites. They serve different purposes.

## Production Backlog Items
- Blob versioning for overwrite protection
- Change feed for event-driven blob processing
- Last access time tracking for lifecycle management
- Container-level immutability policies for compliance
