"""Build stage — generate IaC and application code with staged output.

Creates Terraform/Bicep modules, application source code, SQL scripts,
and documentation based on the architecture design.

**Interactive by default** — the build session uses Claude Code-inspired
bordered prompts, progress indicators, policy enforcement, and a
conversational review loop.  Use ``--dry-run`` for non-interactive mode.

OUTPUT STAGING: All generated artifacts are organized into fine-grained,
dependency-ordered deployment stages.  Each infrastructure component,
database system, and application gets its own stage.  The deploy stage
reads this staging metadata from ``build.yaml``.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from knack.util import CLIError

from azext_prototype.agents.base import AgentContext
from azext_prototype.agents.registry import AgentRegistry
from azext_prototype.config import ProjectConfig
from azext_prototype.stages.base import BaseStage, StageGuard, StageState
from azext_prototype.stages.build_session import BuildSession
from azext_prototype.stages.build_state import BuildState
from azext_prototype.ui.console import console as default_console

logger = logging.getLogger(__name__)

# Template matching threshold — a template must share at least this
# fraction of its services with the design architecture to be considered
# a match.
_TEMPLATE_MATCH_THRESHOLD = 0.30


class BuildStage(BaseStage):
    """Generate infrastructure and application code.

    Uses the architecture design to create:
    - Terraform or Bicep modules (based on config)
    - Application source code
    - SQL DDL scripts
    - Deployment scripts
    - Configuration documentation

    In interactive mode (the default), delegates to
    :class:`~.build_session.BuildSession` for a full conversational
    experience.  In ``--dry-run`` mode, performs a lightweight pass
    without writing files.
    """

    def __init__(self):
        super().__init__(
            name="build",
            description="Generate IaC and application code",
            reentrant=True,
        )

    def get_guards(self) -> list[StageGuard]:
        return [
            StageGuard(
                name="project_initialized",
                description="Project must be initialized",
                check_fn=lambda: Path("prototype.yaml").is_file(),
                error_message="Run 'az prototype init' first.",
            ),
            StageGuard(
                name="discovery_complete",
                description="Discovery must be completed",
                check_fn=lambda: Path(".prototype/state/discovery.yaml").is_file(),
                error_message=("No discovery state found. " "Run 'az prototype design' to complete discovery first."),
            ),
            StageGuard(
                name="design_complete",
                description="Design stage must be completed",
                check_fn=lambda: Path(".prototype/state/design.json").is_file(),
                error_message=(
                    "Design stage has not been completed. "
                    "Run 'az prototype design' to generate the architecture first."
                ),
            ),
        ]

    def execute(
        self,
        agent_context: AgentContext,
        registry: AgentRegistry,
        **kwargs,
    ) -> dict:
        """Execute the build stage.

        Parameters
        ----------
        scope : str
            Build scope (``all``, ``infra``, ``apps``, ``db``, ``docs``).
        dry_run : bool
            Non-interactive mode — show what would be built without
            writing files.
        reset : bool
            Clear existing build state and start fresh.
        input_fn / print_fn : callable
            Injectable I/O for testing.
        """
        scope = kwargs.get("scope", "all")
        dry_run = kwargs.get("dry_run", False)
        reset = kwargs.get("reset", False)
        auto_accept = kwargs.get("auto_accept", False)
        input_fn = kwargs.get("input_fn")
        print_fn = kwargs.get("print_fn")

        self.state = StageState.IN_PROGRESS
        config = ProjectConfig(agent_context.project_dir)
        config.load()

        # Load architecture design
        design = self._load_design(agent_context.project_dir)
        if not design.get("architecture"):
            raise CLIError("No architecture design found. Run 'az prototype design' first.")

        # Build state management
        build_state = BuildState(agent_context.project_dir)
        if reset:
            build_state.reset()
            self._clean_output_dirs(agent_context.project_dir)
        elif build_state.exists:
            build_state.load()

        # Template matching (returns list — may be empty)
        templates = self._match_templates(design, config)

        if dry_run:
            # Non-interactive dry run
            return self._execute_dry_run(
                agent_context,
                registry,
                design,
                config,
                scope,
                templates,
                print_fn=print_fn,
            )

        # Interactive build session (default)
        session = BuildSession(
            agent_context,
            registry,
            console=default_console if print_fn is None else None,
            build_state=build_state,
            auto_accept=auto_accept,
        )
        result = session.run(
            design=design,
            templates=templates,
            scope=scope,
            input_fn=input_fn,
            print_fn=print_fn,
        )

        if result.cancelled:
            self.state = StageState.FAILED
            return {"status": "cancelled"}

        # Update project config
        config.set("stages.build.completed", True)
        config.set("stages.build.timestamp", datetime.now(timezone.utc).isoformat())
        if result.policy_overrides:
            config.set("build.policy_overrides", result.policy_overrides)

        self.state = StageState.COMPLETED

        return {
            "status": "success",
            "scope": scope,
            "files_generated": result.files_generated,
            "deployment_stages": result.deployment_stages,
            "resources": result.resources,
        }

    # ------------------------------------------------------------------
    # Output directory cleanup
    # ------------------------------------------------------------------

    _OUTPUT_DIRS = ("concept/infra", "concept/apps", "concept/db", "concept/docs")

    def _clean_output_dirs(self, project_dir: str) -> None:
        """Remove generated output directories so ``--reset`` starts clean.

        Without this, stale files from a previous build can leak into the
        next Terraform/Bicep run and cause deployment failures.
        """
        import shutil

        for rel in self._OUTPUT_DIRS:
            target = Path(project_dir) / rel
            if target.is_dir():
                shutil.rmtree(target)
                logger.info("Cleaned %s", rel)

    # ------------------------------------------------------------------
    # Template matching
    # ------------------------------------------------------------------

    def _match_templates(self, design: dict, config: ProjectConfig) -> list:
        """Match workload templates against the design architecture.

        Scores each template by the fraction of its service types that
        appear in the architecture text.  Returns all templates with a
        score above :data:`_TEMPLATE_MATCH_THRESHOLD`, sorted by score
        (highest first).

        Returns an empty list when no templates match — this is perfectly
        valid; the build works entirely from the design architecture.
        """
        from azext_prototype.templates.registry import TemplateRegistry

        architecture = design.get("architecture", "").lower()
        if not architecture:
            return []

        registry = TemplateRegistry()
        registry.load()
        all_templates = registry.list_templates()

        if not all_templates:
            return []

        scored: list[tuple[float, object]] = []
        for tmpl in all_templates:
            service_types = tmpl.service_names()
            if not service_types:
                continue

            matches = sum(1 for st in service_types if st.replace("-", " ") in architecture or st in architecture)
            score = matches / len(service_types)
            if score >= _TEMPLATE_MATCH_THRESHOLD:
                scored.append((score, tmpl))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [tmpl for _, tmpl in scored]

    # ------------------------------------------------------------------
    # Dry run (non-interactive)
    # ------------------------------------------------------------------

    def _execute_dry_run(
        self,
        agent_context: AgentContext,
        registry: AgentRegistry,
        design: dict,
        config: ProjectConfig,
        scope: str,
        templates: list,
        *,
        print_fn=None,
    ) -> dict:
        """Non-interactive dry run — show what would be built."""
        _print = print_fn or default_console.print

        iac_tool = config.get("project.iac_tool", "terraform")

        _print("")
        _print(f"  Build Stage — DRY RUN (scope: {scope})")
        _print("  " + "=" * 40)
        _print("")
        _print("  No files will be written.")
        _print("")

        if templates:
            tmpl_names = ", ".join(t.display_name for t in templates)
            _print(f"  Template(s): {tmpl_names}")
        else:
            _print("  Templates: None (building from architecture)")
        _print(f"  IaC Tool: {iac_tool}")
        _print("")

        results = {}

        if scope in ("all", "infra"):
            _print(f"  Would generate {iac_tool} infrastructure code")
            results["infra"] = {"status": "dry-run"}

        if scope in ("all", "apps"):
            _print("  Would generate application code")
            results["apps"] = {"status": "dry-run"}

        if scope in ("all", "db"):
            _print("  Would generate database scripts")
            results["db"] = {"status": "dry-run"}

        if scope in ("all", "docs"):
            _print("  Would generate documentation")
            results["docs"] = {"status": "dry-run"}

        _print("")

        self.state = StageState.COMPLETED
        return {"status": "dry-run", "scope": scope, "results": results}

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _load_design(self, project_dir: str) -> dict:
        """Load the design state from the design stage."""
        design_path = Path(project_dir) / ".prototype" / "state" / "design.json"
        if not design_path.exists():
            return {}

        with open(design_path, "r", encoding="utf-8") as f:
            return json.load(f)
