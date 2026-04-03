# Azure Stream Analytics
> Real-time analytics service for processing high-velocity streaming data from IoT devices, applications, and infrastructure with SQL-like query language.

## When to Use

- **Real-time analytics** -- continuous queries over data streams from IoT Hub, Event Hubs, or Blob Storage
- **IoT telemetry processing** -- aggregation, filtering, and enrichment of device telemetry data
- **Real-time dashboards** -- streaming data to Power BI for live operational dashboards
- **Anomaly detection** -- built-in ML functions for spike, dip, and trend change detection
- **Event-driven alerting** -- trigger actions based on streaming data patterns and thresholds
- **Data transformation** -- real-time ETL from streaming sources to data stores

Choose Stream Analytics over Databricks Structured Streaming when you need a no-code/low-code SQL approach without managing Spark clusters. Choose Databricks for complex ML pipelines or when you already have a Spark ecosystem.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Standard | Only tier available for cloud jobs |
| Streaming units | 1-3 | Minimum for POC; scale based on throughput |
| Compatibility level | 1.2 | Latest; use for new jobs |
| Output error policy | Retry | Retry transient errors; drop only if persistent |
| Late arrival tolerance | 5 seconds | Default; increase for out-of-order data |
| Out-of-order tolerance | 0 seconds | Default; increase for distributed sources |
| Event serialization | JSON | Most common; Avro and CSV also supported |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "stream_analytics_job" {
  type      = "Microsoft.StreamAnalytics/streamingJobs@2021-10-01-preview"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type         = "UserAssigned"
    identity_ids = [var.managed_identity_id]
  }

  body = {
    properties = {
      sku = {
        name = "Standard"
      }
      compatibilityLevel             = "1.2"
      eventsOutOfOrderPolicy         = "Adjust"
      eventsOutOfOrderMaxDelayInSeconds = 0
      eventsLateArrivalMaxDelayInSeconds = 5
      outputErrorPolicy              = "Stop"
      dataLocale                     = "en-US"
      transformation = {
        name = "main-query"
        properties = {
          streamingUnits = 3
          query          = var.query  # SQL query
        }
      }
    }
  }

  tags = var.tags
}
```

### Input (Event Hub)

```hcl
resource "azapi_resource" "input_eventhub" {
  type      = "Microsoft.StreamAnalytics/streamingJobs/inputs@2021-10-01-preview"
  name      = "eventhub-input"
  parent_id = azapi_resource.stream_analytics_job.id

  body = {
    properties = {
      type = "Stream"
      datasource = {
        type = "Microsoft.EventHub/EventHub"
        properties = {
          serviceBusNamespace  = var.eventhub_namespace_name
          eventHubName         = var.eventhub_name
          consumerGroupName    = var.consumer_group_name
          authenticationMode   = "Msi"  # Use managed identity
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

### Input (IoT Hub)

```hcl
resource "azapi_resource" "input_iothub" {
  type      = "Microsoft.StreamAnalytics/streamingJobs/inputs@2021-10-01-preview"
  name      = "iothub-input"
  parent_id = azapi_resource.stream_analytics_job.id

  body = {
    properties = {
      type = "Stream"
      datasource = {
        type = "Microsoft.Devices/IotHubs"
        properties = {
          iotHubNamespace      = var.iot_hub_name
          sharedAccessPolicyName = "service"
          sharedAccessPolicyKey  = var.iot_hub_sas_key  # Store in Key Vault
          consumerGroupName    = var.consumer_group_name
          endpoint             = "messages/events"
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

### Output (Blob Storage)

```hcl
resource "azapi_resource" "output_blob" {
  type      = "Microsoft.StreamAnalytics/streamingJobs/outputs@2021-10-01-preview"
  name      = "blob-output"
  parent_id = azapi_resource.stream_analytics_job.id

  body = {
    properties = {
      datasource = {
        type = "Microsoft.Storage/Blob"
        properties = {
          storageAccounts = [
            {
              accountName = var.storage_account_name
            }
          ]
          container          = var.container_name
          pathPattern        = "{date}/{time}"
          dateFormat         = "yyyy/MM/dd"
          timeFormat         = "HH"
          authenticationMode = "Msi"
        }
      }
      serialization = {
        type = "Json"
        properties = {
          encoding = "UTF8"
          format   = "LineSeparated"
        }
      }
    }
  }
}
```

### RBAC Assignment

```hcl
# Grant Stream Analytics identity access to Event Hub for input
resource "azapi_resource" "eventhub_data_receiver" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.eventhub_namespace_id}${var.managed_identity_principal_id}eventhub-receiver")
  parent_id = var.eventhub_namespace_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/a638d3c7-ab3a-418d-83e6-5f17a39d4fde"  # Azure Event Hubs Data Receiver
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Grant Stream Analytics identity access to storage for output
resource "azapi_resource" "storage_blob_contributor" {
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

### Private Endpoint

Stream Analytics jobs do not support private endpoints on the job itself. To secure inputs and outputs, use private endpoints on the source/destination resources (Event Hub, Storage, SQL) and configure the Stream Analytics job to run in a VNet:

```hcl
# VNet integration via cluster (Standard V2)
# Stream Analytics clusters provide VNet isolation for jobs.
# Note: Clusters are expensive (36 SUs minimum) and not typical for POC.
# For POC, secure the input/output resources with private endpoints instead.
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Stream Analytics job')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Managed identity resource ID')
param managedIdentityId string

@description('Stream Analytics SQL query')
param query string

@description('Tags to apply')
param tags object = {}

resource streamJob 'Microsoft.StreamAnalytics/streamingJobs@2021-10-01-preview' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  properties: {
    sku: {
      name: 'Standard'
    }
    compatibilityLevel: '1.2'
    eventsOutOfOrderPolicy: 'Adjust'
    eventsOutOfOrderMaxDelayInSeconds: 0
    eventsLateArrivalMaxDelayInSeconds: 5
    outputErrorPolicy: 'Stop'
    dataLocale: 'en-US'
    transformation: {
      name: 'main-query'
      properties: {
        streamingUnits: 3
        query: query
      }
    }
  }
}

output id string = streamJob.id
output name string = streamJob.name
```

### Input and Output

```bicep
@description('Event Hub namespace name')
param eventHubNamespace string

@description('Event Hub name')
param eventHubName string

@description('Consumer group name')
param consumerGroupName string = '$Default'

resource input 'Microsoft.StreamAnalytics/streamingJobs/inputs@2021-10-01-preview' = {
  parent: streamJob
  name: 'eventhub-input'
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

### RBAC Assignment

```bicep
@description('Principal ID for Event Hub access')
param principalId string

@description('Event Hub namespace resource ID')
param eventHubNamespaceId string

var eventHubDataReceiverRoleId = 'a638d3c7-ab3a-418d-83e6-5f17a39d4fde'

resource eventHubReceiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(eventHubNamespaceId, principalId, eventHubDataReceiverRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', eventHubDataReceiverRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Query syntax errors at deploy time | Job starts but produces no output | Test queries in Azure portal's query testing tool before deploying |
| Insufficient streaming units | Job falls behind on processing; increasing lag | Monitor SU% utilization; scale up when consistently above 80% |
| Missing consumer group | Multiple readers contend for partitions | Create dedicated consumer groups per Stream Analytics job |
| Windowing function misuse | Unexpected aggregation results | Understand Tumbling, Hopping, Sliding, and Session windows semantics |
| Output serialization mismatch | Downstream systems cannot parse output | Match output serialization format to consumer expectations |
| Job start mode confusion | `JobStartTime` vs `CustomTime` vs `LastOutputEventTime` | Use `LastOutputEventTime` for restart after failure to avoid data loss |
| Reference data refresh | Stale reference data causes incorrect joins | Configure reference data refresh interval in input definition |
| Late arrival data dropped | Data outside tolerance window silently discarded | Set appropriate late arrival tolerance based on data source characteristics |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Streaming unit optimization | P2 | Right-size SUs based on actual throughput and latency requirements |
| VNet integration | P1 | Deploy into Stream Analytics cluster for VNet isolation (if required) |
| Monitoring and alerts | P1 | Set up alerts for watermark delay, SU utilization, and runtime errors |
| Reference data integration | P2 | Add reference data inputs for enriching streaming data with lookup tables |
| Output to multiple sinks | P2 | Configure additional outputs (SQL, Cosmos DB, Power BI) |
| Custom deserializer | P3 | Implement custom deserializer for non-standard input formats |
| CI/CD pipeline | P2 | Automate job deployment with ARM templates and query versioning |
| Anomaly detection | P3 | Enable built-in anomaly detection functions for spike/dip detection |
| Disaster recovery | P3 | Deploy paired jobs in secondary region for geo-redundancy |
| Query optimization | P2 | Optimize query parallelism with PARTITION BY for throughput scaling |
