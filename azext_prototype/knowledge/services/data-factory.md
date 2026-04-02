# Azure Data Factory
> Cloud-based ETL/ELT service for orchestrating data integration pipelines at scale with 100+ built-in connectors and visual authoring.

## When to Use

- **Data integration** -- move and transform data between Azure services, on-premises databases, SaaS applications, and cloud storage
- **ETL/ELT orchestration** -- scheduled or event-driven pipelines for data warehousing and analytics
- **Data migration** -- bulk copy from on-premises to Azure (SQL Server → Azure SQL, files → Blob/ADLS)
- **Hybrid connectivity** -- connect to on-premises data sources via self-hosted integration runtime
- **Low-code data workflows** -- visual pipeline designer with mapping data flows for transformations

Choose Data Factory over Fabric Data Pipelines when you need ARM-level control, VNet integration, or have existing ADF investments. Choose Fabric when you want unified analytics with Spark, warehousing, and BI in one platform.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Version | V2 | V1 is deprecated |
| Integration runtime | Azure (auto-resolve) | Managed; no infrastructure to maintain |
| Data flow compute | General Purpose, 8 cores | Minimum for mapping data flows |
| Git integration | Disabled (POC) | Enable for production CI/CD |
| Managed VNet | Disabled (POC) | Flag as production backlog item |
| Public network access | Disabled (unless user overrides) | Flag private endpoint as production backlog item |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "this" {
  type      = "Microsoft.DataFactory/factories@2018-06-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {
      publicNetworkAccess = "Disabled"  # Unless told otherwise, disabled per governance policy
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}
```

### Linked Service (Azure SQL)

```hcl
resource "azapi_resource" "ls_azuresql" {
  type      = "Microsoft.DataFactory/factories/linkedservices@2018-06-01"
  name      = "ls-azuresql"
  parent_id = azapi_resource.this.id

  body = {
    properties = {
      type = "AzureSqlDatabase"
      typeProperties = {
        connectionString = "Integrated Security=False;Data Source=${var.sql_server_fqdn};Initial Catalog=${var.database_name};"
        credential = {
          referenceName = "ManagedIdentityCredential"
          type          = "CredentialReference"
        }
      }
    }
  }
}
```

### Linked Service (Blob Storage)

```hcl
resource "azapi_resource" "ls_blob" {
  type      = "Microsoft.DataFactory/factories/linkedservices@2018-06-01"
  name      = "ls-blob"
  parent_id = azapi_resource.this.id

  body = {
    properties = {
      type = "AzureBlobStorage"
      typeProperties = {
        serviceEndpoint = "https://${var.storage_account_name}.blob.core.windows.net"
        credential = {
          referenceName = "ManagedIdentityCredential"
          type          = "CredentialReference"
        }
      }
    }
  }
}
```

### Pipeline with Copy Activity

```hcl
resource "azapi_resource" "pipeline_copy" {
  type      = "Microsoft.DataFactory/factories/pipelines@2018-06-01"
  name      = "pl-copy-data"
  parent_id = azapi_resource.this.id

  body = {
    properties = {
      activities = [
        {
          name = "CopyFromBlobToSQL"
          type = "Copy"
          inputs = [{ referenceName = "ds-blob-csv", type = "DatasetReference" }]
          outputs = [{ referenceName = "ds-sql-table", type = "DatasetReference" }]
          typeProperties = {
            source = { type = "DelimitedTextSource" }
            sink   = { type = "AzureSqlSink", writeBehavior = "upsert", upsertSettings = { useTempDB = true } }
          }
        }
      ]
    }
  }
}
```

### RBAC Assignment

```hcl
# Data Factory Contributor -- manage pipelines and triggers
resource "azapi_resource" "adf_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.this.id}-adf-contributor")
  parent_id = azapi_resource.this.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/673868aa-7521-48a0-acc6-0f60742d39f5"
      principalId      = var.admin_identity_principal_id
    }
  }
}

# Grant ADF's managed identity access to data sources
resource "azapi_resource" "adf_blob_reader" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.storage_account_id}-blob-reader")
  parent_id = var.storage_account_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"
      principalId      = azapi_resource.this.output.identity.principalId
    }
  }
}

resource "azapi_resource" "adf_blob_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.storage_account_id}-blob-contributor")
  parent_id = var.storage_account_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"
      principalId      = azapi_resource.this.output.identity.principalId
    }
  }
}
```

RBAC role IDs:
- Data Factory Contributor: `673868aa-7521-48a0-acc6-0f60742d39f5`

### Private Endpoint

```hcl
resource "azapi_resource" "adf_pe" {
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
            privateLinkServiceId = azapi_resource.this.id
            groupIds             = ["dataFactory"]
          }
        }
      ]
    }
  }

  tags = var.tags
}

resource "azapi_resource" "adf_pe_dns" {
  count     = var.enable_private_endpoint && var.private_dns_zone_id != null ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01"
  name      = "dns-zone-group"
  parent_id = azapi_resource.adf_pe[0].id

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

Private DNS zone: `privatelink.datafactory.azure.net`

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Data Factory')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Tags to apply')
param tags object = {}

resource adf 'Microsoft.DataFactory/factories@2018-06-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publicNetworkAccess: 'Disabled'  // Unless told otherwise, disabled per governance policy
  }
}

output id string = adf.id
output name string = adf.name
output principalId string = adf.identity.principalId
```

### Linked Service (Bicep)

```bicep
resource blobLinkedService 'Microsoft.DataFactory/factories/linkedservices@2018-06-01' = {
  parent: adf
  name: 'ls-blob'
  properties: {
    type: 'AzureBlobStorage'
    typeProperties: {
      serviceEndpoint: 'https://${storageAccountName}.blob.core.windows.net'
    }
    connectVia: {
      referenceName: 'AutoResolveIntegrationRuntime'
      type: 'IntegrationRuntimeReference'
    }
  }
}
```

### RBAC Assignment

```bicep
@description('Storage account ID for data source access')
param storageAccountId string

var blobDataReaderRoleId = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'

resource blobReaderAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccountId, adf.identity.principalId, blobDataReaderRoleId)
  scope: resourceId('Microsoft.Storage/storageAccounts', split(storageAccountId, '/')[8])
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobDataReaderRoleId)
    principalId: adf.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
```

### Private Endpoint

```bicep
@description('Subnet ID for private endpoint')
param subnetId string = ''

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = if (!empty(subnetId)) {
  name: 'pe-${name}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'psc-${name}'
        properties: {
          privateLinkServiceId: adf.id
          groupIds: ['dataFactory']
        }
      }
    ]
  }
}
```

## Application Code

Data Factory is a visual/declarative service -- pipelines are authored in the ADF Studio UI or as JSON/ARM templates. Application code interacts with ADF through SDKs for monitoring and triggering.

### Python — Trigger Pipeline Run

```python
from azure.identity import DefaultAzureCredential
from azure.mgmt.datafactory import DataFactoryManagementClient

credential = DefaultAzureCredential()
client = DataFactoryManagementClient(credential, subscription_id)

# Trigger a pipeline run
run = client.pipelines.create_run(
    resource_group_name="my-rg",
    factory_name="my-adf",
    pipeline_name="pl-copy-data",
    parameters={"inputPath": "raw/2024/01/"},
)

print(f"Pipeline run ID: {run.run_id}")

# Monitor pipeline run
import time

while True:
    status = client.pipeline_runs.get("my-rg", "my-adf", run.run_id)
    print(f"Status: {status.status}")
    if status.status in ["Succeeded", "Failed", "Cancelled"]:
        break
    time.sleep(10)
```

### C# — Trigger Pipeline Run

```csharp
using Azure.Identity;
using Azure.ResourceManager;
using Azure.ResourceManager.DataFactory;

var credential = new DefaultAzureCredential();
var armClient = new ArmClient(credential);

var factory = armClient.GetDataFactoryResource(
    DataFactoryResource.CreateResourceIdentifier(subscriptionId, "my-rg", "my-adf")
);

var pipeline = factory.GetDataFactoryPipeline("pl-copy-data");
var runResponse = await pipeline.Value.CreateRunAsync();
Console.WriteLine($"Pipeline run ID: {runResponse.Value.RunId}");
```

### REST API — Trigger Pipeline

```bash
# Trigger pipeline via REST API
curl -X POST \
  "https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.DataFactory/factories/{factory}/pipelines/{pipeline}/createRun?api-version=2018-06-01" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"inputPath": "raw/2024/01/"}'
```

## Common Pitfalls

1. **Self-hosted IR required for on-premises** -- Auto-resolve integration runtime cannot access on-premises data sources. Install self-hosted IR on a VM with network access to the source.
2. **Managed identity on linked services** -- Always use managed identity instead of connection strings or keys. Grant the ADF managed identity appropriate RBAC roles on each data source.
3. **Copy activity parallelism** -- Default DIU (Data Integration Unit) is 4. Increase for large datasets. Parallel copy degree defaults to auto but can be tuned.
4. **Mapping data flow cold start** -- First data flow execution in a session takes 3-5 minutes for cluster spin-up. Use TTL (time-to-live) settings to keep clusters warm.
5. **Git integration conflicts** -- ADF's Live mode and Git mode can diverge. Always publish from Git branches in production. For POC, Git integration can be added later.
6. **Trigger timezone** -- Schedule triggers use UTC by default. Specify timezone explicitly to avoid off-by-hours execution.
7. **Pipeline JSON is not idempotent in Terraform** -- `activities_json` changes on every plan due to ordering. Use `lifecycle { ignore_changes }` or manage pipelines outside Terraform.

## Production Backlog Items

- [ ] Enable managed VNet for secure data source connectivity
- [ ] Enable private endpoint and disable public network access
- [ ] Configure Git integration with Azure DevOps or GitHub
- [ ] Set up CI/CD deployment pipelines (ARM export → deploy)
- [ ] Configure managed private endpoints for data sources
- [ ] Enable diagnostic logging to Log Analytics
- [ ] Implement pipeline monitoring and alerting
- [ ] Configure self-hosted integration runtime for on-premises sources
- [ ] Set up data flow cluster TTL for performance
- [ ] Review and optimize DIU/parallelism settings for copy activities
