---
service_namespace: Microsoft.EventHub/namespaces/eventhubs/consumergroups
display_name: Event Hub Consumer Group
depends_on:
  - Microsoft.EventHub/namespaces/eventhubs
---

# Event Hub Consumer Group

> A named view of an event hub's event stream. Each consumer group maintains independent read positions, enabling multiple downstream processors.

## When to Use
- Each application or processing pipeline needs its own consumer group
- The default `$Default` consumer group should not be shared across applications
- Create separate consumer groups for development, testing, and production readers

## POC Defaults
- **Name**: Application-specific (e.g., `worker-processor`, `analytics-reader`)
- **Default**: `$Default` exists automatically — create additional groups as needed

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "consumer_group" {
  type      = "Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01"
  name      = var.consumer_group_name
  parent_id = azapi_resource.event_hub.id

  body = {
    properties = {
      userMetadata = var.description
    }
  }
}
```

### RBAC Assignment
```hcl
# Consumer group access is inherited from the event hub/namespace RBAC.
# Event Hubs Data Receiver role grants read access across all consumer groups.
```

## Bicep Patterns

### Basic Resource
```bicep
param consumerGroupName string

resource consumerGroup 'Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01' = {
  parent: eventHub
  name: consumerGroupName
  properties: {
    userMetadata: 'Worker processing pipeline'
  }
}

output consumerGroupId string = consumerGroup.id
output consumerGroupName string = consumerGroup.name
```

## Application Code

### Python
```python
from azure.eventhub import EventHubConsumerClient
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
consumer = EventHubConsumerClient(
    fully_qualified_namespace="<namespace>.servicebus.windows.net",
    eventhub_name=event_hub_name,
    consumer_group=consumer_group_name,
    credential=credential
)

async def on_event(partition_context, event):
    print(event.body_as_str())
    await partition_context.update_checkpoint(event)

async with consumer:
    await consumer.receive(on_event=on_event)
```

### C#
```csharp
using Azure.Identity;
using Azure.Messaging.EventHubs.Consumer;

var credential = new DefaultAzureCredential();
var consumer = new EventHubConsumerClient(
    consumerGroupName, "<namespace>.servicebus.windows.net",
    eventHubName, credential);

await foreach (var partitionEvent in consumer.ReadEventsAsync())
{
    Console.WriteLine(partitionEvent.Data.EventBody.ToString());
}
```

### Node.js
```typescript
import { EventHubConsumerClient } from "@azure/event-hubs";
import { DefaultAzureCredential } from "@azure/identity";

const credential = new DefaultAzureCredential();
const consumer = new EventHubConsumerClient(
  consumerGroupName, "<namespace>.servicebus.windows.net",
  eventHubName, credential
);

const subscription = consumer.subscribe({
  processEvents: async (events) => {
    for (const event of events) console.log(event.body);
  },
  processError: async (err) => console.error(err),
});
```

## Common Pitfalls
- **$Default is shared**: The default consumer group is shared by all readers that don't specify one. Create dedicated consumer groups.
- **Max 20 consumer groups**: Standard tier supports 20 consumer groups per event hub. Premium supports unlimited.
- **Checkpoint storage**: Consumer groups need external checkpoint storage (Azure Blob Storage) for reliable offset tracking.

## Production Backlog Items
- Checkpoint storage configuration for reliable offset management
- Consumer group monitoring for lag detection
- Separate consumer groups per environment (dev, staging, prod)
