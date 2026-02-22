# az prototype — Azure CLI Extension

Rapid Azure prototype generation powered by AI agent teams.

## Overview

`az prototype` is an Azure CLI extension that empowers customers to rapidly create functional Azure prototypes using AI-driven agent teams. It leverages GitHub Copilot (via GitHub Models API) or Azure OpenAI for intelligent code and infrastructure generation.

## Prerequisites

- Azure CLI 2.50+
- GitHub CLI (`gh`) installed and authenticated
- GitHub Copilot license (Business or Enterprise)
- Azure subscription with appropriate permissions

## Installation

Currently, AZ Prototype is in _private preview_ and is, therefore, not available in the Azure CLI library. To use, you must currently download the wheel to you local machine from our [releases](https://github.com/Azure/az-prototype/releases) and manually install it.

```bash
az extension add --source path/to/az_prototype-<version>-py3-none-any.whl
```

## Quick Start

```bash
# Initialize a new prototype project
az prototype init --name my-prototype --location eastus

# Run design analysis (interactive)
az prototype design

# Provide artifacts for design analysis
az prototype design --artifacts ./requirements/ --context "Build a data pipeline"

# Generate infrastructure and application code
az prototype build

# Deploy to Azure (incremental — only deploys changes)
az prototype deploy

# Deploy only infrastructure changes
az prototype deploy --scope infra

# Deploy only application changes
az prototype deploy --scope apps
```

To receive help for any specific command, run `az prototype --help` or `az prototype <command> --help`.

View the [command reference](./COMMANDS.md) to see the full list of commands and their parameters.

## Agent System

### Built-in Agents
Ships with 8 pre-defined agents:

| Agent | Capability | Description |
|-------|-----------|-------------|
| `cloud-architect` | Architecture | Cross-service coordination and architecture design |
| `terraform` | Terraform | Terraform IaC generation |
| `bicep` | Bicep | Bicep template generation |
| `app-developer` | Development | Application code generation (APIs, Functions, containers) |
| `documentation` | Documentation | Project and deployment documentation |
| `qa-engineer` | QA / Analysis | Error diagnosis from logs, strings, or screenshots; fix coordination |
| `biz-analyst` | Business Analysis | Requirements gap analysis and interactive design dialogue |
| `cost-analyst` | Cost Analysis | Azure cost estimation at S/M/L t-shirt sizes via Retail Prices API |

### Custom Agents
Add your own agents via YAML or Python:

```bash
# List available agents
az prototype agent list

# Add a custom agent from YAML
az prototype agent add --file ./my-agent.yaml

# Override a built-in agent
az prototype agent override --name cloud-architect --file ./my-architect.yaml
```

### YAML Agent Format
```yaml
name: my-custom-agent
description: Custom agent for specific use case
role: architect
system_prompt: |
  You are a specialized architect for ...
constraints:
  - Must use managed identity
  - Must follow naming conventions
tools:
  - terraform
  - bicep
```

## Configuration

Project configuration is stored in `prototype.yaml`:

```yaml
project:
  name: my-prototype
  location: eastus
  environment: dev
  iac_tool: terraform  # or bicep

naming:
  strategy: microsoft-alz  # microsoft-alz | microsoft-caf | simple | enterprise | custom
  org: contoso
  env: dev
  zone_id: zd              # ALZ zone ID (see table below)

ai:
  provider: github-models  # or azure-openai
  model: gpt-4o

agents:
  custom_dir: ./.prototype/agents/
  overrides: {}

deploy:
  track_changes: true
```

### Naming Strategies

All agents use a shared naming resolver to generate consistent Azure resource names.

| Strategy | Pattern | Example |
|----------|---------|--------|
| `microsoft-alz` **(default)** | `{zoneid}-{type}-{service}-{env}-{region}` | `zd-rg-api-dev-eus` |
| `microsoft-caf` | `{type}-{org}-{service}-{env}-{region}-{instance}` | `rg-contoso-api-dev-eus-001` |
| `simple` | `{org}-{service}-{type}-{env}` | `contoso-api-rg-dev` |
| `enterprise` | `{type}-{bu}-{org}-{service}-{env}-{region}-{instance}` | `rg-it-contoso-api-dev-eus-001` |
| `custom` | User-defined | Depends on pattern |

### Landing Zone IDs (ALZ)

When using `microsoft-alz`, resources are assigned to a landing zone:

| Zone | ID | Used for |
|------|----|----------|
| Connectivity Platform | `pc` | Networking, DNS, firewall |
| Identity Platform | `pi` | Entra ID, RBAC |
| Management Platform | `pm` | Log Analytics, App Insights |
| Development Zone | `zd` | Dev workloads **(default)** |
| Testing Zone | `zt` | QA / test workloads |
| Staging Zone | `zs` | UAT / staging workloads |
| Production Zone | `zp` | Production workloads |

## Stages

| Command | Description | Re-entrant |
|---------|-------------|------------|
| `az prototype init` | Project scaffolding, auth, config | No |
| `az prototype design` | Requirements analysis, architecture | Yes |
| `az prototype build` | Generate IaC and app code | Yes |
| `az prototype deploy` | Deploy with change tracking | Yes |

## License

MIT

