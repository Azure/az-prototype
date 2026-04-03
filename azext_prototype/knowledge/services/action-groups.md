# Azure Monitor Action Groups
> Reusable notification and automation targets for Azure Monitor alerts, enabling email, SMS, webhook, Logic App, Azure Function, and ITSM integrations when alerts fire.

## When to Use

- Defining notification targets (email, SMS, push) for Azure Monitor alert rules
- Triggering automated remediation via Azure Functions, Logic Apps, or webhooks on alert
- Centralizing alert routing so multiple alert rules share the same notification configuration
- ITSM integration for incident creation in ServiceNow, PagerDuty, etc.
- NOT suitable for: complex event processing (use Event Grid or Logic Apps), data ingestion (use Event Hubs), or scheduled tasks (use Azure Automation)

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Email receivers | 1-2 team emails | Sufficient for POC alerting |
| SMS receivers | None | Add for production on-call |
| Short name | 12 chars max | Required; displayed in notifications |
| Enabled | true | Action group must be enabled to fire |
| ARM role receivers | None | Use for production to notify by Azure role |

**Foundation service**: Action Groups are typically created alongside the monitoring stack (Log Analytics, App Insights) and referenced by all alert rules across the deployment.

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "action_group" {
  type      = "Microsoft.Insights/actionGroups@2023-09-01-preview"
  name      = var.name
  location  = "global"
  parent_id = var.resource_group_id

  body = {
    properties = {
      groupShortName = var.short_name  # Max 12 characters
      enabled        = true
      emailReceivers = [
        {
          name                 = "team-email"
          emailAddress         = var.email_address
          useCommonAlertSchema = true
        }
      ]
    }
  }

  tags = var.tags
}
```

### With Webhook Receiver

```hcl
resource "azapi_resource" "action_group_webhook" {
  type      = "Microsoft.Insights/actionGroups@2023-09-01-preview"
  name      = var.name
  location  = "global"
  parent_id = var.resource_group_id

  body = {
    properties = {
      groupShortName = var.short_name
      enabled        = true
      emailReceivers = [
        {
          name                 = "team-email"
          emailAddress         = var.email_address
          useCommonAlertSchema = true
        }
      ]
      webhookReceivers = [
        {
          name                 = "ops-webhook"
          serviceUri           = var.webhook_uri
          useCommonAlertSchema = true
          useAadAuth           = true
          objectId             = var.webhook_aad_object_id
          tenantId             = var.tenant_id
        }
      ]
    }
  }

  tags = var.tags
}
```

### With Azure Function Receiver

```hcl
resource "azapi_resource" "action_group_function" {
  type      = "Microsoft.Insights/actionGroups@2023-09-01-preview"
  name      = var.name
  location  = "global"
  parent_id = var.resource_group_id

  body = {
    properties = {
      groupShortName = var.short_name
      enabled        = true
      azureFunctionReceivers = [
        {
          name                  = "remediation-function"
          functionAppResourceId = var.function_app_id
          functionName          = var.function_name
          httpTriggerUrl        = var.function_trigger_url
          useCommonAlertSchema  = true
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
param shortName string
param emailAddress string
param tags object = {}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-09-01-preview' = {
  name: name
  location: 'global'
  tags: tags
  properties: {
    groupShortName: shortName
    enabled: true
    emailReceivers: [
      {
        name: 'team-email'
        emailAddress: emailAddress
        useCommonAlertSchema: true
      }
    ]
  }
}

output id string = actionGroup.id
output name string = actionGroup.name
```

### With Webhook Receiver

```bicep
param name string
param shortName string
param emailAddress string
param webhookUri string
param tags object = {}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-09-01-preview' = {
  name: name
  location: 'global'
  tags: tags
  properties: {
    groupShortName: shortName
    enabled: true
    emailReceivers: [
      {
        name: 'team-email'
        emailAddress: emailAddress
        useCommonAlertSchema: true
      }
    ]
    webhookReceivers: [
      {
        name: 'ops-webhook'
        serviceUri: webhookUri
        useCommonAlertSchema: true
      }
    ]
  }
}

output id string = actionGroup.id
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Short name exceeds 12 characters | Deployment fails with validation error | Keep `groupShortName` to 12 characters or fewer |
| Not enabling common alert schema | Inconsistent payload formats across alert types | Set `useCommonAlertSchema = true` on all receivers |
| Too many email receivers | Alert fatigue, emails ignored | Use 1-2 emails for POC; use ARM role receivers for production |
| Forgetting to link action group to alert rules | Action group exists but never fires | Always reference the action group ID in metric/log alert rules |
| Not testing action group | Notifications may fail silently (bad email, expired webhook) | Use the "Test" feature in the portal after deployment |
| Using HTTP webhook without AAD auth | Webhook endpoint exposed to unauthenticated callers | Enable `useAadAuth` on webhook receivers in production |
| Location not set to "global" | Deployment may fail or behave unexpectedly | Action Groups are global resources; always use `location = "global"` |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| SMS/voice receivers | P3 | Add SMS and voice call receivers for on-call escalation |
| ARM role receivers | P2 | Notify by Azure AD role (e.g., Owner, Contributor) instead of individual emails |
| Logic App integration | P3 | Connect to Logic Apps for complex notification workflows (Teams, Slack) |
| ITSM connector | P2 | Integrate with ServiceNow/PagerDuty for automated incident creation |
| Rate limiting awareness | P3 | Document and plan around action group rate limits (max 1 SMS/voice per 5 min per number) |
| Suppression rules | P3 | Configure alert processing rules to suppress notifications during maintenance windows |
| Secure webhook with AAD | P1 | Enable AAD authentication on all webhook receivers |
| Multiple action groups | P3 | Create separate action groups for severity levels (critical vs. warning) |
