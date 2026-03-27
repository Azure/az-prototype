"""Interactive build session — Claude Code-inspired conversational build.

Follows the :class:`~.discovery.DiscoverySession` pattern: bordered prompts,
progress indicators, slash commands, and a review loop.  The build session
orchestrates staged code generation through specialised agents and enforces
governance policies interactively.

Phases:

1. **Template detection** — Match workload templates (optional starting points)
2. **Deployment plan** — Derive fine-grained, dependency-ordered stages from
   the design architecture (via the cloud-architect agent)
3. **Staged generation** — Generate code per stage using the appropriate agent,
   with policy checking and interactive resolution after each stage
4. **QA review** — Cross-cutting review of all generated code
5. **Build report** — Structured summary of what was built
6. **Review loop** — User feedback drives regeneration of specific stages
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from azext_prototype.agents.base import AgentCapability, AgentContext
from azext_prototype.agents.governance import GovernanceContext
from azext_prototype.agents.orchestrator import AgentOrchestrator
from azext_prototype.agents.registry import AgentRegistry
from azext_prototype.ai.token_tracker import TokenTracker
from azext_prototype.config import ProjectConfig
from azext_prototype.naming import create_naming_strategy
from azext_prototype.parsers.file_extractor import parse_file_blocks, write_parsed_files
from azext_prototype.stages.build_state import BuildState
from azext_prototype.stages.escalation import EscalationTracker
from azext_prototype.stages.intent import (
    IntentKind,
    build_build_classifier,
    read_files_for_session,
)
from azext_prototype.stages.policy_resolver import PolicyResolver
from azext_prototype.stages.qa_router import route_error_to_qa
from azext_prototype.ui.console import Console, DiscoveryPrompt
from azext_prototype.ui.console import console as default_console

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------- #
# Sentinels
# -------------------------------------------------------------------- #

_QUIT_WORDS = frozenset({"q", "quit", "exit"})
_DONE_WORDS = frozenset({"done", "finish", "accept", "lgtm"})
_SLASH_COMMANDS = frozenset({"/status", "/stages", "/files", "/policy", "/describe", "/help"})

# Maximum remediation cycles per stage before proceeding
_MAX_STAGE_REMEDIATION_ATTEMPTS = 3


# -------------------------------------------------------------------- #
# BuildResult — public interface consumed by BuildStage
# -------------------------------------------------------------------- #


class BuildResult:
    """Result of a build session."""

    __slots__ = (
        "files_generated",
        "deployment_stages",
        "policy_overrides",
        "resources",
        "review_accepted",
        "cancelled",
    )

    def __init__(
        self,
        files_generated: list[str] | None = None,
        deployment_stages: list[dict] | None = None,
        policy_overrides: list[dict] | None = None,
        resources: list[dict[str, str]] | None = None,
        review_accepted: bool = False,
        cancelled: bool = False,
    ) -> None:
        self.files_generated = files_generated or []
        self.deployment_stages = deployment_stages or []
        self.policy_overrides = policy_overrides or []
        self.resources = resources or []
        self.review_accepted = review_accepted
        self.cancelled = cancelled


# -------------------------------------------------------------------- #
# BuildSession
# -------------------------------------------------------------------- #


class BuildSession:
    """Interactive, multi-phase build conversation.

    Manages the full build lifecycle: deployment plan derivation, staged
    code generation with policy enforcement, QA review, build report,
    and a conversational review loop.

    Reuses :class:`~azext_prototype.ui.console.DiscoveryPrompt` for
    bordered input with multi-line support.

    Parameters
    ----------
    agent_context:
        Runtime context with AI provider and project config.
    registry:
        Agent registry for resolving specialised agents.
    console:
        Styled console for output.
    build_state:
        Pre-initialised build state (for re-entrant builds).
    """

    def __init__(
        self,
        agent_context: AgentContext,
        registry: AgentRegistry,
        *,
        console: Console | None = None,
        build_state: BuildState | None = None,
        auto_accept: bool = False,
        status_fn: Any = None,
        section_fn: Any = None,
        update_task_fn: Any = None,
    ) -> None:
        self._context = agent_context
        self._registry = registry
        self._console = console or default_console
        self._status_fn = status_fn
        self._section_fn = section_fn
        self._update_task_fn = update_task_fn
        self._prompt = DiscoveryPrompt(self._console)
        self._build_state = build_state or BuildState(agent_context.project_dir)

        # Policy resolver
        self._governance = GovernanceContext()
        self._auto_accept = auto_accept
        self._policy_resolver = PolicyResolver(
            console=self._console,
            governance_context=self._governance,
            auto_accept=auto_accept,
        )

        # Resolve agents by capability
        self._iac_agents: dict[str, Any] = {}
        for cap, key in [(AgentCapability.TERRAFORM, "terraform"), (AgentCapability.BICEP, "bicep")]:
            agents = registry.find_by_capability(cap)
            if agents:
                self._iac_agents[key] = agents[0]

        dev_agents = registry.find_by_capability(AgentCapability.DEVELOP)
        self._dev_agent = dev_agents[0] if dev_agents else None

        doc_agents = registry.find_by_capability(AgentCapability.DOCUMENT)
        self._doc_agent = doc_agents[0] if doc_agents else None

        architect_agents = registry.find_by_capability(AgentCapability.ARCHITECT)
        self._architect_agent = architect_agents[0] if architect_agents else None

        qa_agents = registry.find_by_capability(AgentCapability.QA)
        self._qa_agent = qa_agents[0] if qa_agents else None

        # Escalation tracker
        self._escalation_tracker = EscalationTracker(agent_context.project_dir)
        if self._escalation_tracker.exists:
            self._escalation_tracker.load()

        # Token tracker — auto-pushes status to UI after every AI call
        self._token_tracker = TokenTracker()
        if self._status_fn:
            self._token_tracker._on_update = lambda text: self._status_fn(text, "tokens")
        elif self._console:
            self._token_tracker._on_update = self._console.print_token_status

        # Intent classifier for natural language command detection
        self._intent_classifier = build_build_classifier(
            ai_provider=agent_context.ai_provider,
            token_tracker=self._token_tracker,
        )

        # Project config
        config = ProjectConfig(agent_context.project_dir)
        config.load()
        self._config = config
        self._iac_tool: str = config.get("project.iac_tool", "terraform")
        self._project_name: str = config.get("project.name", "prototype")

        # Naming strategy for computed resource names
        try:
            self._naming = create_naming_strategy(config.to_dict())
        except Exception:
            # Graceful fallback — use simple strategy defaults
            self._naming = create_naming_strategy(
                {"naming": {"strategy": "simple"}, "project": {"name": self._project_name}}
            )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def run(
        self,
        *,
        design: dict,
        templates: list | None = None,
        scope: str = "all",
        input_fn: Callable[[str], str] | None = None,
        print_fn: Callable[[str], None] | None = None,
    ) -> BuildResult:
        """Run the interactive build session.

        Parameters
        ----------
        design:
            Design state dict (loaded from ``.prototype/state/design.json``).
            Must contain an ``architecture`` key with the architect's output.
        templates:
            List of matched :class:`~.templates.registry.ProjectTemplate`
            objects (may be empty — templates are optional starting points).
        scope:
            Build scope (``all``, ``infra``, ``apps``, ``db``, ``docs``).
        input_fn / print_fn:
            Injectable I/O for testing.

        Returns
        -------
        BuildResult
        """
        use_styled = input_fn is None and print_fn is None
        _input = input_fn or (lambda p: self._prompt.prompt(p))
        _print = print_fn or self._console.print

        architecture = design.get("architecture", "")
        templates = templates or []

        # Persist template + tool choices
        self._build_state._state["templates_used"] = [t.name for t in templates] if templates else []
        self._build_state._state["iac_tool"] = self._iac_tool

        # ---- Phase 1: Show what we're working with ----
        _print("")
        _print("Build Stage")
        _print("=" * 40)
        _print("")

        if templates:
            tmpl_names = ", ".join(t.display_name for t in templates)
            _print(f"Template(s): {tmpl_names}")
        else:
            _print("Templates: None (building from architecture)")
        _print(f"IaC Tool: {self._iac_tool}")
        _print("")

        # ---- Phase 2: Derive deployment plan (three-branch) ----
        existing_stages = self._build_state._state.get("deployment_stages", [])
        skip_generation = False

        if not existing_stages:
            # Branch A: First build — derive fresh plan and save design snapshot
            _print("Deriving deployment plan...")
            _print("")

            with self._maybe_spinner("Analyzing architecture for deployment stages...", use_styled):
                stages = self._derive_deployment_plan(architecture, templates)

            if not stages:
                _print("Could not derive deployment plan from architecture.")
                return BuildResult(cancelled=True)

            self._build_state.set_deployment_plan(stages)
            self._build_state.set_design_snapshot(design)

        elif self._build_state.design_has_changed(design):
            # Branch B: Design changed — incremental rebuild
            _print("Design changes detected since last build.")
            _print("")

            old_arch = self._build_state.get_previous_architecture()

            if old_arch:
                with self._maybe_spinner("Analyzing design changes...", use_styled):
                    diff_result = self._diff_architectures(old_arch, architecture, existing_stages)
            else:
                # Legacy build with no snapshot text — treat all as modified
                diff_result = {
                    "unchanged": [],
                    "modified": [s["stage"] for s in existing_stages],
                    "removed": [],
                    "added": [],
                    "plan_restructured": False,
                    "summary": "No previous architecture snapshot — marking all stages for rebuild.",
                }

            _print(f"  {diff_result.get('summary', 'Changes analyzed.')}")
            _print("")

            if diff_result.get("plan_restructured"):
                _print("The design changes are significant enough to require a full plan re-derive.")
                _print("Press Enter to re-derive the full plan, or type 'quit' to cancel.")
                _print("")
                try:
                    if use_styled:
                        confirm = self._prompt.simple_prompt("> ")
                    else:
                        confirm = _input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    return BuildResult(cancelled=True)
                if confirm.lower() in _QUIT_WORDS:
                    return BuildResult(cancelled=True)

                with self._maybe_spinner("Re-deriving deployment plan...", use_styled):
                    stages = self._derive_deployment_plan(architecture, templates)
                if not stages:
                    _print("Could not derive deployment plan from architecture.")
                    return BuildResult(cancelled=True)
                self._build_state.set_deployment_plan(stages)
            else:
                # Apply targeted updates
                removed = diff_result.get("removed", [])
                added = diff_result.get("added", [])
                modified = diff_result.get("modified", [])

                if removed:
                    self._clean_removed_stage_files(removed, existing_stages)
                    self._build_state.remove_stages(removed)
                    _print(f"  Removed {len(removed)} stage(s).")

                if added:
                    self._build_state.add_stages(added)
                    _print(f"  Added {len(added)} new stage(s).")

                if modified:
                    self._build_state.mark_stages_stale(modified)
                    _print(f"  Marked {len(modified)} stage(s) for regeneration.")

                if removed or added:
                    self._fix_stage_dirs()

            # Update the design snapshot
            self._build_state.set_design_snapshot(design)

        else:
            # Branch C: No design changes
            pending_check = self._build_state.get_pending_stages()
            if pending_check:
                _print("Resuming from existing deployment plan.")
                _print("")
            else:
                _print("Build is up to date — no design changes detected.")
                _print("")
                skip_generation = True

        # Present the plan
        _print(self._build_state.format_stage_status())
        _print("")

        if not skip_generation:
            _print("Review the deployment plan above.")
            _print("Press Enter to start building, or provide feedback to adjust.")
            _print("")

            try:
                if use_styled:
                    confirmation = self._prompt.simple_prompt("> ")
                else:
                    # allow_empty=True so pressing Enter proceeds without text
                    try:
                        confirmation = _input("> ", allow_empty=True).strip()
                    except TypeError:
                        # Fallback for callables that don't accept allow_empty
                        confirmation = _input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                return BuildResult(cancelled=True)

            if confirmation.lower() in _QUIT_WORDS:
                return BuildResult(cancelled=True)

            # If user provides feedback, adjust the plan
            if confirmation and confirmation.lower() not in _DONE_WORDS and confirmation.strip():
                with self._maybe_spinner("Adjusting deployment plan...", use_styled):
                    adjusted = self._adjust_plan(confirmation, architecture, templates)
                if adjusted:
                    self._build_state.set_deployment_plan(adjusted)
                    _print("")
                    _print(self._build_state.format_stage_status())
                    _print("")

        # ---- Populate TUI tree with deployment stages ----
        if self._section_fn:
            all_stages = self._build_state._state.get("deployment_stages", [])
            self._section_fn([
                (f"Stage {s.get('stage', 0)}: {s.get('name', '')}", 2)
                for s in all_stages
            ])
            # Mark already-generated stages as completed
            if self._update_task_fn:
                for s in all_stages:
                    if s.get("status") in ("generated", "accepted"):
                        slug = f"build-stage-{s.get('stage', 0)}"
                        self._update_task_fn(slug, "completed")

        # ---- Condense architecture into per-stage contexts (ONE call) ----
        all_stages = self._build_state._state.get("deployment_stages", [])
        with self._maybe_spinner("Condensing architecture into per-stage contexts...", use_styled):
            stage_contexts = self._condense_architecture(architecture, all_stages, use_styled)

        # ---- Phase 3: Staged generation ----
        if skip_generation:
            pending = []
            total_stages = len(self._build_state._state["deployment_stages"])
            generated_count = total_stages
        else:
            pending = self._build_state.get_pending_stages()
            total_stages = len(self._build_state._state["deployment_stages"])
        generated_count = len(self._build_state.get_generated_stages())

        for stage in pending:
            stage_num = stage["stage"]
            stage_name = stage["name"]
            category = stage.get("category", "infra")
            services = stage.get("services", [])

            svc_names = [s.get("computed_name") or s.get("name", "") for s in services]
            svc_display = ", ".join(svc_names[:3])
            if len(svc_names) > 3:
                svc_display += f" (+{len(svc_names) - 3} more)"

            generated_count += 1
            task_id = f"build-stage-{stage_num}"
            if self._update_task_fn:
                self._update_task_fn(task_id, "in_progress")
            _print(f"[{generated_count}/{total_stages}] Stage {stage_num}: {stage_name}")
            if svc_display:
                _print(f"       Resources: {svc_display}")

            # Use condensed per-stage context (from one-time condensation call)
            focused_context = stage_contexts.get(stage_num, "")

            # Apply governor brief BEFORE building the task so the
            # ## MANDATORY GOVERNANCE RULES section is injected into the
            # task string (near the end where the model pays most attention).
            agent = self._select_agent(stage)
            if not agent:
                _print(f"       Skipped (no agent for category '{stage.get('category', '')}')")
                continue
            self._apply_governor_brief(agent, stage_name, services)

            agent, task = self._build_stage_task(stage, focused_context, templates)

            # Temporarily disable knowledge/standards to keep the prompt lean
            # (~14KB vs 83KB). The condensed context + governor brief have
            # everything the model needs.
            saved_knowledge = (agent._knowledge_role, agent._knowledge_tools, agent._knowledge_languages)
            saved_standards = agent._include_standards
            agent._knowledge_role = ""
            agent._knowledge_tools = []
            agent._knowledge_languages = []
            agent._include_standards = False

            try:
                with self._maybe_spinner(f"Building Stage {stage_num}: {stage_name}...", use_styled):
                    response = agent.execute(self._context, task)
            except Exception as exc:
                _print(f"       Agent error in Stage {stage_num} — routing to QA for diagnosis...")
                svc_names_list = [s.get("name", "") for s in services if s.get("name")]
                route_error_to_qa(
                    exc,
                    f"Build Stage {stage_num}: {stage_name}",
                    self._qa_agent,
                    self._context,
                    self._token_tracker,
                    _print,
                    services=svc_names_list,
                    escalation_tracker=self._escalation_tracker,
                    source_agent=agent.name,
                    source_stage="build",
                )
                # Restore agent settings and skip to next stage
                agent._knowledge_role, agent._knowledge_tools, agent._knowledge_languages = saved_knowledge
                agent._include_standards = saved_standards
                continue
            # Restore agent settings on success path
            agent._knowledge_role, agent._knowledge_tools, agent._knowledge_languages = saved_knowledge
            agent._include_standards = saved_standards

            if response:
                self._token_tracker.record(response)
            content = response.content if response else ""

            from azext_prototype.debug_log import log_flow as _dbg_flow
            from azext_prototype.parsers.file_extractor import parse_file_blocks as _dbg_parse

            _dbg_flow(
                "build_session.generate",
                f"Stage {stage_num} response",
                content_len=len(content) if content else 0,
                content_type=type(content).__name__,
                content_preview=(content[:300] if content else "(empty)"),
            )

            # Debug: check what the parser would extract
            _dbg_files = _dbg_parse(content) if content else {}
            _dbg_flow(
                "build_session.generate",
                f"Stage {stage_num} parse_file_blocks",
                file_count=len(_dbg_files),
                filenames=list(_dbg_files.keys())[:10],
            )

            if not content:
                _print(f"       Empty response for Stage {stage_num} — routing to QA for diagnosis...")
                svc_names_list = [s.get("name", "") for s in services if s.get("name")]
                route_error_to_qa(
                    "Agent returned empty response",
                    f"Build Stage {stage_num}: {stage_name}",
                    self._qa_agent,
                    self._context,
                    self._token_tracker,
                    _print,
                    services=svc_names_list,
                    escalation_tracker=self._escalation_tracker,
                    source_agent=agent.name,
                    source_stage="build",
                )
            written_paths = self._write_stage_files(stage, content)

            _dbg_flow(
                "build_session.generate",
                f"Stage {stage_num} written_paths",
                count=len(written_paths),
                paths=written_paths[:5],
            )

            self._build_state.mark_stage_generated(stage_num, written_paths, agent.name)
            if self._update_task_fn:
                self._update_task_fn(task_id, "completed")

            if written_paths:
                if use_styled:
                    self._console.print_file_list(written_paths)
                else:
                    for f in written_paths:
                        _print(f"         {f}")
            else:
                _print("       No files extracted from response.")

            # Policy check
            if content:
                resolutions, needs_regen = self._policy_resolver.check_and_resolve(
                    agent.name,
                    content,
                    self._build_state,
                    stage_num,
                    input_fn=input_fn,
                    print_fn=print_fn,
                )

                if needs_regen:
                    fix_instructions = self._policy_resolver.build_fix_instructions(resolutions)
                    _print("Regenerating with fix instructions...")

                    try:
                        with self._maybe_spinner(f"Re-building Stage {stage_num}...", use_styled):
                            response = agent.execute(self._context, task + fix_instructions)
                    except Exception as exc:
                        svc_names_list = [s.get("name", "") for s in services if s.get("name")]
                        route_error_to_qa(
                            exc,
                            f"Build Stage {stage_num} (regen): {stage_name}",
                            self._qa_agent,
                            self._context,
                            self._token_tracker,
                            _print,
                            services=svc_names_list,
                            escalation_tracker=self._escalation_tracker,
                            source_agent=agent.name,
                            source_stage="build",
                        )
                        continue

                    if response:
                        self._token_tracker.record(response)
                    content = response.content if response else ""
                    written_paths = self._write_stage_files(stage, content)
                    self._build_state.mark_stage_generated(stage_num, written_paths, agent.name)

            # Per-stage QA validation
            if category in ("infra", "data", "integration", "app"):
                self._run_stage_qa(stage, architecture, templates, use_styled, _print)

            if use_styled:
                self._console.print_token_status(self._token_tracker.format_status())
            _print("")

        # ---- Phase 4: Advisory QA review ----
        if not skip_generation and scope == "all" and self._qa_agent:
            _print("Running advisory review...")

            file_content = self._collect_generated_file_content()

            qa_task = (
                "All stages have passed per-stage QA validation. Now perform a "
                "HIGH-LEVEL ADVISORY review of the complete build output.\n\n"
                "Do NOT re-check for bugs or correctness issues — those were "
                "already caught and fixed during per-stage QA.\n\n"
                "Instead, focus on:\n"
                "- **Known limitations** of the chosen architecture or services\n"
                "- **Security considerations** worth noting (e.g., services running "
                "with default SKUs that lack advanced threat protection)\n"
                "- **Scalability notes** (e.g., Basic-tier services that may need "
                "upgrading for production)\n"
                "- **Cost implications** the user should be aware of\n"
                "- **Architectural trade-offs** made for prototype simplicity\n"
                "- **Missing production concerns** (monitoring gaps, backup config, "
                "disaster recovery, etc.)\n\n"
                "Format your response as a concise list of advisories. Each item "
                "should be a short paragraph with a clear heading. Do NOT suggest "
                "code changes — these are informational notes only.\n\n"
                "## Generated Files\n\n"
            )
            qa_task += file_content if file_content else "(No files.)"

            with self._maybe_spinner("Advisory review...", use_styled):
                orchestrator = AgentOrchestrator(self._registry, self._context)
                qa_result = orchestrator.delegate(
                    from_agent="build-session",
                    to_agent_name=self._qa_agent.name,
                    sub_task=qa_task,
                )
            if qa_result:
                self._token_tracker.record(qa_result)

            qa_content = qa_result.content if qa_result else ""

            if qa_content:
                if use_styled:
                    self._console.print_header("Advisory Notes")
                    self._console.print_agent_response(qa_content)
                else:
                    _print("")
                    _print("Advisory Notes:")
                    _print(qa_content[:2000])
            if use_styled:
                self._console.print_token_status(self._token_tracker.format_status())

            # Fire-and-forget knowledge contribution
            try:
                from azext_prototype.knowledge import KnowledgeLoader
                from azext_prototype.stages.knowledge_contributor import (
                    build_finding_from_qa,
                    submit_if_gap,
                )

                loader = KnowledgeLoader()
                all_services: set[str] = set()
                for ds in self._build_state._state.get("deployment_stages", []):
                    for svc in ds.get("services", []):
                        if svc.get("name"):
                            all_services.add(svc["name"])
                if all_services and qa_content:
                    for svc in all_services:
                        finding = build_finding_from_qa(qa_content, service=svc, source="Build advisory review")
                        submit_if_gap(finding, loader, print_fn=_print)
            except Exception:
                pass

            _print("")

        # ---- Phase 5: Build report ----
        if not skip_generation:
            _print("")
            _print(self._build_state.format_build_report())
            _print("")

        # ---- Phase 6: Review loop ----
        _print("Review the build output above.")
        _print("Provide feedback to regenerate specific stages, or type 'done' to accept.")
        _print("")

        iteration = 0
        while True:
            try:
                if use_styled:
                    user_input = self._prompt.prompt(
                        "> ",
                        instruction="Type 'done' to accept the build.",
                        show_quit_hint=True,
                    )
                else:
                    user_input = _input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input:
                continue

            lower = user_input.lower()

            # Slash commands
            if lower.startswith("/"):
                self._handle_slash_command(lower, _print)
                continue

            # Natural language intent detection
            intent = self._intent_classifier.classify(user_input)
            if intent.kind == IntentKind.COMMAND:
                if intent.command == "/describe" and intent.args:
                    self._handle_describe(intent.args, _print)
                else:
                    self._handle_slash_command(intent.command, _print)
                continue
            if intent.kind == IntentKind.READ_FILES:
                text, _ = read_files_for_session(intent.args, self._context.project_dir, _print)
                if text:
                    user_input = f"{user_input}\n\n## File Content\n{text}"
                # Fall through to feedback handler with enriched input

            if lower in _QUIT_WORDS:
                return BuildResult(
                    files_generated=self._build_state._state.get("files_generated", []),
                    deployment_stages=self._build_state._state.get("deployment_stages", []),
                    cancelled=True,
                )

            if lower in _DONE_WORDS:
                break

            # User feedback — regenerate affected stages
            iteration += 1
            self._build_state.add_review_decision(user_input, iteration)

            _print("")
            affected_stages = self._identify_affected_stages(user_input)

            if affected_stages:
                for stage_num in affected_stages:
                    stage = self._build_state.get_stage(stage_num)
                    if not stage:
                        continue

                    _print(f"Regenerating Stage {stage_num}: {stage['name']}...")

                    agent, task = self._build_stage_task(stage, architecture, templates)
                    if not agent:
                        continue

                    task += f"\n\n## User Feedback\n{user_input}\n"

                    with self._maybe_spinner(f"Re-building Stage {stage_num}...", use_styled):
                        response = agent.execute(self._context, task)

                    if response:
                        self._token_tracker.record(response)
                    content = response.content if response else ""
                    written_paths = self._write_stage_files(stage, content)
                    self._build_state.mark_stage_generated(stage_num, written_paths, agent.name)

                    if written_paths:
                        if use_styled:
                            self._console.print_file_list(written_paths)
                        else:
                            for f in written_paths:
                                _print(f"         {f}")

                    # Policy check on regenerated content
                    if content:
                        self._policy_resolver.check_and_resolve(
                            agent.name,
                            content,
                            self._build_state,
                            stage_num,
                            input_fn=input_fn,
                            print_fn=print_fn,
                        )

                _print("")
                _print(self._build_state.format_build_report())
            else:
                _print("Could not determine which stages to regenerate.")
                _print("Try specifying a stage number or service name.")

            _print("")

        # Mark all generated stages as accepted
        for stage in self._build_state._state.get("deployment_stages", []):
            if stage.get("status") == "generated":
                self._build_state.mark_stage_accepted(stage["stage"])

        return BuildResult(
            files_generated=self._build_state._state.get("files_generated", []),
            deployment_stages=self._build_state._state.get("deployment_stages", []),
            policy_overrides=self._build_state._state.get("policy_overrides", []),
            resources=self._build_state.get_all_resources(),
            review_accepted=True,
        )

    # ------------------------------------------------------------------ #
    # Internal — deployment plan derivation
    # ------------------------------------------------------------------ #

    def _derive_deployment_plan(
        self,
        architecture: str,
        templates: list,
    ) -> list[dict]:
        """Ask the architect to derive a deployment plan from the design.

        Falls back to :meth:`_fallback_deployment_plan` when no architect
        agent is available or the AI response cannot be parsed.
        """
        if not self._architect_agent or not self._context.ai_provider:
            return self._fallback_deployment_plan(templates)

        template_context = ""
        if templates:
            for t in templates:
                template_context += f"\nTemplate: {t.display_name}\nServices: "
                template_context += ", ".join(f"{s.name} ({s.type}, tier={s.tier})" for s in t.services)
                template_context += "\n"

        naming_instructions = self._naming.to_prompt_instructions()

        task = (
            "Analyze this architecture design and produce a deployment plan.\n\n" f"## Architecture\n{architecture}\n\n"
        )
        if template_context:
            task += f"## Template Starting Points\n{template_context}\n\n"

        task += f"## Naming Convention\n{naming_instructions}\n\n"
        task += (
            "## Instructions\n"
            "Produce a JSON deployment plan with fine-grained stages.\n\n"
            "Rules:\n"
            "- All infrastructure stages come before all application/schema stages\n"
            "- Each infrastructure component gets its own stage\n"
            "- Each database system gets its own stage\n"
            "- Each application gets its own stage\n"
            "- Documentation is always the last stage\n"
            "- Order stages by dependency (foundation first, then networking, "
            "then data, then compute, then integration, etc.)\n"
            "- 3rd-party integrations and CI/CD pipelines get their own stages if present\n\n"
            "For each service include:\n"
            "- name: short service identifier (e.g., 'key-vault', 'sql-server')\n"
            "- computed_name: full resource name using the naming convention\n"
            "- resource_type: ARM resource type (e.g., Microsoft.KeyVault/vaults)\n"
            "- sku: tier/SKU if applicable (empty string if not)\n\n"
            "Each stage must have: stage (number), name, category "
            "(infra|data|app|schema|integration|docs|cicd|external), dir (output "
            f"directory path), services (array), status ('pending'), files (empty array).\n\n"
            "Optional per-stage fields:\n"
            "- deploy_mode: 'auto' (default, deploy via IaC/scripts) or 'manual' "
            "(step that cannot be scripted, e.g., portal configuration)\n"
            "- manual_instructions: when deploy_mode is 'manual', provide clear "
            "step-by-step instructions for the user\n\n"
            f"Use '{self._iac_tool}' for IaC directories.  Infrastructure stage dirs "
            f"should be like: concept/infra/{self._iac_tool}/stage-N-name/\n"
            "App stage dirs: concept/apps/stage-N-name/\n"
            "Schema stage dirs: concept/db/type/\n"
            "Doc stage dir: concept/docs/\n\n"
            "Response format — return ONLY valid JSON, no markdown explanation:\n"
            "```json\n"
            '{"stages": [\n'
            '  {"stage": 1, "name": "Foundation", "category": "infra",\n'
            f'   "dir": "concept/infra/{self._iac_tool}/stage-1-foundation",\n'
            '   "services": [\n'
            '     {"name": "managed-identity", "computed_name": "...",\n'
            '      "resource_type": "Microsoft.ManagedIdentity/userAssignedIdentities",\n'
            '      "sku": ""}\n'
            '   ], "status": "pending", "files": []}\n'
            "]}\n"
            "```\n"
        )

        response = self._architect_agent.execute(self._context, task)
        if response:
            self._token_tracker.record(response)
        if not response or not response.content:
            return self._fallback_deployment_plan(templates)

        stages = self._parse_deployment_plan(response.content)
        return stages if stages else self._fallback_deployment_plan(templates)

    def _parse_deployment_plan(self, content: str) -> list[dict]:
        """Parse deployment plan JSON from architect response.

        Tries to extract JSON from a fenced code block first, then falls
        back to parsing the entire content as JSON.
        """
        # Try fenced JSON block
        json_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", content, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                stages = data.get("stages", [])
                if stages:
                    return self._normalise_stages(stages)
            except (json.JSONDecodeError, TypeError):
                pass

        # Try entire content as JSON
        try:
            data = json.loads(content.strip())
            stages = data.get("stages", [])
            if stages:
                return self._normalise_stages(stages)
        except (json.JSONDecodeError, TypeError):
            pass

        return []

    def _normalise_stages(self, stages: list[dict]) -> list[dict]:
        """Ensure every stage has all required keys with sensible defaults."""
        normalised = []
        for s in stages:
            if not isinstance(s, dict):
                continue
            entry = {
                "stage": s.get("stage", len(normalised) + 1),
                "name": s.get("name", f"Stage {len(normalised) + 1}"),
                "category": s.get("category", "infra"),
                "dir": s.get("dir", ""),
                "services": s.get("services", []),
                "status": "pending",
                "files": [],
                "deploy_mode": s.get("deploy_mode", "auto"),
                "manual_instructions": s.get("manual_instructions"),
            }
            normalised.append(entry)
        return normalised

    def _fallback_deployment_plan(self, templates: list) -> list[dict]:
        """Create a basic deployment plan when no architect is available.

        Derives stages from template services (if any) or creates a
        minimal two-stage plan (Foundation + Documentation).
        """
        stages: list[dict] = []
        stage_num = 0

        # Foundation stage (always present)
        stage_num += 1
        stages.append(
            {
                "stage": stage_num,
                "name": "Foundation",
                "category": "infra",
                "dir": f"concept/infra/{self._iac_tool}/stage-{stage_num}-foundation",
                "services": [
                    {
                        "name": "resource-group",
                        "computed_name": self._naming.resolve("resource_group", self._project_name),
                        "resource_type": "Microsoft.Resources/resourceGroups",
                        "sku": "",
                    },
                    {
                        "name": "managed-identity",
                        "computed_name": self._naming.resolve("managed_identity", self._project_name),
                        "resource_type": "Microsoft.ManagedIdentity/userAssignedIdentities",
                        "sku": "",
                    },
                ],
                "status": "pending",
                "files": [],
            }
        )

        # Add stages from template services
        if templates:
            infra_services = []
            data_services = []
            app_services = []

            for t in templates:
                for svc in t.services:
                    cat = self._categorise_service(svc.type)
                    entry = {
                        "name": svc.name,
                        "type": svc.type,
                        "tier": svc.tier,
                        "config": svc.config,
                    }
                    if cat == "infra":
                        infra_services.append(entry)
                    elif cat == "data":
                        data_services.append(entry)
                    else:
                        app_services.append(entry)

            # Infrastructure services (each gets its own stage)
            for svc in infra_services:
                stage_num += 1
                resource_type_key = svc["type"].replace("-", "_")
                stages.append(
                    {
                        "stage": stage_num,
                        "name": svc["name"].replace("-", " ").title(),
                        "category": "infra",
                        "dir": f"concept/infra/{self._iac_tool}/stage-{stage_num}-{svc['name']}",
                        "services": [
                            {
                                "name": svc["name"],
                                "computed_name": self._naming.resolve(resource_type_key, svc["name"]),
                                "resource_type": "",
                                "sku": svc["tier"],
                            }
                        ],
                        "status": "pending",
                        "files": [],
                    }
                )

            # Data services
            for svc in data_services:
                stage_num += 1
                resource_type_key = svc["type"].replace("-", "_")
                stages.append(
                    {
                        "stage": stage_num,
                        "name": svc["name"].replace("-", " ").title(),
                        "category": "data",
                        "dir": f"concept/infra/{self._iac_tool}/stage-{stage_num}-{svc['name']}",
                        "services": [
                            {
                                "name": svc["name"],
                                "computed_name": self._naming.resolve(resource_type_key, svc["name"]),
                                "resource_type": "",
                                "sku": svc["tier"],
                            }
                        ],
                        "status": "pending",
                        "files": [],
                    }
                )

            # Application services
            for svc in app_services:
                stage_num += 1
                stages.append(
                    {
                        "stage": stage_num,
                        "name": svc["name"].replace("-", " ").title(),
                        "category": "app",
                        "dir": f"concept/apps/stage-{stage_num}-{svc['name']}",
                        "services": [
                            {
                                "name": svc["name"],
                                "computed_name": "",
                                "resource_type": "",
                                "sku": svc["tier"],
                            }
                        ],
                        "status": "pending",
                        "files": [],
                    }
                )

        # Documentation stage (always last)
        stage_num += 1
        stages.append(
            {
                "stage": stage_num,
                "name": "Documentation",
                "category": "docs",
                "dir": "concept/docs",
                "services": [],
                "status": "pending",
                "files": [],
            }
        )

        return stages

    @staticmethod
    def _categorise_service(service_type: str) -> str:
        """Categorise a template service type into a stage category."""
        _INFRA_TYPES = {
            "virtual-network",
            "key-vault",
            "container-app-environment",
            "app-service-plan",
            "managed-identity",
            "resource-group",
            "api-management",
            "front-door",
            "cdn-profile",
            "dns-zone",
            "application-insights",
            "log-analytics",
            "network-security-group",
            "private-endpoint",
        }
        _DATA_TYPES = {
            "sql-database",
            "cosmos-db",
            "redis-cache",
            "storage-account",
            "databricks",
            "data-factory",
            "event-hub",
            "service-bus",
        }
        if service_type in _INFRA_TYPES:
            return "infra"
        if service_type in _DATA_TYPES:
            return "data"
        return "app"

    # ------------------------------------------------------------------ #
    # Internal — plan adjustment
    # ------------------------------------------------------------------ #

    def _adjust_plan(
        self,
        feedback: str,
        architecture: str,
        templates: list,
    ) -> list[dict] | None:
        """Ask the architect to adjust the deployment plan."""
        if not self._architect_agent or not self._context.ai_provider:
            return None

        current_plan = json.dumps(
            self._build_state._state.get("deployment_stages", []),
            indent=2,
        )

        task = (
            "Adjust this deployment plan based on user feedback.\n\n"
            f"## Current Plan\n```json\n{current_plan}\n```\n\n"
            f"## User Feedback\n{feedback}\n\n"
            f"## Architecture\n{architecture}\n\n"
            "Return the adjusted plan in the same JSON format.  Keep all "
            "required keys (stage, name, category, dir, services, status, files).\n"
            '```json\n{"stages": [...]}\n```\n'
        )

        response = self._architect_agent.execute(self._context, task)
        if response:
            self._token_tracker.record(response)
        if response and response.content:
            return self._parse_deployment_plan(response.content)
        return None

    # ------------------------------------------------------------------ #
    # Internal — incremental rebuild helpers
    # ------------------------------------------------------------------ #

    def _diff_architectures(
        self,
        old_arch: str,
        new_arch: str,
        existing_stages: list[dict],
    ) -> dict:
        """Ask the architect to compare old and new architectures.

        Returns a dict classifying each existing stage as unchanged,
        modified, or removed, plus any new stages to add.

        Falls back to marking all stages as modified when the architect
        is unavailable or the response cannot be parsed.
        """
        all_modified_fallback: dict = {
            "unchanged": [],
            "modified": [s["stage"] for s in existing_stages],
            "removed": [],
            "added": [],
            "plan_restructured": False,
            "summary": "Could not analyze changes — marking all stages for rebuild.",
        }

        if not self._architect_agent or not self._context.ai_provider:
            return all_modified_fallback

        stage_info = json.dumps(
            [
                {
                    "stage": s["stage"],
                    "name": s["name"],
                    "category": s.get("category", "infra"),
                    "services": [svc.get("name", "") for svc in s.get("services", [])],
                }
                for s in existing_stages
            ],
            indent=2,
        )

        task = (
            "Compare the OLD and NEW architecture designs and determine how each "
            "existing deployment stage is affected.\n\n"
            f"## Old Architecture\n{old_arch}\n\n"
            f"## New Architecture\n{new_arch}\n\n"
            f"## Existing Deployment Stages\n```json\n{stage_info}\n```\n\n"
            "## Instructions\n"
            "Classify each stage number as:\n"
            "- **unchanged**: no impact from the design changes\n"
            "- **modified**: services or configuration in this stage changed\n"
            "- **removed**: the services in this stage no longer exist in the new design\n\n"
            "Also identify any NEW services that need new stages.\n\n"
            "Set `plan_restructured: true` ONLY if the fundamental deployment "
            "order or stage boundaries need to change (e.g., services moved between "
            "stages, major dependency changes). Minor additions/removals should NOT "
            "set this flag.\n\n"
            "Return ONLY valid JSON:\n"
            "```json\n"
            "{\n"
            '  "unchanged": [1, 2],\n'
            '  "modified": [3],\n'
            '  "removed": [4],\n'
            '  "added": [{"name": "Redis Cache", "category": "data", "services": '
            '[{"name": "redis-cache", "computed_name": "", "resource_type": '
            '"Microsoft.Cache/redis", "sku": "Basic"}]}],\n'
            '  "plan_restructured": false,\n'
            '  "summary": "Added Redis cache; modified API to use Redis"\n'
            "}\n"
            "```\n"
        )

        try:
            response = self._architect_agent.execute(self._context, task)
            if response:
                self._token_tracker.record(response)
            if response and response.content:
                result = self._parse_diff_result(response.content, existing_stages)
                if result:
                    return result
        except Exception:
            logger.debug("Architecture diff failed", exc_info=True)

        return all_modified_fallback

    def _parse_diff_result(self, content: str, existing_stages: list[dict]) -> dict | None:
        """Parse the architect's diff response into a structured result.

        Validates that referenced stage numbers actually exist.  Stages
        not mentioned by the architect default to ``unchanged``.
        """
        # Try fenced JSON block first
        json_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", content, re.DOTALL)
        raw = json_match.group(1) if json_match else content.strip()

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

        if not isinstance(data, dict):
            return None

        existing_nums = {s["stage"] for s in existing_stages}

        unchanged = [n for n in data.get("unchanged", []) if isinstance(n, int) and n in existing_nums]
        modified = [n for n in data.get("modified", []) if isinstance(n, int) and n in existing_nums]
        removed = [n for n in data.get("removed", []) if isinstance(n, int) and n in existing_nums]

        # Stages not mentioned default to unchanged
        mentioned = set(unchanged) | set(modified) | set(removed)
        for num in existing_nums:
            if num not in mentioned:
                unchanged.append(num)

        added = data.get("added", [])
        if not isinstance(added, list):
            added = []
        # Normalise added stages
        normalised_added = []
        for item in added:
            if isinstance(item, dict) and item.get("name"):
                normalised_added.append(
                    {
                        "name": item["name"],
                        "category": item.get("category", "infra"),
                        "services": item.get("services", []),
                        "dir": item.get("dir", ""),
                    }
                )

        return {
            "unchanged": sorted(unchanged),
            "modified": sorted(modified),
            "removed": sorted(removed),
            "added": normalised_added,
            "plan_restructured": bool(data.get("plan_restructured", False)),
            "summary": data.get("summary", "Design changes analyzed."),
        }

    def _clean_removed_stage_files(self, removed_nums: list[int], stages: list[dict]) -> None:
        """Delete generated directories from disk for removed stages."""
        project_root = Path(self._context.project_dir)
        for stage in stages:
            if stage["stage"] in removed_nums:
                stage_dir = stage.get("dir", "")
                if stage_dir:
                    full_path = project_root / stage_dir
                    if full_path.exists() and full_path.is_dir():
                        shutil.rmtree(full_path, ignore_errors=True)
                        logger.info("Removed stage directory: %s", full_path)

    def _fix_stage_dirs(self) -> None:
        """Update stage directory paths to match current stage numbers.

        After renumbering, stage dirs like ``stage-4-redis`` may need to
        become ``stage-3-redis`` if a prior stage was removed.
        """
        for stage in self._build_state._state.get("deployment_stages", []):
            old_dir = stage.get("dir", "")
            if not old_dir:
                continue
            # Match pattern: .../stage-N-name
            match = re.match(r"^(.*?/?)stage-\d+(-.*)?$", old_dir)
            if match:
                prefix = match.group(1)
                suffix = match.group(2) or ""
                new_dir = f"{prefix}stage-{stage['stage']}{suffix}"
                if new_dir != old_dir:
                    stage["dir"] = new_dir
        self._build_state.save()

    # ------------------------------------------------------------------ #
    # Internal — governor integration
    # ------------------------------------------------------------------ #

    def _apply_governor_brief(self, agent: Any, stage_name: str, services: list[dict]) -> None:
        """Set a governor policy brief on the agent before generation.

        Retrieves the most relevant policy rules for this stage's context
        and injects them as a concise ~1-2KB brief into the agent's system
        prompt, replacing the full ~40KB policy dump.
        """
        try:
            from azext_prototype.governance.governor import brief as governor_brief

            svc_names = [s.get("name", "") for s in services if s.get("name")]
            task_desc = f"Generate {self._iac_tool} code for {stage_name}: {', '.join(svc_names)}"
            policy_brief = governor_brief(
                project_dir=self._context.project_dir,
                task_description=task_desc,
                agent_name=agent.name,
                top_k=15,
            )
            if policy_brief:
                agent.set_governor_brief(policy_brief)
        except Exception:
            pass  # Never let governor errors block generation

    def _condense_architecture(self, architecture: str, stages: list[dict], use_styled: bool) -> dict[int, str]:
        """One-time condensation of the full architecture into per-stage contexts.

        Makes ONE AI call with the full architecture and deployment plan,
        producing a ~1KB context excerpt per stage.  The result is cached
        in ``build_state.stage_contexts`` so subsequent stages don't need
        the 542KB architecture at all.

        Returns ``{stage_num: context_str}`` mapping.
        """
        from azext_prototype.ai.provider import AIMessage

        # Check cache first
        cached = self._build_state._state.get("stage_contexts", {})
        if cached and len(cached) >= len(stages):
            return {int(k): v for k, v in cached.items()}

        if not self._context.ai_provider:
            return {}

        # Build stage summary for the prompt
        stage_list = ""
        for s in stages:
            svcs = [f"{sv.get('computed_name', '')} ({sv.get('resource_type', '')})" for sv in s.get("services", [])]
            stage_list += f"- Stage {s['stage']}: {s['name']} ({s.get('category', '')}) — {', '.join(svcs)}\n"

        prompt = (
            "Given this architecture and deployment plan, produce a stage-indexed "
            "context document. For EACH stage below, provide:\n"
            "1. What it builds and its role in the system (2-3 sentences)\n"
            "2. Key configuration decisions (SKUs, tiers, network mode private/public)\n"
            "3. Upstream dependencies (resource names/IDs this stage must reference)\n"
            "4. Downstream outputs (what later stages need from this one)\n\n"
            "Keep each stage's context under 1000 characters.\n"
            "Return EXACTLY this format:\n\n"
            "## Stage 1: <name>\n<context>\n\n## Stage 2: <name>\n<context>\n...\n\n"
            f"## Deployment Plan\n{stage_list}\n"
            f"## Architecture\n{architecture}"
        )

        system = AIMessage(
            role="system",
            content="You are an architecture analyst. Produce concise per-stage context summaries.",
        )
        user_msg = AIMessage(role="user", content=prompt)

        try:
            response = self._context.ai_provider.chat(
                [system, user_msg],
                temperature=0.1,
                max_tokens=16384,
            )
            if response:
                self._token_tracker.record(response)
        except Exception:
            return {}

        content = getattr(response, "content", None) if response else None
        if not content or not isinstance(content, str):
            return {}

        # Parse the response into per-stage contexts
        import re

        result: dict[int, str] = {}
        parts = re.split(r"\n(?=## Stage \d+)", content)
        for part in parts:
            m = re.match(r"## Stage (\d+)", part)
            if m:
                stage_num = int(m.group(1))
                result[stage_num] = part.strip()

        # Cache in build state
        self._build_state._state["stage_contexts"] = {str(k): v for k, v in result.items()}
        self._build_state.save()

        return result

    # ------------------------------------------------------------------ #
    # Internal — stage generation
    # ------------------------------------------------------------------ #

    def _select_agent(self, stage: dict) -> Any | None:
        """Select the appropriate agent for a build stage category."""
        category = stage.get("category", "infra")
        if category in ("infra", "data", "integration"):
            return self._iac_agents.get(self._iac_tool)
        elif category in ("app", "schema", "cicd", "external"):
            return self._dev_agent
        elif category == "docs":
            return self._doc_agent
        else:
            return self._iac_agents.get(self._iac_tool) or self._dev_agent

    def _build_stage_task(
        self,
        stage: dict,
        architecture: str,
        templates: list,
    ) -> tuple[Any | None, str]:
        """Build the task prompt for a stage and select the appropriate agent.

        Returns ``(agent, task_prompt)`` or ``(None, "")`` when no
        suitable agent is available.
        """
        category = stage.get("category", "infra")
        stage_name = stage["name"]
        services = stage.get("services", [])

        # Select agent based on category
        if category in ("infra", "data", "integration"):
            agent = self._iac_agents.get(self._iac_tool)
        elif category in ("app", "schema", "cicd", "external"):
            agent = self._dev_agent
        elif category == "docs":
            agent = self._doc_agent
        else:
            agent = self._iac_agents.get(self._iac_tool) or self._dev_agent

        if not agent:
            return None, ""

        # Service list for the prompt
        svc_lines = "\n".join(
            f"- {s.get('computed_name') or s.get('name', '?')}: "
            f"{s.get('resource_type', 'N/A')} (SKU: {s.get('sku') or 'n/a'})"
            for s in services
        )

        # Template context (only services relevant to this stage)
        template_context = ""
        if templates:
            for t in templates:
                stage_svc_names = {s.get("name", "") for s in services}
                matching = [s for s in t.services if s.name in stage_svc_names]
                if matching:
                    template_context += f"\nTemplate reference ({t.display_name}):\n"
                    for s in matching:
                        template_context += f"  - {s.name} ({s.type}, tier={s.tier})\n"
                        if s.config:
                            for k, v in s.config.items():
                                template_context += f"    {k}: {v}\n"

        # Cross-references to previously generated stages
        prev_stages = self._build_state.get_generated_stages()
        prev_context = ""
        if prev_stages:
            prev_context = "\n## Previously Generated Stages\n"
            prev_context += (
                "Use terraform_remote_state (Terraform) or parameter inputs (Bicep) to "
                "reference resources from these stages. NEVER hardcode their resource names.\n"
            )
            for ps in prev_stages:
                prev_svcs = ps.get("services", [])
                prev_names = [s.get("computed_name") or s.get("name") for s in prev_svcs]
                names_str = ", ".join(prev_names) if prev_names else "none"
                prev_context += f"- Stage {ps['stage']}: {ps['name']} (resources: {names_str})\n"

        naming_instructions = self._naming.to_prompt_instructions()
        stage_dir = stage.get("dir", "concept")

        # Build the task prompt
        is_iac = category in ("infra", "data", "integration")
        tool_label = f" {self._iac_tool}" if is_iac else ""

        task = (
            f"Generate{tool_label} code for deployment "
            f"Stage {stage['stage']}: {stage_name}.\n\n"
            f"## Architecture Context\n{architecture}\n\n"
            f"## This Stage\n"
            f"Name: {stage_name}\n"
            f"Category: {category}\n"
            f"Output directory: {stage_dir}/\n\n"
        )

        if svc_lines:
            task += f"## Services in This Stage\n{svc_lines}\n\n"

        if template_context:
            task += f"## Template Configuration\n{template_context}\n\n"

        if prev_context:
            task += prev_context + "\n"

        task += f"## Naming Convention\n{naming_instructions}\n\n"

        task += (
            "## Requirements\n"
            "- Use managed identity (NO connection strings or access keys)\n"
            "- Include proper resource tagging\n"
            "- Follow the naming convention exactly\n"
            "- Reference outputs from prior stages via terraform_remote_state (Terraform) or "
            "parameters (Bicep) — NEVER hardcode resource names from other stages\n"
            f"- All files should be relative to {stage_dir}/\n"
            "- outputs.tf/outputs MUST export ALL resource names, IDs, endpoints, "
            "and managed identity IDs needed by downstream stages\n"
            "- If ANY service disables local/key auth, you MUST also create managed identity "
            "+ RBAC role assignments in the SAME stage\n"
            "- Do NOT output sensitive values (keys, connection strings) — "
            "omit them entirely when local auth is disabled\n"
            "- deploy.sh MUST be complete and syntactically valid — never truncate it\n"
            "- deploy.sh MUST include: set -euo pipefail, Azure login check, "
            "error handling (trap), output export to JSON\n"
        )

        # Terraform-specific file structure rules
        if is_iac and self._iac_tool == "terraform":
            task += (
                "\n## Terraform File Structure (MANDATORY)\n"
                "Generate ONLY these files:\n"
                "- providers.tf — terraform {}, required_providers "
                '{ azapi = { source = "azure/azapi", version pinned } }, '
                "backend {}, provider config. "
                "This is the ONLY file that may contain a terraform {} block.\n"
                "- main.tf — resource definitions ONLY. No terraform {} or provider {} blocks.\n"
                "- variables.tf — all input variable declarations\n"
                "- outputs.tf — all output value declarations\n"
                "- locals.tf — computed local values (if needed)\n"
                "- deploy.sh — deployment script\n"
                "- Additional service-specific files (e.g. identity.tf, networking.tf) are allowed.\n\n"
                "DO NOT create versions.tf. It will be rejected.\n"
                "Every .tf file must be syntactically complete — every opened block must be closed in the SAME file.\n"
            )
        elif is_iac and self._iac_tool == "bicep":
            task += "- Use consistent deployment naming (Bicep)\n"

        # Inject app-type scaffolding requirements when applicable
        scaffolding = self._get_app_scaffolding_requirements(stage)
        if scaffolding:
            task += scaffolding

        # Inject governor brief as high-priority constraints (near the end
        # of the prompt where models pay the most attention).
        governor_brief = getattr(agent, "_governor_brief", "")
        if governor_brief:
            task += (
                "\n## MANDATORY GOVERNANCE RULES (FAILURE TO COMPLY WILL REJECT THE BUILD)\n"
                f"{governor_brief}\n"
                "Violating any MUST rule above will cause the build to fail and require regeneration. "
                "Generate code that complies with ALL listed rules.\n"
            )

        task += (
            "\n## Output Format\n"
            "Wrap EACH generated file in a fenced code block whose label is "
            "the filename (not the language). Example:\n\n"
            "```main.tf\n"
            "# terraform code here\n"
            "```\n\n"
            "```variables.tf\n"
            "# variables here\n"
            "```\n\n"
            "Use short filenames (main.tf, variables.tf, outputs.tf, etc.) — "
            "do NOT include the directory path in the label.\n"
        )

        return agent, task

    @staticmethod
    def _get_app_scaffolding_requirements(stage: dict) -> str:
        """Return scaffolding file requirements for application stages.

        Examines the services in a stage and returns explicit instructions
        listing the project files that *must* be generated for a complete,
        compilable application.  Returns an empty string for non-app stages.
        """
        category = stage.get("category", "infra")
        if category not in ("app", "schema", "external"):
            return ""

        services = stage.get("services", [])
        service_types = {s.get("resource_type", "").lower() for s in services}
        service_names = {s.get("name", "").lower() for s in services}

        # Detect Azure Functions (by resource type or name heuristic)
        is_functions = any("function" in t for t in service_types) or any("function" in n for n in service_names)

        # Detect web/container apps
        is_webapp = any(
            t for t in service_types if "containerapp" in t or "web/site" in t or "app-service" in t
        ) or any(n for n in service_names if "container-app" in n or "web-app" in n or "app-service" in n)

        if is_functions:
            return (
                "\n## Required Project Files\n"
                "This stage MUST generate a complete, compilable project. "
                "Include ALL of these files:\n"
                "- .csproj project file with all NuGet PackageReferences "
                "(Microsoft.Azure.Functions.Worker, Microsoft.Azure.Functions.Worker.Sdk, etc.)\n"
                "- Program.cs with HostBuilder, DI registration for all services/interfaces\n"
                "- host.json (Azure Functions host configuration, version 2.0 with extensionBundle)\n"
                "- local.settings.json (local development settings with FUNCTIONS_WORKER_RUNTIME "
                "and all required config keys)\n"
                "- All model/DTO classes referenced by function and service code "
                "(e.g. Project.cs, User.cs, Draft.cs)\n\n"
                "Every type referenced in the code must be defined in a generated file. "
                "Do not generate service files that reference undefined classes.\n"
                "Use the .NET isolated worker model (Microsoft.Azure.Functions.Worker), "
                "NOT the in-process model.\n"
            )
        elif is_webapp:
            return (
                "\n## Required Project Files\n"
                "This stage MUST generate a complete, compilable project. "
                "Include ALL of these files:\n"
                "- .csproj project file with all NuGet PackageReferences\n"
                "- Program.cs with full DI registration for all services\n"
                "- appsettings.json with all configuration keys\n"
                "- Dockerfile for containerized deployment\n"
                "- All model/DTO classes referenced by controllers/services "
                "(e.g. Project.cs, User.cs)\n\n"
                "Every type referenced in the code must be defined in a generated file. "
                "Do not generate service files that reference undefined classes.\n"
            )
        else:
            return (
                "\n## Required Project Files\n"
                "This stage MUST generate a complete, compilable project. "
                "Include ALL of these files:\n"
                "- Project/build file (e.g. .csproj, package.json, requirements.txt)\n"
                "- Entry point (e.g. Program.cs, main.py, index.ts)\n"
                "- Dependency manifest with all required packages\n"
                "- All model/DTO classes referenced by service code\n\n"
                "Every type referenced in the code must be defined in a generated file. "
                "Do not generate service files that reference undefined classes.\n"
            )

    # Files that should never be written for each IaC tool.
    # The AI occasionally generates these despite prompt instructions.
    _BLOCKED_FILES: dict[str, set[str]] = {
        "terraform": {"versions.tf"},
    }

    def _write_stage_files(self, stage: dict, content: str) -> list[str]:
        """Extract file blocks from AI response and write to disk.

        Filters out blocked filenames (e.g. ``versions.tf`` for Terraform)
        before writing.

        Returns a list of written file paths relative to the project dir.
        """
        if not content:
            return []

        files = parse_file_blocks(content)
        if not files:
            return []

        stage_dir = stage.get("dir", "concept")
        output_dir = Path(self._context.project_dir) / stage_dir
        blocked = self._BLOCKED_FILES.get(self._iac_tool, set())

        # Strip stage_dir prefix from filenames to avoid path duplication.
        # The AI sometimes includes the full output path in the code block
        # label (e.g. "concept/infra/terraform/stage-1/main.tf") even though
        # we already prepend stage_dir when writing.
        cleaned: dict[str, str] = {}
        for filename, file_content in files.items():
            normalized = filename.replace("\\", "/")
            stage_prefix = stage_dir.replace("\\", "/")
            if normalized.startswith(stage_prefix + "/"):
                normalized = normalized[len(stage_prefix) + 1 :]
            elif normalized.startswith(stage_prefix):
                normalized = normalized[len(stage_prefix) :]
            normalized = normalized or filename

            # Drop blocked files (e.g. versions.tf)
            if normalized in blocked:
                logger.info("Dropped blocked file: %s (IaC tool: %s)", normalized, self._iac_tool)
                continue

            cleaned[normalized] = file_content

        written = write_parsed_files(cleaned, output_dir, verbose=False)

        project_root = Path(self._context.project_dir)
        return [str(p.relative_to(project_root)) for p in written]

    # ------------------------------------------------------------------ #
    # Internal — review loop helpers
    # ------------------------------------------------------------------ #

    def _identify_affected_stages(self, feedback: str) -> list[int]:
        """Identify which stages are affected by user feedback.

        When an architect agent is available, asks it to semantically
        match feedback to stages.  Falls back to regex/name matching
        when no architect is available or parsing fails.
        """
        # Try architect-based identification first
        if self._architect_agent and self._context.ai_provider:
            result = self._identify_stages_via_architect(feedback)
            if result:
                return result

        return self._identify_stages_regex(feedback)

    def _identify_stages_via_architect(self, feedback: str) -> list[int]:
        """Ask the architect agent to identify affected stages from feedback."""
        assert self._architect_agent is not None
        stages = self._build_state._state.get("deployment_stages", [])
        if not stages:
            return []

        stage_info = json.dumps(
            [
                {
                    "stage": s["stage"],
                    "name": s["name"],
                    "services": [svc.get("name", "") for svc in s.get("services", [])],
                }
                for s in stages
            ],
            indent=2,
        )

        task = (
            "Given the following deployment stages and user feedback, "
            "identify which stages are affected.\n\n"
            f"## Stages\n```json\n{stage_info}\n```\n\n"
            f"## User Feedback\n{feedback}\n\n"
            "Return ONLY a JSON array of affected stage numbers. "
            "Example: [1, 3]\n"
        )

        try:
            response = self._architect_agent.execute(self._context, task)
            if response:
                self._token_tracker.record(response)
            if response and response.content:
                return self._parse_stage_numbers(response.content)
        except Exception:
            logger.debug("Architect stage identification failed", exc_info=True)

        return []

    @staticmethod
    def _parse_stage_numbers(content: str) -> list[int]:
        """Parse a JSON array of stage numbers from AI response."""
        text = content.strip()
        # Try to extract JSON array
        json_match = re.search(r"\[[\d,\s]+\]", text)
        if json_match:
            try:
                nums = json.loads(json_match.group())
                if isinstance(nums, list) and all(isinstance(n, int) for n in nums):
                    return sorted(set(nums))
            except (json.JSONDecodeError, TypeError):
                pass
        return []

    def _identify_stages_regex(self, feedback: str) -> list[int]:
        """Identify affected stages using regex and name matching (fallback)."""
        affected: list[int] = []
        lower = feedback.lower()

        # Explicit stage numbers (e.g. "stage 3", "stage3")
        for match in re.finditer(r"stage\s*(\d+)", lower):
            num = int(match.group(1))
            if self._build_state.get_stage(num):
                affected.append(num)

        if affected:
            return sorted(set(affected))

        # Service or stage name mentions
        for stage in self._build_state._state.get("deployment_stages", []):
            stage_name = stage["name"].lower()
            if stage_name in lower:
                affected.append(stage["stage"])
                continue
            for svc in stage.get("services", []):
                svc_name = svc.get("name", "").lower()
                if svc_name and svc_name in lower:
                    affected.append(stage["stage"])
                    break

        if affected:
            return sorted(set(affected))

        # Last resort: regenerate all generated stages
        return [
            s["stage"]
            for s in self._build_state._state.get("deployment_stages", [])
            if s.get("status") in ("generated", "accepted")
        ]

    # ------------------------------------------------------------------ #
    # Internal — slash commands
    # ------------------------------------------------------------------ #

    def _handle_slash_command(self, command: str, _print: Callable) -> None:
        """Handle build-session slash commands."""
        parts = command.strip().split(maxsplit=1)
        cmd = parts[0]
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/status", "/stages"):
            _print("")
            _print(self._build_state.format_stage_status())
            _print("")
        elif cmd == "/files":
            _print("")
            _print(self._build_state.format_files_list())
            _print("")
        elif cmd == "/policy":
            _print("")
            _print(self._build_state.format_policy_summary())
            _print("")
        elif cmd == "/describe":
            self._handle_describe(arg, _print)
        elif cmd == "/help":
            _print("")
            _print("Available commands:")
            _print("  /status      - Show stage completion summary")
            _print("  /stages      - Show full deployment plan")
            _print("  /files       - List all generated files")
            _print("  /policy      - Show policy check summary")
            _print("  /describe N  - Show details for stage N")
            _print("  /help        - Show this help")
            _print("  done         - Accept build and exit")
            _print("  quit         - Cancel and exit")
            _print("")
            _print("  You can also use natural language:")
            _print("    'what's the build status'   instead of  /status")
            _print("    'show the generated files'  instead of  /files")
            _print("    'describe stage 2'          instead of  /describe 2")
            _print("")

    def _handle_describe(self, arg: str, _print: Callable) -> None:
        """Show detailed description of a build stage."""
        if not arg or not arg.strip():
            _print("  Usage: /describe N (stage number)")
            return

        numbers = re.findall(r"\d+", arg)
        if not numbers:
            _print("  Usage: /describe N (stage number)")
            return

        stage_num = int(numbers[0])
        stage = self._build_state.get_stage(stage_num)
        if not stage:
            _print(f"  Stage {stage_num} not found.")
            return

        _print("")
        _print(f"  Stage {stage_num}: {stage.get('name', '?')}")
        _print(f"  Category: {stage.get('category', '?')}")
        _print(f"  Status:   {stage.get('status', 'pending')}")
        _print(f"  Dir:      {stage.get('dir', '?')}")

        services = stage.get("services", [])
        if services:
            _print(f"  Resources ({len(services)}):")
            for svc in services:
                name = svc.get("computed_name") or svc.get("name", "?")
                rtype = svc.get("resource_type", "")
                sku = svc.get("sku", "")
                line = f"    - {name}"
                if rtype:
                    line += f"  ({rtype})"
                if sku:
                    line += f"  [{sku}]"
                _print(line)

        files = stage.get("files", [])
        if files:
            _print(f"  Files ({len(files)}):")
            for f in files:
                _print(f"    - {f}")
        _print("")

    # ------------------------------------------------------------------ #
    # Internal — utilities
    # ------------------------------------------------------------------ #

    def _collect_stage_file_content(self, stage: dict, max_bytes: int = 20_000) -> str:
        """Collect content of generated files for a single stage."""
        project_root = Path(self._context.project_dir)
        parts: list[str] = []
        total = 0

        files = stage.get("files", [])
        if not files:
            return ""

        for filepath in files:
            if total >= max_bytes:
                parts.append("\n(remaining files omitted — size cap reached)")
                break

            full_path = project_root / filepath
            try:
                content = full_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                parts.append(f"```{filepath}\n(could not read file)\n```")
                continue

            per_file_cap = 8_000
            if len(content) > per_file_cap:
                content = content[:per_file_cap] + "\n... (truncated)"

            block = f"```{filepath}\n{content}\n```"
            total += len(block)
            parts.append(block)

        return "\n\n".join(parts)

    def _run_stage_qa(
        self,
        stage: dict,
        architecture: str,
        templates: list,
        use_styled: bool,
        _print: Callable,
    ) -> None:
        """Run QA review + remediation loop for a single generated stage."""
        if not self._qa_agent:
            return

        stage_num = stage["stage"]
        orchestrator = AgentOrchestrator(self._registry, self._context)

        for attempt in range(_MAX_STAGE_REMEDIATION_ATTEMPTS + 1):
            # 1. Collect this stage's files
            file_content = self._collect_stage_file_content(stage)
            if not file_content:
                return

            # 2. Build QA task
            if attempt == 0:
                qa_task = (
                    f"Review the generated code for Stage {stage_num}: {stage['name']} "
                    "using your Mandatory Review Checklist. "
                    "Flag any issues — missing managed identity config, hardcoded secrets, "
                    "undefined references, missing outputs, incomplete scripts, etc.\n\n"
                    "Provide specific fixes (corrected file contents) for each issue.\n\n"
                    f"## Stage {stage_num} Files\n\n{file_content}"
                )
            else:
                qa_task = (
                    f"Re-review the REMEDIATED code for Stage {stage_num}: {stage['name']}. "
                    "Report ONLY remaining issues that were NOT fixed.\n\n"
                    f"## Stage {stage_num} Files\n\n{file_content}"
                )

            # 3. Run QA
            with self._maybe_spinner(f"QA reviewing Stage {stage_num}...", use_styled):
                qa_result = orchestrator.delegate(
                    from_agent="build-session",
                    to_agent_name=self._qa_agent.name,
                    sub_task=qa_task,
                )
            if qa_result:
                self._token_tracker.record(qa_result)

            qa_content = qa_result.content if qa_result else ""

            # 4. Check if issues found
            has_issues = qa_content and any(
                kw in qa_content.lower() for kw in ["critical", "error", "missing", "fix", "issue", "broken"]
            )

            if not has_issues:
                _print(f"       Stage {stage_num} passed QA.")
                return

            # 5. If at max attempts, report and move on
            if attempt >= _MAX_STAGE_REMEDIATION_ATTEMPTS:
                _print(f"       Stage {stage_num}: QA issues remain after {attempt} remediation(s). Proceeding.")
                if qa_content:
                    _print(f"       Remaining: {qa_content[:200]}")
                return

            # 6. Remediate — re-invoke IaC agent with focused context + governance
            _print(f"       Stage {stage_num}: QA found issues — remediating (attempt {attempt + 1})...")

            # Use condensed stage context (cached from one-time condensation)
            cached_contexts = self._build_state._state.get("stage_contexts", {})
            focused = cached_contexts.get(str(stage_num), "")
            agent, task = self._build_stage_task(stage, focused, templates)
            if not agent:
                return

            # Escalating governance severity per attempt
            self._apply_governor_brief(agent, stage["name"], stage.get("services", []))
            if attempt == 0:
                severity = "You MUST address ALL of them"
            elif attempt == 1:
                severity = (
                    "CRITICAL: The previous generation VIOLATED governance policies. "
                    "You MUST comply with every rule in the MANDATORY GOVERNANCE RULES section"
                )
            else:
                severity = (
                    "FINAL ATTEMPT: Previous generations repeatedly violated governance. "
                    "This build WILL BE REJECTED if any MUST rule is violated. "
                    "Comply with EVERY governance rule or the build fails permanently"
                )

            task += (
                f"\n\n## QA Review Findings ({severity})\n"
                "The QA engineer found the following issues:\n\n"
                f"{qa_content}\n"
            )

            with self._maybe_spinner(f"Remediating Stage {stage_num} (attempt {attempt + 1})...", use_styled):
                response = agent.execute(self._context, task)

            if response:
                self._token_tracker.record(response)
            content = response.content if response else ""
            written_paths = self._write_stage_files(stage, content)
            self._build_state.mark_stage_generated(stage_num, written_paths, agent.name)

    def _collect_generated_file_content(self, max_bytes: int = 50_000) -> str:
        """Collect content of all generated files for QA review.

        Iterates generated stages, reads each file from disk, and builds
        a formatted string with fenced code blocks.  Applies *max_bytes*
        cap to avoid blowing the context window — individual large files
        are truncated and collection stops once the cap is reached.
        """
        project_root = Path(self._context.project_dir)
        parts: list[str] = []
        total = 0

        for stage in self._build_state.get_generated_stages():
            stage_num = stage["stage"]
            stage_name = stage["name"]
            category = stage.get("category", "infra")
            files = stage.get("files", [])
            if not files:
                continue

            header = f"### Stage {stage_num}: {stage_name} ({category})"
            parts.append(header)
            total += len(header)

            for filepath in files:
                if total >= max_bytes:
                    parts.append("\n(remaining files omitted — size cap reached)")
                    return "\n\n".join(parts)

                full_path = project_root / filepath
                try:
                    content = full_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    parts.append(f"```{filepath}\n(could not read file)\n```")
                    continue

                # Truncate individual large files
                per_file_cap = 8_000
                if len(content) > per_file_cap:
                    content = content[:per_file_cap] + "\n... (truncated)"

                block = f"```{filepath}\n{content}\n```"
                total += len(block)
                parts.append(block)

        return "\n\n".join(parts)

    @contextmanager
    def _maybe_spinner(self, message: str, use_styled: bool, *, status_fn: Callable | None = None) -> Iterator[None]:
        """Show a spinner/status when using styled output or TUI."""
        _sfn = status_fn or self._status_fn
        if use_styled:
            with self._console.spinner(message):
                yield
        elif _sfn:
            _sfn(message, "start")
            try:
                yield
            finally:
                _sfn(message, "end")
                # Push token counts to replace the final elapsed time
                token_text = self._token_tracker.format_status()
                if token_text:
                    _sfn(token_text, "tokens")
        else:
            yield
