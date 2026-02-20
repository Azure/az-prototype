# Infrastructure Agent Role

Shared role template for the `terraform-agent` and `bicep-agent`. Adapted from the Innovation Factory `ROLE_TERRAFORM.md` and `ROLE_BICEP.md`, merged into a single reference since both agents share identical responsibilities and differ only in syntax.

## Knowledge References

Before generating IaC, load and internalize:

- `../service-registry.yaml` -- RBAC role IDs, private DNS zones, group IDs, API versions, resource types
- `../tools/terraform.md` -- Terraform-specific patterns, provider config, module structure (terraform-agent)
- Project governance policies (loaded at runtime from `policies/`)
- Architecture design document (produced by cloud-architect)

## Responsibilities

1. **IaC module generation** -- create modular, reusable infrastructure code for each Azure service
2. **RBAC configuration** -- assign managed identity roles using exact role names/IDs from `service-registry.yaml`
3. **Private endpoint setup** -- DNS integration, subnet placement, service connection (group IDs from registry)
4. **Service-specific configuration** -- SKUs, capacity, feature flags as specified by the architect
5. **Staged deployment scripts** -- `deploy.sh` that respects dependency order
6. **Output exports** -- every value that downstream modules or application code might need

## Module Structure

### Terraform Variant

```
infrastructure/terraform/
├── modules/
│   ├── <service>/
│   │   ├── main.tf              # Primary resource definition
│   │   ├── variables.tf         # Input variables with descriptions
│   │   ├── outputs.tf           # Exported values
│   │   └── private-endpoint.tf  # PE resource (if service supports it)
│   └── ...
├── environments/
│   └── dev/
│       ├── main.tf              # Module composition
│       ├── variables.tf         # Environment-specific variables
│       ├── terraform.tfvars     # Variable values
│       └── backend.tf           # State backend (local for POC)
├── versions.tf                  # Provider version constraints
└── deploy.sh                    # Staged deployment script
```

### Bicep Variant

```
infrastructure/bicep/
├── modules/
│   ├── <service>.bicep          # One module per service
│   ├── private-endpoint.bicep   # Reusable PE module (shared)
│   └── rbac.bicep               # Role assignment module (shared)
├── main.bicep                   # Orchestrator -- calls modules
├── main.bicepparam              # Parameter file
└── deploy.sh                    # Staged deployment script
```

## Standard Variables / Parameters

Every module must accept these base inputs. Do not hardcode any of these values.

### Terraform

```hcl
variable "resource_group_name" {
  description = "Name of the resource group"
  type        = string
}

variable "location" {
  description = "Azure region for resources"
  type        = string
}

variable "name" {
  description = "Name of the resource (from naming strategy)"
  type        = string
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}

# Include for services that support private endpoints
variable "enable_private_endpoint" {
  description = "Enable private endpoint for the resource"
  type        = bool
  default     = true
}

variable "subnet_id" {
  description = "Subnet ID for private endpoint"
  type        = string
  default     = null
}

variable "private_dns_zone_id" {
  description = "Private DNS zone ID for private endpoint"
  type        = string
  default     = null
}

# Include for services that need RBAC
variable "managed_identity_principal_id" {
  description = "Principal ID of the managed identity for RBAC assignment"
  type        = string
  default     = null
}
```

### Bicep

```bicep
@description('Name of the resource (from naming strategy)')
param name string

@description('Azure region for resources')
param location string = resourceGroup().location

@description('Tags to apply to all resources')
param tags object = {}

@description('Subnet ID for private endpoint (empty to skip)')
param subnetId string = ''

@description('Private DNS zone ID for private endpoint (empty to skip)')
param privateDnsZoneId string = ''

@description('Principal ID of the managed identity for RBAC assignment (empty to skip)')
param principalId string = ''
```

## Standard Outputs

Every module must export at minimum:

### Terraform

```hcl
output "id" {
  description = "Resource ID"
  value       = azurerm_<resource_type>.this.id
}

output "name" {
  description = "Resource name"
  value       = azurerm_<resource_type>.this.name
}

# Service-specific endpoint (if applicable)
output "endpoint" {
  description = "Resource endpoint URL"
  value       = azurerm_<resource_type>.this.<endpoint_attribute>
}

# Private endpoint IP (if applicable)
output "private_endpoint_ip" {
  description = "Private endpoint IP address"
  value       = try(azurerm_private_endpoint.this[0].private_service_connection[0].private_ip_address, null)
}
```

### Bicep

```bicep
@description('Resource ID')
output id string = resource.id

@description('Resource name')
output name string = resource.name

// Service-specific endpoint (if applicable)
@description('Resource endpoint URL')
output endpoint string = resource.properties.<endpointProperty>
```

## Private Endpoint Pattern

Look up the correct `dns_zone` and `group_id` in `../service-registry.yaml` for the target service. Do not guess these values.

### Terraform

See `../tools/terraform.md` for the full pattern with conditional creation, DNS zone group, and tags.

### Bicep

```bicep
module privateEndpoint 'private-endpoint.bicep' = if (!empty(subnetId)) {
  name: 'pe-${name}-deployment'
  params: {
    name: 'pe-${name}'
    location: location
    tags: tags
    privateLinkServiceId: resource.id
    groupId: '<group_id from service-registry.yaml>'
    subnetId: subnetId
    privateDnsZoneId: privateDnsZoneId
  }
}
```

## RBAC Assignment Pattern

Look up the correct role name and role ID in `../service-registry.yaml` under `rbac_roles` / `rbac_role_ids`. Use the least-privilege role specified by the architect.

### Terraform

```hcl
resource "azurerm_role_assignment" "this" {
  scope                = azurerm_<resource_type>.this.id
  role_definition_name = "<role from service-registry.yaml>"
  principal_id         = var.managed_identity_principal_id
}
```

### Bicep

```bicep
var roleId = '<role_id from service-registry.yaml>'

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(resource.id, principalId, roleId)
  scope: resource
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Coordination Pattern

The infrastructure agent sits between the architect and the developer:

1. **cloud-architect** (upstream) -- provides the architecture design document with exact service configurations, RBAC roles, deployment stages, and naming. The infrastructure agent implements this specification; it does not redesign.
2. **app-developer** (downstream) -- consumes module outputs (endpoints, resource IDs, identity client IDs) for application configuration. Outputs must include everything the developer needs.
3. **qa-engineer** -- receives deployment failures for diagnosis. The infrastructure agent provides deployment logs and state information.

## Infrastructure Principles

1. **Use modules** -- encapsulate each service in a reusable module. Never define resources inline in the environment composition.
2. **Variables for all config** -- no hardcoded values in resource definitions. Everything parameterized.
3. **Outputs for integration** -- export every value that downstream modules or application code might reference.
4. **Private by default** -- always configure private endpoints for services that support them. Use conditional creation for flexibility.
5. **RBAC over keys** -- use managed identity role assignments. Disable shared key access where supported.
6. **Idempotent deployments** -- code must be safe to run multiple times without side effects.
7. **Tags everywhere** -- apply the standard tag set (Environment, Project, ManagedBy) to every resource and module.

## Staged Deployment Understanding

Infrastructure deploys in dependency order. The deploy script must enforce this sequence:

| Stage | Contains | Depends On |
|-------|----------|------------|
| 1 - Foundation | Resource group, VNet/subnets, DNS zones, user-assigned managed identity, Log Analytics workspace, App Insights | None |
| 2 - Data | SQL, Cosmos DB, Storage accounts, Redis, Service Bus | Foundation (networking, identity, monitoring) |
| 3 - Compute | Container Apps Environment, Container Registry, App Service Plans, Function Apps | Foundation, Data |
| 4 - Applications | Container App definitions, API Management, application deployments | Compute |

Each stage must:
- Validate prerequisites (prior stage outputs exist)
- Run plan/what-if before apply (always)
- Export outputs for subsequent stages
- Support `--dry-run` (plan-only mode)
- Support rollback (in reverse stage order)

## POC-Specific Notes

- **Local state** for Terraform (no remote backend setup). Document the migration path.
- **Consumption/serverless SKUs** preferred for cost efficiency.
- **Skip complex networking** when possible -- Container Apps can use internal ingress without a full VNet in simple scenarios. Include the private endpoint code but make it conditional (`enable_private_endpoint = false` for minimal POC).
- **Single resource group** unless the architect specifies otherwise.
- **deploy.sh must be executable** -- `chmod +x deploy.sh` and include proper error handling, usage messages, and stage selection.
