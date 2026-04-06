---
service_namespace: Microsoft.ServiceBus/namespaces/topics/subscriptions
display_name: Service Bus Topic Subscription
depends_on:
  - Microsoft.ServiceBus/namespaces/topics
---

# Service Bus Topic Subscription

> A named subscription on a Service Bus topic that receives copies of published messages. Supports SQL and correlation filters for content-based routing.

## When to Use
- Each consumer of a topic needs its own subscription
- Use filters to route only relevant messages to each subscriber
- Dead-letter subscriptions handle processing failures

## POC Defaults
- **Max delivery count**: 10
- **Lock duration**: 30 seconds
- **Dead-lettering on expiration**: Enabled
- **Filter**: None (receives all messages by default)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "topic_subscription" {
  type      = "Microsoft.ServiceBus/namespaces/topics/subscriptions@2024-01-01"
  name      = var.subscription_name
  parent_id = azapi_resource.servicebus_topic.id

  body = {
    properties = {
      lockDuration                  = "PT30S"
      maxDeliveryCount              = 10
      deadLetteringOnMessageExpiration = true
      defaultMessageTimeToLive      = "P14D"
    }
  }
}
```

### RBAC Assignment
```hcl
# Subscription access is granted at the namespace level via
# Service Bus Data Receiver role.
```

## Bicep Patterns

### Basic Resource
```bicep
param subscriptionName string

resource subscription 'Microsoft.ServiceBus/namespaces/topics/subscriptions@2024-01-01' = {
  parent: serviceBusTopic
  name: subscriptionName
  properties: {
    lockDuration: 'PT30S'
    maxDeliveryCount: 10
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'P14D'
  }
}

output subscriptionId string = subscription.id
output subscriptionName string = subscription.name
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

# Receive from subscription
receiver = client.get_subscription_receiver(
    topic_name=topic_name,
    subscription_name=subscription_name
)
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

var receiver = client.CreateReceiver(topicName, subscriptionName);
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

const receiver = client.createReceiver(topicName, subscriptionName);
const messages = await receiver.receiveMessages(10, { maxWaitTimeInMs: 5000 });
for (const msg of messages) {
  console.log(msg.body);
  await receiver.completeMessage(msg);
}
```

## Common Pitfalls
- **Default filter receives everything**: Without a filter, the subscription receives all messages on the topic. Add SQL or correlation filters for selective routing.
- **Lock duration vs processing time**: If processing exceeds lock duration, the message becomes visible to other receivers. Increase lock duration or use `renewMessageLock`.
- **Dead letter queue monitoring**: Dead-lettered messages accumulate silently. Set up monitoring.

## Production Backlog Items
- SQL filter rules for content-based routing
- Correlation filters for high-performance header-based routing
- Auto-forwarding to another queue or topic
- Session-enabled subscriptions for ordered processing
