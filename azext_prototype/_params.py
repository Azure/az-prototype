"""CLI parameter definitions for az prototype."""

from azure.cli.core.commands.parameters import get_enum_type


def load_arguments(self, _):
    """Register CLI parameters for all commands."""

    # --- global: --json on every prototype command ---
    with self.argument_context("prototype") as c:
        c.argument(
            "json_output",
            options_list=["--json", "-j"],
            help="Output machine-readable JSON instead of formatted display.",
            action="store_true",
            default=False,
        )

    # --- az prototype init ---
    with self.argument_context("prototype init") as c:
        c.argument("name", help="Name of the prototype project.")
        c.argument("location", help="Azure region for resource deployment (e.g., eastus).")
        c.argument(
            "iac_tool",
            arg_type=get_enum_type(["terraform", "bicep"]),
            help="Infrastructure-as-code tool preference.",
            default="terraform",
        )
        c.argument(
            "ai_provider",
            arg_type=get_enum_type(["github-models", "azure-openai", "copilot"]),
            help="AI provider for agent interactions.",
            default="copilot",
        )
        c.argument("output_dir", help="Output directory for project files.", default=".")
        c.argument(
            "template",
            help="Project template to use (e.g., web-app, data-pipeline, ai-app).",
        )
        c.argument(
            "environment",
            arg_type=get_enum_type(["dev", "staging", "prod"]),
            help="Target environment for the prototype.",
            default="dev",
        )
        c.argument(
            "model",
            help="AI model to use (default: claude-sonnet-4.5 for copilot, gpt-4o for others).",
        )

    # --- az prototype design ---
    with self.argument_context("prototype design") as c:
        c.argument(
            "artifacts",
            help="Path to directory containing requirement documents, diagrams, or other artifacts.",
        )
        c.argument("context", help="Additional context or requirements as free text.")
        c.argument(
            "reset",
            help="Reset design state and start fresh.",
            action="store_true",
            default=False,
        )
        c.argument(
            "interactive",
            options_list=["--interactive", "-i"],
            help="Enter an interactive refinement loop after architecture generation.",
            action="store_true",
            default=False,
        )
        c.argument(
            "status",
            options_list=["--status", "-s"],
            help="Show current discovery status (open items, confirmed items) without starting a session.",
            action="store_true",
            default=False,
        )
        c.argument(
            "skip_discovery",
            options_list=["--skip-discovery"],
            help="Skip the discovery conversation and generate architecture directly from existing discovery state.",
            action="store_true",
            default=False,
        )

    # --- az prototype build ---
    with self.argument_context("prototype build") as c:
        c.argument(
            "scope",
            arg_type=get_enum_type(["all", "infra", "apps", "db", "docs"]),
            help="What to build.",
            default="all",
        )
        c.argument(
            "dry_run",
            help="Preview what would be generated without writing files.",
            action="store_true",
            default=False,
        )
        c.argument(
            "status",
            options_list=["--status", "-s"],
            help="Show current build progress without starting a session.",
            action="store_true",
            default=False,
        )
        c.argument(
            "reset",
            help="Clear existing build state and start fresh.",
            action="store_true",
            default=False,
        )
        c.argument(
            "auto_accept",
            options_list=["--auto-accept"],
            help="Automatically accept the default recommendation for policy violations and standards conflicts.",
            action="store_true",
            default=False,
        )

    # --- az prototype deploy ---
    with self.argument_context("prototype deploy") as c:
        c.argument(
            "stage",
            type=int,
            help="Deploy only a specific stage number (use --status to see stages).",
        )
        c.argument(
            "force",
            help="Force full deployment, ignoring change tracking.",
            action="store_true",
            default=False,
        )
        c.argument(
            "dry_run",
            help="Preview what would be deployed (what-if for Bicep, plan for Terraform).",
            action="store_true",
            default=False,
        )
        c.argument(
            "status",
            options_list=["--status", "-s"],
            help="Show current deploy progress without starting a session.",
            action="store_true",
            default=False,
        )
        c.argument(
            "reset",
            help="Clear deploy state and start fresh.",
            action="store_true",
            default=False,
        )
        # NOTE: --subscription is a built-in Azure CLI global parameter;
        # do not re-register it here or it will conflict.
        # NOTE: resource_group is resolved from config (deploy.resource_group),
        # not a CLI flag — the build phase determines it.
        c.argument("tenant", help="Azure AD tenant ID for cross-tenant deployment.")
        c.argument(
            "service_principal",
            options_list=["--service-principal"],
            action="store_true",
            default=False,
            help="Authenticate using a service principal before deploying.",
        )
        c.argument("client_id", help="Service principal application/client ID (or set via config).")
        c.argument("client_secret", help="Service principal client secret (or set via config).")
        c.argument("tenant_id", help="Tenant ID for service principal authentication (or set via config).")

    # --- az prototype status ---
    with self.argument_context("prototype status") as c:
        c.argument(
            "detailed",
            options_list=["--detailed", "-d"],
            help="Show expanded per-stage details.",
            action="store_true",
            default=False,
        )

    # --- az prototype deploy generate-scripts ---
    with self.argument_context("prototype deploy generate-scripts") as c:
        c.argument(
            "scope",
            arg_type=get_enum_type(["apps"]),
            help="Scope for script generation.",
            default="apps",
        )
        c.argument(
            "deploy_type",
            arg_type=get_enum_type(["webapp", "container_app", "function"]),
            help="Azure deployment target type.",
            default="webapp",
        )
        c.argument("resource_group", help="Default resource group name for generated scripts.")
        c.argument("registry", help="Container registry URL (for container_app type).")

    # --- az prototype analyze ---
    with self.argument_context("prototype analyze error") as c:
        c.argument(
            "input",
            help=(
                "Error input to analyze. Can be an inline error string, "
                "path to a log file, or path to a screenshot image."
            ),
        )

    with self.argument_context("prototype analyze costs") as c:
        c.argument(
            "output_format",
            arg_type=get_enum_type(["table", "json", "markdown"]),
            help="Output format for the cost report.",
            default="markdown",
        )
        c.argument(
            "refresh",
            action="store_true",
            default=False,
            help="Force fresh analysis, bypassing cached results.",
        )

    # --- az prototype generate ---
    with self.argument_context("prototype generate backlog") as c:
        c.argument(
            "provider",
            arg_type=get_enum_type(["github", "devops"]),
            help="Backlog provider: 'github' for GitHub Issues, 'devops' for Azure DevOps work items.",
            default=None,
        )
        c.argument("org", help="Organization or owner name (GitHub org/user or Azure DevOps org).")
        c.argument("project", help="Project name (Azure DevOps project or GitHub repo).")
        c.argument(
            "output_format",
            arg_type=get_enum_type(["table", "json", "markdown"]),
            help="Output format for the backlog.",
            default="markdown",
        )
        c.argument(
            "quick",
            help="Skip interactive session — generate, confirm, and push.",
            action="store_true",
            default=False,
        )
        c.argument(
            "refresh",
            help="Force fresh AI generation, bypassing cached items.",
            action="store_true",
            default=False,
        )
        c.argument(
            "status",
            options_list=["--status", "-s"],
            help="Show current backlog state without starting a session.",
            action="store_true",
            default=False,
        )
        c.argument(
            "push",
            help="In quick mode, auto-push after generation.",
            action="store_true",
            default=False,
        )

    with self.argument_context("prototype generate docs") as c:
        c.argument("path", help="Output directory for generated documents.", default=None)

    with self.argument_context("prototype generate speckit") as c:
        c.argument("path", help="Output directory for the spec-kit bundle.", default=None)

    # --- az prototype knowledge contribute ---
    with self.argument_context("prototype knowledge contribute") as c:
        c.argument("service", help="Azure service name (e.g., cosmos-db, key-vault).")
        c.argument("description", help="Brief description of the knowledge contribution.")
        c.argument("file", help="Path to a file containing the contribution content.")
        c.argument(
            "draft",
            help="Preview the contribution without submitting.",
            action="store_true",
            default=False,
        )
        c.argument(
            "contribution_type",
            options_list=["--type"],
            arg_type=get_enum_type(
                [
                    "Service pattern update",
                    "New service",
                    "Tool pattern",
                    "Language pattern",
                    "Pitfall",
                ]
            ),
            help="Type of knowledge contribution.",
            default="Pitfall",
        )
        c.argument("section", help="Target section within the knowledge file.")

    # --- az prototype config ---
    with self.argument_context("prototype config get") as c:
        c.argument("key", help="Configuration key to retrieve (dot-separated path, e.g., ai.provider).")

    with self.argument_context("prototype config set") as c:
        c.argument("key", help="Configuration key (dot-separated path, e.g., ai.provider).")
        c.argument("value", help="Configuration value to set.")

    # --- az prototype agent ---
    with self.argument_context("prototype agent add") as c:
        c.argument("name", help="Unique name for the custom agent (used as filename and registry key).")
        c.argument("file", help="Path to a YAML or Python agent definition file. Mutually exclusive with --definition.")
        c.argument(
            "definition",
            help=(
                "Name of a built-in definition to copy as a starting point "
                "(e.g., cloud_architect, bicep_agent, terraform_agent). "
                "Mutually exclusive with --file."
            ),
        )

    with self.argument_context("prototype agent override") as c:
        c.argument("name", help="Name of the built-in agent to override.")
        c.argument("file", help="Path to YAML or Python agent definition file.")

    with self.argument_context("prototype agent remove") as c:
        c.argument("name", help="Name of the custom agent to remove.")

    with self.argument_context("prototype agent list") as c:
        c.argument(
            "show_builtin",
            help="Include built-in agents in the listing.",
            action="store_true",
            default=True,
        )
        c.argument(
            "detailed",
            options_list=["--detailed", "-d"],
            help="Show expanded capability details for each agent.",
            action="store_true",
            default=False,
        )

    with self.argument_context("prototype agent show") as c:
        c.argument("name", help="Name of the agent to show details for.")
        c.argument(
            "detailed",
            options_list=["--detailed", "-d"],
            help="Show full system prompt instead of 200-char preview.",
            action="store_true",
            default=False,
        )

    with self.argument_context("prototype agent update") as c:
        c.argument("name", help="Name of the custom agent to update.")
        c.argument("description", options_list=["--description"], help="New description for the agent.")
        c.argument(
            "capabilities",
            options_list=["--capabilities"],
            help="Comma-separated list of capabilities (e.g., architect,deploy).",
        )
        c.argument(
            "system_prompt_file",
            options_list=["--system-prompt-file"],
            help="Path to a text file containing the new system prompt.",
        )

    with self.argument_context("prototype agent test") as c:
        c.argument("name", help="Name of the agent to test.")
        c.argument(
            "prompt",
            help="Test prompt to send to the agent.",
            default=None,
        )

    with self.argument_context("prototype agent export") as c:
        c.argument("name", help="Name of the agent to export.")
        c.argument(
            "output",
            help="Output file path for the exported YAML.",
            default=None,
        )
