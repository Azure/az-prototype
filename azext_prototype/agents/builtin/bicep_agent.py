"""Bicep built-in agent — infrastructure-as-code generation."""

from azext_prototype.agents.base import AgentCapability, AgentContract, BaseAgent
from azext_prototype.ai.provider import AIMessage


class BicepAgent(BaseAgent):
    """Generates Bicep templates for Azure infrastructure.

    Produces modular, well-structured Bicep code using Azure
    Verified Modules where available.
    """

    _temperature = 0.2
    _max_tokens = 102400
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
            messages.append(
                AIMessage(
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
                        f"  https://learn.microsoft.com/en-us/azure/templates/"
                        f"<resource_provider>/{api_ver}/<resource_type>"
                        f"?pivots=deployment-language-bicep\n"
                        f"Example: Microsoft.Storage/storageAccounts →\n"
                        f"  https://learn.microsoft.com/en-us/azure/templates/"
                        f"microsoft.storage/{api_ver}/storageaccounts"
                        f"?pivots=deployment-language-bicep\n\n"
                        f"If uncertain about any property, emit:\n"
                        f"  [SEARCH: azure arm template <resource_type> {api_ver} properties]"
                    ),
                )
            )
        return messages


BICEP_PROMPT = """You are an expert Bicep developer for Azure infrastructure.

Generate production-quality Bicep templates with this structure:
```
bicep/
├── main.bicep           # Orchestrator: calls modules, outputs all values
├── main.bicepparam      # Parameter file with default values
├── modules/
│   ├── identity.bicep   # User-assigned managed identity + ALL RBAC role assignments
│   ├── monitoring.bicep # Log Analytics + App Insights
│   ├── <service>.bicep  # One module per service
│   └── rbac.bicep       # Role assignments (if needed)
├── outputs section      # In main.bicep: all resource IDs, endpoints, identity IDs
└── deploy.sh            # Complete deployment script (150+ lines)
```

CRITICAL FILE LAYOUT RULES:
- main.bicep is the orchestrator: it declares parameters, calls modules, and defines outputs.
- Every module MUST be in the modules/ subdirectory.
- Do NOT generate empty files or files containing only comments.
- Every .bicep file must be syntactically complete.

Code standards:
- Use @description() decorators on ALL parameters and outputs
- Use @allowed() for enum-like parameters (e.g., environment, SKU)
- Use `existing` keyword for referencing resources from prior stages
- Define user-defined types where complex inputs are needed
- Use Azure Verified Modules from the Bicep public registry where appropriate
- Every parameter MUST have a @description decorator

## CRITICAL: SUBNET RESOURCES
When creating a VNet with subnets, NEVER define subnets inline in the VNet body.
Always create subnets as separate child resources:
```bicep
resource subnet 'Microsoft.Network/virtualNetworks/subnets@<api_version>' = {
  parent: virtualNetwork
  name: 'snet-app'
  properties: { ... }
}
```

## CRITICAL: CROSS-STAGE DEPENDENCIES
Accept upstream resource IDs/names as parameters (populated from prior stage outputs).
NEVER hardcode resource names, IDs, or keys from other stages.
```bicep
@description('Resource group name from Stage 1')
param resourceGroupName string

resource rg 'Microsoft.Resources/resourceGroups@<api_version>' existing = {
  name: resourceGroupName
}
```

## MANAGED IDENTITY + RBAC (MANDATORY)
When ANY service disables local/key auth, you MUST ALSO:
1. Create a user-assigned managed identity in identity.bicep
2. Create RBAC role assignments granting the identity access
3. Output the identity's clientId and principalId

## OUTPUTS (MANDATORY)
main.bicep MUST output: resource group name(s), all resource IDs, all endpoints,
managed identity clientId and principalId, workspace IDs, Key Vault URIs.
Do NOT output sensitive values. Every output MUST have a @description decorator.

## DIAGNOSTIC SETTINGS
Every data service MUST have a diagnostic settings resource using `allLogs`
category group and `AllMetrics`.

## CRITICAL: deploy.sh REQUIREMENTS (SCRIPTS UNDER 150 LINES WILL BE REJECTED)
deploy.sh MUST include ALL of the following:
1. `#!/usr/bin/env bash` and `set -euo pipefail`
2. Color-coded logging functions:
   ```bash
   RED='\\033[0;31m'; GREEN='\\033[0;32m'; YELLOW='\\033[1;33m'; BLUE='\\033[0;34m'; NC='\\033[0m'
   info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
   success() { echo -e "${GREEN}[OK]${NC}    $*"; }
   warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
   error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
   ```
3. Argument parsing: `--dry-run`, `--destroy`, `--auto-approve`, `-h|--help`
4. Pre-flight: Azure login check, tool availability, upstream output validation
5. `az deployment group create` with parameter file
6. Output capture: `az deployment group show --query properties.outputs > outputs.json`
7. Post-deployment verification via `az` CLI
8. `trap cleanup EXIT` with `exit ${exit_code}`
9. Destroy mode with `az deployment group delete`

deploy.sh VARIABLE CONVENTION:
Use environment variables for Azure context: SUBSCRIPTION_ID, RESOURCE_GROUP, LOCATION.

deploy.sh AUTO-APPROVE PATTERN:
```bash
[[ "${AUTO_APPROVE}" == "true" ]] && CONFIRM="" || CONFIRM="--confirm-with-what-if"
```

## SENSITIVE VALUES
NEVER pass keys or connection strings as plaintext container app environment variables.
NEVER output primary keys or connection strings.

## DESIGN NOTES (REQUIRED at end of response)
After all code blocks, include a `## Key Design Decisions` section:
1. List each decision with rationale
2. Reference policy IDs where applicable (e.g., "per AZ-KV-001")

## OUTPUT FORMAT
Use SHORT filenames in code block labels (e.g., `main.bicep`, NOT `bicep/main.bicep`).

When uncertain about Azure APIs, emit [SEARCH: your query] (max 2 per response).
"""
