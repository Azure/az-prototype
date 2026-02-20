# Azure Event Grid
> Fully managed event routing service for building event-driven architectures with publish-subscribe semantics.

## When to Use

- **Event-driven architectures** -- decouple producers and consumers with reliable event delivery
- **Azure resource events** -- react to Azure resource lifecycle events (blob created, resource group changed, etc.)
- **Custom application events** -- publish domain events from your application for downstream processing
- **Serverless triggers** -- trigger Azure Functions, Logic Apps, or webhooks in response to events
- **Fan-out** -- deliver a single event to multiple subscribers simultaneously
- **Event filtering** -- route events to specific handlers based on event type, subject, or data content

Prefer Event Grid over Service Bus when you need **event notification** (something happened) rather than **command messaging** (do something). Event Grid excels at fire-and-forget broadcasting; Service Bus excels at reliable, ordered, transactional messaging.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Topic type | Custom Topic | For application-generated events |
| Topic type (alternative) | System Topic | For Azure resource events (auto-created) |
| Schema | CloudEvents v1.0 | Recommended for new implementations |
| Public network access | Enabled (POC) | Flag private endpoint as production backlog item |

## Terraform Patterns

### Basic Resource

```hcl
# Custom Topic
resource "azurerm_eventgrid_topic" "this" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name

  input_schema = "CloudEventSchemaV1_0"  # Recommended schema

  identity {
    type = "SystemAssigned"
  }

  public_network_access_enabled = true  # Set false when using private endpoint

  tags = var.tags
}

# Event Subscription (e.g., to Azure Function)
resource "azurerm_eventgrid_event_subscription" "function" {
  name  = "sub-${var.name}-function"
  scope = azurerm_eventgrid_topic.this.id

  azure_function_endpoint {
    function_id = var.function_id  # Resource ID of the Azure Function
  }

  # Optional: filter events
  advanced_filter {
    string_contains {
      key    = "subject"
      values = ["orders/"]
    }
  }

  retry_policy {
    max_delivery_attempts = 30
    event_time_to_live    = 1440  # 24 hours in minutes
  }
}

# Event Subscription (to webhook)
resource "azurerm_eventgrid_event_subscription" "webhook" {
  name  = "sub-${var.name}-webhook"
  scope = azurerm_eventgrid_topic.this.id

  webhook_endpoint {
    url = var.webhook_url
  }
}

# System Topic (for Azure resource events)
resource "azurerm_eventgrid_system_topic" "storage" {
  name                   = "systopic-${var.name}-storage"
  location               = var.location
  resource_group_name    = var.resource_group_name
  source_arm_resource_id = var.storage_account_id
  topic_type             = "Microsoft.Storage.StorageAccounts"

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# EventGrid Data Sender -- allows publishing events to topic
resource "azurerm_role_assignment" "event_sender" {
  scope                = azurerm_eventgrid_topic.this.id
  role_definition_name = "EventGrid Data Sender"
  principal_id         = var.managed_identity_principal_id
}
```

RBAC role IDs:
- EventGrid Data Sender: `d5a91429-5739-47e2-a06b-3470a27159e7`

### Private Endpoint

```hcl
resource "azurerm_private_endpoint" "eventgrid" {
  count = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0

  name                = "pe-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id

  private_service_connection {
    name                           = "psc-${var.name}"
    private_connection_resource_id = azurerm_eventgrid_topic.this.id
    subresource_names              = ["topic"]
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

Private DNS zone: `privatelink.eventgrid.azure.net`

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Event Grid topic')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Tags to apply')
param tags object = {}

resource topic 'Microsoft.EventGrid/topics@2024-06-01-preview' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    inputSchema: 'CloudEventSchemaV1_0'
    publicNetworkAccess: 'Enabled'  // Set 'Disabled' when using private endpoint
  }
}

output id string = topic.id
output name string = topic.name
output endpoint string = topic.properties.endpoint
output principalId string = topic.identity.principalId
```

### RBAC Assignment

```bicep
@description('Principal ID of the managed identity for event publishing')
param publisherPrincipalId string

var eventGridDataSenderRoleId = 'd5a91429-5739-47e2-a06b-3470a27159e7'

resource senderRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(topic.id, publisherPrincipalId, eventGridDataSenderRoleId)
  scope: topic
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', eventGridDataSenderRoleId)
    principalId: publisherPrincipalId
    principalType: 'ServicePrincipal'
  }
}
```

## Application Code

### Python

```python
from azure.eventgrid import EventGridPublisherClient
from azure.core.messaging import CloudEvent
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential(managed_identity_client_id="<client-id>")
client = EventGridPublisherClient(
    endpoint="https://mytopic.eastus-1.eventgrid.azure.net/api/events",
    credential=credential,
)

# Publish a CloudEvent
event = CloudEvent(
    type="MyApp.Orders.OrderCreated",
    source="/myapp/orders",
    data={"order_id": "12345", "customer": "contoso"},
)
client.send(event)

# Publish multiple events
events = [
    CloudEvent(type="MyApp.Orders.OrderCreated", source="/myapp/orders", data={"order_id": "12345"}),
    CloudEvent(type="MyApp.Orders.OrderCreated", source="/myapp/orders", data={"order_id": "12346"}),
]
client.send(events)
```

### C# / .NET

```csharp
using Azure.Identity;
using Azure.Messaging.EventGrid;
using Azure.Messaging;

var credential = new DefaultAzureCredential(new DefaultAzureCredentialOptions
{
    ManagedIdentityClientId = "<client-id>"
});

var client = new EventGridPublisherClient(
    new Uri("https://mytopic.eastus-1.eventgrid.azure.net/api/events"),
    credential
);

// Publish a CloudEvent
var cloudEvent = new CloudEvent(
    source: "/myapp/orders",
    type: "MyApp.Orders.OrderCreated",
    jsonSerializableData: new { OrderId = "12345", Customer = "contoso" }
);

await client.SendEventAsync(cloudEvent);
```

### Node.js

```typescript
import { EventGridPublisherClient } from "@azure/eventgrid";
import { DefaultAzureCredential } from "@azure/identity";

const credential = new DefaultAzureCredential({
  managedIdentityClientId: "<client-id>",
});

const client = new EventGridPublisherClient(
  "https://mytopic.eastus-1.eventgrid.azure.net/api/events",
  "CloudEvent",
  credential
);

// Publish a CloudEvent
await client.send([
  {
    type: "MyApp.Orders.OrderCreated",
    source: "/myapp/orders",
    data: { orderId: "12345", customer: "contoso" },
  },
]);
```

## Common Pitfalls

1. **Schema mismatch** -- Events published with a different schema than the topic expects will be rejected. If the topic uses `CloudEventSchemaV1_0`, all publishers must send CloudEvents.
2. **Webhook validation** -- Webhook endpoints must respond to Event Grid's validation handshake (subscription validation event). Without it, the subscription creation fails.
3. **System topic vs custom topic** -- System topics are auto-created for Azure resource events and cannot be manually created. Custom topics are for application-generated events.
4. **Dead-letter not configured** -- Without dead-letter configuration, events that fail delivery are silently dropped after retry exhaustion. Always configure a dead-letter destination (Storage Blob) for production.
5. **Event ordering** -- Event Grid does not guarantee ordering. If ordering matters, include a sequence number in the event data and handle ordering in the subscriber.
6. **Event size limits** -- Individual events must be under 1 MB. Batch requests must be under 1 MB total. For larger payloads, send a reference (blob URL) instead of the full data.
7. **Retry behavior** -- Event Grid retries failed deliveries with exponential backoff. Default retry: 30 attempts over 24 hours. Configure retry policy based on your latency requirements.

## Production Backlog Items

- [ ] Configure dead-letter destination (Azure Blob Storage) for undeliverable events
- [ ] Implement retry policies tuned to subscriber SLA requirements
- [ ] Enable advanced filtering to reduce unnecessary event delivery
- [ ] Configure private endpoints and disable public network access
- [ ] Set up monitoring alerts for delivery failures and dead-lettered events
- [ ] Implement event schema validation in subscribers
- [ ] Configure event subscriptions with expiration times for temporary integrations
- [ ] Review and implement event batching for high-throughput scenarios
- [ ] Set up Azure Monitor diagnostic settings for topic-level metrics
