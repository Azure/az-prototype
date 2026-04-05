---
service_namespace: Microsoft.ServiceBus/namespaces/topics
display_name: Service Bus Topic
depends_on:
  - Microsoft.ServiceBus/namespaces
---

# Service Bus Topic

> Publish-subscribe messaging within a Service Bus namespace. Multiple subscribers receive copies of each message via subscriptions with optional filters.

## When to Use
- Fan-out messaging where multiple consumers need the same message
- Event distribution to multiple downstream services
- When subscribers need different filtering criteria on the same message stream

## POC Defaults
- **Max size**: 1 GB
- **Message TTL**: 14 days
- **Requires at least one subscription**: Topics without subscriptions discard messages

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "servicebus_topic" {
  type      = "Microsoft.ServiceBus/namespaces/topics@2024-01-01"
  name      = var.topic_name
  parent_id = azapi_resource.servicebus_namespace.id

  body = {
    properties = {
      maxSizeInMegabytes       = 1024
      defaultMessageTimeToLive = "P14D"
      enablePartitioning       = false
    }
  }
}
```

### RBAC Assignment
```hcl
# Topic-level access is granted at the namespace level via
# Microsoft.Authorization/roleAssignments with Service Bus Data roles.
```

## Bicep Patterns

### Basic Resource
```bicep
param topicName string

resource topic 'Microsoft.ServiceBus/namespaces/topics@2024-01-01' = {
  parent: serviceBusNamespace
  name: topicName
  properties: {
    maxSizeInMegabytes: 1024
    defaultMessageTimeToLive: 'P14D'
    enablePartitioning: false
  }
}

output topicId string = topic.id
output topicName string = topic.name
```

## Application Code

### Python
```python
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
client = ServiceBusClient(
    fully_qualified_namespace="<namespace>.servicebus.windows.net",
    credential=credential
)

# Publish to topic
sender = client.get_topic_sender(topic_name=topic_name)
with sender:
    sender.send_messages(ServiceBusMessage("Event occurred"))
```

### C#
```csharp
using Azure.Identity;
using Azure.Messaging.ServiceBus;

var credential = new DefaultAzureCredential();
var client = new ServiceBusClient("<namespace>.servicebus.windows.net", credential);

var sender = client.CreateSender(topicName);
await sender.SendMessageAsync(new ServiceBusMessage("Event occurred"));
```

### Node.js
```typescript
import { ServiceBusClient } from "@azure/service-bus";
import { DefaultAzureCredential } from "@azure/identity";

const credential = new DefaultAzureCredential();
const client = new ServiceBusClient("<namespace>.servicebus.windows.net", credential);

const sender = client.createSender(topicName);
await sender.sendMessages({ body: "Event occurred" });
```

## Common Pitfalls
- **No subscriptions = lost messages**: Messages sent to a topic with no subscriptions are discarded. Create subscriptions before sending.
- **Partitioning cannot be changed**: Once created, partitioning cannot be enabled or disabled.
- **Premium required for large messages**: Messages over 256 KB require Premium tier.

## Production Backlog Items
- Subscription filters (SQL, correlation) for content-based routing
- Auto-forwarding between topics for routing chains
- Duplicate detection for idempotent publishing
- Topic size monitoring and alerting
