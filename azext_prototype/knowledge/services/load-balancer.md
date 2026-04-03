# Azure Load Balancer
> High-performance, ultra-low-latency Layer 4 (TCP/UDP) load balancer for distributing traffic across virtual machines, VM scale sets, and availability sets within a region.

## When to Use

- **TCP/UDP load balancing** -- distribute non-HTTP traffic (databases, custom TCP services, gaming servers)
- **VM-based architectures** -- load balance across VMs or VM scale sets
- **Internal service tiers** -- internal load balancer for private backend communication between tiers
- **High-throughput, low-latency** -- millions of flows per second with minimal latency overhead
- **HA ports** -- load balance all ports/protocols simultaneously for network virtual appliances
- NOT suitable for: HTTP/HTTPS routing (use Application Gateway), global distribution (use Front Door or Traffic Manager), or PaaS services (Container Apps, App Service have built-in LB)

Choose Load Balancer for L4 traffic. Choose Application Gateway for L7 HTTP routing with SSL termination.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Standard | Basic is deprecated for new deployments |
| Type | Public or Internal | Internal for private backend tiers |
| Frontend IP | Static | Dynamic not supported on Standard SKU |
| Health probe | TCP or HTTP | HTTP preferred for application-level health |
| Session persistence | None | Client IP-based if sticky sessions needed |
| Outbound rules | Configured | Required for Standard LB outbound connectivity |

## Terraform Patterns

### Basic Resource (Public)

```hcl
resource "azapi_resource" "lb_pip" {
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

resource "azapi_resource" "load_balancer" {
  type      = "Microsoft.Network/loadBalancers@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard"
    }
    properties = {
      frontendIPConfigurations = [
        {
          name = "lb-frontend"
          properties = {
            publicIPAddress = {
              id = azapi_resource.lb_pip.id
            }
          }
        }
      ]
      backendAddressPools = [
        {
          name = "lb-backend-pool"
        }
      ]
      loadBalancingRules = [
        {
          name = "lb-rule-http"
          properties = {
            frontendIPConfiguration = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/loadBalancers/${var.name}/frontendIPConfigurations/lb-frontend"
            }
            backendAddressPool = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/loadBalancers/${var.name}/backendAddressPools/lb-backend-pool"
            }
            probe = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/loadBalancers/${var.name}/probes/health-probe"
            }
            protocol               = "Tcp"
            frontendPort           = 80
            backendPort            = 80
            enableFloatingIP       = false
            idleTimeoutInMinutes   = 4
            loadDistribution       = "Default"
            disableOutboundSnat    = true  # Use explicit outbound rules
          }
        }
      ]
      probes = [
        {
          name = "health-probe"
          properties = {
            protocol            = "Http"
            port                = 80
            requestPath         = "/health"
            intervalInSeconds   = 15
            numberOfProbes      = 2
            probeThreshold      = 1
          }
        }
      ]
      outboundRules = [
        {
          name = "outbound-rule"
          properties = {
            frontendIPConfigurations = [
              {
                id = "${var.resource_group_id}/providers/Microsoft.Network/loadBalancers/${var.name}/frontendIPConfigurations/lb-frontend"
              }
            ]
            backendAddressPool = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/loadBalancers/${var.name}/backendAddressPools/lb-backend-pool"
            }
            protocol               = "All"
            idleTimeoutInMinutes   = 4
            allocatedOutboundPorts = 1024
          }
        }
      ]
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}
```

### Internal Load Balancer

```hcl
resource "azapi_resource" "internal_lb" {
  type      = "Microsoft.Network/loadBalancers@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard"
    }
    properties = {
      frontendIPConfigurations = [
        {
          name = "lb-frontend-internal"
          properties = {
            subnet = {
              id = var.subnet_id
            }
            privateIPAllocationMethod = "Static"
            privateIPAddress          = var.private_ip  # e.g., "10.0.1.10"
          }
        }
      ]
      backendAddressPools = [
        {
          name = "lb-backend-pool"
        }
      ]
      loadBalancingRules = [
        {
          name = "lb-rule-tcp"
          properties = {
            frontendIPConfiguration = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/loadBalancers/${var.name}/frontendIPConfigurations/lb-frontend-internal"
            }
            backendAddressPool = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/loadBalancers/${var.name}/backendAddressPools/lb-backend-pool"
            }
            probe = {
              id = "${var.resource_group_id}/providers/Microsoft.Network/loadBalancers/${var.name}/probes/health-probe"
            }
            protocol             = "Tcp"
            frontendPort         = var.frontend_port
            backendPort          = var.backend_port
            enableFloatingIP     = false
            idleTimeoutInMinutes = 4
            loadDistribution     = "Default"
          }
        }
      ]
      probes = [
        {
          name = "health-probe"
          properties = {
            protocol          = "Tcp"
            port              = var.backend_port
            intervalInSeconds = 15
            numberOfProbes    = 2
            probeThreshold    = 1
          }
        }
      ]
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# Network Contributor for load balancer management
resource "azapi_resource" "lb_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.load_balancer.id}-${var.admin_principal_id}-network-contributor")
  parent_id = azapi_resource.load_balancer.id

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

Azure Load Balancer does not use private endpoints. Internal Load Balancer is inherently private -- it is placed in a VNet subnet with a private frontend IP.

## Bicep Patterns

### Basic Resource (Public)

```bicep
@description('Name of the Load Balancer')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Tags to apply')
param tags object = {}

resource lbPip 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
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

resource loadBalancer 'Microsoft.Network/loadBalancers@2024-01-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    frontendIPConfigurations: [
      {
        name: 'lb-frontend'
        properties: {
          publicIPAddress: {
            id: lbPip.id
          }
        }
      }
    ]
    backendAddressPools: [
      {
        name: 'lb-backend-pool'
      }
    ]
    loadBalancingRules: [
      {
        name: 'lb-rule-http'
        properties: {
          frontendIPConfiguration: {
            id: resourceId('Microsoft.Network/loadBalancers/frontendIPConfigurations', name, 'lb-frontend')
          }
          backendAddressPool: {
            id: resourceId('Microsoft.Network/loadBalancers/backendAddressPools', name, 'lb-backend-pool')
          }
          probe: {
            id: resourceId('Microsoft.Network/loadBalancers/probes', name, 'health-probe')
          }
          protocol: 'Tcp'
          frontendPort: 80
          backendPort: 80
          enableFloatingIP: false
          idleTimeoutInMinutes: 4
          disableOutboundSnat: true
        }
      }
    ]
    probes: [
      {
        name: 'health-probe'
        properties: {
          protocol: 'Http'
          port: 80
          requestPath: '/health'
          intervalInSeconds: 15
          numberOfProbes: 2
          probeThreshold: 1
        }
      }
    ]
    outboundRules: [
      {
        name: 'outbound-rule'
        properties: {
          frontendIPConfigurations: [
            {
              id: resourceId('Microsoft.Network/loadBalancers/frontendIPConfigurations', name, 'lb-frontend')
            }
          ]
          backendAddressPool: {
            id: resourceId('Microsoft.Network/loadBalancers/backendAddressPools', name, 'lb-backend-pool')
          }
          protocol: 'All'
          idleTimeoutInMinutes: 4
          allocatedOutboundPorts: 1024
        }
      }
    ]
  }
}

output id string = loadBalancer.id
output frontendIpId string = loadBalancer.properties.frontendIPConfigurations[0].id
output backendPoolId string = loadBalancer.properties.backendAddressPools[0].id
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Using Basic SKU | Basic is deprecated, no SLA, no availability zones | Always use Standard SKU for new deployments |
| No outbound rule on Standard LB | VMs behind Standard LB lose default outbound internet | Configure explicit outbound rule or use NAT Gateway |
| Health probe on wrong port/path | All backends marked unhealthy; traffic stops | Verify probe endpoint returns HTTP 200 and is reachable |
| Mixing Basic and Standard resources | Deployment fails; Basic and Standard cannot be mixed | Ensure all resources (LB, PIPs, VMs) are same SKU tier |
| Not disabling SNAT on LB rules | Port exhaustion when outbound rules also configured | Set `disableOutboundSnat = true` on LB rules when using outbound rules |
| Session persistence misconfiguration | Stateful apps fail with random distribution | Use `ClientIP` or `ClientIPProtocol` for sticky sessions |
| Idle timeout too short | Long-running connections dropped | Increase `idleTimeoutInMinutes` (max 30) or enable TCP keepalives |
| Forgetting backend pool association | VMs not receiving traffic | Associate VM NICs with the backend pool |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Availability zones | P1 | Deploy zone-redundant LB with zone-redundant frontend IP |
| Multiple frontend IPs | P3 | Add frontend IPs for different services or SNAT capacity |
| Cross-region LB | P2 | Deploy Global tier for cross-region failover (replaces Traffic Manager for L4) |
| Diagnostic logging | P2 | Enable load balancer metrics and health probe logs to Log Analytics |
| NAT Gateway for outbound | P2 | Replace outbound rules with NAT Gateway for predictable SNAT |
| HA Ports rule | P3 | Configure HA ports for NVA scenarios (all ports/protocols) |
| Connection draining | P2 | Configure idle timeout and TCP reset for graceful connection handling |
| Backend pool scaling | P3 | Integrate with VM scale sets for auto-scaling backend pools |
| Health probe refinement | P2 | Switch from TCP to HTTP probes with application-level health checks |
| Inbound NAT rules | P3 | Configure port-based NAT for direct VM access if needed |
