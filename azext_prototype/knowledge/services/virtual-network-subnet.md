---
service_namespace: Microsoft.Network/virtualNetworks/subnets
display_name: Virtual Network Subnet
depends_on:
  - Microsoft.Network/virtualNetworks
---

# Virtual Network Subnet

> An IP address range within a VNet that segments network traffic and hosts Azure resources. Subnets enable network isolation, NSG attachment, and service delegation.

## When to Use
- Every VNet-integrated resource needs a subnet
- Separate subnets for compute, data, private endpoints, and gateways
- Subnet delegation required for certain services (Container Apps, PostgreSQL Flexible)

## POC Defaults
- **Compute subnet**: /23 (Container Apps require minimum /23)
- **Data subnet**: /24 (general purpose)
- **Private endpoint subnet**: /24
- **Gateway subnet**: /27 (minimum for VPN/ExpressRoute gateways)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "subnet" {
  type      = "Microsoft.Network/virtualNetworks/subnets@2024-01-01"
  name      = var.subnet_name
  parent_id = azapi_resource.virtual_network.id

  body = {
    properties = {
      addressPrefix = var.address_prefix
      networkSecurityGroup = {
        id = azapi_resource.nsg.id
      }
    }
  }
}
```

### Delegated Subnet (Container Apps)
```hcl
resource "azapi_resource" "subnet_container_apps" {
  type      = "Microsoft.Network/virtualNetworks/subnets@2024-01-01"
  name      = "snet-container-apps"
  parent_id = azapi_resource.virtual_network.id

  body = {
    properties = {
      addressPrefix = "10.0.16.0/23"
      delegations = [
        {
          name = "container-apps"
          properties = {
            serviceName = "Microsoft.App/environments"
          }
        }
      ]
    }
  }
}
```

### RBAC Assignment
```hcl
# Subnet access is typically inherited from the VNet-level RBAC.
# Network Contributor role at VNet or subnet scope for management.
```

## Bicep Patterns

### Basic Resource
```bicep
param subnetName string
param addressPrefix string
param nsgId string

resource subnet 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' = {
  parent: virtualNetwork
  name: subnetName
  properties: {
    addressPrefix: addressPrefix
    networkSecurityGroup: {
      id: nsgId
    }
  }
}

output subnetId string = subnet.id
output subnetName string = subnet.name
```

## Application Code

### Python
```python
# Subnets are infrastructure — no direct application code.
# Applications connect to resources within subnets via private endpoints
# or VNet integration. The subnet configuration is transparent to app code.
```

### C#
```csharp
// Subnets are infrastructure — no direct application code.
```

### Node.js
```typescript
// Subnets are infrastructure — no direct application code.
```

## Common Pitfalls
- **Container Apps require /23 minimum**: A /24 or smaller subnet will fail for Container Apps Environment deployment.
- **NSG must be attached**: Subnets without NSGs have no traffic filtering. Always attach an NSG.
- **Delegation conflicts**: A subnet can only be delegated to one service type. You cannot share a delegated subnet across different service types.
- **GatewaySubnet naming**: VPN and ExpressRoute gateway subnets MUST be named exactly `GatewaySubnet`.
- **Address space overlap**: Subnet address prefixes must not overlap with each other or with the VNet's address space boundaries.
- **Sequential creation**: Azure processes subnet creation sequentially. Creating multiple subnets in parallel may fail with conflict errors. Use `depends_on` to serialize.

## Production Backlog Items
- Service endpoints for direct PaaS access without private endpoints
- Route table association for custom traffic routing
- Network security group flow logs for traffic analysis
- Subnet-level network policies for private endpoints
