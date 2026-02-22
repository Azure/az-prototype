"""Cloud Architect built-in agent — cross-service coordination and design."""

import logging

from azext_prototype.agents.base import (
    AgentCapability,
    AgentContext,
    AgentContract,
    BaseAgent,
)
from azext_prototype.ai.provider import AIMessage, AIResponse

logger = logging.getLogger(__name__)


class CloudArchitectAgent(BaseAgent):
    """Designs Azure architecture, coordinates services, and manages configuration.

    This is the primary agent for the design stage. It understands Azure
    services, networking, identity, and best practices for prototype
    architectures.
    """

    _temperature = 0.3
    _max_tokens = 32768
    _enable_web_search = True
    _knowledge_role = "architect"
    _keywords = [
        "architect",
        "design",
        "service",
        "infrastructure",
        "networking",
        "security",
        "identity",
        "managed identity",
        "azure",
        "resource",
        "configuration",
        "integration",
    ]
    _keyword_weight = 0.1
    _contract = AgentContract(
        inputs=["requirements"],
        outputs=["architecture", "deployment_plan"],
        delegates_to=["terraform-agent", "bicep-agent", "app-developer"],
    )

    def __init__(self):
        super().__init__(
            name="cloud-architect",
            description="Azure architecture design and cross-service coordination",
            capabilities=[
                AgentCapability.ARCHITECT,
                AgentCapability.COORDINATE,
                AgentCapability.ANALYZE,
            ],
            constraints=[
                "All Azure services MUST use Managed Identity — NO connection strings or access keys",
                "Follow Microsoft Well-Architected Framework principles",
                "This is a PROTOTYPE — optimize for speed and demonstration, not production readiness",
                "Prefer PaaS over IaaS for simplicity",
                "Include cost-appropriate SKUs (dev/test tiers where available)",
                "All resources must be in a single resource group unless architecturally required",
                "Include proper resource tagging (Environment, Purpose, Zone)",
                "Follow the project's naming conventions EXACTLY — do not invent names",
            ],
            system_prompt=CLOUD_ARCHITECT_PROMPT,
        )

    def execute(self, context: AgentContext, task: str) -> AIResponse:
        """Execute architecture design task."""
        messages = self.get_system_messages()

        # Add project context
        project_config = context.project_config
        messages.append(
            AIMessage(
                role="system",
                content=(
                    f"PROJECT CONTEXT:\n"
                    f"- Name: {project_config.get('project', {}).get('name', 'unnamed')}\n"
                    f"- Region: {project_config.get('project', {}).get('location', 'eastus')}\n"
                    f"- IaC Tool: {project_config.get('project', {}).get('iac_tool', 'terraform')}\n"
                    f"- Environment: {project_config.get('project', {}).get('environment', 'dev')}\n"
                ),
            )
        )

        # Add Azure API version context for both Terraform and Bicep
        from azext_prototype.requirements import get_dependency_version

        api_ver = get_dependency_version("azure_api")
        if api_ver:
            iac_tool = project_config.get("project", {}).get("iac_tool", "terraform")
            lang = "terraform" if iac_tool == "terraform" else "bicep"
            messages.append(
                AIMessage(
                    role="system",
                    content=(
                        f"AZURE API VERSION: {api_ver}\n"
                        f"All resource type declarations must use API version {api_ver}.\n"
                        f"Format: Microsoft.<Provider>/<ResourceType>@{api_ver}\n"
                        f"Reference docs: "
                        f"https://learn.microsoft.com/en-us/azure/templates/"
                        f"<resource_provider>/{api_ver}/<resource_type>"
                        f"?pivots=deployment-language-{lang}"
                    ),
                )
            )

        # Add naming conventions
        naming_instructions = self._get_naming_instructions(project_config)
        if naming_instructions:
            messages.append(
                AIMessage(
                    role="system",
                    content=naming_instructions,
                )
            )

        # Add any artifacts
        requirements = context.get_artifact("requirements")
        if requirements:
            messages.append(
                AIMessage(
                    role="system",
                    content=f"CUSTOMER REQUIREMENTS:\n{requirements}",
                )
            )

        # Add conversation history
        messages.extend(context.conversation_history)

        # Add the task
        messages.append(AIMessage(role="user", content=task))

        response = context.ai_provider.chat(
            messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

        # Post-response governance check
        warnings = self.validate_response(response.content)
        if warnings:
            for w in warnings:
                logger.warning("Governance: %s", w)
            warning_block = "\n\n---\n" "**\u26a0 Governance warnings:**\n" + "\n".join(f"- {w}" for w in warnings)
            response = AIResponse(
                content=response.content + warning_block,
                model=response.model,
                usage=response.usage,
                finish_reason=response.finish_reason,
            )

        return response

    def _get_naming_instructions(self, config: dict) -> str:
        """Generate naming convention instructions from project config."""
        try:
            from azext_prototype.naming import create_naming_strategy

            strategy = create_naming_strategy(config)
            return strategy.to_prompt_instructions()
        except Exception:
            return ""


CLOUD_ARCHITECT_PROMPT = """You are an expert Azure Cloud Architect specializing in rapid prototype design.

Your role is to design Azure architectures that are:
- Simple and focused on demonstrating the core value proposition
- Cost-effective (use dev/test SKUs and free tiers where possible)
- Secure by default (managed identity, RBAC, no secrets in code)
- Well-documented with clear deployment stages

You receive requirements from a discovery conversation between the user
and the biz-analyst.  Trust that output as your primary input.  If
something is ambiguous or conflicts with best practice, call it out and
ask — don't silently override or silently assume.

If any governance policies were overridden during discovery, the
requirements will say so.  Acknowledge the override and design
accordingly — don't re-argue it.

When designing architectures:
1. Start with the problem being solved
2. Select the minimum set of Azure services needed
3. Design the data flow and integration points
4. Define authentication and authorization using managed identity
5. Create a deployment order that respects dependencies
6. Assign resources to the correct landing zone:
   - Platform resources (networking, DNS, firewall) -> pc (Connectivity Platform)
   - Identity resources (Entra ID config, RBAC) -> pi (Identity Platform)
   - Monitoring resources (Log Analytics, App Insights) -> pm (Management Platform)
   - Application resources -> zd/zt/zs/zp based on environment
7. Document any shortcuts taken (this is a prototype)

NAMING CONVENTIONS:
- You will receive specific naming convention instructions in the context
- Follow them EXACTLY for all resources
- Do NOT invent your own naming scheme
- If using Azure Landing Zone strategy, place platform vs. application resources
  in the correct zone using the zone ID prefix
- If no naming instructions are provided, use Microsoft Cloud Adoption Framework conventions:
  https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ready/azure-best-practices/resource-naming

Output format for architecture documents:
- Use Markdown with clear sections
- Include a Mermaid architecture diagram
- List all Azure services with their SKUs and configurations
- Include the exact resource names following the naming conventions
- Group resources by landing zone where applicable
- Specify deployment stages in dependency order

DEPLOYMENT PLAN COMPLETENESS (MANDATORY):
When producing deployment stages, each stage MUST define:
1. **Outputs**: What resource names, IDs, and endpoints this stage provides
   to downstream stages (e.g., resource_group_name, workspace_id, identity_client_id)
2. **Inputs**: What values this stage needs from prior stages
   (reference by stage number and output name)
3. **Companion resources**: If a service disables key-based auth (e.g., Cosmos DB
   local auth disabled, Storage shared key disabled), the SAME stage MUST also
   include a managed identity and RBAC role assignment. Never disable auth
   without providing the alternative auth mechanism.
4. **Backend state**: All stages share a common Terraform/Bicep state backend.
   Stage 1 should create or document the backend storage prerequisite.

If the architecture requires a Key Vault for secret storage (connection strings,
external API keys, OAuth secrets), include it as a resource in the monitoring/
foundation stage — do NOT leave it out and expect downstream stages to reference
a non-existent vault.

CRITICAL RULES:
- NEVER use connection strings or access keys
- ALWAYS use Managed Identity for service-to-service auth
- ALWAYS include resource tags (Environment, Purpose, Zone)
- ALWAYS use the project's naming conventions
- Keep the architecture as simple as possible
- This is a PROTOTYPE — document production considerations but don't implement them
- NEVER design a service with disabled local auth unless the same stage
  includes managed identity + RBAC as the replacement auth mechanism

When you need current Azure documentation or are uncertain about a service API,
SDK version, or configuration option, emit [SEARCH: your query] in your response.
The framework will fetch relevant Microsoft Learn documentation and re-invoke you
with the results. Use at most 2 search markers per response. Only search when your
built-in knowledge is insufficient.
"""
