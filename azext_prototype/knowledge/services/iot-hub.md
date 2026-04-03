# Azure IoT Hub
> Managed service for bi-directional communication between IoT applications and devices, with device management, security, and message routing at scale.

## When to Use

- **Device telemetry ingestion** -- collecting data from thousands to millions of IoT devices
- **Device management** -- provisioning, monitoring, and updating device firmware and configuration
- **Cloud-to-device messaging** -- sending commands, configuration updates, or notifications to devices
- **Edge computing** -- deploying workloads to IoT Edge devices with Azure IoT Edge integration
- **Digital twins** -- integrating with Azure Digital Twins for spatial intelligence scenarios

Choose IoT Hub over Event Hubs when you need device identity management, per-device authentication, cloud-to-device messaging, or device twins. Choose Event Hubs for simple high-throughput telemetry ingestion without device management.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | S1 (Standard) | Free tier (F1) limited to 8K messages/day; S1 for realistic POC |
| Units | 1 | Each S1 unit = 400K messages/day |
| Partitions | 4 | Default; sufficient for POC throughput |
| Message retention | 1 day | Minimum; increase for replay scenarios |
| Device authentication | Symmetric key | SAS tokens for POC; X.509 certificates for production |
| Cloud-to-device | Enabled | Built-in with Standard tier |
| File upload | Optional | Requires linked storage account |
| Public network access | Disabled (unless user overrides) | Flag private endpoint as production backlog item |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "iot_hub" {
  type      = "Microsoft.Devices/IotHubs@2023-06-30"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type         = "UserAssigned"
    identity_ids = [var.managed_identity_id]
  }

  body = {
    sku = {
      name     = "S1"
      capacity = 1
    }
    properties = {
      publicNetworkAccess = "Disabled"  # Unless told otherwise, disabled per governance policy
      minTlsVersion       = "1.2"
      disableLocalAuth    = false  # Devices use SAS tokens; disable for X.509-only
      eventHubEndpoints = {
        events = {
          retentionTimeInDays = 1
          partitionCount      = 4
        }
      }
      routing = {
        fallbackRoute = {
          name      = "fallback"
          source    = "DeviceMessages"
          condition = "true"
          endpointNames = ["events"]
          isEnabled = true
        }
      }
    }
  }

  tags = var.tags

  response_export_values = ["properties.hostName", "properties.eventHubEndpoints.events"]
}
```

### Consumer Group

```hcl
resource "azapi_resource" "consumer_group" {
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

### Message Route to Storage

```hcl
resource "azapi_resource" "storage_endpoint" {
  type      = "Microsoft.Devices/IotHubs@2023-06-30"
  name      = azapi_resource.iot_hub.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      routing = {
        endpoints = {
          storageContainers = [
            {
              name                    = "storage-endpoint"
              connectionString        = ""  # Use identity-based when possible
              containerName           = var.container_name
              fileNameFormat          = "{iothub}/{partition}/{YYYY}/{MM}/{DD}/{HH}/{mm}"
              batchFrequencyInSeconds = 300
              maxChunkSizeInBytes     = 314572800
              encoding                = "JSON"
              authenticationType      = "identityBased"
              endpointUri             = "https://${var.storage_account_name}.blob.core.windows.net"
              identity = {
                userAssignedIdentity = var.managed_identity_id
              }
            }
          ]
        }
        routes = [
          {
            name      = "telemetry-to-storage"
            source    = "DeviceMessages"
            condition = "true"
            endpointNames = ["storage-endpoint"]
            isEnabled = true
          }
        ]
        fallbackRoute = {
          name      = "fallback"
          source    = "DeviceMessages"
          condition = "true"
          endpointNames = ["events"]
          isEnabled = true
        }
      }
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# IoT Hub Data Contributor -- read/write device data, invoke direct methods
resource "azapi_resource" "iothub_data_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.iot_hub.id}${var.managed_identity_principal_id}iothub-data-contributor")
  parent_id = azapi_resource.iot_hub.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/4fc6c259-987e-4a07-842e-c321cc9d413f"  # IoT Hub Data Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Grant IoT Hub's identity access to storage for message routing
resource "azapi_resource" "storage_blob_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.storage_account_id}${var.managed_identity_principal_id}storage-blob-contributor")
  parent_id = var.storage_account_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"  # Storage Blob Data Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

RBAC role IDs:
- IoT Hub Data Contributor: `4fc6c259-987e-4a07-842e-c321cc9d413f`
- IoT Hub Data Reader: `b447c946-2db7-41ec-983d-d8bf3b1c77e3`
- IoT Hub Registry Contributor: `4ea46cd5-c1b2-4a8e-910b-273211f9ce47`
- IoT Hub Twin Contributor: `494bdba2-168f-4f31-a0a1-191d2f7c028c`

### Private Endpoint

```hcl
resource "azapi_resource" "iot_private_endpoint" {
  count     = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints@2023-11-01"
  name      = "pe-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      subnet = {
        id = var.subnet_id
      }
      privateLinkServiceConnections = [
        {
          name = "psc-${var.name}"
          properties = {
            privateLinkServiceId = azapi_resource.iot_hub.id
            groupIds             = ["iotHub"]
          }
        }
      ]
    }
  }

  tags = var.tags
}

resource "azapi_resource" "iot_pe_dns_zone_group" {
  count     = var.enable_private_endpoint && var.subnet_id != null && var.private_dns_zone_id != null ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01"
  name      = "dns-zone-group"
  parent_id = azapi_resource.iot_private_endpoint[0].id

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "config"
          properties = {
            privateDnsZoneId = var.private_dns_zone_id
          }
        }
      ]
    }
  }
}
```

Private DNS zone: `privatelink.azure-devices.net`

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the IoT Hub')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Managed identity resource ID')
param managedIdentityId string

@description('Tags to apply')
param tags object = {}

resource iotHub 'Microsoft.Devices/IotHubs@2023-06-30' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  sku: {
    name: 'S1'
    capacity: 1
  }
  properties: {
    publicNetworkAccess: 'Disabled'
    minTlsVersion: '1.2'
    eventHubEndpoints: {
      events: {
        retentionTimeInDays: 1
        partitionCount: 4
      }
    }
    routing: {
      fallbackRoute: {
        name: 'fallback'
        source: 'DeviceMessages'
        condition: 'true'
        endpointNames: [
          'events'
        ]
        isEnabled: true
      }
    }
  }
}

output id string = iotHub.id
output name string = iotHub.name
output hostName string = iotHub.properties.hostName
output eventHubEndpoint string = iotHub.properties.eventHubEndpoints.events.endpoint
```

### RBAC Assignment

```bicep
@description('Principal ID for IoT Hub data access')
param principalId string

var iotHubDataContributorRoleId = '4fc6c259-987e-4a07-842e-c321cc9d413f'

resource iotDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(iotHub.id, principalId, iotHubDataContributorRoleId)
  scope: iotHub
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', iotHubDataContributorRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Free tier (F1) message limits | Only 8K messages/day; quickly exhausted | Use S1 for realistic POC workloads |
| Message size limit (256 KB) | Large payloads rejected | Use file upload for large data; keep telemetry messages small |
| Partition count is immutable | Cannot change after creation | Plan partition count based on expected throughput |
| Device twin size limit (8 KB) | Cannot store large device state | Use desired/reported properties sparingly; offload to external store |
| Missing consumer group | Multiple readers interfere with each other | Create dedicated consumer groups per downstream service |
| SAS token expiration | Devices disconnect and cannot reconnect | Implement token refresh logic; use X.509 for production |
| Throttling on device operations | Bulk device provisioning fails | Use Device Provisioning Service for at-scale onboarding |
| Built-in endpoint retention | Messages lost after retention period | Route messages to storage for long-term retention |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Private endpoint | P1 | Add private endpoint and disable public network access |
| X.509 certificate auth | P1 | Migrate from SAS tokens to X.509 certificates for device authentication |
| Device Provisioning Service | P2 | Enable zero-touch device provisioning at scale |
| Message routing | P2 | Configure routes to Storage, Event Hubs, or Service Bus for downstream processing |
| IoT Edge | P2 | Deploy edge modules for local processing and offline capability |
| Device Update | P3 | Configure Azure Device Update for OTA firmware updates |
| Monitoring and alerts | P2 | Set up alerts for connected devices, message throughput, and throttling |
| Diagnostic logging | P3 | Enable diagnostic logs and route to Log Analytics |
| IP filtering | P2 | Configure IP filter rules to restrict device connections by source IP |
| Disaster recovery | P3 | Configure manual failover to paired region for business continuity |
