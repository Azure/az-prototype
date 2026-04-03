# Azure Firewall
> Cloud-native, fully managed network security service providing centralized network and application rule enforcement, threat intelligence-based filtering, and FQDN-based egress control.

## When to Use

- **Centralized egress filtering** -- control and log all outbound traffic from VNets to the internet
- **Hub-spoke network topology** -- central firewall in the hub VNet inspecting traffic between spokes
- **FQDN-based rules** -- allow outbound access to specific domain names (e.g., `*.docker.io`, `pypi.org`)
- **Threat intelligence** -- block traffic to/from known malicious IPs and domains
- **Forced tunneling** -- route all internet-bound traffic through the firewall for inspection
- NOT suitable for: L7 HTTP load balancing (use Application Gateway), global CDN/WAF (use Front Door), or simple NSG-level filtering

Choose Azure Firewall for centralized network-level security. Pair with Application Gateway or Front Door for L7 web application protection.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Standard | Premium for IDPS/TLS inspection; Basic for dev/test |
| Subnet name | AzureFirewallSubnet | Must be exactly this name (Azure requirement) |
| Subnet size | /26 minimum | Required minimum for Azure Firewall |
| Threat intelligence | Alert only | Alert and deny for production |
| DNS proxy | Enabled | Required for FQDN filtering in network rules |
| Public IP | Standard SKU, Static | Required; multiple for SNAT ports |
| Firewall policy | Centralized | Rule collection groups in a firewall policy |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "firewall_subnet" {
  type      = "Microsoft.Network/virtualNetworks/subnets@2024-01-01"
  name      = "AzureFirewallSubnet"  # Must be exactly this name
  parent_id = var.virtual_network_id

  body = {
    properties = {
      addressPrefix = var.firewall_subnet_prefix  # e.g., "10.0.255.0/26"
    }
  }
}

resource "azapi_resource" "firewall_pip" {
  type      = "Microsoft.Network/publicIPAddresses@2024-01-01"
  name      = "pip-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard"
    }
    properties = {
      publicIPAllocationMethod = "Static"
    }
  }

  tags = var.tags
}

resource "azapi_resource" "firewall_policy" {
  type      = "Microsoft.Network/firewallPolicies@2024-01-01"
  name      = "policy-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      sku = {
        tier = "Standard"  # or "Premium"
      }
      threatIntelMode = "Alert"  # "Deny" for production
      dnsSettings = {
        enableProxy = true
      }
    }
  }

  tags = var.tags
}

resource "azapi_resource" "firewall" {
  type      = "Microsoft.Network/azureFirewalls@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      sku = {
        name = "AZFW_VNet"
        tier = "Standard"
      }
      ipConfigurations = [
        {
          name = "fw-ip-config"
          properties = {
            publicIPAddress = {
              id = azapi_resource.firewall_pip.id
            }
            subnet = {
              id = azapi_resource.firewall_subnet.id
            }
          }
        }
      ]
      firewallPolicy = {
        id = azapi_resource.firewall_policy.id
      }
    }
  }

  tags = var.tags

  response_export_values = ["properties.ipConfigurations[0].properties.privateIPAddress"]
}
```

### Firewall Policy Rule Collection Group

```hcl
resource "azapi_resource" "rule_collection_group" {
  type      = "Microsoft.Network/firewallPolicies/ruleCollectionGroups@2024-01-01"
  name      = "default-rule-collection-group"
  parent_id = azapi_resource.firewall_policy.id

  body = {
    properties = {
      priority = 200
      ruleCollections = [
        {
          ruleCollectionType = "FirewallPolicyFilterRuleCollection"
          name               = "allow-application-rules"
          priority           = 100
          action = {
            type = "Allow"
          }
          rules = [
            {
              ruleType         = "ApplicationRule"
              name             = "allow-azure-services"
              sourceAddresses  = ["10.0.0.0/16"]
              protocols = [
                {
                  protocolType = "Https"
                  port         = 443
                }
              ]
              targetFqdns = [
                "*.azure.com"
                "*.microsoft.com"
                "*.windows.net"
              ]
            }
          ]
        }
        {
          ruleCollectionType = "FirewallPolicyFilterRuleCollection"
          name               = "allow-network-rules"
          priority           = 200
          action = {
            type = "Allow"
          }
          rules = [
            {
              ruleType            = "NetworkRule"
              name                = "allow-dns"
              sourceAddresses     = ["10.0.0.0/16"]
              destinationAddresses = ["*"]
              destinationPorts    = ["53"]
              ipProtocols         = ["TCP", "UDP"]
            }
          ]
        }
      ]
    }
  }
}
```

### Route Table for Forced Tunneling

```hcl
resource "azapi_resource" "route_table" {
  type      = "Microsoft.Network/routeTables@2024-01-01"
  name      = "rt-firewall"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      disableBgpRoutePropagation = true
      routes = [
        {
          name = "route-to-firewall"
          properties = {
            addressPrefix    = "0.0.0.0/0"
            nextHopType      = "VirtualAppliance"
            nextHopIpAddress = azapi_resource.firewall.output.properties.ipConfigurations[0].properties.privateIPAddress
          }
        }
      ]
    }
  }

  tags = var.tags
}

# Associate route table with workload subnets
resource "azapi_update_resource" "subnet_route_table" {
  type        = "Microsoft.Network/virtualNetworks/subnets@2024-01-01"
  resource_id = var.workload_subnet_id

  body = {
    properties = {
      addressPrefix = var.workload_subnet_prefix
      routeTable = {
        id = azapi_resource.route_table.id
      }
    }
  }
}
```

### RBAC Assignment

```hcl
# Network Contributor for firewall management
resource "azapi_resource" "firewall_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.firewall.id}-${var.admin_principal_id}-network-contributor")
  parent_id = azapi_resource.firewall.id

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

Azure Firewall does not use private endpoints -- it is deployed into a dedicated subnet (`AzureFirewallSubnet`) and operates as a network virtual appliance within the VNet.

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Azure Firewall')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Virtual network ID')
param virtualNetworkId string

@description('Firewall subnet prefix (min /26)')
param firewallSubnetPrefix string = '10.0.255.0/26'

@description('Tags to apply')
param tags object = {}

resource firewallSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' = {
  name: '${split(virtualNetworkId, '/')[8]}/AzureFirewallSubnet'
  properties: {
    addressPrefix: firewallSubnetPrefix
  }
}

resource firewallPip 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
  name: 'pip-${name}'
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
  tags: tags
}

resource firewallPolicy 'Microsoft.Network/firewallPolicies@2024-01-01' = {
  name: 'policy-${name}'
  location: location
  properties: {
    sku: {
      tier: 'Standard'
    }
    threatIntelMode: 'Alert'
    dnsSettings: {
      enableProxy: true
    }
  }
  tags: tags
}

resource firewall 'Microsoft.Network/azureFirewalls@2024-01-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'AZFW_VNet'
      tier: 'Standard'
    }
    ipConfigurations: [
      {
        name: 'fw-ip-config'
        properties: {
          publicIPAddress: {
            id: firewallPip.id
          }
          subnet: {
            id: firewallSubnet.id
          }
        }
      }
    ]
    firewallPolicy: {
      id: firewallPolicy.id
    }
  }
}

output id string = firewall.id
output privateIpAddress string = firewall.properties.ipConfigurations[0].properties.privateIPAddress
output policyId string = firewallPolicy.id
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Wrong subnet name | Deployment fails | Subnet must be named exactly `AzureFirewallSubnet` |
| Subnet too small | Cannot deploy firewall | Minimum /26 (64 addresses); Azure reserves some |
| Missing route table on workload subnets | Traffic bypasses the firewall | Attach UDR with `0.0.0.0/0 -> VirtualAppliance -> FW private IP` to all workload subnets |
| DNS proxy not enabled | FQDN-based network rules do not resolve | Enable `dnsSettings.enableProxy = true` in firewall policy |
| Threat intel mode set to Deny in POC | Legitimate traffic blocked unexpectedly | Use `Alert` mode during POC; switch to `Deny` for production |
| SNAT port exhaustion | Outbound connections fail under load | Add multiple public IPs to the firewall for more SNAT ports |
| Forgetting to allow Azure management traffic | VM extensions, AKS, updates break | Add application rules for `*.azure.com`, `*.windows.net`, etc. |
| Cost surprise | Standard is ~$1.25/hour even when idle | Consider Basic SKU ($0.395/hour) for POC environments |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Premium SKU upgrade | P2 | Upgrade for IDPS, TLS inspection, and URL filtering |
| Threat intelligence deny mode | P1 | Switch from Alert to Deny for known malicious traffic |
| Diagnostic logging | P1 | Enable firewall logs and metrics to Log Analytics for auditing |
| Availability zones | P1 | Deploy across zones for 99.99% SLA |
| Multiple public IPs | P2 | Add additional public IPs for SNAT port capacity |
| Forced tunneling for all subnets | P1 | Ensure all workload subnets route through firewall |
| Application rule refinement | P2 | Narrow FQDN rules to specific required destinations |
| TLS inspection | P2 | Enable TLS inspection for encrypted traffic analysis (Premium) |
| IP Groups | P3 | Use IP Groups for reusable source/destination address sets |
| Centralized policy management | P3 | Use Azure Firewall Manager for multi-firewall policy management |
