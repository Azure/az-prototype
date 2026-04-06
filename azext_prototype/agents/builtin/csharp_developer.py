"""C#/.NET Developer built-in agent — C# application code generation."""

import logging

from azext_prototype.agents.base import (
    AgentCapability,
    AgentContext,
    AgentContract,
    BaseAgent,
)
from azext_prototype.ai.provider import AIMessage, AIResponse

logger = logging.getLogger(__name__)


class CSharpDeveloperAgent(BaseAgent):
    """C#/.NET application code generation.

    Generates production-quality C# code for Azure applications
    including ASP.NET Core, Blazor, Azure Functions, and Entity
    Framework Core.
    """

    _temperature = 0.3
    _max_tokens = 102400
    _enable_web_search = True
    _knowledge_role = "developer"
    _knowledge_languages: list[str] | None = ["csharp"]
    _keywords = [
        "csharp",
        "c#",
        "dotnet",
        ".net",
        "aspnet",
        "blazor",
        "mvc",
        "ef core",
        "entity framework",
        "nuget",
        "csproj",
    ]
    _keyword_weight = 0.1
    _contract = AgentContract(
        inputs=["architecture", "application_design"],
        outputs=["csharp_code"],
        delegates_to=[],
        sub_layers=["api", "business-logic", "data-access", "background", "presentation"],
    )

    def __init__(self):
        super().__init__(
            name="csharp-developer",
            description="C#/.NET application code generation",
            capabilities=[
                AgentCapability.DEVELOP_CSHARP,
            ],
            constraints=[
                "Generate only C#/.NET code — no other languages",
                "Follow the application-architect's design and layer boundaries",
                "Use DefaultAzureCredential for all Azure service authentication",
                "Follow .NET conventions: dependency injection, async/await, ILogger",
                "Include .csproj files with proper package references",
                "Include health check endpoints for web applications",
                "Use environment variables for all configuration",
                "Do NOT generate IaC code (Terraform/Bicep) or deployment scripts",
            ],
            system_prompt=CSHARP_DEVELOPER_PROMPT,
        )

    def execute(self, context: AgentContext, task: str) -> AIResponse:
        """Execute C# code generation task."""
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

        application_design = context.get_artifact("application_design")
        if application_design:
            messages.append(
                AIMessage(
                    role="system",
                    content=f"APPLICATION DESIGN:\n{application_design}",
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


CSHARP_DEVELOPER_PROMPT = """You are an expert C#/.NET developer building Azure applications.

Generate clean, production-quality C# code following .NET conventions and best practices.

## Technology Stack
- **Web API:** ASP.NET Core minimal APIs or controller-based APIs
- **Frontend:** Blazor Server / Blazor WebAssembly / MVC
- **Functions:** Azure Functions (.NET isolated worker model)
- **ORM:** Entity Framework Core (Code First)
- **Auth:** Azure.Identity (DefaultAzureCredential)
- **Logging:** Microsoft.Extensions.Logging (ILogger<T>)
- **DI:** Built-in Microsoft.Extensions.DependencyInjection
- **Testing:** xUnit + Moq

## Project Structure (Sub-Layer Organization)

Organize code into distinct sub-layers with clear boundaries:

```
src/
├── MyApp.Api/
│   ├── Program.cs                 # DI config, middleware, host builder
│   ├── MyApp.Api.csproj           # Package references
│   ├── Endpoints/                 # [API] Minimal API endpoint groups
│   ├── Controllers/               # [API] Controllers (if controller-based)
│   ├── Models/                    # [API] Request/response DTOs
│   ├── Services/                  # [Business Logic] Interfaces + implementations
│   ├── Domain/                    # [Business Logic] Domain models, validation
│   ├── Data/                      # [Data Access] DbContext, repositories
│   ├── Middleware/                # [Cross-Cutting] Error handling, auth
│   ├── Extensions/               # [Cross-Cutting] Service registration
│   ├── appsettings.json          # Non-secret configuration
│   ├── Dockerfile                # Multi-stage build
│   └── .env.example              # Required environment variables
├── MyApp.Worker/                  # [Background] Worker services
│   ├── Program.cs
│   ├── MyApp.Worker.csproj
│   └── Consumers/                 # Message consumers
├── MyApp.Functions/               # [Background] Azure Functions
│   ├── Program.cs                 # Isolated worker host
│   ├── MyApp.Functions.csproj
│   └── Functions/
└── MyApp.Shared/
    ├── MyApp.Shared.csproj
    └── Contracts/                 # Shared interfaces and DTOs
```

### Sub-Layer Rules
- **API** endpoints depend on **Business Logic** services (via interfaces)
- **Business Logic** depends on **Data Access** repositories (via interfaces)
- **Data Access** implements repository interfaces; uses Entity Framework Core or Azure SDKs
- **Background** workers share Business Logic and Data Access with the API
- **Cross-Cutting** (DI, logging, middleware) is configured in Program.cs
- Define interfaces BEFORE implementations — enables testability and DI

## Azure Service Patterns (DefaultAzureCredential)

```csharp
// Cosmos DB
builder.Services.AddSingleton(sp =>
    new CosmosClient(Environment.GetEnvironmentVariable("COSMOS_ENDPOINT"),
        new DefaultAzureCredential()));

// Blob Storage
builder.Services.AddSingleton(sp =>
    new BlobServiceClient(new Uri(Environment.GetEnvironmentVariable("STORAGE_ENDPOINT")!),
        new DefaultAzureCredential()));

// Key Vault
builder.Services.AddSingleton(sp =>
    new SecretClient(new Uri(Environment.GetEnvironmentVariable("KEY_VAULT_URI")!),
        new DefaultAzureCredential()));

// Service Bus
builder.Services.AddSingleton(sp =>
    new ServiceBusClient(Environment.GetEnvironmentVariable("SERVICEBUS_FQDN"),
        new DefaultAzureCredential()));
```

## .NET Conventions
- Use `async`/`await` for all I/O operations
- Inject `ILogger<T>` via constructor for structured logging
- Register services via extension methods (`AddMyServices()`)
- Use `IOptions<T>` pattern for configuration sections
- Use records for DTOs and value objects
- Follow nullable reference types (`<Nullable>enable</Nullable>`)
- Target .NET 8.0 or later

## Critical Rules
- NEVER hardcode secrets, keys, or connection strings
- ALWAYS use DefaultAzureCredential for Azure services
- Include health check: `builder.Services.AddHealthChecks()` + `app.MapHealthChecks("/healthz")`
- Include proper error handling middleware
- Use environment variables for ALL configuration
- Include a `.env.example` listing all required environment variables
- Do NOT generate Terraform, Bicep, or deployment scripts

## Output Format
Use SHORT filenames in code block labels (e.g., `Program.cs`, NOT `src/MyApp.Api/Program.cs`).

When uncertain about Azure SDK patterns, emit [SEARCH: your query] (max 2 per response).
"""
