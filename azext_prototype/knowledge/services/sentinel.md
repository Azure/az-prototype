# Microsoft Sentinel
> Cloud-native SIEM and SOAR platform built on Log Analytics, providing intelligent security analytics, threat detection, and automated incident response across the enterprise.

## When to Use

- Centralized security monitoring and threat detection across Azure and hybrid environments
- Security incident investigation with AI-powered analytics
- Automated incident response using playbooks (Logic Apps)
- Compliance monitoring and security posture reporting
- Correlation of security signals across multiple data sources (Azure AD, firewalls, endpoints)
- NOT suitable for: operational monitoring (use Azure Monitor), application performance monitoring (use App Insights), or log storage only without security analytics (use Log Analytics alone)

**Prerequisite**: Microsoft Sentinel is deployed as a solution on top of an existing Log Analytics workspace. The workspace must exist before enabling Sentinel.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Log Analytics workspace | Existing workspace | Sentinel is enabled on a Log Analytics workspace |
| Data connectors | Azure Activity, Azure AD | Minimum for POC; expand for production |
| Analytics rules | Built-in templates | Enable 3-5 relevant detection rules for POC |
| Retention | 90 days free (Sentinel) | Sentinel adds 90 free days on top of workspace retention |
| UEBA | Disabled | Enable for production user behavior analytics |
| Data ingestion | Pay-as-you-go | Commitment tiers for production |

## Terraform Patterns

### Basic Resource (Enable Sentinel on Workspace)

```hcl
# Sentinel is deployed as an OperationsManagement solution on a Log Analytics workspace
resource "azapi_resource" "sentinel" {
  type      = "Microsoft.OperationsManagement/solutions@2015-11-01-preview"
  name      = "SecurityInsights(${var.workspace_name})"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      workspaceResourceId = var.workspace_id
    }
    plan = {
      name      = "SecurityInsights(${var.workspace_name})"
      publisher = "Microsoft"
      product   = "OMSGallery/SecurityInsights"
    }
  }

  tags = var.tags
}
```

### Onboarding (required after solution deployment)

```hcl
resource "azapi_resource" "sentinel_onboarding" {
  type      = "Microsoft.SecurityInsights/onboardingStates@2024-03-01"
  name      = "default"
  parent_id = var.workspace_id

  body = {
    properties = {
      customerManagedKey = false
    }
  }

  depends_on = [azapi_resource.sentinel]
}
```

### Data Connector (Azure Activity)

```hcl
resource "azapi_resource" "connector_azure_activity" {
  type      = "Microsoft.SecurityInsights/dataConnectors@2024-03-01"
  name      = var.connector_name
  parent_id = var.workspace_id

  body = {
    kind = "AzureActivity"
    properties = {
      linkedResourceId = "/subscriptions/${var.subscription_id}/providers/microsoft.insights/eventtypes/management"
    }
  }

  depends_on = [azapi_resource.sentinel_onboarding]
}
```

### Analytics Rule (Scheduled)

```hcl
resource "azapi_resource" "analytics_rule" {
  type      = "Microsoft.SecurityInsights/alertRules@2024-03-01"
  name      = var.rule_id
  parent_id = var.workspace_id

  body = {
    kind = "Scheduled"
    properties = {
      displayName        = var.rule_display_name
      description        = var.rule_description
      severity           = "High"
      enabled            = true
      query              = var.kql_query
      queryFrequency     = "PT5H"
      queryPeriod        = "PT6H"
      triggerOperator    = "GreaterThan"
      triggerThreshold   = 0
      suppressionEnabled = false
      tactics            = ["InitialAccess", "Persistence"]
      incidentConfiguration = {
        createIncident = true
        groupingConfiguration = {
          enabled              = true
          reopenClosedIncident = false
          lookbackDuration     = "PT5H"
          matchingMethod       = "AllEntities"
        }
      }
    }
  }

  depends_on = [azapi_resource.sentinel_onboarding]
}
```

### RBAC Assignment

```hcl
# Microsoft Sentinel Contributor -- manage Sentinel resources
resource "azapi_resource" "sentinel_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.workspace_id}${var.security_principal_id}sentinel-contributor")
  parent_id = var.workspace_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ab8e14d6-4a74-4a29-9ba8-549422addade"  # Microsoft Sentinel Contributor
      principalId      = var.security_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Microsoft Sentinel Reader -- read-only access for analysts
resource "azapi_resource" "sentinel_reader" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.workspace_id}${var.analyst_principal_id}sentinel-reader")
  parent_id = var.workspace_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/8d289c81-5878-46d4-8554-54e1e3d8b5cb"  # Microsoft Sentinel Reader
      principalId      = var.analyst_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
param workspaceName string
param workspaceId string
param location string
param tags object = {}

resource sentinel 'Microsoft.OperationsManagement/solutions@2015-11-01-preview' = {
  name: 'SecurityInsights(${workspaceName})'
  location: location
  tags: tags
  properties: {
    workspaceResourceId: workspaceId
  }
  plan: {
    name: 'SecurityInsights(${workspaceName})'
    publisher: 'Microsoft'
    product: 'OMSGallery/SecurityInsights'
  }
}

output id string = sentinel.id
output name string = sentinel.name
```

### Analytics Rule

```bicep
param workspaceId string
param ruleId string
param displayName string
param kqlQuery string
param severity string = 'High'

resource analyticsRule 'Microsoft.SecurityInsights/alertRules@2024-03-01' = {
  name: ruleId
  scope: workspace
  kind: 'Scheduled'
  properties: {
    displayName: displayName
    description: 'Scheduled analytics rule'
    severity: severity
    enabled: true
    query: kqlQuery
    queryFrequency: 'PT5H'
    queryPeriod: 'PT6H'
    triggerOperator: 'GreaterThan'
    triggerThreshold: 0
    suppressionEnabled: false
    incidentConfiguration: {
      createIncident: true
      groupingConfiguration: {
        enabled: true
        reopenClosedIncident: false
        lookbackDuration: 'PT5H'
        matchingMethod: 'AllEntities'
      }
    }
  }
}
```

### RBAC Assignment

```bicep
param principalId string

// Microsoft Sentinel Contributor
resource sentinelContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(workspaceId, principalId, 'ab8e14d6-4a74-4a29-9ba8-549422addade')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ab8e14d6-4a74-4a29-9ba8-549422addade')
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Enabling too many data connectors at once | Unexpectedly high ingestion costs | Start with 2-3 connectors for POC; monitor costs before adding more |
| Not enabling Sentinel onboarding state | Some features fail silently | Always deploy `onboardingStates/default` after the solution |
| Solution naming convention wrong | Deployment fails | Name must be exactly `SecurityInsights(<workspace-name>)` |
| Ignoring analytics rule tuning | Alert fatigue from false positives | Start with built-in templates, tune thresholds based on environment |
| Not connecting data sources | Sentinel has no data to analyze | Enable at least Azure Activity and Azure AD connectors |
| Confusing Sentinel roles with Log Analytics roles | Wrong permissions; analysts cannot manage incidents | Use Sentinel-specific roles (Sentinel Contributor/Reader/Responder) |
| Running expensive KQL queries | Slow queries, high compute costs | Optimize queries; use summarize and time filters |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Additional data connectors | P1 | Connect Microsoft 365, Azure AD Identity Protection, Defender for Cloud |
| UEBA (User Entity Behavior Analytics) | P2 | Enable UEBA for anomaly detection on user and entity behavior |
| Automation playbooks | P1 | Create Logic App playbooks for automated incident response |
| Custom analytics rules | P2 | Develop organization-specific detection rules beyond built-in templates |
| Workbooks and dashboards | P3 | Create custom workbooks for security posture visualization |
| Threat intelligence integration | P2 | Connect threat intelligence feeds for IOC matching |
| SOC process documentation | P3 | Document incident triage and response procedures |
| Commitment tier pricing | P3 | Evaluate and switch to commitment tier based on ingestion volume |
| Multi-workspace architecture | P4 | Design workspace architecture for multi-tenant or multi-region |
| Watchlists | P3 | Create watchlists for IP allowlists, VIP users, and known-good entities |
