"""Help text for az prototype commands."""

from knack.help_files import helps

helps["prototype"] = """
type: group
short-summary: Rapidly create Azure prototypes using AI-driven agent teams.
long-summary: |
    The az prototype extension empowers you to build functional Azure prototypes
    using intelligent agent teams powered by GitHub Copilot or Azure OpenAI.

    Workflow: init → design → build → deploy

    Each stage can be run independently (with prerequisite guards) and most
    stages are re-entrant — you can return to refine your design or rebuild
    specific components.

    Analysis commands let you diagnose errors and estimate costs at any point.
"""

helps["prototype init"] = """
type: command
short-summary: Initialize a new prototype project.
long-summary: |
    Sets up project scaffolding, creates the project configuration file, and
    optionally authenticates with GitHub (validates Copilot license).

    GitHub authentication is only required for the copilot and github-models
    AI providers. When using azure-openai, GitHub auth is skipped entirely.

    If the target directory already contains a prototype.yaml, the command
    will prompt before overwriting.
examples:
    - name: Create a new prototype project
      text: az prototype init --name my-prototype --location eastus
    - name: Initialize with Bicep preference
      text: az prototype init --name my-app --location westus2 --iac-tool bicep
    - name: Use Azure OpenAI (skips GitHub auth)
      text: az prototype init --name my-app --location eastus --ai-provider azure-openai
    - name: Specify environment and model
      text: az prototype init --name my-app --location eastus --environment staging --model gpt-4o
"""

helps["prototype design"] = """
type: command
short-summary: Analyze requirements and generate architecture design.
long-summary: |
    Reads artifacts (documents, diagrams, specs), engages the biz-analyst
    agent to identify gaps, and generates architecture documentation.

    When run without parameters, starts an interactive dialogue to
    capture requirements through guided questions.

    With --interactive, enters a refinement loop after architecture
    generation so you can review the design and request changes.

    The biz-analyst agent is always engaged — even when --context is
    provided — to check for missing requirements and unstated assumptions.

    This stage is re-entrant — run it again to refine the design.
examples:
    - name: Interactive design session (guided dialogue)
      text: az prototype design
    - name: Interactive design with architecture refinement loop
      text: az prototype design --interactive
    - name: Design from artifact directory
      text: az prototype design --artifacts ./requirements/
    - name: Add context to existing design
      text: az prototype design --context "Add Redis caching layer"
    - name: Reset and start design fresh
      text: az prototype design --reset
"""

helps["prototype build"] = """
type: command
short-summary: Generate infrastructure and application code in staged output.
long-summary: |
    Uses the architecture design to generate Terraform/Bicep modules,
    application code, database scripts, and documentation.

    Interactive by default — the build session uses Claude Code-inspired
    bordered prompts, progress indicators, policy enforcement, and a
    conversational review loop.

    All output is organized into fine-grained, dependency-ordered
    deployment stages. Each infrastructure component, database system,
    and application gets its own stage. Workload templates are used
    as optional starting points when they match the design.

    After generation, a build report shows what was built and you can
    provide feedback to regenerate specific stages. Type 'done' to
    accept the build.

    Slash commands during build:
      /status  - Show stage completion summary
      /stages  - Show full deployment plan
      /files   - List all generated files
      /policy  - Show policy check summary
      /help    - Show available commands

    Use --dry-run for a non-interactive preview.
examples:
    - name: Interactive build session (default)
      text: az prototype build
    - name: Show current build progress
      text: az prototype build --status
    - name: Clear build state and start fresh
      text: az prototype build --reset
    - name: Build only infrastructure code
      text: az prototype build --scope infra
    - name: Preview what would be generated
      text: az prototype build --scope all --dry-run
"""

helps["prototype deploy"] = """
type: command
short-summary: Deploy prototype to Azure with interactive staged deployments.
long-summary: |
    Interactive by default — runs preflight checks (subscription, IaC tool,
    resource group, resource providers), then deploys stages sequentially
    with progress tracking and QA-first error routing.

    After deployment, enters a conversational loop where you can check
    status, rollback, redeploy, or preview changes using slash commands.

    Slash commands during deploy:
      /status    - Show stage completion summary
      /stages    - Show full stage breakdown (alias for /status)
      /deploy N  - Deploy a specific stage (or 'all' for pending)
      /rollback N - Roll back a specific stage (or 'all' in reverse order)
      /redeploy N - Roll back and redeploy a stage
      /plan N    - What-if / terraform plan for a stage
      /outputs   - Show captured deployment outputs
      /preflight - Re-run preflight checks
      /help      - Show available commands

    Use --dry-run for non-interactive what-if / terraform plan preview.
    Use --stage N for non-interactive single-stage deploy.
    Use --stage N --dry-run for what-if preview of a single stage.
    Use --outputs to show captured deployment outputs.
    Use --rollback-info to show rollback instructions.
    Use --generate-scripts to generate deploy.sh for application directories.
examples:
    - name: Interactive deploy session (default)
      text: az prototype deploy
    - name: Show current deploy progress
      text: az prototype deploy --status
    - name: Preview all stages (what-if / terraform plan)
      text: az prototype deploy --dry-run
    - name: Deploy only stage 2
      text: az prototype deploy --stage 2
    - name: Force full redeployment
      text: az prototype deploy --force
    - name: Show captured deployment outputs
      text: az prototype deploy --outputs
    - name: Show rollback instructions
      text: az prototype deploy --rollback-info
    - name: Generate App Service deployment scripts
      text: az prototype deploy --generate-scripts --script-type webapp
"""

helps["prototype status"] = """
type: command
short-summary: Show current project status across all stages.
long-summary: |
    Displays a summary of the prototype project including configuration,
    stage progress (design, build, deploy), and pending changes.

    By default shows a human-readable summary. Use --json for machine-readable
    output suitable for scripting. Use --detailed for expanded per-stage details.
examples:
    - name: Show project status
      text: az prototype status
    - name: Show detailed status with per-stage breakdown
      text: az prototype status --detailed
    - name: Get machine-readable JSON output
      text: az prototype status --json
"""

helps["prototype analyze"] = """
type: group
short-summary: Analyze errors, costs, and diagnostics for the prototype.
long-summary: |
    Provides analysis capabilities powered by specialized AI agents.
    Use 'error' to diagnose and fix issues, or 'costs' to estimate
    Azure spending at different scale tiers.
"""

helps["prototype analyze error"] = """
type: command
short-summary: Analyze an error and get a fix with redeployment instructions.
long-summary: |
    Accepts an inline error string, log file path, or screenshot image.
    The QA engineer agent identifies the root cause, proposes a fix,
    and tells you which commands to run to redeploy.
examples:
    - name: Analyze an inline error message
      text: az prototype analyze error --input "ResourceNotFound - The Resource was not found"
    - name: Analyze a log file
      text: az prototype analyze error --input ./deploy.log
    - name: Analyze a screenshot
      text: az prototype analyze error --input ./error-screenshot.png
"""

helps["prototype analyze costs"] = """
type: command
short-summary: Estimate Azure costs at Small/Medium/Large t-shirt sizes.
long-summary: |
    Analyzes the current architecture design, queries Azure Retail Prices
    API for each component, and produces a cost report with estimates
    at three consumption tiers.

    Results are cached in .prototype/state/cost_analysis.yaml. Re-running
    the command returns the cached result unless the design context has
    changed. Use --refresh to force a fresh analysis.
examples:
    - name: Generate cost estimate
      text: az prototype analyze costs
    - name: Get costs in JSON format
      text: az prototype analyze costs --output-format json
    - name: Force fresh analysis (bypass cache)
      text: az prototype analyze costs --refresh
"""

helps["prototype config"] = """
type: group
short-summary: Manage prototype project configuration.
"""

helps["prototype config init"] = """
type: command
short-summary: Interactive setup to create a prototype.yaml configuration file.
long-summary: |
    Walks through standard project questions (name, region, IaC tool,
    naming strategy, AI provider, etc.) and generates a prototype.yaml file.

    Naming strategies control how Azure resources are named:
      - microsoft-alz (default): Azure Landing Zone — {zoneid}-{type}-{service}-{env}-{region}
      - microsoft-caf:           Cloud Adoption Framework — {type}-{org}-{service}-{env}-{region}-{instance}
      - simple:                  Quick prototypes — {org}-{service}-{type}-{env}
      - enterprise:              Business unit scoped — {type}-{bu}-{org}-{service}-{env}-{region}-{instance}
      - custom:                  User-defined pattern

    The configuration file is optional — all settings can also be
    provided via command-line parameters.
examples:
    - name: Start interactive configuration
      text: az prototype config init
    - name: Set naming strategy after init
      text: az prototype config set --key naming.strategy --value microsoft-caf
    - name: Change the landing zone
      text: az prototype config set --key naming.zone_id --value zp
"""

helps["prototype config show"] = """
type: command
short-summary: Display current project configuration.
long-summary: |
    Shows the full prototype.yaml configuration. Secret values (API keys,
    subscription IDs, tokens) stored in prototype.secrets.yaml are masked
    as '***' in the output.
"""

helps["prototype config get"] = """
type: command
short-summary: Get a single configuration value.
long-summary: |
    Retrieves a configuration value by its dot-separated key path.
    Secret values are masked as '***'.
examples:
    - name: Get the AI provider
      text: az prototype config get --key ai.provider
    - name: Get the project location
      text: az prototype config get --key project.location
    - name: Get the naming strategy
      text: az prototype config get --key naming.strategy
"""

helps["prototype config set"] = """
type: command
short-summary: Set a configuration value.
examples:
    - name: Switch AI provider
      text: az prototype config set --key ai.provider --value azure-openai
    - name: Change deployment location
      text: az prototype config set --key project.location --value westus2
    - name: Switch naming strategy
      text: az prototype config set --key naming.strategy --value microsoft-caf
    - name: Change landing zone to production
      text: az prototype config set --key naming.zone_id --value zp
"""

helps["prototype knowledge"] = """
type: group
short-summary: Manage knowledge base contributions.
long-summary: |
    Submit knowledge contributions as GitHub Issues when patterns or pitfalls
    are discovered during QA diagnosis or manual testing.  Contributions are
    reviewed and merged into the shared knowledge base so future sessions
    benefit from community findings.
"""

helps["prototype knowledge contribute"] = """
type: command
short-summary: Submit a knowledge base contribution as a GitHub Issue.
long-summary: |
    Creates a structured GitHub Issue in the knowledge repository when a
    pattern, pitfall, or service gap is discovered.

    Interactive by default — walks through type, section, context, rationale,
    and content.  Non-interactive when --service and --description are provided.

    Use --draft to preview the contribution without submitting (skips gh auth).
    Use --file to load contribution content from a file.
examples:
    - name: Interactive knowledge contribution
      text: az prototype knowledge contribute
    - name: Quick non-interactive contribution
      text: az prototype knowledge contribute --service cosmos-db --description "RU throughput must be >= 400"
    - name: Contribute from a file
      text: az prototype knowledge contribute --file ./finding.md
    - name: Preview without submitting
      text: az prototype knowledge contribute --service redis --description "Cache eviction pitfall" --draft
"""

helps["prototype agent"] = """
type: group
short-summary: Manage AI agents for prototype generation.
long-summary: |
    Agents are specialized AI personas that handle different aspects of
    prototype generation. Built-in agents ship with the extension; you can
    add custom agents or override built-in ones.

    Built-in agents: cloud-architect, terraform, bicep, app-developer,
    documentation, qa-engineer, biz-analyst, cost-analyst, project-manager,
    security-reviewer, monitoring-agent

    Agent resolution order: custom > override > built-in.

    Use 'agent list' to see all available agents, 'agent add' to create
    custom agents, 'agent test' to validate an agent, and 'agent export'
    to share agent definitions.
"""

helps["prototype generate"] = """
type: group
short-summary: Generate documentation and spec-kit artifacts.
long-summary: |
    Commands for generating project documentation and specification-kit
    bundles from built-in templates.

    Templates are populated with project configuration values and written
    to the output directory. Remaining [PLACEHOLDER] values are left for
    AI agents to fill during the build stage.

    Document types generated:
      - ARCHITECTURE.md:    High-level and detailed architecture diagrams
      - DEPLOYMENT.md:      Step-by-step deployment guide
      - DEVELOPMENT.md:     Developer setup and local dev guide
      - CONFIGURATION.md:   Azure service configuration reference
      - AS_BUILT.md:        As-built record of delivered solution
      - COST_ESTIMATE.md:   Azure cost estimates at t-shirt sizes
"""

helps["prototype generate backlog"] = """
type: command
short-summary: Generate a backlog and push work items to GitHub or Azure DevOps.
long-summary: |
    Interactive by default — generates a structured backlog from the architecture
    design and enters a conversational session where you can review, refine, add,
    update, and remove items before pushing them to your provider.

    GitHub mode creates issues with checkbox task lists in the description.
    Azure DevOps mode creates Features with User Stories and Tasks.

    Scope-aware: in-scope items become stories, out-of-scope items are excluded,
    and deferred items get a separate "Deferred / Future Work" epic.

    Slash commands during session:
      /list       - Show all items grouped by epic
      /show N     - Show item N with full details
      /add        - Add a new item (AI-assisted)
      /remove N   - Remove item N
      /preview    - Show what will be pushed
      /save       - Save to concept/docs/BACKLOG.md
      /push       - Push all pending items to provider
      /push N     - Push specific item
      /status     - Show push status per item
      /help       - Show available commands
      /quit       - Exit session

    Use --quick for a lighter generate -> confirm -> push flow.
    Use --status to view current backlog state without starting a session.
    Use --refresh to force fresh AI generation.

    Backlog provider, org, and project can be set in prototype.yaml
    under the 'backlog' section so you don't have to pass them every time.
examples:
    - name: Interactive backlog session (default)
      text: az prototype generate backlog --provider github
    - name: Quick mode (generate and push)
      text: az prototype generate backlog --provider github --quick
    - name: Show current backlog status
      text: az prototype generate backlog --status
    - name: Force fresh generation
      text: az prototype generate backlog --refresh
    - name: Generate Azure DevOps work items
      text: az prototype generate backlog --provider devops --org myorg --project myproject
    - name: Use defaults from prototype.yaml
      text: az prototype generate backlog
"""

helps["prototype generate docs"] = """
type: command
short-summary: Generate documentation from templates with AI population.
long-summary: |
    Reads each documentation template, applies project configuration values
    (project name, location, date), and writes the resulting markdown files
    to the output directory.

    When a design context is available (from 'az prototype design'), the
    doc-agent fills remaining [PLACEHOLDER] values with real content from
    the architecture. Falls back to static templates if no design context
    or AI is unavailable.

    Default output directory: ./docs/
examples:
    - name: Generate documentation to default directory
      text: az prototype generate docs
    - name: Generate documentation to a custom path
      text: az prototype generate docs --path ./deliverables/docs
"""

helps["prototype generate speckit"] = """
type: command
short-summary: Generate the spec-kit documentation bundle with AI population.
long-summary: |
    Creates a self-contained package of documentation templates that define
    the project's deliverables. The spec-kit is typically stored under
    the concept directory and serves as the starting point for all project
    documentation.

    When a design context is available (from 'az prototype design'), the
    doc-agent fills remaining [PLACEHOLDER] values with real content from
    the architecture. Falls back to static templates if no design context
    or AI is unavailable. Includes a manifest.json with metadata.

    Default output directory: ./concept/.specify/
examples:
    - name: Generate spec-kit to default directory
      text: az prototype generate speckit
    - name: Generate spec-kit to a custom path
      text: az prototype generate speckit --path ./my-speckit
"""

helps["prototype agent list"] = """
type: command
short-summary: List all available agents (built-in and custom).
long-summary: |
    Displays agents grouped by source (built-in, custom, override) with
    name, description, and capabilities.

    By default shows a formatted console display. Use --json for
    machine-readable output. Use --detailed for expanded capability details.
examples:
    - name: List all agents with formatted output
      text: az prototype agent list
    - name: Get machine-readable JSON output
      text: az prototype agent list --json
    - name: Show expanded details
      text: az prototype agent list --detailed
    - name: List only custom agents
      text: az prototype agent list --show-builtin false
"""

helps["prototype agent add"] = """
type: command
short-summary: Add a custom agent to the project.
long-summary: |
    Creates a new custom agent definition in .prototype/agents/ and registers it
    in the project configuration manifest.

    Interactive by default — when neither --file nor --definition is provided,
    walks you through description, capabilities, constraints, system prompt,
    and optional few-shot examples.

    Non-interactive modes:
    - --definition copies a built-in agent's YAML as a starting point
    - --file uses your own YAML or Python definition

    After creation, test the agent with 'az prototype agent test --name <name>'.
examples:
    - name: Interactive agent creation (default)
      text: az prototype agent add --name my-data-agent
    - name: Start from the cloud_architect built-in definition
      text: az prototype agent add --name my-architect --definition cloud_architect
    - name: Add agent from a user-supplied file
      text: az prototype agent add --name security --file ./security-checker.yaml
"""

helps["prototype agent override"] = """
type: command
short-summary: Override a built-in agent with a custom definition.
long-summary: |
    Replaces the behavior of a built-in agent with a custom implementation.
    The override is recorded in prototype.yaml and takes effect on the next
    command run.

    The override file is validated: must exist on disk, parse as valid YAML,
    and contain a 'name' field. A warning is shown if the target name does
    not match a known built-in agent.
examples:
    - name: Override cloud-architect with custom definition
      text: az prototype agent override --name cloud-architect --file ./my-architect.yaml
    - name: Override the terraform agent
      text: az prototype agent override --name terraform --file ./custom-terraform.yaml
"""

helps["prototype agent show"] = """
type: command
short-summary: Show details of a specific agent.
long-summary: |
    Displays agent metadata including description, source, capabilities,
    constraints, and a preview of the system prompt.

    Use --detailed to show the full system prompt instead of a 200-character
    preview. Use --json for machine-readable output.
examples:
    - name: Show agent details
      text: az prototype agent show --name cloud-architect
    - name: Show full system prompt
      text: az prototype agent show --name cloud-architect --detailed
    - name: Get JSON output
      text: az prototype agent show --name cloud-architect --json
"""

helps["prototype agent remove"] = """
type: command
short-summary: Remove a custom agent or override.
long-summary: |
    Removes a custom agent definition from .prototype/agents/ and cleans up
    the project configuration manifest entry. Can also remove overrides,
    restoring the built-in agent behavior.

    Built-in agents cannot be removed.
examples:
    - name: Remove a custom agent
      text: az prototype agent remove --name my-data-agent
    - name: Remove an override (restores built-in)
      text: az prototype agent remove --name cloud-architect
"""

helps["prototype agent update"] = """
type: command
short-summary: Update an existing custom agent's properties.
long-summary: |
    Interactive by default — walks through the same prompts as 'agent add'
    with current values as defaults. Press Enter to keep existing values.

    Providing any field flag (--description, --capabilities,
    --system-prompt-file) switches to non-interactive mode and only
    changes the specified fields.

    Only custom YAML agents can be updated.
examples:
    - name: Interactive update with current values as defaults
      text: az prototype agent update --name my-agent
    - name: Update only the description
      text: az prototype agent update --name my-agent --description "New description"
    - name: Update capabilities
      text: az prototype agent update --name my-agent --capabilities "architect,deploy"
    - name: Update system prompt from file
      text: az prototype agent update --name my-agent --system-prompt-file ./new-prompt.txt
"""

helps["prototype agent test"] = """
type: command
short-summary: Send a test prompt to any agent and display the response.
long-summary: |
    Sends a prompt to the specified agent using the configured AI provider
    and displays the response. Useful for validating agent behavior after
    creation or update.

    Reports the model used and token count after the response.
    Requires a configured AI provider (run 'az prototype init' first).
examples:
    - name: Test with default prompt
      text: az prototype agent test --name cloud-architect
    - name: Test with custom prompt
      text: az prototype agent test --name my-agent --prompt "Design a web app with Redis caching"
"""

helps["prototype agent export"] = """
type: command
short-summary: Export any agent (including built-in) as a YAML file.
long-summary: |
    Exports the agent's metadata, system prompt, capabilities, constraints,
    and examples as a portable YAML file. The exported file can be shared
    with other projects or used as a starting point for customization.

    Built-in agents can be exported to inspect or customize their definitions.
examples:
    - name: Export a built-in agent
      text: az prototype agent export --name cloud-architect
    - name: Export to a specific path
      text: az prototype agent export --name qa-engineer --output-file ./agents/qa.yaml
"""
