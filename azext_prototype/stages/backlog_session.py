"""Interactive backlog session — conversational backlog generation and push.

Follows the :class:`~.build_session.BuildSession` pattern: bordered prompts,
progress indicators, slash commands, and a review loop.

Phases:

1. **Load context** — Load design context, scope, and existing backlog state
2. **Generate** — If no cached items or ``--refresh``, call project-manager
   agent for structured decomposition
3. **Review/Refine loop** — Conversational back-and-forth where the user can
   add, update, and delete items via natural language or slash commands
4. **Push** — On ``/push``, create work items in GitHub or Azure DevOps
5. **Report** — Display links to created work items
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from azext_prototype.agents.base import AgentCapability, AgentContext
from azext_prototype.agents.registry import AgentRegistry
from azext_prototype.ai.token_tracker import TokenTracker
from azext_prototype.stages.backlog_push import (
    check_devops_ext,
    check_gh_auth,
    push_devops_feature,
    push_devops_story,
    push_github_issue,
)
from azext_prototype.stages.backlog_state import BacklogState
from azext_prototype.stages.escalation import EscalationTracker
from azext_prototype.stages.qa_router import route_error_to_qa
from azext_prototype.ui.console import Console, DiscoveryPrompt
from azext_prototype.ui.console import console as default_console

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------- #
# Sentinels
# -------------------------------------------------------------------- #

_QUIT_WORDS = frozenset({"q", "quit", "exit"})
_DONE_WORDS = frozenset({"done", "finish", "accept", "lgtm"})
_SLASH_COMMANDS = frozenset(
    {
        "/list",
        "/show",
        "/add",
        "/remove",
        "/preview",
        "/save",
        "/push",
        "/status",
        "/help",
        "/quit",
    }
)


# -------------------------------------------------------------------- #
# BacklogResult — public interface consumed by custom.py
# -------------------------------------------------------------------- #


class BacklogResult:
    """Result of a backlog session."""

    __slots__ = (
        "items_generated",
        "items_pushed",
        "items_failed",
        "push_urls",
        "cancelled",
    )

    def __init__(
        self,
        items_generated: int = 0,
        items_pushed: int = 0,
        items_failed: int = 0,
        push_urls: list[str] | None = None,
        cancelled: bool = False,
    ) -> None:
        self.items_generated = items_generated
        self.items_pushed = items_pushed
        self.items_failed = items_failed
        self.push_urls = push_urls or []
        self.cancelled = cancelled


# -------------------------------------------------------------------- #
# BacklogSession
# -------------------------------------------------------------------- #


class BacklogSession:
    """Interactive, multi-phase backlog conversation.

    Manages the full backlog lifecycle: AI generation, review/refinement,
    push to provider, and reporting.

    Parameters
    ----------
    agent_context:
        Runtime context with AI provider and project config.
    registry:
        Agent registry for resolving specialised agents.
    console:
        Styled console for output.
    backlog_state:
        Pre-initialised backlog state (for re-entrant sessions).
    """

    def __init__(
        self,
        agent_context: AgentContext,
        registry: AgentRegistry,
        *,
        console: Console | None = None,
        backlog_state: BacklogState | None = None,
    ) -> None:
        self._context = agent_context
        self._registry = registry
        self._console = console or default_console
        self._prompt = DiscoveryPrompt(self._console)
        self._backlog_state = backlog_state or BacklogState(agent_context.project_dir)

        # Token tracker
        self._token_tracker = TokenTracker()

        # Resolve project-manager agent
        pm_agents = registry.find_by_capability(AgentCapability.BACKLOG_GENERATION)
        self._pm_agent = pm_agents[0] if pm_agents else None

        # Resolve QA agent for error routing
        qa_agents = registry.find_by_capability(AgentCapability.QA)
        self._qa_agent = qa_agents[0] if qa_agents else None

        # Escalation tracker
        self._escalation_tracker = EscalationTracker(agent_context.project_dir)
        if self._escalation_tracker.exists:
            self._escalation_tracker.load()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def run(
        self,
        *,
        design_context: str,
        scope: dict | None = None,
        provider: str = "github",
        org: str = "",
        project: str = "",
        refresh: bool = False,
        quick: bool = False,
        input_fn: Callable[[str], str] | None = None,
        print_fn: Callable[[str], None] | None = None,
    ) -> BacklogResult:
        """Run the interactive backlog session.

        Parameters
        ----------
        design_context:
            The architecture design text.
        scope:
            Discovery scope dict with in_scope/out_of_scope/deferred.
        provider:
            Target provider ('github' or 'devops').
        org / project:
            Target org and project/repo.
        refresh:
            Force regeneration even if cached items exist.
        quick:
            Skip interactive loop; generate → confirm → push.
        input_fn / print_fn:
            Injectable I/O for testing.

        Returns
        -------
        BacklogResult
        """
        use_styled = input_fn is None and print_fn is None
        _input = input_fn or (lambda p: self._prompt.prompt(p))
        _print = print_fn or self._console.print

        # Store provider info
        self._backlog_state._state["provider"] = provider
        self._backlog_state._state["org"] = org
        self._backlog_state._state["project"] = project

        # ---- Phase 1: Load or generate ----
        existing_items = self._backlog_state._state.get("items", [])
        has_cache = existing_items and not refresh and self._backlog_state.matches_context(design_context, scope)

        if has_cache:
            _print("")
            _print("  Backlog Session (resumed)")
            _print("  " + "=" * 40)
            _print("")
            _print(f"  Loaded {len(existing_items)} cached item(s).")
            _print("")
        else:
            # ---- Phase 2: Generate ----
            _print("")
            _print("  Backlog Session")
            _print("  " + "=" * 40)
            _print("")

            if not self._pm_agent:
                _print("  No project-manager agent available.")
                return BacklogResult(cancelled=True)

            if not self._context.ai_provider:
                _print("  No AI provider configured.")
                return BacklogResult(cancelled=True)

            with self._maybe_spinner("Generating backlog from architecture...", use_styled):
                items = self._generate_items(design_context, scope, provider)

            if not items:
                route_error_to_qa(
                    "AI returned no parseable backlog items",
                    "Backlog generation",
                    self._qa_agent,
                    self._context,
                    self._token_tracker,
                    _print,
                )
                _print("  Could not generate backlog items.")
                return BacklogResult(cancelled=True)

            self._backlog_state.set_items(items)
            self._backlog_state.set_context_hash(design_context, scope)
            self._backlog_state.save()
            if use_styled:
                self._console.print_token_status(self._token_tracker.format_status())

        # Display summary
        _print(self._backlog_state.format_backlog_summary())
        _print("")

        items = self._backlog_state._state.get("items", [])
        items_count = len(items)

        # ---- Quick mode: confirm and push ----
        if quick:
            return self._run_quick_mode(
                provider,
                org,
                project,
                items_count,
                _input,
                _print,
                use_styled,
            )

        # ---- Phase 3: Interactive review/refine loop ----
        _print("  Review the backlog above. Use slash commands to manage items.")
        _print("  Type 'done' to finish, '/push' to create work items, '/help' for commands.")
        _print("")

        exchange = 0
        while True:
            try:
                if use_styled:
                    user_input = self._prompt.prompt(
                        "> ",
                        instruction="Type '/help' for commands, 'done' to finish.",
                        show_quit_hint=True,
                    )
                else:
                    user_input = _input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                return BacklogResult(
                    items_generated=items_count,
                    cancelled=True,
                )

            if not user_input:
                continue

            lower = user_input.lower().strip()

            # Quit
            if lower in _QUIT_WORDS or lower == "/quit":
                return BacklogResult(
                    items_generated=items_count,
                    cancelled=True,
                )

            # Done
            if lower in _DONE_WORDS:
                break

            # Slash commands
            if lower.startswith("/"):
                handled = self._handle_slash_command(
                    lower,
                    provider,
                    org,
                    project,
                    _input,
                    _print,
                    use_styled,
                )
                if handled == "pushed":
                    break
                continue

            # Natural language — send to AI for item mutation
            exchange += 1
            with self._maybe_spinner("Updating backlog...", use_styled):
                updated = self._mutate_items(user_input, design_context)

            if updated is not None:
                self._backlog_state.set_items(updated)
                self._backlog_state.update_from_exchange(
                    user_input,
                    f"Updated {len(updated)} items",
                    exchange,
                )
                self._backlog_state.save()
                _print("")
                _print(self._backlog_state.format_backlog_summary())
                if use_styled:
                    self._console.print_token_status(self._token_tracker.format_status())
            else:
                _print("  Could not update items. Try being more specific.")

            _print("")

        # ---- Phase 5: Report ----
        pushed = self._backlog_state.get_pushed_items()
        failed = self._backlog_state.get_failed_items()
        items = self._backlog_state._state.get("items", [])

        push_urls = []
        for _, item in pushed:
            # URLs stored in push_results
            pass
        push_results = self._backlog_state._state.get("push_results", [])
        for r in push_results:
            if r and not str(r).startswith("error:"):
                push_urls.append(str(r))

        return BacklogResult(
            items_generated=len(items),
            items_pushed=len(pushed),
            items_failed=len(failed),
            push_urls=push_urls,
        )

    # ------------------------------------------------------------------ #
    # Quick mode
    # ------------------------------------------------------------------ #

    def _run_quick_mode(
        self,
        provider: str,
        org: str,
        project: str,
        items_count: int,
        _input: Callable,
        _print: Callable,
        use_styled: bool,
    ) -> BacklogResult:
        """Generate → confirm → push without interactive loop."""
        provider_label = "GitHub" if provider == "github" else "Azure DevOps"
        _print(f"  Push {items_count} item(s) to {provider_label} ({org}/{project})? (y/n)")

        try:
            if use_styled:
                confirm = self._prompt.simple_prompt("  > ")
            else:
                confirm = _input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            return BacklogResult(items_generated=items_count, cancelled=True)

        if confirm.lower() not in ("y", "yes"):
            _print("  Cancelled.")
            return BacklogResult(items_generated=items_count, cancelled=True)

        return self._push_all(provider, org, project, _print, use_styled)

    # ------------------------------------------------------------------ #
    # Item generation
    # ------------------------------------------------------------------ #

    def _generate_items(
        self,
        design_context: str,
        scope: dict | None,
        provider: str,
    ) -> list[dict]:
        """Call the project-manager agent to generate structured items."""
        from azext_prototype.ai.provider import AIMessage

        messages = self._pm_agent.get_system_messages()
        messages.extend(self._context.conversation_history)

        # Build scope context
        scope_text = ""
        if scope:
            in_scope = scope.get("in_scope", [])
            out_scope = scope.get("out_of_scope", [])
            deferred = scope.get("deferred", [])
            if in_scope:
                scope_text += "\n## In Scope\n" + "\n".join(f"- {s}" for s in in_scope)
            if out_scope:
                scope_text += "\n## Out of Scope (DO NOT include)\n" + "\n".join(f"- {s}" for s in out_scope)
            if deferred:
                scope_text += "\n## Deferred (separate 'Deferred / Future Work' epic)\n" + "\n".join(
                    f"- {s}" for s in deferred
                )

        task = (
            "Analyze the following architecture and produce a comprehensive "
            "backlog of work items.\n\n"
            "For each item provide:\n"
            "- epic (feature area grouping)\n"
            "- title\n"
            "- description (2-4 sentences explaining the purpose)\n"
            "- acceptance_criteria (numbered list)\n"
            "- tasks (concrete actionable sub-tasks)\n"
            "- effort estimate (S / M / L / XL)\n\n"
        )

        # Inject production backlog items from knowledge base
        production_items_text = self._get_production_items()
        if production_items_text:
            task += (
                "## Production Backlog Items (from knowledge base)\n"
                "The following production-readiness items were identified for "
                "the services in this architecture. Include them as stories in "
                "a 'Deferred / Future Work' epic:\n"
                f"{production_items_text}\n\n"
            )

        if scope_text:
            task += (
                "## Scope Boundaries\n"
                "Only create stories for in-scope items. "
                "Create a separate 'Deferred / Future Work' epic for deferred items. "
                "Do NOT create stories for out-of-scope items.\n"
                f"{scope_text}\n\n"
            )

        task += (
            "Respond ONLY with a JSON array. Each element:\n"
            "```\n"
            "{\n"
            '  "epic": "...",\n'
            '  "title": "...",\n'
            '  "description": "...",\n'
            '  "acceptance_criteria": ["AC1", "AC2"],\n'
            '  "tasks": ["Task 1", "Task 2"],\n'
            '  "effort": "M"\n'
            "}\n"
            "```\n\n"
            "No markdown, no explanation — only the JSON array.\n\n"
            f"## Architecture\n{design_context}"
        )

        messages.append(AIMessage(role="user", content=task))

        response = self._context.ai_provider.chat(
            messages,
            temperature=0.3,
            max_tokens=8192,
        )
        self._token_tracker.record(response)

        return self._parse_items(response.content)

    def _mutate_items(
        self,
        user_input: str,
        design_context: str,
    ) -> list[dict] | None:
        """Send user input to AI to mutate the current item list."""
        if not self._pm_agent or not self._context.ai_provider:
            return None

        from azext_prototype.ai.provider import AIMessage

        current_items = self._backlog_state._state.get("items", [])
        messages = self._pm_agent.get_system_messages()

        messages.append(
            AIMessage(
                role="user",
                content=(
                    "Here is the current backlog as JSON:\n"
                    f"```json\n{json.dumps(current_items, indent=2)}\n```\n\n"
                    f"User request: {user_input}\n\n"
                    "Apply the requested change and return the COMPLETE updated JSON array. "
                    "Return ONLY the JSON array, no explanation."
                ),
            )
        )

        response = self._context.ai_provider.chat(
            messages,
            temperature=0.2,
            max_tokens=8192,
        )
        self._token_tracker.record(response)

        return self._parse_items(response.content)

    @staticmethod
    def _parse_items(ai_output: str) -> list[dict]:
        """Parse the AI's JSON item list, tolerating markdown fences."""
        text = ai_output.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            items = json.loads(text)
            if isinstance(items, list):
                return items
        except json.JSONDecodeError:
            logger.warning("Could not parse backlog items from AI.")

        return []

    # ------------------------------------------------------------------ #
    # Push
    # ------------------------------------------------------------------ #

    def _push_all(
        self,
        provider: str,
        org: str,
        project: str,
        _print: Callable,
        use_styled: bool,
    ) -> BacklogResult:
        """Push all pending items to the target provider."""
        pending = self._backlog_state.get_pending_items()
        if not pending:
            _print("  No pending items to push.")
            items = self._backlog_state._state.get("items", [])
            pushed = self._backlog_state.get_pushed_items()
            return BacklogResult(
                items_generated=len(items),
                items_pushed=len(pushed),
            )

        # Auth check
        if provider == "github":
            if not check_gh_auth():
                _print("  GitHub CLI not authenticated. Run 'gh auth login' first.")
                return BacklogResult(
                    items_generated=len(self._backlog_state._state.get("items", [])),
                    cancelled=True,
                )
        else:
            if not check_devops_ext():
                _print("  Azure DevOps extension not available. Run 'az extension add --name azure-devops'.")
                return BacklogResult(
                    items_generated=len(self._backlog_state._state.get("items", [])),
                    cancelled=True,
                )

        _print(f"  Pushing {len(pending)} item(s)...")
        _print("")

        push_urls = []
        pushed_count = 0
        failed_count = 0

        for idx, item in pending:
            title = item.get("title", "Untitled")

            with self._maybe_spinner(f"Creating: {title}...", use_styled):
                if provider == "github":
                    result = push_github_issue(org, project, item)
                else:
                    result = push_devops_feature(org, project, item)

            if "error" in result:
                _print(f"    x {title}: {result['error']}")
                self._backlog_state.mark_item_failed(idx, result["error"])
                route_error_to_qa(
                    result["error"],
                    f"Backlog push: {title}",
                    self._qa_agent,
                    self._context,
                    self._token_tracker,
                    _print,
                )
                failed_count += 1
            else:
                url = result.get("url", "")
                _print(f"    v {title}: {url}")
                self._backlog_state.mark_item_pushed(idx, url)
                if url:
                    push_urls.append(url)
                pushed_count += 1

                # Push children for DevOps hierarchical items
                if provider == "devops":
                    parent_id = result.get("id")
                    children = item.get("children", [])
                    for child in children:
                        child_result = push_devops_story(
                            org,
                            project,
                            child,
                            parent_id=parent_id,
                        )
                        if "error" not in child_result:
                            child_url = child_result.get("url", "")
                            if child_url:
                                push_urls.append(child_url)

        _print("")
        _print(f"  Done: {pushed_count} pushed, {failed_count} failed")

        items = self._backlog_state._state.get("items", [])
        return BacklogResult(
            items_generated=len(items),
            items_pushed=pushed_count,
            items_failed=failed_count,
            push_urls=push_urls,
        )

    def _push_single(
        self,
        idx: int,
        provider: str,
        org: str,
        project: str,
        _print: Callable,
        use_styled: bool,
    ) -> None:
        """Push a single item by index."""
        items = self._backlog_state._state.get("items", [])
        if idx < 0 or idx >= len(items):
            _print(f"  Item {idx + 1} not found.")
            return

        item = items[idx]
        title = item.get("title", "Untitled")

        with self._maybe_spinner(f"Creating: {title}...", use_styled):
            if provider == "github":
                result = push_github_issue(org, project, item)
            else:
                result = push_devops_feature(org, project, item)

        if "error" in result:
            _print(f"  x {title}: {result['error']}")
            self._backlog_state.mark_item_failed(idx, result["error"])
        else:
            url = result.get("url", "")
            _print(f"  v {title}: {url}")
            self._backlog_state.mark_item_pushed(idx, url)

    # ------------------------------------------------------------------ #
    # Slash commands
    # ------------------------------------------------------------------ #

    def _handle_slash_command(
        self,
        command: str,
        provider: str,
        org: str,
        project: str,
        _input: Callable,
        _print: Callable,
        use_styled: bool,
    ) -> str | None:
        """Handle backlog session slash commands. Returns 'pushed' if push completes."""
        parts = command.split()
        cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/list":
            _print("")
            _print(self._backlog_state.format_backlog_summary())
            _print("")

        elif cmd == "/show":
            if arg and arg.isdigit():
                idx = int(arg) - 1
                _print("")
                _print(self._backlog_state.format_item_detail(idx))
                _print("")
            else:
                _print("  Usage: /show N (item number)")

        elif cmd == "/add":
            _print("  Describe the item to add:")
            try:
                if use_styled:
                    desc = self._prompt.simple_prompt("  > ")
                else:
                    desc = _input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                return None

            if desc:
                enriched = self._enrich_new_item(desc)
                items = self._backlog_state._state.get("items", [])
                items.append(enriched)
                self._backlog_state.set_items(items)
                _print(f"  Added item {len(items)}.")

        elif cmd == "/remove":
            if arg and arg.isdigit():
                idx = int(arg) - 1
                items = self._backlog_state._state.get("items", [])
                if 0 <= idx < len(items):
                    removed = items.pop(idx)
                    self._backlog_state.set_items(items)
                    _print(f"  Removed: {removed.get('title', 'item')}")
                else:
                    _print(f"  Item {idx + 1} not found.")
            else:
                _print("  Usage: /remove N (item number)")

        elif cmd == "/preview":
            _print("")
            items = self._backlog_state._state.get("items", [])
            provider_label = "GitHub Issues" if provider == "github" else "DevOps Work Items"
            _print(f"  Preview: {len(items)} {provider_label} → {org}/{project}")
            _print("")
            for i, item in enumerate(items):
                title = item.get("title", "Untitled")
                epic = item.get("epic", "")
                if provider == "github" and epic:
                    _print(f"    {i + 1}. [{epic}] {title}")
                else:
                    _print(f"    {i + 1}. {title}")
            _print("")

        elif cmd == "/save":
            self._save_backlog_md(_print)

        elif cmd == "/push":
            if arg and arg.isdigit():
                idx = int(arg) - 1
                self._push_single(idx, provider, org, project, _print, use_styled)
            else:
                result = self._push_all(provider, org, project, _print, use_styled)
                if result.items_pushed > 0:
                    return "pushed"

        elif cmd == "/status":
            _print("")
            push_status = self._backlog_state._state.get("push_status", [])
            items = self._backlog_state._state.get("items", [])
            for i, item in enumerate(items):
                title = item.get("title", "Untitled")
                status = push_status[i] if i < len(push_status) else "pending"
                icon = {"pending": "  ", "pushed": " v", "failed": " x"}.get(status, "  ")
                _print(f"  {icon} {i + 1}. {title} ({status})")
            _print("")

        elif cmd == "/help":
            _print("")
            _print("  Available commands:")
            _print("    /list       - Show all items grouped by epic")
            _print("    /show N     - Show item N with full details")
            _print("    /add        - Add a new item (AI-assisted)")
            _print("    /remove N   - Remove item N")
            _print("    /preview    - Show what will be pushed")
            _print("    /save       - Save to concept/docs/BACKLOG.md")
            _print("    /push       - Push all pending items to provider")
            _print("    /push N     - Push specific item N")
            _print("    /status     - Show push status per item")
            _print("    /help       - Show this help")
            _print("    /quit       - Exit session")
            _print("    done        - Finish session")
            _print("")
            _print("  Or type natural language to modify items:")
            _print("    'Add a story for API rate limiting'")
            _print("    'Update story 3 to include MFA'")
            _print("    'Remove story 7'")
            _print("")

        return None

    # ------------------------------------------------------------------ #
    # Item enrichment
    # ------------------------------------------------------------------ #

    def _enrich_new_item(self, description: str) -> dict:
        """Enrich a bare description into a structured backlog item.

        When the PM agent and AI provider are available, asks the PM to
        create a proper item with acceptance criteria and tasks.  Falls
        back to a bare item dict on failure.
        """
        bare = {
            "epic": "Added",
            "title": description,
            "description": description,
            "acceptance_criteria": [],
            "tasks": [],
            "effort": "M",
        }

        if not self._pm_agent or not self._context.ai_provider:
            return bare

        from azext_prototype.ai.provider import AIMessage

        messages = self._pm_agent.get_system_messages()
        messages.append(
            AIMessage(
                role="user",
                content=(
                    "Create a structured backlog item from this description:\n\n"
                    f"{description}\n\n"
                    "Return ONLY a JSON object with these fields:\n"
                    '{"epic": "...", "title": "...", "description": "...", '
                    '"acceptance_criteria": ["AC1", "AC2"], '
                    '"tasks": ["Task 1", "Task 2"], "effort": "S|M|L|XL"}\n'
                ),
            )
        )

        try:
            response = self._context.ai_provider.chat(
                messages,
                temperature=0.2,
                max_tokens=2048,
            )
            self._token_tracker.record(response)

            text = response.content.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                lines = [ln for ln in lines if not ln.strip().startswith("```")]
                text = "\n".join(lines)

            item = json.loads(text)
            if isinstance(item, dict) and "title" in item:
                # Ensure all expected keys exist
                item.setdefault("epic", "Added")
                item.setdefault("description", description)
                item.setdefault("acceptance_criteria", [])
                item.setdefault("tasks", [])
                item.setdefault("effort", "M")
                return item
        except Exception:
            logger.debug("PM enrichment failed, using bare item", exc_info=True)

        return bare

    # ------------------------------------------------------------------ #
    # Save to markdown
    # ------------------------------------------------------------------ #

    def _save_backlog_md(self, _print: Callable) -> None:
        """Save the current backlog to concept/docs/BACKLOG.md."""
        items = self._backlog_state._state.get("items", [])
        if not items:
            _print("  No items to save.")
            return

        lines: list[str] = ["# Backlog\n"]

        # Group by epic
        epics: dict[str, list[dict]] = {}
        for item in items:
            epic = item.get("epic", "Ungrouped")
            epics.setdefault(epic, []).append(item)

        for epic, epic_items in epics.items():
            lines.append(f"## {epic}\n")
            for item in epic_items:
                title = item.get("title", "Untitled")
                effort = item.get("effort", "?")
                desc = item.get("description", "")
                lines.append(f"### {title} [{effort}]\n")
                if desc:
                    lines.append(f"{desc}\n")

                ac = item.get("acceptance_criteria", [])
                if ac:
                    lines.append("**Acceptance Criteria:**\n")
                    for i, c in enumerate(ac, 1):
                        lines.append(f"{i}. {c}")
                    lines.append("")

                tasks = item.get("tasks", [])
                if tasks:
                    lines.append("**Tasks:**\n")
                    for t in tasks:
                        lines.append(f"- [ ] {t}")
                    lines.append("")

                lines.append("---\n")

        output_path = Path(self._context.project_dir) / "concept" / "docs" / "BACKLOG.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines), encoding="utf-8")
        _print(f"  Saved to {output_path.relative_to(self._context.project_dir)}")

    # ------------------------------------------------------------------ #
    # Utilities
    # ------------------------------------------------------------------ #

    def _get_production_items(self) -> str:
        """Extract production backlog items from the knowledge base.

        Reads the discovery state to find the architecture's services,
        then extracts ``## Production Backlog Items`` from each service
        knowledge file.  Returns a formatted text block or empty string.
        """
        try:
            from azext_prototype.knowledge import KnowledgeLoader
            from azext_prototype.stages.discovery_state import DiscoveryState

            ds = DiscoveryState(self._context.project_dir)
            if not ds.exists:
                return ""
            ds.load()

            services = ds.state.get("architecture", {}).get("services", [])
            if not services:
                return ""

            loader = KnowledgeLoader()
            lines: list[str] = []
            for svc in services:
                items = loader.extract_production_items(svc)
                if items:
                    lines.append(f"### {svc}")
                    lines.extend(f"- {item}" for item in items)
                    lines.append("")

            return "\n".join(lines)
        except Exception:
            logger.debug("Could not load production items from knowledge base")
            return ""

    @contextmanager
    def _maybe_spinner(self, message: str, use_styled: bool) -> Iterator[None]:
        """Show a spinner when using styled output, otherwise no-op."""
        if use_styled:
            with self._console.spinner(message):
                yield
        else:
            yield
