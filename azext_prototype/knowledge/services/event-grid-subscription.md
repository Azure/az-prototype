---
service_namespace: Microsoft.EventGrid/topics/eventSubscriptions
display_name: Event Grid Subscription
depends_on:
  - Microsoft.EventGrid/topics
---

# Event Grid Subscription

> Routes events from an Event Grid topic (custom or system) to a destination handler such as a webhook, Azure Function, Service Bus queue, Storage queue, or Event Hub.

## When to Use
- Deliver events from custom topics or system topics to subscribers
- Filter events by type, subject prefix/suffix, or advanced filters
- Fan out events to multiple destinations with separate subscriptions
- Configure retry policies and dead-lettering for reliable delivery
- Every event-driven workflow needs at least one subscription

## POC Defaults
- **Destination**: Webhook or Azure Function
- **Event delivery schema**: EventGridSchema (default)
- **Max delivery attempts**: 30
- **Event TTL**: 1440 minutes (24 hours)
- **Subject filter**: None (receive all events)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "eg_subscription" {
  type      = "Microsoft.EventGrid/topics/eventSubscriptions@2024-06-01-preview"
  name      = var.subscription_name
  parent_id = azapi_resource.eg_topic.id

  body = {
    properties = {
      destination = {
        endpointType = "WebHook"
        properties = {
          endpointUrl = var.webhook_url
        }
      }
      filter = {
        includedEventTypes = var.event_types
        subjectBeginsWith  = var.subject_prefix
        subjectEndsWith    = var.subject_suffix
      }
      retryPolicy = {
        maxDeliveryAttempts = 30
        eventTimeToLiveInMinutes = 1440
      }
    }
  }
}

# Azure Function destination
resource "azapi_resource" "eg_subscription_func" {
  type      = "Microsoft.EventGrid/topics/eventSubscriptions@2024-06-01-preview"
  name      = var.subscription_name
  parent_id = azapi_resource.eg_topic.id

  body = {
    properties = {
      destination = {
        endpointType = "AzureFunction"
        properties = {
          resourceId = "${azapi_resource.function_app.id}/functions/${var.function_name}"
        }
      }
      filter = {
        includedEventTypes = var.event_types
      }
    }
  }
}
```

### RBAC Assignment
```hcl
# EventGrid EventSubscription Contributor role allows creating subscriptions.
# The topic owner or Contributor can also manage subscriptions.
```

## Bicep Patterns

### Basic Resource
```bicep
param subscriptionName string
param webhookUrl string
param eventTypes array = []

resource eventSubscription 'Microsoft.EventGrid/topics/eventSubscriptions@2024-06-01-preview' = {
  parent: eventGridTopic
  name: subscriptionName
  properties: {
    destination: {
      endpointType: 'WebHook'
      properties: {
        endpointUrl: webhookUrl
      }
    }
    filter: {
      includedEventTypes: !empty(eventTypes) ? eventTypes : null
    }
    retryPolicy: {
      maxDeliveryAttempts: 30
      eventTimeToLiveInMinutes: 1440
    }
  }
}
```

## Application Code

### Python
```python
# Event Grid delivers events to your handler. For webhook destinations:
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/events", methods=["POST"])
def handle_events():
    events = request.get_json()
    for event in events:
        # Handle validation handshake
        if event.get("eventType") == "Microsoft.EventGrid.SubscriptionValidationEvent":
            validation_code = event["data"]["validationCode"]
            return jsonify({"validationResponse": validation_code})
        # Handle actual events
        print(f"Event: {event['eventType']}, Subject: {event['subject']}")
    return "", 200
```

### C#
```csharp
using Azure.Messaging.EventGrid;
using Microsoft.AspNetCore.Mvc;

[ApiController]
[Route("events")]
public class EventGridController : ControllerBase
{
    [HttpPost]
    public IActionResult HandleEvents([FromBody] EventGridEvent[] events)
    {
        foreach (var ev in events)
        {
            if (ev.EventType == "Microsoft.EventGrid.SubscriptionValidationEvent")
            {
                var data = ev.Data.ToObjectFromJson<SubscriptionValidationEventData>();
                return Ok(new { validationResponse = data.ValidationCode });
            }
            _logger.LogInformation($"Event: {ev.EventType}, Subject: {ev.Subject}");
        }
        return Ok();
    }
}
```

### Node.js
```typescript
import express from "express";

const app = express();
app.use(express.json());

app.post("/events", (req, res) => {
  const events = req.body;
  for (const event of events) {
    if (event.eventType === "Microsoft.EventGrid.SubscriptionValidationEvent") {
      return res.json({ validationResponse: event.data.validationCode });
    }
    console.log(`Event: ${event.eventType}, Subject: ${event.subject}`);
  }
  res.sendStatus(200);
});
```

## Common Pitfalls
- **Webhook validation required**: When creating a webhook subscription, Event Grid sends a validation event. The endpoint must respond with the validation code or creation fails.
- **HTTPS required for webhooks**: Webhook endpoints must use HTTPS. HTTP endpoints are rejected.
- **Filter is inclusive**: `includedEventTypes` is an allowlist. An empty array means all event types. Omitting it also means all types.
- **Dead-letter requires storage**: Dead-letter destinations need a blob storage container. Without dead-lettering, failed events are dropped after max retry attempts.
- **System topic subscriptions**: For system topics, the parent resource type is `Microsoft.EventGrid/systemTopics/eventSubscriptions`, not `topics/eventSubscriptions`.

## Production Backlog Items
- Dead-letter destination for failed event delivery
- Advanced filters for fine-grained event routing
- Managed identity authentication for delivery endpoints
- CloudEvents v1.0 schema for interoperability
- Event delivery metrics monitoring and alerting
