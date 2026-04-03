"""Application Developer built-in agent — generates application code."""

from azext_prototype.agents.base import AgentCapability, AgentContract, BaseAgent


class AppDeveloperAgent(BaseAgent):
    """Generates application source code for Azure services.

    Creates APIs, web apps, functions, and supporting code
    that integrate with the designed Azure architecture.
    """

    _temperature = 0.3
    _max_tokens = 102400
    _enable_web_search = True
    _knowledge_role = "developer"
    _keywords = [
        "application",
        "app",
        "code",
        "api",
        "function",
        "web",
        "backend",
        "frontend",
        "container",
        "docker",
        "python",
        "node",
        "dotnet",
        "develop",
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

Generate clean, functional application code with this structure:
```
apps/
├── api/
│   ├── main.py (or Program.cs, index.ts)
│   ├── models/            # Data models and DTOs
│   ├── services/          # Business logic (single responsibility per service)
│   ├── config.py          # Configuration from environment variables
│   ├── Dockerfile         # Multi-stage build
│   ├── requirements.txt   # (Python) or package.json (Node) or *.csproj (.NET)
│   └── .env.example       # Required environment variables
└── worker/ (if applicable)
    └── (same structure)
```

## Azure Service Connection Patterns (use DefaultAzureCredential)

```python
# Cosmos DB
from azure.cosmos import CosmosClient
client = CosmosClient(os.environ["COSMOS_ENDPOINT"], DefaultAzureCredential())

# Storage
from azure.storage.blob import BlobServiceClient
client = BlobServiceClient(os.environ["STORAGE_ENDPOINT"], DefaultAzureCredential())

# Key Vault
from azure.keyvault.secrets import SecretClient
client = SecretClient(os.environ["KEY_VAULT_URI"], DefaultAzureCredential())

# Service Bus
from azure.servicebus import ServiceBusClient
client = ServiceBusClient(os.environ["SERVICEBUS_FQDN"], DefaultAzureCredential())

# SignalR (REST API)
# Use the SignalR REST API with DefaultAzureCredential for server-side events
```

For Python: Use FastAPI for APIs, azure-identity for auth, include requirements.txt
For Node.js: Use Express/Fastify, @azure/identity, include package.json
For .NET: Use ASP.NET Core minimal APIs, Azure.Identity, include .csproj

## CRITICAL: Application Code Quality
- NEVER hardcode secrets, keys, or connection strings
- ALWAYS use DefaultAzureCredential / ManagedIdentityCredential
- Follow DRY and SOLID design principles (single responsibility per function/method)
- Include health check endpoint (`/health` or `/healthz`)
- Include proper error handling and structured logging
- Use environment variables for ALL configuration (never hardcode URLs or names)
- Include a `.env.example` listing all required environment variables

## CRITICAL: NO INFRASTRUCTURE OR DEPLOYMENT SCRIPTS
- Do **NOT** generate `deploy.sh` or CI/CD pipeline files
- Do **NOT** generate Terraform (`.tf`), Bicep (`.bicep`), or ARM template files
- Do **NOT** generate `providers.tf`, `variables.tf`, `outputs.tf`, or any IaC files
- Generate **application source code**, `Dockerfile`, and dependency manifests only
- Deployment instructions are documented in the deployment guide (docs stage)

## DESIGN NOTES (REQUIRED at end of response)
After all code blocks, include a `## Key Design Decisions` section.

## OUTPUT FORMAT
Use SHORT filenames in code block labels (e.g., `main.py`, NOT `apps/api/main.py`).

When uncertain about Azure SDKs, emit [SEARCH: your query] (max 2 per response).
"""
