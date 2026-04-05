---
service_namespace: Microsoft.App/managedEnvironments
display_name: Container Apps Environment
depends_on:
  - Microsoft.OperationalInsights/workspaces
---

# Container Apps Environment

> Shared hosting environment for Azure Container Apps that provides networking, logging, and Dapr configuration.

## When to Use
- Required parent resource for all Container Apps
- Provides shared VNet integration, Log Analytics, and Dapr configuration
- One environment per application group (microservices that communicate)

## POC Defaults
- **Plan**: Consumption (serverless, pay-per-use)
- **VNet integration**: Recommended (requires /23 subnet minimum)
- **Log Analytics**: Required — environment cannot be created without it

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "container_app_env" {
  type      = "Microsoft.App/managedEnvironments@2024-03-01"
  name      = var.environment_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      appLogsConfiguration = {
        destination = "log-analytics"
        logAnalyticsConfiguration = {
          customerId = var.log_analytics_customer_id
          sharedKey  = var.log_analytics_shared_key
        }
      }
    }
  }

  tags = var.tags
  response_export_values = ["*"]
}
```

### VNet-Integrated Environment
```hcl
resource "azapi_resource" "container_app_env" {
  type      = "Microsoft.App/managedEnvironments@2024-03-01"
  name      = var.environment_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      vnetConfiguration = {
        infrastructureSubnetId = var.container_apps_subnet_id
        internal               = false   # true = internal only (no public ingress)
      }
      appLogsConfiguration = {
        destination = "log-analytics"
        logAnalyticsConfiguration = {
          customerId = var.log_analytics_customer_id
          sharedKey  = var.log_analytics_shared_key
        }
      }
    }
  }

  tags = var.tags
  response_export_values = ["*"]
}
```

## Bicep Patterns

### Basic Resource
```bicep
param environmentName string
param location string = resourceGroup().location
param logAnalyticsCustomerId string
@secure()
param logAnalyticsSharedKey string
param tags object = {}

resource containerAppEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
  }
  tags: tags
}

output environmentId string = containerAppEnv.id
output defaultDomain string = containerAppEnv.properties.defaultDomain
output staticIp string = containerAppEnv.properties.staticIp
```

## Common Pitfalls
- **Log Analytics required**: The environment CANNOT be created without a Log Analytics workspace. Ensure the workspace exists before creating the environment.
- **Subnet sizing**: VNet-integrated subnets must be at least /23 (512 addresses). A /27 or /28 will fail. The subnet must be delegated to `Microsoft.App/environments`.
- **Log Analytics shared key retrieval**: Use `data "azapi_resource_action"` (not `resource`) for read-only operations like fetching the shared key. Using `resource` causes re-execution on every apply.
- **Internal vs external**: Setting `internal = true` disables all public ingress to ALL apps in the environment. Individual apps cannot override this.

## Production Backlog Items
- Dedicated workload profile plan for predictable performance and reserved capacity
- Custom VNET configuration with internal-only access and private DNS zones
- Dapr component configuration for service-to-service communication
- Zone redundancy for high availability
