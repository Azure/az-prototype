# Azure Batch
> Managed service for running large-scale parallel and high-performance computing (HPC) workloads with automatic VM provisioning and job scheduling.

## When to Use

- **Parallel processing** -- large-scale batch jobs that can be split into independent tasks (rendering, simulations, data processing)
- **HPC workloads** -- computational fluid dynamics, finite element analysis, molecular dynamics
- **Media encoding** -- video transcoding and image processing at scale
- **Data transformation** -- ETL jobs that process millions of files in parallel
- **Machine learning training** -- distributed hyperparameter tuning across many VMs

Choose Batch over AKS when the workload is embarrassingly parallel with independent tasks, does not need long-running infrastructure, and benefits from automatic VM scaling to zero. Choose AKS for always-on microservices or workloads that need Kubernetes orchestration.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Account allocation mode | Batch service | User subscription mode needed for VNet injection |
| Pool VM size | Standard_D2s_v5 | 2 vCPU, 8 GiB; sufficient for POC tasks |
| Pool allocation | Auto-scale | Scale to zero when idle to minimize cost |
| Target dedicated nodes | 0-2 | Low-priority/spot for cost savings |
| OS | Ubuntu 22.04 LTS | Linux preferred for most batch workloads |
| Managed identity | User-assigned | For accessing storage and other Azure resources |
| Public network access | Disabled (unless user overrides) | Flag private endpoint as production backlog item |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "batch_account" {
  type      = "Microsoft.Batch/batchAccounts@2024-02-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type         = "UserAssigned"
    identity_ids = [var.managed_identity_id]
  }

  body = {
    properties = {
      poolAllocationMode  = "BatchService"
      publicNetworkAccess = "Disabled"  # Unless told otherwise, disabled per governance policy
      autoStorage = {
        storageAccountId   = var.storage_account_id
        authenticationMode = "BatchAccountManagedIdentity"
        nodeIdentityReference = {
          resourceId = var.managed_identity_id
        }
      }
      allowedAuthenticationModes = [
        "AAD"  # Disable shared key; use Azure AD only
      ]
    }
  }

  tags = var.tags

  response_export_values = ["properties.accountEndpoint"]
}
```

### Pool

```hcl
resource "azapi_resource" "batch_pool" {
  type      = "Microsoft.Batch/batchAccounts/pools@2024-02-01"
  name      = var.pool_name
  parent_id = azapi_resource.batch_account.id

  body = {
    properties = {
      vmSize = "Standard_D2s_v5"
      deploymentConfiguration = {
        virtualMachineConfiguration = {
          imageReference = {
            publisher = "canonical"
            offer     = "0001-com-ubuntu-server-jammy"
            sku       = "22_04-lts"
            version   = "latest"
          }
          nodeAgentSkuId = "batch.node.ubuntu 22.04"
        }
      }
      scaleSettings = {
        autoScale = {
          formula              = "$TargetDedicatedNodes = max(0, min($PendingTasks.GetSample(TimeInterval_Minute * 5), 4));"
          evaluationInterval   = "PT5M"
        }
      }
      taskSlotsPerNode = 2
      identity = {
        type                    = "UserAssigned"
        userAssignedIdentities = [
          {
            resourceId = var.managed_identity_id
          }
        ]
      }
    }
  }
}
```

### RBAC Assignment

```hcl
# Batch Contributor -- allows submitting and managing jobs
resource "azapi_resource" "batch_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.batch_account.id}${var.managed_identity_principal_id}batch-contributor")
  parent_id = azapi_resource.batch_account.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/b24988ac-6180-42a0-ab88-20f7382dd24c"  # Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Grant batch pool identity access to storage for input/output
resource "azapi_resource" "storage_blob_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.storage_account_id}${var.managed_identity_principal_id}storage-blob-contributor")
  parent_id = var.storage_account_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"  # Storage Blob Data Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

```hcl
resource "azapi_resource" "batch_private_endpoint" {
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
            privateLinkServiceId = azapi_resource.batch_account.id
            groupIds             = ["batchAccount"]
          }
        }
      ]
    }
  }

  tags = var.tags
}

resource "azapi_resource" "batch_pe_dns_zone_group" {
  count     = var.enable_private_endpoint && var.subnet_id != null && var.private_dns_zone_id != null ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01"
  name      = "dns-zone-group"
  parent_id = azapi_resource.batch_private_endpoint[0].id

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "config"
          properties = {
            privateDnsZoneId = var.private_dns_zone_id
          }
        }
      ]
    }
  }
}
```

Private DNS zone: `privatelink.<region>.batch.azure.com`

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Batch account')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Storage account ID for auto-storage')
param storageAccountId string

@description('Managed identity resource ID')
param managedIdentityId string

@description('Tags to apply')
param tags object = {}

resource batchAccount 'Microsoft.Batch/batchAccounts@2024-02-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  properties: {
    poolAllocationMode: 'BatchService'
    publicNetworkAccess: 'Disabled'
    autoStorage: {
      storageAccountId: storageAccountId
      authenticationMode: 'BatchAccountManagedIdentity'
      nodeIdentityReference: {
        resourceId: managedIdentityId
      }
    }
    allowedAuthenticationModes: [
      'AAD'
    ]
  }
}

output id string = batchAccount.id
output name string = batchAccount.name
output accountEndpoint string = batchAccount.properties.accountEndpoint
```

### Pool

```bicep
@description('Pool name')
param poolName string

@description('VM size for pool nodes')
param vmSize string = 'Standard_D2s_v5'

@description('Managed identity resource ID for pool nodes')
param managedIdentityId string

resource pool 'Microsoft.Batch/batchAccounts/pools@2024-02-01' = {
  parent: batchAccount
  name: poolName
  properties: {
    vmSize: vmSize
    deploymentConfiguration: {
      virtualMachineConfiguration: {
        imageReference: {
          publisher: 'canonical'
          offer: '0001-com-ubuntu-server-jammy'
          sku: '22_04-lts'
          version: 'latest'
        }
        nodeAgentSkuId: 'batch.node.ubuntu 22.04'
      }
    }
    scaleSettings: {
      autoScale: {
        formula: '$TargetDedicatedNodes = max(0, min($PendingTasks.GetSample(TimeInterval_Minute * 5), 4));'
        evaluationInterval: 'PT5M'
      }
    }
    taskSlotsPerNode: 2
    identity: {
      type: 'UserAssigned'
      userAssignedIdentities: [
        {
          resourceId: managedIdentityId
        }
      ]
    }
  }
}
```

### RBAC Assignment

```bicep
@description('Principal ID for the managed identity')
param principalId string

resource batchContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(batchAccount.id, principalId, 'contributor')
  scope: batchAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c')  // Contributor
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Auto-storage not configured | Tasks cannot stage input/output files | Always configure `autoStorage` with a storage account |
| Shared key auth left enabled | Security risk; keys can be leaked | Set `allowedAuthenticationModes` to `["AAD"]` only |
| Pool auto-scale formula errors | Pool stuck at 0 nodes or over-provisioned | Test formulas with evaluation endpoint before deploying |
| Node agent SKU mismatch | Pool creation fails silently | Match `nodeAgentSkuId` exactly to the image publisher/offer/sku |
| Missing start task | Nodes lack required software/config | Use start tasks for package installs and environment setup |
| Over-provisioning dedicated nodes | Unnecessary costs for POC | Use low-priority/spot nodes and auto-scale to zero |
| Forgetting task retry policy | Transient failures cause job failure | Set `maxTaskRetryCount` on job/task for fault tolerance |
| User subscription mode complexity | Requires additional VNet and quota config | Use Batch service allocation mode for POC simplicity |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| VNet integration | P1 | Switch to user subscription mode and deploy pools into VNet subnets |
| Private endpoint | P1 | Add private endpoint for Batch account management plane |
| Low-priority/spot nodes | P2 | Use spot VMs for cost savings on fault-tolerant workloads |
| Application packages | P2 | Package and version application binaries for deployment to nodes |
| Job scheduling | P3 | Configure job schedules for recurring batch processing |
| Monitoring and alerts | P2 | Set up alerts for pool resize failures, task failures, and quota usage |
| Customer-managed keys | P3 | Enable CMK encryption for data at rest |
| Container support | P3 | Run tasks in Docker containers for dependency isolation |
| Multi-region pools | P3 | Deploy pools across regions for disaster recovery |
| Certificate management | P3 | Configure certificates for tasks that need TLS client auth |
