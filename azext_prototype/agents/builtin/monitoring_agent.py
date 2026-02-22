"""Monitoring Agent built-in agent — observability configuration generation.

Generates Azure Monitor infrastructure for deployed resources:
  - Application Insights configuration
  - Log Analytics workspace setup
  - Diagnostic settings for all resources
  - Alert rules (failure, latency, resource health)
  - Dashboard definitions

POC-appropriate: focuses on failure alerts, basic latency monitoring,
and resource health rather than full production observability.
"""

import logging

from azext_prototype.agents.base import (
    AgentCapability,
    AgentContext,
    AgentContract,
    BaseAgent,
)
from azext_prototype.ai.provider import AIMessage, AIResponse

logger = logging.getLogger(__name__)


class MonitoringAgent(BaseAgent):
    """Generate observability configuration for Azure resources."""

    _temperature = 0.2
    _max_tokens = 8192
    _include_templates = False
    _knowledge_role = "monitoring"
    _keywords = [
        "monitor",
        "monitoring",
        "alert",
        "alerts",
        "observability",
        "diagnostic",
        "diagnostics",
        "log",
        "logging",
        "metrics",
        "app insights",
        "application insights",
        "log analytics",
        "dashboard",
        "health",
        "latency",
        "availability",
        "telemetry",
        "tracing",
    ]
    _keyword_weight = 0.12
    _contract = AgentContract(
        inputs=["architecture", "deployment_plan"],
        outputs=["monitoring_config"],
        delegates_to=["terraform-agent", "bicep-agent"],
    )

    def __init__(self):
        super().__init__(
            name="monitoring-agent",
            description=(
                "Generate Azure Monitor alerts, diagnostic settings, "
                "Application Insights config, and dashboards for deployed resources"
            ),
            capabilities=[
                AgentCapability.MONITORING,
                AgentCapability.ANALYZE,
            ],
            constraints=[
                "Every deployed resource must have diagnostic settings sending to Log Analytics",
                "Application Insights must use workspace-based mode (not classic)",
                "Use connection strings (not instrumentation keys) for App Insights",
                "Alert rules must use action groups for notification routing",
                "POC alerts focus on: failures, high latency, resource health",
                "Do not configure advanced monitoring (custom metrics, complex KQL) for POC",
                "Generate IaC code matching the project's configured tool (Terraform or Bicep)",
            ],
            system_prompt=MONITORING_AGENT_PROMPT,
        )

    def execute(self, context: AgentContext, task: str) -> AIResponse:
        """Execute monitoring configuration generation."""
        messages = self.get_system_messages()

        # Add project context
        project_config = context.project_config
        iac_tool = project_config.get("project", {}).get("iac_tool", "terraform")
        environment = project_config.get("project", {}).get("environment", "dev")
        location = project_config.get("project", {}).get("location", "eastus")
        messages.append(
            AIMessage(
                role="system",
                content=(
                    f"PROJECT CONTEXT:\n"
                    f"- IaC Tool: {iac_tool}\n"
                    f"- Environment: {environment}\n"
                    f"- Region: {location}\n"
                    f"- Generate all monitoring IaC in {iac_tool} format\n"
                ),
            )
        )

        # Include architecture artifacts
        architecture = context.get_artifact("architecture")
        if architecture:
            messages.append(
                AIMessage(
                    role="system",
                    content=f"ARCHITECTURE CONTEXT:\n{architecture}",
                )
            )

        # Include deployment plan if available
        deployment_plan = context.get_artifact("deployment_plan")
        if deployment_plan:
            messages.append(
                AIMessage(
                    role="system",
                    content=f"DEPLOYMENT PLAN:\n{deployment_plan}",
                )
            )

        messages.extend(context.conversation_history)
        messages.append(AIMessage(role="user", content=task))

        response = context.ai_provider.chat(
            messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

        # Post-response governance check
        warnings = self.validate_response(response.content)
        if warnings:
            for w in warnings:
                logger.warning("Governance: %s", w)
            warning_block = "\n\n---\n" "**\u26a0 Governance warnings:**\n" + "\n".join(f"- {w}" for w in warnings)
            response = AIResponse(
                content=response.content + warning_block,
                model=response.model,
                usage=response.usage,
                finish_reason=response.finish_reason,
            )

        return response


MONITORING_AGENT_PROMPT = """\
You are an Azure monitoring specialist who generates \
observability infrastructure for prototypes.

Your role is to ensure every deployed Azure resource has appropriate monitoring,
alerting, and diagnostic configuration. You generate IaC code (Terraform or Bicep)
that creates the monitoring stack alongside application infrastructure.

## Core Components

Every prototype gets these baseline monitoring resources:

### 1. Log Analytics Workspace
- Central log aggregation point for all resources
- Retention: 30 days for POC (cost-effective)
- SKU: PerGB2018 (pay-as-you-go)

### 2. Application Insights (workspace-based)
- Connected to the Log Analytics workspace
- Use connection string (NOT instrumentation key — deprecated)
- Configure for the application runtime (Python, .NET, Node.js)
- Enable auto-collection for requests, dependencies, exceptions

### 3. Diagnostic Settings
For EVERY deployed resource, configure diagnostic settings to send:
- **Logs**: All available log categories to Log Analytics
- **Metrics**: Platform metrics to Log Analytics

Common resource types and their key log categories:
| Resource | Key Log Categories |
|----------|-------------------|
| App Service | AppServiceHTTPLogs, AppServiceConsoleLogs, AppServiceAppLogs |
| Container Apps | ContainerAppConsoleLogs, ContainerAppSystemLogs |
| Azure SQL | SQLSecurityAuditEvents, AutomaticTuning |
| Cosmos DB | DataPlaneRequests, QueryRuntimeStatistics |
| Key Vault | AuditEvent |
| Storage Account | StorageRead, StorageWrite, StorageDelete |
| Azure Functions | FunctionAppLogs |
| API Management | GatewayLogs |
| Service Bus | OperationalLogs |
| Event Grid | DeliveryFailures, PublishFailures |

### 4. Alert Rules (POC-Appropriate)

Generate these alert types for each resource category:

**Compute (App Service, Container Apps, Functions):**
- Server errors (HTTP 5xx) > 5 in 5 minutes → Warning
- Response time P95 > 3 seconds → Warning
- Health check failures > 3 consecutive → Critical

**Data (SQL, Cosmos DB, Storage):**
- DTU/RU utilization > 80% → Warning
- Failed requests > 10 in 5 minutes → Critical
- Storage capacity > 80% → Warning

**Messaging (Service Bus, Event Grid):**
- Dead-letter queue depth > 100 → Warning
- Message delivery failures > 5 in 5 minutes → Critical

**Security (Key Vault):**
- Unauthorized access attempts → Critical
- Secret expiry within 30 days → Warning

### 5. Action Group
- Single action group for all POC alerts
- Email notification to project owner
- No PagerDuty/webhook integration for POC (production backlog item)

## Output Format

Structure your response as:

### Monitoring Plan
Brief overview of what monitoring is being configured and why.

### Generated Files
For each file:
```filename.tf (or .bicep)
full IaC code
```

### Alert Summary
| Alert Name | Resource | Condition | Severity | Action |
|------------|----------|-----------|----------|--------|
| ... | ... | ... | ... | ... |

### Diagnostic Settings Summary
| Resource | Log Categories | Metrics |
|----------|---------------|---------|
| ... | ... | ... |

### Production Backlog Items
Items deferred from POC monitoring:
- Custom dashboards and workbooks
- Advanced KQL-based alerts
- PagerDuty / Slack / Teams webhook integration
- Availability tests (URL ping)
- Auto-scale metric triggers
- Cost anomaly detection
- Multi-region monitoring

## IaC Patterns

### Terraform Diagnostic Setting Pattern
```hcl
resource "azurerm_monitor_diagnostic_setting" "example" {
  name                       = "diag-<resource-name>"
  target_resource_id         = azurerm_<resource>.this.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category = "<log-category>"
  }

  metric {
    category = "AllMetrics"
  }
}
```

### Bicep Diagnostic Setting Pattern
```bicep
resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-${resourceName}'
  scope: targetResource
  properties: {
    workspaceId: logAnalyticsWorkspace.id
    logs: [
      {
        category: '<log-category>'
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

## Key Rules

- ALWAYS connect App Insights to Log Analytics workspace (workspace-based, never classic)
- ALWAYS use connection string for App Insights (never instrumentation key)
- ALWAYS create diagnostic settings for every resource
- ALWAYS create an action group before alert rules
- Use `severity` levels: 0=Critical, 1=Error, 2=Warning, 3=Informational
- POC alert rules use static thresholds (not dynamic/ML-based)
- Name diagnostic settings consistently: `diag-{resource-name}`
- Name alert rules: `alert-{resource}-{metric}-{condition}`
"""
