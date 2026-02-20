# Azure Service Bus
> Enterprise messaging service providing reliable message queuing and publish-subscribe patterns with support for transactions, sessions, and dead-lettering.

## When to Use

- Decoupling microservices with reliable asynchronous messaging
- Command and event patterns (CQRS, event sourcing)
- Ordered message processing with sessions
- Load leveling -- buffering bursty traffic for downstream consumers
- Publish-subscribe scenarios with topic/subscription filtering
- Transactional messaging where exactly-once processing matters
- NOT suitable for: high-throughput event streaming (use Event Hubs), simple fire-and-forget notifications (use Event Grid), or IoT telemetry ingestion (use IoT Hub)

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Standard | **Basic does not support topics/subscriptions** -- use Standard minimum |
| Capacity | 0 (Standard is shared) | Premium uses Messaging Units |
| Queues | As needed | One queue per command/job type |
| Topics | As needed | One topic per event type |
| Max message size | 256 KB (Standard) | 100 MB on Premium |
| Managed identity | User-assigned | RBAC for data plane access |

**CRITICAL**: The Basic tier does NOT support topics or subscriptions. Always use Standard or Premium for POCs that require pub/sub patterns.

## Terraform Patterns

### Basic Resource

```hcl
resource "azurerm_servicebus_namespace" "this" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "Standard"  # "Basic" lacks topics; "Premium" for private endpoints
  minimum_tls_version = "1.2"

  local_auth_enabled  = false  # Disable SAS keys; use RBAC only

  tags = var.tags
}

resource "azurerm_servicebus_queue" "this" {
  for_each = var.queues

  name         = each.key
  namespace_id = azurerm_servicebus_namespace.this.id

  max_delivery_count          = each.value.max_delivery_count != null ? each.value.max_delivery_count : 10
  dead_lettering_on_message_expiration = true
  enable_partitioning         = false  # true for high throughput
  default_message_ttl         = each.value.ttl != null ? each.value.ttl : "P14D"  # ISO 8601
}

resource "azurerm_servicebus_topic" "this" {
  for_each = var.topics

  name         = each.key
  namespace_id = azurerm_servicebus_namespace.this.id

  enable_partitioning = false
  default_message_ttl = each.value.ttl != null ? each.value.ttl : "P14D"
}

resource "azurerm_servicebus_subscription" "this" {
  for_each = var.subscriptions

  name               = each.value.name
  topic_id           = azurerm_servicebus_topic.this[each.value.topic].id
  max_delivery_count = each.value.max_delivery_count != null ? each.value.max_delivery_count : 10

  dead_lettering_on_message_expiration = true
}
```

### RBAC Assignment

```hcl
# Grant managed identity the ability to send messages
resource "azurerm_role_assignment" "data_sender" {
  scope                = azurerm_servicebus_namespace.this.id
  role_definition_id   = "/providers/Microsoft.Authorization/roleDefinitions/69a216fc-b8fb-44d8-bc22-1f3c2cd27a39"  # Azure Service Bus Data Sender
  principal_id         = var.sender_principal_id
}

# Grant managed identity the ability to receive messages
resource "azurerm_role_assignment" "data_receiver" {
  scope                = azurerm_servicebus_namespace.this.id
  role_definition_id   = "/providers/Microsoft.Authorization/roleDefinitions/4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0"  # Azure Service Bus Data Receiver
  principal_id         = var.receiver_principal_id
}

# Grant full data owner (send + receive + manage) -- use sparingly
resource "azurerm_role_assignment" "data_owner" {
  scope                = azurerm_servicebus_namespace.this.id
  role_definition_id   = "/providers/Microsoft.Authorization/roleDefinitions/090c5cfd-751d-490a-894a-3ce6f1109419"  # Azure Service Bus Data Owner
  principal_id         = var.admin_principal_id
}
```

### Private Endpoint

```hcl
resource "azurerm_private_endpoint" "this" {
  count = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0

  name                = "pe-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id

  private_service_connection {
    name                           = "psc-${var.name}"
    private_connection_resource_id = azurerm_servicebus_namespace.this.id
    subresource_names              = ["namespace"]
    is_manual_connection           = false
  }

  dynamic "private_dns_zone_group" {
    for_each = var.private_dns_zone_id != null ? [1] : []
    content {
      name                 = "dns-zone-group"
      private_dns_zone_ids = [var.private_dns_zone_id]
    }
  }

  tags = var.tags
}
```

## Bicep Patterns

### Basic Resource

```bicep
param name string
param location string
param sku string = 'Standard'
param queueNames array = []
param topicNames array = []
param tags object = {}

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: name
  location: location
  sku: {
    name: sku
  }
  properties: {
    minimumTlsVersion: '1.2'
    disableLocalAuth: true  // RBAC only
  }
  tags: tags
}

resource queues 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = [for queueName in queueNames: {
  parent: serviceBusNamespace
  name: queueName
  properties: {
    maxDeliveryCount: 10
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'P14D'
  }
}]

resource topics 'Microsoft.ServiceBus/namespaces/topics@2024-01-01' = [for topicName in topicNames: {
  parent: serviceBusNamespace
  name: topicName
  properties: {
    defaultMessageTimeToLive: 'P14D'
  }
}]

output id string = serviceBusNamespace.id
output name string = serviceBusNamespace.name
output endpoint string = '${serviceBusNamespace.name}.servicebus.windows.net'
```

### RBAC Assignment

```bicep
param principalId string
param namespaceName string

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2024-01-01' existing = {
  name: namespaceName
}

// Azure Service Bus Data Sender
resource senderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, principalId, '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39')
  scope: serviceBusNamespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39')
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

// Azure Service Bus Data Receiver
resource receiverRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, principalId, '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0')
  scope: serviceBusNamespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0')
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Application Code

### Python

```python
import os
from azure.identity import ManagedIdentityCredential, DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage

def get_credential():
    client_id = os.getenv("AZURE_CLIENT_ID")
    if client_id:
        return ManagedIdentityCredential(client_id=client_id)
    return DefaultAzureCredential()

namespace = os.getenv("SERVICEBUS_NAMESPACE")  # e.g., "sb-myproject-dev.servicebus.windows.net"
credential = get_credential()

# Send a message to a queue
def send_message(queue_name: str, body: str):
    with ServiceBusClient(namespace, credential) as client:
        with client.get_queue_sender(queue_name) as sender:
            sender.send_messages(ServiceBusMessage(body))

# Receive messages from a queue
def receive_messages(queue_name: str, max_messages: int = 10):
    with ServiceBusClient(namespace, credential) as client:
        with client.get_queue_receiver(queue_name) as receiver:
            messages = receiver.receive_messages(max_message_count=max_messages, max_wait_time=5)
            for msg in messages:
                print(f"Received: {str(msg)}")
                receiver.complete_message(msg)

# Send to a topic
def send_to_topic(topic_name: str, body: str):
    with ServiceBusClient(namespace, credential) as client:
        with client.get_topic_sender(topic_name) as sender:
            sender.send_messages(ServiceBusMessage(body))

# Receive from a subscription
def receive_from_subscription(topic_name: str, subscription_name: str):
    with ServiceBusClient(namespace, credential) as client:
        with client.get_subscription_receiver(topic_name, subscription_name) as receiver:
            messages = receiver.receive_messages(max_message_count=10, max_wait_time=5)
            for msg in messages:
                print(f"Received: {str(msg)}")
                receiver.complete_message(msg)
```

### C#

```csharp
using Azure.Identity;
using Azure.Messaging.ServiceBus;

var clientId = Environment.GetEnvironmentVariable("AZURE_CLIENT_ID");
var credential = string.IsNullOrEmpty(clientId)
    ? new DefaultAzureCredential()
    : new ManagedIdentityCredential(clientId);

var ns = Environment.GetEnvironmentVariable("SERVICEBUS_NAMESPACE");
// e.g., "sb-myproject-dev.servicebus.windows.net"

await using var client = new ServiceBusClient(ns, credential);

// Send a message
async Task SendMessageAsync(string queueName, string body)
{
    var sender = client.CreateSender(queueName);
    await sender.SendMessageAsync(new ServiceBusMessage(body));
}

// Receive messages (processor pattern for production)
async Task StartProcessingAsync(string queueName)
{
    var processor = client.CreateProcessor(queueName, new ServiceBusProcessorOptions
    {
        MaxConcurrentCalls = 5,
        AutoCompleteMessages = false
    });

    processor.ProcessMessageAsync += async args =>
    {
        Console.WriteLine($"Received: {args.Message.Body}");
        await args.CompleteMessageAsync(args.Message);
    };

    processor.ProcessErrorAsync += args =>
    {
        Console.Error.WriteLine($"Error: {args.Exception}");
        return Task.CompletedTask;
    };

    await processor.StartProcessingAsync();
}
```

### Node.js

```javascript
const { ServiceBusClient } = require("@azure/service-bus");
const { DefaultAzureCredential, ManagedIdentityCredential } = require("@azure/identity");

function getCredential() {
  const clientId = process.env.AZURE_CLIENT_ID;
  return clientId
    ? new ManagedIdentityCredential(clientId)
    : new DefaultAzureCredential();
}

const ns = process.env.SERVICEBUS_NAMESPACE;
// e.g., "sb-myproject-dev.servicebus.windows.net"
const client = new ServiceBusClient(ns, getCredential());

// Send a message
async function sendMessage(queueName, body) {
  const sender = client.createSender(queueName);
  await sender.sendMessages({ body });
  await sender.close();
}

// Receive messages (subscribe pattern)
async function receiveMessages(queueName) {
  const receiver = client.createReceiver(queueName);
  const messages = await receiver.receiveMessages(10, { maxWaitTimeInMs: 5000 });
  for (const msg of messages) {
    console.log("Received:", msg.body);
    await receiver.completeMessage(msg);
  }
  await receiver.close();
}

// Processor pattern (long-running)
async function startProcessor(queueName) {
  const processor = client.createProcessor(queueName, {
    maxConcurrentCalls: 5,
    autoCompleteMessages: false,
  });

  processor.subscribe({
    processMessage: async (args) => {
      console.log("Received:", args.message.body);
      await args.completeMessage(args.message);
    },
    processError: async (args) => {
      console.error("Error:", args.error);
    },
  });

  await processor.start();
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Using Basic tier with topics | Deployment fails -- Basic does not support topics | Always use Standard or Premium |
| Using connection strings instead of RBAC | Secrets in config, no per-identity access control | Set `disableLocalAuth = true`, use managed identity + RBAC roles |
| Not handling dead-letter queue | Poisoned messages accumulate silently | Monitor DLQ; implement DLQ processor or alerting |
| Forgetting `max_delivery_count` | Messages retried indefinitely on transient failures | Set reasonable `max_delivery_count` (default 10) |
| Not completing/abandoning messages | Messages become invisible until lock expires, then re-appear | Always call `complete_message()` on success or `abandon_message()` on failure |
| Standard tier with private endpoints | Private endpoints require Premium tier | Use Standard for POC (public); upgrade to Premium for production private endpoints |
| Ignoring message ordering | Out-of-order processing in parallel consumers | Use sessions for ordered processing when required |
| Large messages on Standard tier | 256 KB limit exceeded | Claim-check pattern: store payload in Blob Storage, send reference in message |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Premium tier upgrade | P1 | Upgrade to Premium for private endpoints, larger messages, and dedicated capacity |
| Dead-letter queue handling | P2 | Implement DLQ processor or alerting for failed messages |
| Duplicate detection | P3 | Enable duplicate detection window on queues/topics for at-most-once delivery |
| Geo-disaster recovery | P2 | Configure geo-DR pairing for namespace failover |
| Message sessions | P3 | Implement sessions for ordered processing if message ordering is required |
| Diagnostic logging | P3 | Enable diagnostic settings and route to Log Analytics workspace |
| Auto-forwarding | P3 | Configure auto-forwarding rules for message routing between queues/topics |
| Namespace firewall | P1 | Restrict network access to known VNets and IP ranges |
| Monitoring and alerting | P3 | Configure alerts on DLQ depth, active messages count, and throttled requests |
| Message encryption | P2 | Implement application-level message encryption for sensitive payloads |
