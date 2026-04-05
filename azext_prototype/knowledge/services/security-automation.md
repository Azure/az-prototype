---
service_namespace: Microsoft.Security/automations
display_name: Defender for Cloud Automation
---

# Defender for Cloud Automation

> Workflow automation in Defender for Cloud that triggers Logic Apps in response to security alerts, recommendations, or regulatory compliance changes, enabling automated remediation and notification.

## When to Use
- **Automated alert response** -- trigger a Logic App when a high-severity security alert fires (e.g., notify Teams, create ServiceNow ticket)
- **Auto-remediation** -- automatically remediate common security findings (e.g., enable encryption, restrict NSG rules)
- **Compliance change tracking** -- trigger workflows when compliance assessment status changes
- **Export to SIEM** -- continuous export of alerts/recommendations to Event Hub for external SIEM integration

Automations bridge Defender for Cloud findings with Azure Logic Apps for customizable response workflows.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Scope | Resource group or subscription | Subscription for broad coverage |
| Source | Security alerts | Most actionable trigger for POC |
| Severity filter | High | Reduce noise |
| Logic App | Pre-built template | Use built-in templates for common scenarios |
| Status | Enabled | Active by default |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "security_automation" {
  type      = "Microsoft.Security/automations@2023-12-01-preview"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      isEnabled   = true
      description = var.description
      scopes = [
        {
          scopePath   = "/subscriptions/${var.subscription_id}"
          description = "Full subscription scope"
        }
      ]
      sources = [
        {
          eventSource = "Alerts"
          ruleSets = [
            {
              rules = [
                {
                  propertyJPath  = "Severity"
                  propertyType   = "String"
                  expectedValue  = "High"
                  operator       = "Equals"
                }
              ]
            }
          ]
        }
      ]
      actions = [
        {
          actionType = "LogicApp"
          logicAppResourceId = var.logic_app_id
          uri                = var.logic_app_trigger_uri  # HTTP trigger URL
        }
      ]
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# Security Admin on the subscription for managing automations
resource "azapi_resource" "security_admin" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.resource_group_id}-${var.principal_id}-security-admin")
  parent_id = var.resource_group_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/fb1c8493-542b-48eb-b624-b4c8fea62acd"
      principalId      = var.principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Logic Apps Contributor for the automation to invoke Logic Apps
resource "azapi_resource" "logic_app_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.logic_app_id}-${var.principal_id}-logic-contributor")
  parent_id = var.logic_app_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/87a39d53-fc1b-424a-814c-f7e04687dc9e"
      principalId      = var.principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Automation name')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Subscription ID for scope')
param subscriptionId string

@description('Logic App resource ID')
param logicAppId string

@description('Logic App HTTP trigger URI')
@secure()
param logicAppTriggerUri string

param tags object = {}

resource automation 'Microsoft.Security/automations@2023-12-01-preview' = {
  name: name
  location: location
  tags: tags
  properties: {
    isEnabled: true
    description: 'Automated response for high-severity security alerts'
    scopes: [
      {
        scopePath: '/subscriptions/${subscriptionId}'
        description: 'Full subscription scope'
      }
    ]
    sources: [
      {
        eventSource: 'Alerts'
        ruleSets: [
          {
            rules: [
              {
                propertyJPath: 'Severity'
                propertyType: 'String'
                expectedValue: 'High'
                operator: 'Equals'
              }
            ]
          }
        ]
      }
    ]
    actions: [
      {
        actionType: 'LogicApp'
        logicAppResourceId: logicAppId
        uri: logicAppTriggerUri
      }
    ]
  }
}

output id string = automation.id
```

## Application Code

### Python
Infrastructure -- transparent to application code. Security automations are triggered by Defender for Cloud events; applications are unaware of the automation workflows.

### C#
Infrastructure -- transparent to application code. Security automations are triggered by Defender for Cloud events; applications are unaware of the automation workflows.

### Node.js
Infrastructure -- transparent to application code. Security automations are triggered by Defender for Cloud events; applications are unaware of the automation workflows.

## Common Pitfalls

1. **Logic App trigger URI is a secret** -- The HTTP trigger URI contains a SAS token. Store it in Key Vault, not in plain text in IaC templates.
2. **Scope must be valid** -- The `scopePath` must be a valid subscription or resource group ID. Invalid paths cause the automation to never trigger.
3. **Rule operators are case-sensitive** -- `propertyType` values (`String`, `Integer`) and `operator` values (`Equals`, `Contains`) are case-sensitive.
4. **Logic App must have HTTP trigger** -- The target Logic App must have an HTTP Request trigger. Timer or Event Grid triggers won't work with security automations.
5. **Multiple sources create OR logic** -- Multiple items in `sources` are evaluated as OR. Multiple rules within a `ruleSet` are AND. Multiple `ruleSets` are OR.
6. **No built-in retry** -- If the Logic App is unavailable when an alert fires, the automation invocation is lost. Use Event Hub export for guaranteed delivery.
7. **Automation location matters** -- The automation resource must be in a supported region. Not all regions support security automations.

## Production Backlog Items

- [ ] Create automations for recommendation changes (e.g., auto-remediate common findings)
- [ ] Configure continuous export to Event Hub for SIEM integration
- [ ] Add compliance change automation for regulatory reporting
- [ ] Implement Logic App error handling and dead-letter queue
- [ ] Lower severity filter to Medium for broader automated response
- [ ] Document automation runbooks for the security operations team
- [ ] Set up monitoring for automation execution failures
