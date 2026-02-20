# Azure Blob Storage

> Massively scalable object storage for unstructured data including documents, images, video, backups, and data lake workloads.

## When to Use
- Storing unstructured data (documents, images, media, logs, backups)
- Static website hosting or CDN origin
- Data lake foundation for analytics pipelines

## POC Defaults
- **Account kind**: StorageV2 (general-purpose v2)
- **Replication**: Standard_LRS (locally redundant, lowest cost for POC)
- **Access tier**: Hot (frequent access pattern)
- **Shared access key**: Disabled (RBAC-only)

## Terraform Patterns

### Basic Resource
```hcl
resource "azurerm_storage_account" "this" {
  name                            = var.storage_account_name   # 3-24 chars, lowercase alphanumeric only
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  access_tier                     = "Hot"
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = false   # CRITICAL: Enforce RBAC-only access
  allow_nested_items_to_be_public = false   # CRITICAL: Prevent anonymous public access

  blob_properties {
    delete_retention_policy {
      days = 7
    }
    container_delete_retention_policy {
      days = 7
    }
  }

  tags = var.tags
}

resource "azurerm_storage_container" "this" {
  name                  = var.container_name
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}
```

### RBAC Assignment
```hcl
# Role IDs from service-registry.yaml:
#   Storage Blob Data Reader:      2a2b9908-6ea1-4ae2-8e65-a410df84e7d1
#   Storage Blob Data Contributor: ba92f5b4-2d11-453d-a403-e96b0029c9fe
#   Storage Blob Data Owner:       b7e6dc6d-f1e8-4753-8033-0f276bb0955b

resource "azurerm_role_assignment" "storage_blob_contributor" {
  scope                = azurerm_storage_account.this.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.this.principal_id
}

# If only read access is needed:
resource "azurerm_role_assignment" "storage_blob_reader" {
  scope                = azurerm_storage_account.this.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_user_assigned_identity.this.principal_id
}
```

### Private Endpoint
```hcl
resource "azurerm_private_endpoint" "blob" {
  name                = "${var.storage_account_name}-blob-pe"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  subnet_id           = azurerm_subnet.private_endpoints.id

  private_service_connection {
    name                           = "${var.storage_account_name}-blob-psc"
    private_connection_resource_id = azurerm_storage_account.this.id
    is_manual_connection           = false
    subresource_names              = ["blob"]   # Also: "queue", "table", "file" for other sub-resources
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [azurerm_private_dns_zone.blob.id]
  }
}

resource "azurerm_private_dns_zone" "blob" {
  name                = "privatelink.blob.core.windows.net"
  resource_group_name = azurerm_resource_group.this.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "blob" {
  name                  = "blob-dns-link"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.blob.name
  virtual_network_id    = azurerm_virtual_network.this.id
}
```

## Bicep Patterns

### Basic Resource
```bicep
param storageAccountName string   // 3-24 chars, lowercase alphanumeric only
param location string = resourceGroup().location
param containerName string
param tags object = {}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    allowSharedKeyAccess: false           // CRITICAL: Enforce RBAC-only access
    allowBlobPublicAccess: false          // CRITICAL: Prevent anonymous public access
    supportsHttpsTrafficOnly: true
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
  tags: tags
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

output storageAccountId string = storageAccount.id
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob
```

### RBAC Assignment
```bicep
param principalId string

// Storage Blob Data Contributor
var blobContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource blobContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, principalId, blobContributorRoleId)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributorRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Application Code

### Python
```python
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

credential = DefaultAzureCredential()
blob_service = BlobServiceClient(
    account_url="https://<account-name>.blob.core.windows.net",
    credential=credential
)

container_client = blob_service.get_container_client("<container-name>")

# Upload a blob
with open("local-file.txt", "rb") as data:
    container_client.upload_blob(name="remote-file.txt", data=data, overwrite=True)

# Download a blob
blob_client = container_client.get_blob_client("remote-file.txt")
with open("downloaded-file.txt", "wb") as download_file:
    download_stream = blob_client.download_blob()
    download_file.write(download_stream.readall())

# List blobs
for blob in container_client.list_blobs():
    print(f"Blob: {blob.name}, Size: {blob.size}")
```

### C#
```csharp
using Azure.Identity;
using Azure.Storage.Blobs;
using Azure.Storage.Blobs.Models;

var credential = new DefaultAzureCredential();
var blobServiceClient = new BlobServiceClient(
    new Uri("https://<account-name>.blob.core.windows.net"),
    credential
);

var containerClient = blobServiceClient.GetBlobContainerClient("<container-name>");

// Upload a blob
await containerClient.UploadBlobAsync("remote-file.txt", File.OpenRead("local-file.txt"));

// Or overwrite if exists
var blobClient = containerClient.GetBlobClient("remote-file.txt");
await blobClient.UploadAsync(File.OpenRead("local-file.txt"), overwrite: true);

// Download a blob
BlobDownloadResult download = await blobClient.DownloadContentAsync();
await File.WriteAllBytesAsync("downloaded-file.txt", download.Content.ToArray());

// List blobs
await foreach (BlobItem blob in containerClient.GetBlobsAsync())
{
    Console.WriteLine($"Blob: {blob.Name}, Size: {blob.Properties.ContentLength}");
}
```

### Node.js
```typescript
import { DefaultAzureCredential } from "@azure/identity";
import { BlobServiceClient } from "@azure/storage-blob";
import { readFileSync, writeFileSync } from "fs";

const credential = new DefaultAzureCredential();
const blobServiceClient = new BlobServiceClient(
  "https://<account-name>.blob.core.windows.net",
  credential
);

const containerClient = blobServiceClient.getContainerClient("<container-name>");

// Upload a blob
const blockBlobClient = containerClient.getBlockBlobClient("remote-file.txt");
await blockBlobClient.uploadData(readFileSync("local-file.txt"));

// Download a blob
const downloadResponse = await blockBlobClient.download();
const downloaded = await streamToBuffer(downloadResponse.readableStreamBody!);
writeFileSync("downloaded-file.txt", downloaded);

// List blobs
for await (const blob of containerClient.listBlobsFlat()) {
  console.log(`Blob: ${blob.name}, Size: ${blob.properties.contentLength}`);
}

// Helper for download stream
async function streamToBuffer(stream: NodeJS.ReadableStream): Promise<Buffer> {
  const chunks: Buffer[] = [];
  for await (const chunk of stream) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return Buffer.concat(chunks);
}
```

## Common Pitfalls
- **Shared access keys still enabled**: Set `shared_access_key_enabled = false` (Terraform) or `allowSharedKeyAccess: false` (Bicep). Without this, anyone with the storage key bypasses RBAC entirely.
- **Public blob access**: Set `allow_nested_items_to_be_public = false` (Terraform) or `allowBlobPublicAccess: false` (Bicep) to prevent accidental anonymous access to containers.
- **Storage account naming**: Names must be 3-24 characters, lowercase letters and numbers only. No hyphens, underscores, or uppercase. This is more restrictive than most Azure resources.
- **TLS version**: Always set `min_tls_version = "TLS1_2"`. Older TLS versions are insecure.
- **Deployer needs RBAC too**: When `shared_access_key_enabled = false`, the deploying principal needs "Storage Blob Data Contributor" to upload blobs during deployment.
- **Private endpoint per sub-resource**: Blob, Queue, Table, and File each need separate private endpoints if all are used.
- **Firewall timing**: Setting `default_action = "Deny"` on network rules before adding exceptions will block the deployer.

## Production Backlog Items
- Geo-redundant storage (GRS or RA-GRS) for disaster recovery
- Lifecycle management policies (auto-tier Hot -> Cool -> Archive)
- Immutability policies for compliance (WORM storage)
- Network rules with default deny and explicit allow
- Diagnostic settings for access logging and metrics
- Customer-managed encryption keys (CMK) via Key Vault
- Private endpoints for all used sub-resources (blob, queue, table, file)
- Azure Defender for Storage (threat detection)
