# az prototype — Feature Overview

**Idea to deployed Azure prototype in four commands.**

`az prototype init` → `az prototype design` → `az prototype build` → `az prototype deploy`

---

## AI-Driven Discovery & Design

- Conversational requirements gathering through a joint business-analyst + cloud-architect session
- Automatic gap detection — surfaces missing requirements, unstated assumptions, and architectural conflicts
- Binary artifact ingestion — feed in PDFs, DOCX, PPTX, XLSX, images, and screenshots as design inputs
- Embedded image extraction from documents sent to the AI via vision API
- Explicit scope tracking — in-scope, out-of-scope, and deferred items carried through every stage
- Cost-aware discovery — pricing models and relative cost comparisons surfaced during service selection
- Template-aware suggestions — workload templates recommended when requirements match known patterns
- Re-entrant design — return to refine architecture at any point without starting over

## Multi-Agent System

- 11 built-in AI agents with specialized roles: architecture, infrastructure, application code, security, monitoring, QA, cost analysis, documentation, project management, and business analysis
- Three-tier agent resolution — custom agents override built-in agents, or extend the system with new roles
- Formal agent contracts — declared inputs, outputs, and delegation targets for dependency validation
- Parallel execution — independent agent tasks run concurrently with automatic dependency ordering
- Agent testing and export — validate any agent with a test prompt, export definitions as portable YAML
- Interactive agent creation — guided walkthrough to build custom agents with system prompts and examples

## Infrastructure as Code Generation

- Terraform and Bicep support — choose your IaC tool at project init
- Fine-grained deployment staging — each infrastructure component, database, and application gets its own dependency-ordered stage
- Cross-stage dependency management — stages reference prior-stage outputs via Terraform remote state or Bicep parameters, never hardcoded names
- Companion resource enforcement — disabling key-based auth on any service automatically requires managed identity and RBAC role assignments in the same stage
- Complete deployment scripts — every stage includes a runnable deploy.sh with error handling, output capture, and post-deployment verification
- Workload templates as optional starting points — web apps, serverless APIs, microservices, AI applications, and data pipelines
- Computed resource names — every service carries its resolved name via your chosen naming strategy, ARM resource type, and SKU
- Per-stage policy enforcement — generated code checked against governance policies after each stage
- Conversational review loop — provide feedback to regenerate specific stages before accepting the build

## Interactive Deployment

- Staged deployments with progress tracking, preflight checks, and QA-first error routing
- Preflight validation — subscription, resource providers, resource group, and IaC tool verified before any deployment runs
- Ordered rollback — roll back individual stages or all stages in reverse dependency order
- Dry-run mode — Terraform plan / Bicep What-If preview without executing
- Output capture — persists Terraform and Bicep outputs to JSON and exports environment variables for downstream stages
- Slash commands during deployment — `/status`, `/deploy`, `/rollback`, `/redeploy`, `/plan`, `/outputs`, `/preflight`
- Application deploy script generation — auto-generates `deploy.sh` for App Service, Container Apps, and Azure Functions application stages

## Policy-Driven Governance

- 13 built-in governance policies covering Container Apps, Cosmos DB, Key Vault, SQL Database, Storage, App Service, Azure Functions, Monitoring, Managed Identity, Network Isolation, API Management, Authentication, and Data Protection
- Severity levels — required, recommended, and optional — with interactive resolution for violations
- Governance context automatically injected into every agent's system prompt
- Custom policies supported via project-level `.prototype/policies/` directory
- Policy validation CLI for pre-commit hooks and CI pipelines

## Design Standards

- Prescriptive design principles and reference patterns injected into agent system prompts during generation
- 5 standard categories — design principles, coding conventions, Terraform module structure, Bicep module structure, and application patterns
- Terraform standards enforce cross-stage dependencies via remote state, consistent backend configuration, complete stage outputs, robust deployment scripts, and companion resources when authentication is disabled
- Bicep standards enforce cross-stage parameter passing, module composition, output completeness, and companion identity resources
- Application standards for Python and .NET — DefaultAzureCredential, project structure, configuration externalization, async patterns, and isolated worker model for Azure Functions
- Per-agent opt-in — standards injected only into agents that generate code, not analysts or diagnostics agents

## Anti-Pattern Detection

- Post-generation scanning across 9 domains — security, authentication, networking, storage, containers, encryption, monitoring, cost, and completeness
- Pattern matching with safe-pattern exemptions — detects issues while ignoring intentional usage
- Security domain detects hardcoded credentials, admin passwords, disabled encryption, and sensitive Terraform/Bicep outputs that should be omitted
- Completeness domain detects disabled authentication without companion managed identity and RBAC, hardcoded cross-stage resource references, and incomplete deployment scripts
- Anti-pattern warnings surfaced during build for interactive accept, override, or regenerate resolution
- Custom anti-patterns supported via project-level `.prototype/anti_patterns/` directory

## Knowledge System

- 25 Azure service knowledge files with Terraform patterns, Bicep patterns, application code, common pitfalls, RBAC requirements, private endpoint configuration, and production backlog items
- 6 role-based knowledge templates — architect, infrastructure, developer, analyst, security reviewer, monitoring
- 4 tool pattern files and 4 language pattern files for consistent code generation
- Token-budgeted context composition — knowledge loaded within configurable token limits
- POC vs. production annotations — knowledge files distinguish what's appropriate now vs. what belongs in the backlog
- Community contributions — submit knowledge findings as structured GitHub Issues from the CLI or automatically after QA diagnosis

## Runtime Documentation Access

- Agents fetch live Azure documentation during execution via web search
- In-memory search cache with TTL-based expiry shared across agents in a session
- Five agents enabled for runtime lookups — architecture, Terraform, Bicep, application development, and QA

## MCP Server Integration

- Model Context Protocol handler-based plugin system for extending agents with external tools
- Handlers own their transport, authentication, and protocol — JSON-RPC over HTTP, stdio, or custom
- Per-stage and per-agent scoping — restrict tools to specific build phases or agent roles
- AI-driven tool calling — agents discover available tools and invoke them through the standard OpenAI function-calling loop
- Code-driven tool calling — stages can proactively invoke MCP tools outside the AI loop
- Circuit breaker with automatic disable after consecutive failures
- Lazy connection management with configurable timeouts and retry limits
- Builtin and custom handler resolution following the same registry pattern as agents
- Custom handlers loaded from `.prototype/mcp/` Python files at runtime

## Security & Quality Assurance

- Pre-deployment IaC security scanning — RBAC over-privilege, public endpoints, missing encryption, hardcoded secrets
- Findings classified as blockers (must fix) or warnings (can defer)
- Automatic QA remediation — QA findings routed back to the IaC agent for regeneration, then re-reviewed before presenting to the user
- QA-first error routing — all failures across every stage route to QA for diagnosis before user action
- Mandatory QA review checklist — authentication completeness, cross-stage references, script validity, output completeness, structural consistency, and code completeness
- Four-level escalation chain — documented solutions, architect/PM review, web search, human escalation
- Escalation tracking with timeout-based auto-escalation

## Monitoring & Observability Generation

- Azure Monitor alerts, diagnostic settings, Application Insights configuration, and dashboard generation
- POC-appropriate defaults — failure alerts, latency tracking, resource health monitoring

## Backlog & Project Management

- AI-generated backlog from architecture design — structured epics, user stories, and tasks
- Scope-aware decomposition — in-scope items become stories, deferred items get a separate epic
- Push to GitHub Issues or Azure DevOps work items with parent-child linking
- Interactive review session — add, update, remove, and refine items before pushing
- Quick mode for streamlined generate-confirm-push workflows

## Documentation Generation

- 6 document templates — Architecture, Deployment, Development, Configuration, As-Built, and Cost Estimate
- AI-populated content — doc agent fills templates with real architecture details when design context is available
- Spec-kit bundle generation with manifest metadata

## Cost Analysis

- Cost estimation at Small, Medium, and Large t-shirt sizes using the Azure Retail Prices API
- Cached results — re-running returns cached estimates unless the design changes
- JSON output for integration with external tools

## Configuration & Naming

- 4 built-in naming strategies — Microsoft ALZ, Microsoft CAF, Simple, and Enterprise — plus fully custom patterns
- YAML-based project configuration with automatic secrets separation
- Sensitive values (API keys, subscription IDs, tokens) isolated to a git-ignored secrets file
- Interactive configuration wizard with validation for regions, IaC tools, and naming strategies

## AI Provider Flexibility

- Three backends — GitHub Copilot, GitHub Models, and Azure OpenAI
- Provider allowlisting — non-Azure providers are blocked
- Managed identity and API key authentication for Azure OpenAI
- Copilot Business and Enterprise license validation
- Streaming support across all providers

## Developer Experience

- Claude Code-inspired interactive sessions with bordered prompts, spinners, and progress indicators
- Token usage tracking displayed after every AI response across all sessions
- Unified session-ending vocabulary — `done`, `finish`, `accept`, `lgtm` work everywhere
- Slash commands in every interactive session for status, navigation, and control
- `--status` flag on every stage to check progress without starting a session
- `--reset` flag on every stage to clear state and start fresh
- Rich console UI with themed, emoji-free output suitable for terminals and CI
- Telemetry with Application Insights — opt-out respected, sensitive values redacted, failures silent
