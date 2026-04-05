---
service_namespace: Microsoft.EventGrid/systemTopics
display_name: Event Grid System Topic
---

# Event Grid System Topic

> A managed topic that represents events published by Azure services (Storage, Resource Groups, IoT Hub, etc.). System topics are automatically available for supported Azure resources.

## When to Use
- React to Azure service events (blob created, resource modified, IoT device telemetry)
- Trigger Azure Functions, Logic Apps, or webhooks from Azure resource lifecycle events
- Storage events: blob created, blob deleted (common for data processing pipelines)
- Resource group events: resource write success/failure (infrastructure automation)
- Only one system topic per source per region per subscription

## POC Defaults
- **Topic type**: Depends on source (e.g., `Microsoft.Storage.StorageAccounts`, `Microsoft.Resources.ResourceGroups`)
- **Location**: Must match the source resource's location
- **Identity**: System-assigned managed identity (for dead-letter and delivery auth)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "eg_system_topic" {
  type      = "Microsoft.EventGrid/systemTopics@2024-06-01-preview"
  name      = var.system_topic_name
  parent_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}"
  location  = var.location

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {
      source    = azapi_resource.storage_account.id
      topicType = "Microsoft.Storage.StorageAccounts"
    }
  }
}
```

### RBAC Assignment
```hcl
# EventGrid Contributor role allows managing system topics.
# The system topic's managed identity needs roles on delivery targets
# (e.g., Storage Blob Data Contributor for dead-letter container).
```

## Bicep Patterns

### Basic Resource
```bicep
param systemTopicName string
param location string
param sourceResourceId string

resource systemTopic 'Microsoft.EventGrid/systemTopics@2024-06-01-preview' = {
  name: systemTopicName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    source: sourceResourceId
    topicType: 'Microsoft.Storage.StorageAccounts'
  }
}

output systemTopicId string = systemTopic.id
output systemTopicName string = systemTopic.name
```

## Application Code

### Python
```python
# System topics emit events to subscriptions. The subscriber (e.g., Azure Function) handles events:
import azure.functions as func
import json

def main(event: func.EventGridEvent):
    data = event.get_json()
    print(f"Event type: {event.event_type}")
    print(f"Subject: {event.subject}")
    # For storage events: data["url"], data["contentType"], data["contentLength"]
    if event.event_type == "Microsoft.Storage.BlobCreated":
        blob_url = data["url"]
        print(f"New blob: {blob_url}")
```

### C#
```csharp
using Azure.Messaging.EventGrid;
using Microsoft.Azure.Functions.Worker;

[Function("HandleStorageEvent")]
public async Task Run(
    [EventGridTrigger] EventGridEvent eventGridEvent)
{
    _logger.LogInformation($"Event type: {eventGridEvent.EventType}");
    _logger.LogInformation($"Subject: {eventGridEvent.Subject}");

    if (eventGridEvent.EventType == "Microsoft.Storage.BlobCreated")
    {
        var data = eventGridEvent.Data.ToObjectFromJson<StorageBlobCreatedEventData>();
        _logger.LogInformation($"New blob: {data.Url}");
    }
}
```

### Node.js
```typescript
import { EventGridEvent } from "@azure/eventgrid";
import { InvocationContext } from "@azure/functions";

export async function handleStorageEvent(
  event: EventGridEvent, context: InvocationContext
): Promise<void> {
  context.log(`Event type: ${event.eventType}`);
  context.log(`Subject: ${event.subject}`);
  if (event.eventType === "Microsoft.Storage.BlobCreated") {
    const data = event.data as { url: string };
    context.log(`New blob: ${data.url}`);
  }
}
```

## Common Pitfalls
- **One system topic per source**: Each Azure resource can have only one system topic in a given region. Attempting to create a second fails with a conflict error.
- **Topic type must match source**: The `topicType` must exactly match the Azure provider (e.g., `Microsoft.Storage.StorageAccounts`, not `Microsoft.Storage`). Invalid types produce unhelpful errors.
- **Location must match source**: The system topic location must match the source resource's location, or deployment fails.
- **Event subscription separate resource**: The system topic alone doesn't route events. You must create an event subscription (child resource) to deliver events to handlers.
- **Storage event filtering**: Use subject filters (prefix/suffix) on subscriptions to limit events to specific containers or blob paths.

## Production Backlog Items
- Dead-letter destination with managed identity authentication
- Event delivery retry policies and exponential backoff
- Advanced subject filtering for granular event routing
- Event delivery metrics and monitoring
- Multiple event subscriptions for fan-out patterns
