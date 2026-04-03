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
resource "azapi_resource" "storage_account" {
  type      = "Microsoft.Storage/storageAccounts@2023-05-01"
  name      = var.storage_account_name   # 3-24 chars, lowercase alphanumeric only
  location  = azapi_resource.resource_group.output.location
  parent_id = azapi_resource.resource_group.id

  body = {
    kind = "StorageV2"
    sku = {
      name = "Standard_LRS"
    }
    properties = {
      accessTier              = "Hot"
      minimumTlsVersion       = "TLS1_2"
      allowSharedKeyAccess    = false   # CRITICAL: Enforce RBAC-only access
      allowBlobPublicAccess   = false   # CRITICAL: Prevent anonymous public access
      supportsHttpsTrafficOnly = true
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}

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
      containerDeleteRetentionPolicy = {
        enabled = true
        days    = 7
      }
    }
  }
}

resource "azapi_resource" "storage_container" {
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
# Role IDs from service-registry.yaml:
#   Storage Blob Data Reader:      2a2b9908-6ea1-4ae2-8e65-a410df84e7d1
#   Storage Blob Data Contributor: ba92f5b4-2d11-453d-a403-e96b0029c9fe
#   Storage Blob Data Owner:       b7e6dc6d-f1e8-4753-8033-0f276bb0955b

resource "azapi_resource" "storage_blob_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("sha1", "${azapi_resource.storage_account.id}-${azapi_resource.user_assigned_identity.output.properties.principalId}-ba92f5b4-2d11-453d-a403-e96b0029c9fe")
  parent_id = azapi_resource.storage_account.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"  # Storage Blob Data Contributor
      principalId      = azapi_resource.user_assigned_identity.output.properties.principalId
      principalType    = "ServicePrincipal"
    }
  }
}

# If only read access is needed:
resource "azapi_resource" "storage_blob_reader" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("sha1", "${azapi_resource.storage_account.id}-${azapi_resource.user_assigned_identity.output.properties.principalId}-2a2b9908-6ea1-4ae2-8e65-a410df84e7d1")
  parent_id = azapi_resource.storage_account.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"  # Storage Blob Data Reader
      principalId      = azapi_resource.user_assigned_identity.output.properties.principalId
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint
```hcl
resource "azapi_resource" "blob_private_endpoint" {
  type      = "Microsoft.Network/privateEndpoints@2023-11-01"
  name      = "${var.storage_account_name}-blob-pe"
  location  = azapi_resource.resource_group.output.location
  parent_id = azapi_resource.resource_group.id

  body = {
    properties = {
      subnet = {
        id = azapi_resource.private_endpoints_subnet.id
      }
      privateLinkServiceConnections = [
        {
          name = "${var.storage_account_name}-blob-psc"
          properties = {
            privateLinkServiceId = azapi_resource.storage_account.id
            groupIds             = ["blob"]   # Also: "queue", "table", "file" for other sub-resources
          }
        }
      ]
    }
  }

  tags = var.tags
}

resource "azapi_resource" "blob_dns_zone" {
  type      = "Microsoft.Network/privateDnsZones@2020-06-01"
  name      = "privatelink.blob.core.windows.net"
  location  = "global"
  parent_id = azapi_resource.resource_group.id

  tags = var.tags
}

resource "azapi_resource" "blob_dns_zone_link" {
  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01"
  name      = "blob-dns-link"
  location  = "global"
  parent_id = azapi_resource.blob_dns_zone.id

  body = {
    properties = {
      virtualNetwork = {
        id = azapi_resource.virtual_network.id
      }
      registrationEnabled = false
    }
  }

  tags = var.tags
}

resource "azapi_resource" "blob_pe_dns_zone_group" {
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01"
  name      = "default"
  parent_id = azapi_resource.blob_private_endpoint.id

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "config"
          properties = {
            privateDnsZoneId = azapi_resource.blob_dns_zone.id
          }
        }
      ]
    }
  }
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

## CRITICAL: Blob Service Diagnostics
- Diagnostic settings for blob storage **MUST** target the blob service child resource, **NOT** use string interpolation
- CORRECT: `parent_id = azapi_resource.blob_service.id`
- WRONG: `parent_id = "${azapi_resource.storage_account.id}/blobServices/default"` (bypasses Terraform dependency graph)
- Create an explicit `azapi_resource` for `Microsoft.Storage/storageAccounts/blobServices` with name `"default"` and use its `.id` as the diagnostic settings parent

## Common Pitfalls
- **Shared access keys still enabled**: Set `allowSharedKeyAccess = false` in `body.properties` (Terraform azapi) or `allowSharedKeyAccess: false` (Bicep). Without this, anyone with the storage key bypasses RBAC entirely.
- **Public blob access**: Set `allowBlobPublicAccess = false` in `body.properties` (Terraform azapi) or `allowBlobPublicAccess: false` (Bicep) to prevent accidental anonymous access to containers.
- **Storage account naming**: Names must be 3-24 characters, lowercase letters and numbers only. No hyphens, underscores, or uppercase. This is more restrictive than most Azure resources.
- **TLS version**: Always set `minimumTlsVersion = "TLS1_2"`. Older TLS versions are insecure.
- **Deployer needs RBAC too**: When `allowSharedKeyAccess = false`, the deploying principal needs "Storage Blob Data Contributor" to upload blobs during deployment.
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
