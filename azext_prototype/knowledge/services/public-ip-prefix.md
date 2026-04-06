---
service_namespace: Microsoft.Network/publicIPPrefixes
display_name: Public IP Prefix
---

# Public IP Prefix

> Contiguous range of static public IP addresses reserved from Azure's pool, enabling predictable outbound IP ranges for firewall allowlisting and consistent NAT gateway addressing.

## When to Use
- **NAT Gateway** -- assign a prefix to a NAT gateway for predictable outbound SNAT IPs from a known contiguous range
- **Firewall allowlisting** -- when partner or customer firewalls need a known, stable IP range to allowlist
- **Load balancer frontends** -- allocate multiple public IPs from a single prefix for load balancer rules
- **Azure Firewall** -- use a prefix for outbound SNAT with predictable IP ranges

Public IP prefixes guarantee contiguous addresses. Individual public IPs allocated from a prefix share the same range and can be referenced by firewall rules on partner systems.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Prefix length | /31 | 2 IPs; smallest useful range for POC |
| SKU | Standard | Only Standard is supported |
| Tier | Regional | Global for cross-region load balancing |
| IP version | IPv4 | IPv6 prefixes also available |
| Zone | Zone-redundant | For availability; or specific zone |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "ip_prefix" {
  type      = "Microsoft.Network/publicIPPrefixes@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard"
      tier = "Regional"
    }
    properties = {
      prefixLength           = var.prefix_length  # /28 = 16 IPs, /31 = 2 IPs
      publicIPAddressVersion = "IPv4"
    }
    zones = var.availability_zones  # e.g., ["1", "2", "3"]
  }

  tags = var.tags

  response_export_values = ["properties.ipPrefix"]
}
```

### RBAC Assignment

```hcl
# Network Contributor on the resource group covers public IP prefix management.
# Role ID: 4d97b98b-1d4f-4787-a291-c67834d212e7
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the public IP prefix')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Prefix length (e.g., 28 for 16 IPs, 31 for 2 IPs)')
@minValue(21)
@maxValue(31)
param prefixLength int = 31

param tags object = {}

resource ipPrefix 'Microsoft.Network/publicIPPrefixes@2024-01-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Regional'
  }
  properties: {
    prefixLength: prefixLength
    publicIPAddressVersion: 'IPv4'
  }
  zones: ['1', '2', '3']
}

output id string = ipPrefix.id
output ipPrefix string = ipPrefix.properties.ipPrefix
```

## Application Code

### Python
Infrastructure -- transparent to application code. Public IP prefixes define network addressing; applications are unaware of the specific outbound IP addresses.

### C#
Infrastructure -- transparent to application code. Public IP prefixes define network addressing; applications are unaware of the specific outbound IP addresses.

### Node.js
Infrastructure -- transparent to application code. Public IP prefixes define network addressing; applications are unaware of the specific outbound IP addresses.

## Common Pitfalls

1. **Prefix length is immutable** -- Cannot resize a prefix after creation. If you need more IPs, create a new prefix. Plan capacity upfront.
2. **Only Standard SKU** -- Public IP prefixes only work with Standard SKU public IPs. Basic SKU IPs cannot be derived from a prefix.
3. **Region-locked** -- A prefix is tied to a region. Public IPs derived from it must be in the same region.
4. **Cost accrues immediately** -- You pay for all IPs in the prefix whether or not they are allocated to resources. A /28 prefix (16 IPs) costs 16x a single public IP.
5. **Deletion requires all IPs released** -- Cannot delete a prefix while any public IP derived from it is still in use. Deallocate all child IPs first.
6. **NAT Gateway limit** -- A NAT gateway supports up to 16 public IPs or prefixes. A /28 prefix counts as one, but a single prefix can provide up to 16 IPs.
7. **Zone selection is permanent** -- The availability zone assignment cannot be changed after creation. Zone-redundant is the safest default.

## Production Backlog Items

- [ ] Right-size prefix length based on expected outbound IP requirements
- [ ] Document the IP prefix range and share with partners for firewall allowlisting
- [ ] Configure DDoS Protection Standard on public IPs derived from the prefix
- [ ] Set up monitoring for IP allocation from the prefix
- [ ] Plan for IPv6 dual-stack prefix if required
- [ ] Evaluate Global tier for cross-region load balancing scenarios
