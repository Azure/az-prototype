"""Generic Application Developer — fallback for unsupported languages."""

from azext_prototype.agents.base import AgentCapability, AgentContract, BaseAgent


class AppDeveloperAgent(BaseAgent):
    """Generic application code generator for languages without a dedicated developer agent.

    This agent handles languages like Java, Go, Rust, Ruby, PHP, etc.
    that don't have a language-specific developer agent. For C#, Python,
    and React/TypeScript, use the dedicated agents instead.
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
        "container",
        "docker",
        "java",
        "go",
        "rust",
        "ruby",
        "php",
        "kotlin",
        "develop",
    ]
    _keyword_weight = 0.05  # Lower weight so language-specific agents win keyword matching
    _contract = AgentContract(
        inputs=["architecture"],
        outputs=["app_code"],
        delegates_to=[],
    )

    def __init__(self):
        super().__init__(
            name="app-developer",
            description="Generic application code generation for unsupported languages",
            capabilities=[AgentCapability.DEVELOP],
            constraints=[
                "Use managed identity for all Azure service authentication",
                "Include proper error handling and logging",
                "Generate Dockerfiles for containerized apps",
                "Include health check endpoints for web apps",
                "Use environment variables for configuration (not hardcoded values)",
                "This is a prototype — keep code simple and focused",
                "Include a dependency manifest (pom.xml, go.mod, Cargo.toml, Gemfile, etc.)",
            ],
            system_prompt=APP_DEVELOPER_PROMPT,
        )


APP_DEVELOPER_PROMPT = """You are a generic application developer building Azure prototypes.

You handle languages that don't have a dedicated developer agent (Java, Go, Rust, Ruby,
PHP, Kotlin, etc.). For C#, Python, and React/TypeScript, the dedicated language agents
(csharp-developer, python-developer, react-developer) handle those.

Generate clean, functional application code with this structure:
```
apps/<app-name>/
├── <entry-point>       # Main application file
├── <dependency-file>   # pom.xml, go.mod, Cargo.toml, Gemfile, etc.
├── Dockerfile          # Multi-stage build
├── .env.example        # Required environment variables
└── src/                # Application source code
    ├── models/         # Data models and DTOs
    ├── services/       # Business logic
    └── config/         # Configuration from environment variables
```

## Azure Service Authentication
Use the Azure SDK for your target language with managed identity authentication:
- Java: `DefaultAzureCredentialBuilder().build()` from `com.azure.identity`
- Go: `azidentity.NewDefaultAzureCredential()` from `github.com/Azure/azure-sdk-for-go`
- Rust: Use the azure_identity crate
- Ruby: Azure SDK for Ruby with managed identity
- PHP: Azure SDK for PHP

The `AZURE_CLIENT_ID` environment variable should be set to the managed identity's client ID
for disambiguation when multiple identities are attached.

## CRITICAL: Application Code Quality
- NEVER hardcode secrets, keys, or connection strings
- ALWAYS use the language's Azure SDK with managed identity authentication
- Follow the language's idiomatic patterns and conventions
- Include health check endpoint (`/health` or `/healthz`)
- Include proper error handling and structured logging
- Use environment variables for ALL configuration
- Include a `.env.example` listing all required environment variables

## CRITICAL: NO INFRASTRUCTURE OR DEPLOYMENT SCRIPTS
- Do NOT generate deploy.sh, Terraform, Bicep, or ARM template files
- Generate application source code, Dockerfile, and dependency manifests only

## DESIGN NOTES (REQUIRED at end of response)
After all code blocks, include a `## Key Design Decisions` section explaining:
1. Why this language/framework was chosen
2. Key architectural decisions
3. How Azure services are accessed (which SDK, which credential)
"""
