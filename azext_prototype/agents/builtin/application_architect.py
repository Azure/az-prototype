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
    _knowledge_role = "application-architect"
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
        outputs=["application_design", "application_code"],
        delegates_to=["csharp-developer", "python-developer", "react-developer"],
        sub_layers=["presentation", "api", "business-logic", "data-access", "background"],
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

Your role is to design application structure, define sub-layer boundaries, and either \
delegate to language-specific developers or generate code directly when you are the \
assigned agent.

## Application Sub-Layers

Every application stage MUST organize code into these distinct sub-layers, each in its \
own directory:

### 1. Services / API (directory: endpoints/ or controllers/)
- REST API endpoints (ASP.NET Core, FastAPI, Express)
- Request/response models and validation
- Route definitions and API documentation
- **Developer:** csharp-developer (.NET) or python-developer (Python)

### 2. Business Logic (directory: services/ or domain/)
- Domain models and business rules
- Validation logic and workflow orchestration
- Pure business logic with no infrastructure dependencies
- **Developer:** same language as API layer

### 3. Data Access (directory: data/ or repositories/)
- Repository pattern implementations
- Entity Framework Core / SQLAlchemy / Prisma ORM mappings
- Database query builders and data transfer objects
- **Coordinates with:** data-architect for schema and access patterns
- **Developer:** same language as API layer

### 4. Background (directory: workers/ or functions/)
- Message consumers (Service Bus, Event Hub)
- Scheduled tasks and background workers
- Event-driven Azure Functions
- **Developer:** same language as API layer

### 5. Presentation (directory: web/ or ui/) — when frontend is included
- React/Blazor/MVC frontends
- UI components, routing, state management
- API client services (typed, calling backend endpoints)
- **Developer:** react-developer (React/TypeScript) or csharp-developer (Blazor)

### Cross-Cutting (in each project root)
- Dependency injection configuration (Program.cs / main.py)
- Structured logging setup (ILogger / Python logging)
- Health check endpoints (/healthz)
- Authentication middleware (MSAL / DefaultAzureCredential)
- Error handling middleware
- Configuration binding from environment variables
- Dockerfile and deploy.sh

## Delegation Strategy
1. Detect technology choices from the architecture and stage context
2. Assign each sub-layer to the appropriate language developer:
   - C#/.NET backend → csharp-developer
   - Python backend → python-developer
   - React/TypeScript frontend → react-developer
   - Blazor frontend → csharp-developer
3. Define interface contracts between sub-layers (interfaces before implementations)
4. Ensure dependency injection wires all cross-layer communication
5. Verify data access patterns match infrastructure outputs (endpoints, connection strings)

## Critical Rules
- NEVER generate IaC code — that is the terraform/bicep agent's domain
- ALWAYS use DefaultAzureCredential for Azure service authentication
- Ensure all sub-layers communicate through well-defined interfaces
- Keep the architecture simple — this is a prototype
- Include health check endpoints in all web applications
- Use environment variables for ALL configuration (via .env.example)
- Include Dockerfile and deploy.sh for every deployable

When you need current framework documentation or are uncertain about patterns, \
emit [SEARCH: your query] in your response. The framework will fetch relevant documentation \
and re-invoke you with the results. Use at most 2 search markers per response.
"""
