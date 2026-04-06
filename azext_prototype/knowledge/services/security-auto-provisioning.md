---
service_namespace: Microsoft.Security/autoProvisioningSettings
display_name: Defender Auto-Provisioning
---

# Defender Auto-Provisioning

> Subscription-level setting that controls automatic installation of the Log Analytics agent (MMA) or Azure Monitor Agent (AMA) on Azure VMs for Defender for Cloud data collection.

## When to Use
- **VM security monitoring** -- automatically install monitoring agents on new and existing VMs
- **Defender for Servers** -- required for full Defender for Servers protection (vulnerability assessment, adaptive controls, file integrity monitoring)
- **Security baseline compliance** -- many compliance frameworks require agent-based monitoring on all compute
- Configure once per subscription as part of the security baseline

**Note:** Microsoft is migrating from the Log Analytics agent (MMA) to Azure Monitor Agent (AMA). New deployments should use AMA via data collection rules (DCR) instead of the legacy auto-provisioning setting.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Name | `default` | Must be `default` |
| Auto provision | On | For Defender for Servers; Off if not using Defender |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "auto_provisioning" {
  type      = "Microsoft.Security/autoProvisioningSettings@2017-08-01-preview"
  name      = "default"
  parent_id = "/subscriptions/${var.subscription_id}"

  body = {
    properties = {
      autoProvision = "On"  # or "Off"
    }
  }
}
```

### RBAC Assignment

```hcl
# Security Admin on the subscription for managing auto-provisioning
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

@description('Enable auto-provisioning of monitoring agent')
@allowed(['On', 'Off'])
param autoProvision string = 'On'

resource autoProvisioning 'Microsoft.Security/autoProvisioningSettings@2017-08-01-preview' = {
  name: 'default'
  properties: {
    autoProvision: autoProvision
  }
}
```

## Application Code

### Python
Infrastructure -- transparent to application code. Auto-provisioning installs monitoring agents on VMs; applications running on those VMs are unaware of the agent.

### C#
Infrastructure -- transparent to application code. Auto-provisioning installs monitoring agents on VMs; applications running on those VMs are unaware of the agent.

### Node.js
Infrastructure -- transparent to application code. Auto-provisioning installs monitoring agents on VMs; applications running on those VMs are unaware of the agent.

## Common Pitfalls

1. **Name must be `"default"`** -- The auto-provisioning setting name must always be `default`.
2. **Subscription-scoped** -- Parent ID is the subscription, not a resource group.
3. **Legacy MMA agent** -- This setting provisions the legacy Log Analytics agent (MMA). Microsoft recommends migrating to Azure Monitor Agent (AMA) via data collection rules.
4. **Agent conflicts** -- If VMs already have a manually installed MMA agent pointing to a different workspace, auto-provisioning creates a second agent instance, causing duplicate data and confusion.
5. **No effect without Defender plans** -- Auto-provisioning installs agents, but without Defender for Servers enabled, the security data isn't analyzed. Enable both together.
6. **Extension installation requires VM running** -- The agent extension is only installed on running VMs. Deallocated VMs get the agent on next start.
7. **API version is old** -- `2017-08-01-preview` is the only version for this resource type. Despite being a preview, it is the production API.

## Production Backlog Items

- [ ] Migrate from MMA to Azure Monitor Agent (AMA) with data collection rules
- [ ] Configure custom workspace destination for agent data
- [ ] Evaluate agentless scanning (Defender for Servers P2) as alternative to agent-based
- [ ] Set up Azure Policy to enforce monitoring agent presence on VMs
- [ ] Monitor agent health and connectivity across the VM fleet
- [ ] Plan MMA deprecation timeline (August 2024 retirement announced)
