# Azure Notification Hubs
> Scalable push notification engine for sending personalized notifications to mobile and web applications across all major platforms (iOS, Android, Windows, Web Push).

## When to Use

- **Mobile push notifications** -- send notifications to iOS (APNs), Android (FCM), Windows (WNS) devices
- **Web push** -- browser-based push notifications via Web Push protocol
- **Broadcast messaging** -- send to millions of devices simultaneously with low latency
- **Personalized notifications** -- tag-based routing for user segmentation and targeting
- **Cross-platform** -- single API to reach all platforms without managing platform-specific integrations

Choose Notification Hubs over direct platform integration (APNs/FCM) when you need cross-platform abstraction, tag-based routing, or scale beyond individual platform limits. Choose direct platform SDKs for simple single-platform apps with minimal notification needs.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Free | 1M pushes, 500 active devices; sufficient for POC |
| Namespace SKU | Free | Namespace contains one or more hubs |
| Platforms | Configure as needed | APNs (iOS), FCM (Android), WNS (Windows) |
| Authentication | Managed identity for backend | SAS tokens for direct device registration |
| Tags | Enabled | Use tags for user/group targeting |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "notification_namespace" {
  type      = "Microsoft.NotificationHubs/namespaces@2023-10-01-preview"
  name      = var.namespace_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Free"  # Free for POC; Basic or Standard for production
    }
    properties = {
      namespaceType = "NotificationHub"
    }
  }

  tags = var.tags
}

resource "azapi_resource" "notification_hub" {
  type      = "Microsoft.NotificationHubs/namespaces/notificationHubs@2023-10-01-preview"
  name      = var.hub_name
  location  = var.location
  parent_id = azapi_resource.notification_namespace.id

  body = {
    properties = {}
  }

  tags = var.tags
}
```

### Platform Configuration (FCM v1)

```hcl
resource "azapi_update_resource" "fcm_credential" {
  type        = "Microsoft.NotificationHubs/namespaces/notificationHubs@2023-10-01-preview"
  resource_id = azapi_resource.notification_hub.id

  body = {
    properties = {
      gcmCredential = {
        properties = {
          googleApiKey = var.fcm_server_key  # Store in Key Vault
          gcmEndpoint  = "https://fcm.googleapis.com/fcm/send"
        }
      }
    }
  }
}
```

### Platform Configuration (APNs)

```hcl
resource "azapi_update_resource" "apns_credential" {
  type        = "Microsoft.NotificationHubs/namespaces/notificationHubs@2023-10-01-preview"
  resource_id = azapi_resource.notification_hub.id

  body = {
    properties = {
      apnsCredential = {
        properties = {
          apnsCertificate = var.apns_certificate  # Base64 .p12 certificate
          certificateKey  = var.apns_certificate_key
          endpoint        = "https://api.sandbox.push.apple.com:443/3/device"  # Use api.push.apple.com for production
        }
      }
    }
  }
}
```

### RBAC Assignment

```hcl
# Notification Hubs does not have dedicated data-plane RBAC roles.
# Use Contributor or custom roles for management-plane access.
# SAS tokens (DefaultFullSharedAccessSignature, DefaultListenSharedAccessSignature)
# are used for data-plane operations (sending, registering).

resource "azapi_resource" "nh_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.notification_namespace.id}${var.managed_identity_principal_id}contributor")
  parent_id = azapi_resource.notification_namespace.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/b24988ac-6180-42a0-ab88-20f7382dd24c"  # Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

Notification Hubs does not support private endpoints. All communication is over HTTPS. Secure access using SAS tokens with appropriate permissions (Listen for devices, Send for backend).

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Notification Hubs namespace')
param namespaceName string

@description('Name of the notification hub')
param hubName string

@description('Azure region')
param location string = resourceGroup().location

@description('Tags to apply')
param tags object = {}

resource notificationNamespace 'Microsoft.NotificationHubs/namespaces@2023-10-01-preview' = {
  name: namespaceName
  location: location
  tags: tags
  sku: {
    name: 'Free'
  }
  properties: {
    namespaceType: 'NotificationHub'
  }
}

resource notificationHub 'Microsoft.NotificationHubs/namespaces/notificationHubs@2023-10-01-preview' = {
  parent: notificationNamespace
  name: hubName
  location: location
  tags: tags
  properties: {}
}

output namespaceId string = notificationNamespace.id
output hubId string = notificationHub.id
output hubName string = notificationHub.name
```

### RBAC Assignment

Notification Hubs uses SAS-based authentication for data-plane operations. Use ARM RBAC (Contributor) for management-plane access only.

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Free tier limits | 500 active devices, 1M pushes; quickly exhausted | Monitor usage; upgrade to Basic for production testing |
| APNs sandbox vs production endpoint | Notifications fail in production or development | Use sandbox endpoint for dev, production endpoint for release builds |
| FCM legacy API deprecation | Google deprecated legacy FCM HTTP API | Use FCM v1 API (HTTP v1) with service account JSON credentials |
| Missing platform credentials | Push silently fails for that platform | Configure all target platform credentials before testing |
| Tag expression complexity | Invalid tag expressions cause send failures | Test tag expressions with small audiences first; max 20 tags per expression |
| Registration expiration | Stale registrations waste quota | Implement registration refresh on app launch; use installation API |
| Large payload size | Platform-specific size limits cause truncation | APNs: 4 KB, FCM: 4 KB, WNS: 5 KB -- keep payloads small |
| SAS token management | Leaked tokens allow unauthorized sends | Rotate SAS keys regularly; use Listen-only tokens on devices |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Upgrade to Basic/Standard SKU | P1 | Remove device limits and enable telemetry |
| FCM v1 migration | P1 | Migrate from legacy FCM to HTTP v1 API with service account |
| Installation API | P2 | Migrate from registrations to installations API for better device management |
| Telemetry and analytics | P2 | Enable per-message telemetry for delivery tracking (Standard tier) |
| Scheduled sends | P3 | Configure scheduled notifications for time-zone-aware delivery |
| Template registrations | P2 | Use templates for cross-platform notification formatting |
| Tag management | P2 | Implement user segmentation strategy with tags |
| Certificate rotation | P1 | Automate APNs certificate rotation before expiry |
| Monitoring and alerts | P2 | Set up alerts for push failures, throttling, and quota usage |
| Multi-hub architecture | P3 | Separate hubs per environment (dev/staging/prod) for isolation |
