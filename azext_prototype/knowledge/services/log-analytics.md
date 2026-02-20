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
resource "azurerm_log_analytics_workspace" "this" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = var.retention_in_days  # 30 for POC

  tags = var.tags
}
```

### With Diagnostic Settings (apply to other resources)

```hcl
# Example: send Key Vault diagnostics to Log Analytics
resource "azurerm_monitor_diagnostic_setting" "keyvault" {
  name                       = "diag-${var.keyvault_name}"
  target_resource_id         = var.keyvault_id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category = "AuditEvent"
  }

  enabled_log {
    category = "AzurePolicyEvaluationDetails"
  }

  metric {
    category = "AllMetrics"
  }
}

# Example: send App Service diagnostics to Log Analytics
resource "azurerm_monitor_diagnostic_setting" "webapp" {
  name                       = "diag-${var.webapp_name}"
  target_resource_id         = var.webapp_id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category = "AppServiceHTTPLogs"
  }

  enabled_log {
    category = "AppServiceConsoleLogs"
  }

  enabled_log {
    category = "AppServiceAppLogs"
  }

  metric {
    category = "AllMetrics"
  }
}
```

### RBAC Assignment

```hcl
# Grant read access for querying logs
resource "azurerm_role_assignment" "reader" {
  scope                = azurerm_log_analytics_workspace.this.id
  role_definition_name = "Log Analytics Reader"
  principal_id         = var.reader_principal_id
}

# Grant contributor access for managing workspace settings
resource "azurerm_role_assignment" "contributor" {
  scope                = azurerm_log_analytics_workspace.this.id
  role_definition_name = "Log Analytics Contributor"
  principal_id         = var.admin_principal_id
}
```

### Private Endpoint

```hcl
# Private endpoint for Log Analytics is via Azure Monitor Private Link Scope (AMPLS)
# This is NOT typically needed for POC -- public ingestion and query endpoints are fine
# Include as a production backlog item

# For production:
resource "azurerm_monitor_private_link_scope" "this" {
  count               = var.enable_private_link ? 1 : 0
  name                = "ampls-${var.name}"
  resource_group_name = var.resource_group_name

  tags = var.tags
}

resource "azurerm_monitor_private_link_scoped_service" "this" {
  count               = var.enable_private_link ? 1 : 0
  name                = "amplsservice-${var.name}"
  resource_group_name = var.resource_group_name
  scope_name          = azurerm_monitor_private_link_scope.this[0].name
  linked_resource_id  = azurerm_log_analytics_workspace.this.id
}

resource "azurerm_private_endpoint" "this" {
  count = var.enable_private_link && var.subnet_id != null ? 1 : 0

  name                = "pe-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id

  private_service_connection {
    name                           = "psc-${var.name}"
    private_connection_resource_id = azurerm_monitor_private_link_scope.this[0].id
    subresource_names              = ["azuremonitor"]
    is_manual_connection           = false
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

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Creating multiple workspaces unnecessarily | Fragmented logs, harder to query across resources | Use a single workspace per environment for most POCs |
| Not setting retention policy | Default 30 days may be too short for production | Configure retention explicitly; accept 30 days for POC |
| Ignoring ingestion costs | Unexpected bills from high-volume log sources | Set daily cap for production; monitor ingestion volume |
| Not enabling diagnostic settings on resources | Resources create no logs in the workspace | Add `azurerm_monitor_diagnostic_setting` for each resource |
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
