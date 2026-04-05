---
service_namespace: Microsoft.Devices/IotHubs/eventHubEndpoints/ConsumerGroups
display_name: IoT Hub Consumer Group
depends_on:
  - Microsoft.Devices/IotHubs
---

# IoT Hub Consumer Group

> A consumer group on the IoT Hub's built-in Event Hub-compatible endpoint, enabling multiple downstream readers to independently process device-to-cloud messages.

## When to Use
- Each application or processing pipeline reading from IoT Hub needs its own consumer group
- The default `$Default` consumer group should not be shared across applications
- Separate consumer groups for real-time analytics, storage archival, and alerting pipelines
- Required when multiple Azure Stream Analytics jobs or Azure Functions read from the same IoT Hub

## POC Defaults
- **Endpoint name**: `events` (the built-in Event Hub-compatible endpoint)
- **Consumer group name**: Application-specific (e.g., `analytics`, `storage-writer`)
- **Default**: `$Default` exists automatically — create additional groups as needed

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "iot_consumer_group" {
  type      = "Microsoft.Devices/IotHubs/eventHubEndpoints/ConsumerGroups@2023-06-30"
  name      = var.consumer_group_name
  parent_id = "${azapi_resource.iot_hub.id}/eventHubEndpoints/events"

  body = {
    properties = {
      name = var.consumer_group_name
    }
  }
}
```

### RBAC Assignment
```hcl
# Consumer group access inherits from the IoT Hub RBAC.
# IoT Hub Data Reader role grants read access to the events endpoint.
```

## Bicep Patterns

### Basic Resource
```bicep
param consumerGroupName string

resource consumerGroup 'Microsoft.Devices/IotHubs/eventHubEndpoints/ConsumerGroups@2023-06-30' = {
  // Note: parent chain is IotHub > eventHubEndpoints > ConsumerGroups
  name: '${iotHub.name}/events/${consumerGroupName}'
  properties: {}
}
```

## Application Code

### Python
```python
from azure.eventhub import EventHubConsumerClient
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
# IoT Hub's built-in endpoint is Event Hub-compatible
consumer = EventHubConsumerClient(
    fully_qualified_namespace="<iothub-compatible-endpoint>.servicebus.windows.net",
    eventhub_name="<iothub-compatible-name>",
    consumer_group=consumer_group_name,
    credential=credential
)

async def on_event(partition_context, event):
    device_id = event.system_properties[b"iothub-connection-device-id"].decode()
    print(f"Device: {device_id}, Data: {event.body_as_str()}")
    await partition_context.update_checkpoint(event)

async with consumer:
    await consumer.receive(on_event=on_event)
```

### C#
```csharp
using Azure.Identity;
using Azure.Messaging.EventHubs.Consumer;

var credential = new DefaultAzureCredential();
// Use the IoT Hub's Event Hub-compatible endpoint
var consumer = new EventHubConsumerClient(
    consumerGroupName,
    "<iothub-compatible-endpoint>.servicebus.windows.net",
    "<iothub-compatible-name>",
    credential);

await foreach (var partitionEvent in consumer.ReadEventsAsync())
{
    var deviceId = partitionEvent.Data.SystemProperties["iothub-connection-device-id"];
    Console.WriteLine($"Device: {deviceId}, Data: {partitionEvent.Data.EventBody}");
}
```

### Node.js
```typescript
import { EventHubConsumerClient } from "@azure/event-hubs";
import { DefaultAzureCredential } from "@azure/identity";

const credential = new DefaultAzureCredential();
const consumer = new EventHubConsumerClient(
  consumerGroupName,
  "<iothub-compatible-endpoint>.servicebus.windows.net",
  "<iothub-compatible-name>",
  credential
);

const subscription = consumer.subscribe({
  processEvents: async (events) => {
    for (const event of events) {
      const deviceId = event.systemProperties["iothub-connection-device-id"];
      console.log(`Device: ${deviceId}, Data: ${event.body}`);
    }
  },
  processError: async (err) => console.error(err),
});
```

## Common Pitfalls
- **Parent path includes 'events'**: The parent resource ID must include `/eventHubEndpoints/events`. Omitting this segment causes a 404 error.
- **$Default is shared**: The default consumer group is shared by all readers. Create dedicated consumer groups to avoid checkpoint conflicts.
- **Max consumer groups varies by tier**: Free/Basic: 2, S1: 10, S2/S3: up to 20. Exceeding the limit fails with a quota error.
- **Event Hub SDK, not IoT SDK**: Reading from the built-in endpoint uses the Event Hubs SDK, not the IoT Hub SDK. The IoT Hub SDK is for device management.
- **Checkpoint storage needed**: Like Event Hubs, reliable processing requires checkpoint storage in Azure Blob Storage.

## Production Backlog Items
- Checkpoint storage configuration for reliable offset management
- Consumer group per downstream service (analytics, archival, alerting)
- Consumer lag monitoring and alerting
- Message enrichment rules on the IoT Hub for simplified downstream processing
