---
service_namespace: Microsoft.StreamAnalytics/streamingJobs
display_name: Azure Stream Analytics Job
---

# Azure Stream Analytics Job

> Real-time analytics service for processing high-velocity streaming data from IoT devices, applications, and infrastructure using a SQL-like query language, with managed compute and exactly-once delivery guarantees.

## When to Use
- **Real-time analytics** -- continuous queries over data streams from IoT Hub, Event Hubs, or Blob Storage
- **IoT telemetry processing** -- aggregation, filtering, and enrichment of device telemetry
- **Real-time dashboards** -- streaming data to Power BI for live operational dashboards
- **Anomaly detection** -- built-in ML functions for spike, dip, and trend change detection
- **Event-driven alerting** -- trigger actions based on streaming data patterns and thresholds
- **Data transformation** -- real-time ETL from streaming sources to data stores

Choose Stream Analytics over Databricks Structured Streaming when you need a no-code/low-code SQL approach without managing Spark clusters. Choose Databricks for complex ML pipelines or when you already have a Spark ecosystem.

**Note:** This file uses the correctly-capitalized ARM namespace `Microsoft.StreamAnalytics/streamingJobs`. The ARM API is case-insensitive, but `streamingJobs` (camelCase) is the canonical form in ARM schemas.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Standard | Only tier available for cloud jobs |
| Streaming units | 1-3 | Minimum for POC; scale based on throughput |
| Compatibility level | 1.2 | Latest; use for new jobs |
| Output error policy | Retry | Retry transient errors; drop only if persistent |
| Late arrival tolerance | 5 seconds | Default; increase for out-of-order data |
| Out-of-order tolerance | 0 seconds | Default; increase for distributed sources |
| Event serialization | JSON | Most common; Avro and CSV also supported |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "stream_analytics_job" {
  type      = "Microsoft.StreamAnalytics/streamingJobs@2021-10-01-preview"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type         = "UserAssigned"
    identity_ids = [var.managed_identity_id]
  }

  body = {
    properties = {
      sku = {
        name = "Standard"
      }
      compatibilityLevel                = "1.2"
      eventsOutOfOrderPolicy            = "Adjust"
      eventsOutOfOrderMaxDelayInSeconds = 0
      eventsLateArrivalMaxDelayInSeconds = 5
      outputErrorPolicy                 = "Stop"
      dataLocale                        = "en-US"
      transformation = {
        name = "main-query"
        properties = {
          streamingUnits = 3
          query          = var.query
        }
      }
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}
```

### RBAC Assignment

```hcl
# Grant Stream Analytics identity access to Event Hub for input
resource "azapi_resource" "eventhub_data_receiver" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.eventhub_namespace_id}-${var.managed_identity_principal_id}-receiver")
  parent_id = var.eventhub_namespace_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/a638d3c7-ab3a-418d-83e6-5f17a39d4fde"
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Grant Stream Analytics identity access to Storage for output
resource "azapi_resource" "storage_blob_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.storage_account_id}-${var.managed_identity_principal_id}-blob-contributor")
  parent_id = var.storage_account_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Stream Analytics job')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('User-assigned managed identity resource ID')
param managedIdentityId string

@description('Stream Analytics SQL query')
param query string

@description('Number of streaming units')
@minValue(1)
@maxValue(396)
param streamingUnits int = 3

param tags object = {}

resource streamJob 'Microsoft.StreamAnalytics/streamingJobs@2021-10-01-preview' = {
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
    sku: {
      name: 'Standard'
    }
    compatibilityLevel: '1.2'
    eventsOutOfOrderPolicy: 'Adjust'
    eventsOutOfOrderMaxDelayInSeconds: 0
    eventsLateArrivalMaxDelayInSeconds: 5
    outputErrorPolicy: 'Stop'
    dataLocale: 'en-US'
    transformation: {
      name: 'main-query'
      properties: {
        streamingUnits: streamingUnits
        query: query
      }
    }
  }
}

output id string = streamJob.id
output name string = streamJob.name
```

## Application Code

### Python

```python
# Stream Analytics jobs are managed via Azure REST API.
# Applications typically produce data TO the job's input, not interact with the job itself.
from azure.eventhub import EventHubProducerClient
from azure.identity import DefaultAzureCredential

# Produce events to the input Event Hub
credential = DefaultAzureCredential()
producer = EventHubProducerClient(
    fully_qualified_namespace="mynamespace.servicebus.windows.net",
    eventhub_name="telemetry",
    credential=credential,
)

from azure.eventhub import EventData
import json

batch = await producer.create_batch()
batch.add(EventData(json.dumps({"device_id": "d1", "temperature": 72.5})))
await producer.send_batch(batch)
```

### C#

```csharp
// Produce events to the input Event Hub
using Azure.Messaging.EventHubs;
using Azure.Messaging.EventHubs.Producer;
using Azure.Identity;
using System.Text.Json;

var producer = new EventHubProducerClient(
    "mynamespace.servicebus.windows.net",
    "telemetry",
    new DefaultAzureCredential());

using var batch = await producer.CreateBatchAsync();
var telemetry = new { device_id = "d1", temperature = 72.5 };
batch.TryAdd(new EventData(JsonSerializer.SerializeToUtf8Bytes(telemetry)));
await producer.SendAsync(batch);
```

### Node.js

```typescript
// Produce events to the input Event Hub
import { EventHubProducerClient } from "@azure/event-hubs";
import { DefaultAzureCredential } from "@azure/identity";

const producer = new EventHubProducerClient(
  "mynamespace.servicebus.windows.net",
  "telemetry",
  new DefaultAzureCredential()
);

const batch = await producer.createBatch();
batch.tryAdd({
  body: { device_id: "d1", temperature: 72.5 },
});
await producer.sendBatch(batch);
```

## Common Pitfalls

1. **Query syntax errors at deploy time** -- The job deploys successfully but produces no output if the query has errors. Test queries in the Azure portal's query testing tool before deploying.
2. **Insufficient streaming units** -- The job falls behind on processing with increasing watermark delay. Monitor SU% utilization and scale up when consistently above 80%.
3. **Missing consumer group** -- Multiple readers on the same consumer group contend for partitions. Create dedicated consumer groups per Stream Analytics job.
4. **Windowing function misuse** -- Understand Tumbling (non-overlapping fixed), Hopping (overlapping fixed), Sliding (event-triggered), and Session (gap-based) window semantics before writing queries.
5. **Job start mode confusion** -- `JobStartTime` starts from now, `CustomTime` from a specific UTC time, `LastOutputEventTime` from where the job left off. Use `LastOutputEventTime` after failures to avoid data loss.
6. **Late arrival data dropped** -- Data outside the late arrival tolerance window is silently discarded. Set tolerance based on actual data source latency characteristics.
7. **Streaming unit multiples** -- Valid SU values are 1, 3, 6, 12, 18, 24, 30, 36, 42, 48 and then multiples of 6 up to 396. Invalid values cause deployment failure.
8. **Identity type matters** -- User-assigned managed identity is recommended for IaC deployments as it survives job recreation. System-assigned identity changes when the job is deleted and recreated.

## Production Backlog Items

- [ ] Right-size streaming units based on actual throughput and SU% utilization
- [ ] Configure monitoring alerts for watermark delay, SU utilization, and runtime errors
- [ ] Add reference data inputs for enriching streaming data with lookup tables
- [ ] Configure additional outputs (SQL, Cosmos DB, Power BI) for different consumers
- [ ] Enable built-in anomaly detection functions for spike/dip detection
- [ ] Optimize query parallelism with `PARTITION BY` for throughput scaling
- [ ] Implement CI/CD pipeline for job deployment and query versioning
- [ ] Plan geo-redundant deployment with paired jobs in secondary region
- [ ] Configure custom deserializer for non-standard input formats
- [ ] Enable diagnostic logging for input/output errors and query execution metrics
