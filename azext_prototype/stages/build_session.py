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
_SLASH_COMMANDS = frozenset({"/status", "/stages", "/files", "/policy", "/help"})

# Maximum remediation cycles per stage before proceeding
_MAX_STAGE_REMEDIATION_ATTEMPTS = 2


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
    ) -> None:
        self._context = agent_context
        self._registry = registry
        self._console = console or default_console
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

        # Token tracker
        self._token_tracker = TokenTracker()

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

        # ---- Phase 2: Derive deployment plan ----
        existing_stages = self._build_state._state.get("deployment_stages", [])
        if not existing_stages:
            _print("Deriving deployment plan...")
            _print("")

            with self._maybe_spinner("Analyzing architecture for deployment stages...", use_styled):
                stages = self._derive_deployment_plan(architecture, templates)

            if not stages:
                _print("Could not derive deployment plan from architecture.")
                return BuildResult(cancelled=True)

            self._build_state.set_deployment_plan(stages)
        else:
            _print("Resuming from existing deployment plan.")
            _print("")

        # Present the plan
        _print(self._build_state.format_stage_status())
        _print("")
        _print("Review the deployment plan above.")
        _print("Press Enter to start building, or provide feedback to adjust.")
        _print("")

        try:
            if use_styled:
                confirmation = self._prompt.simple_prompt("> ")
            else:
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

        # ---- Phase 3: Staged generation ----
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
            _print(f"[{generated_count}/{total_stages}] Stage {stage_num}: {stage_name}")
            if svc_display:
                _print(f"       Resources: {svc_display}")

            agent, task = self._build_stage_task(stage, architecture, templates)
            if not agent:
                _print(f"       Skipped (no agent for category '{category}')")
                continue

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
                continue

            if response:
                self._token_tracker.record(response)
            content = response.content if response else ""

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

            self._build_state.mark_stage_generated(stage_num, written_paths, agent.name)

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
        if scope == "all" and self._qa_agent:
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
            if lower in _SLASH_COMMANDS:
                self._handle_slash_command(lower, _print)
                continue

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
            normalised.append(
                {
                    "stage": s.get("stage", len(normalised) + 1),
                    "name": s.get("name", f"Stage {len(normalised) + 1}"),
                    "category": s.get("category", "infra"),
                    "dir": s.get("dir", ""),
                    "services": s.get("services", []),
                    "status": "pending",
                    "files": [],
                }
            )
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
    # Internal — stage generation
    # ------------------------------------------------------------------ #

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
        if command == "/status":
            _print("")
            _print(self._build_state.format_stage_status())
            _print("")
        elif command == "/stages":
            _print("")
            _print(self._build_state.format_stage_status())
            _print("")
        elif command == "/files":
            _print("")
            _print(self._build_state.format_files_list())
            _print("")
        elif command == "/policy":
            _print("")
            _print(self._build_state.format_policy_summary())
            _print("")
        elif command == "/help":
            _print("")
            _print("Available commands:")
            _print("  /status  - Show stage completion summary")
            _print("  /stages  - Show full deployment plan")
            _print("  /files   - List all generated files")
            _print("  /policy  - Show policy check summary")
            _print("  /help    - Show this help")
            _print("  done     - Accept build and exit")
            _print("  quit     - Cancel and exit")
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

            # 6. Remediate — re-invoke IaC agent with QA findings
            _print(f"       Stage {stage_num}: QA found issues — remediating (attempt {attempt + 1})...")

            agent, task = self._build_stage_task(stage, architecture, templates)
            if not agent:
                return

            task += (
                "\n\n## QA Review Findings (MUST FIX)\n"
                "The QA engineer found the following issues. "
                "You MUST address ALL of them:\n\n"
                f"{qa_content}\n"
            )

            with self._maybe_spinner(f"Remediating Stage {stage_num}...", use_styled):
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
    def _maybe_spinner(self, message: str, use_styled: bool) -> Iterator[None]:
        """Show a spinner when using styled output, otherwise no-op."""
        if use_styled:
            with self._console.spinner(message):
                yield
        else:
            yield
