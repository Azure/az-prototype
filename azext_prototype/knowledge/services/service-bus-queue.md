---
service_namespace: Microsoft.ServiceBus/namespaces/queues
display_name: Service Bus Queue
depends_on:
  - Microsoft.ServiceBus/namespaces
---

# Service Bus Queue

> Point-to-point messaging queue within a Service Bus namespace. Guarantees FIFO ordering, at-least-once delivery, and dead-letter handling.

## When to Use
- Asynchronous decoupling between producer and single consumer
- Work distribution across competing consumers (load leveling)
- When ordering guarantees are needed (sessions)
- When dead-letter handling is required for poison messages

## POC Defaults
- **Max size**: 1 GB
- **Message TTL**: 14 days (default)
- **Lock duration**: 30 seconds
- **Dead-lettering**: Enabled on expiration

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "servicebus_queue" {
  type      = "Microsoft.ServiceBus/namespaces/queues@2024-01-01"
  name      = var.queue_name
  parent_id = azapi_resource.servicebus_namespace.id

  body = {
    properties = {
      maxSizeInMegabytes            = 1024
      defaultMessageTimeToLive      = "P14D"
      lockDuration                  = "PT30S"
      deadLetteringOnMessageExpiration = true
      maxDeliveryCount              = 10
    }
  }
}
```

### RBAC Assignment
```hcl
# Queue-level access is granted at the namespace level via
# Microsoft.Authorization/roleAssignments with Service Bus Data roles.
# See the service-bus knowledge file for role assignment patterns.
```

## Bicep Patterns

### Basic Resource
```bicep
param queueName string

resource queue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: serviceBusNamespace
  name: queueName
  properties: {
    maxSizeInMegabytes: 1024
    defaultMessageTimeToLive: 'P14D'
    lockDuration: 'PT30S'
    deadLetteringOnMessageExpiration: true
    maxDeliveryCount: 10
  }
}

output queueId string = queue.id
output queueName string = queue.name
```

## Application Code

### Python
```python
from azure.servicebus import ServiceBusClient
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
client = ServiceBusClient(
    fully_qualified_namespace="<namespace>.servicebus.windows.net",
    credential=credential
)

# Send message
sender = client.get_queue_sender(queue_name=queue_name)
with sender:
    sender.send_messages(ServiceBusMessage("Hello"))

# Receive messages
receiver = client.get_queue_receiver(queue_name=queue_name)
with receiver:
    messages = receiver.receive_messages(max_message_count=10, max_wait_time=5)
    for msg in messages:
        print(str(msg))
        receiver.complete_message(msg)
```

### C#
```csharp
using Azure.Identity;
using Azure.Messaging.ServiceBus;

var credential = new DefaultAzureCredential();
var client = new ServiceBusClient("<namespace>.servicebus.windows.net", credential);

// Send
var sender = client.CreateSender(queueName);
await sender.SendMessageAsync(new ServiceBusMessage("Hello"));

// Receive
var receiver = client.CreateReceiver(queueName);
var messages = await receiver.ReceiveMessagesAsync(maxMessages: 10);
foreach (var msg in messages)
{
    Console.WriteLine(msg.Body.ToString());
    await receiver.CompleteMessageAsync(msg);
}
```

### Node.js
```typescript
import { ServiceBusClient } from "@azure/service-bus";
import { DefaultAzureCredential } from "@azure/identity";

const credential = new DefaultAzureCredential();
const client = new ServiceBusClient("<namespace>.servicebus.windows.net", credential);

// Send
const sender = client.createSender(queueName);
await sender.sendMessages({ body: "Hello" });

// Receive
const receiver = client.createReceiver(queueName);
const messages = await receiver.receiveMessages(10, { maxWaitTimeInMs: 5000 });
for (const msg of messages) {
  console.log(msg.body);
  await receiver.completeMessage(msg);
}
```

## Common Pitfalls
- **Lock duration too short**: If message processing takes longer than the lock duration, the message becomes visible to other consumers. Increase lock duration or renew the lock.
- **Dead letter queue overflow**: Dead-lettered messages count against the queue size. Monitor and process the DLQ.
- **Max delivery count**: After `maxDeliveryCount` failed attempts, messages are dead-lettered. Set appropriately for your retry strategy.
- **ISO 8601 duration format**: Properties like TTL use ISO 8601 durations (e.g., `P14D` = 14 days, `PT30S` = 30 seconds).

## Production Backlog Items
- Session-enabled queues for ordered processing per partition
- Auto-forwarding to chain queues for routing patterns
- Duplicate detection window configuration
- Queue size monitoring and alerting
- Dead-letter queue processing automation
