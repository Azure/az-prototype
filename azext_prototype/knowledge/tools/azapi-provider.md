# AzAPI Provider — Configuration and Subscription Binding

This project uses **only** the `hashicorp/azapi` provider. The `azurerm` provider is never used.

## Provider Block

The azapi provider inherits authentication and subscription context from the Azure CLI session. Do NOT add `subscription_id` or `tenant_id` to the provider block:

```hcl
# providers.tf — CORRECT
provider "azapi" {}
```

```hcl
# WRONG — do not bind subscription in the provider block
provider "azapi" {
  subscription_id = var.subscription_id   # REMOVE THIS
  tenant_id       = var.tenant_id         # REMOVE THIS
}
```

The provider resolves subscription from (in order):
1. `ARM_SUBSCRIPTION_ID` environment variable
2. Azure CLI default subscription (`az account show --query id`)

## deploy.sh — Subscription Context

Every `deploy.sh` MUST establish the correct subscription context before running Terraform. This is what makes `provider "azapi" {}` (with no explicit subscription) work correctly.

### Required in preflight / setup

```bash
# 1. Verify login
if ! az account show &>/dev/null; then
  error "Not logged in. Run: az login"
  exit 1
fi

# 2. Set the target subscription (CRITICAL — without this, Terraform may
#    deploy to whatever subscription the CLI happens to default to)
az account set --subscription "${TF_VAR_subscription_id}"

# 3. Export for Terraform azapi provider
export ARM_SUBSCRIPTION_ID="${TF_VAR_subscription_id}"
export ARM_TENANT_ID="${TF_VAR_tenant_id}"
```

Without step 2 and 3, the azapi provider will use the CLI's default subscription, which may differ from the project's target subscription. This is the most common cause of "deployed to wrong subscription" errors.

## Using subscription_id as a Variable

Declare `subscription_id` and `tenant_id` as input variables — they are used for constructing ARM resource IDs, NOT for provider binding:

```hcl
variable "subscription_id" {
  type        = string
  description = "Azure subscription ID — used in ARM resource ID construction"
}

variable "tenant_id" {
  type        = string
  description = "Azure tenant ID"
}
```

### Where variables are used

ARM resource IDs in role assignments and cross-resource references:

```hcl
resource "azapi_resource" "role_assignment" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  parent_id = azapi_resource.registry.id
  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/<role-guid>"
      principalId      = azapi_resource.identity.output.properties.principalId
      principalType    = "ServicePrincipal"
    }
  }
}
```

## QA Reviewers — Do NOT Flag

- `provider "azapi" {}` with no `subscription_id` is **correct** — the CLI context provides it
- `deploy.sh` must set `az account set` and `export ARM_SUBSCRIPTION_ID` — flag if missing
- `var.subscription_id` in resource IDs is for ARM path construction, not provider config

## Common Mistakes

| Mistake | Why it's wrong | Fix |
|---------|---------------|-----|
| Adding `subscription_id` to `provider "azapi" {}` | Duplicates CLI context, breaks when variable not set | Remove from provider block |
| deploy.sh missing `az account set` | Terraform uses wrong subscription | Add `az account set --subscription` in preflight |
| deploy.sh missing `export ARM_SUBSCRIPTION_ID` | azapi provider falls back to CLI default | Export before `terraform init` |
| Using `data "azurerm_client_config"` | azurerm is not available | Use `var.subscription_id` and `var.tenant_id` |
