---
service_namespace: Microsoft.SecurityInsights/dataConnectors
display_name: Sentinel Data Connector
depends_on:
  - Microsoft.SecurityInsights/settings
---

# Sentinel Data Connector

> Integration point that streams security-relevant log data from Azure services, Microsoft products, and third-party sources into the Sentinel workspace for threat detection and investigation.

## When to Use
- **Azure Activity logs** -- track resource management operations (create, delete, modify)
- **Azure AD / Entra ID** -- sign-in logs, audit logs, provisioning logs for identity threat detection
- **Microsoft Defender** -- alerts from Defender for Cloud, Endpoint, Identity, Office 365
- **Third-party sources** -- Syslog, CEF, REST API connectors for firewalls, SIEMs, custom apps
- **AWS / GCP** -- multi-cloud security monitoring via built-in cloud connectors

Data connectors are the primary way to ingest security data into Sentinel. Without connectors, Sentinel has no data to analyze.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Minimum connectors | Azure Activity + Azure AD Sign-In | Covers resource and identity events |
| Authentication | Managed identity | MSI for Azure-native connectors |
| Tenant scope | Current tenant | Multi-tenant requires Lighthouse |
| Diagnostic settings | Enabled | Some connectors use diagnostic settings under the hood |

## Terraform Patterns

### Basic Resource

```hcl
# Azure Activity data connector
resource "azapi_resource" "connector_activity" {
  type      = "Microsoft.SecurityInsights/dataConnectors@2024-03-01"
  name      = var.connector_name  # GUID
  parent_id = var.workspace_id

  body = {
    kind = "AzureActivity"
    properties = {
      linkedResourceId = "/subscriptions/${var.subscription_id}/providers/microsoft.insights/eventtypes/management"
    }
  }

  depends_on = [azapi_resource.sentinel_onboarding]
}

# Microsoft Defender for Cloud connector
resource "azapi_resource" "connector_defender" {
  type      = "Microsoft.SecurityInsights/dataConnectors@2024-03-01"
  name      = var.defender_connector_name  # GUID
  parent_id = var.workspace_id

  body = {
    kind = "AzureSecurityCenter"
    properties = {
      subscriptionId = var.subscription_id
      dataTypes = {
        alerts = {
          state = "Enabled"
        }
      }
    }
  }

  depends_on = [azapi_resource.sentinel_onboarding]
}

# Threat Intelligence connector
resource "azapi_resource" "connector_ti" {
  type      = "Microsoft.SecurityInsights/dataConnectors@2024-03-01"
  name      = var.ti_connector_name  # GUID
  parent_id = var.workspace_id

  body = {
    kind = "ThreatIntelligence"
    properties = {
      dataTypes = {
        indicators = {
          state = "Enabled"
        }
      }
      tenantId = var.tenant_id
    }
  }

  depends_on = [azapi_resource.sentinel_onboarding]
}
```

### RBAC Assignment

```hcl
# Microsoft Sentinel Contributor on the workspace for connector management
# Role ID: ab8e14d6-4a74-4a29-9ba8-549422addade
# Some connectors also require permissions on the source resource
# (e.g., Security Reader on subscription for Defender for Cloud)
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Connector GUID')
param connectorName string = newGuid()

@description('Subscription ID for activity logs')
param subscriptionId string

resource activityConnector 'Microsoft.SecurityInsights/dataConnectors@2024-03-01' = {
  name: connectorName
  scope: workspace
  kind: 'AzureActivity'
  properties: {
    linkedResourceId: '/subscriptions/${subscriptionId}/providers/microsoft.insights/eventtypes/management'
  }
}
```

## Application Code

### Python
Infrastructure -- transparent to application code. Data connectors stream security logs into Sentinel; applications generating logs do so through standard Azure diagnostic settings or logging SDKs.

### C#
Infrastructure -- transparent to application code. Data connectors stream security logs into Sentinel; applications generating logs do so through standard Azure diagnostic settings or logging SDKs.

### Node.js
Infrastructure -- transparent to application code. Data connectors stream security logs into Sentinel; applications generating logs do so through standard Azure diagnostic settings or logging SDKs.

## Common Pitfalls

1. **Connector name must be a GUID** -- Like alert rules, data connector resource names must be GUIDs. Using descriptive names fails deployment.
2. **Sentinel onboarding required first** -- Deploying connectors before `onboardingStates/default` causes inconsistent behavior. Always add `depends_on`.
3. **Azure Activity requires diagnostic settings** -- The `AzureActivity` connector kind configures the linkage, but the subscription must also have diagnostic settings sending Activity logs to the workspace.
4. **Duplicate connector detection** -- Some connector kinds allow only one instance per workspace. Deploying a second `AzureActivity` connector overwrites the first.
5. **Permissions on source resources** -- Many connectors require read permissions on the source (e.g., Security Reader on subscription for Defender for Cloud). Missing permissions cause silent ingestion failures.
6. **Data ingestion cost** -- Each connector adds data volume to the workspace. Azure AD sign-in logs for large tenants can generate several GB/day. Monitor ingestion volume.
7. **Kind-specific property schemas** -- Each connector `kind` has a different properties schema. Using wrong properties for a kind causes cryptic ARM errors.

## Production Backlog Items

- [ ] Enable Azure AD (Entra ID) sign-in and audit log connectors
- [ ] Connect Microsoft Defender for Cloud alerts
- [ ] Add Microsoft 365 Defender connector for endpoint/email/identity alerts
- [ ] Configure Syslog/CEF connectors for on-premises firewalls and network devices
- [ ] Enable AWS CloudTrail and/or GCP connector for multi-cloud coverage
- [ ] Set up diagnostic settings on all Azure resources to route logs to the workspace
- [ ] Monitor data ingestion volume and costs per connector
- [ ] Implement content hub solutions for additional connector templates
