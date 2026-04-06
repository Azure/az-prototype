"""Infrastructure Architect built-in agent — infrastructure layer oversight."""

import logging

from azext_prototype.agents.base import (
    AgentCapability,
    AgentContext,
    AgentContract,
    BaseAgent,
)
from azext_prototype.ai.provider import AIMessage, AIResponse

logger = logging.getLogger(__name__)


class InfrastructureArchitectAgent(BaseAgent):
    """Infrastructure layer oversight -- directs terraform and bicep agents.

    Maintains awareness of the entire infrastructure layer including
    networking, compute, containers, and supporting services.  Delegates
    actual IaC generation to the terraform-agent and bicep-agent.
    """

    _temperature = 0.3
    _max_tokens = 32768
    _enable_web_search = True
    _knowledge_role = "infrastructure"
    _keywords = [
        "infrastructure",
        "networking",
        "compute",
        "container",
        "app service",
        "function",
        "vnet",
        "subnet",
        "nsg",
        "firewall",
        "load balancer",
        "gateway",
        "service bus",
        "event hub",
        "signalr",
        "iot",
    ]
    _keyword_weight = 0.1
    _contract = AgentContract(
        inputs=["architecture"],
        outputs=["infrastructure_code"],
        delegates_to=["terraform-agent", "bicep-agent"],
    )

    def __init__(self):
        super().__init__(
            name="infrastructure-architect",
            description="Infrastructure layer oversight — directs terraform and bicep agents",
            capabilities=[
                AgentCapability.INFRASTRUCTURE_ARCHITECT,
                AgentCapability.COORDINATE,
                AgentCapability.ANALYZE,
            ],
            constraints=[
                "Focus on Azure infrastructure layer only",
                "Direct terraform-agent and bicep-agent for IaC implementation",
                "Maintain awareness of the entire infrastructure — networking, compute, "
                "containers, supporting services",
                "Do NOT generate application code",
                "Ensure networking architecture boundary — private endpoints belong in the networking stage only",
                "All services MUST use Managed Identity — NO connection strings or access keys",
            ],
            system_prompt=INFRASTRUCTURE_ARCHITECT_PROMPT,
        )

    def execute(self, context: AgentContext, task: str) -> AIResponse:
        """Execute infrastructure architecture task."""
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

        # Add any artifacts
        architecture = context.get_artifact("architecture")
        if architecture:
            messages.append(
                AIMessage(
                    role="system",
                    content=f"ARCHITECTURE CONTEXT:\n{architecture}",
                )
            )

        # Add conversation history
        messages.extend(context.conversation_history)

        # Add the task
        messages.append(AIMessage(role="user", content=task))

        assert context.ai_provider is not None
        response = context.ai_provider.chat(
            messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

        return self._apply_governance_check(response, context)


INFRASTRUCTURE_ARCHITECT_PROMPT = """You are an expert Azure infrastructure architect responsible for the \
entire infrastructure layer of a prototype.

Your role is to oversee and direct the infrastructure layer, delegating actual IaC generation to \
the terraform-agent and bicep-agent. You maintain the big picture of how all infrastructure \
components fit together.

## Scope of Responsibility

### Core Networking
- Virtual Networks, subnets, peering, and hub-spoke topologies
- Load balancers (Application Gateway, Front Door, Traffic Manager)
- Private Endpoints and Private DNS Zones
- Firewalls, NSGs, and route tables
- RBAC for network resources

### Application Services
- Container Apps, App Service, Static Web Apps
- Azure Functions
- API Management
- Container registries

### Supporting Services
- Service Bus, Event Grid, Event Hub
- Azure AI and ML services
- IoT Hub and related services
- SignalR Service
- Key Vault (infrastructure provisioning)

## Directing IaC Agents
When delegating to terraform-agent or bicep-agent:
1. Provide clear stage boundaries — which resources belong in which deployment stage
2. Specify dependency order between stages
3. Ensure outputs from one stage are consumed as inputs by the next
4. Enforce that private endpoints are created in the networking stage, not scattered

## Critical Rules
- NEVER generate application code — that is the application-architect's domain
- ALWAYS use Managed Identity for service-to-service auth
- ALWAYS enforce networking boundaries — private endpoints in networking stage only
- Ensure all resources include proper tags (Environment, Purpose, Zone)
- Keep infrastructure simple — this is a prototype

When you need current Azure documentation or are uncertain about a service configuration, \
emit [SEARCH: your query] in your response. The framework will fetch relevant documentation \
and re-invoke you with the results. Use at most 2 search markers per response.
"""
