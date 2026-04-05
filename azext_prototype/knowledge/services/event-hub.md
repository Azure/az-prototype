---
service_namespace: Microsoft.EventHub/namespaces/eventhubs
display_name: Event Hub
depends_on:
  - Microsoft.EventHub/namespaces
---

# Event Hub

> A named event stream within an Event Hub namespace. High-throughput, partitioned log for event ingestion and processing.

## When to Use
- High-volume event ingestion (millions of events per second)
- Streaming data pipelines (IoT telemetry, application logs, click streams)
- When multiple consumer groups need independent read positions on the same stream

## POC Defaults
- **Partition count**: 2 (minimum, sufficient for POC)
- **Message retention**: 1 day
- **Capture**: Disabled (not needed for POC)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "event_hub" {
  type      = "Microsoft.EventHub/namespaces/eventhubs@2024-01-01"
  name      = var.event_hub_name
  parent_id = azapi_resource.eventhub_namespace.id

  body = {
    properties = {
      partitionCount    = 2
      messageRetentionInDays = 1
    }
  }
}
```

### RBAC Assignment
```hcl
# Event hub access is granted at the namespace level:
# Azure Event Hubs Data Sender: 2b629674-e913-4c01-ae53-ef4638d8f975
# Azure Event Hubs Data Receiver: a638d3c7-ab3a-418d-83e6-5f17a39d4fde
```

## Bicep Patterns

### Basic Resource
```bicep
param eventHubName string

resource eventHub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: eventHubNamespace
  name: eventHubName
  properties: {
    partitionCount: 2
    messageRetentionInDays: 1
  }
}

output eventHubId string = eventHub.id
output eventHubName string = eventHub.name
```

## Application Code

### Python
```python
from azure.eventhub import EventHubProducerClient, EventData
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
producer = EventHubProducerClient(
    fully_qualified_namespace="<namespace>.servicebus.windows.net",
    eventhub_name=event_hub_name,
    credential=credential
)

batch = await producer.create_batch()
batch.add(EventData("Event data"))
await producer.send_batch(batch)
await producer.close()
```

### C#
```csharp
using Azure.Identity;
using Azure.Messaging.EventHubs;
using Azure.Messaging.EventHubs.Producer;

var credential = new DefaultAzureCredential();
var producer = new EventHubProducerClient(
    "<namespace>.servicebus.windows.net", eventHubName, credential);

using var batch = await producer.CreateBatchAsync();
batch.TryAdd(new EventData("Event data"));
await producer.SendAsync(batch);
```

### Node.js
```typescript
import { EventHubProducerClient } from "@azure/event-hubs";
import { DefaultAzureCredential } from "@azure/identity";

const credential = new DefaultAzureCredential();
const producer = new EventHubProducerClient(
  "<namespace>.servicebus.windows.net", eventHubName, credential
);

const batch = await producer.createBatch();
batch.tryAdd({ body: "Event data" });
await producer.sendBatch(batch);
await producer.close();
```

## Common Pitfalls
- **Partition count is immutable**: Cannot be changed after creation. Plan for growth.
- **Ordering is per-partition**: Events are ordered within a partition, not across partitions. Use partition keys for related events.
- **Consumer groups**: Each consumer group maintains independent read positions. Create separate consumer groups for each downstream processor.

## Production Backlog Items
- Event capture to Azure Storage or Data Lake for archival
- Increased partition count for higher throughput
- Schema registry for event schema evolution
- Geo-disaster recovery configuration
