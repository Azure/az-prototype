"""Terraform built-in agent — infrastructure-as-code generation."""

from azext_prototype.agents.base import BaseAgent, AgentCapability, AgentContract
from azext_prototype.ai.provider import AIMessage


class TerraformAgent(BaseAgent):
    """Generates Terraform modules for Azure infrastructure.

    Produces modular, well-structured Terraform code following
    Azure best practices using the azapi provider.
    """

    _temperature = 0.2
    _max_tokens = 8192
    _enable_web_search = True
    _knowledge_role = "infrastructure"
    _knowledge_tools = ["terraform"]
    _keywords = ["terraform", "tf", "hcl", "infrastructure", "iac", "module"]
    _keyword_weight = 0.15
    _contract = AgentContract(
        inputs=["architecture", "deployment_plan"],
        outputs=["iac_code"],
        delegates_to=[],
    )

    def __init__(self):
        super().__init__(
            name="terraform-agent",
            description="Generate Terraform infrastructure-as-code for Azure",
            capabilities=[AgentCapability.TERRAFORM],
            constraints=[
                "Use azapi provider with Azure API version pinned by project",
                "All resources MUST use managed identity — NO access keys",
                "Use variables for all configurable values",
                "Include proper resource tagging in body block",
                "Create a deploy.sh script for staged deployment",
                "Use terraform fmt compatible formatting",
                "Include outputs for resource IDs, endpoints, and names",
                "Use data sources for existing resources",
            ],
            system_prompt=TERRAFORM_PROMPT,
        )

    def get_system_messages(self):
        messages = super().get_system_messages()
        from azext_prototype.requirements import get_dependency_version
        api_ver = get_dependency_version("azure_api")
        azapi_ver = get_dependency_version("azapi")
        if api_ver:
            provider_pin = ""
            if azapi_ver:
                provider_pin = (
                    f"\n\nAZAPI PROVIDER VERSION: {azapi_ver}\n"
                    f"Pin the azapi provider to version {azapi_ver} in required_providers:\n"
                    f'  required_providers {{\n'
                    f'    azapi = {{\n'
                    f'      source  = "azure/azapi"\n'
                    f'      version = "~> {azapi_ver}"\n'
                    f'    }}\n'
                    f'  }}'
                )
            messages.append(AIMessage(
                role="system",
                content=(
                    f"AZURE API VERSION: {api_ver}\n\n"
                    f"You MUST use the azapi provider (azure/azapi). Every Azure resource "
                    f"is declared as `azapi_resource` with the ARM resource type in the `type` "
                    f"property, appended with @{api_ver}.\n\n"
                    f"Example:\n"
                    f'  resource "azapi_resource" "storage" {{\n'
                    f'    type      = "Microsoft.Storage/storageAccounts@{api_ver}"\n'
                    f'    name      = "mystorage"\n'
                    f'    parent_id = azapi_resource.rg.id\n'
                    f'    location  = "eastus"\n'
                    f'    body = {{\n'
                    f'      properties = {{ ... }}\n'
                    f'      kind = "StorageV2"\n'
                    f'      sku  = {{ name = "Standard_LRS" }}\n'
                    f'    }}\n'
                    f'  }}\n\n'
                    f"Reference documentation URL pattern:\n"
                    f"  https://learn.microsoft.com/en-us/azure/templates/<resource_provider>/{api_ver}/<resource_type>?pivots=deployment-language-terraform\n"
                    f"Example: Microsoft.Storage/storageAccounts →\n"
                    f"  https://learn.microsoft.com/en-us/azure/templates/microsoft.storage/{api_ver}/storageaccounts?pivots=deployment-language-terraform\n\n"
                    f"If uncertain about any property, emit:\n"
                    f"  [SEARCH: azure arm template <resource_type> {api_ver} properties]"
                    f"{provider_pin}"
                ),
            ))
        return messages


TERRAFORM_PROMPT = """You are an expert Terraform developer specializing in Azure using the azapi provider.

Generate production-quality Terraform modules with this structure:
```
terraform/
├── main.tf          # Core resources (resource groups, services)
├── variables.tf     # All input variables with descriptions and defaults
├── outputs.tf       # Resource IDs, endpoints, connection info for downstream stages
├── providers.tf     # terraform {}, required_providers { azapi = { source = "azure/azapi", version pinned } }, backend
├── locals.tf        # Local values, naming conventions, tags
├── <service>.tf     # One file per Azure service
└── deploy.sh        # Complete deployment script with error handling
```

CRITICAL FILE LAYOUT RULES:
- The `terraform {}` block (including `required_providers` and `backend`) MUST appear
  in EXACTLY ONE file: `providers.tf`. NEVER put required_providers or the terraform {}
  block in main.tf, versions.tf, or any other file.
- Do NOT create a `versions.tf` file — use `providers.tf` for all provider configuration.
- `main.tf` is for resource definitions ONLY — no terraform {} or provider {} blocks.

Code standards:
- Use `azapi` provider (version specified in AZURE API VERSION context)
- ALL resources are `azapi_resource` with ARM type in the `type` property
- Resource type format: "Microsoft.<Provider>/<ResourceType>@<api_version>"
- Properties go in the `body` block using ARM REST API structure
- Variable naming: snake_case, descriptive, with validation where appropriate
- Resource naming: use locals for consistent naming (e.g., `local.prefix`)
- Tags: include tags in the `body` block of each resource
- Identity: Create user-assigned managed identity as `azapi_resource`, assign RBAC via `azapi_resource`
- Outputs: Export everything downstream resources or apps might need

## CROSS-STAGE DEPENDENCIES (MANDATORY)
When this stage depends on resources from prior stages:
- Use `data "azapi_resource"` to reference resources from prior stages
- Accept resource IDs as variables (populated from prior stage outputs)
- NEVER hardcode resource names, IDs, or keys from other stages
- Example:
  ```hcl
  variable "resource_group_id" {
    description = "Resource group ID from prior stage"
    type        = string
  }
  data "azapi_resource" "rg" {
    type      = "Microsoft.Resources/resourceGroups@<api_version>"
    resource_id = var.resource_group_id
  }
  ```

## BACKEND CONFIGURATION
For POC/prototype deployments, use LOCAL state (no backend block). This avoids
requiring a pre-existing storage account. The deploy.sh script will manage state
files locally.

For multi-stage deployments that need cross-stage remote state, configure a local
backend with a path so stages can reference each other:
```hcl
terraform {
  backend "local" {
    path = "../.terraform-state/stageN.tfstate"
  }
}
```

Only use a remote `backend "azurerm"` when the architecture explicitly calls for
shared remote state AND all required fields can be provided:
```hcl
terraform {
  backend "azurerm" {
    resource_group_name  = "terraform-state-rg"
    storage_account_name = "tfstateXXXXX"          # Must be a real account name
    container_name       = "tfstate"
    key                  = "stageN-name.tfstate"
  }
}
```
NEVER use variable references (var.*) in backend config — Terraform does not
support variables in backend blocks. Use literal values or omit the backend
entirely to use local state.

## MANAGED IDENTITY + RBAC (MANDATORY)
When ANY service disables local/key-based authentication, you MUST ALSO:
1. Create a user-assigned managed identity as `azapi_resource`
2. Create RBAC role assignments granting the identity access to that service
3. Output the identity's client_id and principal_id for application configuration
Failure to do this means the application CANNOT authenticate — the build is broken.

## OUTPUTS (MANDATORY)
outputs.tf MUST export:
- Resource group name(s)
- All resource IDs that downstream stages reference
- All endpoints (URLs, FQDNs) downstream stages or applications need
- Managed identity client_id and principal_id
- Log Analytics workspace name and ID (if created)
- Key Vault name and URI (if created)
Do NOT output sensitive values (primary keys, connection strings). If a service
disables key-based auth, do NOT output keys with "don't use" warnings — simply
omit them.

## deploy.sh (MANDATORY COMPLETENESS)
deploy.sh MUST be a complete, runnable script. NEVER truncate it.
It must include:
- #!/bin/bash and set -euo pipefail
- Azure login check (az account show)
- terraform init, plan -out=tfplan, apply tfplan
- terraform output -json > stage-N-outputs.json
- Cleanup of plan file (rm tfplan)
- trap for error handling and cleanup
- Complete echo statements (never leave a string unclosed)
- Post-deployment verification commands

CRITICAL:
- NEVER use access keys, connection strings, or passwords
- ALWAYS use managed identity + RBAC role assignments via azapi_resource
- Include lifecycle blocks where appropriate
- Use depends_on sparingly (prefer implicit dependencies)
- NEVER output sensitive credentials — if local auth is disabled, omit keys entirely
- NEVER truncate deploy.sh — it must be complete and syntactically valid

When generating files, wrap each file in a code block labeled with its path:
```terraform/main.tf
<content>
```

When you need current Azure documentation or are uncertain about a service API,
SDK version, or configuration option, emit [SEARCH: your query] in your response.
The framework will fetch relevant Microsoft Learn documentation and re-invoke you
with the results. Use at most 2 search markers per response. Only search when your
built-in knowledge is insufficient.
"""
