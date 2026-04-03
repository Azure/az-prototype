# Azure DDoS Protection
> Always-on traffic monitoring and automatic DDoS attack mitigation for Azure public IP resources, providing L3/L4 volumetric, protocol, and resource-layer attack protection.

## When to Use

- **Public-facing workloads** -- any architecture with public IP addresses exposed to the internet
- **Compliance requirements** -- regulatory frameworks requiring DDoS protection (PCI-DSS, SOC 2)
- **Financial protection** -- DDoS Protection includes cost protection credits for scale-out during attacks
- **Advanced telemetry** -- attack analytics, flow logs, and rapid response support
- **Multi-resource protection** -- single plan protects all public IPs in associated VNets
- NOT suitable for: pure internal/private workloads (no public IPs), or cost-constrained POC where Azure DDoS Infrastructure Protection (free, default) is acceptable

All Azure resources have free DDoS Infrastructure Protection. DDoS Protection (paid) adds adaptive tuning, attack analytics, cost protection, and Rapid Response support.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Tier | DDoS Protection | Free Infrastructure Protection for tight-budget POC |
| Association | VNet-level | Plan associates with VNets; protects all public IPs in those VNets |
| Alerts | Enabled | Alert on DDoS attack detection and mitigation |
| Diagnostic logs | Enabled | Flow logs and mitigation reports |
| Cost protection | Included | Credits for scale-out costs during attacks (Standard only) |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "ddos_plan" {
  type      = "Microsoft.Network/ddosProtectionPlans@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  tags = var.tags
}
```

### Associate DDoS Plan with VNet

```hcl
# Associate the DDoS protection plan with a VNet
resource "azapi_update_resource" "vnet_ddos" {
  type        = "Microsoft.Network/virtualNetworks@2024-01-01"
  resource_id = var.virtual_network_id

  body = {
    properties = {
      addressSpace = {
        addressPrefixes = var.address_prefixes
      }
      enableDdosProtection = true
      ddosProtectionPlan = {
        id = azapi_resource.ddos_plan.id
      }
    }
  }
}
```

### DDoS Protection Plan with Multiple VNets

```hcl
resource "azapi_resource" "ddos_plan" {
  type      = "Microsoft.Network/ddosProtectionPlans@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  tags = var.tags
}

# One plan can protect multiple VNets (even cross-subscription)
resource "azapi_update_resource" "vnet_ddos_assoc" {
  for_each = var.virtual_network_ids

  type        = "Microsoft.Network/virtualNetworks@2024-01-01"
  resource_id = each.value

  body = {
    properties = {
      addressSpace = {
        addressPrefixes = var.vnet_address_prefixes[each.key]
      }
      enableDdosProtection = true
      ddosProtectionPlan = {
        id = azapi_resource.ddos_plan.id
      }
    }
  }
}
```

### Diagnostic Settings

```hcl
# Enable diagnostic logging for DDoS-protected public IPs
resource "azapi_resource" "ddos_diagnostics" {
  type      = "Microsoft.Insights/diagnosticSettings@2021-05-01-preview"
  name      = "ddos-diagnostics"
  parent_id = var.public_ip_id  # Diagnostics are on the public IP, not the plan

  body = {
    properties = {
      workspaceId = var.log_analytics_workspace_id
      logs = [
        {
          categoryGroup = "allLogs"
          enabled       = true
          retentionPolicy = {
            days    = 30
            enabled = true
          }
        }
      ]
      metrics = [
        {
          category = "AllMetrics"
          enabled  = true
          retentionPolicy = {
            days    = 30
            enabled = true
          }
        }
      ]
    }
  }
}
```

### RBAC Assignment

```hcl
# Network Contributor for DDoS plan management
resource "azapi_resource" "ddos_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.ddos_plan.id}-${var.admin_principal_id}-network-contributor")
  parent_id = azapi_resource.ddos_plan.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/4d97b98b-1d4f-4787-a291-c67834d212e7"  # Network Contributor
      principalId      = var.admin_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

DDoS Protection does not use private endpoints -- it is a network-level protection service that attaches to VNets and automatically protects all public IPs within those VNets.

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the DDoS Protection Plan')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Tags to apply')
param tags object = {}

resource ddosPlan 'Microsoft.Network/ddosProtectionPlans@2024-01-01' = {
  name: name
  location: location
  tags: tags
}

output id string = ddosPlan.id
```

### VNet Association

```bicep
@description('VNet name to protect')
param vnetName string

@description('VNet address prefixes')
param addressPrefixes array

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' existing = {
  name: vnetName
}

resource vnetDdos 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: vnetName
  location: vnet.location
  properties: {
    addressSpace: {
      addressPrefixes: addressPrefixes
    }
    enableDdosProtection: true
    ddosProtectionPlan: {
      id: ddosPlan.id
    }
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Cost surprise | DDoS Protection Plan is ~$2,944/month flat fee | One plan covers up to 100 public IPs across VNets; share across subscriptions |
| Not associating with VNet | Plan exists but no resources are protected | Associate plan with each VNet containing public IPs |
| Confusing Infrastructure vs. Protection | Infrastructure Protection is basic and free; Protection is the paid plan | Infrastructure Protection is automatic; paid plan is needed for advanced features |
| Forgetting diagnostic logging on public IPs | No attack visibility or forensics | Enable diagnostics on each protected public IP, not on the plan |
| Protecting too many plans | Each plan is $2,944/month; only one is needed per tenant | Use a single plan associated with multiple VNets across subscriptions |
| No alert configuration | Attacks happen without notification | Configure Azure Monitor alerts on DDoS metrics for each public IP |
| Removing plan accidentally | All associated VNets lose protection immediately | Use resource locks on the DDoS plan |
| Not claiming cost protection | Scale-out costs during attack are not refunded automatically | File support ticket with attack logs to claim cost protection credits |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| DDoS Rapid Response | P1 | Enroll in DDoS Rapid Response for Microsoft-assisted mitigation during attacks |
| Attack analytics | P1 | Enable attack analytics for post-attack forensics and reporting |
| Metric alerts | P1 | Configure alerts on `UnderDDoSAttack`, `PacketsDroppedDDoS`, `BytesDroppedDDoS` |
| Cross-subscription sharing | P2 | Associate the single plan with VNets in other subscriptions to reduce cost |
| Flow logs | P2 | Enable DDoS mitigation flow logs for detailed traffic analysis |
| IP protection configuration | P3 | Review auto-tuned protection thresholds for each public IP |
| Resource lock | P1 | Apply CannotDelete lock on the DDoS plan to prevent accidental removal |
| Integration with SIEM | P2 | Forward DDoS logs to SIEM for security operations center visibility |
| Cost protection documentation | P2 | Document the cost protection claim process for operations team |
| Regular drills | P3 | Schedule DDoS simulation tests with approved testing partners |
