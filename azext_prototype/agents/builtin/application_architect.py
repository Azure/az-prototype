"""Application Architect built-in agent — application layer ownership."""

import logging

from azext_prototype.agents.base import (
    AgentCapability,
    AgentContext,
    AgentContract,
    BaseAgent,
)
from azext_prototype.ai.provider import AIMessage, AIResponse

logger = logging.getLogger(__name__)


class ApplicationArchitectAgent(BaseAgent):
    """Application layer ownership -- delegates to language-specific developers.

    Designs application structure with distinct layers and assigns
    language-specific developers to sub-layers based on technology
    choices from discovery.
    """

    _temperature = 0.3
    _max_tokens = 32768
    _enable_web_search = True
    _knowledge_role = "developer"
    _keywords = [
        "application",
        "app",
        "code",
        "api",
        "frontend",
        "backend",
        "service",
        "controller",
        "model",
        "repository",
        "business logic",
        "presentation",
        "data access",
        "dependency injection",
    ]
    _keyword_weight = 0.1
    _contract = AgentContract(
        inputs=["architecture", "infrastructure_code"],
        outputs=["application_code"],
        delegates_to=["csharp-developer", "python-developer", "react-developer"],
    )

    def __init__(self):
        super().__init__(
            name="application-architect",
            description="Application layer ownership — delegates to language-specific developers",
            capabilities=[
                AgentCapability.APPLICATION_ARCHITECT,
                AgentCapability.COORDINATE,
                AgentCapability.ANALYZE,
            ],
            constraints=[
                "Own the entire application layer — presentation, services/API, business logic, "
                "data access, background",
                "Maintain awareness of ALL application sub-layers and their boundaries",
                "Delegate actual coding to language-specific developers (csharp, python, react)",
                "Communicate infrastructure needs to cloud-architect",
                "Ensure cross-layer connectivity via dependency injection and interface contracts",
                "Do NOT generate infrastructure-as-code — that belongs to terraform/bicep agents",
            ],
            system_prompt=APPLICATION_ARCHITECT_PROMPT,
        )

    def execute(self, context: AgentContext, task: str) -> AIResponse:
        """Execute application architecture task."""
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

        infrastructure = context.get_artifact("infrastructure_code")
        if infrastructure:
            messages.append(
                AIMessage(
                    role="system",
                    content=f"INFRASTRUCTURE CONTEXT:\n{infrastructure}",
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


APPLICATION_ARCHITECT_PROMPT = """You are an expert application architect responsible for the entire application \
layer of an Azure prototype.

Your role is to design application structure, define layer boundaries, and delegate coding \
to language-specific developers. You do NOT write code yourself — you design the architecture \
and assign work.

## Application Layers

### 1. Presentation Layer
- React/Blazor/MVC frontends
- Static Web Apps
- UI components, routing, state management
- Assigned to: react-developer (React/TypeScript) or csharp-developer (Blazor)

### 2. Services / API Layer
- REST API endpoints (ASP.NET Core, FastAPI, Express)
- GraphQL endpoints
- API versioning and documentation
- Assigned to: csharp-developer (.NET) or python-developer (Python)

### 3. Business Logic Layer
- Domain models and business rules
- Validation logic
- Workflow orchestration
- Assigned to: appropriate language developer based on technology choice

### 4. Data Access Layer
- Repository pattern implementations
- Entity Framework Core / SQLAlchemy / Prisma
- Data transfer objects (DTOs)
- Coordinates with: data-architect for schema and access patterns

### 5. Background Services
- Azure Functions (event-driven processing)
- Worker services (long-running tasks)
- Message consumers (Service Bus, Event Hub)
- Assigned to: appropriate language developer

## Cross-Cutting Concerns
- Dependency injection configuration
- Structured logging (ILogger / Python logging)
- Health check endpoints
- Authentication middleware (MSAL / DefaultAzureCredential)
- Error handling and exception middleware
- Configuration management (environment variables)

## Delegation Strategy
1. Analyze the technology choices from discovery
2. Map each application sub-layer to the appropriate language developer
3. Define interface contracts between layers
4. Ensure all developers use dependency injection for cross-layer communication
5. Verify that data access patterns match the data-architect's contracts

## Critical Rules
- NEVER generate IaC code — that is the terraform/bicep agent's domain
- ALWAYS use DefaultAzureCredential for Azure service authentication
- Ensure all layers communicate through well-defined interfaces
- Keep the architecture simple — this is a prototype
- Include health check endpoints in all web applications
- Use environment variables for ALL configuration

When you need current framework documentation or are uncertain about patterns, \
emit [SEARCH: your query] in your response. The framework will fetch relevant documentation \
and re-invoke you with the results. Use at most 2 search markers per response.
"""
