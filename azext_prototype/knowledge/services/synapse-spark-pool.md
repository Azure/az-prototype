---
service_namespace: Microsoft.Synapse/workspaces/bigDataPools
display_name: Synapse Spark Pool
depends_on:
  - Microsoft.Synapse/workspaces
---

# Synapse Spark Pool

> Apache Spark cluster within a Synapse workspace for big data processing, data engineering, machine learning, and interactive data exploration with auto-scaling and auto-pause.

## When to Use
- **Data engineering** -- ETL/ELT pipelines transforming raw data in the data lake (Parquet, Delta Lake, CSV)
- **Machine learning** -- model training with PySpark, MLlib, or integration with Azure ML
- **Interactive exploration** -- Synapse notebooks for ad-hoc data analysis with Spark SQL
- **Streaming analytics** -- Spark Structured Streaming for near-real-time processing
- **Delta Lake** -- ACID transactions on data lake storage with time travel and schema evolution

Choose Spark pools over dedicated SQL pools for unstructured/semi-structured data, ML workloads, or when using PySpark/Scala/R. Choose dedicated SQL pools for structured data warehouse queries with T-SQL.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Node size | Small (4 vCPU, 32 GB) | Sufficient for POC data volumes |
| Min nodes | 3 | Minimum required |
| Max nodes | 5 | Cap for POC cost control |
| Auto-scale | Enabled | Elastic within min/max range |
| Auto-pause | 15 minutes | Pause after 15 min idle to save costs |
| Spark version | 3.4 | Latest stable version |
| Dynamic executor allocation | Enabled | Optimizes resource usage per job |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "spark_pool" {
  type      = "Microsoft.Synapse/workspaces/bigDataPools@2021-06-01"
  name      = var.name
  location  = var.location
  parent_id = azapi_resource.synapse_workspace.id

  body = {
    properties = {
      sparkVersion             = "3.4"
      nodeSize                 = "Small"
      nodeSizeFamily           = "MemoryOptimized"
      nodeCount                = 0  # 0 when auto-scale is enabled
      autoScale = {
        enabled  = true
        minNodeCount = 3
        maxNodeCount = 5
      }
      autoPause = {
        enabled            = true
        delayInMinutes     = 15
      }
      dynamicExecutorAllocation = {
        enabled      = true
        minExecutors = 1
        maxExecutors = 4
      }
      sessionLevelPackagesEnabled = true
      isComputeIsolationEnabled   = false
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}
```

### RBAC Assignment

```hcl
# Synapse Contributor on the workspace for Spark pool management
# Synapse Apache Spark Administrator for job execution
resource "azapi_resource" "spark_admin" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.synapse_workspace.id}-${var.principal_id}-synapse-contributor")
  parent_id = azapi_resource.synapse_workspace.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/6e4bf58a-b8e1-4cc3-bbf9-d73143322b78"
      principalId      = var.principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Spark pool name')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Node size')
@allowed(['Small', 'Medium', 'Large', 'XLarge', 'XXLarge'])
param nodeSize string = 'Small'

@description('Auto-pause delay in minutes')
param autoPauseDelay int = 15

param tags object = {}

resource sparkPool 'Microsoft.Synapse/workspaces/bigDataPools@2021-06-01' = {
  parent: synapseWorkspace
  name: name
  location: location
  tags: tags
  properties: {
    sparkVersion: '3.4'
    nodeSize: nodeSize
    nodeSizeFamily: 'MemoryOptimized'
    nodeCount: 0
    autoScale: {
      enabled: true
      minNodeCount: 3
      maxNodeCount: 5
    }
    autoPause: {
      enabled: true
      delayInMinutes: autoPauseDelay
    }
    dynamicExecutorAllocation: {
      enabled: true
      minExecutors: 1
      maxExecutors: 4
    }
    sessionLevelPackagesEnabled: true
  }
}

output id string = sparkPool.id
output name string = sparkPool.name
```

## Application Code

### Python

```python
# Synapse Spark notebooks use PySpark (runs inside the Spark pool)
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

# Read from ADLS Gen2 (linked storage)
df = spark.read.parquet("abfss://container@storageaccount.dfs.core.windows.net/data/")

# Transform
result = (
    df.filter(df["status"] == "active")
    .groupBy("category")
    .agg({"amount": "sum", "id": "count"})
    .withColumnRenamed("sum(amount)", "total_amount")
    .withColumnRenamed("count(id)", "record_count")
)

# Write as Delta Lake
result.write.format("delta").mode("overwrite").save(
    "abfss://container@storageaccount.dfs.core.windows.net/output/"
)
```

### C#

```csharp
// C# (.NET for Spark) is supported in Synapse notebooks
// Primarily used via Synapse notebooks, not standalone apps
// For external access, use the Synapse REST API:
using Azure.Analytics.Synapse.Spark;
using Azure.Identity;

var client = new SparkSessionClient(
    new Uri($"https://{workspaceName}.dev.azuresynapse.net"),
    sparkPoolName,
    new DefaultAzureCredential());

var sessionOptions = new SparkSessionOptions(name: "my-session")
{
    DriverMemory = "4g",
    ExecutorMemory = "4g",
    ExecutorCount = 2
};

SparkSession session = await client.CreateSparkSessionAsync(sessionOptions);
```

### Node.js

```typescript
// Submit Spark jobs via Synapse REST API
import { DefaultAzureCredential } from "@azure/identity";
import axios from "axios";

const credential = new DefaultAzureCredential();
const token = await credential.getToken(
  "https://dev.azuresynapse.net/.default"
);

const response = await axios.post(
  `https://${workspaceName}.dev.azuresynapse.net/sparkPools/${sparkPoolName}/sessions`,
  {
    name: "my-session",
    driverMemory: "4g",
    executorMemory: "4g",
    executorCount: 2,
  },
  { headers: { Authorization: `Bearer ${token.token}` } }
);
```

## Common Pitfalls

1. **Minimum 3 nodes** -- Spark pools require at least 3 nodes (1 driver + 2 executors). You cannot set min nodes below 3.
2. **Auto-pause has a cold start** -- After auto-pause, the first job takes 2-5 minutes to start while nodes are provisioned. For latency-sensitive workloads, increase the auto-pause delay or disable it.
3. **Session-level packages add startup time** -- Installing packages per session (pip install) adds 3-5 minutes to session startup. Use workspace-level or pool-level packages for commonly used libraries.
4. **Node size is immutable** -- Cannot change the node size after pool creation. Delete and recreate with a different size.
5. **Spark version upgrades** -- Spark versions are immutable per pool. Create a new pool with the new version and migrate notebooks.
6. **ADLS Gen2 permissions** -- The Synapse workspace managed identity (or user identity) needs Storage Blob Data Contributor on the linked storage account. Missing permissions cause "403 Forbidden" errors in notebooks.
7. **Cost accumulation during development** -- Interactive notebook sessions keep the pool running. Close idle sessions and rely on auto-pause.

## Production Backlog Items

- [ ] Right-size node size and max node count based on actual workload requirements
- [ ] Configure pool-level library management instead of session-level installs
- [ ] Set up monitoring for Spark pool utilization and job duration
- [ ] Implement Delta Lake for ACID transactions on the data lake
- [ ] Configure workspace-level Spark configuration for consistent settings
- [ ] Enable diagnostic logging for Spark application events
- [ ] Plan Spark version upgrade strategy across pools
- [ ] Implement cost alerts based on Spark pool node-hours consumption
