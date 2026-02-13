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
| [az prototype status](#az-prototype-status) | Show current project status across all stages. | Preview |
| [az prototype analyze](#az-prototype-analyze) | Analyze errors, costs, and diagnostics for the prototype. | Preview |
| [az prototype analyze error](#az-prototype-analyze-error) | Analyze an error and get a fix with redeployment instructions. | Preview |
| [az prototype analyze costs](#az-prototype-analyze-costs) | Estimate Azure costs at Small/Medium/Large t-shirt sizes. | Preview |
| [az prototype config](#az-prototype-config) | Manage prototype project configuration. | Preview |
| [az prototype config init](#az-prototype-config-init) | Interactive setup to create a prototype.yaml configuration file. | Preview |
| [az prototype config show](#az-prototype-config-show) | Display current project configuration. | Preview |
| [az prototype config set](#az-prototype-config-set) | Set a configuration value. | Preview |
| [az prototype generate](#az-prototype-generate) | Generate documentation, spec-kit artifacts, and backlogs. | Preview |
| [az prototype generate backlog](#az-prototype-generate-backlog) | Generate a backlog of user stories or issues from the architecture. | Preview |
| [az prototype generate docs](#az-prototype-generate-docs) | Generate documentation from templates. | Preview |
| [az prototype generate speckit](#az-prototype-generate-speckit) | Generate the spec-kit documentation bundle. | Preview |
| [az prototype agent](#az-prototype-agent) | Manage AI agents for prototype generation. | Preview |
| [az prototype agent list](#az-prototype-agent-list) | List all available agents (built-in and custom). | Preview |
| [az prototype agent add](#az-prototype-agent-add) | Add a custom agent to the project. | Preview |
| [az prototype agent override](#az-prototype-agent-override) | Override a built-in agent with a custom definition. | Preview |
| [az prototype agent show](#az-prototype-agent-show) | Show details of a specific agent. | Preview |
| [az prototype agent remove](#az-prototype-agent-remove) | Remove a custom agent. | Preview |

---

## az prototype init

Initialize a new prototype project.

Sets up project scaffolding, authenticates with GitHub (validates Copilot license), and creates the project configuration file.

```
az prototype init --name
                  --location
                  [--iac-tool {bicep, terraform}]
                  [--ai-provider {azure-openai, copilot, github-models}]
                  [--output-dir]
                  [--template]
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

Use Azure OpenAI instead of GitHub Models.

```
az prototype init --name my-app --location eastus --ai-provider azure-openai
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

AI provider for agent interactions.

| | |
|---|---|
| Default value: | `github-models` |
| Accepted values: | `azure-openai`, `copilot`, `github-models` |

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
                    [--reset]
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

### Optional Parameters

`--artifacts`

Path to directory containing requirement documents, diagrams, or other artifacts.

`--context`

Additional context or requirements as free text.

`--reset`

Reset design state and start fresh.

| | |
|---|---|
| Default value: | `False` |

---

## az prototype build

Generate infrastructure and application code in staged output.

Uses the architecture design to generate Terraform/Bicep modules, application code, database scripts, and documentation.

All output is organized into deployment stages based on dependency analysis. Use `az prototype deploy --plan-only` to see the stage breakdown before deploying.

```
az prototype build [--scope {all, apps, db, docs, infra}]
                   [--dry-run]
```

### Examples

Build everything (staged).

```
az prototype build
```

Build only infrastructure code.

```
az prototype build --scope infra
```

Preview what would be generated.

```
az prototype build --scope all --dry-run
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

---

## az prototype deploy

Deploy prototype to Azure with staged, incremental deployments.

Deploys infrastructure and applications to Azure. Supports staged deployments — use `--plan-only` to see stages, then `--stage N` to deploy a specific stage. Tracks changes so subsequent deployments only push what has changed.

```
az prototype deploy [--scope {all, apps, infra}]
                    [--stage]
                    [--force]
                    [--plan-only]
                    [--subscription]
                    [--resource-group]
```

### Examples

Deploy everything (all stages, incremental).

```
az prototype deploy
```

View deployment plan with stage breakdown.

```
az prototype deploy --plan-only
```

View plan for apps only.

```
az prototype deploy --scope apps --plan-only
```

Deploy only stage 1 of infrastructure.

```
az prototype deploy --scope infra --stage 1
```

Deploy only stage 2 of apps.

```
az prototype deploy --scope apps --stage 2
```

Force full redeployment.

```
az prototype deploy --force
```

### Optional Parameters

`--scope`

What to deploy.

| | |
|---|---|
| Default value: | `all` |
| Accepted values: | `all`, `apps`, `infra` |

`--stage`

Deploy only a specific stage number. Use `--plan-only` to see available stages before deploying.

| | |
|---|---|
| Type: | `int` |

`--force`

Force full deployment, ignoring change tracking.

| | |
|---|---|
| Default value: | `False` |

`--plan-only`

Show deployment plan with stage breakdown without executing.

| | |
|---|---|
| Default value: | `False` |

`--subscription`

Azure subscription ID to deploy to.

`--resource-group`

Target resource group name.

---

## az prototype status

Show current project status across all stages.

```
az prototype status
```

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

```
az prototype analyze costs [--output-format {json, markdown, table}]
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

### Optional Parameters

`--output-format`

Output format for the cost report.

| | |
|---|---|
| Default value: | `markdown` |
| Accepted values: | `json`, `markdown`, `table` |

---

## az prototype config

Manage prototype project configuration.

### Commands

| Command | Description |
|---|---|
| [az prototype config init](#az-prototype-config-init) | Interactive setup to create a prototype.yaml configuration file. |
| [az prototype config show](#az-prototype-config-show) | Display current project configuration. |
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
az prototype config init
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

Display current project configuration.

```
az prototype config show
```

---

## az prototype config set

Set a configuration value.

```
az prototype config set --key
                        --value
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

Generate a backlog of user stories or issues from the architecture.

Analyzes the current architecture design and produces a structured backlog suitable for GitHub Issues or Azure DevOps work items. The project-manager agent decomposes the architecture into epics, user stories, tasks, and acceptance criteria.

**GitHub mode** creates issues with checkbox task lists (`- [ ]`) in the description body, grouped by epic with effort labels.

**Azure DevOps mode** creates User Story work items with associated Task work items, including area paths and remaining work estimates.

Each story/issue includes:
- A descriptive title
- Description (2-4 sentences)
- Acceptance criteria (numbered, testable)
- Actionable tasks
- Effort estimate (S/M/L/XL)

Backlog provider, org, and project can also be set persistently in `prototype.yaml` under the `backlog` section. Authentication tokens are stored in `prototype.secrets.yaml`.

```
az prototype generate backlog [--provider {devops, github}]
                              [--org]
                              [--project]
                              [--output-format {json, markdown, table}]
```

### Examples

Generate GitHub issues backlog.

```
az prototype generate backlog --provider github
```

Generate Azure DevOps work items.

```
az prototype generate backlog --provider devops --org myorg --project myproject
```

Use defaults from prototype.yaml.

```
az prototype generate backlog
```

### Optional Parameters

`--provider`

Backlog provider: `github` for GitHub Issues, `devops` for Azure DevOps work items.

| | |
|---|---|
| Default value: | `github` |
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
| [az prototype agent remove](#az-prototype-agent-remove) | Remove a custom agent. |

---

## az prototype agent list

List all available agents (built-in and custom).

```
az prototype agent list [--show-builtin]
```

### Optional Parameters

`--show-builtin`

Include built-in agents in the listing.

| | |
|---|---|
| Default value: | `True` |

---

## az prototype agent add

Add a custom agent to the project.

Creates a new custom agent definition in `.prototype/agents/` and registers it
in the project configuration manifest.

When neither `--file` nor `--definition` is provided, the built-in example
template is used as the starting point. Use `--definition` to start from a
specific built-in agent's YAML (e.g., `cloud_architect`, `bicep_agent`).
Use `--file` to bring your own YAML or Python definition.

```
az prototype agent add --name
                       [--file]
                       [--definition]
```

### Examples

Create a new agent from the example template.

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

Replaces the behavior of a built-in agent with a custom implementation. The override is recorded in the project configuration.

```
az prototype agent override --name
                            --file
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

```
az prototype agent show --name
```

### Required Parameters

`--name`

Name of the agent to show details for.

---

## az prototype agent remove

Remove a custom agent.

Removes the agent definition from the project's `.prototype/agents/` directory and cleans up the project configuration manifest entry.

```
az prototype agent remove --name
```

### Required Parameters

`--name`

Name of the custom agent to remove.

---

## Governance Policies

The extension ships with built-in governance policies that are automatically
injected into agent prompts. Policies define rules, patterns, and anti-patterns
that agents MUST or SHOULD follow when generating infrastructure and application code.

### Built-in Policies

| Policy | Category | Services | Rules |
|---|---|---|---|
| `container-apps` | azure | container-apps, container-registry | CA-001 through CA-004 |
| `key-vault` | azure | key-vault | KV-001 through KV-005 |
| `sql-database` | azure | sql-database | SQL-001 through SQL-005 |
| `cosmos-db` | azure | cosmos-db | CDB-001 through CDB-004 |
| `managed-identity` | security | all compute + data services | MI-001 through MI-004 |
| `network-isolation` | security | all PaaS services | NET-001 through NET-004 |
| `apim-to-container-apps` | integration | api-management, container-apps | INT-001 through INT-004 |

### Custom Policies

Add `.policy.yaml` files to `.prototype/policies/` in your project to extend
or override built-in policies. Use the same schema as the built-in files.

### Policy Schema

```yaml
apiVersion: v1
kind: policy
metadata:
  name: my-service
  category: azure | security | integration | cost | data
  services: [service-name-1, service-name-2]
  last_reviewed: "2025-01-01"

rules:
  - id: XX-001
    severity: required | recommended | optional
    description: "What to do"
    rationale: "Why"
    applies_to: [cloud-architect, terraform, bicep, app-developer]

patterns:
  - name: "Pattern name"
    description: "When to use it"
    example: |
      code example here

anti_patterns:
  - description: "What NOT to do"
    instead: "What to do instead"

references:
  - title: "Doc title"
    url: "https://..."
```

### Severity Levels

| Level | Prompt keyword | Meaning |
|---|---|---|
| `required` | **MUST** | Agent must follow; violation is a defect |
| `recommended` | **SHOULD** | Agent should follow unless there is a justified reason |
| `optional` | **MAY** | Best practice; agent may skip if not relevant |

### Validation

Policy files are validated automatically:

- **Pre-commit hook**: Install with `python scripts/install-hooks.py` or `pre-commit install`
- **CI pipeline**: Runs `python -m azext_prototype.policies.validate --strict` on every push
- **Release pipeline**: Validates before building the wheel
- **Manual**: `python -m azext_prototype.policies.validate --dir azext_prototype/policies/`

---

## Global Parameters

The following global parameters are available for all `az prototype` commands:

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
