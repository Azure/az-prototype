---
service_namespace: Microsoft.Storage/storageAccounts/blobServices/containers
display_name: Storage Blob Container
depends_on:
  - Microsoft.Storage/storageAccounts/blobServices
---

# Storage Blob Container

> A logical grouping of blobs within a storage account's blob service. Controls access level and metadata for a set of related blobs.

## When to Use
- Every blob must be stored in a container
- Use separate containers for different access patterns or data classifications
- Container-level access policies control anonymous access (should be disabled)

## POC Defaults
- **Public access**: None (private only)
- **Name**: Application-specific (e.g., `attachments`, `uploads`, `exports`)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "blob_container" {
  type      = "Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01"
  name      = var.container_name
  parent_id = azapi_resource.blob_service.id

  body = {
    properties = {
      publicAccess = "None"
    }
  }
}
```

### RBAC Assignment
```hcl
# Container access is typically granted at the storage account level.
# For container-scoped access, use the container resource ID as the scope:
resource "azapi_resource" "container_blob_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("sha1", "${azapi_resource.blob_container.id}-${var.principal_id}-ba92f5b4")
  parent_id = azapi_resource.blob_container.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"
      principalId      = var.principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource
```bicep
param containerName string

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

output containerId string = container.id
output containerName string = container.name
```

## Application Code

### Python
```python
from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
blob_service = BlobServiceClient(
    account_url="https://<account>.blob.core.windows.net",
    credential=credential
)
container = blob_service.get_container_client(container_name)

# Upload blob
with open("file.txt", "rb") as data:
    container.upload_blob(name="file.txt", data=data, overwrite=True)

# List blobs
for blob in container.list_blobs():
    print(blob.name)
```

### C#
```csharp
using Azure.Identity;
using Azure.Storage.Blobs;

var credential = new DefaultAzureCredential();
var blobService = new BlobServiceClient(
    new Uri("https://<account>.blob.core.windows.net"), credential);
var container = blobService.GetBlobContainerClient(containerName);

// Upload
await container.UploadBlobAsync("file.txt", File.OpenRead("file.txt"));

// List
await foreach (var blob in container.GetBlobsAsync())
{
    Console.WriteLine(blob.Name);
}
```

### Node.js
```typescript
import { BlobServiceClient } from "@azure/storage-blob";
import { DefaultAzureCredential } from "@azure/identity";

const credential = new DefaultAzureCredential();
const blobService = new BlobServiceClient(
  "https://<account>.blob.core.windows.net", credential
);
const container = blobService.getContainerClient(containerName);

// Upload
const blockBlob = container.getBlockBlobClient("file.txt");
await blockBlob.uploadFile("file.txt");

// List
for await (const blob of container.listBlobsFlat()) {
  console.log(blob.name);
}
```

## Common Pitfalls
- **Public access must be None**: Never set `publicAccess` to `Blob` or `Container` — this allows anonymous internet access to blobs.
- **Container names are lowercase**: Container names must be 3-63 characters, lowercase letters, numbers, and hyphens only.
- **Immutability is irreversible**: Once an immutability policy is locked on a container, it cannot be removed or shortened.

## Production Backlog Items
- Immutability policies for compliance (legal hold, time-based retention)
- Lifecycle management rules (tier to cool/archive after N days)
- Container-scoped RBAC for least-privilege access
- Event Grid integration for blob-triggered processing
