# Azure Public IP Address
> Static or dynamic public IPv4/IPv6 address resource used by load balancers, application gateways, VPN gateways, Bastion hosts, and virtual machines for internet-facing connectivity.

## When to Use

- **Internet-facing services** -- required by Application Gateway, Load Balancer, Azure Firewall, Bastion
- **VM direct internet access** -- attach to VM NIC for direct public connectivity (not recommended for production)
- **NAT Gateway** -- provides static outbound IP for subnet-level SNAT
- **VPN/ExpressRoute Gateway** -- required for gateway public endpoint
- **Static IP requirement** -- DNS A records, firewall allow-listing, partner integrations

Public IP is a **foundational resource** -- it is consumed by other networking resources rather than used standalone.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Standard | Basic is deprecated for new deployments |
| Allocation | Static | Required for Standard SKU |
| Version | IPv4 | IPv6 for dual-stack scenarios |
| Tier | Regional | Global for cross-region LB |
| Idle timeout | 4 minutes | Default; configurable 4-30 minutes |
| DNS label | Optional | Creates `<label>.<region>.cloudapp.azure.com` |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "public_ip" {
  type      = "Microsoft.Network/publicIPAddresses@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard"   # "Basic" is deprecated
      tier = "Regional"   # "Global" for cross-region LB
    }
    properties = {
      publicIPAllocationMethod = "Static"  # Required for Standard SKU
      idleTimeoutInMinutes     = 4
    }
  }

  tags = var.tags

  response_export_values = ["properties.ipAddress"]
}
```

### Public IP with DNS Label

```hcl
resource "azapi_resource" "public_ip_dns" {
  type      = "Microsoft.Network/publicIPAddresses@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard"
    }
    properties = {
      publicIPAllocationMethod = "Static"
      dnsSettings = {
        domainNameLabel = var.dns_label  # Creates <label>.<region>.cloudapp.azure.com
      }
    }
  }

  tags = var.tags

  response_export_values = ["properties.ipAddress", "properties.dnsSettings.fqdn"]
}
```

### Public IP Prefix (Contiguous Range)

```hcl
resource "azapi_resource" "public_ip_prefix" {
  type      = "Microsoft.Network/publicIPPrefixes@2024-01-01"
  name      = "ippre-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard"
    }
    properties = {
      prefixLength             = 30  # /30 = 4 IPs, /29 = 8 IPs, /28 = 16 IPs
      publicIPAddressVersion   = "IPv4"
    }
  }

  tags = var.tags
}

# Create IPs from the prefix
resource "azapi_resource" "public_ip_from_prefix" {
  type      = "Microsoft.Network/publicIPAddresses@2024-01-01"
  name      = "pip-${var.name}-1"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard"
    }
    properties = {
      publicIPAllocationMethod = "Static"
      publicIPPrefix = {
        id = azapi_resource.public_ip_prefix.id
      }
    }
  }

  tags = var.tags
}
```

### Zone-Redundant Public IP

```hcl
resource "azapi_resource" "public_ip_zonal" {
  type      = "Microsoft.Network/publicIPAddresses@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard"
    }
    zones = ["1", "2", "3"]  # Zone-redundant
    properties = {
      publicIPAllocationMethod = "Static"
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# Network Contributor for public IP management
resource "azapi_resource" "pip_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.public_ip.id}-${var.admin_principal_id}-network-contributor")
  parent_id = azapi_resource.public_ip.id

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

Public IP does not use private endpoints -- it is inherently a public-facing resource.

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Public IP Address')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Optional DNS label')
param dnsLabel string = ''

@description('Tags to apply')
param tags object = {}

resource publicIp 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
    dnsSettings: !empty(dnsLabel) ? {
      domainNameLabel: dnsLabel
    } : null
  }
}

output id string = publicIp.id
output ipAddress string = publicIp.properties.ipAddress
output fqdn string = !empty(dnsLabel) ? publicIp.properties.dnsSettings.fqdn : ''
```

### Zone-Redundant Public IP

```bicep
resource publicIpZonal 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  zones: ['1', '2', '3']
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Using Basic SKU | Basic is deprecated, no zone support, no Standard LB compatibility | Always use Standard SKU |
| Dynamic allocation with Standard SKU | Not supported; deployment fails | Standard SKU requires `Static` allocation |
| Mixing Basic and Standard | Resources must match SKU tier | Ensure all associated resources use Standard |
| Orphaned public IPs | Continued billing for unattached IPs | Delete unused public IPs; monitor with Azure Advisor |
| No DDoS protection | Public IPs are exposed to volumetric attacks | Associate with DDoS Protection Plan for production |
| Forgetting DNS label | Must use raw IP instead of hostname | Set `dnsSettings.domainNameLabel` for a stable FQDN |
| IPv6 without dual-stack VNet | IPv6 PIP cannot be used without VNet IPv6 address space | Configure VNet dual-stack before creating IPv6 PIPs |
| Idle timeout too low | Long-running connections drop | Increase idle timeout or use TCP keepalives at application level |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| DDoS Protection | P1 | Associate with DDoS Protection Plan for volumetric attack mitigation |
| Zone redundancy | P1 | Deploy zone-redundant PIPs for high availability |
| IP prefix | P3 | Use public IP prefix for contiguous ranges for partner allow-listing |
| DNS label | P3 | Configure DNS labels for stable FQDNs where needed |
| Orphan cleanup | P2 | Review and delete unattached public IPs to reduce cost |
| Diagnostic logging | P2 | Enable DDoS mitigation flow logs and metrics |
| IPv6 dual-stack | P3 | Add IPv6 public IPs for dual-stack connectivity if required |
| Tagging | P3 | Ensure all public IPs are tagged with owning service for cost tracking |
