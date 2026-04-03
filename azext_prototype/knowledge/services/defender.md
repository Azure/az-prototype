# Microsoft Defender for Cloud
> Unified cloud security posture management (CSPM) and cloud workload protection platform (CWPP) providing security recommendations, threat detection, and vulnerability assessment across Azure, multi-cloud, and hybrid environments.

## When to Use

- Security posture assessment and hardening recommendations for Azure resources
- Threat protection for compute (VMs, containers, App Service), data (SQL, Storage), and identity
- Regulatory compliance dashboards (PCI DSS, SOC 2, ISO 27001, NIST)
- Vulnerability scanning for VMs, container images, and SQL databases
- Just-in-time VM access and adaptive application controls
- NOT suitable for: SIEM/incident management (use Microsoft Sentinel), identity governance (use Entra ID), or network traffic inspection (use Azure Firewall/NSGs)

**Note**: Defender for Cloud has two tiers: Free (basic CSPM with security score and recommendations) and Enhanced (per-resource plans with advanced threat protection). Most Defender plans are subscription-level resources.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Tier | Free (Foundational CSPM) | Enhanced plans cost per-resource; enable selectively |
| Auto-provisioning | Disabled | Enable selectively for production |
| Security contacts | 1-2 team emails | For alert notifications |
| Continuous export | Disabled | Enable with Log Analytics for production |
| Secure score | Enabled (always on) | Monitor and improve over time |
| Defender plans | None (free tier) | Enable per-workload as needed |

## Terraform Patterns

### Security Contact Configuration

```hcl
resource "azapi_resource" "security_contact" {
  type      = "Microsoft.Security/securityContacts@2023-12-01-preview"
  name      = "default"
  parent_id = "/subscriptions/${var.subscription_id}"

  body = {
    properties = {
      emails                = var.security_email
      phone                 = var.security_phone
      isEnabled             = true
      notificationsByRole = {
        state = "On"
        roles = ["Owner", "ServiceAdmin"]
      }
      notificationsSources = [
        {
          sourceType       = "Alert"
          minimalSeverity  = "Medium"
        },
        {
          sourceType       = "AttackPath"
          minimalRiskLevel = "Critical"
        }
      ]
    }
  }
}
```

### Enable Defender Plans (Subscription Level)

```hcl
# Defender for Servers
resource "azapi_resource" "defender_servers" {
  type      = "Microsoft.Security/pricings@2024-01-01"
  name      = "VirtualMachines"
  parent_id = "/subscriptions/${var.subscription_id}"

  body = {
    properties = {
      pricingTier = var.enable_defender_servers ? "Standard" : "Free"
      subPlan     = "P1"  # P1 or P2
    }
  }
}

# Defender for App Service
resource "azapi_resource" "defender_appservice" {
  type      = "Microsoft.Security/pricings@2024-01-01"
  name      = "AppServices"
  parent_id = "/subscriptions/${var.subscription_id}"

  body = {
    properties = {
      pricingTier = var.enable_defender_appservice ? "Standard" : "Free"
    }
  }
}

# Defender for Key Vault
resource "azapi_resource" "defender_keyvault" {
  type      = "Microsoft.Security/pricings@2024-01-01"
  name      = "KeyVaults"
  parent_id = "/subscriptions/${var.subscription_id}"

  body = {
    properties = {
      pricingTier = var.enable_defender_keyvault ? "Standard" : "Free"
    }
  }
}

# Defender for Storage
resource "azapi_resource" "defender_storage" {
  type      = "Microsoft.Security/pricings@2024-01-01"
  name      = "StorageAccounts"
  parent_id = "/subscriptions/${var.subscription_id}"

  body = {
    properties = {
      pricingTier = var.enable_defender_storage ? "Standard" : "Free"
      subPlan     = "DefenderForStorageV2"
    }
  }
}

# Defender for SQL
resource "azapi_resource" "defender_sql" {
  type      = "Microsoft.Security/pricings@2024-01-01"
  name      = "SqlServers"
  parent_id = "/subscriptions/${var.subscription_id}"

  body = {
    properties = {
      pricingTier = var.enable_defender_sql ? "Standard" : "Free"
    }
  }
}

# Defender for Containers
resource "azapi_resource" "defender_containers" {
  type      = "Microsoft.Security/pricings@2024-01-01"
  name      = "Containers"
  parent_id = "/subscriptions/${var.subscription_id}"

  body = {
    properties = {
      pricingTier = var.enable_defender_containers ? "Standard" : "Free"
    }
  }
}
```

### Auto-Provisioning Settings

```hcl
resource "azapi_resource" "auto_provision_mma" {
  type      = "Microsoft.Security/autoProvisioningSettings@2017-08-01-preview"
  name      = "default"
  parent_id = "/subscriptions/${var.subscription_id}"

  body = {
    properties = {
      autoProvision = var.enable_auto_provisioning ? "On" : "Off"
    }
  }
}
```

### Continuous Export to Log Analytics

```hcl
resource "azapi_resource" "continuous_export" {
  type      = "Microsoft.Security/automations@2023-12-01-preview"
  name      = var.export_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      isEnabled   = true
      description = "Export Defender alerts and recommendations to Log Analytics"
      scopes = [
        {
          description = "Subscription scope"
          scopePath   = "/subscriptions/${var.subscription_id}"
        }
      ]
      sources = [
        {
          eventSource = "Alerts"
          ruleSets    = []
        },
        {
          eventSource = "Assessments"
          ruleSets    = []
        }
      ]
      actions = [
        {
          actionType    = "Workspace"
          workspaceResourceId = var.workspace_id
        }
      ]
    }
  }

  tags = var.tags
}
```

## Bicep Patterns

### Security Contact Configuration

```bicep
targetScope = 'subscription'

param securityEmail string
param securityPhone string = ''

resource securityContact 'Microsoft.Security/securityContacts@2023-12-01-preview' = {
  name: 'default'
  properties: {
    emails: securityEmail
    phone: securityPhone
    isEnabled: true
    notificationsByRole: {
      state: 'On'
      roles: ['Owner', 'ServiceAdmin']
    }
    notificationsSources: [
      {
        sourceType: 'Alert'
        minimalSeverity: 'Medium'
      }
    ]
  }
}
```

### Enable Defender Plans

```bicep
targetScope = 'subscription'

param enableDefenderServers bool = false
param enableDefenderAppService bool = false
param enableDefenderKeyVault bool = false

resource defenderServers 'Microsoft.Security/pricings@2024-01-01' = {
  name: 'VirtualMachines'
  properties: {
    pricingTier: enableDefenderServers ? 'Standard' : 'Free'
    subPlan: 'P1'
  }
}

resource defenderAppService 'Microsoft.Security/pricings@2024-01-01' = {
  name: 'AppServices'
  properties: {
    pricingTier: enableDefenderAppService ? 'Standard' : 'Free'
  }
}

resource defenderKeyVault 'Microsoft.Security/pricings@2024-01-01' = {
  name: 'KeyVaults'
  properties: {
    pricingTier: enableDefenderKeyVault ? 'Standard' : 'Free'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Enabling all Defender plans at once | Unexpected costs; many plans charge per-resource per-month | Start with Free tier for POC; enable plans selectively |
| Not configuring security contacts | Critical alerts go unnoticed | Always set at least one email contact |
| Ignoring Secure Score recommendations | Security posture degrades over time | Review and address high-impact recommendations regularly |
| Auto-provisioning without planning | Agents deployed to all VMs, potential performance impact | Enable auto-provisioning selectively, test on non-production first |
| Confusing Defender for Cloud with Sentinel | Wrong tool for the job | Defender = prevention/detection per resource; Sentinel = SIEM/SOAR |
| Subscription-level resources in Terraform | Terraform state conflicts if multiple deployments target same subscription | Use a dedicated Terraform workspace for subscription-level Defender config |
| Not enabling continuous export | Alerts only visible in portal, not in Log Analytics | Enable continuous export for Sentinel integration and long-term retention |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Enable Defender plans | P1 | Enable Standard tier for production workloads (Servers, App Service, SQL, Storage, Key Vault) |
| Continuous export | P1 | Export alerts and recommendations to Log Analytics for Sentinel correlation |
| Just-in-time VM access | P2 | Enable JIT access to reduce VM attack surface |
| Adaptive application controls | P3 | Enable application allowlisting on VMs |
| Vulnerability assessment | P2 | Enable vulnerability scanning for VMs and container images |
| Regulatory compliance | P2 | Enable compliance dashboards for required standards (PCI DSS, SOC 2, etc.) |
| Workflow automation | P3 | Create Logic App workflows triggered by Defender recommendations |
| Multi-cloud connectors | P3 | Connect AWS/GCP accounts for unified security posture |
| Defender for DevOps | P3 | Enable DevOps security for pipeline and code scanning |
| Custom security policies | P2 | Create custom Azure Policy definitions for organization-specific requirements |
