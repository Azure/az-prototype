---
service_namespace: Microsoft.Monitor/accounts
display_name: Azure Monitor Workspace (Managed Prometheus)
---

# Azure Monitor Workspace (Managed Prometheus)

> Dedicated workspace for Azure Managed Prometheus metrics, providing a scalable time-series database for collecting, storing, and querying Prometheus metrics from Kubernetes and other workloads.

## When to Use
- **AKS monitoring** -- collect Prometheus metrics from AKS clusters using Azure Monitor managed Prometheus
- **Grafana dashboards** -- pair with Azure Managed Grafana for Prometheus-native visualization
- **Multi-cluster monitoring** -- centralize Prometheus metrics from multiple AKS clusters
- **Custom metrics** -- store application-level Prometheus metrics alongside infrastructure metrics

Azure Monitor workspaces are distinct from Log Analytics workspaces. They store Prometheus metrics in a time-series format optimized for PromQL queries.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Location | Same as AKS cluster | Minimize latency and egress costs |
| Default DCR | Auto-created | Data collection rule for Prometheus scraping |
| Retention | 18 months | Included; no configuration needed |
| Grafana | Azure Managed Grafana | Link for PromQL dashboards |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "monitor_account" {
  type      = "Microsoft.Monitor/accounts@2023-04-03"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {}
  }

  tags = var.tags

  response_export_values = [
    "properties.defaultIngestionSettings.dataCollectionEndpointResourceId",
    "properties.defaultIngestionSettings.dataCollectionRuleResourceId",
    "properties.metrics.prometheusQueryEndpoint"
  ]
}
```

### RBAC Assignment

```hcl
# Monitoring Data Reader -- for querying Prometheus metrics (Grafana)
resource "azapi_resource" "monitoring_data_reader" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.monitor_account.id}-${var.grafana_principal_id}-data-reader")
  parent_id = azapi_resource.monitor_account.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/b0d8363b-8ddd-447d-831f-62ca05bff136"
      principalId      = var.grafana_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Monitoring Contributor -- for managing the workspace
resource "azapi_resource" "monitoring_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.monitor_account.id}-${var.principal_id}-monitoring-contributor")
  parent_id = azapi_resource.monitor_account.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/749f88d5-cbae-40b8-bcfc-e573ddc772fa"
      principalId      = var.principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Azure Monitor workspace')
param name string

@description('Azure region')
param location string = resourceGroup().location

param tags object = {}

resource monitorAccount 'Microsoft.Monitor/accounts@2023-04-03' = {
  name: name
  location: location
  tags: tags
  properties: {}
}

output id string = monitorAccount.id
output prometheusQueryEndpoint string = monitorAccount.properties.metrics.prometheusQueryEndpoint
output defaultDcrId string = monitorAccount.properties.defaultIngestionSettings.dataCollectionRuleResourceId
```

## Application Code

### Python

```python
# Applications expose Prometheus metrics; the Azure Monitor agent scrapes them.
# No Azure SDK needed -- use standard Prometheus client libraries.
from prometheus_client import Counter, Histogram, start_http_server

REQUEST_COUNT = Counter("http_requests_total", "Total HTTP requests", ["method", "endpoint"])
REQUEST_LATENCY = Histogram("http_request_duration_seconds", "Request latency", ["endpoint"])

# Start metrics endpoint for scraping
start_http_server(8080)

# In your request handler:
REQUEST_COUNT.labels(method="GET", endpoint="/api/items").inc()
with REQUEST_LATENCY.labels(endpoint="/api/items").time():
    pass  # handle request
```

### C#

```csharp
// Use prometheus-net library to expose metrics
// Install: dotnet add package prometheus-net.AspNetCore
using Prometheus;

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

// Add Prometheus metrics middleware
app.UseHttpMetrics();  // Auto-tracks HTTP request metrics
app.MapMetrics();      // Exposes /metrics endpoint

var requestCounter = Metrics.CreateCounter(
    "http_requests_total", "Total HTTP requests",
    new CounterConfiguration { LabelNames = new[] { "method", "endpoint" } });

requestCounter.WithLabels("GET", "/api/items").Inc();
app.Run();
```

### Node.js

```typescript
// Use prom-client library to expose metrics
// Install: npm install prom-client
import { Counter, Histogram, collectDefaultMetrics, register } from "prom-client";

collectDefaultMetrics();

const requestCount = new Counter({
  name: "http_requests_total",
  help: "Total HTTP requests",
  labelNames: ["method", "endpoint"],
});

const requestLatency = new Histogram({
  name: "http_request_duration_seconds",
  help: "Request latency",
  labelNames: ["endpoint"],
});

// Expose /metrics endpoint
app.get("/metrics", async (req, res) => {
  res.set("Content-Type", register.contentType);
  res.end(await register.metrics());
});
```

## Common Pitfalls

1. **Not the same as Log Analytics workspace** -- `Microsoft.Monitor/accounts` is for Prometheus metrics. `Microsoft.OperationalInsights/workspaces` is for logs. They are different resources with different APIs.
2. **Region availability** -- Azure Monitor workspaces are not available in all regions. Check regional availability before deployment.
3. **Grafana must have Data Reader role** -- Azure Managed Grafana needs `Monitoring Data Reader` role on the workspace to query metrics. Missing this causes "no data" in dashboards.
4. **Data collection rule configuration** -- The workspace auto-creates a default DCR, but you must configure the AKS cluster to use it (via `Microsoft.ContainerService/managedClusters` monitoring addon or DCR association).
5. **Metric cardinality** -- High-cardinality labels (user IDs, request IDs) cause metric explosion and storage costs. Use bounded label values.
6. **Ingestion latency** -- Metrics have a 1-3 minute ingestion delay. Dashboards show slightly stale data. This is normal for managed Prometheus.
7. **PromQL compatibility** -- Azure Managed Prometheus supports most PromQL functions but some advanced features (exemplars, native histograms) may have limitations.

## Production Backlog Items

- [ ] Link to Azure Managed Grafana with Monitoring Data Reader RBAC
- [ ] Configure data collection rules for custom metric scraping targets
- [ ] Set up recording rules for frequently-used PromQL aggregations
- [ ] Configure alert rules using Prometheus alert syntax
- [ ] Enable multi-cluster metric collection with appropriate labels
- [ ] Optimize metric cardinality to control storage costs
- [ ] Import community Grafana dashboards for common workloads
- [ ] Configure remote-write from self-hosted Prometheus if needed
