# Terraform Patterns for Azure

Standard Terraform patterns for Azure resource deployment. **All Terraform agents must reference these patterns.** This extension deploys directly -- deployment commands are executed by the CLI, not handed to the user. Use `--dry-run` for plan-only previews.

## Project Structure

```
infrastructure/terraform/
├── modules/
│   ├── <service-name>/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   ├── outputs.tf
│   │   └── (NO private-endpoint.tf — PEs belong in Networking stage)
│   └── ...
├── environments/
│   ├── dev/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   ├── terraform.tfvars
│   │   └── backend.tf
│   └── prod/
│       └── ...
├── shared/
│   ├── providers.tf
│   └── versions.tf
└── deploy.sh                      # staged deployment script (see deploy-scripts.md)
```

## Provider Configuration

### versions.tf

```hcl
terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azapi = {
      source  = "hashicorp/azapi"
      version = ">= 2.0, < 3.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.0"
    }
  }
}
```

### providers.tf

**Note:** AzAPI v2 uses `body` as an HCL object (not a JSON string). All Azure resource properties are set via `body = { properties = { ... } }`. Resource types include an explicit API version in the `type` attribute (e.g., `Microsoft.Storage/storageAccounts@2023-05-01`).

```hcl
provider "azapi" {
  # Subscription is set via prototype.secrets.yaml or az account
  # subscription_id = var.subscription_id  # uncomment if needed
}

provider "azuread" {}
```

### AzAPI v2 Key Concepts

Key patterns for AzAPI v2 provider:

- Every resource uses `resource "azapi_resource"` with `type = "Microsoft.<Provider>/<ResourceType>@<api-version>"`
- Properties go in `body = { properties = { ... } }` as native HCL (not JSON strings)
- `parent_id` sets the parent scope (resource group ID, subscription ID, or parent resource ID)
- Output values accessed via `output` attribute: `azapi_resource.<name>.output.properties.<field>`
- Resource `id` and `name` are top-level: `azapi_resource.<name>.id`, `azapi_resource.<name>.name`
- Data sources use `data "azapi_resource"` with `type` and `resource_id`
- Use `azapi_resource_action` for POST-based operations (e.g., list keys)

## State Management

### Local State (POC Default)

For POC projects, local state is the default. State files are stored in the environment directory:

```hcl
# No backend block needed -- Terraform defaults to local state
# State file: terraform.tfstate in the working directory
```

Add `*.tfstate` and `*.tfstate.backup` to `.gitignore`.

### Remote State (Production / Shared)

When the project graduates beyond POC, configure remote state:

```hcl
# backend.tf
terraform {
  backend "azurerm" {
    resource_group_name  = "rg-terraform-state"
    storage_account_name = "stterraformstate"
    container_name       = "tfstate"
    key                  = "<project>/<environment>/terraform.tfstate"
  }
}
```

Bootstrap the storage account before first use:

```bash
az group create --name rg-terraform-state --location eastus
az storage account create \
  --name stterraformstate \
  --resource-group rg-terraform-state \
  --sku Standard_LRS \
  --encryption-services blob
az storage container create \
  --name tfstate \
  --account-name stterraformstate
```

## Standard Module Variables

### variables.tf (Template)

```hcl
variable "resource_group_id" {
  description = "Resource ID of the resource group"
  type        = string
}

variable "location" {
  description = "Azure region for resources"
  type        = string
}

variable "name" {
  description = "Name of the resource"
  type        = string
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}

# NOTE: Do NOT include private endpoint variables or resources in service stages.
# Private endpoints are created ONLY in the dedicated Networking stage.
# Service stages should only set publicNetworkAccess = "Disabled" on their resources.
```

## Private Endpoint Pattern (NETWORKING STAGE ONLY)

**This pattern is used ONLY by the Networking stage.** Do NOT include private endpoint
resources, DNS zone groups, or PE-related variables in any other stage. The Networking
stage creates all private endpoints for all services in the deployment plan.

### private-endpoint.tf (Networking stage only)

```hcl
resource "azapi_resource" "private_endpoint" {
  count = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0

  type      = "Microsoft.Network/privateEndpoints@2023-11-01"
  name      = "pe-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      subnet = {
        id = var.subnet_id
      }
      privateLinkServiceConnections = [
        {
          name = "psc-${var.name}"
          properties = {
            privateLinkServiceId = azapi_resource.this.id
            groupIds             = ["<group_id>"]  # See service-registry.yaml
          }
        }
      ]
    }
  }

  tags = var.tags
}

resource "azapi_resource" "private_dns_zone_group" {
  count = var.enable_private_endpoint && var.subnet_id != null && var.private_dns_zone_id != null ? 1 : 0

  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01"
  name      = "dns-zone-group"
  parent_id = azapi_resource.private_endpoint[0].id

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "config"
          properties = {
            privateDnsZoneId = var.private_dns_zone_id
          }
        }
      ]
    }
  }
}
```

## RBAC Assignment Pattern

```hcl
resource "azapi_resource" "role_assignment" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = var.role_assignment_name  # Must be a GUID
  parent_id = azapi_resource.this.id    # Scope: the target resource ID

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${data.azapi_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/<role_id>"
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Standard Outputs

### outputs.tf (Template)

```hcl
output "id" {
  description = "Resource ID"
  value       = azapi_resource.this.id
}

output "name" {
  description = "Resource name"
  value       = azapi_resource.this.name
}

# Include endpoint output for services with endpoints
# AzAPI v2: access properties via .output.properties.<field>
output "endpoint" {
  description = "Resource endpoint URL"
  value       = azapi_resource.this.output.properties.<endpoint_attribute>
}

# Include private endpoint IP if applicable
output "private_endpoint_ip" {
  description = "Private endpoint IP address"
  value       = try(azapi_resource.private_endpoint[0].output.properties.customDnsConfigs[0].ipAddresses[0], null)
}
```

## Environment Configuration

### dev/main.tf

```hcl
resource "azapi_resource" "resource_group" {
  type     = "Microsoft.Resources/resourceGroups@2024-03-01"
  name     = "rg-${var.project_name}-${var.environment}"
  location = var.location

  tags = local.common_tags
}

module "<service>" {
  source = "../../modules/<service-name>"

  resource_group_id = azapi_resource.resource_group.id
  location          = azapi_resource.resource_group.output.location
  name              = "<service>-${var.project_name}-${var.environment}"

  # NOTE: Private endpoints are NOT configured here.
  # The Networking stage creates all PEs centrally.

  tags = local.common_tags
}
```

### dev/variables.tf

```hcl
variable "project_name" {
  description = "Project name used in resource naming"
  type        = string
}

variable "environment" {
  description = "Environment name (dev, test, prod)"
  type        = string
  default     = "dev"
}

variable "location" {
  description = "Azure region"
  type        = string
  default     = "eastus"
}
```

### dev/terraform.tfvars

```hcl
project_name = "myproject"
environment  = "dev"
location     = "eastus"
```

## Staged Deployment Script Pattern

The deploy stage uses a staged deployment script (`deploy.sh`) to deploy infrastructure in dependency order. See `deploy-scripts.md` for the full pattern. The Terraform-specific commands within each stage are:

```bash
# Per-stage Terraform deployment
deploy_terraform_stage() {
  local stage_dir="$1"
  local stage_name="$2"

  cd "$stage_dir"
  terraform init -input=false
  terraform validate

  if [ "$DRY_RUN" = "true" ]; then
    terraform plan -input=false -var-file="$VARS_FILE"
  else
    terraform plan -input=false -var-file="$VARS_FILE" -out=tfplan
    terraform apply -input=false tfplan
    rm -f tfplan
  fi
}
```

Stage ordering follows the pattern: foundation (resource group, networking, identity) then data then compute then applications. See `deploy-scripts.md` for the complete staged deployment framework.

## Deployment Commands

These commands are executed directly by the deploy stage. `--dry-run` uses `terraform plan` only.

```bash
# Navigate to environment directory
cd infrastructure/terraform/environments/dev

# Initialize Terraform (first time or after provider changes)
terraform init -input=false

# Validate configuration
terraform validate

# Format check
terraform fmt -check -recursive

# Plan deployment (dry-run mode)
terraform plan -input=false -var-file=terraform.tfvars

# Apply deployment (execute mode)
terraform plan -input=false -var-file=terraform.tfvars -out=tfplan
terraform apply -input=false tfplan

# Destroy resources (rollback / teardown)
terraform plan -destroy -input=false -var-file=terraform.tfvars -out=tfplan-destroy
terraform apply -input=false tfplan-destroy
```

## Common Patterns

### Conditional Resource Creation

```hcl
resource "azapi_resource" "example" {
  count = var.enable_feature ? 1 : 0

  type      = "Microsoft.<Provider>/<ResourceType>@<api-version>"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      # ...
    }
  }
}

# Reference with try()
output "example_id" {
  value = try(azapi_resource.example[0].id, null)
}
```

### For Each with Map

```hcl
resource "azapi_resource" "example" {
  for_each = var.instances

  type      = "Microsoft.<Provider>/<ResourceType>@<api-version>"
  name      = each.key
  parent_id = var.resource_group_id

  body = {
    properties = {
      property = each.value.property
    }
  }
}
```

### Local Values

```hcl
locals {
  common_tags = merge(var.tags, {
    Environment = var.environment
    ManagedBy   = "Terraform"
    Project     = var.project_name
  })

  resource_prefix = "${var.project_name}-${var.environment}"
}
```

### Data Sources for Existing Resources

```hcl
data "azapi_client_config" "current" {}

# Look up an existing resource by ID
data "azapi_resource" "existing" {
  type        = "Microsoft.<Provider>/<ResourceType>@<api-version>"
  resource_id = "/subscriptions/.../resourceGroups/.../providers/..."
}

# Reference current user/service principal
output "current_tenant_id" {
  value = data.azapi_client_config.current.tenant_id
}
```

### Moved Blocks (Refactoring)

When renaming resources, use `moved` blocks to avoid destroy/recreate:

```hcl
moved {
  from = azapi_resource.old_name
  to   = azapi_resource.new_name
}
```

## Security Checklist

All Terraform configurations MUST:

- [ ] Disable public network access where supported
- [ ] Set `publicNetworkAccess = "Disabled"` (private endpoints are created by the Networking stage)
- [ ] Use Managed Identity for authentication (avoid keys/secrets)
- [ ] Enable TLS 1.2+ minimum
- [ ] Enable diagnostic logging
- [ ] Apply required tags (Environment, Project, ManagedBy at minimum)
- [ ] Never hardcode secrets -- use Key Vault references or variables marked `sensitive`
- [ ] Set `sensitive = true` on variables containing secrets

```hcl
variable "admin_password" {
  description = "Admin password"
  type        = string
  sensitive   = true
}
```

## Service-Specific Values

Refer to `service-registry.yaml` for per-service details:

- Private endpoint `subresource_names` (group IDs)
- RBAC role definition names
- SKU options and defaults
- Resource naming prefixes

## Critical Reminders

1. **Direct execution** -- This extension runs `terraform apply` directly. Always validate with `terraform plan` first.
2. **Do NOT create private endpoints** -- The dedicated Networking stage creates all PEs and DNS zone groups. Your stage should only set `publicNetworkAccess = "Disabled"` on resources.
3. **Use variables** -- No hardcoded values in main.tf.
4. **Export outputs** -- Other modules depend on these values.
5. **Follow naming conventions** -- Use project/environment prefix from the naming strategy.
6. **AzAPI v2** -- Use `>= 2.0, < 3.0` version constraint. All resources use ARM resource types with explicit API versions.
7. **State hygiene** -- Local state for POC, remote state for production. Never commit `.tfstate` files.
