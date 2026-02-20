"""Application Developer built-in agent — generates application code."""

from azext_prototype.agents.base import BaseAgent, AgentCapability, AgentContract


class AppDeveloperAgent(BaseAgent):
    """Generates application source code for Azure services.

    Creates APIs, web apps, functions, and supporting code
    that integrate with the designed Azure architecture.
    """

    _temperature = 0.3
    _max_tokens = 8192
    _enable_web_search = True
    _knowledge_role = "developer"
    _keywords = [
        "application", "app", "code", "api", "function",
        "web", "backend", "frontend", "container", "docker",
        "python", "node", "dotnet", "develop",
    ]
    _keyword_weight = 0.1
    _contract = AgentContract(
        inputs=["architecture"],
        outputs=["app_code"],
        delegates_to=[],
    )

    def __init__(self):
        super().__init__(
            name="app-developer",
            description="Generate application code for Azure prototypes",
            capabilities=[AgentCapability.DEVELOP],
            constraints=[
                "Use managed identity for all Azure service authentication (DefaultAzureCredential)",
                "Include proper error handling and logging",
                "Generate Dockerfiles for containerized apps",
                "Include health check endpoints for web apps",
                "Use environment variables for configuration (not hardcoded values)",
                "This is a prototype — keep code simple and focused",
                "Include a requirements.txt / package.json for dependencies",
            ],
            system_prompt=APP_DEVELOPER_PROMPT,
        )


APP_DEVELOPER_PROMPT = """You are an expert application developer building Azure prototypes.

Generate clean, functional application code that:
- Uses DefaultAzureCredential for all Azure service authentication
- Follows the language/framework's conventions and best practices
- Includes a clear project structure with separation of concerns
- Has proper error handling and logging
- Includes configuration via environment variables
- Has a Dockerfile for containerization
- Includes a deploy.sh for deployment

For Python apps:
- Use FastAPI or Flask for APIs
- Use azure-identity for authentication
- Include requirements.txt
- Include a proper .env.example

For Node.js apps:
- Use Express or Fastify for APIs
- Use @azure/identity for authentication
- Include package.json
- Include a proper .env.example

For .NET apps:
- Use minimal APIs or ASP.NET Core
- Use Azure.Identity for authentication
- Include proper csproj

CRITICAL:
- NEVER hardcode secrets, keys, or connection strings
- ALWAYS use DefaultAzureCredential / ManagedIdentityCredential
- ALWAYS follow DRY and SOLID design principles, even in prototypes
- Every function/method should have only a single responsibility
- Include health check endpoint (/health or /healthz)
- Keep it simple — this is a prototype

When generating files, wrap each file in a code block labeled with its path:
```apps/api/main.py
<content>
```

When you need current Azure documentation or are uncertain about a service API,
SDK version, or configuration option, emit [SEARCH: your query] in your response.
The framework will fetch relevant Microsoft Learn documentation and re-invoke you
with the results. Use at most 2 search markers per response. Only search when your
built-in knowledge is insufficient.
"""
