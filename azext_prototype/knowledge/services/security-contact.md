---
service_namespace: Microsoft.Security/securityContacts
display_name: Defender for Cloud Security Contact
---

# Defender for Cloud Security Contact

> Subscription-level configuration that defines email recipients and notification preferences for Microsoft Defender for Cloud security alerts and recommendations.

## When to Use
- **Security alert notifications** -- receive email when Defender for Cloud generates high-severity alerts
- **Compliance requirement** -- many frameworks (SOC 2, ISO 27001) require designated security contacts
- **Subscription governance** -- ensure security team is notified of threats even if they don't check the portal
- Configure once per subscription as part of the security baseline

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Name | `default` | Must be `default` |
| Email | Security team DL | Use distribution list, not personal email |
| Phone | Optional | For high-severity escalation |
| Alert notifications | Enabled | High severity alerts |
| Notify admins | Enabled | Also notify subscription owners/contributors |
| Min severity | High | Reduce noise during POC |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "security_contact" {
  type      = "Microsoft.Security/securityContacts@2023-12-01-preview"
  name      = "default"
  parent_id = "/subscriptions/${var.subscription_id}"

  body = {
    properties = {
      emails               = var.security_email  # e.g., "security@contoso.com"
      phone                = var.security_phone   # Optional
      isEnabled            = true
      notificationsByRole = {
        state = "On"
        roles = ["Owner", "Contributor", "ServiceAdmin"]
      }
      notificationsSources = [
        {
          sourceType       = "Alert"
          minimalSeverity  = "High"
        },
        {
          sourceType       = "AttackPath"
          minimalRiskLevel = "High"
        }
      ]
    }
  }
}
```

### RBAC Assignment

```hcl
# Security Admin on the subscription for managing security contacts
resource "azapi_resource" "security_admin" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "/subscriptions/${var.subscription_id}-${var.principal_id}-security-admin")
  parent_id = "/subscriptions/${var.subscription_id}"

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/fb1c8493-542b-48eb-b624-b4c8fea62acd"
      principalId      = var.principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
targetScope = 'subscription'

@description('Security contact email address')
param securityEmail string

@description('Security contact phone number')
param securityPhone string = ''

resource securityContact 'Microsoft.Security/securityContacts@2023-12-01-preview' = {
  name: 'default'
  properties: {
    emails: securityEmail
    phone: securityPhone
    isEnabled: true
    notificationsByRole: {
      state: 'On'
      roles: ['Owner', 'Contributor', 'ServiceAdmin']
    }
    notificationsSources: [
      {
        sourceType: 'Alert'
        minimalSeverity: 'High'
      }
      {
        sourceType: 'AttackPath'
        minimalRiskLevel: 'High'
      }
    ]
  }
}
```

## Application Code

### Python
Infrastructure -- transparent to application code. Security contacts are a subscription-level governance configuration; applications do not interact with them.

### C#
Infrastructure -- transparent to application code. Security contacts are a subscription-level governance configuration; applications do not interact with them.

### Node.js
Infrastructure -- transparent to application code. Security contacts are a subscription-level governance configuration; applications do not interact with them.

## Common Pitfalls

1. **Name must be `"default"`** -- The security contact resource name must always be `default`. Other names are rejected by the API.
2. **Subscription-scoped resource** -- The parent ID is the subscription, not a resource group. Using a resource group ID fails.
3. **Email format validation** -- The `emails` property must be a valid email address or semicolon-separated list. Invalid formats cause deployment failure.
4. **Notification suppression** -- If `notificationsByRole.state` is `Off`, subscription owners/admins won't receive alerts even if their email matches `emails`.
5. **API version differences** -- Older API versions use different property names (`alertNotifications`, `alertsToAdmins`). Use `2023-12-01-preview` for the current schema.
6. **No notification without Defender plans** -- Security contacts only receive notifications when Defender plans are enabled. Without Defender plans, there are no alerts to notify about.
7. **Email deliverability** -- Verify the security email inbox accepts Azure notification emails. Spam filters may block automated Azure emails.

## Production Backlog Items

- [ ] Configure security contact with a monitored security distribution list
- [ ] Lower minimum severity to Medium for broader alert coverage
- [ ] Add phone number for high-severity escalation calls
- [ ] Enable attack path notification sources
- [ ] Verify email delivery by triggering a test alert
- [ ] Document escalation procedures for different alert severities
- [ ] Configure additional notification channels via Logic Apps automation
