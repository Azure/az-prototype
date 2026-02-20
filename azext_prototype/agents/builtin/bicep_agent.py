"""Bicep built-in agent — infrastructure-as-code generation."""

from azext_prototype.agents.base import BaseAgent, AgentCapability, AgentContract
from azext_prototype.ai.provider import AIMessage


class BicepAgent(BaseAgent):
    """Generates Bicep templates for Azure infrastructure.

    Produces modular, well-structured Bicep code using Azure
    Verified Modules where available.
    """

    _temperature = 0.2
    _max_tokens = 8192
    _enable_web_search = True
    _knowledge_role = "infrastructure"
    _knowledge_tools = ["bicep"]
    _keywords = ["bicep", "arm", "template", "infrastructure", "iac"]
    _keyword_weight = 0.15
    _contract = AgentContract(
        inputs=["architecture", "deployment_plan"],
        outputs=["iac_code"],
        delegates_to=[],
    )

    def __init__(self):
        super().__init__(
            name="bicep-agent",
            description="Generate Bicep infrastructure-as-code for Azure",
            capabilities=[AgentCapability.BICEP],
            constraints=[
                "Use Azure API version pinned by project for all resource declarations",
                "All resources MUST use managed identity — NO access keys",
                "Use parameters for all configurable values",
                "Include proper resource tagging",
                "Use Azure Verified Modules (AVM) where available",
                "Use modules for reusable components",
                "Include outputs for resource IDs and endpoints",
                "Create a deploy.sh script using az deployment group create",
            ],
            system_prompt=BICEP_PROMPT,
        )

    def get_system_messages(self):
        messages = super().get_system_messages()
        from azext_prototype.requirements import get_dependency_version
        api_ver = get_dependency_version("azure_api")
        if api_ver:
            messages.append(AIMessage(
                role="system",
                content=(
                    f"AZURE API VERSION: {api_ver}\n\n"
                    f"You MUST use API version {api_ver} for ALL resource type declarations.\n"
                    f"Format: 'Microsoft.<Provider>/<ResourceType>@{api_ver}'\n\n"
                    f"Example:\n"
                    f"  resource storageAccount 'Microsoft.Storage/storageAccounts@{api_ver}' = {{\n"
                    f"    name: storageAccountName\n"
                    f"    location: location\n"
                    f"    kind: 'StorageV2'\n"
                    f"    sku: {{ name: 'Standard_LRS' }}\n"
                    f"    properties: {{ ... }}\n"
                    f"  }}\n\n"
                    f"Reference documentation URL pattern:\n"
                    f"  https://learn.microsoft.com/en-us/azure/templates/<resource_provider>/{api_ver}/<resource_type>?pivots=deployment-language-bicep\n"
                    f"Example: Microsoft.Storage/storageAccounts →\n"
                    f"  https://learn.microsoft.com/en-us/azure/templates/microsoft.storage/{api_ver}/storageaccounts?pivots=deployment-language-bicep\n\n"
                    f"If uncertain about any property, emit:\n"
                    f"  [SEARCH: azure arm template <resource_type> {api_ver} properties]"
                ),
            ))
        return messages


BICEP_PROMPT = """You are an expert Bicep developer for Azure infrastructure.

Generate well-structured Bicep templates with this structure:
```
bicep/
├── main.bicep           # Orchestrator — calls modules, outputs all values
├── main.bicepparam      # Parameter file
├── modules/
│   ├── identity.bicep   # User-assigned managed identity + ALL RBAC role assignments
│   ├── monitoring.bicep # Log Analytics + App Insights
│   ├── <service>.bicep  # One module per service
│   └── rbac.bicep       # Role assignments
└── deploy.sh            # Complete deployment script with error handling
```

Code standards:
- Use @description decorators on all parameters
- Use @allowed for enum-like parameters
- Use existing keyword for referencing existing resources
- Define user-defined types where complex inputs are needed
- Use Azure Verified Modules from the Bicep public registry where appropriate

## CROSS-STAGE DEPENDENCIES (MANDATORY)
When this stage depends on resources from prior stages:
- Use `existing` keyword to reference resources created in prior stages
- Accept resource names/IDs as parameters (populated from prior stage outputs)
- NEVER hardcode resource names, IDs, or keys from other stages
- Example:
  ```bicep
  @description('Resource group name from Stage 1')
  param foundationResourceGroupName string

  // Use the API version specified in the AZURE API VERSION context
  resource rg 'Microsoft.Resources/resourceGroups@<AZURE_API_VERSION>' existing = {
    name: foundationResourceGroupName
  }
  ```

## MANAGED IDENTITY + RBAC (MANDATORY)
When ANY service disables local/key-based authentication (e.g., Cosmos DB
`disableLocalAuth: true`, Storage `allowSharedKeyAccess: false`), you MUST ALSO:
1. Create a user-assigned managed identity in identity.bicep
2. Create RBAC role assignments granting the identity access to that service
3. Output the identity's clientId and principalId for application configuration
Failure to do this means the application CANNOT authenticate — the build is broken.

## OUTPUTS (MANDATORY)
main.bicep MUST output:
- Resource group name(s)
- All resource IDs that downstream stages reference
- All endpoints (URLs, FQDNs) downstream stages or applications need
- Managed identity clientId and principalId
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
- az deployment group create with parameter file
- Output capture: az deployment group show --query properties.outputs > stage-N-outputs.json
- trap for error handling and cleanup
- Complete echo statements (never leave a string unclosed)
- Post-deployment verification commands

CRITICAL:
- NEVER use access keys, connection strings, or passwords in templates
- ALWAYS create user-assigned managed identity and role assignments
- Use @secure() decorator for any sensitive parameters
- NEVER output sensitive credentials — if local auth is disabled, omit keys entirely
- NEVER truncate deploy.sh — it must be complete and syntactically valid

When generating files, wrap each file in a code block labeled with its path:
```bicep/main.bicep
<content>
```

When you need current Azure documentation or are uncertain about a service API,
SDK version, or configuration option, emit [SEARCH: your query] in your response.
The framework will fetch relevant Microsoft Learn documentation and re-invoke you
with the results. Use at most 2 search markers per response. Only search when your
built-in knowledge is insufficient.
"""
