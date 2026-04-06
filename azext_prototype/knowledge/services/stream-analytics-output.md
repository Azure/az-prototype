---
service_namespace: Microsoft.StreamAnalytics/streamingjobs/outputs
display_name: Stream Analytics Output
depends_on:
  - Microsoft.StreamAnalytics/streamingjobs
---

# Stream Analytics Output

> Data sink binding for a Stream Analytics job that defines where processed query results are written -- Blob Storage, SQL Database, Cosmos DB, Event Hub, Power BI, or other supported destinations.

## When to Use
- **Blob/ADLS output** -- archive processed data to data lake for batch analytics or long-term storage
- **SQL Database output** -- write aggregated results to Azure SQL for operational dashboards
- **Cosmos DB output** -- low-latency writes for real-time serving layer
- **Event Hub output** -- chain to downstream Stream Analytics jobs or consumer applications
- **Power BI output** -- real-time streaming datasets for live dashboards
- **Azure Functions output** -- trigger serverless compute for custom processing

Every Stream Analytics job requires at least one output for the query's `INTO` clause to reference.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Authentication | Managed identity (MSI) | Preferred over connection strings |
| Serialization | JSON (UTF-8) | Match downstream consumer expectations |
| Output format | Line-separated JSON | For Blob/ADLS; most compatible |
| Write mode | Append | For streaming outputs; Upsert for SQL/Cosmos |

## Terraform Patterns

### Basic Resource

```hcl
# Blob Storage output
resource "azapi_resource" "output_blob" {
  type      = "Microsoft.StreamAnalytics/streamingjobs/outputs@2021-10-01-preview"
  name      = var.output_name
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
          container          = var.output_container
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

# Azure SQL output
resource "azapi_resource" "output_sql" {
  type      = "Microsoft.StreamAnalytics/streamingjobs/outputs@2021-10-01-preview"
  name      = "sql-output"
  parent_id = azapi_resource.stream_analytics_job.id

  body = {
    properties = {
      datasource = {
        type = "Microsoft.Sql/Server/Database"
        properties = {
          server             = var.sql_server_name
          database           = var.sql_database_name
          table              = var.sql_table_name
          authenticationMode = "Msi"
        }
      }
    }
  }
}

# Event Hub output (for chaining)
resource "azapi_resource" "output_eventhub" {
  type      = "Microsoft.StreamAnalytics/streamingjobs/outputs@2021-10-01-preview"
  name      = "eventhub-output"
  parent_id = azapi_resource.stream_analytics_job.id

  body = {
    properties = {
      datasource = {
        type = "Microsoft.EventHub/EventHub"
        properties = {
          serviceBusNamespace = var.output_eventhub_namespace
          eventHubName        = var.output_eventhub_name
          authenticationMode  = "Msi"
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
# Storage Blob Data Contributor for Blob output
resource "azapi_resource" "storage_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.storage_account_id}-${var.managed_identity_principal_id}-blob-contributor")
  parent_id = var.storage_account_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Azure Event Hubs Data Sender for Event Hub output
resource "azapi_resource" "eventhub_sender" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.output_eventhub_namespace_id}-${var.managed_identity_principal_id}-sender")
  parent_id = var.output_eventhub_namespace_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/2b629674-e913-4c01-ae53-ef4638d8f975"
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Output name (referenced in the job query INTO clause)')
param outputName string

@description('Storage account name')
param storageAccountName string

@description('Output container name')
param containerName string

resource output 'Microsoft.StreamAnalytics/streamingjobs/outputs@2021-10-01-preview' = {
  parent: streamAnalyticsJob
  name: outputName
  properties: {
    datasource: {
      type: 'Microsoft.Storage/Blob'
      properties: {
        storageAccounts: [
          {
            accountName: storageAccountName
          }
        ]
        container: containerName
        pathPattern: '{date}/{time}'
        dateFormat: 'yyyy/MM/dd'
        timeFormat: 'HH'
        authenticationMode: 'Msi'
      }
    }
    serialization: {
      type: 'Json'
      properties: {
        encoding: 'UTF8'
        format: 'LineSeparated'
      }
    }
  }
}
```

## Application Code

### Python
Infrastructure -- transparent to application code. Stream Analytics outputs define where processed results are written; downstream applications read from those destinations using their respective SDKs (e.g., Azure Storage SDK, pyodbc for SQL).

### C#
Infrastructure -- transparent to application code. Stream Analytics outputs define where processed results are written; downstream applications read from those destinations using their respective SDKs (e.g., Azure.Storage.Blobs, SqlClient for SQL).

### Node.js
Infrastructure -- transparent to application code. Stream Analytics outputs define where processed results are written; downstream applications read from those destinations using their respective SDKs (e.g., @azure/storage-blob, mssql for SQL).

## Common Pitfalls

1. **Output name must match query** -- The output name must exactly match the `INTO` clause in the job query (e.g., `INTO [blob-output]`). Mismatches cause "output not found" errors.
2. **SQL table must pre-exist** -- Unlike Blob Storage, the SQL output does not auto-create the target table. The table must exist with matching column names and types.
3. **MSI permissions on destination** -- The job's managed identity needs write permissions on the output resource. Storage Blob Data Contributor for Blob, Event Hubs Data Sender for Event Hub, db_owner for SQL.
4. **Blob path pattern time lag** -- The `{date}` and `{time}` tokens in blob path patterns use the event time, not the wall clock. Late-arriving events may write to past time partitions.
5. **Power BI output token expiration** -- Power BI outputs use OAuth tokens that expire after 90 days. Jobs silently stop writing to Power BI after expiration. Re-authorize periodically.
6. **Output batching** -- Some outputs (Blob, SQL) batch writes for efficiency. This introduces a small delay between processing and data availability in the destination.
7. **Max writers per output** -- Parallel queries with `PARTITION BY` create multiple writers per output. Ensure the destination can handle concurrent writes (e.g., SQL DTU, Cosmos RU/s).
8. **Cosmos DB partition key** -- When writing to Cosmos DB, the output record must include the configured partition key field. Missing partition keys cause write failures.

## Production Backlog Items

- [ ] Configure output error policies (retry vs drop) based on data criticality
- [ ] Add multiple output destinations for different consumers (archive + real-time)
- [ ] Implement output diagnostic logging for write failures
- [ ] Plan partition alignment between query and output for optimal write throughput
- [ ] Configure blob output path pattern for efficient downstream querying
- [ ] Set up dead-letter destination for records that fail to write
- [ ] Monitor output latency and throughput metrics
- [ ] Plan Power BI OAuth token refresh automation if using Power BI output
