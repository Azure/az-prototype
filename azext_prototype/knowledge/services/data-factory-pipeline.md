---
service_namespace: Microsoft.DataFactory/factories/pipelines
display_name: Data Factory Pipeline
depends_on:
  - Microsoft.DataFactory/factories
---

# Data Factory Pipeline

> A logical grouping of activities within a Data Factory that together perform a data movement or transformation task. Pipelines orchestrate Copy, DataFlow, and custom activities.

## When to Use
- Orchestrate data movement between data stores (ETL/ELT pipelines)
- Chain multiple activities with dependency logic (success, failure, completion)
- Schedule recurring data integration jobs
- Parameterize data workflows for reuse across environments
- Combine Copy activities, Data Flows, and stored procedure calls

## POC Defaults
- **Concurrency**: 1 (single concurrent run)
- **Annotations**: Empty
- **Activities**: Copy activity with source and sink datasets

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "adf_pipeline" {
  type      = "Microsoft.DataFactory/factories/pipelines@2018-06-01"
  name      = var.pipeline_name
  parent_id = azapi_resource.data_factory.id

  body = {
    properties = {
      description = var.description
      concurrency = 1
      parameters = {
        sourcePath = { type = "String", defaultValue = "input" }
        sinkPath   = { type = "String", defaultValue = "output" }
      }
      activities = [
        {
          name = "CopyBlobToBlob"
          type = "Copy"
          inputs = [
            { referenceName = "SourceDataset", type = "DatasetReference" }
          ]
          outputs = [
            { referenceName = "SinkDataset", type = "DatasetReference" }
          ]
          typeProperties = {
            source = { type = "BlobSource" }
            sink   = { type = "BlobSink" }
          }
        }
      ]
    }
  }
}
```

### RBAC Assignment
```hcl
# Data Factory Contributor role allows pipeline management.
# Pipeline execution uses the Data Factory managed identity's permissions.
```

## Bicep Patterns

### Basic Resource
```bicep
param pipelineName string

resource pipeline 'Microsoft.DataFactory/factories/pipelines@2018-06-01' = {
  parent: dataFactory
  name: pipelineName
  properties: {
    description: 'Copy data from source to sink'
    concurrency: 1
    parameters: {
      sourcePath: { type: 'String', defaultValue: 'input' }
      sinkPath: { type: 'String', defaultValue: 'output' }
    }
    activities: [
      {
        name: 'CopyBlobToBlob'
        type: 'Copy'
        inputs: [
          { referenceName: 'SourceDataset', type: 'DatasetReference' }
        ]
        outputs: [
          { referenceName: 'SinkDataset', type: 'DatasetReference' }
        ]
        typeProperties: {
          source: { type: 'BlobSource' }
          sink: { type: 'BlobSink' }
        }
      }
    ]
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

# Trigger a pipeline run
run = client.pipelines.create_run(
    rg_name, factory_name, pipeline_name,
    parameters={"sourcePath": "data/2025", "sinkPath": "archive/2025"}
)
print(f"Pipeline run ID: {run.run_id}")

# Monitor the run
import time
while True:
    status = client.pipeline_runs.get(rg_name, factory_name, run.run_id)
    print(f"Status: {status.status}")
    if status.status in ("Succeeded", "Failed", "Cancelled"):
        break
    time.sleep(10)
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
var pipeline = await factory.GetDataFactoryPipelineAsync(pipelineName);

// Trigger pipeline run via REST API or SDK
```

### Node.js
```typescript
import { DefaultAzureCredential } from "@azure/identity";
import { DataFactoryManagementClient } from "@azure/arm-datafactory";

const credential = new DefaultAzureCredential();
const client = new DataFactoryManagementClient(credential, subscriptionId);

const run = await client.pipelines.createRun(rgName, factoryName, pipelineName, {
  parameters: { sourcePath: "data/2025", sinkPath: "archive/2025" },
});
console.log(`Pipeline run ID: ${run.runId}`);
```

## Common Pitfalls
- **Datasets must exist**: Pipelines reference datasets by name. If the referenced datasets don't exist, pipeline deployment succeeds but execution fails.
- **Activity dependencies**: Without explicit dependencies, activities run in parallel. Use `dependsOn` with conditions (Succeeded, Failed, Completed, Skipped) for ordering.
- **API version is 2018-06-01**: This is the current stable API for Data Factory pipeline resources. Don't use newer API versions.
- **JSON complexity**: Pipeline definitions can be very large. For complex pipelines, consider managing them via the ADF UI and exporting ARM templates.
- **Trigger vs manual run**: Deploying a pipeline doesn't start it. You need a trigger resource or manual `createRun` API call to execute.
- **Concurrency limit**: The `concurrency` property limits simultaneous runs. Set to 1 to prevent overlapping runs of the same pipeline.

## Production Backlog Items
- Trigger configuration (schedule, tumbling window, event-based)
- Error handling with retry policies and failure activities
- Parameterized pipelines for environment promotion (dev → staging → prod)
- Monitoring and alerting for pipeline failures
- Data lineage tracking with Azure Purview integration
