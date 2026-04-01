"""Design stage — requirements analysis and architecture generation.

This stage is RE-ENTRANT: customers can return to it multiple times
to provide additional context, change requirements, or refine the
architecture.

When ``interactive=True``, the stage enters a **refinement loop** after
architecture generation.  The user can review the proposed architecture,
provide feedback, and the architect agent will refine the design in
subsequent iterations — all without leaving the stage.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from knack.util import CLIError

from azext_prototype.agents.base import AgentCapability, AgentContext
from azext_prototype.agents.orchestrator import AgentOrchestrator
from azext_prototype.agents.registry import AgentRegistry
from azext_prototype.config import ProjectConfig
from azext_prototype.stages.base import BaseStage, StageGuard, StageState
from azext_prototype.stages.discovery import DiscoveryResult, DiscoverySession
from azext_prototype.stages.discovery_state import DiscoveryState
from azext_prototype.ui.console import Console
from azext_prototype.ui.console import console as default_console

logger = logging.getLogger(__name__)

_NEW_SECTION_RE = re.compile(
    r"\[NEW_SECTION:\s*(\{.*?\})\]",
    re.DOTALL,
)


def _format_section_elapsed(seconds: float) -> str:
    """Format elapsed seconds as ``12s`` or ``1m04s`` when >= 60."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{minutes}m{secs:02d}s"


def _extract_new_sections(content: str) -> list[dict]:
    """Parse ``[NEW_SECTION: {...}]`` markers from AI response content."""
    results = []
    for m in _NEW_SECTION_RE.finditer(content):
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict) and "name" in obj:
                obj.setdefault("context", "")
                results.append(obj)
        except (json.JSONDecodeError, TypeError):
            pass
    return results


class DesignStage(BaseStage):
    """Analyze requirements and generate architecture design.

    Workflow:
    1. Read artifacts (documents, specs, diagrams)
    2. Ask clarifying questions interactively
    3. Generate architecture documentation
    4. Store design decisions for the build stage

    Re-entrant: each invocation can add context or modify the design.
    """

    DESIGN_STATE_FILE = ".prototype/state/design.json"

    def __init__(self):
        super().__init__(
            name="design",
            description="Requirements analysis and architecture design",
            reentrant=True,
        )

    def get_guards(self) -> list[StageGuard]:
        return [
            StageGuard(
                name="project_initialized",
                description="Project must be initialized",
                check_fn=lambda: Path("prototype.yaml").is_file(),
                error_message="No prototype project found. Run 'az prototype init'.",
            ),
        ]

    def execute(
        self,
        agent_context: AgentContext,
        registry: AgentRegistry,
        **kwargs,
    ) -> dict:
        """Execute the design stage.

        The biz-analyst agent is ALWAYS engaged in an **iterative
        discovery conversation** — regardless of whether artifacts or
        context were provided.  Even when ``--context`` is supplied
        the agents will analyse what was given and ask follow-up
        questions before generating architecture.

        When ``interactive=True`` the stage enters a refinement loop
        after the first architecture pass, allowing the user to review
        the design and request changes iteratively.
        """
        from azext_prototype.debug_log import debug as _dbg

        artifacts_path = kwargs.get("artifacts")
        additional_context = kwargs.get("context", "")
        reset = kwargs.get("reset", False)
        interactive = kwargs.get("interactive", False)
        skip_discovery = kwargs.get("skip_discovery", False)
        # Accept injected I/O callables (for tests / TUI)
        input_fn = kwargs.get("input_fn")
        print_fn = kwargs.get("print_fn")
        status_fn = kwargs.get("status_fn")
        section_fn = kwargs.get("section_fn")
        response_fn = kwargs.get("response_fn")
        update_task_fn = kwargs.get("update_task_fn")

        _dbg(
            "DesignStage.execute",
            "Entry",
            artifacts=artifacts_path,
            context_len=len(additional_context),
            reset=reset,
            interactive=interactive,
            skip_discovery=skip_discovery,
        )

        self.state = StageState.IN_PROGRESS
        config = ProjectConfig(agent_context.project_dir)
        config.load()

        # Load or reset design state
        design_state = self._load_design_state(agent_context.project_dir, reset)

        # Use styled console unless test I/O is injected
        use_styled = print_fn is None
        ui = default_console if use_styled else None
        _print = print_fn or default_console.print

        if use_styled:
            default_console.print_header("Starting design session")
        else:
            _print("\n[bold bright_magenta]Starting design session[/bold bright_magenta]\n")

        # Load existing discovery state
        discovery_state = DiscoveryState(agent_context.project_dir)
        if reset:
            discovery_state.reset()
        elif discovery_state.exists:
            discovery_state.load()
            if ui:
                ui.print_info("Loaded existing discovery context from previous session.")
            else:
                _print("[bright_cyan]\u2192[/bright_cyan] Loaded existing discovery context from previous session.")

        # Determine if this is a context-only invocation
        # (--context provided but no --artifacts)
        context_only = bool(additional_context) and not artifacts_path

        # 1. Ingest artifacts if provided (with hash-based change detection)
        artifact_images: list[dict] = []
        current_artifact_hashes: dict[str, str] = {}
        if artifacts_path:
            # Compute content hashes for change detection
            current_artifact_hashes = self._compute_artifact_hashes(artifacts_path)
            stored_hashes = discovery_state.get_artifact_hashes()

            # Determine which files are new or changed
            new_files = {p for p in current_artifact_hashes if p not in stored_hashes}
            changed_files = {
                p for p, h in current_artifact_hashes.items() if p in stored_hashes and stored_hashes[p] != h
            }
            delta_files = new_files | changed_files

            if delta_files:
                # Show summary of what changed
                total = len(current_artifact_hashes)
                parts = []
                if new_files:
                    parts.append(f"{len(new_files)} new")
                if changed_files:
                    parts.append(f"{len(changed_files)} changed")
                summary = " and ".join(parts)
                if ui:
                    ui.print_info(f"{summary} artifact(s) of {total} total -- analyzing changes...")
                else:
                    _print(f"  {summary} artifact(s) of {total} total -- analyzing changes...")

                result = self._read_artifacts_with_progress(artifacts_path, ui, include_only=delta_files)
                artifact_content = result["content"]
                artifact_images = result.get("images", [])

                if result["read"]:
                    _print(f"  Read {len(result['read'])} file(s):")
                    if ui:
                        ui.print_file_list(result["read"], success=True)
                    else:
                        for name in result["read"]:
                            _print(f"    [bright_green]\u2713[/bright_green] [bright_cyan]{name}[/bright_cyan]")

                if artifact_images:
                    n_img = len(artifact_images)
                    _print(f"  [bright_cyan]\u2192[/bright_cyan] Extracted {n_img} image(s) for vision analysis")

                if result["failed"]:
                    _print(f"  Could not read {len(result['failed'])} file(s):")
                    if ui:
                        ui.print_file_list([f"{n}  ({r})" for n, r in result["failed"]], success=False)
                    else:
                        for name, reason in result["failed"]:
                            _print(f"    [bright_red]\u2717[/bright_red] {name}  ({reason})")

                if not result["read"] and not result["failed"]:
                    _print("  [dim](no files found)[/dim]")
                _print("")
            else:
                # No changes detected — skip reading
                if ui:
                    ui.print_info("No changes detected in artifacts -- skipping content analysis.")
                else:
                    _print("  No changes detected in artifacts -- skipping content analysis.")
                _print("")
                artifact_content = ""

            design_state["artifacts"].append(
                {
                    "path": artifacts_path,
                    "content_summary": artifact_content[:500] if artifact_content else "(unchanged)",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "delta_files": len(delta_files),
                }
            )
            if artifact_content:
                agent_context.add_artifact("requirements", artifact_content)

            # Persist inventory immediately so it survives cancelled/interrupted sessions
            discovery_state.update_artifact_inventory(current_artifact_hashes)
        else:
            artifact_content = ""

        # Context change detection
        if additional_context:
            ctx_hash = hashlib.sha256(additional_context.encode("utf-8")).hexdigest()
            if ctx_hash == discovery_state.get_context_hash():
                if ui:
                    ui.print_info("Context unchanged from previous session -- skipping.")
                else:
                    _print("  Context unchanged from previous session -- skipping.")
                additional_context = ""
            else:
                discovery_state.update_context_hash(ctx_hash)

        # 2. Discovery session
        if skip_discovery:
            # --skip-discovery: use existing discovery state directly
            if not discovery_state.exists:
                raise CLIError(
                    "No discovery state found. Run 'az prototype design' first "
                    "to complete discovery before using --skip-discovery."
                )
            # The richest context is in the last assistant message of the
            # conversation history — it contains the full requirements
            # summary the biz-analyst produced (with headings, scope, etc.).
            # The structured fields may be empty if the state was only
            # populated via conversation_history.  Fall back to
            # format_as_context() only when conversation history is absent.
            additional_context = (
                self._extract_last_summary(discovery_state)
                or discovery_state.format_as_context()
                or additional_context
                or ""
            )

            # Prompt the user — they can press Enter to proceed or type
            # additional context (e.g. scope changes since last discovery).
            _input = input_fn or (lambda p: input(p))
            if ui:
                ui.print_info("Discovery skipped. Press Enter to proceed, or keep typing.")
            else:
                _print("Discovery skipped. Press Enter to proceed, or keep typing.")
            try:
                extra = _input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                extra = ""
            if extra:
                additional_context = additional_context + "\n\n## Additional Context\n" + extra
            elif ui:
                ui.clear_last_line()

            discovery_result = DiscoveryResult(
                requirements=additional_context,
                conversation=[],
                policy_overrides=[],
                exchange_count=discovery_state.state.get("_metadata", {}).get("exchange_count", 0),
            )
        else:
            # Normal path: iterative discovery conversation
            #    The DiscoverySession analyses any supplied context/artifacts
            #    and asks targeted follow-up questions.  When no context is
            #    supplied it starts with a blank-slate discovery conversation.
            #
            #    If context_only=True and the context is clear, the agent may
            #    skip interactive conversation and proceed directly to design.
            discovery = DiscoverySession(
                agent_context,
                registry,
                console=ui,
                discovery_state=discovery_state,
            )
            discovery_result = discovery.run(
                seed_context=additional_context or "",
                artifacts=artifact_content,
                artifact_images=artifact_images,
                input_fn=input_fn,
                print_fn=print_fn,
                context_only=context_only,
                status_fn=status_fn,
                section_fn=section_fn,
                response_fn=response_fn,
                update_task_fn=update_task_fn,
            )

            if discovery_result.cancelled:
                self.state = StageState.FAILED
                return {"status": "cancelled"}

            # Use discovery output as the enriched context
            additional_context = discovery_result.requirements or additional_context or ""

        # Persist any policy overrides
        if discovery_result.policy_overrides:
            design_state["policy_overrides"] = (
                design_state.get("policy_overrides", []) + discovery_result.policy_overrides
            )

        # 3. Build the design prompt
        architect_agents = registry.find_by_capability(AgentCapability.ARCHITECT)
        if not architect_agents:
            raise CLIError("No architect agents available. Add one with 'az prototype agent add'.")

        primary_architect = architect_agents[0]

        # 4. Plan and generate architecture iteratively (per-section)
        sections = self._plan_architecture(
            ui,
            agent_context,
            primary_architect,
            config,
            additional_context,
            _print,
            status_fn=status_fn,
        )

        # Add architecture parent node and section children to the task tree
        if section_fn:
            section_fn([("Generate Architecture", 2)])
        if update_task_fn:
            update_task_fn("design-section-generate-architecture", "in_progress")
        if section_fn:
            section_fn([(s["name"], 3) for s in sections])

        design_output, _usage = self._generate_architecture_sections(
            ui,
            agent_context,
            primary_architect,
            config,
            sections,
            additional_context,
            _print,
            section_fn=section_fn,
            update_task_fn=update_task_fn,
            status_fn=status_fn,
        )

        if update_task_fn:
            update_task_fn("design-section-generate-architecture", "completed")

        # 5. Run supporting IaC review
        iac_tool = config.get("project.iac_tool", "terraform")
        if ui:
            with ui.spinner(f"Confirming {iac_tool} feasibility..."):
                self._run_iac_review(
                    agent_context,
                    registry,
                    config,
                    primary_architect,
                    design_output,
                )
        else:
            _print(f"Confirming {iac_tool} feasibility...")
            self._run_iac_review(
                agent_context,
                registry,
                config,
                primary_architect,
                design_output,
            )

        # 6. Parse and store design output
        design_state["architecture"] = design_output
        design_state["iteration"] = design_state.get("iteration", 0) + 1
        design_state["last_updated"] = datetime.now(timezone.utc).isoformat()

        # 7. Write architecture documentation
        self._write_architecture_docs(agent_context.project_dir, design_output)

        # 8. Save design state (snapshot after first generation)
        self._save_design_state(agent_context.project_dir, design_state)

        # 9. Persist discovery learnings for the build stage
        self._save_discovery_learnings(
            agent_context.project_dir,
            discovery_result,
            design_state,
            discovery_state=discovery_state,
        )

        # 10. Interactive refinement loop (if requested)
        if interactive:
            design_output = self._refine_architecture_loop(
                agent_context,
                primary_architect,
                design_state,
                config,
                print_fn=print_fn,
            )

        # 11. Update config
        config.set("stages.design.completed", True)
        config.set("stages.design.timestamp", datetime.now(timezone.utc).isoformat())
        config.set("stages.design.iterations", design_state["iteration"])

        self.state = StageState.COMPLETED

        _print("")
        if ui:
            ui.print_success(f"Design iteration {design_state['iteration']} complete.")
            ui.print_info("Architecture docs: [path]concept/docs/ARCHITECTURE.md[/path]")
            _print("")
            ui.print_dim("Next steps:")
            ui.print_dim("  az prototype design --context 'your changes'  # Refine")
            ui.print_dim("  az prototype analyze costs                    # Cost estimate")
            ui.print_dim("  az prototype build                            # Generate code")
        else:
            _print(
                f"[bold bright_green]\u2714[/bold bright_green] Design iteration {design_state['iteration']} complete."
            )
            _print(
                "[bright_cyan]\u2192[/bright_cyan] Architecture docs:"
                " [bright_cyan]concept/docs/ARCHITECTURE.md[/bright_cyan]"
            )
            _print("")
            _print("Architecture generated. Type 'continue' to begin build out.")

        return {
            "status": "success",
            "iteration": design_state["iteration"],
            "architecture": design_output[:200] + "...",
        }

    # ------------------------------------------------------------------
    # Architecture refinement loop
    # ------------------------------------------------------------------

    def _refine_architecture_loop(
        self,
        agent_context: AgentContext,
        architect,
        design_state: dict,
        config: ProjectConfig,
        print_fn=None,
    ) -> str:
        """Multi-turn loop that lets the user refine the architecture.

        After the initial architecture is generated the user is presented
        with the design and may provide feedback.  When feedback is given,
        the architect agent regenerates the architecture incorporating the
        requested changes.  The loop continues until the user types
        ``accept`` (or ``done``) or presses Enter without input.

        Parameters
        ----------
        agent_context:
            Current agent context.
        architect:
            The primary architect agent to use for refinement.
        design_state:
            Mutable design state dict (updated in-place).
        config:
            Project configuration (used to rebuild prompts).

        Returns
        -------
        str:
            The final (possibly refined) architecture content.
        """
        design_output = design_state["architecture"]

        _print = print_fn or print
        _print("\nReview the architecture (concept/docs/ARCHITECTURE.md).")
        _print("Provide feedback to refine, or press Enter to proceed.\n")

        while True:
            try:
                feedback = input("Feedback (or Enter to accept): ").strip()
            except (EOFError, KeyboardInterrupt):
                # Non-interactive environment or user cancelled
                break

            if not feedback or feedback.lower() in {"accept", "done", "ok", "lgtm"}:
                break

            design_state["decisions"] = design_state.get("decisions", [])
            design_state["decisions"].append(
                {
                    "feedback": feedback,
                    "iteration": design_state["iteration"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

            # Build a refinement prompt
            refinement_task = (
                f"## Current Architecture (Iteration {design_state['iteration']})\n"
                f"{design_output}\n\n"
                f"## User Feedback\n{feedback}\n\n"
                "Revise the architecture to address the feedback above. "
                "Keep all sections that were not affected and update only "
                "the parts that need to change. Output the FULL revised "
                "architecture document."
            )

            _print("\nRefining architecture...")
            response = architect.execute(agent_context, refinement_task)

            design_output = response.content
            design_state["architecture"] = design_output
            design_state["iteration"] = design_state.get("iteration", 0) + 1
            design_state["last_updated"] = datetime.now(timezone.utc).isoformat()

            # Persist incrementally so work isn't lost
            self._write_architecture_docs(agent_context.project_dir, design_output)
            self._save_design_state(agent_context.project_dir, design_state)

            _print(
                f"\nArchitecture updated (iteration {design_state['iteration']}). "
                "Review concept/docs/ARCHITECTURE.md.\n"
            )

        return design_output

    # ------------------------------------------------------------------
    # IaC feasibility review
    # ------------------------------------------------------------------

    def _run_iac_review(
        self,
        agent_context: AgentContext,
        registry: AgentRegistry,
        config: ProjectConfig,
        primary_architect,
        design_output: str,
    ) -> None:
        """Delegate IaC review to the terraform/bicep agent (if available).

        The IaC agent reviews the architecture design and validates
        infrastructure feasibility.  Results are stored as an artifact
        on *agent_context* for later stages.
        """
        iac_tool = config.get("project.iac_tool", "terraform")
        iac_capability = AgentCapability.TERRAFORM if iac_tool == "terraform" else AgentCapability.BICEP
        iac_agents = registry.find_by_capability(iac_capability)
        if not iac_agents:
            return

        orchestrator = AgentOrchestrator(registry, agent_context)
        iac_review = orchestrator.delegate(
            from_agent=primary_architect.name,
            to_agent_name=iac_agents[0].name,
            sub_task=(
                f"Review this architecture design and validate the "
                f"{iac_tool} feasibility. Suggest any corrections:\n\n"
                f"{design_output}"
            ),
        )
        if iac_review.content and "error" not in iac_review.content.lower()[:20]:
            agent_context.add_artifact("iac_review", iac_review.content)

    # ------------------------------------------------------------------
    # Iterative architecture generation
    # ------------------------------------------------------------------

    _DEFAULT_SECTIONS = [
        {"name": "Solution Overview", "context": "What this prototype demonstrates"},
        {"name": "Azure Services", "context": "Which services are needed and why"},
        {"name": "Architecture Diagram", "context": "Mermaid diagram of the system"},
        {"name": "Data Flow", "context": "How data moves through the system"},
        {"name": "Security", "context": "Authentication, authorization, managed identity"},
        {"name": "Service Configuration", "context": "SKUs, settings, networking"},
        {"name": "Application Components", "context": "Apps, APIs, functions to build"},
        {"name": "Deployment Stages", "context": "Order of IaC deployment"},
        {"name": "Future Considerations", "context": "Deferred items for later"},
    ]

    @staticmethod
    def _execute_with_prompt_trim(architect, agent_context, prompt, accumulated):
        """Execute architect with automatic prompt trimming on token limit errors.

        If the Copilot API rejects the prompt as too large, progressively
        shrink the "Architecture So Far" section by summarizing older
        sections as headings only, then retry.
        """
        from azext_prototype.ai.copilot_provider import CopilotPromptTooLargeError

        try:
            return architect.execute(agent_context, prompt)
        except CopilotPromptTooLargeError:
            pass

        # Retry with aggressively trimmed context — keep only headings
        # from ALL prior sections (no full content from any)
        if accumulated and "## Architecture So Far" in prompt:
            summaries = []
            for sec_text in accumulated:
                heading = next(
                    (ln for ln in sec_text.splitlines() if ln.startswith("## ")),
                    "",
                )
                summaries.append(heading + " *(content omitted — see prior output)*")
            trimmed_context = "## Architecture So Far\n" + "\n\n".join(summaries)

            # Replace the Architecture So Far section in the prompt
            before_arch = prompt.split("## Architecture So Far")[0]
            after_arch_parts = prompt.split("## Instructions")
            instructions = "## Instructions" + after_arch_parts[-1] if len(after_arch_parts) > 1 else ""
            trimmed_prompt = before_arch + trimmed_context + "\n\n" + instructions

            return architect.execute(agent_context, trimmed_prompt)

        # No accumulated context to trim — re-raise
        raise

    def _plan_architecture(
        self,
        ui: Console | None,
        agent_context: AgentContext,
        architect,
        config: ProjectConfig,
        additional_context: str,
        _print,
        status_fn=None,
    ) -> list[dict]:
        """Ask the architect for a section plan, return list of section dicts.

        Falls back to ``_DEFAULT_SECTIONS`` if the AI returns invalid JSON.
        """
        cfg = config.to_dict()
        name = cfg.get("project", {}).get("name", "unnamed")
        region = cfg.get("project", {}).get("location", "eastus")
        iac_tool = cfg.get("project", {}).get("iac_tool", "terraform")

        planning_prompt = (
            "## Task\n"
            "Analyze the requirements below and create a plan for the architecture document.\n"
            "Return a JSON array of sections to generate, in order.\n\n"
            "## Requirements\n"
            f"{additional_context}\n\n"
            "## Project\n"
            f"- Name: {name}, Region: {region}, IaC: {iac_tool}\n\n"
            "## Instructions\n"
            "Return ONLY a fenced JSON code block with this structure:\n"
            "```json\n"
            '[\n  {"name": "Solution Overview", "context": "What this prototype demonstrates"},\n'
            '  {"name": "Azure Services", "context": "Which services are needed and why"},\n'
            "  ...\n]\n"
            "```\n"
            "Include all sections needed for a complete architecture. Typical sections:\n"
            "Solution Overview, Azure Services, Architecture Diagram, Data Flow, Security,\n"
            "Service Configuration, Application Components, Deployment Stages, Future Considerations.\n"
            "You may add, remove, or reorder sections based on the specific requirements."
        )

        if ui:
            with ui.spinner("Planning architecture sections..."):
                response = architect.execute(agent_context, planning_prompt)
        else:
            _print("Planning architecture sections...")
            response = architect.execute(agent_context, planning_prompt)

        # Parse JSON from the response
        content = response.content or ""
        # Try fenced code block first
        fence_match = re.search(r"```(?:json)?\s*\n(.*?)```", content, re.DOTALL)
        json_str = fence_match.group(1).strip() if fence_match else content.strip()

        try:
            sections = json.loads(json_str)
            if isinstance(sections, list) and all(isinstance(s, dict) and "name" in s for s in sections):
                # Ensure each section has a context field
                for s in sections:
                    s.setdefault("context", "")
                logger.info("Architecture plan: %d sections", len(sections))
                return sections
        except (json.JSONDecodeError, TypeError):
            pass

        logger.warning("Failed to parse architecture plan, using defaults")
        return list(self._DEFAULT_SECTIONS)

    def _generate_architecture_sections(
        self,
        ui: Console | None,
        agent_context: AgentContext,
        architect,
        config: ProjectConfig,
        sections: list[dict],
        additional_context: str,
        _print,
        section_fn=None,
        update_task_fn=None,
        status_fn=None,
    ) -> tuple[str, dict]:
        """Generate each architecture section iteratively.

        Returns ``(full_markdown, merged_usage)`` where *merged_usage*
        accumulates token counts across all section calls.
        """
        cfg = config.to_dict()
        name = cfg.get("project", {}).get("name", "unnamed")
        region = cfg.get("project", {}).get("location", "eastus")
        iac_tool = cfg.get("project", {}).get("iac_tool", "terraform")

        plan_summary = "\n".join(f"- {s['name']}: {s.get('context', '')}" for s in sections)

        accumulated: list[str] = []
        merged_usage: dict[str, int] = {}

        # Start cumulative timer for the entire architecture generation
        if status_fn:
            status_fn("Generating architecture...", "start")

        idx = 0
        while idx < len(sections):
            section = sections[idx]
            section_name = section["name"]
            section_context = section.get("context", "")
            slug = re.sub(r"[^a-z0-9]+", "-", section_name.lower()).strip("-")
            task_id = f"design-section-{slug}"

            if update_task_fn:
                update_task_fn(task_id, "in_progress")

            prompt = (
                f"## Task\n"
                f'Generate the "{section_name}" section of the architecture document.\n\n'
                f"## Section Focus\n{section_context}\n\n"
                f"## Project Context\n"
                f"- Name: {name}, Region: {region}, IaC: {iac_tool}\n\n"
                f"## Requirements\n{additional_context}\n\n"
                f"## Architecture Plan\n{plan_summary}\n\n"
            )
            if accumulated:
                # Sliding window: keep the last 3 sections in full,
                # summarise older ones as headings only to avoid
                # exceeding the model's context window.
                _RECENT = 3
                if len(accumulated) <= _RECENT:
                    context_parts = list(accumulated)
                else:
                    older = accumulated[:-_RECENT]
                    recent = accumulated[-_RECENT:]
                    # Extract the first ## heading from each older section
                    summaries = []
                    for sec_text in older:
                        heading = next(
                            (ln for ln in sec_text.splitlines() if ln.startswith("## ")),
                            "",
                        )
                        summaries.append(heading + " *(see above — omitted for brevity)*")
                    context_parts = summaries + list(recent)
                prompt += "## Architecture So Far\n" + "\n\n".join(context_parts) + "\n\n"
            prompt += (
                f"## Instructions\n"
                f'Generate ONLY the "{section_name}" section. Use markdown with a ## heading.\n'
                f"Ensure consistency with the sections already generated above.\n"
                f"Do not repeat content from prior sections.\n"
                f"If while writing this section you determine an additional section is needed "
                f"that is not in the architecture plan, include a line at the very end:\n"
                f'[NEW_SECTION: {{"name": "Section Name", "context": "Brief description"}}]'
            )

            section_start = time.monotonic()

            spinner_msg = f"Generating architecture ({section_name})..."
            if ui and not status_fn:
                with ui.spinner(spinner_msg):
                    response = self._execute_with_prompt_trim(architect, agent_context, prompt, accumulated)
            else:
                _print(spinner_msg)
                response = self._execute_with_prompt_trim(architect, agent_context, prompt, accumulated)

            # Handle truncation for this section
            for _ in range(3):
                if response.finish_reason != "length":
                    break
                logger.info("Section '%s' truncated, requesting continuation", section_name)
                cont_task = (
                    "Your previous response was cut off. Continue EXACTLY where "
                    "you left off — do not repeat any content already generated. "
                    "Pick up mid-sentence if necessary."
                )
                cont = architect.execute(agent_context, cont_task)
                response = type(response)(
                    content=response.content + cont.content,
                    model=cont.model,
                    usage={
                        k: response.usage.get(k, 0) + cont.usage.get(k, 0)
                        for k in set(response.usage) | set(cont.usage)
                    },
                    finish_reason=cont.finish_reason,
                )

            section_elapsed = time.monotonic() - section_start
            elapsed_str = _format_section_elapsed(section_elapsed)
            _print(f"  {section_name}...Done. ({elapsed_str})")

            accumulated.append(response.content)

            # Merge usage
            for k, v in response.usage.items():
                merged_usage[k] = merged_usage.get(k, 0) + v

            if update_task_fn:
                update_task_fn(task_id, "completed")

            # Check for dynamically discovered sections
            new_sections = _extract_new_sections(response.content)
            for ns in new_sections:
                if not any(s["name"].lower() == ns["name"].lower() for s in sections):
                    sections.append(ns)
                    plan_summary += f"\n- {ns['name']}: {ns.get('context', '')}"
                    if section_fn:
                        section_fn([(ns["name"], 3)])

            idx += 1

        if status_fn:
            status_fn("Generating architecture...", "end")

        return "\n\n".join(accumulated), merged_usage

    # ------------------------------------------------------------------
    # Design task composition
    # ------------------------------------------------------------------
    # Artifact reading
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_file(file_path: Path) -> str:
        """Compute SHA256 of a file using chunked reading (Python 3.10 compat)."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _compute_artifact_hashes(path: str) -> dict[str, str]:
        """Walk an artifacts path and compute SHA256 for each file.

        Returns ``{absolute_path_str: sha256_hex}`` for every file found.
        """
        artifacts_path = Path(path)
        hashes: dict[str, str] = {}
        if artifacts_path.is_file():
            hashes[str(artifacts_path.resolve())] = DesignStage._hash_file(artifacts_path)
        elif artifacts_path.is_dir():
            for file_path in sorted(artifacts_path.rglob("*")):
                if file_path.is_file():
                    hashes[str(file_path.resolve())] = DesignStage._hash_file(file_path)
        return hashes

    def _read_artifacts_with_progress(
        self, path: str, console: Console | None, include_only: set[str] | None = None
    ) -> dict:
        """Read artifacts with progress indicator.

        When console is provided, shows a progress bar during file reading.
        Falls back to _read_artifacts when no console is available.
        """
        from azext_prototype.parsers.binary_reader import (
            MAX_IMAGES_PER_DIR,
            FileCategory,
        )

        artifacts_dir = Path(path)
        if not artifacts_dir.exists():
            raise CLIError(f"Artifacts path not found: {path}")

        if console:
            console.print_info(f"Reading artifacts from: [path]{path}[/path]")

        # If it's a single file, just read it
        if artifacts_dir.is_file():
            if include_only is not None and str(artifacts_dir.resolve()) not in include_only:
                return {"content": "", "images": [], "read": [], "failed": []}
            return self._read_artifacts(path)

        # Count files first for progress
        files = [f for f in sorted(artifacts_dir.rglob("*")) if f.is_file()]
        if include_only is not None:
            files = [f for f in files if str(f.resolve()) in include_only]

        if not files:
            if console:
                console.print_dim("  (no files found)")
            return {"content": "", "images": [], "read": [], "failed": []}

        content_parts: list[str] = []
        read_files: list[str] = []
        failed_files: list[tuple[str, str]] = []
        images: list[dict] = []
        image_count = 0

        def _process_result(rel, result):
            nonlocal image_count
            if result.error:
                failed_files.append((rel, result.error))
            elif result.category == FileCategory.IMAGE:
                if image_count >= MAX_IMAGES_PER_DIR:
                    failed_files.append((rel, f"Image limit reached ({MAX_IMAGES_PER_DIR})"))
                else:
                    images.append({"filename": result.filename, "data": result.image_data, "mime": result.mime_type})
                    read_files.append(rel)
                    image_count += 1
            else:
                # TEXT or DOCUMENT — both have result.text
                content_parts.append(f"\n--- {rel} ---\n")
                content_parts.append(result.text)
                read_files.append(rel)
                # Collect embedded images from documents
                for emb in result.embedded_images:
                    if image_count >= MAX_IMAGES_PER_DIR:
                        break
                    images.append({"filename": emb.source, "data": emb.data, "mime": emb.mime_type})
                    image_count += 1

        if console:
            with console.progress_files(f"Processing {len(files)} file(s)") as progress:
                task = progress.add_task("Reading...", total=len(files))
                for file_path in files:
                    rel = str(file_path.relative_to(artifacts_dir))
                    progress.update(task, description=f"Reading {rel[:30]}...")
                    result = self._read_file(file_path)
                    _process_result(rel, result)
                    progress.advance(task)
        else:
            for file_path in files:
                rel = str(file_path.relative_to(artifacts_dir))
                result = self._read_file(file_path)
                _process_result(rel, result)

        return {
            "content": "\n".join(content_parts),
            "images": images,
            "read": read_files,
            "failed": failed_files,
        }

    def _read_artifacts(self, path: str, include_only: set[str] | None = None) -> dict:
        """Read files from an artifacts directory.

        When *include_only* is ``None`` every file is read.  Otherwise
        only files whose ``str(resolve())`` is in the set are processed.

        Returns a dict with keys:
            ``content``  – concatenated text of all successfully-read files
            ``images``   – list of image dicts for vision API
            ``read``     – list of relative paths that were read
            ``failed``   – list of ``(relative_path, reason)`` tuples
        """
        from azext_prototype.parsers.binary_reader import (
            MAX_IMAGES_PER_DIR,
            FileCategory,
        )

        artifacts_dir = Path(path)
        if not artifacts_dir.exists():
            raise CLIError(f"Artifacts path not found: {path}")

        content_parts: list[str] = []
        read_files: list[str] = []
        failed_files: list[tuple[str, str]] = []
        images: list[dict] = []
        image_count = 0

        def _process(rel, result):
            nonlocal image_count
            if result.error:
                failed_files.append((rel, result.error))
            elif result.category == FileCategory.IMAGE:
                if image_count >= MAX_IMAGES_PER_DIR:
                    failed_files.append((rel, f"Image limit reached ({MAX_IMAGES_PER_DIR})"))
                else:
                    images.append({"filename": result.filename, "data": result.image_data, "mime": result.mime_type})
                    read_files.append(rel)
                    image_count += 1
            else:
                content_parts.append(f"\n--- {rel} ---\n" if not artifacts_dir.is_file() else "")
                content_parts.append(result.text)
                read_files.append(rel)
                for emb in result.embedded_images:
                    if image_count >= MAX_IMAGES_PER_DIR:
                        break
                    images.append({"filename": emb.source, "data": emb.data, "mime": emb.mime_type})
                    image_count += 1

        if artifacts_dir.is_file():
            if include_only is not None and str(artifacts_dir.resolve()) not in include_only:
                pass  # filtered out
            else:
                result = self._read_file(artifacts_dir)
                _process(artifacts_dir.name, result)
        else:
            for file_path in sorted(artifacts_dir.rglob("*")):
                if not file_path.is_file():
                    continue
                if include_only is not None and str(file_path.resolve()) not in include_only:
                    continue
                rel = str(file_path.relative_to(artifacts_dir))
                result = self._read_file(file_path)
                _process(rel, result)

        if not content_parts and not images:
            logger.warning("No readable artifacts found in %s", path)

        return {
            "content": "\n".join(content_parts),
            "images": images,
            "read": read_files,
            "failed": failed_files,
        }

    def _read_file(self, path: Path):
        """Read a single file, dispatching based on file type.

        Returns a ``ReadResult`` from the binary reader module.
        """
        from azext_prototype.parsers.binary_reader import read_file

        return read_file(path)

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_last_summary(discovery_state: DiscoveryState) -> str:
        """Extract the requirements summary from conversation history.

        Delegates to :meth:`DiscoveryState.extract_conversation_summary`.
        """
        return discovery_state.extract_conversation_summary()

    def _load_design_state(self, project_dir: str, reset: bool = False) -> dict:
        """Load persisted design state."""
        state_path = Path(project_dir) / self.DESIGN_STATE_FILE

        if reset or not state_path.exists():
            return {
                "iteration": 0,
                "artifacts": [],
                "architecture": None,
                "decisions": [],
                "last_updated": None,
            }

        try:
            with open(state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {"iteration": 0, "artifacts": [], "architecture": None, "decisions": [], "last_updated": None}

    def _save_design_state(self, project_dir: str, state: dict):
        """Persist design state."""
        state_path = Path(project_dir) / self.DESIGN_STATE_FILE
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    # ------------------------------------------------------------------
    # Discovery learnings persistence
    # ------------------------------------------------------------------

    def _save_discovery_learnings(
        self,
        project_dir: str,
        discovery_result: DiscoveryResult,
        design_state: dict,
        discovery_state: DiscoveryState | None = None,
    ) -> None:
        """Persist discovery learnings to YAML for the build stage.

        Merges the final requirements summary into the existing discovery
        state (which has been incrementally updated during the conversation).
        This ensures all learnings are captured including conversation history.

        The file is consumed by the build stage to provide context
        for code generation.
        """
        # Use provided state or load existing
        state = discovery_state or DiscoveryState(project_dir)
        if not discovery_state:
            state.load()

        # Parse the requirements summary to extract structured data
        learnings = self._parse_requirements_to_learnings(
            discovery_result.requirements,
            discovery_result.conversation,
            design_state,
        )

        # Merge final learnings into the discovery state
        state.merge_learnings(learnings)

        # Update metadata with final iteration info
        state.state["_metadata"]["exchange_count"] = discovery_result.exchange_count
        state.state["_metadata"]["iteration"] = design_state.get("iteration", 1)
        state.save()

        logger.info("Discovery learnings saved to %s", state._path)

    # Heading map: (regex, learnings key path)
    _HEADING_MAP = [
        (r"^##\s+Project\s+Summary\s*$", ("project", "summary")),
        (r"^##\s+Goals\s*$", ("project", "goals")),
        (r"^##\s+Confirmed\s+Functional\s+Requirements\s*$", ("requirements", "functional")),
        (r"^##\s+Confirmed\s+Non[- ]?Functional\s+Requirements\s*$", ("requirements", "non_functional")),
        (r"^##\s+Constraints\s*$", ("constraints",)),
        (r"^##\s+Decisions\s*$", ("decisions",)),
        (r"^##\s+Open\s+Items\s*$", ("open_items",)),
        (r"^##\s+Risks\s*$", ("risks",)),
        (r"^###\s+In\s+Scope\s*$", ("scope", "in_scope")),
        (r"^###\s+Out\s+of\s+Scope\s*$", ("scope", "out_of_scope")),
        (r"^###?\s+Deferred\s*/?\s*Future\s+Work\s*$", ("scope", "deferred")),
        (r"^##\s+Azure\s+Services\s*$", ("architecture", "services")),
        (r"^##\s+Policy\s+Overrides?\s*$", ("_policy_overrides",)),
    ]

    def _parse_requirements_to_learnings(
        self,
        requirements: str,
        conversation: list,
        design_state: dict,
    ) -> dict:
        """Parse requirements summary into structured learnings.

        The summary uses exact markdown headings that we control via the
        biz-analyst prompt.  We split on those headings and extract
        bullet items from each section.
        """
        learnings: dict = {
            "project": {
                "summary": "",
                "goals": [],
            },
            "requirements": {
                "functional": [],
                "non_functional": [],
            },
            "constraints": [],
            "decisions": [],
            "open_items": [],
            "risks": [],
            "scope": {
                "in_scope": [],
                "out_of_scope": [],
                "deferred": [],
            },
            "architecture": {
                "services": [],
                "integrations": [],
                "data_flow": "",
            },
        }

        if not requirements:
            return learnings

        # Split into sections by heading
        sections: dict[tuple, str] = {}
        current_key: tuple | None = None
        current_lines: list[str] = []

        for line in requirements.split("\n"):
            stripped = line.strip()
            matched = False
            for pattern, key in self._HEADING_MAP:
                if re.match(pattern, stripped, re.IGNORECASE):
                    if current_key is not None:
                        sections[current_key] = "\n".join(current_lines)
                    current_key = key
                    current_lines = []
                    matched = True
                    break
            if not matched:
                current_lines.append(line)

        # Save the last section
        if current_key is not None:
            sections[current_key] = "\n".join(current_lines)

        # Populate learnings from sections
        for key, content in sections.items():
            if len(key) == 1:
                # Top-level list: constraints, decisions, open_items, risks
                if key[0] not in learnings:
                    continue  # Skip boundary-only headings (e.g. Policy Overrides)
                items = self._extract_list_items(content)
                if items:
                    learnings[key[0]] = items
            elif len(key) == 2:
                parent, child = key
                if child == "summary":
                    learnings[parent][child] = content.strip()
                else:
                    items = self._extract_list_items(content)
                    if items:
                        learnings[parent][child] = items

        # Add decisions from design state
        if design_state.get("decisions"):
            for decision in design_state["decisions"]:
                if decision.get("feedback"):
                    learnings["decisions"].append(decision["feedback"])

        # Add policy overrides as constraints
        if design_state.get("policy_overrides"):
            for override in design_state["policy_overrides"]:
                learnings["constraints"].append(
                    f"Policy override: {override.get('policy_name', 'unknown')} - " f"{override.get('description', '')}"
                )

        return learnings

    def _extract_list_items(self, content: str) -> list[str]:
        """Extract list items from markdown-style content."""
        items = []
        for line in content.split("\n"):
            line = line.strip()
            # Match bullet points, numbered lists, or plain items
            if line.startswith(("-", "*", "•")):
                item = line.lstrip("-*• ").strip()
                if item:
                    items.append(item)
            elif re.match(r"^\d+[\.\)]\s+", line):
                item = re.sub(r"^\d+[\.\)]\s+", "", line).strip()
                if item:
                    items.append(item)
        return items

    def _write_architecture_docs(self, project_dir: str, content: str):
        """Write architecture documentation."""
        docs_dir = Path(project_dir) / "concept" / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)

        arch_path = docs_dir / "ARCHITECTURE.md"
        arch_path.write_text(content, encoding="utf-8")
        logger.info("Architecture documentation written to %s", arch_path)
