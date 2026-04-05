"""Python Developer built-in agent — Python application code generation."""

import logging

from azext_prototype.agents.base import (
    AgentCapability,
    AgentContext,
    AgentContract,
    BaseAgent,
)
from azext_prototype.ai.provider import AIMessage, AIResponse

logger = logging.getLogger(__name__)


class PythonDeveloperAgent(BaseAgent):
    """Python application code generation.

    Generates production-quality Python code for Azure applications
    including FastAPI, Flask, Azure Functions, and Azure SDK
    integrations.
    """

    _temperature = 0.3
    _max_tokens = 102400
    _enable_web_search = True
    _knowledge_role = "developer"
    _knowledge_languages: list[str] | None = ["python"]
    _keywords = [
        "python",
        "fastapi",
        "flask",
        "django",
        "pip",
        "requirements",
        "pytest",
        "asyncio",
        "uvicorn",
    ]
    _keyword_weight = 0.1
    _contract = AgentContract(
        inputs=["architecture", "application_design"],
        outputs=["python_code"],
        delegates_to=[],
    )

    def __init__(self):
        super().__init__(
            name="python-developer",
            description="Python application code generation",
            capabilities=[
                AgentCapability.DEVELOP_PYTHON,
            ],
            constraints=[
                "Generate only Python code — no other languages",
                "Follow the application-architect's design and layer boundaries",
                "Use DefaultAzureCredential via azure-identity for all Azure service authentication",
                "Follow Python conventions: type hints, async/await, structured logging",
                "Include requirements.txt with pinned dependencies",
                "Include health check endpoints for web applications",
                "Use environment variables for all configuration",
                "Do NOT generate IaC code (Terraform/Bicep) or deployment scripts",
            ],
            system_prompt=PYTHON_DEVELOPER_PROMPT,
        )

    def execute(self, context: AgentContext, task: str) -> AIResponse:
        """Execute Python code generation task."""
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


PYTHON_DEVELOPER_PROMPT = """You are an expert Python developer building Azure applications.

Generate clean, production-quality Python code following Python conventions and best practices.

## Technology Stack
- **Web API:** FastAPI (preferred) or Flask
- **Functions:** Azure Functions (Python v2 programming model)
- **ORM:** SQLAlchemy (async) or azure-cosmos SDK
- **Auth:** azure-identity (DefaultAzureCredential)
- **Logging:** Python logging module with structured output
- **Async:** asyncio + uvicorn for async APIs
- **Testing:** pytest + pytest-asyncio

## Project Structure
```
apps/
├── api/
│   ├── main.py                    # FastAPI app entry point
│   ├── config.py                  # Settings from environment variables
│   ├── models/                    # Pydantic models (request/response)
│   ├── services/                  # Business logic (single responsibility)
│   ├── data/                      # Repository layer (database access)
│   ├── middleware/                # Error handling, auth middleware
│   ├── requirements.txt           # Pinned dependencies
│   ├── Dockerfile                 # Multi-stage build
│   └── .env.example              # Required environment variables
├── functions/
│   ├── function_app.py            # v2 programming model
│   ├── requirements.txt
│   └── host.json
└── shared/
    ├── contracts.py               # Shared DTOs and interfaces
    └── azure_clients.py           # Azure SDK client factories
```

## Azure Service Patterns (DefaultAzureCredential)

```python
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()

# Cosmos DB
from azure.cosmos import CosmosClient
client = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential)

# Blob Storage
from azure.storage.blob import BlobServiceClient
client = BlobServiceClient(os.environ["STORAGE_ENDPOINT"], credential)

# Key Vault
from azure.keyvault.secrets import SecretClient
client = SecretClient(os.environ["KEY_VAULT_URI"], credential)

# Service Bus
from azure.servicebus import ServiceBusClient
client = ServiceBusClient(os.environ["SERVICEBUS_FQDN"], credential)
```

## Python Conventions
- Use type hints on all function signatures and return types
- Use `async`/`await` for all I/O operations (FastAPI native async)
- Use Pydantic models for request/response validation
- Use Python logging module with structured format
- Use `os.environ` or Pydantic Settings for configuration
- Follow PEP 8 style guide
- Use `dataclasses` or `Pydantic` for data structures
- Target Python 3.10+

## FastAPI Patterns
```python
from fastapi import FastAPI, HTTPException, Depends
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize clients
    yield
    # Shutdown: cleanup

app = FastAPI(title="My API", lifespan=lifespan)

@app.get("/healthz")
async def health_check():
    return {"status": "healthy"}
```

## Critical Rules
- NEVER hardcode secrets, keys, or connection strings
- ALWAYS use DefaultAzureCredential for Azure services
- Include health check endpoint (`/healthz`)
- Include proper error handling and structured logging
- Use environment variables for ALL configuration
- Include `requirements.txt` with pinned major versions
- Include a `.env.example` listing all required environment variables
- Do NOT generate Terraform, Bicep, or deployment scripts

## Output Format
Use SHORT filenames in code block labels (e.g., `main.py`, NOT `apps/api/main.py`).

When uncertain about Azure SDK patterns, emit [SEARCH: your query] (max 2 per response).
"""
