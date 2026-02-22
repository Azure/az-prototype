# az prototype

> [!NOTE]
> This reference is part of the **prototype** extension for the Azure CLI (version 2.50+). The extension will automatically install the first time you run an `az prototype` command. [Learn more](README.md) about extensions.

> [!IMPORTANT]
> This command group is in **Preview**. It may change before reaching general availability.

Rapidly create Azure prototypes using AI-driven agent teams.

The `az prototype` extension empowers you to build functional Azure prototypes using intelligent agent teams powered by GitHub Copilot or Azure OpenAI.

**Workflow:** `init` → `design` → `build` → `deploy`

Each stage can be run independently (with prerequisite guards) and most stages are re-entrant — you can return to refine your design or rebuild specific components.

Analysis commands let you diagnose errors and estimate costs at any point.

## Commands

| Command | Description | Status |
|---|---|---|
| [az prototype init](#az-prototype-init) | Initialize a new prototype project. | Preview |
| [az prototype design](#az-prototype-design) | Analyze requirements and generate architecture design. | Preview |
| [az prototype build](#az-prototype-build) | Generate infrastructure and application code in staged output. | Preview |
| [az prototype deploy](#az-prototype-deploy) | Deploy prototype to Azure with staged, incremental deployments. | Preview |
| [az prototype deploy outputs](#az-prototype-deploy-outputs) | Show captured deployment outputs. | Preview |
| [az prototype deploy rollback-info](#az-prototype-deploy-rollback-info) | Show rollback instructions based on deployment history. | Preview |
| [az prototype deploy generate-scripts](#az-prototype-deploy-generate-scripts) | Generate deploy scripts for application directories. | Preview |
| [az prototype status](#az-prototype-status) | Show current project status across all stages. | Preview |
| [az prototype analyze](#az-prototype-analyze) | Analyze errors, costs, and diagnostics for the prototype. | Preview |
| [az prototype analyze error](#az-prototype-analyze-error) | Analyze an error and get a fix with redeployment instructions. | Preview |
| [az prototype analyze costs](#az-prototype-analyze-costs) | Estimate Azure costs at Small/Medium/Large t-shirt sizes. | Preview |
| [az prototype config](#az-prototype-config) | Manage prototype project configuration. | Preview |
| [az prototype config init](#az-prototype-config-init) | Interactive setup to create a prototype.yaml configuration file. | Preview |
| [az prototype config show](#az-prototype-config-show) | Display current project configuration. | Preview |
| [az prototype config get](#az-prototype-config-get) | Get a single configuration value. | Preview |
| [az prototype config set](#az-prototype-config-set) | Set a configuration value. | Preview |
| [az prototype generate](#az-prototype-generate) | Generate documentation, spec-kit artifacts, and backlogs. | Preview |
| [az prototype generate backlog](#az-prototype-generate-backlog) | Generate a backlog of user stories or issues from the architecture. | Preview |
| [az prototype generate docs](#az-prototype-generate-docs) | Generate documentation from templates. | Preview |
| [az prototype generate speckit](#az-prototype-generate-speckit) | Generate the spec-kit documentation bundle. | Preview |
| [az prototype knowledge](#az-prototype-knowledge) | Manage knowledge base contributions. | Preview |
| [az prototype knowledge contribute](#az-prototype-knowledge-contribute) | Submit a knowledge base contribution as a GitHub Issue. | Preview |
| [az prototype agent](#az-prototype-agent) | Manage AI agents for prototype generation. | Preview |
| [az prototype agent list](#az-prototype-agent-list) | List all available agents (built-in and custom). | Preview |
| [az prototype agent add](#az-prototype-agent-add) | Add a custom agent to the project. | Preview |
| [az prototype agent override](#az-prototype-agent-override) | Override a built-in agent with a custom definition. | Preview |
| [az prototype agent show](#az-prototype-agent-show) | Show details of a specific agent. | Preview |
| [az prototype agent remove](#az-prototype-agent-remove) | Remove a custom agent or override. | Preview |
| [az prototype agent update](#az-prototype-agent-update) | Update an existing custom agent's properties. | Preview |
| [az prototype agent test](#az-prototype-agent-test) | Send a test prompt to any agent. | Preview |
| [az prototype agent export](#az-prototype-agent-export) | Export an agent as a YAML file. | Preview |

---

## az prototype init

Initialize a new prototype project.

Sets up project scaffolding, creates the project configuration file, and optionally authenticates with GitHub (validates Copilot license). GitHub authentication is only required for the `copilot` and `github-models` AI providers. When using `azure-openai`, GitHub auth is skipped entirely.

If the target directory already contains a `prototype.yaml`, the command will prompt before overwriting.

```
az prototype init --name
                  --location
                  [--iac-tool {bicep, terraform}]
                  [--ai-provider {azure-openai, copilot, github-models}]
                  [--environment {dev, staging, prod}]
                  [--model]
                  [--output-dir]
                  [--template]
                  [--json]
```

### Examples

Create a new prototype project.

```
az prototype init --name my-prototype --location eastus
```

Initialize with Bicep preference.

```
az prototype init --name my-app --location westus2 --iac-tool bicep
```

Use Azure OpenAI (skips GitHub auth).

```
az prototype init --name my-app --location eastus --ai-provider azure-openai
```

Specify environment and model.

```
az prototype init --name my-app --location eastus --environment staging --model gpt-4o
```

### Required Parameters

`--name`

Name of the prototype project.

`--location`

Azure region for resource deployment (e.g., `eastus`).

### Optional Parameters

`--iac-tool`

Infrastructure-as-code tool preference.

| | |
|---|---|
| Default value: | `terraform` |
| Accepted values: | `bicep`, `terraform` |

`--ai-provider`

AI provider for agent interactions. When set to `azure-openai`, GitHub authentication is skipped.

| | |
|---|---|
| Default value: | `copilot` |
| Accepted values: | `azure-openai`, `copilot`, `github-models` |

`--environment`

Target environment for the prototype.

| | |
|---|---|
| Default value: | `dev` |
| Accepted values: | `dev`, `staging`, `prod` |

`--model`

AI model to use. If not specified, defaults to `claude-sonnet-4.5` for the copilot provider and `gpt-4o` for others.

`--output-dir`

Output directory for project files.

| | |
|---|---|
| Default value: | `.` |

`--template`

Project template to use. Templates provide a pre-configured service topology
that adheres to built-in governance policies.

| | |
|---|---|
| Accepted values: | `web-app`, `data-pipeline`, `ai-app`, `microservices`, `serverless-api` |

The following templates are available:

| Template | Description | Key Services |
|---|---|---|
| `web-app` | Containerised web app with SQL backend and APIM gateway | Container Apps, SQL, Key Vault, APIM |
| `data-pipeline` | Event-driven data pipeline with serverless Cosmos DB | Functions, Cosmos DB, Storage, Event Grid |
| `ai-app` | AI-powered app with Azure OpenAI and conversation history | Container Apps, OpenAI, Cosmos DB, APIM |
| `microservices` | Multi-service architecture with async messaging | Container Apps (x3), Service Bus, APIM |
| `serverless-api` | Serverless REST API with auto-pause SQL | Functions, SQL, Key Vault, APIM |

---

## az prototype design

Analyze requirements and generate architecture design.

Reads artifacts (documents, diagrams, specs), engages the biz-analyst agent to identify gaps, and generates architecture documentation.

When run without parameters, starts an interactive dialogue to capture requirements through guided questions.

The biz-analyst agent is always engaged — even when `--context` is provided — to check for missing requirements and unstated assumptions.

This stage is re-entrant — run it again to refine the design.

```
az prototype design [--artifacts]
                    [--context]
                    [--interactive]
                    [--reset]
                    [--skip-discovery]
                    [--status]
                    [--json]
```

### Examples

Interactive design session (guided dialogue).

```
az prototype design
```

Design from artifact directory.

```
az prototype design --artifacts ./requirements/
```

Add context to existing design.

```
az prototype design --context "Add Redis caching layer"
```

Reset and start design fresh.

```
az prototype design --reset
```

Skip discovery and generate architecture from existing discovery state.

```
az prototype design --skip-discovery
```

Show current discovery status without starting a session.

```
az prototype design --status
```

### Optional Parameters

`--artifacts`

Path to directory containing requirement documents, diagrams, or other artifacts.

`--context`

Additional context or requirements as free text.

`--interactive` `-i`

Enter an interactive refinement loop after architecture generation.

| | |
|---|---|
| Default value: | `False` |

`--reset`

Reset design state and start fresh.

| | |
|---|---|
| Default value: | `False` |

`--skip-discovery`

Skip the discovery conversation and generate architecture directly from existing discovery state. Requires a previous discovery session to have been completed. Use this to resume architecture generation without re-answering discovery questions.

| | |
|---|---|
| Default value: | `False` |

`--status` `-s`

Show current discovery status (open items, confirmed items) without starting a session. Useful for checking progress before resuming.

| | |
|---|---|
| Default value: | `False` |

---

## az prototype build

Generate infrastructure and application code in staged output.

Uses the architecture design to generate Terraform/Bicep modules, application code, database scripts, and documentation.

**Interactive by default** — the build session uses Claude Code-inspired bordered prompts, progress indicators, policy enforcement, and a conversational review loop. All output is organized into fine-grained, dependency-ordered deployment stages. Each infrastructure component, database system, and application gets its own stage.

Workload templates are used as optional starting points when they match the design. After generation, a build report shows what was built and you can provide feedback to regenerate specific stages. Type `done` to accept the build.

**Slash commands during build:**
- `/status` — Show stage completion summary
- `/stages` — Show full deployment plan
- `/files` — List all generated files
- `/policy` — Show policy check summary
- `/help` — Show available commands

```
az prototype build [--scope {all, apps, db, docs, infra}]
                   [--dry-run]
                   [--status]
                   [--reset]
                   [--auto-accept]
                   [--json]
```

### Examples

Interactive build session (default).

```
az prototype build
```

Show current build progress.

```
az prototype build --status
```

Clear build state and start fresh.

```
az prototype build --reset
```

Build only infrastructure code.

```
az prototype build --scope infra
```

Preview what would be generated.

```
az prototype build --scope all --dry-run
```

Build and auto-accept all policy/standards recommendations.

```
az prototype build --auto-accept
```

### Optional Parameters

`--scope`

What to build.

| | |
|---|---|
| Default value: | `all` |
| Accepted values: | `all`, `apps`, `db`, `docs`, `infra` |

`--dry-run`

Preview what would be generated without writing files.

| | |
|---|---|
| Default value: | `False` |

`--status` / `-s`

Show current build progress without starting a session.

| | |
|---|---|
| Default value: | `False` |

`--reset`

Clear existing build state and start fresh.

| | |
|---|---|
| Default value: | `False` |

`--auto-accept`

Automatically accept the default (compliant) recommendation for every policy violation or standards conflict without prompting. Useful for CI/CD pipelines or non-interactive builds where governance defaults are trusted.

| | |
|---|---|
| Default value: | `False` |

---

## az prototype deploy

Deploy prototype to Azure with an interactive deployment session.

Launches an interactive session that deploys infrastructure and applications to Azure in the staged order defined by `az prototype build`. Runs preflight checks (subscription, tenant, IaC tool, resource group, resource providers), then deploys each stage sequentially with real-time output. Supports rollback, per-stage what-if previews, and QA-first error routing.

**AI provider is optional** — the deploy stage is 100% subprocess-based (terraform/bicep/az CLI). Users without an AI provider configured (e.g., no GitHub Copilot license) can still deploy. QA error diagnosis degrades gracefully when no AI is available.

Use `--dry-run` for what-if/plan preview without deploying. Use `--stage N` to deploy a single stage non-interactively. Use `--status` to view current deployment state. Use `--service-principal` for cross-tenant CI/CD deployments.

```
az prototype deploy [--stage]
                    [--force]
                    [--dry-run]
                    [--status]
                    [--reset]
                    [--subscription]
                    [--tenant]
                    [--service-principal]
                    [--client-id]
                    [--client-secret]
                    [--tenant-id]
                    [--json]
```

### Examples

Start interactive deployment session with preflight checks.

```
az prototype deploy
```

Preview what-if/plan for all stages without deploying.

```
az prototype deploy --dry-run
```

Preview what-if/plan for stage 2 only.

```
az prototype deploy --stage 2 --dry-run
```

Deploy only stage 1 (non-interactive).

```
az prototype deploy --stage 1
```

View current deployment status across all stages.

```
az prototype deploy --status
```

Reset deployment state to start over.

```
az prototype deploy --reset
```

Force full redeployment, ignoring change tracking.

```
az prototype deploy --force
```

Deploy to a specific subscription.

```
az prototype deploy --subscription abc-123
```

Deploy to a different tenant.

```
az prototype deploy --tenant 00000000-0000-0000-0000-000000000001 --subscription abc-123
```

Deploy using a service principal (one-off credentials).

```
az prototype deploy --service-principal --client-id abc123 --client-secret mysecret --tenant-id def456
```

Deploy using a service principal with pre-configured credentials.

```
az prototype config set --key deploy.service_principal.client_id --value abc123
az prototype config set --key deploy.service_principal.client_secret --value mysecret
az prototype config set --key deploy.service_principal.tenant_id --value def456
az prototype deploy --service-principal
```

### Interactive Session

The default mode launches an interactive session with 7 phases:

1. **Load build state** — imports deployment stages from build output
2. **Plan overview** — displays stage status, confirms proceeding
3. **Preflight** — checks subscription, IaC tool, resource group, resource providers
4. **Stage-by-stage deploy** — executes each pending stage with real-time output
5. **Output capture** — captures Terraform/Bicep outputs after infra stages
6. **Deploy report** — summarizes deployment results
7. **Interactive loop** — slash commands for status, rollback, redeploy, etc.

### Slash Commands

During the interactive session, the following commands are available:

| Command | Description |
|---------|-------------|
| `/status` | Show deployment status for all stages |
| `/stages` | Alias for `/status` |
| `/deploy [N\|all]` | Deploy a specific stage or all pending stages |
| `/rollback [N\|all]` | Roll back a deployed stage (reverse order enforced) |
| `/redeploy N` | Roll back and redeploy a specific stage |
| `/plan N` | Show what-if/terraform plan for a stage |
| `/outputs` | Display captured deployment outputs |
| `/preflight` | Re-run preflight checks |
| `/login` | Run `az login` interactively |
| `/help` | Show available commands |

Type `q`, `quit`, or `exit` to end the session. Type `done` or `finish` to finalize.

### Rollback

Rollback enforces reverse deployment order — you cannot roll back stage N while a higher-numbered stage (N+1, N+2, ...) is still deployed. Use `/rollback all` to roll back all deployed stages in the correct order.

### Preflight Checks

Before deploying, the session validates:
- Azure subscription is set and accessible
- Azure tenant matches the target (when `--tenant` is specified)
- IaC tool (Terraform or Bicep) is installed
- Target resource group exists (offers fix command if missing)
- Required Azure resource providers are registered

### Optional Parameters

`--stage`

Deploy or preview a specific stage number. Without `--dry-run`, deploys the stage non-interactively. With `--dry-run`, shows what-if/plan for that stage only.

| | |
|---|---|
| Type: | `int` |

`--force`

Force full deployment, ignoring change tracking.

| | |
|---|---|
| Default value: | `False` |

`--dry-run`

Show what-if/terraform plan preview without executing any deployments.

| | |
|---|---|
| Default value: | `False` |

`--status` `-s`

Display current deployment state across all stages and exit.

| | |
|---|---|
| Default value: | `False` |

`--reset`

Clear all deployment state and start fresh.

| | |
|---|---|
| Default value: | `False` |

`--subscription`

Azure subscription ID to deploy to.

`--tenant`

Azure AD tenant ID for cross-tenant deployment. When specified, the session sets the deployment context to this tenant and warns during preflight if the active tenant differs.

`--service-principal`

Authenticate using a service principal before deploying. Requires `--client-id`, `--client-secret`, and `--tenant-id` (via CLI flags or pre-configured values). Service principal login runs before guard checks so that the `az_logged_in` guard passes after authentication. Having credentials configured in `prototype.secrets.yaml` does **not** auto-activate SP login — this flag is an explicit opt-in.

| | |
|---|---|
| Default value: | `False` |

`--client-id`

Service principal application/client ID. Can also be set via `az prototype config set --key deploy.service_principal.client_id --value <id>`.

`--client-secret`

Service principal client secret. Can also be set via `az prototype config set --key deploy.service_principal.client_secret --value <secret>`. Stored in `prototype.secrets.yaml`.

`--tenant-id`

Tenant ID for service principal authentication. Can also be set via `az prototype config set --key deploy.service_principal.tenant_id --value <tenant>`. Stored in `prototype.secrets.yaml`.

### Subcommands

| Command | Description |
|---|---|
| [az prototype deploy outputs](#az-prototype-deploy-outputs) | Show captured deployment outputs. |
| [az prototype deploy rollback-info](#az-prototype-deploy-rollback-info) | Show rollback instructions based on deployment history. |
| [az prototype deploy generate-scripts](#az-prototype-deploy-generate-scripts) | Generate deploy scripts for application directories. |

---

## az prototype deploy outputs

Show captured deployment outputs.

After infrastructure is deployed (Terraform / Bicep), the outputs are captured so that app deploy scripts can reference them. Displays all captured outputs from the most recent deployment.

```
az prototype deploy outputs [--json]
```

### Examples

Show deployment outputs.

```
az prototype deploy outputs
```

---

## az prototype deploy rollback-info

Show rollback instructions based on deployment history.

Displays the last deployment snapshot and generated rollback instructions. Use this to understand what would happen if you roll back.

```
az prototype deploy rollback-info [--json]
```

### Examples

View rollback instructions.

```
az prototype deploy rollback-info
```

---

## az prototype deploy generate-scripts

Generate deploy scripts for application directories.

Scans `./concept/apps/` for sub-directories and generates a `deploy.sh` in each one, tailored to the chosen deployment target (webapp, container app, or function).

```
az prototype deploy generate-scripts [--scope {apps}]
                                      [--deploy-type {container_app, function, webapp}]
                                      [--resource-group]
                                      [--registry]
                                      [--json]
```

### Examples

Generate webapp deploy scripts (default).

```
az prototype deploy generate-scripts
```

Generate container app deploy scripts with registry.

```
az prototype deploy generate-scripts --deploy-type container_app --registry myregistry.azurecr.io
```

Generate function deploy scripts for a specific resource group.

```
az prototype deploy generate-scripts --deploy-type function --resource-group my-rg
```

### Optional Parameters

`--scope`

Scope for script generation.

| | |
|---|---|
| Default value: | `apps` |
| Accepted values: | `apps` |

`--deploy-type`

Azure deployment target type.

| | |
|---|---|
| Default value: | `webapp` |
| Accepted values: | `container_app`, `function`, `webapp` |

`--resource-group`

Default resource group name for generated scripts.

`--registry`

Container registry URL (for `container_app` type).

---

## az prototype status

Show current project status across all stages.

Displays a layered summary of the prototype project including configuration, stage progress (design, build, deploy), and pending file changes. By default shows a human-readable Rich console summary. Use `--json` for machine-readable output suitable for scripting. Use `--verbose` for expanded per-stage details.

The command reads state from all three stage files (`discovery.yaml`, `build.yaml`, `deploy.yaml`) to show real progress — not just boolean completion flags.

```
az prototype status [--verbose]
                    [--json]
```

### Examples

Show project status.

```
az prototype status
```

Show detailed status with per-stage breakdown.

```
az prototype status --verbose
```

Get machine-readable JSON output.

```
az prototype status --json
```

### Default Output

```
Project: my-prototype (eastus, dev)
IaC: terraform | AI: copilot | Naming: microsoft-caf

  Design   [v] Complete (8 exchanges, 12 confirmed, 0 open)
  Build    [v] Complete (5/5 stages accepted, 23 files, 1 policy override)
  Deploy   [~] In Progress (3/5 deployed, 1 failed, 0 rolled back)

  3 file(s) changed since last deployment
```

### Optional Parameters

`--verbose` `-v`

Show expanded per-stage details: discovery open/confirmed items, build stage breakdown, deploy stage status, and deployment history.

| | |
|---|---|
| Default value: | `False` |

---

## az prototype analyze

Analyze errors, costs, and diagnostics for the prototype.

Provides analysis capabilities powered by specialized AI agents. Use `error` to diagnose and fix issues, or `costs` to estimate Azure spending at different scale tiers.

### Commands

| Command | Description |
|---|---|
| [az prototype analyze error](#az-prototype-analyze-error) | Analyze an error and get a fix with redeployment instructions. |
| [az prototype analyze costs](#az-prototype-analyze-costs) | Estimate Azure costs at Small/Medium/Large t-shirt sizes. |

---

## az prototype analyze error

Analyze an error and get a fix with redeployment instructions.

Accepts an inline error string, log file path, or screenshot image. The QA engineer agent identifies the root cause, proposes a fix, and tells you which commands to run to redeploy.

When a screenshot (`.png`, `.jpg`, `.gif`) is provided, the agent uses vision/multi-modal AI to read the image content.

```
az prototype analyze error [--input]
                            [--json]
```

### Examples

Analyze an inline error message.

```
az prototype analyze error --input "ResourceNotFound: The Resource ... was not found"
```

Analyze a log file.

```
az prototype analyze error --input ./deploy.log
```

Analyze a screenshot.

```
az prototype analyze error --input ./error-screenshot.png
```

### Optional Parameters

`--input`

Error input to analyze. Can be an inline error string, path to a log file, or path to a screenshot image.

---

## az prototype analyze costs

Estimate Azure costs at Small/Medium/Large t-shirt sizes.

Analyzes the current architecture design, queries Azure Retail Prices API for each component, and produces a cost report with estimates at three consumption tiers.

Results are cached in `.prototype/state/cost_analysis.yaml`. Re-running the command returns the cached result unless the design context has changed. Use `--refresh` to force a fresh analysis.

```
az prototype analyze costs [--output-format {json, markdown, table}]
                            [--refresh]
                            [--json]
```

### Examples

Generate cost estimate.

```
az prototype analyze costs
```

Get costs in JSON format.

```
az prototype analyze costs --output-format json
```

Force fresh analysis (bypass cache).

```
az prototype analyze costs --refresh
```

### Optional Parameters

`--output-format`

Output format for the cost report.

| | |
|---|---|
| Default value: | `markdown` |
| Accepted values: | `json`, `markdown`, `table` |

`--refresh`

Force fresh analysis, bypassing cached results.

| | |
|---|---|
| Default value: | `False` |

---

## az prototype config

Manage prototype project configuration.

### Commands

| Command | Description |
|---|---|
| [az prototype config init](#az-prototype-config-init) | Interactive setup to create a prototype.yaml configuration file. |
| [az prototype config show](#az-prototype-config-show) | Display current project configuration. |
| [az prototype config get](#az-prototype-config-get) | Get a single configuration value. |
| [az prototype config set](#az-prototype-config-set) | Set a configuration value. |

---

## az prototype config init

Interactive setup to create a prototype.yaml configuration file.

Walks through standard project questions and generates a `prototype.yaml` file. The interactive wizard collects:

- **Project basics** — name, Azure region, environment, IaC tool
- **Naming strategy** — how Azure resources are named (see table below)
- **Landing zone** — zone ID for ALZ strategy (e.g., `zd` for Development)
- **AI provider** — GitHub Models or Azure OpenAI configuration
- **Deployment targets** — subscription and resource group (optional)

#### Naming Strategies

| Strategy | Pattern | Example (resource group, service=api) |
|---|---|---|
| `microsoft-alz` **(default)** | `{zoneid}-{type}-{service}-{env}-{region_short}` | `zd-rg-api-dev-eus` |
| `microsoft-caf` | `{type}-{org}-{service}-{env}-{region_short}-{instance}` | `rg-contoso-api-dev-eus-001` |
| `simple` | `{org}-{service}-{type}-{env}` | `contoso-api-rg-dev` |
| `enterprise` | `{type}-{bu}-{org}-{service}-{env}-{region_short}-{instance}` | `rg-it-contoso-api-dev-eus-001` |
| `custom` | User-defined pattern | Depends on pattern |

#### Landing Zone IDs (microsoft-alz)

| Zone ID | Description |
|---|---|
| `pc` | Connectivity Platform (networking, DNS, firewall) |
| `pi` | Identity Platform (Entra ID, RBAC) |
| `pm` | Management Platform (Log Analytics, App Insights) |
| `zd` | Development Zone **(default)** |
| `zt` | Testing Zone |
| `zs` | Staging Zone |
| `zp` | Production Zone |

The configuration file is optional — all settings can also be provided via command-line parameters.

```
az prototype config init [--json]
```

### Examples

Start interactive configuration.

```
az prototype config init
```

Set naming strategy after init.

```
az prototype config set --key naming.strategy --value microsoft-caf
```

Change the landing zone.

```
az prototype config set --key naming.zone_id --value zp
```

---

## az prototype config show

Display current project configuration. Secret values (API keys, subscription IDs, tokens) stored in `prototype.secrets.yaml` are masked as `***` in the output.

```
az prototype config show [--json]
```

---

## az prototype config get

Get a single configuration value by its dot-separated key path. Secret values are masked as `***`.

```
az prototype config get --key
                        [--json]
```

### Examples

Get the AI provider.

```
az prototype config get --key ai.provider
```

Get the project location.

```
az prototype config get --key project.location
```

Get the naming strategy.

```
az prototype config get --key naming.strategy
```

### Required Parameters

`--key`

Configuration key to retrieve (dot-separated path, e.g., `ai.provider`).

---

## az prototype config set

Set a configuration value.

```
az prototype config set --key
                        --value
                        [--json]
```

### Examples

Switch AI provider.

```
az prototype config set --key ai.provider --value azure-openai
```

Change deployment location.

```
az prototype config set --key project.location --value westus2
```

Switch naming strategy.

```
az prototype config set --key naming.strategy --value microsoft-caf
```

Change landing zone to production.

```
az prototype config set --key naming.zone_id --value zp
```

### Required Parameters

`--key`

Configuration key (dot-separated path, e.g., `ai.provider`).

`--value`

Configuration value to set.

---

## az prototype generate

Generate documentation, spec-kit artifacts, and backlogs.

Commands for generating project documentation, specification-kit bundles, and structured backlogs from built-in templates and AI agents. Templates are populated with project configuration values and written to the output directory. Remaining `[PLACEHOLDER]` values are left for AI agents to fill during the build stage.

**Document types generated:**

| Template | Description |
|---|---|
| `ARCHITECTURE.md` | High-level and detailed architecture diagrams |
| `DEPLOYMENT.md` | Step-by-step deployment guide |
| `DEVELOPMENT.md` | Developer setup and local dev guide |
| `CONFIGURATION.md` | Azure service configuration reference |
| `AS_BUILT.md` | As-built record of delivered solution |
| `COST_ESTIMATE.md` | Azure cost estimates at t-shirt sizes |
| `BACKLOG.md` | Backlog of user stories / issues with tasks and acceptance criteria |

### Commands

| Command | Description |
|---|---|
| [az prototype generate backlog](#az-prototype-generate-backlog) | Generate a backlog of user stories or issues from the architecture. |
| [az prototype generate docs](#az-prototype-generate-docs) | Generate documentation from templates. |
| [az prototype generate speckit](#az-prototype-generate-speckit) | Generate the spec-kit documentation bundle. |

---

## az prototype generate docs

Generate documentation from templates.

Reads each documentation template, applies project configuration values (project name, location, date), and writes the resulting markdown files to the output directory.

```
az prototype generate docs [--path]
                            [--json]
```

### Examples

Generate documentation to default directory.

```
az prototype generate docs
```

Generate documentation to a custom path.

```
az prototype generate docs --path ./deliverables/docs
```

### Optional Parameters

`--path`

Output directory for generated documents.

| | |
|---|---|
| Default value: | `./docs/` |

---

## az prototype generate speckit

Generate the spec-kit documentation bundle.

Creates a self-contained package of documentation templates that define the project's deliverables. The spec-kit is typically stored under the concept directory and serves as the starting point for all project documentation. Includes a `manifest.json` with metadata about the generated bundle.

```
az prototype generate speckit [--path]
                               [--json]
```

### Examples

Generate spec-kit to default directory.

```
az prototype generate speckit
```

Generate spec-kit to a custom path.

```
az prototype generate speckit --path ./my-speckit
```

### Optional Parameters

`--path`

Output directory for the spec-kit bundle.

| | |
|---|---|
| Default value: | `./concept/.specify/` |

---

## az prototype generate backlog

Generate a backlog and push work items to GitHub or Azure DevOps.

**Interactive by default** — generates a structured backlog from the architecture design and enters a conversational session where you can review, refine, add, update, and remove items before pushing them to your provider.

**GitHub mode** creates issues with checkbox task lists (`- [ ]`) in the description body, grouped by epic with effort labels. Issues are created via the `gh` CLI.

**Azure DevOps mode** creates Features with User Stories and Tasks as child work items, including area paths and effort estimates. Work items are created via `az boards`.

**Scope-aware**: in-scope items become stories, out-of-scope items are excluded, and deferred items get a separate "Deferred / Future Work" epic (scope from `az prototype design`).

Each story/issue includes:
- A descriptive title
- Description (2-4 sentences)
- Acceptance criteria (numbered, testable)
- Actionable tasks
- Effort estimate (S/M/L/XL)

Backlog state is persisted in `.prototype/state/backlog.yaml` for re-entrant sessions. Provider, org, and project can be set persistently in `prototype.yaml` under the `backlog` section.

```
az prototype generate backlog [--provider {devops, github}]
                              [--org]
                              [--project]
                              [--output-format {json, markdown, table}]
                              [--quick]
                              [--refresh]
                              [--status]
                              [--push]
                              [--json]
```

### Examples

Interactive backlog session (default).

```
az prototype generate backlog --provider github
```

Quick mode — generate, confirm, and push.

```
az prototype generate backlog --provider github --quick
```

Show current backlog status.

```
az prototype generate backlog --status
```

Force fresh generation (bypass cache).

```
az prototype generate backlog --refresh
```

Generate Azure DevOps work items.

```
az prototype generate backlog --provider devops --org myorg --project myproject
```

Use defaults from prototype.yaml.

```
az prototype generate backlog
```

### Interactive Session

The default mode launches an interactive session with these phases:

1. **Load context** — loads design context, scope, and existing backlog state
2. **Generate** — AI generates structured backlog items from architecture
3. **Review/Refine loop** — conversational back-and-forth for modifications
4. **Push** — creates work items in GitHub or Azure DevOps
5. **Report** — displays links to created work items

### Slash Commands

During the interactive session:

| Command | Description |
|---------|-------------|
| `/list` | Show all items grouped by epic |
| `/show N` | Show item N with full details |
| `/add` | Add a new item (AI-assisted) |
| `/remove N` | Remove item N |
| `/preview` | Show what will be pushed (provider-formatted) |
| `/save` | Save to `concept/docs/BACKLOG.md` locally |
| `/push` | Push all pending items to provider |
| `/push N` | Push specific item N |
| `/status` | Show push status per item |
| `/help` | Show available commands |
| `/quit` | Exit session |

Type `done` or `finish` to end the session. Type `q`, `quit`, or `exit` to cancel.

### Optional Parameters

`--provider`

Backlog provider: `github` for GitHub Issues, `devops` for Azure DevOps work items.

| | |
|---|---|
| Accepted values: | `devops`, `github` |

`--org`

Organization or owner name (GitHub org/user or Azure DevOps org).

`--project`

Project name (Azure DevOps project or GitHub repo).

`--output-format`

Output format for the backlog.

| | |
|---|---|
| Default value: | `markdown` |
| Accepted values: | `json`, `markdown`, `table` |

`--quick`

Skip interactive session — generate, confirm, and push.

| | |
|---|---|
| Default value: | `False` |

`--refresh`

Force fresh AI generation, bypassing cached items.

| | |
|---|---|
| Default value: | `False` |

`--status` `-s`

Show current backlog state without starting a session.

| | |
|---|---|
| Default value: | `False` |

`--push`

In quick mode, auto-push after generation (without confirmation prompt).

| | |
|---|---|
| Default value: | `False` |

---

## az prototype knowledge

Manage knowledge base contributions.

Submit knowledge contributions as GitHub Issues when patterns or pitfalls are discovered during QA diagnosis or manual testing. Contributions are reviewed and merged into the shared knowledge base so future sessions benefit from community findings.

### Commands

| Command | Description |
|---|---|
| [az prototype knowledge contribute](#az-prototype-knowledge-contribute) | Submit a knowledge base contribution as a GitHub Issue. |

---

## az prototype knowledge contribute

Submit a knowledge base contribution as a GitHub Issue.

Creates a structured GitHub Issue in the knowledge repository when a pattern, pitfall, or service gap is discovered. Interactive by default — walks through type, section, context, rationale, and content. Non-interactive when `--service` and `--description` are provided.

Use `--draft` to preview the contribution without submitting (skips gh auth). Use `--file` to load contribution content from a file.

```
az prototype knowledge contribute [--service]
                                   [--description]
                                   [--file]
                                   [--draft]
                                   [--type]
                                   [--section]
                                   [--json]
```

### Examples

Interactive knowledge contribution.

```
az prototype knowledge contribute
```

Quick non-interactive contribution.

```
az prototype knowledge contribute --service cosmos-db --description "RU throughput must be >= 400"
```

Contribute from a file.

```
az prototype knowledge contribute --file ./finding.md
```

Preview without submitting.

```
az prototype knowledge contribute --service redis --description "Cache eviction pitfall" --draft
```

### Optional Parameters

`--service`

Azure service name (e.g., `cosmos-db`, `key-vault`).

`--description`

Brief description of the knowledge contribution.

`--file`

Path to a file containing the contribution content.

`--draft`

Preview the contribution without submitting.

| | |
|---|---|
| Default value: | `False` |

`--type`

Type of knowledge contribution.

| | |
|---|---|
| Default value: | `Pitfall` |
| Accepted values: | `Service pattern update`, `New service`, `Tool pattern`, `Language pattern`, `Pitfall` |

`--section`

Target section within the knowledge file.

---

## az prototype agent

Manage AI agents for prototype generation.

Agents are specialized AI personas that handle different aspects of prototype generation. Built-in agents ship with the extension; you can add custom agents or override built-in ones.

**Built-in agents:**

| Agent | Role | Capabilities |
|---|---|---|
| `cloud-architect` | Architecture design & Azure service selection | Architecture, Cloud Design |
| `terraform` | Terraform IaC generation | IaC, Terraform |
| `bicep` | Bicep IaC generation | IaC, Bicep |
| `app-developer` | Application code generation | App Development |
| `documentation` | Documentation & diagram generation | Documentation |
| `qa-engineer` | Error analysis & screenshot diagnosis | QA, Vision/Image Analysis |
| `biz-analyst` | Requirements gap analysis & NFR validation | Business Analysis |
| `cost-analyst` | Azure cost estimation at t-shirt sizes | Cost Analysis |
| `project-manager` | Backlog generation for GitHub & Azure DevOps | Backlog Generation |

Agent resolution order: **custom** → **override** → **built-in**.

### Commands

| Command | Description |
|---|---|
| [az prototype agent list](#az-prototype-agent-list) | List all available agents (built-in and custom). |
| [az prototype agent add](#az-prototype-agent-add) | Add a custom agent to the project. |
| [az prototype agent override](#az-prototype-agent-override) | Override a built-in agent with a custom definition. |
| [az prototype agent show](#az-prototype-agent-show) | Show details of a specific agent. |
| [az prototype agent remove](#az-prototype-agent-remove) | Remove a custom agent or override. |
| [az prototype agent update](#az-prototype-agent-update) | Update an existing custom agent's properties. |
| [az prototype agent test](#az-prototype-agent-test) | Send a test prompt to any agent. |
| [az prototype agent export](#az-prototype-agent-export) | Export an agent as a YAML file. |

---

## az prototype agent list

List all available agents (built-in and custom).

Displays agents grouped by source (built-in, custom, override) with name, description, and capabilities. Use `--json` for machine-readable output. Use `--verbose` for expanded capability details.

```
az prototype agent list [--show-builtin]
                        [--verbose]
                        [--json]
```

### Examples

List all agents with formatted output.

```
az prototype agent list
```

Get machine-readable JSON output.

```
az prototype agent list --json
```

Show expanded details.

```
az prototype agent list --verbose
```

### Optional Parameters

`--show-builtin`

Include built-in agents in the listing.

| | |
|---|---|
| Default value: | `True` |

`--verbose` `-v`

Show expanded capability details for each agent.

| | |
|---|---|
| Default value: | `False` |

---

## az prototype agent add

Add a custom agent to the project.

Creates a new custom agent definition in `.prototype/agents/` and registers it in the project configuration manifest.

**Interactive by default** — when neither `--file` nor `--definition` is provided, walks you through description, capabilities, constraints, system prompt, and optional few-shot examples. Non-interactive modes: `--definition` copies a built-in agent's YAML, `--file` uses your own definition.

```
az prototype agent add --name
                       [--file]
                       [--definition]
                       [--json]
```

### Examples

Interactive agent creation (default).

```
az prototype agent add --name my-data-agent
```

Start from the cloud_architect built-in definition.

```
az prototype agent add --name my-architect --definition cloud_architect
```

Add agent from a user-supplied file.

```
az prototype agent add --name security --file ./security-checker.yaml
```

### Required Parameters

`--name`

Unique name for the custom agent (used as filename and registry key).

### Optional Parameters

`--file`

Path to a YAML or Python agent definition file. Mutually exclusive with `--definition`.

`--definition`

Name of a built-in definition to copy as a starting point (e.g., `cloud_architect`,
`bicep_agent`, `terraform_agent`). Mutually exclusive with `--file`.

---

## az prototype agent override

Override a built-in agent with a custom definition.

Replaces the behavior of a built-in agent with a custom implementation. The override is recorded in `prototype.yaml` and takes effect on the next command run. The override file is validated: must exist on disk, parse as valid YAML, and contain a `name` field. A warning is shown if the target name does not match a known built-in agent.

```
az prototype agent override --name
                            --file
                            [--json]
```

### Examples

Override cloud-architect with custom definition.

```
az prototype agent override --name cloud-architect --file ./my-architect.yaml
```

### Required Parameters

`--name`

Name of the built-in agent to override.

`--file`

Path to YAML or Python agent definition file.

---

## az prototype agent show

Show details of a specific agent.

Displays agent metadata including description, source, capabilities, constraints, and a preview of the system prompt. Use `--verbose` to show the full system prompt. Use `--json` for machine-readable output.

```
az prototype agent show --name
                        [--verbose]
                        [--json]
```

### Examples

Show agent details.

```
az prototype agent show --name cloud-architect
```

Show full system prompt.

```
az prototype agent show --name cloud-architect --verbose
```

Get JSON output.

```
az prototype agent show --name cloud-architect --json
```

### Required Parameters

`--name`

Name of the agent to show details for.

### Optional Parameters

`--verbose` `-v`

Show full system prompt instead of 200-char preview.

| | |
|---|---|
| Default value: | `False` |

---

## az prototype agent remove

Remove a custom agent or override.

Removes the agent definition from the project's `.prototype/agents/` directory and cleans up the project configuration manifest entry. Can also remove overrides, restoring the built-in agent behavior. Built-in agents cannot be removed.

```
az prototype agent remove --name
                          [--json]
```

### Examples

Remove a custom agent.

```
az prototype agent remove --name my-data-agent
```

Remove an override (restores built-in).

```
az prototype agent remove --name cloud-architect
```

### Required Parameters

`--name`

Name of the custom agent to remove.

---

## az prototype agent update

Update an existing custom agent's properties.

**Interactive by default** — walks through the same prompts as `agent add` with current values as defaults. Press Enter to keep existing values. Providing any field flag (`--description`, `--capabilities`, `--system-prompt-file`) switches to non-interactive mode and only changes the specified fields. Only custom YAML agents can be updated.

```
az prototype agent update --name
                          [--description]
                          [--capabilities]
                          [--system-prompt-file]
                          [--json]
```

### Examples

Interactive update with current values as defaults.

```
az prototype agent update --name my-agent
```

Update only the description.

```
az prototype agent update --name my-agent --description "New description"
```

Update capabilities.

```
az prototype agent update --name my-agent --capabilities "architect,deploy"
```

Update system prompt from file.

```
az prototype agent update --name my-agent --system-prompt-file ./new-prompt.txt
```

### Required Parameters

`--name`

Name of the custom agent to update.

### Optional Parameters

`--description`

New description for the agent.

`--capabilities`

Comma-separated list of capabilities (e.g., `architect,deploy`).

`--system-prompt-file`

Path to a text file containing the new system prompt.

---

## az prototype agent test

Send a test prompt to any agent and display the response.

Sends a prompt to the specified agent using the configured AI provider and displays the response with model and token count. Useful for validating agent behavior after creation or update. Requires a configured AI provider.

```
az prototype agent test --name
                        [--prompt]
                        [--json]
```

### Examples

Test with default prompt.

```
az prototype agent test --name cloud-architect
```

Test with custom prompt.

```
az prototype agent test --name my-agent --prompt "Design a web app with Redis caching"
```

### Required Parameters

`--name`

Name of the agent to test.

### Optional Parameters

`--prompt`

Test prompt to send to the agent. Defaults to "Briefly introduce yourself and describe your capabilities."

---

## az prototype agent export

Export any agent (including built-in) as a YAML file.

Exports the agent's metadata, system prompt, capabilities, constraints, and examples as a portable YAML file. The exported file can be shared with other projects or loaded via `agent add --file`.

```
az prototype agent export --name
                          [--output]
                          [--json]
```

### Examples

Export a built-in agent.

```
az prototype agent export --name cloud-architect
```

Export to a specific path.

```
az prototype agent export --name qa-engineer --output ./agents/qa.yaml
```

### Required Parameters

`--name`

Name of the agent to export.

### Optional Parameters

`--output`

Output file path for the exported YAML. Defaults to `./<name>.yaml`.

---

## Global Parameters

The following global parameters are available for all `az prototype` commands:

`--json` `-j`

Output machine-readable JSON instead of formatted display. Available on all `az prototype` commands.

| | |
|---|---|
| Default value: | `False` |

`--debug`

Increase logging verbosity to show all debug logs.

`--help -h`

Show the help message and exit.

`--only-show-errors`

Only show errors, suppressing warnings.

`--output -o`

Output format.

| | |
|---|---|
| Default value: | `json` |
| Accepted values: | `json`, `jsonc`, `none`, `table`, `tsv`, `yaml`, `yamlc` |

`--query`

JMESPath query string. See [http://jmespath.org/](http://jmespath.org/) for more information and examples.

`--verbose`

Increase logging verbosity. Use `--debug` for full debug logs.
