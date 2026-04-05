---
service_namespace: Microsoft.DataFactory/factories/linkedservices
display_name: Data Factory Linked Service
depends_on:
  - Microsoft.DataFactory/factories
---

# Data Factory Linked Service

> A connection definition within a Data Factory that specifies how to connect to an external data store or compute resource (Azure SQL, Blob Storage, REST APIs, etc.).

## When to Use
- Connect Data Factory to source and destination data stores for data movement
- Authenticate to Azure services using managed identity (preferred) or connection strings
- Define compute contexts for data transformation (HDInsight, Databricks, Azure Batch)
- Every Data Factory pipeline that reads or writes data needs at least one linked service
- Reusable connection definitions shared across multiple pipelines and datasets

## POC Defaults
- **Authentication**: Managed identity (for Azure services that support it)
- **Connect via integration runtime**: AutoResolveIntegrationRuntime (default)
- **Encryption**: In-transit encryption enabled

## Terraform Patterns

### Basic Resource
```hcl
# Blob Storage linked service with managed identity
resource "azapi_resource" "adf_ls_blob" {
  type      = "Microsoft.DataFactory/factories/linkedservices@2018-06-01"
  name      = var.linked_service_name
  parent_id = azapi_resource.data_factory.id

  body = {
    properties = {
      type = "AzureBlobStorage"
      typeProperties = {
        serviceEndpoint = "https://${var.storage_account_name}.blob.core.windows.net"
      }
      connectVia = {
        referenceName = "AutoResolveIntegrationRuntime"
        type          = "IntegrationRuntimeReference"
      }
    }
  }
}

# Azure SQL linked service with managed identity
resource "azapi_resource" "adf_ls_sql" {
  type      = "Microsoft.DataFactory/factories/linkedservices@2018-06-01"
  name      = "ls-azure-sql"
  parent_id = azapi_resource.data_factory.id

  body = {
    properties = {
      type = "AzureSqlDatabase"
      typeProperties = {
        connectionString = "Server=tcp:${var.sql_server}.database.windows.net,1433;Database=${var.database};Authentication=Active Directory Managed Identity"
      }
    }
  }
}
```

### RBAC Assignment
```hcl
# The Data Factory managed identity needs access to the target resource.
# For Blob Storage: Storage Blob Data Contributor
# For Azure SQL: db_datareader/db_datawriter roles via SQL
resource "azapi_resource" "adf_blob_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = var.role_assignment_name
  parent_id = azapi_resource.storage_account.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"
      principalId      = azapi_resource.data_factory.identity[0].principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource
```bicep
param linkedServiceName string
param storageAccountName string

resource linkedService 'Microsoft.DataFactory/factories/linkedservices@2018-06-01' = {
  parent: dataFactory
  name: linkedServiceName
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

## Application Code

### Python
```python
from azure.identity import DefaultAzureCredential
from azure.mgmt.datafactory import DataFactoryManagementClient

credential = DefaultAzureCredential()
client = DataFactoryManagementClient(credential, subscription_id)

# Create a linked service programmatically
from azure.mgmt.datafactory.models import LinkedServiceResource, AzureBlobStorageLinkedService

ls = client.linked_services.create_or_update(
    rg_name, factory_name, "ls-blob",
    LinkedServiceResource(
        properties=AzureBlobStorageLinkedService(
            service_endpoint=f"https://{storage_account}.blob.core.windows.net"
        )
    )
)
print(f"Linked service: {ls.name}")
```

### C#
```csharp
using Azure.Identity;
using Azure.ResourceManager;
using Azure.ResourceManager.DataFactory;

var credential = new DefaultAzureCredential();
var client = new ArmClient(credential);

var factory = client.GetDataFactoryResource(
    DataFactoryResource.CreateResourceIdentifier(subscriptionId, rgName, factoryName));
var linkedServices = factory.GetDataFactoryLinkedServices();

// Linked services are typically managed via IaC, not runtime code
```

### Node.js
```typescript
import { DefaultAzureCredential } from "@azure/identity";
import { DataFactoryManagementClient } from "@azure/arm-datafactory";

const credential = new DefaultAzureCredential();
const client = new DataFactoryManagementClient(credential, subscriptionId);

await client.linkedServices.createOrUpdate(rgName, factoryName, "ls-blob", {
  properties: {
    type: "AzureBlobStorage",
    typeProperties: {
      serviceEndpoint: `https://${storageAccount}.blob.core.windows.net`,
    },
  },
});
```

## Common Pitfalls
- **Managed identity permissions**: The Data Factory's managed identity must have the correct role on the target resource. Missing permissions cause runtime failures, not deployment failures.
- **API version is 2018-06-01**: Despite being old, this is the current stable API version for Data Factory child resources. Newer versions may not be available.
- **Type-specific properties**: Each linked service type has different `typeProperties`. Using wrong properties for the type causes validation errors.
- **Key Vault for secrets**: Connection strings with passwords should reference Azure Key Vault secrets, not inline values. Use `AzureKeyVaultSecretReference` in `typeProperties`.
- **Integration runtime**: For on-premises or VNet-connected sources, you need a self-hosted integration runtime, not the default AutoResolve.

## Production Backlog Items
- Key Vault integration for secret management in linked services
- Self-hosted integration runtime for on-premises data sources
- Parameterized linked services for environment-specific connections
- Managed VNet integration runtime for network-isolated access
- Linked service testing and connectivity validation automation
