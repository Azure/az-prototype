---
service_namespace: Microsoft.SecurityInsights/alertRules
display_name: Sentinel Alert Rule
depends_on:
  - Microsoft.SecurityInsights/settings
---

# Sentinel Alert Rule

> Detection rule in Microsoft Sentinel that runs KQL queries on a schedule (or uses ML/fusion) to detect threats and generate security incidents from log data.

## When to Use
- **Scheduled detection** -- periodic KQL query that triggers alerts when results match conditions (most common type)
- **Microsoft Security (fusion)** -- aggregates alerts from Microsoft Defender products into Sentinel incidents
- **ML Behavior Analytics** -- built-in ML models for anomaly detection (e.g., anomalous Azure AD sign-in)
- **NRT (Near Real-Time)** -- queries run every minute for time-sensitive detections (latency-critical threats)
- **Threat Intelligence** -- matches TI indicators against log data automatically

Start with built-in rule templates for POC. Custom rules are added as you understand your environment's baseline behavior.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Kind | Scheduled | Most flexible; KQL-based |
| Frequency | PT5H | Every 5 hours for POC |
| Lookback period | PT6H | 1 hour overlap for completeness |
| Severity | High or Medium | Tune to avoid alert fatigue |
| Incident creation | Enabled | Group related alerts into incidents |
| Suppression | Disabled | Enable when tuning false positives |
| Entity mapping | Recommended | Map Account, Host, IP for investigation |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "alert_rule" {
  type      = "Microsoft.SecurityInsights/alertRules@2024-03-01"
  name      = var.rule_id  # GUID
  parent_id = var.workspace_id

  body = {
    kind = "Scheduled"
    properties = {
      displayName           = var.display_name
      description           = var.description
      severity              = "High"
      enabled               = true
      query                 = var.kql_query
      queryFrequency        = "PT5H"
      queryPeriod           = "PT6H"
      triggerOperator       = "GreaterThan"
      triggerThreshold      = 0
      suppressionEnabled    = false
      suppressionDuration   = "PT1H"
      tactics               = var.tactics  # e.g., ["InitialAccess", "Persistence"]
      techniques            = var.techniques  # e.g., ["T1078"]
      entityMappings = [
        {
          entityType = "Account"
          fieldMappings = [
            {
              identifier = "FullName"
              columnName = "AccountCustomEntity"
            }
          ]
        },
        {
          entityType = "IP"
          fieldMappings = [
            {
              identifier = "Address"
              columnName = "IPCustomEntity"
            }
          ]
        }
      ]
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
# Microsoft Sentinel Contributor (ab8e14d6-4a74-4a29-9ba8-549422addade) on the workspace
# Required for creating and managing alert rules.
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Rule GUID')
param ruleId string = newGuid()

@description('Display name of the rule')
param displayName string

@description('KQL query for the rule')
param kqlQuery string

@description('Severity of generated alerts')
@allowed(['High', 'Medium', 'Low', 'Informational'])
param severity string = 'High'

@description('MITRE ATT&CK tactics')
param tactics array = []

resource alertRule 'Microsoft.SecurityInsights/alertRules@2024-03-01' = {
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
    suppressionDuration: 'PT1H'
    tactics: tactics
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

## Application Code

### Python
Infrastructure -- transparent to application code. Alert rules are security detection configurations that run within Sentinel; applications do not interact with them directly.

### C#
Infrastructure -- transparent to application code. Alert rules are security detection configurations that run within Sentinel; applications do not interact with them directly.

### Node.js
Infrastructure -- transparent to application code. Alert rules are security detection configurations that run within Sentinel; applications do not interact with them directly.

## Common Pitfalls

1. **Query period must be >= query frequency** -- If `queryFrequency` is PT5H but `queryPeriod` is PT4H, you have a 1-hour gap where events are never evaluated. Period should exceed frequency.
2. **Rule name must be a GUID** -- The `name` property must be a valid GUID format. Human-readable names go in `displayName`.
3. **Entity mapping column names must exist in query output** -- If the KQL query doesn't project a column named in `entityMappings`, the mapping fails silently and investigations lack entity context.
4. **Sentinel must be onboarded first** -- Alert rules deployed before `onboardingStates/default` may fail or behave unpredictably. Always add `depends_on`.
5. **Suppression hides duplicate alerts** -- When `suppressionEnabled: true`, matching alerts within the suppression window are dropped. Over-suppression can mask real incidents.
6. **MITRE ATT&CK mapping** -- `tactics` and `techniques` must use exact enum values (e.g., `InitialAccess` not `Initial Access`). Invalid values cause deployment errors.
7. **High-frequency rules increase cost** -- NRT and short-frequency scheduled rules increase query compute costs. Balance detection speed with Log Analytics costs.

## Production Backlog Items

- [ ] Enable built-in analytics rule templates relevant to the environment
- [ ] Add entity mappings (Account, Host, IP, URL) for investigation enrichment
- [ ] Map rules to MITRE ATT&CK tactics and techniques for coverage analysis
- [ ] Configure alert grouping to reduce incident volume
- [ ] Tune thresholds and suppression based on false positive analysis
- [ ] Create custom rules for organization-specific threat scenarios
- [ ] Set up automation rules to auto-assign incidents to analysts
- [ ] Implement rule health monitoring for failed or degraded rules
