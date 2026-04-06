# Application Layer

The layer responsible for all application source code. Owned by the `application-architect`, implemented by language-specific developers (`csharp-developer`, `python-developer`, `react-developer`).

## Owner

- **Primary**: `application-architect`
- **Delegates to**: `csharp-developer`, `python-developer`, `react-developer` (based on technology choices from discovery)
- **Security review**: `security-architect` reviews authentication flows, secret handling, input validation

## Sub-Layers

Every application stage is decomposed into distinct sub-layers. The application-architect assigns each sub-layer to the appropriate developer based on the architecture's technology choices.

### 1. Presentation

UI and frontend code. Framework depends on architecture choices (not hardcoded):

- React/TypeScript frontend -> `react-developer`
- Blazor / .NET MVC -> `csharp-developer`
- Python web framework (Flask, Django templates) -> `python-developer`

**Produces**: Pages, components, routing, static assets, client-side state management.

### 2. Services / API

API endpoints and controllers that expose business logic:

- ASP.NET Core Web API -> `csharp-developer`
- FastAPI / Flask -> `python-developer`
- Express.js -> `react-developer` (Node.js)

**Produces**: API controllers/routes, request/response models, middleware, OpenAPI specs.

### 3. Business Logic

Domain logic, validation rules, and business workflows:

- Same language as the API layer (shared codebase)

**Produces**: Domain models, validation logic, business rule implementations, workflow orchestrators.

### 4. Data Access

Repository pattern, ORM mappings, and database query logic:

- Entity Framework Core -> `csharp-developer`
- SQLAlchemy / motor (async MongoDB) -> `python-developer`
- Prisma / TypeORM -> `react-developer` (Node.js)

**Produces**: Repository interfaces and implementations, ORM entity mappings, migration scripts, query builders.

### 5. Background / Auxiliary

DI-injected cross-cutting concerns and background services:

- Logging abstractions (`ILogger`, OpenTelemetry SDK)
- Messaging interfaces (`IMessageSender`, queue processors)
- External service clients (HTTP clients, SDK wrappers)
- Background workers (hosted services, queue listeners)

**Important**: These are the *application-side abstractions* that interact with Azure resources. The Azure resources themselves (Service Bus namespace, Storage Account) are provisioned by the Infrastructure and Data layers -- not here.

**Produces**: Interface definitions, DI registration, background service implementations, configuration binding.

## What Does NOT Belong Here

- **Azure resource provisioning** (IaC) -- that is Infrastructure or Data layer
- **Network configuration** -- that is Infrastructure layer
- **Database schema creation** (via IaC) -- that is Data layer (but migration scripts in code ARE Application layer)
- **Deployment scripts** for infrastructure -- that is Infrastructure layer

## Key Boundary: Application Code vs Infrastructure

Application layer generates *source code* that runs on compute resources. It does NOT generate Terraform/Bicep. Application stages produce:

- Source files (`.cs`, `.py`, `.tsx`, `.ts`, `.js`)
- Project files (`*.csproj`, `requirements.txt`, `package.json`)
- Dockerfiles and container configuration
- Build and deploy scripts (`deploy.sh` for build+push+update)
- Configuration files (`appsettings.json`, `.env.example`)

Infrastructure references (endpoints, connection strings, secrets) come from environment variables injected at deploy time by the compute platform (Container Apps settings, App Service configuration), NOT from `terraform_remote_state`.

## Deployment Order

Application deploys **last** (before Documentation), after all infrastructure and data services are provisioned:

1. All Core, Infrastructure, and Data stages must complete first
2. Each application stage builds and deploys one deployable unit
3. Multiple app stages run in sequence (API before frontend if frontend calls API)

## Inter-Layer Communication

| Provider | What Application Consumes |
|----------|--------------------------|
| Core | Application Insights connection string (for telemetry) |
| Infrastructure | Container registry login server (for image push), compute endpoint URLs |
| Data | Database connection endpoints, Key Vault URIs, messaging endpoints |

## Governance

- No hardcoded secrets in source code -- use environment variables backed by Key Vault references
- Use managed identity credential libraries (`DefaultAzureCredential`) for all Azure service access
- Follow the project's language-specific standards (STAN-PY, STAN-CS, STAN-CODE)
- Every deployable must include a Dockerfile and `deploy.sh` script
- API endpoints must validate input and return proper error responses
- Background services must handle graceful shutdown
