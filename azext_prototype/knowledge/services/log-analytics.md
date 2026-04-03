# Log Analytics Workspace
> Centralized log aggregation and query service in Azure Monitor, providing the data store and query engine for diagnostics, metrics, and operational insights across all Azure resources.

## When to Use

- Foundation service for all Azure monitoring and observability
- Collecting diagnostic logs and metrics from Azure resources
- Backing store for Application Insights (workspace-based)
- Required by Container Apps Environment
- Centralized log querying with Kusto Query Language (KQL)
- Security monitoring with Microsoft Sentinel
- NOT suitable for: real-time streaming analytics (use Event Hubs + Stream Analytics), long-term archival storage (use Storage Account export), or application-level custom metrics without diagnostic settings

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | PerGB2018 | Only pricing tier for new workspaces |
| Retention | 30 days | Free retention period; beyond 30 days incurs charges |
| Daily cap | Not set (unlimited) | Set a cap in production to control costs |
| Location | Same as resource group | Must match or be in a supported region |

**Foundation service**: Log Analytics Workspace is typically created in Stage 1 (foundation) and referenced by all subsequent resources that need monitoring. Create it early in the deployment sequence.

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "log_analytics" {
  type      = "Microsoft.OperationalInsights/workspaces@2023-09-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      sku = {
        name = "PerGB2018"
      }
      retentionInDays = var.retention_in_days  # 30 for POC
    }
  }

  tags = var.tags

  response_export_values = ["properties.customerId"]
}
```

### With Diagnostic Settings (apply to other resources)

```hcl
# Example: send Key Vault diagnostics to Log Analytics
resource "azapi_resource" "diag_keyvault" {
  type      = "Microsoft.Insights/diagnosticSettings@2021-05-01-preview"
  name      = "diag-${var.keyvault_name}"
  parent_id = var.keyvault_id

  body = {
    properties = {
      workspaceId = azapi_resource.log_analytics.id
      logs = [
        {
          category = "AuditEvent"
          enabled  = true
        },
        {
          category = "AzurePolicyEvaluationDetails"
          enabled  = true
        }
      ]
      metrics = [
        {
          category = "AllMetrics"
          enabled  = true
        }
      ]
    }
  }
}

# Example: send App Service diagnostics to Log Analytics
resource "azapi_resource" "diag_webapp" {
  type      = "Microsoft.Insights/diagnosticSettings@2021-05-01-preview"
  name      = "diag-${var.webapp_name}"
  parent_id = var.webapp_id

  body = {
    properties = {
      workspaceId = azapi_resource.log_analytics.id
      logs = [
        {
          category = "AppServiceHTTPLogs"
          enabled  = true
        },
        {
          category = "AppServiceConsoleLogs"
          enabled  = true
        },
        {
          category = "AppServiceAppLogs"
          enabled  = true
        }
      ]
      metrics = [
        {
          category = "AllMetrics"
          enabled  = true
        }
      ]
    }
  }
}
```

### RBAC Assignment

```hcl
# Grant read access for querying logs
resource "azapi_resource" "reader_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.log_analytics.id}${var.reader_principal_id}reader")
  parent_id = azapi_resource.log_analytics.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/73c42c96-874c-492b-b04d-ab87d138a893"  # Log Analytics Reader
      principalId      = var.reader_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Grant contributor access for managing workspace settings
resource "azapi_resource" "contributor_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.log_analytics.id}${var.admin_principal_id}contributor")
  parent_id = azapi_resource.log_analytics.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/92aaf0da-9dab-42b6-94a3-d43ce8d16293"  # Log Analytics Contributor
      principalId      = var.admin_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

```hcl
# Private endpoint for Log Analytics is via Azure Monitor Private Link Scope (AMPLS)
# Unless told otherwise, private endpoint via AMPLS is required per governance policy --
# publicNetworkAccessForIngestion and publicNetworkAccessForQuery should be set to "Disabled"

# For production:
resource "azapi_resource" "ampls" {
  count     = var.enable_private_link ? 1 : 0
  type      = "Microsoft.Insights/privateLinkScopes@2021-07-01-preview"
  name      = "ampls-${var.name}"
  location  = "global"
  parent_id = var.resource_group_id

  body = {
    properties = {
      accessModeSettings = {
        ingestionAccessMode = "PrivateOnly"
        queryAccessMode     = "PrivateOnly"
      }
    }
  }

  tags = var.tags
}

resource "azapi_resource" "ampls_scoped_service" {
  count     = var.enable_private_link ? 1 : 0
  type      = "Microsoft.Insights/privateLinkScopes/scopedResources@2021-07-01-preview"
  name      = "amplsservice-${var.name}"
  parent_id = azapi_resource.ampls[0].id

  body = {
    properties = {
      linkedResourceId = azapi_resource.log_analytics.id
    }
  }
}

resource "azapi_resource" "private_endpoint" {
  count     = var.enable_private_link && var.subnet_id != null ? 1 : 0
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
            privateLinkServiceId = azapi_resource.ampls[0].id
            groupIds             = ["azuremonitor"]
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

## Bicep Patterns

### Basic Resource

```bicep
param name string
param location string
param retentionInDays int = 30
param tags object = {}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: name
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
  }
  tags: tags
}

output id string = logAnalytics.id
output name string = logAnalytics.name
output customerId string = logAnalytics.properties.customerId
```

### Diagnostic Settings (applied to another resource)

```bicep
param workspaceId string
param targetResourceId string
param settingName string

resource diagnosticSetting 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: settingName
  scope: targetResource
  properties: {
    workspaceId: workspaceId
    logs: [
      {
        category: 'AuditEvent'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}
```

### RBAC Assignment

```bicep
param principalId string

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsName
}

// Log Analytics Reader
resource readerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(logAnalytics.id, principalId, '73c42c96-874c-492b-b04d-ab87d138a893')
  scope: logAnalytics
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '73c42c96-874c-492b-b04d-ab87d138a893')
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Application Code

Log Analytics Workspace is primarily an infrastructure service. Application code interacts with it indirectly through SDKs that emit telemetry (App Insights, OpenTelemetry) or directly for log queries.

### Python (Query Logs)

```python
import os
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.monitor.query import LogsQueryClient
from datetime import timedelta

def get_credential():
    client_id = os.getenv("AZURE_CLIENT_ID")
    if client_id:
        return ManagedIdentityCredential(client_id=client_id)
    return DefaultAzureCredential()

workspace_id = os.getenv("LOG_ANALYTICS_WORKSPACE_ID")  # Customer ID (GUID)
credential = get_credential()

client = LogsQueryClient(credential)
response = client.query_workspace(
    workspace_id=workspace_id,
    query="AppRequests | summarize count() by resultCode | order by count_ desc",
    timespan=timedelta(hours=24),
)

for row in response.tables[0].rows:
    print(f"Status {row[0]}: {row[1]} requests")
```

### C# (Query Logs)

```csharp
using Azure.Identity;
using Azure.Monitor.Query;
using Azure.Monitor.Query.Models;

var clientId = Environment.GetEnvironmentVariable("AZURE_CLIENT_ID");
var credential = string.IsNullOrEmpty(clientId)
    ? new DefaultAzureCredential()
    : new ManagedIdentityCredential(clientId);

var workspaceId = Environment.GetEnvironmentVariable("LOG_ANALYTICS_WORKSPACE_ID");
var client = new LogsQueryClient(credential);

var response = await client.QueryWorkspaceAsync(
    workspaceId,
    "AppRequests | summarize count() by resultCode | order by count_ desc",
    new QueryTimeRange(TimeSpan.FromHours(24))
);

foreach (var row in response.Value.Table.Rows)
{
    Console.WriteLine($"Status {row[0]}: {row[1]} requests");
}
```

### Node.js (Query Logs)

```javascript
const { LogsQueryClient } = require("@azure/monitor-query");
const { DefaultAzureCredential, ManagedIdentityCredential } = require("@azure/identity");

function getCredential() {
  const clientId = process.env.AZURE_CLIENT_ID;
  return clientId
    ? new ManagedIdentityCredential(clientId)
    : new DefaultAzureCredential();
}

const workspaceId = process.env.LOG_ANALYTICS_WORKSPACE_ID;
const client = new LogsQueryClient(getCredential());

async function queryLogs() {
  const result = await client.queryWorkspace(
    workspaceId,
    "AppRequests | summarize count() by resultCode | order by count_ desc",
    { duration: "PT24H" }
  );

  for (const row of result.tables[0].rows) {
    console.log(`Status ${row[0]}: ${row[1]} requests`);
  }
}
```

## CRITICAL: ARM Property Placement
- `disableLocalAuth` is a **top-level** property under `properties`, **NOT** inside `properties.features`
- The ARM API _silently drops_ `disableLocalAuth` if nested inside `features`
- CORRECT: `properties = { disableLocalAuth = false, features = { enableLogAccessUsingOnlyResourcePermissions = true } }`
- WRONG: `properties = { features = { disableLocalAuth = false } }`

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Creating multiple workspaces unnecessarily | Fragmented logs, harder to query across resources | Use a single workspace per environment for most POCs |
| Not setting retention policy | Default 30 days may be too short for production | Configure retention explicitly; accept 30 days for POC |
| Ignoring ingestion costs | Unexpected bills from high-volume log sources | Set daily cap for production; monitor ingestion volume |
| Not enabling diagnostic settings on resources | Resources create no logs in the workspace | Add an `azapi_resource` of type `Microsoft.Insights/diagnosticSettings` for each resource |
| Workspace region mismatch | Some diagnostic settings require same-region workspace | Deploy workspace in the same region as the resource group |
| Querying without proper RBAC | Access denied on workspace queries | Assign `Log Analytics Reader` role for query access |
| Confusing workspace ID with resource ID | API calls fail | Workspace ID (customerId) is the GUID used for queries; resource ID is the ARM path |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Data retention policies | P3 | Configure per-table retention beyond the default 30 days for compliance |
| Daily ingestion cap | P3 | Set daily cap to prevent unexpected cost spikes |
| Workspace-based access control | P3 | Configure table-level RBAC for fine-grained access to sensitive logs |
| Azure Private Link Scope | P1 | Deploy AMPLS for private ingestion and query endpoints |
| Data export rules | P4 | Configure continuous export to Storage Account for long-term archival |
| Dedicated cluster | P4 | For high-volume scenarios (500+ GB/day), use a dedicated cluster for cost optimization |
| Alert rules | P3 | Create log-based and metric-based alert rules for operational monitoring |
| Workbooks and dashboards | P3 | Build Azure Monitor Workbooks for visual dashboards |
| Cross-workspace queries | P4 | Configure cross-workspace queries if multiple workspaces exist |
| Sentinel integration | P2 | Enable Microsoft Sentinel on the workspace for security monitoring |
