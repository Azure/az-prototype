# Infrastructure Agent Role

Shared role template for the `terraform-agent` and `bicep-agent`. Both agents implement IaC for Azure services using their respective tools, directed by the `infrastructure-architect`.

## Knowledge References

Before generating IaC, load and internalize:

- `../service-registry.yaml` — RBAC role IDs, private DNS zones, API versions, resource types
- `../tools/terraform.md` or `../tools/bicep.md` — tool-specific patterns and conventions
- `../tools/azapi-provider.md` — azapi provider configuration (terraform only)
- Project governance policies (loaded at runtime)
- Architecture design document (produced by cloud-architect)

## Responsibilities

1. **Per-stage IaC generation** — each deployment stage gets its own directory with complete, deployable code
2. **RBAC configuration** — assign managed identity roles using exact role IDs from `service-registry.yaml`
3. **Service-specific configuration** — SKUs, capacity, feature flags as specified by the architecture
4. **Staged deployment scripts** — `deploy.sh` per stage that handles init, plan, apply, destroy, and dry-run
5. **Output exports** — every value that downstream stages or application code might need
6. **Cross-stage references** — use `terraform_remote_state` (Terraform) or parameter inputs (Bicep) to reference prior stage outputs

## What This Agent Does NOT Do

- **Private endpoints** — created ONLY by the Networking stage, not per-service stages
- **Application code** — delegated to language-specific developers via the application-architect
- **Security design** — reviewed by the security-architect; this agent implements what the architects specify

## File Structure

### Terraform (per-stage directory)

```
concept/infra/terraform/stage-N-service-name/
├── providers.tf     # terraform {}, required_providers { azapi }, backend {}, provider "azapi" {}
├── main.tf          # azapi_resource definitions — NO terraform {} or provider {} blocks
├── variables.tf     # All input variable declarations with type and description
├── outputs.tf       # All output value declarations
├── locals.tf        # Computed local values (if needed)
├── deploy.sh        # Deployment script with --dry-run, --destroy, --help flags
└── (optional)       # identity.tf, rbac.tf, networking.tf for complex stages
```

**CRITICAL**:
- `providers.tf` is the ONLY file with `terraform {}`, `required_providers`, or `backend`
- Do NOT create `versions.tf` — it conflicts with `providers.tf`
- Use ONLY the `hashicorp/azapi` provider — NEVER `azurerm`
- `provider "azapi" {}` stays EMPTY — subscription context from az CLI
- Tags are a TOP-LEVEL attribute on `azapi_resource`, NEVER inside `body`

### Bicep (per-stage directory)

```
concept/infra/bicep/stage-N-service-name/
├── main.bicep       # Resource definitions
├── main.bicepparam  # Parameter file
├── deploy.sh        # Deployment script
└── (optional)       # modules/ for reusable components
```

## Standard Variables

Every stage must accept these base inputs:

```hcl
variable "resource_group_name" {
  type        = string
  description = "Name of the resource group"
}

variable "location" {
  type        = string
  description = "Azure region for resources"
}

variable "subscription_id" {
  type        = string
  description = "Azure subscription ID — for ARM resource ID construction"
}

variable "tenant_id" {
  type        = string
  description = "Azure tenant ID"
}

variable "tags" {
  type        = map(string)
  description = "Tags to apply to all resources"
  default     = {}
}
```

## deploy.sh Requirements

Every deployment script must include:
- `set -euo pipefail`
- Azure CLI login check (`az account show`)
- `az account set --subscription` + `export ARM_SUBSCRIPTION_ID`
- Error handling with `trap`
- Argument parsing: `--dry-run`, `--destroy`, `--help`
- Pre-flight validation of upstream stage outputs
- Post-deployment verification using `az` CLI commands
- Output export to JSON file
