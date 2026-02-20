# Application Insights
> Application performance monitoring (APM) service that provides deep observability into application behavior, including request tracing, dependency tracking, exception logging, and live metrics.

## When to Use

- Monitoring web application performance (request rates, response times, failure rates)
- Distributed tracing across microservices
- Exception and error tracking with stack traces
- Custom metrics and event tracking for business telemetry
- Dependency tracking (database calls, HTTP requests, external service calls)
- Availability monitoring with URL ping tests
- NOT suitable for: infrastructure-only monitoring without application code (use Azure Monitor metrics), log-only scenarios (use Log Analytics directly), or high-volume IoT telemetry (use IoT Hub + Time Series Insights)

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Type | Workspace-based | Classic (standalone) is deprecated |
| Log Analytics workspace | Required | Must reference an existing workspace |
| Application type | web | Default for most scenarios |
| Sampling | Default (adaptive) | Reduces volume automatically |
| Retention | Inherited from workspace | 30 days default |

**CRITICAL**: Workspace-based Application Insights requires a `workspace_id` parameter pointing to an existing Log Analytics workspace. Always create the Log Analytics workspace first.

**Connection string is NOT a secret**: The Application Insights connection string contains the instrumentation key and ingestion endpoint. It is safe to include in application configuration, environment variables, and source code. It does not grant access to read telemetry data.

## Terraform Patterns

### Basic Resource

```hcl
resource "azurerm_application_insights" "this" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  workspace_id        = var.log_analytics_workspace_id  # REQUIRED for workspace-based
  application_type    = "web"

  tags = var.tags
}
```

### With Connection String Output

```hcl
output "id" {
  description = "Application Insights resource ID"
  value       = azurerm_application_insights.this.id
}

output "instrumentation_key" {
  description = "Instrumentation key (not a secret)"
  value       = azurerm_application_insights.this.instrumentation_key
}

output "connection_string" {
  description = "Connection string for SDK configuration (not a secret)"
  value       = azurerm_application_insights.this.connection_string
}

output "app_id" {
  description = "Application Insights application ID (for API queries)"
  value       = azurerm_application_insights.this.app_id
}
```

### Injecting into App Service / Functions

```hcl
# Pass connection string to App Service via app_settings
resource "azurerm_linux_web_app" "this" {
  # ... other config ...

  app_settings = {
    "APPLICATIONINSIGHTS_CONNECTION_STRING" = azurerm_application_insights.this.connection_string
    "ApplicationInsightsAgent_EXTENSION_VERSION" = "~3"  # Auto-instrumentation for .NET
  }
}

# Pass connection string to Function App via app_settings
resource "azurerm_linux_function_app" "this" {
  # ... other config ...

  app_settings = {
    "APPLICATIONINSIGHTS_CONNECTION_STRING" = azurerm_application_insights.this.connection_string
    "APPINSIGHTS_INSTRUMENTATIONKEY"        = azurerm_application_insights.this.instrumentation_key
  }
}
```

### RBAC Assignment

```hcl
# Grant read access to telemetry data
resource "azurerm_role_assignment" "reader" {
  scope                = azurerm_application_insights.this.id
  role_definition_name = "Application Insights Component Reader"
  principal_id         = var.reader_principal_id
}

# Grant contributor access for managing settings
resource "azurerm_role_assignment" "contributor" {
  scope                = azurerm_application_insights.this.id
  role_definition_name = "Application Insights Component Contributor"
  principal_id         = var.admin_principal_id
}
```

### Private Endpoint

```hcl
# Application Insights does NOT have its own private endpoint.
# Private access is achieved via Azure Monitor Private Link Scope (AMPLS),
# which is shared with Log Analytics.
# See log-analytics.md for the AMPLS pattern.
# For POC, public ingestion endpoints are acceptable.
```

## Bicep Patterns

### Basic Resource

```bicep
param name string
param location string
param logAnalyticsWorkspaceId string
param tags object = {}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: name
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalyticsWorkspaceId
    IngestionMode: 'LogAnalytics'
  }
  tags: tags
}

output id string = appInsights.id
output connectionString string = appInsights.properties.ConnectionString
output instrumentationKey string = appInsights.properties.InstrumentationKey
```

### RBAC Assignment

```bicep
param principalId string

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

// Application Insights Component Reader
resource readerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appInsights.id, principalId, 'reader')
  scope: appInsights
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'aa49f09b-42d2-4ee6-8548-4c9c6fd4acbb')
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Application Code

### Python (OpenTelemetry -- recommended for new apps)

```python
import os
from azure.monitor.opentelemetry import configure_azure_monitor

# Configure once at application startup
configure_azure_monitor(
    connection_string=os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"),
)

# After configuration, use standard OpenTelemetry APIs
from opentelemetry import trace, metrics

tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)

request_counter = meter.create_counter("app.requests", description="Total requests")

def handle_request():
    with tracer.start_as_current_span("handle_request") as span:
        span.set_attribute("custom.attribute", "value")
        request_counter.add(1)
        # ... application logic ...
```

### Python (opencensus -- legacy, for existing apps)

```python
import os
from opencensus.ext.azure import metrics_exporter
from opencensus.ext.azure.trace_exporter import AzureExporter
from opencensus.trace.samplers import ProbabilitySampler
from opencensus.trace.tracer import Tracer

connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")

tracer = Tracer(
    exporter=AzureExporter(connection_string=connection_string),
    sampler=ProbabilitySampler(1.0),
)

# For Flask
from opencensus.ext.flask.flask_middleware import FlaskMiddleware
FlaskMiddleware(app, exporter=AzureExporter(connection_string=connection_string))
```

### C# (ASP.NET Core -- OpenTelemetry recommended)

```csharp
using Azure.Monitor.OpenTelemetry.AspNetCore;

var builder = WebApplication.CreateBuilder(args);

// Option 1: OpenTelemetry (recommended for new apps)
builder.Services.AddOpenTelemetry().UseAzureMonitor(options =>
{
    options.ConnectionString = builder.Configuration["APPLICATIONINSIGHTS_CONNECTION_STRING"];
});

// Option 2: Classic Application Insights SDK
// builder.Services.AddApplicationInsightsTelemetry(options =>
// {
//     options.ConnectionString = builder.Configuration["APPLICATIONINSIGHTS_CONNECTION_STRING"];
// });

var app = builder.Build();
app.MapGet("/", () => "Hello World");
app.Run();
```

### C# (Custom Telemetry)

```csharp
using Microsoft.ApplicationInsights;
using Microsoft.ApplicationInsights.DataContracts;

public class MyService
{
    private readonly TelemetryClient _telemetry;

    public MyService(TelemetryClient telemetry)
    {
        _telemetry = telemetry;
    }

    public void ProcessOrder(string orderId)
    {
        _telemetry.TrackEvent("OrderProcessed", new Dictionary<string, string>
        {
            ["OrderId"] = orderId
        });

        _telemetry.GetMetric("OrdersProcessed").TrackValue(1);
    }
}
```

### Node.js (OpenTelemetry -- recommended)

```javascript
const { useAzureMonitor } = require("@azure/monitor-opentelemetry");

// Configure at application entry point (before other imports)
useAzureMonitor({
  azureMonitorExporterOptions: {
    connectionString: process.env.APPLICATIONINSIGHTS_CONNECTION_STRING,
  },
});

// After configuration, use standard OpenTelemetry APIs
const { trace, metrics } = require("@opentelemetry/api");

const tracer = trace.getTracer("my-app");
const meter = metrics.getMeter("my-app");
const requestCounter = meter.createCounter("app.requests");

function handleRequest(req, res) {
  const span = tracer.startSpan("handleRequest");
  requestCounter.add(1);
  // ... application logic ...
  span.end();
}
```

### Node.js (Classic SDK -- legacy)

```javascript
const appInsights = require("applicationinsights");

appInsights
  .setup(process.env.APPLICATIONINSIGHTS_CONNECTION_STRING)
  .setAutoCollectRequests(true)
  .setAutoCollectPerformance(true)
  .setAutoCollectExceptions(true)
  .setAutoCollectDependencies(true)
  .start();

const client = appInsights.defaultClient;

// Custom events
client.trackEvent({ name: "OrderProcessed", properties: { orderId: "123" } });

// Custom metrics
client.trackMetric({ name: "OrderValue", value: 99.99 });
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Creating standalone (classic) App Insights | Classic mode is deprecated; no workspace integration | Always set `workspace_id` (Terraform) or `WorkspaceResourceId` (Bicep) |
| Missing Log Analytics workspace dependency | Deployment fails | Create Log Analytics workspace first; reference its ID |
| Treating connection string as a secret | Unnecessary complexity in secret management | Connection string is NOT a secret -- safe in app settings and environment variables |
| Not configuring sampling | High telemetry volume and unexpected costs | Use adaptive sampling (default) or configure fixed-rate sampling |
| Confusing instrumentation key vs connection string | SDK misconfiguration | Use connection string (newer, includes endpoint); instrumentation key is legacy |
| Auto-instrumentation not enabled | Missing telemetry for .NET apps on App Service | Set `ApplicationInsightsAgent_EXTENSION_VERSION` to `~3` in app settings |
| Multiple App Insights instances for one app | Fragmented telemetry, broken distributed tracing | Use a single App Insights resource per application (microservice) |
| Not linking App Insights to the correct workspace | Logs go to wrong workspace | Verify `workspace_id` points to the shared workspace |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Sampling configuration | P3 | Tune sampling rates to balance cost and observability (fixed-rate or adaptive) |
| Custom metrics | P3 | Implement custom business metrics for dashboards and alerting |
| Availability tests | P3 | Configure URL ping tests or multi-step web tests for uptime monitoring |
| Smart detection alerts | P3 | Review and configure smart detection rules for anomaly alerting |
| Application Map review | P4 | Verify distributed tracing connections appear correctly in Application Map |
| Live Metrics authorization | P3 | Configure authenticated access for Live Metrics Stream |
| Azure Monitor Private Link Scope | P1 | Route telemetry ingestion through AMPLS for private network environments |
| Continuous export / data export | P4 | Configure diagnostic settings for long-term telemetry archival |
| Workbooks and dashboards | P3 | Create custom Azure Monitor Workbooks for operational visibility |
| Cost optimization | P3 | Review daily data volume and configure daily cap if needed |
