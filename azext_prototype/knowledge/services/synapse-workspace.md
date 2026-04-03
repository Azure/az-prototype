# Azure Synapse Analytics
> Unified analytics platform combining enterprise data warehousing, big data analytics, and data integration with serverless and dedicated SQL pools, Apache Spark, and pipelines.

## When to Use

- **Enterprise data warehousing** -- dedicated SQL pools (formerly SQL Data Warehouse) for large-scale MPP analytics
- **Serverless data exploration** -- query data lake files (Parquet, CSV, JSON) with serverless SQL without provisioning infrastructure
- **Big data analytics** -- Apache Spark pools for data engineering, ML, and large-scale transformations
- **Data integration** -- built-in pipelines (same engine as Data Factory) for ETL/ELT orchestration
- **Unified analytics** -- combine SQL, Spark, and pipelines in a single workspace with shared metadata

Choose Synapse over Databricks when you need dedicated SQL pools (MPP DW), T-SQL compatibility, or tight Power BI integration. Choose Databricks for advanced Spark tuning, MLflow, or multi-cloud portability. Choose Fabric for new projects wanting the latest Microsoft analytics platform.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SQL admin auth | Azure AD + SQL login | SQL login for initial setup; AAD for application access |
| Dedicated SQL pool | DW100c | Smallest tier; pause when not in use to save costs |
| Serverless SQL pool | Built-in | Always available; no provisioning needed |
| Spark pool | Small (3-4 nodes) | Auto-pause after 15 minutes idle |
| Managed VNet | Enabled | Provides managed private endpoints |
| Data exfiltration protection | Disabled for POC | Enable for production |
| ADLS Gen2 | Required | Primary storage for workspace data lake |
| Public network access | Disabled (unless user overrides) | Flag private endpoint as production backlog item |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "synapse_workspace" {
  type      = "Microsoft.Synapse/workspaces@2021-06-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {
      defaultDataLakeStorage = {
        accountUrl = "https://${var.storage_account_name}.dfs.core.windows.net"
        filesystem = var.filesystem_name  # ADLS Gen2 container
      }
      sqlAdministratorLogin         = var.sql_admin_username
      sqlAdministratorLoginPassword = var.sql_admin_password  # Store in Key Vault
      managedVirtualNetwork         = "default"
      managedResourceGroupName      = "${var.resource_group_name}-synapse-managed"
      publicNetworkAccess           = "Disabled"  # Unless told otherwise, disabled per governance policy
      azureADOnlyAuthentication     = false  # Enable after initial setup
    }
  }

  tags = var.tags

  response_export_values = ["properties.connectivityEndpoints"]
}
```

### Firewall Rule (Allow Azure Services)

```hcl
resource "azapi_resource" "firewall_azure" {
  type      = "Microsoft.Synapse/workspaces/firewallRules@2021-06-01"
  name      = "AllowAllWindowsAzureIps"
  parent_id = azapi_resource.synapse_workspace.id

  body = {
    properties = {
      startIpAddress = "0.0.0.0"
      endIpAddress   = "0.0.0.0"
    }
  }
}
```

### Dedicated SQL Pool

```hcl
resource "azapi_resource" "sql_pool" {
  type      = "Microsoft.Synapse/workspaces/sqlPools@2021-06-01"
  name      = var.sql_pool_name
  location  = var.location
  parent_id = azapi_resource.synapse_workspace.id

  body = {
    sku = {
      name = "DW100c"  # Smallest; pause when idle
    }
    properties = {
      collation = "SQL_Latin1_General_CP1_CI_AS"
    }
  }

  tags = var.tags
}
```

### Spark Pool

```hcl
resource "azapi_resource" "spark_pool" {
  type      = "Microsoft.Synapse/workspaces/bigDataPools@2021-06-01"
  name      = var.spark_pool_name
  location  = var.location
  parent_id = azapi_resource.synapse_workspace.id

  body = {
    properties = {
      nodeCount     = 3
      nodeSizeFamily = "MemoryOptimized"
      nodeSize       = "Small"  # 4 vCPU, 32 GiB per node
      autoScale = {
        enabled      = true
        minNodeCount = 3
        maxNodeCount = 5
      }
      autoPause = {
        enabled            = true
        delayInMinutes     = 15  # Auto-pause after 15 min idle
      }
      sparkVersion = "3.4"
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# Grant workspace identity access to ADLS Gen2 storage
resource "azapi_resource" "storage_blob_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.storage_account_id}${azapi_resource.synapse_workspace.output.identity.principalId}storage-blob-contributor")
  parent_id = var.storage_account_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"  # Storage Blob Data Contributor
      principalId      = azapi_resource.synapse_workspace.output.identity.principalId
      principalType    = "ServicePrincipal"
    }
  }
}

# Synapse Administrator role for workspace management
resource "azapi_resource" "synapse_admin" {
  type      = "Microsoft.Synapse/workspaces/roleAssignments@2021-06-01"
  name      = uuidv5("oid", "${azapi_resource.synapse_workspace.id}${var.admin_principal_id}synapse-admin")
  parent_id = azapi_resource.synapse_workspace.id

  body = {
    roleId      = "6e4bf58a-b8e1-4cc3-bbf9-d73143322b78"  # Synapse Administrator
    principalId = var.admin_principal_id
  }
}
```

Synapse RBAC role IDs (workspace-level):
- Synapse Administrator: `6e4bf58a-b8e1-4cc3-bbf9-d73143322b78`
- Synapse SQL Administrator: `7af0c69a-a548-47d6-aea3-d00e69bd83aa`
- Synapse Contributor: `7572bffe-f453-4b66-912a-46cc5ef38fda`
- Synapse User: `2a2b9908-6ea1-4ae2-8e65-a410df84e7d1`

### Private Endpoint

```hcl
# Synapse has multiple private link sub-resources
# Dev: Studio access, Sql: dedicated SQL, SqlOnDemand: serverless SQL

resource "azapi_resource" "synapse_pe_dev" {
  count     = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints@2023-11-01"
  name      = "pe-${var.name}-dev"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      subnet = {
        id = var.subnet_id
      }
      privateLinkServiceConnections = [
        {
          name = "psc-${var.name}-dev"
          properties = {
            privateLinkServiceId = azapi_resource.synapse_workspace.id
            groupIds             = ["Dev"]
          }
        }
      ]
    }
  }

  tags = var.tags
}

resource "azapi_resource" "synapse_pe_sql" {
  count     = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints@2023-11-01"
  name      = "pe-${var.name}-sql"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      subnet = {
        id = var.subnet_id
      }
      privateLinkServiceConnections = [
        {
          name = "psc-${var.name}-sql"
          properties = {
            privateLinkServiceId = azapi_resource.synapse_workspace.id
            groupIds             = ["Sql"]
          }
        }
      ]
    }
  }

  tags = var.tags
}

resource "azapi_resource" "synapse_pe_sqlod" {
  count     = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints@2023-11-01"
  name      = "pe-${var.name}-sqlod"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      subnet = {
        id = var.subnet_id
      }
      privateLinkServiceConnections = [
        {
          name = "psc-${var.name}-sqlod"
          properties = {
            privateLinkServiceId = azapi_resource.synapse_workspace.id
            groupIds             = ["SqlOnDemand"]
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

Private DNS zones:
- Dev (Studio): `privatelink.azuresynapse.net`
- Sql (Dedicated): `privatelink.sql.azuresynapse.net`
- SqlOnDemand (Serverless): `privatelink.sql.azuresynapse.net`

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Synapse workspace')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('ADLS Gen2 storage account name')
param storageAccountName string

@description('ADLS Gen2 filesystem (container) name')
param filesystemName string = 'synapse'

@description('SQL admin login')
@secure()
param sqlAdminLogin string

@description('SQL admin password')
@secure()
param sqlAdminPassword string

@description('Tags to apply')
param tags object = {}

resource workspace 'Microsoft.Synapse/workspaces@2021-06-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    defaultDataLakeStorage: {
      accountUrl: 'https://${storageAccountName}.dfs.core.windows.net'
      filesystem: filesystemName
    }
    sqlAdministratorLogin: sqlAdminLogin
    sqlAdministratorLoginPassword: sqlAdminPassword
    managedVirtualNetwork: 'default'
    publicNetworkAccess: 'Disabled'
  }
}

resource firewallAzure 'Microsoft.Synapse/workspaces/firewallRules@2021-06-01' = {
  parent: workspace
  name: 'AllowAllWindowsAzureIps'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

output id string = workspace.id
output name string = workspace.name
output connectivityEndpoints object = workspace.properties.connectivityEndpoints
output principalId string = workspace.identity.principalId
```

### Dedicated SQL Pool

```bicep
@description('SQL pool name')
param sqlPoolName string

resource sqlPool 'Microsoft.Synapse/workspaces/sqlPools@2021-06-01' = {
  parent: workspace
  name: sqlPoolName
  location: location
  tags: tags
  sku: {
    name: 'DW100c'
  }
  properties: {
    collation: 'SQL_Latin1_General_CP1_CI_AS'
  }
}
```

### Spark Pool

```bicep
@description('Spark pool name')
param sparkPoolName string

resource sparkPool 'Microsoft.Synapse/workspaces/bigDataPools@2021-06-01' = {
  parent: workspace
  name: sparkPoolName
  location: location
  tags: tags
  properties: {
    nodeCount: 3
    nodeSizeFamily: 'MemoryOptimized'
    nodeSize: 'Small'
    autoScale: {
      enabled: true
      minNodeCount: 3
      maxNodeCount: 5
    }
    autoPause: {
      enabled: true
      delayInMinutes: 15
    }
    sparkVersion: '3.4'
  }
}
```

### RBAC Assignment

```bicep
@description('Storage account resource')
param storageAccountId string

var storageBlobContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource storageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccountId, workspace.identity.principalId, storageBlobContributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobContributorRoleId)
    principalId: workspace.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Dedicated SQL pool left running | Continuous billing even when idle (DW100c ~$1.50/hr) | Pause dedicated pools when not in use; use serverless SQL for ad-hoc queries |
| ADLS Gen2 not configured as HNS | Workspace creation fails | Storage account must have hierarchical namespace enabled |
| Managed VNet is immutable | Cannot enable/disable after creation | Enable managed VNet at workspace creation time |
| Synapse RBAC vs Azure RBAC | Workspace permissions not effective | Use Synapse workspace roles (Synapse Administrator, etc.) for data-plane access |
| Spark pool cold start | 2-5 minute startup time for paused pools | Keep pool running during active development sessions |
| Missing storage RBAC | Workspace cannot access data lake | Grant Storage Blob Data Contributor to workspace managed identity |
| SQL pool scaling is slow | 5-15 minutes to scale up/down | Plan capacity changes during low-usage periods |
| Pipeline vs Data Factory confusion | Same engine but different management planes | Synapse pipelines are managed within the workspace; ADF pipelines are standalone |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Private endpoints | P1 | Configure private endpoints for Dev, Sql, and SqlOnDemand sub-resources |
| Data exfiltration protection | P1 | Enable managed private endpoints with data exfiltration protection |
| Azure AD-only authentication | P1 | Disable SQL authentication after setting up AAD admin |
| Dedicated SQL pool sizing | P2 | Right-size DWU based on workload requirements and concurrency |
| Spark pool optimization | P2 | Configure library management and Spark configuration for workloads |
| Pipeline monitoring | P2 | Set up alerts for pipeline failures and long-running activities |
| Customer-managed keys | P3 | Enable CMK for workspace encryption at rest |
| Git integration | P2 | Connect workspace to Git repository for version control |
| Workload management | P3 | Configure workload isolation and classification for dedicated SQL pool |
| Disaster recovery | P3 | Configure geo-backup and restore procedures for dedicated SQL pools |
