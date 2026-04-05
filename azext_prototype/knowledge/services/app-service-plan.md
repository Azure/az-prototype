---
service_namespace: Microsoft.Web/serverfarms
display_name: App Service Plan
---

# App Service Plan

> Defines the compute resources (SKU, OS, scaling) that host Azure App Service and Function App instances.

## When to Use
- Required parent resource for all App Service web apps and Function Apps
- Defines the pricing tier, OS, and scaling configuration
- Multiple apps can share a single plan for cost efficiency

## POC Defaults
- **OS**: Linux (preferred for Python/Node); Windows for .NET Framework
- **SKU**: B1 (Basic) for realistic POC; F1 (Free) for minimal testing
- **Always On**: Enabled on B1+ (not available on F1/Consumption)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "plan" {
  type      = "Microsoft.Web/serverfarms@2023-12-01"
  name      = var.plan_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    kind = "linux"
    sku = {
      name = "B1"
      tier = "Basic"
    }
    properties = {
      reserved = true   # Required for Linux
    }
  }

  tags = var.tags
}
```

### Functions Consumption Plan
```hcl
resource "azapi_resource" "functions_plan" {
  type      = "Microsoft.Web/serverfarms@2023-12-01"
  name      = var.plan_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    kind = "functionapp"
    sku = {
      name = "Y1"
      tier = "Dynamic"
    }
    properties = {
      reserved = true
    }
  }

  tags = var.tags
}
```

## Bicep Patterns

### Basic Resource
```bicep
param planName string
param location string = resourceGroup().location
param tags object = {}

resource servicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  kind: 'linux'
  sku: {
    name: 'B1'
    tier: 'Basic'
  }
  properties: {
    reserved: true
  }
  tags: tags
}

output planId string = servicePlan.id
output planName string = servicePlan.name
```

## Common Pitfalls
- **`reserved = true` for Linux**: Linux plans MUST set `reserved = true` or the plan defaults to Windows.
- **Free tier limitations**: F1 (Free) does not support Always On, custom domains, or TLS certificates.
- **Shared plans**: Multiple apps on one plan share CPU/memory. High-traffic apps should have dedicated plans.

## Production Backlog Items
- Premium V3 plan for production workloads with predictable performance
- Autoscale rules based on CPU, memory, or HTTP queue length
- Zone redundancy for high availability
