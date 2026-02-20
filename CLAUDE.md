# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`az prototype` is an Azure CLI extension that enables users to rapidly build Azure POCs using AI-driven agent teams. It implements a simplified version of the Innovation Factory methodology (see `~/projects/microsoft/poc/starter` for the full Starter Kit).

**Key difference**: The Starter Kit uses Claude Code directly with 71+ agents and 12 detailed stages. This CLI condenses that into 4 stages with 9 agents, making the methodology accessible via Azure CLI commands.

### Stage Mapping to Innovation Factory

| CLI Stage | IF Stages | Purpose |
|-----------|-----------|---------|
| `init` | — | Project folder initialization, config scaffolding |
| `design` | 1-6 | Discovery conversation, requirements analysis, architecture design, deployment planning |
| `build` | 7 | Generate IaC (Bicep/Terraform) + application code |
| `deploy` | 8-10 | Infrastructure deployment, app deployment, customer testing |
| `design` (re-run) | 10-11 | Refinements based on feedback, architecture improvements |

Each stage is re-entrant—users can re-run stages to iterate on their prototype.

## Build & Development Commands

```bash
# Build and install the extension
./build.sh

# Run tests
pip install -e . && pip install pytest pytest-cov
pytest tests/ -v --tb=short --cov=azext_prototype --cov-report=term-missing

# Run a single test
pytest tests/test_foo.py::test_function_name -v

# Linting
pip install flake8 black isort
flake8 azext_prototype/ --max-line-length=120 --extend-ignore=E203,W503
black --check --line-length 120 azext_prototype/
isort --check-only --profile black azext_prototype/

# Validate policies and templates
python -m azext_prototype.governance.policies.validate --dir azext_prototype/governance/policies/ --strict
python -m azext_prototype.templates.validate --dir azext_prototype/templates/workloads/ --strict

# Pre-commit hooks
pip install pre-commit && pre-commit install
pre-commit run --all-files
```

## Architecture

### Module Structure

| Module | Purpose |
|--------|---------|
| `custom.py` | Command implementations (entry points) |
| `commands.py` | Command group definitions |
| `stages/` | Pipeline stages (init, design, build, deploy) with guards and state tracking |
| `agents/` | Multi-agent system with registry resolution: custom → override → builtin |
| `ai/` | AI provider abstraction (copilot, github-models, azure-openai) |
| `config/` | ProjectConfig class for `prototype.yaml` + `prototype.secrets.yaml` |
| `policies/` | Governance policy engine (loads `*.policy.yaml` files) |
| `templates/` | Workload templates (web-app, serverless-api, microservices, ai-app, data-pipeline) |
| `naming/` | Resource naming strategies (microsoft-alz, microsoft-caf, simple, enterprise, custom) |

### Key Classes

- **BaseAgent** (`agents/base.py`): Abstract agent with system prompt, capability matching, governance checking
- **AgentRegistry** (`agents/registry.py`): Resolves agents in order: custom → override → builtin
- **AgentContext** (`agents/base.py`): Runtime context containing config, AI provider, conversation history, artifacts
- **BaseStage** (`stages/base.py`): Abstract stage with guards, state tracking, re-entrancy support
- **ProjectConfig** (`config/__init__.py`): YAML config with automatic secrets separation
- **AIProvider** (`ai/provider.py`): Base class for AI providers; use factory pattern via `ai/factory.py`
- **PolicyEngine** (`policies/__init__.py`): Loads and enforces governance policies

### Built-in Agents (11)

| Agent | Role | IF Equivalent |
|-------|------|---------------|
| `cloud-architect` | Cross-service architecture, central config | cloud-architect |
| `biz-analyst` | Discovery, requirements analysis | business-analyst |
| `project-manager` | Scope management, coordination | project-manager |
| `terraform-agent` | Terraform module generation | *-terraform agents |
| `bicep-agent` | Bicep template generation | *-bicep agents |
| `app-developer` | Application code generation | *-developer agents |
| `qa-engineer` | Issue diagnosis, troubleshooting | qa-engineer |
| `cost-analyst` | Cost estimation (S/M/L tiers) | cost-analyst |
| `doc-agent` | Documentation generation | documentation-manager |
| `security-reviewer` | Pre-deployment IaC security scanning | — (new) |
| `monitoring-agent` | Observability config generation | — (new) |

## Agent Governance Principles

**These principles need to be systematically implemented in the stage orchestration:**

### 1. Mandatory Agent Usage
All work must be delegated to agents. Stages should not perform substantive work directly—they orchestrate agent execution.

### 2. Agent Delegation Priority
When determining which agent handles a task:
1. Service + role → specific service agent (terraform, bicep, developer)
2. **Error/issue/bug reported → `qa-engineer` ALWAYS** (QA owns troubleshooting)
3. Scope/requirements/communication → `project-manager`
4. Multi-service spanning → `cloud-architect`
5. Discovery/requirements analysis → `biz-analyst`
6. Documentation → `doc-agent`
7. Cost estimation → `cost-analyst`
8. Unknown/ambiguous → `project-manager` decides assignment

### 3. QA-First Troubleshooting
All errors, failures, and unexpected behavior must route to `qa-engineer` first. QA owns the full diagnostic lifecycle: evidence gathering, log analysis, root cause identification.

### 4. Escalation Procedures
1. Document blocker and attempted solutions
2. Escalate to `cloud-architect` (technical) or `project-manager` (scope)
3. After extended blocking: expand to web search
4. If still blocked: flag to human
5. **Do NOT proceed with workarounds without human approval**

### 5. Direct Execution
Unlike the Innovation Factory (which generates commands for humans to run), this extension executes deployment commands directly (`terraform apply`, `az deployment group create`, etc.). The `--dry-run` flag provides what-if/plan previews without executing. Preflight checks validate prerequisites before any deployment runs.

## Code Patterns

### Command Implementation

```python
def prototype_init(cmd, name, location, iac_tool=None, ai_provider=None, output_dir=None, template=None):
    project_dir, config, registry, agent_context = _prepare_command()
    stage = InitStage()
    result = stage.execute(agent_context, registry, ...)
    return result
```

### Stage Execution

```python
class MyStage(BaseStage):
    def get_guards(self) -> list[StageGuard]:
        return [StageGuard(...), ...]

    def execute(self, agent_context, registry, **kwargs) -> dict:
        can_run, failures = self.can_run()
        # Delegate to agents via registry.find_best_for_task()
        return results
```

### Agent Implementation

```python
class MyAgent(BaseAgent):
    _keywords = ["keyword1", "keyword2"]
    _keyword_weight = 0.1

    def execute(self, context: AgentContext, task: str) -> AIResponse:
        return super().execute(context, task)

    def can_handle(self, task_description: str) -> float:
        return super().can_handle(task_description)  # Returns 0.0-1.0
```

### Testing

```python
@pytest.fixture(autouse=True)
def _no_telemetry_network():
    with patch("azext_prototype.telemetry._send_envelope"):
        yield

def test_something(tmp_project):
    project_dir = str(tmp_project)
```

## Configuration Files

- **prototype.yaml**: Per-project config (project settings, AI provider, naming strategy, stage state)
- **prototype.secrets.yaml**: Git-ignored secrets (API keys, subscription IDs, tokens)
- Sensitive keys with prefixes `ai.azure_openai.api_key`, `deploy.subscription`, `backlog.token` auto-route to secrets file

## Quick Navigation

| Task | Location |
|------|----------|
| Add a command | `commands.py` + `custom.py` |
| Add a built-in agent | `agents/builtin/*.py` |
| Add a stage | `stages/*.py` (inherit `BaseStage`) |
| Add a policy | `policies/*/` (validate with JSON schema) |
| Add a template | `templates/workloads/*.yaml` |
| Configure AI provider | `ai/*.py` + factory |
| Add naming strategy | `naming/__init__.py` |
| Write tests | `tests/test_*.py` (use `tmp_project` fixture) |
| Update dependencies | `setup.py` DEPENDENCIES list |
