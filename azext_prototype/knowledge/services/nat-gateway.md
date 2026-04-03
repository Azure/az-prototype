# Azure NAT Gateway
> Fully managed, highly resilient outbound-only network address translation service providing predictable SNAT ports and static public IP addresses for outbound internet connectivity.

## When to Use

- **Predictable outbound IPs** -- when downstream services whitelist specific IP addresses
- **SNAT port exhaustion prevention** -- replaces default outbound access with dedicated SNAT ports
- **Standard Load Balancer backends** -- VMs behind Standard LB need explicit outbound connectivity
- **Container-based workloads** -- Container Apps and AKS nodes making many outbound connections
- **API integrations** -- calling third-party APIs that require IP-based allow-listing
- NOT suitable for: inbound traffic (use Load Balancer or Application Gateway) or cross-region scenarios

NAT Gateway is a **subnet-level** service -- associate it with subnets that need outbound internet access.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Standard | Only available SKU |
| Idle timeout | 4 minutes | Default; increase for long-lived connections |
| Public IPs | 1 | Each IP provides ~64,000 SNAT ports |
| Public IP prefixes | None | Use for contiguous IP ranges |
| Availability zones | Zone-redundant | Automatic in supported regions |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "nat_pip" {
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

resource "azapi_resource" "nat_gateway" {
  type      = "Microsoft.Network/natGateways@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard"
    }
    properties = {
      idleTimeoutInMinutes = 4
      publicIpAddresses = [
        {
          id = azapi_resource.nat_pip.id
        }
      ]
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}
```

### Associate NAT Gateway with Subnet

```hcl
# Associate NAT Gateway with a workload subnet
resource "azapi_update_resource" "subnet_nat" {
  type        = "Microsoft.Network/virtualNetworks/subnets@2024-01-01"
  resource_id = var.workload_subnet_id

  body = {
    properties = {
      addressPrefix = var.workload_subnet_prefix
      natGateway = {
        id = azapi_resource.nat_gateway.id
      }
    }
  }
}
```

### Multiple Public IPs for Scale

```hcl
resource "azapi_resource" "nat_pips" {
  for_each = toset(["1", "2", "3"])

  type      = "Microsoft.Network/publicIPAddresses@2024-01-01"
  name      = "pip-${var.name}-${each.key}"
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

resource "azapi_resource" "nat_gateway_scaled" {
  type      = "Microsoft.Network/natGateways@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard"
    }
    properties = {
      idleTimeoutInMinutes = 10
      publicIpAddresses = [
        for pip in azapi_resource.nat_pips : {
          id = pip.id
        }
      ]
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# Network Contributor for NAT Gateway management
resource "azapi_resource" "nat_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.nat_gateway.id}-${var.admin_principal_id}-network-contributor")
  parent_id = azapi_resource.nat_gateway.id

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

NAT Gateway does not use private endpoints -- it provides outbound internet connectivity for subnets. It operates transparently at the subnet level.

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the NAT Gateway')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Idle timeout in minutes')
param idleTimeoutInMinutes int = 4

@description('Tags to apply')
param tags object = {}

resource natPip 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
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

resource natGateway 'Microsoft.Network/natGateways@2024-01-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    idleTimeoutInMinutes: idleTimeoutInMinutes
    publicIpAddresses: [
      {
        id: natPip.id
      }
    ]
  }
}

output id string = natGateway.id
output publicIpAddress string = natPip.properties.ipAddress
```

### Subnet Association

```bicep
@description('Existing VNet name')
param vnetName string

@description('Subnet name to associate')
param subnetName string

@description('Subnet address prefix')
param subnetPrefix string

resource subnet 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' = {
  name: '${vnetName}/${subnetName}'
  properties: {
    addressPrefix: subnetPrefix
    natGateway: {
      id: natGateway.id
    }
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Not associating with subnet | NAT Gateway has no effect until linked to a subnet | Explicitly update subnet properties with `natGateway.id` |
| Conflict with LB outbound rules | Both NAT Gateway and LB outbound rules defined | NAT Gateway takes precedence; remove LB outbound rules to avoid confusion |
| Using Basic SKU public IPs | Deployment fails; NAT Gateway requires Standard | Always use Standard SKU public IPs |
| Idle timeout too short | Long-running HTTP connections or downloads fail | Increase `idleTimeoutInMinutes` for workloads with long connections |
| Not enough public IPs | SNAT port exhaustion under high concurrency | Each public IP provides ~64K ports; add more IPs for scale |
| AzureBastionSubnet association | Bastion does not support NAT Gateway | Do not associate NAT Gateway with the AzureBastionSubnet |
| AzureFirewallSubnet association | Firewall does not support NAT Gateway | Do not associate NAT Gateway with the AzureFirewallSubnet |
| Gateway subnet association | VPN/ExpressRoute gateways have their own outbound path | Do not associate NAT Gateway with the GatewaySubnet |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Multiple public IPs | P2 | Add public IPs based on outbound connection requirements (~64K ports per IP) |
| Public IP prefix | P3 | Use a public IP prefix for contiguous IP ranges for firewall allow-listing |
| Idle timeout tuning | P3 | Adjust idle timeout based on observed connection patterns |
| Monitoring | P2 | Enable NAT Gateway metrics (SNAT port usage, dropped packets) in Azure Monitor |
| Subnet coverage | P1 | Ensure all workload subnets requiring outbound access have NAT Gateway associated |
| Documentation | P3 | Document outbound public IPs for third-party API allow-listing |
| Diagnostic logging | P2 | Enable resource logs for connection tracking and troubleshooting |
| Cost review | P3 | Review NAT Gateway costs vs. LB outbound rules for cost optimization |
