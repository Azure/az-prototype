"""Init stage — project scaffolding, auth, and configuration."""

import logging
from datetime import datetime, timezone
from pathlib import Path

from knack.util import CLIError

from azext_prototype.agents.base import AgentContext
from azext_prototype.agents.registry import AgentRegistry
from azext_prototype.config import ProjectConfig
from azext_prototype.stages.base import BaseStage, StageGuard, StageState
from azext_prototype.templates.registry import TemplateRegistry

logger = logging.getLogger(__name__)

# Project directory structure created by init
PROJECT_SCAFFOLD = {
    "concept": {
        "apps": {},
        "infra": {
            "terraform": {},
            "bicep": {},
        },
        "db": {
            "sql": {},
            "cosmos": {},
            "databricks": {},
            "fabric": {},
        },
        "docs": {},
    },
    ".prototype": {
        "agents": {},
        "state": {},
        "policies": {},
    },
}

# Default AI model per provider.
# Claude models are only available via the Copilot API.
_DEFAULT_MODELS = {
    "copilot": "claude-sonnet-4.5",
    "github-models": "gpt-4o",
    "azure-openai": "gpt-4o",
}


class InitStage(BaseStage):
    """Initialize a new prototype project.

    This stage:
    1. Optionally authenticates with GitHub (copilot/github-models only)
    2. Creates the project directory structure
    3. Generates prototype.yaml configuration
    4. Optionally applies a workload template
    """

    def __init__(self):
        super().__init__(
            name="init",
            description="Initialize project, authenticate, and scaffold",
            reentrant=False,
        )

    def get_guards(self) -> list[StageGuard]:
        """Init has no unconditional guards.

        The ``gh`` CLI is only required for copilot/github-models providers.
        That check is performed inside ``execute()`` after the provider is
        known, so that azure-openai users are not blocked.
        """
        return []

    def execute(
        self,
        agent_context: AgentContext,
        registry: AgentRegistry,
        **kwargs,
    ) -> dict:
        """Execute the init stage."""
        from azext_prototype.ui.console import console

        name = kwargs.get("name", "")
        location = kwargs.get("location")
        iac_tool = kwargs.get("iac_tool", "terraform")
        ai_provider = kwargs.get("ai_provider", "copilot")
        output_dir = kwargs.get("output_dir", ".")
        environment = kwargs.get("environment", "dev")
        model = kwargs.get("model")
        _template = kwargs.get("template")

        # --- Validate required params ---
        if not name:
            raise CLIError("Project name is required. Use --name <name>.")
        if not location:
            raise CLIError("Azure region is required. Use --location <region>.")

        # Resolve project template if specified
        template = None
        if _template:
            tmpl_registry = TemplateRegistry()
            tmpl_registry.load()
            template = tmpl_registry.get(_template)
            if template is None:
                available = tmpl_registry.list_names()
                raise CLIError(f"Unknown template '{_template}'. " f"Available templates: {', '.join(available)}")

        # --- Idempotency check ---
        project_dir = Path(output_dir).resolve() / name
        config_path = project_dir / ProjectConfig.CONFIG_FILENAME
        if config_path.exists():
            console.print_warning(f"Project already initialized at {project_dir}")
            answer = input("Reinitialize? This will reset configuration. [y/N] ").strip().lower()
            if answer != "y":
                return {"status": "cancelled", "message": "Existing project preserved."}

        self.state = StageState.IN_PROGRESS
        result: dict = {}

        # --- GitHub auth (only for copilot/github-models) ---
        if ai_provider in ("copilot", "github-models"):
            if not self._check_gh():
                raise CLIError(
                    "GitHub CLI (gh) is not installed. "
                    "Install from https://cli.github.com/\n"
                    "gh is required for the '{ai_provider}' AI provider."
                )

            console.print_header("Authenticating with GitHub")

            from azext_prototype.auth.copilot_license import CopilotLicenseValidator
            from azext_prototype.auth.github_auth import GitHubAuthManager

            auth_manager = GitHubAuthManager()
            user_info = auth_manager.ensure_authenticated()
            result["github_user"] = user_info.get("login")
            console.print_success(f"Authenticated as: {result['github_user']}")

            # Validate Copilot license
            console.print_header("Validating Copilot License")
            license_validator = CopilotLicenseValidator(auth_manager)
            try:
                license_info = license_validator.validate_license()
                result["copilot_license"] = license_info
                console.print_success(f"Copilot license: {license_info.get('plan', 'active')}")
            except CLIError as e:
                console.print_warning(str(e))
                console.print_dim("  Continuing — license will be validated on first AI call.")
                result["copilot_license"] = {"status": "unverified"}

            # Check for Copilot credentials
            if ai_provider == "copilot":
                from azext_prototype.ai.copilot_auth import is_copilot_authenticated

                if is_copilot_authenticated():
                    console.print_success("Copilot API credentials found")
                else:
                    console.print_warning(
                        "No GitHub credentials found for Copilot API. " "Run 'copilot login' to authenticate."
                    )
        else:
            console.print_dim(f"\n  Skipping GitHub auth (not required for {ai_provider} provider)")
            result["github_user"] = None

        # --- Create project directory structure ---
        console.print_header("Creating Project")
        self._create_scaffold(project_dir)
        result["project_dir"] = str(project_dir)
        console.print_success(str(project_dir))

        # --- Generate configuration ---
        config = ProjectConfig(str(project_dir))

        resolved_model = model or _DEFAULT_MODELS.get(ai_provider, "gpt-4o")

        config_data = config.create_default(
            {
                "project": {
                    "name": name,
                    "location": location,
                    "environment": environment,
                    "iac_tool": iac_tool,
                },
                "ai": {
                    "provider": ai_provider,
                    "model": resolved_model,
                },
            }
        )

        # Apply template to configuration
        if template:
            config.set("project.template", template.name)
            config.set(
                "project.services",
                [{"name": s.name, "type": s.type, "tier": s.tier, "config": s.config} for s in template.services],
            )
            if template.iac_defaults:
                config.set("project.iac_defaults", template.iac_defaults)
            if template.requirements:
                config.set("project.requirements", template.requirements)
            config_data = config.load()
        result["config"] = config_data

        # Create .gitignore
        self._create_gitignore(project_dir)

        # List created files
        created = ["prototype.yaml", ".gitignore"]
        if config.secrets_path.exists():
            created.append("prototype.secrets.yaml")
        console.print_file_list(created)

        # Mark stage complete
        config.set("stages.init.completed", True)
        config.set("stages.init.timestamp", datetime.now(timezone.utc).isoformat())

        self.state = StageState.COMPLETED
        result["status"] = "success"

        # --- Summary panel ---
        summary_lines = [
            f"  Project:     {name}",
            f"  Location:    {location}",
            f"  Environment: {environment}",
            f"  AI Provider: {ai_provider} ({resolved_model})",
            f"  IaC Tool:    {iac_tool}",
        ]
        if template:
            summary_lines.append(f"  Template:    {template.display_name} ({template.name})")
            summary_lines.append(f"  Services:    {', '.join(s.type for s in template.services)}")
        summary_lines.append("")
        summary_lines.append(f"  Next: cd {name} && az prototype design")
        console.panel("\n".join(summary_lines), title="Project Initialized")

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _create_scaffold(self, project_dir: Path):
        """Create the project directory structure."""
        self._create_dirs(project_dir, PROJECT_SCAFFOLD)

    def _create_dirs(self, base: Path, structure: dict):
        """Recursively create directories from a structure dict."""
        base.mkdir(parents=True, exist_ok=True)
        for name, children in structure.items():
            child_path = base / name
            child_path.mkdir(parents=True, exist_ok=True)
            if isinstance(children, dict) and children:
                self._create_dirs(child_path, children)

    def _create_gitignore(self, project_dir: Path):
        """Create a .gitignore for the project."""
        gitignore_content = """# =============================================================================
# Prototype secrets (sensitive values only)
# =============================================================================
prototype.secrets.yaml

# =============================================================================
# Terraform / OpenTofu
# =============================================================================
.terraform/
*.tfstate
*.tfstate.backup
*.tfvars
*.tfvars.json
.terraform.lock.hcl
crash.log
override.tf
override.tf.json
*_override.tf
*_override.tf.json

# =============================================================================
# Bicep
# =============================================================================
*.bicep.parameters.local.json

# =============================================================================
# Python
# =============================================================================
__pycache__/
*.py[cod]
*$py.class
*.so
.venv/
env/
venv/
ENV/
*.egg-info/
*.egg
dist/
build/
.eggs/
*.whl
pip-log.txt
pip-delete-this-directory.txt
htmlcov/
.tox/
.nox/
.coverage
.coverage.*
.cache
nosetests.xml
coverage.xml
*.cover
*.py,cover
.hypothesis/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.pytype/

# =============================================================================
# C# / .NET
# =============================================================================
[Bb]in/
[Oo]bj/
[Dd]ebug/
[Rr]elease/
x64/
x86/
[Aa][Rr][Mm]/
[Aa][Rr][Mm]64/
bld/
[Ll]og/
[Ll]ogs/
*.suo
*.user
*.userosscache
*.sln.docstates
*.nupkg
*.snupkg
*.DotSettings.user
project.lock.json
project.fragment.lock.json
artifacts/
*_i.c
*_p.c
*_h.h
*.ilk
*.meta
*.obj
*.iobj
*.pch
*.pdb
*.ipdb
*.pgc
*.pgd
*.rsp
*.sbr
*.tlb
*.tli
*.tlh
*.tmp
*.tmp_proj
*_wpftmp.csproj
*.vspscc
*.vssscc
.builds
*.pidb
*.svclog
*.scc

# NuGet
*.nupkg
**/[Pp]ackages/*
!**/[Pp]ackages/build/
*.nuget.props
*.nuget.targets
project.assets.json

# =============================================================================
# .NET test results
# =============================================================================
[Tt]est[Rr]esult*/
[Bb]uild[Ll]og.*
TestResult.xml
[Dd]ebugPS/
[Rr]eleasePS/
*.trx
*.coverage
*.coveragexml
dlldata.c

# =============================================================================
# Visual Studio
# =============================================================================
.vs/
*.aps
*.ncb
*.opendb
*.opensdf
*.sdf
*.cachefile
*.VC.db
*.VC.VC.opendb
*.psess
*.vsp
*.vspx
*.sap
[Aa]uto[Gg]en[Ff]iles/
_ReSharper*/
*.[Rr]e[Ss]harper
*.DotSettings
[Tt]humbs.db
ehthumbs.db
ehthumbs_vista.db
[Dd]esktop.ini
$RECYCLE.BIN/
launchSettings.json

# =============================================================================
# JetBrains (Rider, IntelliJ, PyCharm, WebStorm, etc.)
# =============================================================================
.idea/
*.iml
*.iws
*.ipr
out/
.idea_modules/
atlassian-ide-plugin.xml
com_crashlytics_export_strings.xml
crashlytics.properties
crashlytics-build.properties
fabric.properties

# =============================================================================
# VS Code
# =============================================================================
.vscode/*
!.vscode/settings.json
!.vscode/tasks.json
!.vscode/launch.json
!.vscode/extensions.json
!.vscode/*.code-snippets
.history/
*.vsix

# =============================================================================
# Node.js / JavaScript / TypeScript
# =============================================================================
node_modules/
dist/
.output/
.nuxt/
.next/
*.tsbuildinfo
.npm
.eslintcache
.stylelintcache
*.js.map

# =============================================================================
# Java / Gradle / Maven
# =============================================================================
*.class
*.jar
*.war
*.nar
*.ear
*.zip
*.tar.gz
*.rar
hs_err_pid*
replay_pid*
target/
.gradle/
build/
!**/src/main/**/build/
!**/src/test/**/build/
.mvn/timing.properties
.mvn/wrapper/maven-wrapper.jar

# =============================================================================
# Go
# =============================================================================
*.exe
*.exe~
*.dll
*.dylib
*.test
*.out
vendor/

# =============================================================================
# Rust
# =============================================================================
/target/
Cargo.lock
**/*.rs.bk

# =============================================================================
# OS generated
# =============================================================================
.DS_Store
.DS_Store?
._*
.Spotlight-V100
.Trashes
Thumbs.db
[Dd]esktop.ini

# =============================================================================
# Environment & secrets
# =============================================================================
.env
.env.*
!.env.example
!.env.template
*.pem
*.key
*.pfx
*.p12

# =============================================================================
# Docker
# =============================================================================
**/docker-compose.override.yml

# =============================================================================
# Misc
# =============================================================================
*.log
*.bak
*.swp
*.swo
*~
"""
        gitignore_path = project_dir / ".gitignore"
        if not gitignore_path.exists():
            gitignore_path.write_text(gitignore_content, encoding="utf-8")

    @staticmethod
    def _check_gh() -> bool:
        """Check if gh CLI is available."""
        import subprocess

        try:
            result = subprocess.run(
                ["gh", "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False
