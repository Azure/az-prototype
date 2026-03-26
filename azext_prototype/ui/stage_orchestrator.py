"""Stage orchestrator — detects project state and manages stage transitions.

Runs on a Textual worker thread.  Reads ``.prototype/state/`` files to
determine the current position in the pipeline, populates the task tree
with sub-tasks from completed stages, and waits for user commands.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any

from azext_prototype.ui.task_model import TaskStatus
from azext_prototype.ui.tui_adapter import ShutdownRequested

if TYPE_CHECKING:
    from azext_prototype.ui.app import PrototypeApp
    from azext_prototype.ui.tui_adapter import TUIAdapter

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------- #
# State detection
# -------------------------------------------------------------------- #


def detect_stage(project_dir: str) -> str:
    """Detect the furthest completed stage from state files.

    Returns one of: ``"init"``, ``"design"``, ``"build"``, ``"deploy"``.
    """
    state_dir = Path(project_dir) / ".prototype" / "state"
    if (state_dir / "deploy.yaml").exists():
        return "deploy"
    if (state_dir / "build.yaml").exists():
        return "build"
    if (state_dir / "discovery.yaml").exists() or (state_dir / "design.json").exists():
        return "design"
    return "init"


# -------------------------------------------------------------------- #
# Orchestrator
# -------------------------------------------------------------------- #


class StageOrchestrator:
    """Manages stage lifecycle within the dashboard.

    Call :meth:`run` from a Textual worker thread.  It will detect the
    current project state, populate the task tree, show project metadata,
    and wait for user commands.
    """

    def __init__(
        self,
        app: PrototypeApp,
        adapter: TUIAdapter,
        project_dir: str,
        stage_kwargs: dict | None = None,
    ) -> None:
        self._app = app
        self._adapter = adapter
        self._project_dir = project_dir
        self._stage_kwargs = stage_kwargs or {}

    # ------------------------------------------------------------------ #
    # Public
    # ------------------------------------------------------------------ #

    def run(self, start_stage: str | None = None) -> None:
        """Main orchestration loop — detect state, populate tree, prompt."""
        try:
            detected = detect_stage(self._project_dir)
            current = start_stage or detected

            # Guard: prevent skipping stages.  The user may only target the
            # next stage after the furthest completed one (or re-run a
            # completed stage).
            stage_order = ["init", "design", "build", "deploy"]
            detected_idx = stage_order.index(detected) if detected in stage_order else 0
            target_idx = stage_order.index(current) if current in stage_order else 0
            next_allowed_idx = detected_idx + 1

            if target_idx > next_allowed_idx:
                pf = self._adapter.print_fn
                skipped = stage_order[next_allowed_idx:target_idx]
                skipped_names = ", ".join(s.title() for s in skipped)
                pf(
                    f"[bright_red]![/bright_red] Cannot skip to "
                    f"[bold]{current}[/bold] — {skipped_names} "
                    f"{'has' if len(skipped) == 1 else 'have'} not been completed yet."
                )
                # Fall back to the next valid stage
                current = stage_order[next_allowed_idx]
                pf(f"  Resuming at [bold]{current}[/bold] stage.\n")

            # Always mark init as completed
            self._adapter.update_task("init", TaskStatus.COMPLETED)

            # Populate tree from *detected* state (what's actually completed),
            # then mark the target stage as in-progress if it hasn't run yet.
            self._populate_from_state(detected)
            if current != detected:
                self._adapter.update_task(current, TaskStatus.IN_PROGRESS)

            # When re-running an earlier stage, mark all downstream stages
            # as pending — they depend on the output of the current stage
            # and will need to be re-run after it changes.
            if target_idx < detected_idx:
                for i in range(target_idx + 1, len(stage_order)):
                    self._adapter.update_task(stage_order[i], TaskStatus.PENDING)

            self._show_welcome(current)

            # Auto-run a stage when launched with stage_kwargs
            if self._stage_kwargs and start_stage:
                # Mark the target stage as in-progress before execution
                # (overrides the COMPLETED status set by _populate_from_state)
                self._adapter.update_task(start_stage, TaskStatus.IN_PROGRESS)
                if start_stage == "design":
                    self._run_design(**self._stage_kwargs)
                elif start_stage == "build":
                    self._run_build(**self._stage_kwargs)
                elif start_stage == "deploy":
                    self._run_deploy(**self._stage_kwargs)

            # Enter the command loop
            self._command_loop(current)
        except ShutdownRequested:
            logger.debug("Orchestrator received shutdown signal")

    # ------------------------------------------------------------------ #
    # Welcome + metadata
    # ------------------------------------------------------------------ #

    def _show_welcome(self, current_stage: str) -> None:
        """Display project metadata in the console view."""
        pf = self._adapter.print_fn

        # Load config for project metadata
        try:
            from azext_prototype.config import ProjectConfig

            config = ProjectConfig(self._project_dir)
            config.load()

            name = config.get("project.name", "")
            location = config.get("project.location", "")
            iac = config.get("project.iac_tool", "")
            ai_provider = config.get("ai.provider", "")
            model = config.get("ai.model", "")

            if name:
                pf(f"  Project:    {name}")
            summary = self._get_project_summary()
            if summary:
                prefix = "  Summary:    "
                indent = " " * len(prefix)
                wrapped = textwrap.fill(
                    summary,
                    width=80,
                    initial_indent=prefix,
                    subsequent_indent=indent,
                )
                for line in wrapped.splitlines():
                    pf(line)
            if location:
                pf(f"  Location:   {location}")
            if iac:
                pf(f"  IaC tool:   {iac}")
            if ai_provider:
                provider_str = ai_provider
                if model:
                    provider_str += f" ({model})"
                pf(f"  AI:         {provider_str}")

            pf(f"  Stage:      {current_stage}")
            pf("")
        except Exception:
            pf(f"  Stage: {current_stage}")
            pf("")

    def _get_project_summary(self) -> str:
        """Extract a project summary from discovery state or design output.

        Returns empty string if no summary is available.
        """
        import re

        def _normalize(text: str) -> str:
            """Collapse multiple spaces into one."""
            return re.sub(r"  +", " ", text).strip()

        # Try discovery state first
        try:
            from azext_prototype.stages.discovery_state import DiscoveryState

            ds = DiscoveryState(self._project_dir)
            if ds.exists:
                ds.load()
                summary = ds.state.get("project", {}).get("summary", "")
                if summary:
                    return _normalize(summary)
        except Exception:
            pass

        # Fall back to design.json architecture text
        try:
            import json

            design_json = Path(self._project_dir) / ".prototype" / "state" / "design.json"
            if design_json.exists():
                data = json.loads(design_json.read_text(encoding="utf-8"))
                arch = data.get("architecture", "")
                if arch:
                    # First sentence
                    first_sentence = arch.split(".")[0].strip()
                    if first_sentence:
                        return _normalize(first_sentence + ".")
        except Exception:
            pass

        return ""

    # ------------------------------------------------------------------ #
    # State → task tree population
    # ------------------------------------------------------------------ #

    def _populate_from_state(self, current_stage: str) -> None:
        """Read state files and populate the task tree with sub-tasks."""
        stage_order = ["init", "design", "build", "deploy"]
        current_idx = stage_order.index(current_stage) if current_stage in stage_order else 0

        # Mark all stages up to current as completed
        for i, stage_name in enumerate(stage_order):
            if i == 0:
                continue  # init already marked
            if i <= current_idx:
                self._adapter.update_task(stage_name, TaskStatus.COMPLETED)

        # Populate sub-tasks from state files
        self._populate_design_subtasks()
        self._populate_build_subtasks()
        self._populate_deploy_subtasks()

    def _populate_design_subtasks(self) -> None:
        """Populate design sub-tasks from discovery state."""
        try:
            from azext_prototype.stages.discovery_state import DiscoveryState

            ds = DiscoveryState(self._project_dir)
            if not ds.exists:
                return
            ds.load()

            confirmed = ds.confirmed_count
            open_count = ds.open_count

            if confirmed > 0:
                self._adapter.add_task(
                    "design",
                    "design-confirmed",
                    f"Confirmed requirements ({confirmed})",
                )
                self._adapter.update_task("design-confirmed", TaskStatus.COMPLETED)

            if open_count > 0:
                self._adapter.add_task(
                    "design",
                    "design-open",
                    f"Open items ({open_count})",
                )
                # Open items are pending resolution
                self._adapter.update_task("design-open", TaskStatus.PENDING)

            # Check for architecture output
            design_json = Path(self._project_dir) / ".prototype" / "state" / "design.json"
            if design_json.exists():
                self._adapter.add_task(
                    "design",
                    "design-arch",
                    "Architecture document",
                )
                self._adapter.update_task("design-arch", TaskStatus.COMPLETED)
        except Exception:
            logger.debug("Could not populate design subtasks", exc_info=True)

    def _populate_build_subtasks(self) -> None:
        """Populate build sub-tasks from build state."""
        try:
            from azext_prototype.stages.build_state import BuildState

            bs = BuildState(self._project_dir)
            if not bs.exists:
                return
            bs.load()

            stages = bs.state.get("deployment_stages", [])
            for s in stages:
                stage_num = s.get("stage", 0)
                name = s.get("name", f"Stage {stage_num}")
                status = s.get("status", "pending")
                task_id = f"build-stage-{stage_num}"

                self._adapter.add_task("build", task_id, f"Stage {stage_num}: {name}")

                if status in ("generated", "accepted"):
                    self._adapter.update_task(task_id, TaskStatus.COMPLETED)
                elif status == "in_progress":
                    self._adapter.update_task(task_id, TaskStatus.IN_PROGRESS)
                # else: stays PENDING
        except Exception:
            logger.debug("Could not populate build subtasks", exc_info=True)

    def _populate_deploy_subtasks(self) -> None:
        """Populate deploy sub-tasks from deploy state."""
        try:
            from azext_prototype.stages.deploy_state import DeployState

            ds = DeployState(self._project_dir)
            if not ds.exists:
                return
            ds.load()

            stages = ds.state.get("deployment_stages", [])
            for s in stages:
                stage_num = s.get("stage", 0)
                name = s.get("name", f"Stage {stage_num}")
                deploy_status = s.get("deploy_status", "pending")
                task_id = f"deploy-stage-{stage_num}"

                self._adapter.add_task("deploy", task_id, f"Stage {stage_num}: {name}")

                if deploy_status == "deployed":
                    self._adapter.update_task(task_id, TaskStatus.COMPLETED)
                elif deploy_status in ("deploying", "in_progress", "remediating"):
                    self._adapter.update_task(task_id, TaskStatus.IN_PROGRESS)
                elif deploy_status in ("failed", "rolled_back"):
                    self._adapter.update_task(task_id, TaskStatus.FAILED)
                # else: stays PENDING
        except Exception:
            logger.debug("Could not populate deploy subtasks", exc_info=True)

    # ------------------------------------------------------------------ #
    # Command loop
    # ------------------------------------------------------------------ #

    def _command_loop(self, current_stage: str) -> None:
        """Wait for user commands: design, build, deploy, quit.

        Raises :class:`ShutdownRequested` if the app is shutting down,
        which is caught by :meth:`run`.
        """
        pf = self._adapter.print_fn

        while True:
            user_input = self._adapter.input_fn("> ").strip().lower()

            if not user_input:
                continue

            if user_input in ("q", "quit", "exit", "end"):
                self._app.call_from_thread(self._app.exit)
                break
            elif user_input in ("design", "redesign"):
                self._run_design()
            elif user_input in ("build", "continue"):
                self._run_build()
            elif user_input in ("deploy", "redeploy"):
                self._run_deploy()
            elif user_input == "help":
                pf("")
                pf("Commands:")
                pf("  design   - Run or re-run the design stage")
                pf("  build    - Run or re-run the build stage")
                pf("  deploy   - Run or re-run the deploy stage")
                pf("  quit     - Exit")
                pf("")
            else:
                pf(f"Unknown command: {user_input}. Type 'help' for options.")

    # ------------------------------------------------------------------ #
    # Stage runners
    # ------------------------------------------------------------------ #

    def _run_design(self, **kwargs) -> None:
        """Launch the design (discovery + architecture) session."""
        self._adapter.clear_tasks("design")
        self._adapter.update_task("design", TaskStatus.IN_PROGRESS)
        # Show an initial subtask so the tree isn't empty during discovery
        self._adapter.add_task("design", "design-discovery", "Discovery")
        self._adapter.update_task("design-discovery", TaskStatus.IN_PROGRESS)

        try:
            _, config, registry, agent_context = self._prepare()
            from azext_prototype.stages.design_stage import DesignStage

            stage = DesignStage()
            result = stage.execute(
                agent_context,
                registry,
                input_fn=self._adapter.input_fn,
                print_fn=self._adapter.print_fn,
                status_fn=self._adapter.status_fn,
                section_fn=self._adapter.section_fn,
                response_fn=self._adapter.response_fn,
                update_task_fn=lambda tid, status: self._adapter.update_task(tid, TaskStatus(status)),
                **kwargs,
            )
            if result.get("status") == "cancelled":
                self._adapter.print_fn("[bright_yellow]![/bright_yellow] Design session cancelled.")
                self._app.call_from_thread(self._app.exit)
                raise ShutdownRequested()
            self._adapter.update_task("design", TaskStatus.COMPLETED)
            self._populate_design_subtasks()
        except ShutdownRequested:
            raise
        except Exception as exc:
            logger.exception("Design stage failed")
            self._adapter.update_task("design", TaskStatus.FAILED)
            self._adapter.print_fn(f"Design stage failed: {exc}")

    def _run_build(self, **kwargs) -> None:
        """Launch the build session."""
        self._adapter.clear_tasks("build")
        self._adapter.update_task("build", TaskStatus.IN_PROGRESS)

        try:
            _, config, registry, agent_context = self._prepare()
            from azext_prototype.stages.build_stage import BuildStage

            stage = BuildStage()
            stage.execute(
                agent_context,
                registry,
                input_fn=self._adapter.input_fn,
                print_fn=self._adapter.print_fn,
                status_fn=self._adapter.status_fn,
                **kwargs,
            )
            self._adapter.update_task("build", TaskStatus.COMPLETED)
            self._populate_build_subtasks()
        except ShutdownRequested:
            raise
        except Exception as exc:
            logger.exception("Build stage failed")
            self._adapter.update_task("build", TaskStatus.FAILED)
            self._adapter.print_fn(f"Build stage failed: {exc}")

    def _run_deploy(self, **kwargs) -> None:
        """Launch the deploy session."""
        self._adapter.clear_tasks("deploy")
        self._adapter.update_task("deploy", TaskStatus.IN_PROGRESS)

        try:
            _, config, registry, agent_context = self._prepare()
            from azext_prototype.stages.deploy_stage import DeployStage

            stage = DeployStage()
            stage.execute(
                agent_context,
                registry,
                input_fn=self._adapter.input_fn,
                print_fn=self._adapter.print_fn,
                status_fn=self._adapter.status_fn,
                **kwargs,
            )
            self._adapter.update_task("deploy", TaskStatus.COMPLETED)
            self._populate_deploy_subtasks()
        except ShutdownRequested:
            raise
        except Exception as exc:
            logger.exception("Deploy stage failed")
            self._adapter.update_task("deploy", TaskStatus.FAILED)
            self._adapter.print_fn(f"Deploy stage failed: {exc}")

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _prepare(self) -> tuple[Any, Any, Any, Any]:
        """Load config, registry, and agent context.

        Lazy import to avoid circular dependencies and keep the UI module
        lightweight.  Returns ``(project_dir, config, registry, agent_context)``.
        """
        from azext_prototype.custom import _prepare_command

        return _prepare_command(self._project_dir)
