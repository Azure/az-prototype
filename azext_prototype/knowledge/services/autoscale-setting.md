---
service_namespace: Microsoft.Insights/autoscaleSettings
display_name: Autoscale Setting
---

# Autoscale Setting

> Automatic scaling configuration for Azure resources (App Service Plans, VM Scale Sets, Cloud Services) that adjusts instance count based on metrics, schedules, or both.

## When to Use
- **App Service Plan scaling** -- automatically add/remove instances based on CPU, memory, or HTTP queue length
- **VM Scale Set scaling** -- scale out/in based on CPU, memory, or custom metrics
- **Schedule-based scaling** -- pre-scale for known traffic patterns (business hours, weekends)
- **Cost optimization** -- scale down during off-peak to reduce costs, scale up during peak to maintain performance

Autoscale settings are separate resources that attach to a target resource. They are not embedded in the target resource itself.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Default instances | 1 | Minimum for POC |
| Min instances | 1 | Floor for scale-in |
| Max instances | 3 | Cap for scale-out in POC |
| Scale-out metric | CPU > 70% | 5-minute average |
| Scale-in metric | CPU < 30% | 5-minute average |
| Cooldown | 5 minutes | Prevents flapping |
| Enabled | true | Active by default |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "autoscale" {
  type      = "Microsoft.Insights/autoscaleSettings@2022-10-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      enabled           = true
      targetResourceUri = var.target_resource_id  # e.g., App Service Plan ID
      profiles = [
        {
          name = "default"
          capacity = {
            default = "1"
            minimum = "1"
            maximum = "3"
          }
          rules = [
            {
              metricTrigger = {
                metricName        = "CpuPercentage"
                metricResourceUri = var.target_resource_id
                timeGrain         = "PT1M"
                statistic         = "Average"
                timeWindow        = "PT5M"
                timeAggregation   = "Average"
                operator          = "GreaterThan"
                threshold         = 70
              }
              scaleAction = {
                direction = "Increase"
                type      = "ChangeCount"
                value     = "1"
                cooldown  = "PT5M"
              }
            },
            {
              metricTrigger = {
                metricName        = "CpuPercentage"
                metricResourceUri = var.target_resource_id
                timeGrain         = "PT1M"
                statistic         = "Average"
                timeWindow        = "PT5M"
                timeAggregation   = "Average"
                operator          = "LessThan"
                threshold         = 30
              }
              scaleAction = {
                direction = "Decrease"
                type      = "ChangeCount"
                value     = "1"
                cooldown  = "PT5M"
              }
            }
          ]
        }
      ]
      notifications = [
        {
          operation = "Scale"
          email = {
            sendToSubscriptionAdministrator    = true
            sendToSubscriptionCoAdministrators = false
            customEmails                       = var.notification_emails
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# Monitoring Contributor on the target resource for autoscale management
resource "azapi_resource" "monitoring_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.target_resource_id}-${var.principal_id}-monitoring-contributor")
  parent_id = var.target_resource_id

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
@description('Autoscale setting name')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Target resource ID (e.g., App Service Plan)')
param targetResourceId string

@description('Notification email addresses')
param notificationEmails array = []

param tags object = {}

resource autoscale 'Microsoft.Insights/autoscaleSettings@2022-10-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    enabled: true
    targetResourceUri: targetResourceId
    profiles: [
      {
        name: 'default'
        capacity: {
          default: '1'
          minimum: '1'
          maximum: '3'
        }
        rules: [
          {
            metricTrigger: {
              metricName: 'CpuPercentage'
              metricResourceUri: targetResourceId
              timeGrain: 'PT1M'
              statistic: 'Average'
              timeWindow: 'PT5M'
              timeAggregation: 'Average'
              operator: 'GreaterThan'
              threshold: 70
            }
            scaleAction: {
              direction: 'Increase'
              type: 'ChangeCount'
              value: '1'
              cooldown: 'PT5M'
            }
          }
          {
            metricTrigger: {
              metricName: 'CpuPercentage'
              metricResourceUri: targetResourceId
              timeGrain: 'PT1M'
              statistic: 'Average'
              timeWindow: 'PT5M'
              timeAggregation: 'Average'
              operator: 'LessThan'
              threshold: 30
            }
            scaleAction: {
              direction: 'Decrease'
              type: 'ChangeCount'
              value: '1'
              cooldown: 'PT5M'
            }
          }
        ]
      }
    ]
    notifications: [
      {
        operation: 'Scale'
        email: {
          sendToSubscriptionAdministrator: true
          sendToSubscriptionCoAdministrators: false
          customEmails: notificationEmails
        }
      }
    ]
  }
}

output id string = autoscale.id
```

## Application Code

### Python
Infrastructure -- transparent to application code. Autoscale manages the number of instances running your application; the application code itself does not need to be aware of scaling events.

### C#
Infrastructure -- transparent to application code. Autoscale manages the number of instances running your application; the application code itself does not need to be aware of scaling events.

### Node.js
Infrastructure -- transparent to application code. Autoscale manages the number of instances running your application; the application code itself does not need to be aware of scaling events.

## Common Pitfalls

1. **Capacity values are strings** -- `default`, `minimum`, and `maximum` in the capacity block must be strings (e.g., `"1"` not `1`). Numeric values cause deployment errors.
2. **Scale-in and scale-out thresholds** -- Ensure scale-out threshold (e.g., 70%) and scale-in threshold (e.g., 30%) have sufficient gap. Overlapping thresholds cause flapping.
3. **Cooldown too short** -- A cooldown under 5 minutes can cause rapid scaling oscillation. The default 5 minutes is the recommended minimum.
4. **Metric not available on target** -- The `metricName` must exist on the `metricResourceUri`. Using an App Insights metric against an App Service Plan ID fails silently.
5. **Multiple profiles conflict** -- When schedule-based profiles overlap, the first matching profile wins. Order profiles carefully.
6. **Location must match target** -- The autoscale setting must be in the same region as the target resource.
7. **Default profile required** -- At least one profile without a recurrence/schedule is required as the fallback. Omitting it causes unpredictable behavior outside scheduled windows.
8. **Notifications don't include webhooks by default** -- Email is simple but webhook notifications are more actionable. Add webhook URIs for integration with monitoring tools.

## Production Backlog Items

- [ ] Tune scale-out/scale-in thresholds based on observed traffic patterns
- [ ] Add schedule-based profiles for known peak/off-peak periods
- [ ] Configure webhook notifications for scaling events
- [ ] Add custom metrics (HTTP queue length, memory) alongside CPU
- [ ] Increase maximum instance count based on capacity requirements
- [ ] Set up Azure Monitor alerts for autoscale failures
- [ ] Test scale-out behavior under load to verify instance readiness time
- [ ] Implement predictive autoscale (preview) for proactive scaling
