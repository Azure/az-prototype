# Azure Container Instances
> Serverless container platform for running isolated containers on demand without managing VMs or orchestrators, ideal for burst workloads, batch jobs, and simple container deployments.

## When to Use

- **Quick container deployment** -- run a container image without provisioning VMs or Kubernetes clusters
- **Batch processing** -- short-lived jobs that process data and exit (ETL, data migration, report generation)
- **CI/CD build agents** -- ephemeral build/test runners
- **Sidecar containers** -- multi-container groups for init containers, log shippers, or proxies
- **Dev/test environments** -- quick spin-up of containerized applications for testing
- **Event-driven containers** -- trigger container execution from Logic Apps, Functions, or Event Grid

Prefer Container Instances over Container Apps when you need simple, short-lived container execution without scaling, ingress routing, or Dapr integration. Use Container Apps for long-running microservices with auto-scaling. Use AKS for complex multi-service orchestration.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| OS type | Linux | Default; Windows available for .NET Framework |
| CPU | 1 core | Sufficient for most POC workloads |
| Memory | 1.5 GiB | Default allocation |
| Restart policy | OnFailure | Restart only on failure; use "Never" for batch jobs |
| IP address type | Public | Flag private (VNet) deployment as production backlog item |
| Image source | ACR with managed identity | No admin credentials needed |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "container_group" {
  type      = "Microsoft.ContainerInstance/containerGroups@2023-05-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type         = "UserAssigned"
    identity_ids = [var.managed_identity_id]
  }

  body = {
    properties = {
      osType        = "Linux"
      restartPolicy = "OnFailure"
      ipAddress = {
        type  = "Public"
        ports = [
          {
            protocol = "TCP"
            port     = 80
          }
        ]
      }
      imageRegistryCredentials = [
        {
          server   = var.acr_login_server
          identity = var.managed_identity_id
        }
      ]
      containers = [
        {
          name = var.container_name
          properties = {
            image = "${var.acr_login_server}/${var.image_name}:${var.image_tag}"
            resources = {
              requests = {
                cpu        = 1
                memoryInGB = 1.5
              }
            }
            ports = [
              {
                protocol = "TCP"
                port     = 80
              }
            ]
            environmentVariables = [
              {
                name  = "AZURE_CLIENT_ID"
                value = var.managed_identity_client_id
              }
            ]
          }
        }
      ]
    }
  }

  tags = var.tags

  response_export_values = ["properties.ipAddress.ip", "properties.ipAddress.fqdn"]
}
```

### Multi-Container Group (Sidecar Pattern)

```hcl
resource "azapi_resource" "container_group_sidecar" {
  type      = "Microsoft.ContainerInstance/containerGroups@2023-05-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      osType        = "Linux"
      restartPolicy = "Always"
      containers = [
        {
          name = "app"
          properties = {
            image = "${var.acr_login_server}/${var.app_image}:latest"
            resources = {
              requests = {
                cpu        = 1
                memoryInGB = 1
              }
            }
            ports = [
              {
                protocol = "TCP"
                port     = 80
              }
            ]
          }
        },
        {
          name = "log-shipper"
          properties = {
            image = "${var.acr_login_server}/${var.sidecar_image}:latest"
            resources = {
              requests = {
                cpu        = 0.5
                memoryInGB = 0.5
              }
            }
          }
        }
      ]
      ipAddress = {
        type  = "Public"
        ports = [
          {
            protocol = "TCP"
            port     = 80
          }
        ]
      }
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# ACI's managed identity accessing ACR for image pull
resource "azapi_resource" "acr_pull_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.acr_id}${var.managed_identity_principal_id}acr-pull")
  parent_id = var.acr_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"  # AcrPull
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the container group')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Container image (including registry)')
param image string

@description('ACR login server')
param acrLoginServer string

@description('Managed identity resource ID')
param managedIdentityId string

@description('Managed identity client ID')
param managedIdentityClientId string

@description('Tags to apply')
param tags object = {}

resource containerGroup 'Microsoft.ContainerInstance/containerGroups@2023-05-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  properties: {
    osType: 'Linux'
    restartPolicy: 'OnFailure'
    imageRegistryCredentials: [
      {
        server: acrLoginServer
        identity: managedIdentityId
      }
    ]
    containers: [
      {
        name: 'main'
        properties: {
          image: image
          resources: {
            requests: {
              cpu: 1
              memoryInGB: json('1.5')
            }
          }
          ports: [
            {
              protocol: 'TCP'
              port: 80
            }
          ]
          environmentVariables: [
            {
              name: 'AZURE_CLIENT_ID'
              value: managedIdentityClientId
            }
          ]
        }
      }
    ]
    ipAddress: {
      type: 'Public'
      ports: [
        {
          protocol: 'TCP'
          port: 80
        }
      ]
    }
  }
}

output id string = containerGroup.id
output name string = containerGroup.name
output ipAddress string = containerGroup.properties.ipAddress.ip
```

### RBAC Assignment

```bicep
@description('Principal ID of the managed identity')
param principalId string

// AcrPull -- allow container group to pull images from ACR
resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, principalId, '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')  // AcrPull
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Fix |
|---------|--------|-----|
| Using admin credentials for ACR | Secrets in config, rotation burden | Use managed identity with AcrPull role and `imageRegistryCredentials.identity` |
| Container group immutability | Cannot update containers in-place; must delete and recreate | Design for immutable deployments; use deployment scripts or CI/CD |
| No auto-scaling | ACI does not scale horizontally; fixed resource allocation | Use Container Apps for auto-scaling scenarios |
| Public IP without authentication | Container exposed to internet without auth | Add application-level authentication or use VNet deployment |
| Exceeding resource limits | Max 4 CPU, 16 GiB memory per container group | Split into multiple container groups or use AKS for larger workloads |
| Restart policy mismatch | `Always` for batch jobs wastes resources; `Never` for services loses availability | Use `OnFailure` for services, `Never` for batch jobs |
| Forgetting port alignment | IP address ports must match container ports | Ensure `ipAddress.ports` and `containers[].ports` are consistent |

## Production Backlog Items

- [ ] Deploy into VNet subnet for private networking
- [ ] Configure diagnostic logging to Log Analytics workspace
- [ ] Set up monitoring alerts (CPU usage, memory usage, restart count)
- [ ] Move to Container Apps or AKS for production auto-scaling and ingress management
- [ ] Configure liveness and readiness probes
- [ ] Review CPU and memory allocations based on actual usage
- [ ] Implement secure environment variables (use `secureValue` for secrets)
- [ ] Set up Azure Monitor container insights
- [ ] Consider GPU-enabled container groups for ML inference workloads
