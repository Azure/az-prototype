---
service_namespace: Microsoft.StreamAnalytics/streamingjobs/inputs
display_name: Stream Analytics Input
depends_on:
  - Microsoft.StreamAnalytics/streamingjobs
---

# Stream Analytics Input

> Data source binding for a Stream Analytics job that defines where streaming or reference data is ingested from -- Event Hubs, IoT Hub, Blob Storage, or other supported sources.

## When to Use
- **Event Hub input** -- high-throughput streaming data from applications, microservices, or Kafka-compatible producers
- **IoT Hub input** -- device telemetry from IoT Hub's built-in Event Hub-compatible endpoint
- **Blob Storage input** -- reference data (lookup tables) or batch streaming from blob files
- **Kafka input** -- direct Kafka cluster connectivity (preview)
- Every Stream Analytics job requires at least one input before the query can reference data

Inputs are either **Stream** (continuous flow) or **Reference** (static/slowly-changing lookup data). Most jobs have one stream input and optionally one or more reference inputs for enrichment.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Input type | Stream | Continuous data flow |
| Source | Event Hub | Most common streaming source |
| Authentication | Managed identity (MSI) | Preferred over connection strings |
| Serialization | JSON (UTF-8) | Most common; Avro and CSV also supported |
| Consumer group | Dedicated | One consumer group per Stream Analytics job |

## Terraform Patterns

### Basic Resource

```hcl
# Event Hub stream input
resource "azapi_resource" "input_eventhub" {
  type      = "Microsoft.StreamAnalytics/streamingjobs/inputs@2021-10-01-preview"
  name      = var.input_name
  parent_id = azapi_resource.stream_analytics_job.id

  body = {
    properties = {
      type = "Stream"
      datasource = {
        type = "Microsoft.EventHub/EventHub"
        properties = {
          serviceBusNamespace = var.eventhub_namespace_name
          eventHubName        = var.eventhub_name
          consumerGroupName   = var.consumer_group_name
          authenticationMode  = "Msi"
        }
      }
      serialization = {
        type = "Json"
        properties = {
          encoding = "UTF8"
        }
      }
    }
  }
}

# Blob Storage reference input
resource "azapi_resource" "input_reference" {
  type      = "Microsoft.StreamAnalytics/streamingjobs/inputs@2021-10-01-preview"
  name      = "reference-data"
  parent_id = azapi_resource.stream_analytics_job.id

  body = {
    properties = {
      type = "Reference"
      datasource = {
        type = "Microsoft.Storage/Blob"
        properties = {
          storageAccounts = [
            {
              accountName = var.storage_account_name
            }
          ]
          container          = var.reference_container
          pathPattern        = "reference/{date}/lookup.json"
          dateFormat         = "yyyy-MM-dd"
          authenticationMode = "Msi"
        }
      }
      serialization = {
        type = "Json"
        properties = {
          encoding = "UTF8"
        }
      }
    }
  }
}
```

### RBAC Assignment

```hcl
# Azure Event Hubs Data Receiver for stream input
resource "azapi_resource" "eventhub_receiver" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.eventhub_namespace_id}-${var.managed_identity_principal_id}-receiver")
  parent_id = var.eventhub_namespace_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/a638d3c7-ab3a-418d-83e6-5f17a39d4fde"
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Storage Blob Data Reader for reference input
resource "azapi_resource" "storage_reader" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.storage_account_id}-${var.managed_identity_principal_id}-blob-reader")
  parent_id = var.storage_account_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Input name (referenced in the job query)')
param inputName string

@description('Event Hub namespace name')
param eventHubNamespace string

@description('Event Hub name')
param eventHubName string

@description('Consumer group name')
param consumerGroupName string = '$Default'

resource input 'Microsoft.StreamAnalytics/streamingjobs/inputs@2021-10-01-preview' = {
  parent: streamAnalyticsJob
  name: inputName
  properties: {
    type: 'Stream'
    datasource: {
      type: 'Microsoft.EventHub/EventHub'
      properties: {
        serviceBusNamespace: eventHubNamespace
        eventHubName: eventHubName
        consumerGroupName: consumerGroupName
        authenticationMode: 'Msi'
      }
    }
    serialization: {
      type: 'Json'
      properties: {
        encoding: 'UTF8'
      }
    }
  }
}
```

## Application Code

### Python
Infrastructure -- transparent to application code. Stream Analytics inputs define data ingestion sources; applications produce data to Event Hubs or IoT Hub using their respective SDKs, and Stream Analytics consumes it.

### C#
Infrastructure -- transparent to application code. Stream Analytics inputs define data ingestion sources; applications produce data to Event Hubs or IoT Hub using their respective SDKs, and Stream Analytics consumes it.

### Node.js
Infrastructure -- transparent to application code. Stream Analytics inputs define data ingestion sources; applications produce data to Event Hubs or IoT Hub using their respective SDKs, and Stream Analytics consumes it.

## Common Pitfalls

1. **Input name must match query** -- The input name in the resource definition must exactly match the `FROM` clause in the Stream Analytics query (e.g., `FROM [eventhub-input]`). Mismatched names cause "input not found" errors.
2. **Consumer group contention** -- Multiple readers on `$Default` consumer group cause message loss. Always create a dedicated consumer group per Stream Analytics job.
3. **Serialization mismatch** -- If the input expects JSON but receives CSV (or vice versa), records are silently dropped or malformed. Match serialization to the actual data format.
4. **MSI permissions delay** -- After granting Event Hubs Data Receiver role, it takes up to 10 minutes to propagate. The job may fail to start during this window.
5. **Reference data refresh interval** -- Reference data is loaded once at job start and refreshed per the `refreshInterval` or path pattern. Stale reference data produces incorrect join results.
6. **IoT Hub consumer group limit** -- IoT Hub supports a maximum of 5 consumer groups per endpoint (10 for S2/S3). Plan consumer group allocation across all consumers.
7. **Partition key alignment** -- For optimal throughput, align the Stream Analytics query `PARTITION BY` with the Event Hub partition key. Misalignment prevents query parallelism.

## Production Backlog Items

- [ ] Create dedicated consumer groups for all Stream Analytics inputs
- [ ] Configure reference data with appropriate refresh intervals
- [ ] Add input diagnostic logging for deserialization errors
- [ ] Implement input schema validation for early error detection
- [ ] Configure dead-letter queue for events that fail deserialization
- [ ] Test input connectivity and throughput under expected data volumes
- [ ] Plan partition scaling on Event Hub to match Stream Analytics throughput needs
