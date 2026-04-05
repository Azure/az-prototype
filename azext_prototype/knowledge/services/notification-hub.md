---
service_namespace: Microsoft.NotificationHubs/namespaces/notificationHubs
display_name: Notification Hub
depends_on:
  - Microsoft.NotificationHubs/namespaces
---

# Notification Hub

> A push notification hub within a Notification Hubs namespace that enables sending push notifications to iOS (APNs), Android (FCM), Windows (WNS), and other platforms at scale.

## When to Use
- Send push notifications to mobile apps across multiple platforms (iOS, Android, Windows)
- Broadcast notifications to millions of devices with a single API call
- Tag-based routing to send targeted notifications to user segments
- Template notifications for platform-independent message formatting
- NOT suitable for: SMS/email notifications (use Communication Services), real-time messaging (use SignalR)

## POC Defaults
- **SKU**: Free (500 active devices, 1M pushes/month — inherited from namespace)
- **APNs**: Token-based auth (simpler than certificate-based for POC)
- **FCM**: FCM v1 API key
- **Registration TTL**: 90 days

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "notification_hub" {
  type      = "Microsoft.NotificationHubs/namespaces/notificationHubs@2023-10-01-preview"
  name      = var.hub_name
  parent_id = azapi_resource.nh_namespace.id
  location  = var.location

  body = {
    properties = {
      name = var.hub_name
    }
  }
}
```

### RBAC Assignment
```hcl
# Notification Hubs use Shared Access Policies (SAS), not Azure RBAC.
# DefaultFullSharedAccessSignature is created automatically.
# For least-privilege, create custom policies:
#   - Listen: mobile clients for registration
#   - Send: backend services for pushing
#   - Manage: admin operations
```

## Bicep Patterns

### Basic Resource
```bicep
param hubName string
param location string

resource hub 'Microsoft.NotificationHubs/namespaces/notificationHubs@2023-10-01-preview' = {
  parent: nhNamespace
  name: hubName
  location: location
  properties: {
    name: hubName
  }
}

output hubName string = hub.name
```

## Application Code

### Python
```python
from azure.notificationhubs import NotificationHubClient

hub_client = NotificationHubClient(connection_string, hub_name)

# Send a template notification (platform-independent)
hub_client.send_notification(
    notification={"message": "Hello from Azure!"},
    tags="user:12345"
)

# Send platform-specific (FCM)
hub_client.send_gcm_native_notification(
    '{"data": {"message": "Hello Android!"}}',
    tags="platform:android"
)
```

### C#
```csharp
using Microsoft.Azure.NotificationHubs;

var hub = NotificationHubClient.CreateClientFromConnectionString(connectionString, hubName);

// Template notification (cross-platform)
await hub.SendTemplateNotificationAsync(
    new Dictionary<string, string> { { "message", "Hello from Azure!" } },
    "user:12345");

// FCM native notification
await hub.SendFcmV1NativeNotificationAsync(
    """{"message":{"notification":{"title":"Hello","body":"World"}}}""",
    "platform:android");
```

### Node.js
```typescript
import { NotificationHubsClient } from "@azure/notification-hubs";

const client = new NotificationHubsClient(connectionString, hubName);

// Send a template notification
await client.sendNotification({
  body: JSON.stringify({ message: "Hello from Azure!" }),
  headers: { "ServiceBusNotification-Tags": "user:12345" },
});
```

## Common Pitfalls
- **Platform credentials required**: The hub itself is just a container. You must configure APNs/FCM/WNS credentials before any notifications can be sent. Missing credentials fail silently.
- **Free tier limits**: Free SKU supports 500 active devices and 1M pushes/month. Exceeding these limits drops notifications without error.
- **SAS, not RBAC**: Notification Hubs use Shared Access Signature authentication, not Azure RBAC. Connection strings contain the SAS key.
- **FCM v1 migration**: Google deprecated the legacy FCM API. Use FCM v1 API credentials (service account JSON), not the legacy server key.
- **Registration staleness**: Device registrations expire. Clients must re-register on app startup to keep registrations fresh.

## Production Backlog Items
- Platform credential configuration (APNs token, FCM v1 service account, WNS client secret)
- Tag-based audience segmentation strategy
- Template registration for cross-platform notifications
- Push notification analytics and delivery tracking
- Upgrade to Standard SKU for higher device limits and telemetry
