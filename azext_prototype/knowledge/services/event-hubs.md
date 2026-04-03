# Azure Event Hubs
> Fully managed real-time data ingestion service capable of receiving and processing millions of events per second with low latency and high throughput.

## When to Use

- **Event streaming** -- high-throughput ingestion of telemetry, logs, and clickstream data
- **Event-driven architectures** -- decouple producers from consumers with partitioned event streams
- **IoT data ingestion** -- collect device telemetry at massive scale
- **Log aggregation** -- centralize application and infrastructure logs for downstream processing
- **Kafka replacement** -- Event Hubs exposes a Kafka-compatible endpoint (no code changes needed)
- **Stream processing** -- feed into Azure Stream Analytics, Azure Functions, or custom consumers

Prefer Event Hubs over Service Bus when you need high-throughput streaming with partitioned consumers. Use Service Bus for transactional message queuing with ordering guarantees and dead-lettering on individual messages.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Basic | 1 consumer group, 100 brokered connections, 1 day retention |
| SKU (with Kafka) | Standard | Kafka endpoint, 20 consumer groups, 7 day retention |
| Throughput units | 1 | Auto-inflate disabled for POC cost control |
| Partition count | 2 | Minimum; sufficient for POC throughput |
| Message retention | 1 day (Basic) / 7 days (Standard) | Increase for replay scenarios |
| Authentication | AAD (RBAC) | Disable SAS keys when possible |
| Public network access | Enabled | Flag private endpoint as production backlog item |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "eventhub_namespace" {
  type      = "Microsoft.EventHub/namespaces@2024-01-01"
  name      = var.namespace_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name     = "Basic"
      tier     = "Basic"
      capacity = 1  # Throughput units
    }
    properties = {
      isAutoInflateEnabled   = false
      disableLocalAuth       = true   # CRITICAL: Disable SAS keys, enforce AAD
      publicNetworkAccess    = "Enabled"  # Disable for production
      minimumTlsVersion      = "1.2"
    }
  }

  tags = var.tags
}

resource "azapi_resource" "eventhub" {
  type      = "Microsoft.EventHub/namespaces/eventhubs@2024-01-01"
  name      = var.eventhub_name
  parent_id = azapi_resource.eventhub_namespace.id

  body = {
    properties = {
      partitionCount    = 2
      messageRetentionInDays = 1
    }
  }
}
```

### Consumer Group

```hcl
resource "azapi_resource" "consumer_group" {
  type      = "Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01"
  name      = var.consumer_group_name
  parent_id = azapi_resource.eventhub.id

  body = {
    properties = {
      userMetadata = "Consumer group for ${var.application_name}"
    }
  }
}
```

### RBAC Assignment

```hcl
# Azure Event Hubs Data Sender -- send events
resource "azapi_resource" "eventhub_sender_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.eventhub_namespace.id}${var.managed_identity_principal_id}eventhub-sender")
  parent_id = azapi_resource.eventhub_namespace.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/2b629674-e913-4c01-ae53-ef4638d8f975"  # Azure Event Hubs Data Sender
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Azure Event Hubs Data Receiver -- receive events
resource "azapi_resource" "eventhub_receiver_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.eventhub_namespace.id}${var.managed_identity_principal_id}eventhub-receiver")
  parent_id = azapi_resource.eventhub_namespace.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/a638d3c7-ab3a-418d-83e6-5f17a39d4fde"  # Azure Event Hubs Data Receiver
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Event Hubs namespace')
param namespaceName string

@description('Name of the event hub')
param eventHubName string

@description('Azure region')
param location string = resourceGroup().location

@description('Tags to apply')
param tags object = {}

resource namespace 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: namespaceName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
    tier: 'Basic'
    capacity: 1
  }
  properties: {
    isAutoInflateEnabled: false
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    minimumTlsVersion: '1.2'
  }
}

resource eventHub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: namespace
  name: eventHubName
  properties: {
    partitionCount: 2
    messageRetentionInDays: 1
  }
}

output namespaceId string = namespace.id
output namespaceName string = namespace.name
output eventHubName string = eventHub.name
```

### RBAC Assignment

```bicep
@description('Principal ID of the managed identity')
param principalId string

// Azure Event Hubs Data Sender
resource senderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(namespace.id, principalId, '2b629674-e913-4c01-ae53-ef4638d8f975')
  scope: namespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2b629674-e913-4c01-ae53-ef4638d8f975')  // Azure Event Hubs Data Sender
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

// Azure Event Hubs Data Receiver
resource receiverRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(namespace.id, principalId, 'a638d3c7-ab3a-418d-83e6-5f17a39d4fde')
  scope: namespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a638d3c7-ab3a-418d-83e6-5f17a39d4fde')  // Azure Event Hubs Data Receiver
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Fix |
|---------|--------|-----|
| Using SAS keys instead of AAD | Secrets in config, rotation burden | Set `disableLocalAuth = true`, use RBAC roles |
| Too few partitions | Cannot scale consumers beyond partition count; partitions cannot be increased after creation | Plan partition count based on expected consumer parallelism (2 for POC, 4-32 for production) |
| Forgetting consumer groups | Multiple consumers sharing `$Default` group compete for messages | Create dedicated consumer groups per consuming application |
| Basic tier limitations | No Kafka endpoint, 1 consumer group, 256 KB message size, 1 day retention | Use Standard tier if Kafka compatibility or multiple consumer groups are needed |
| Checkpoint storage missing | Consumers lose track of position, reprocess events | Provision a Storage Account with Blob Data Contributor for checkpoint storage |
| Not handling partitioned ordering | Events only ordered within a partition | Use partition keys to group related events to the same partition |

## Production Backlog Items

- [ ] Upgrade to Standard or Premium tier for Kafka support and higher limits
- [ ] Enable private endpoint and disable public network access
- [ ] Configure auto-inflate for throughput scaling (Standard tier)
- [ ] Set up capture to Azure Storage or Data Lake for event archival
- [ ] Configure geo-disaster recovery (namespace pairing)
- [ ] Set up monitoring alerts (throttled requests, incoming/outgoing messages, errors)
- [ ] Review partition count for production throughput requirements
- [ ] Enable diagnostic logging to Log Analytics workspace
- [ ] Configure network rules and IP filtering
