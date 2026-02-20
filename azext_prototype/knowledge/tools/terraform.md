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
│   │   └── private-endpoint.tf    # if applicable
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
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.0, < 5.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.0"
    }
  }
}
```

### providers.tf

**Note:** AzureRM 4.x removed the `features {}` sub-blocks for `key_vault` and `resource_group`. The provider now requires explicit opt-in for destructive behaviors via resource-level attributes instead.

```hcl
provider "azurerm" {
  features {}

  # Subscription is set via prototype.secrets.yaml or az account
  # subscription_id = var.subscription_id  # uncomment if needed
}

provider "azuread" {}
```

### AzureRM 4.x Migration Notes

Key breaking changes from 3.x to 4.x:

- `features {}` block is now required but most sub-blocks are removed
- `azurerm_resource_group` no longer has `prevent_deletion_if_contains_resources`
- `azurerm_key_vault`: `purge_soft_delete_on_destroy` and `recover_soft_deleted_key_vaults` removed from features; use resource-level properties
- Several resources renamed or split (check provider changelog)
- `skip_provider_registration` moved to a top-level provider attribute

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
variable "resource_group_name" {
  description = "Name of the resource group"
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

# Private Endpoint Variables (include if service supports private endpoints)
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
```

## Private Endpoint Pattern

### private-endpoint.tf

```hcl
resource "azurerm_private_endpoint" "this" {
  count = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0

  name                = "pe-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id

  private_service_connection {
    name                           = "psc-${var.name}"
    private_connection_resource_id = azurerm_<resource_type>.this.id
    subresource_names              = ["<group_id>"]  # See service-registry.yaml
    is_manual_connection           = false
  }

  dynamic "private_dns_zone_group" {
    for_each = var.private_dns_zone_id != null ? [1] : []
    content {
      name                 = "dns-zone-group"
      private_dns_zone_ids = [var.private_dns_zone_id]
    }
  }

  tags = var.tags
}
```

## RBAC Assignment Pattern

```hcl
resource "azurerm_role_assignment" "this" {
  scope                = azurerm_<resource_type>.this.id
  role_definition_name = "<role_name>"  # See service-registry.yaml for roles
  principal_id         = var.managed_identity_principal_id
}
```

## Standard Outputs

### outputs.tf (Template)

```hcl
output "id" {
  description = "Resource ID"
  value       = azurerm_<resource_type>.this.id
}

output "name" {
  description = "Resource name"
  value       = azurerm_<resource_type>.this.name
}

# Include endpoint output for services with endpoints
output "endpoint" {
  description = "Resource endpoint URL"
  value       = azurerm_<resource_type>.this.<endpoint_attribute>
}

# Include private endpoint IP if applicable
output "private_endpoint_ip" {
  description = "Private endpoint IP address"
  value       = try(azurerm_private_endpoint.this[0].private_service_connection[0].private_ip_address, null)
}
```

## Environment Configuration

### dev/main.tf

```hcl
module "<service>" {
  source = "../../modules/<service-name>"

  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  name                = "<service>-${var.project_name}-${var.environment}"

  enable_private_endpoint = true
  subnet_id               = module.networking.data_subnet_id
  private_dns_zone_id     = module.dns.<service>_zone_id

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
resource "azurerm_example" "this" {
  count = var.enable_feature ? 1 : 0
  # ...
}

# Reference with try()
output "example_id" {
  value = try(azurerm_example.this[0].id, null)
}
```

### For Each with Map

```hcl
resource "azurerm_example" "this" {
  for_each = var.instances

  name     = each.key
  property = each.value.property
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
data "azurerm_client_config" "current" {}

data "azurerm_subscription" "current" {}

# Reference current user/service principal
output "current_tenant_id" {
  value = data.azurerm_client_config.current.tenant_id
}
```

### Moved Blocks (4.x Refactoring)

When renaming resources in 4.x, use `moved` blocks to avoid destroy/recreate:

```hcl
moved {
  from = azurerm_storage_account.old_name
  to   = azurerm_storage_account.new_name
}
```

## Security Checklist

All Terraform configurations MUST:

- [ ] Disable public network access where supported
- [ ] Enable private endpoints for data services
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
2. **Always include private endpoint** -- Use the pattern above for any service that supports it.
3. **Use variables** -- No hardcoded values in main.tf.
4. **Export outputs** -- Other modules depend on these values.
5. **Follow naming conventions** -- Use project/environment prefix from the naming strategy.
6. **AzureRM 4.x** -- Use `>= 4.0, < 5.0` version constraint. Check migration guide for renamed resources.
7. **State hygiene** -- Local state for POC, remote state for production. Never commit `.tfstate` files.
