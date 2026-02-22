"""Discovery session — organic, multi-turn requirements conversation.

Thin I/O loop that connects the user to the biz-analyst agent.  The
agent's system prompt handles all intelligence: policy awareness,
convergence, follow-up questions, and conflict detection.

The code's only jobs:
1. Build proper multi-turn message history
   (system prompt + governance -> user -> assistant -> user -> ...)
2. Shuttle text between the user and the AI
3. Detect session-ending signals (user says ``done`` / agent emits
   the ``[READY]`` marker)
4. Produce a structured summary for the architect at the end
5. Persist learnings incrementally to discovery.yaml after each exchange

There is no meta-prompt injection, no Python-side keyword matching,
no numbered menus.  The experience should feel like talking to an
expert colleague — not running a script.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Callable

from azext_prototype.agents.base import AgentCapability, AgentContext
from azext_prototype.agents.registry import AgentRegistry
from azext_prototype.ai.provider import AIMessage
from azext_prototype.ai.token_tracker import TokenTracker
from azext_prototype.stages.discovery_state import DiscoveryState
from azext_prototype.stages.qa_router import route_error_to_qa
from azext_prototype.ui.console import Console, DiscoveryPrompt
from azext_prototype.ui.console import console as default_console

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------- #
# Sentinels
# -------------------------------------------------------------------- #

# User inputs that end the session
_QUIT_WORDS = frozenset({"q", "quit", "exit"})
_DONE_WORDS = frozenset({"done", "finish", "accept", "lgtm"})

# Slash commands
_SLASH_COMMANDS = frozenset({"/open", "/status", "/confirmed", "/help", "/summary", "/restart"})

# The agent is instructed to include this invisible marker at the very
# end of a message when it believes requirements are complete.  The code
# strips it before displaying the response, so the user sees a natural
# message.
_READY_MARKER = "[READY]"


# -------------------------------------------------------------------- #
# DiscoveryResult — public interface consumed by DesignStage
# -------------------------------------------------------------------- #


class DiscoveryResult:
    """Result of a discovery session."""

    __slots__ = (
        "requirements",
        "conversation",
        "policy_overrides",
        "exchange_count",
        "cancelled",
    )

    def __init__(
        self,
        requirements: str,
        conversation: list[AIMessage],
        policy_overrides: list[dict[str, str]],
        exchange_count: int,
        cancelled: bool = False,
    ) -> None:
        self.requirements = requirements
        self.conversation = conversation
        self.policy_overrides = policy_overrides
        self.exchange_count = exchange_count
        self.cancelled = cancelled


# -------------------------------------------------------------------- #
# DiscoverySession
# -------------------------------------------------------------------- #


class DiscoverySession:
    """Organic, multi-turn discovery conversation.

    Manages a proper multi-turn chat between the user and the
    biz-analyst agent.  The conversation history is passed in full on
    every turn so the LLM has complete context — exactly the way an
    agentic prompt (like Claude Code) works.

    The Python code is deliberately minimal.  All intelligence — asking
    the right questions, detecting conflicts, driving convergence — is
    delegated to the LLM via the system prompt.

    Discovery state is persisted incrementally to `.prototype/state/discovery.yaml`
    after each exchange, ensuring no learnings are lost if the session is
    interrupted.
    """

    def __init__(
        self,
        agent_context: AgentContext,
        registry: AgentRegistry,
        *,
        governance: Any = None,  # accepted for interface compat; unused
        console: Console | None = None,
        discovery_state: DiscoveryState | None = None,
    ) -> None:
        self._context = agent_context
        self._registry = registry
        self._console = console or default_console
        self._prompt = DiscoveryPrompt(self._console)

        # Discovery state for incremental persistence
        self._discovery_state = discovery_state or DiscoveryState(agent_context.project_dir)

        # Conversation state — proper multi-turn history
        self._messages: list[AIMessage] = []
        self._exchange_count: int = 0
        self._token_tracker = TokenTracker()

        # Resolve agents for joint discovery
        biz_agents = registry.find_by_capability(AgentCapability.BIZ_ANALYSIS)
        self._biz_agent = biz_agents[0] if biz_agents else None

        architect_agents = registry.find_by_capability(AgentCapability.ARCHITECT)
        self._architect_agent = architect_agents[0] if architect_agents else None

        qa_agents = registry.find_by_capability(AgentCapability.QA)
        self._qa_agent = qa_agents[0] if qa_agents else None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def run(
        self,
        seed_context: str = "",
        artifacts: str = "",
        artifact_images: list[dict] | None = None,
        input_fn: Callable[[str], str] | None = None,
        print_fn: Callable[[str], None] | None = None,
        context_only: bool = False,
    ) -> DiscoveryResult:
        """Run the discovery conversation.

        Parameters
        ----------
        seed_context:
            Initial context from ``--context`` (may be empty).
        artifacts:
            Content from ``--artifacts`` (may be empty).
        artifact_images:
            List of image dicts (``filename``, ``data``, ``mime``) from
            standalone images and embedded document images.  Sent via
            the vision API in the opening message.
        input_fn / print_fn:
            Injectable I/O for testing.  Default to styled console I/O.
        context_only:
            If True, the session was started with only ``--context`` and
            no artifacts. The agent will decide if interactive conversation
            is needed or if the context is sufficient.

        Returns
        -------
        DiscoveryResult
            Consolidated requirements, conversation transcript, and any
            policy overrides the user declared during conversation.
        """
        # Use injected I/O for tests, otherwise use styled console
        use_styled = input_fn is None and print_fn is None
        _input = input_fn or (lambda p: self._prompt.prompt(p))

        # Load existing discovery state for context
        existing_context = ""
        if self._discovery_state.exists:
            self._discovery_state.load()
            existing_context = self._discovery_state.format_as_context()
            if existing_context and use_styled:
                self._console.print_info("Loaded existing discovery context from previous session.")
        else:
            self._discovery_state.load()  # Initialize empty state

        # ---- Fallback when no agent is available ----
        if not self._biz_agent:
            self._console.print_warning("No biz-analyst agent available. Enter your requirements:")
            try:
                text = _input("> ")
            except (EOFError, KeyboardInterrupt):
                text = ""
            return DiscoveryResult(
                requirements=text.strip(),
                conversation=[],
                policy_overrides=[],
                exchange_count=0,
            )

        # ---- Kick off the conversation ----
        opening = self._build_opening(seed_context, artifacts, existing_context, images=artifact_images)

        with self._console.spinner("Analyzing your input..."):
            response = self._chat(opening)

        # Update discovery state with the initial exchange
        self._exchange_count += 1
        self._discovery_state.update_from_exchange(opening, response, self._exchange_count)

        clean_response = self._clean(response)
        self._console.print_agent_response(clean_response)
        if use_styled:
            self._console.print_token_status(self._token_tracker.format_status())

        # ---- Check if agent needs more information ----
        # If context_only mode and agent signals READY, skip interactive loop
        if context_only and _READY_MARKER in response:
            self._console.print_info("Context is sufficient. Proceeding with design.")
            summary = self._produce_summary()
            overrides = self._extract_overrides(summary)
            return DiscoveryResult(
                requirements=summary,
                conversation=list(self._messages),
                policy_overrides=overrides,
                exchange_count=self._exchange_count,
            )

        # ---- Main conversation loop ----
        first_prompt = True
        while True:
            try:
                if use_styled:
                    # Use bordered prompt with instruction and status
                    user_input = self._prompt.prompt(
                        "> ",
                        instruction=DiscoveryPrompt.INSTRUCTION if first_prompt else None,
                        show_quit_hint=first_prompt,
                        open_count=self._discovery_state.open_count,
                    )
                    first_prompt = False
                else:
                    user_input = _input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input:
                continue

            # Handle slash commands
            lower_input = user_input.lower()
            if lower_input in _SLASH_COMMANDS:
                self._handle_slash_command(lower_input)
                continue
            if lower_input.startswith("/why"):
                self._handle_why_command(user_input)
                continue

            if user_input.lower() in _QUIT_WORDS:
                return DiscoveryResult(
                    requirements="",
                    conversation=list(self._messages),
                    policy_overrides=[],
                    exchange_count=self._exchange_count,
                    cancelled=True,
                )

            if user_input.lower() in _DONE_WORDS:
                break

            self._exchange_count += 1

            with self._console.spinner("Thinking..."):
                response = self._chat(user_input)

            # Update discovery state after each exchange
            self._discovery_state.update_from_exchange(user_input, response, self._exchange_count)

            # Extract any open/confirmed items from the response
            self._extract_items_from_response(response)

            clean = self._clean(response)
            self._console.print_agent_response(clean)
            if use_styled:
                self._console.print_token_status(self._token_tracker.format_status())

            # Agent signalled convergence
            if _READY_MARKER in response:
                self._console.print_info("Discovery complete. Press Enter to proceed, or keep typing.")
                try:
                    if use_styled:
                        more = self._prompt.simple_prompt("> ")
                    else:
                        more = _input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not more or more.lower() in _DONE_WORDS:
                    if use_styled:
                        self._console.clear_last_line()
                    break
                # User wants to continue
                self._exchange_count += 1
                with self._console.spinner("Thinking..."):
                    response = self._chat(more)
                self._discovery_state.update_from_exchange(more, response, self._exchange_count)
                self._extract_items_from_response(response)
                self._console.print_agent_response(self._clean(response))
                if use_styled:
                    self._console.print_token_status(self._token_tracker.format_status())

        # ---- Produce the final summary ----
        with self._console.spinner("Generating requirements summary..."):
            summary = self._produce_summary()
            overrides = self._extract_overrides(summary)

        return DiscoveryResult(
            requirements=summary,
            conversation=list(self._messages),
            policy_overrides=overrides,
            exchange_count=self._exchange_count,
        )

    # ------------------------------------------------------------------ #
    # Internal — AI communication
    # ------------------------------------------------------------------ #

    def _chat(self, user_content: str | list) -> str:
        """Send a user message and return the assistant's response.

        Builds the full message list each call::

            [system messages] + [conversation history] + [new user msg]

        This mirrors how agentic prompts work — the LLM always sees
        the complete conversation and all system instructions.

        When ``user_content`` is a list (multi-modal content array with
        images), the provider sends it as an OpenAI vision-format message.
        If the provider rejects multi-modal content, falls back to
        text-only with a note that images could not be processed.
        """
        assert self._biz_agent is not None
        assert self._context.ai_provider is not None

        self._messages.append(AIMessage(role="user", content=user_content))

        # System messages: biz-analyst prompt + governance + architect context
        full = self._biz_agent.get_system_messages()
        full.append(
            AIMessage(
                role="system",
                content=f"Today's date is {date.today().strftime('%B %d, %Y')}.",
            )
        )
        architect_context = self._build_architect_context()
        if architect_context:
            full.append(AIMessage(role="system", content=architect_context))
        full.extend(self._messages)

        try:
            response = self._context.ai_provider.chat(
                full,
                temperature=self._biz_agent._temperature,
                max_tokens=self._biz_agent._max_tokens,
            )
        except Exception as exc:
            if isinstance(user_content, list):
                # Graceful degradation: retry text-only when vision fails
                logger.warning("Multi-modal chat failed, retrying text-only")
                text_only = next(
                    (p["text"] for p in user_content if isinstance(p, dict) and p.get("type") == "text"),
                    str(user_content),
                )
                self._messages[-1] = AIMessage(
                    role="user",
                    content=text_only + "\n\n[Images could not be processed by the AI provider]",
                )
                full = self._biz_agent.get_system_messages()
                if architect_context:
                    full.append(AIMessage(role="system", content=architect_context))
                full.extend(self._messages)
                response = self._context.ai_provider.chat(
                    full,
                    temperature=self._biz_agent._temperature,
                    max_tokens=self._biz_agent._max_tokens,
                )
            else:
                route_error_to_qa(
                    exc,
                    "Discovery conversation",
                    self._qa_agent,
                    self._context,
                    self._token_tracker,
                    lambda msg: logger.info(msg),
                )
                raise

        self._token_tracker.record(response)
        self._messages.append(
            AIMessage(role="assistant", content=response.content),
        )
        return response.content

    # ------------------------------------------------------------------ #
    # Internal — opening message
    # ------------------------------------------------------------------ #

    def _build_opening(
        self,
        seed_context: str,
        artifacts: str,
        existing_context: str = "",
        images: list[dict] | None = None,
    ) -> str | list:
        """Compose a natural opening message from the user's provided inputs.

        If there's existing discovery context from a previous session,
        it's included so the agent is aware of prior learnings and can
        identify any conflicts with new information.

        When ``images`` are provided, returns a multi-modal content array
        (list) instead of a plain string.  The array contains a text block
        followed by ``image_url`` blocks for each image.
        """
        parts = []

        # Include existing context if available
        if existing_context:
            parts.append(
                "Here's what we've established in previous sessions:\n\n"
                f"{existing_context}\n\n"
                "Please review this context and identify any conflicts with "
                "the new information I'm about to provide. If there are "
                "conflicts, ask clarifying questions to resolve them."
            )

        # Add new context
        if seed_context and artifacts:
            parts.append(
                f"Here's what I'm thinking:\n\n{seed_context}\n\n"
                f"I also have some requirement documents:\n\n{artifacts}"
            )
        elif seed_context:
            parts.append(seed_context)
        elif artifacts:
            parts.append("I have some requirement documents for you to review:\n\n" + artifacts)
        elif not existing_context:
            parts.append("I'd like to design a new Azure prototype.")

        text = (
            "\n\n---\n\n".join(parts)
            if len(parts) > 1
            else (parts[0] if parts else "I'd like to design a new Azure prototype.")
        )

        # If images are present, build a multi-modal content array
        if images:
            content: list[dict] = [{"type": "text", "text": text}]
            for img in images:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img['mime']};base64,{img['data']}",
                            "detail": "high",
                        },
                    }
                )
            return content

        return text

    # ------------------------------------------------------------------ #
    # Internal — architect context for joint discovery
    # ------------------------------------------------------------------ #

    def _build_architect_context(self) -> str:
        """Build architectural context from the cloud-architect agent.

        Extracts the architect's constraints and key design guidance
        to inject alongside the biz-analyst system prompt.  This gives
        the discovery conversation an architectural perspective without
        making a separate AI call.

        Returns empty string if no architect agent is available.
        """
        if not self._architect_agent:
            return ""

        parts = [
            "## Architectural Guidance\n"
            "You also have the perspective of a cloud architect.  During "
            "discovery, apply this architectural lens and get into the "
            "technical weeds:\n"
        ]

        if hasattr(self._architect_agent, "constraints") and self._architect_agent.constraints:
            parts.append("**Architecture constraints to keep in mind:**")
            for constraint in self._architect_agent.constraints:
                parts.append(f"- {constraint}")
            parts.append("")

        parts.append(
            "When the user describes service choices or integration patterns, "
            "assess feasibility from an Azure architecture standpoint.  If "
            "something won't work well (e.g., mixing incompatible services, "
            "anti-patterns for Azure), mention it during the conversation so "
            "the user can course-correct early.\n"
            "\n"
            "Do NOT generate a full architecture design during discovery.  "
            "That happens in a separate step after discovery completes.  "
            "Your role here is to ask architecturally-informed questions and "
            "flag potential issues.\n"
            "\n"
            "## Technical Areas to Probe\n"
            "\n"
            "Go beyond business requirements — the architect needs concrete "
            "technical detail in each of these areas to produce a deployable "
            "design.  Ask **open-ended** questions that invite the user to "
            "describe their thinking, not just pick from a menu.  Use "
            '"how", "what", "tell me about", "walk me through" '
            "phrasing:\n"
            "\n"
            "**Compute & hosting** — How do you picture the application "
            "running?  Walk me through a typical request from the user's "
            "browser to the backend — what happens at each step?  What does "
            "the deployment artifact look like (container image, code "
            "package, something else)?\n"
            "\n"
            "**Data layer** — Tell me about the data.  What are the main "
            "entities and how do they relate to each other?  How will the "
            "data be queried — mostly lookups by key, or complex joins and "
            "aggregations?  What kind of volumes are you expecting?\n"
            "\n"
            "**Networking** — Who needs to reach this system and from where?  "
            "Walk me through the network path you have in mind — public "
            "internet, corporate network, or both?  Any requirements around "
            "custom domains or private connectivity?\n"
            "\n"
            "**Identity & auth** — How do you expect users to sign in, and "
            "what should they be able to do once they're in?  Are there "
            "different levels of access?  What other services does this "
            "system need to talk to, and how should it authenticate?\n"
            "\n"
            "**Integration & messaging** — What external systems does this "
            "need to talk to?  Tell me about the data flows — are they "
            "real-time request/response, or can some work happen "
            "asynchronously in the background?\n"
            "\n"
            "**AI / ML services** — Tell me about the AI capabilities you "
            "have in mind.  What kind of content will the model work with?  "
            "How do you envision the user interacting with the AI features?  "
            "What does a good response look like?\n"
            "\n"
            "**Observability** — What would you need to see in a dashboard "
            "to feel confident the system is healthy?  What would a bad day "
            "look like, and how would you want to find out about it?\n"
            "\n"
            "**Deployment & environments** — How do you picture this getting "
            "deployed?  Tell me about your environment strategy — where does "
            "the prototype live relative to other environments?\n"
            "\n"
            "**Scaling characteristics** — Describe the expected usage "
            "pattern.  How many people will use this, and when?  Are there "
            "spiky periods or is it fairly steady?\n"
            "\n"
            "**Security boundaries** — What kind of data flows through this "
            "system?  Anything sensitive or regulated?  Tell me about any "
            "compliance requirements or security policies you need to "
            "follow.\n"
            "\n"
            "Don't dump all of these at once.  Weave them naturally into "
            "the conversation as each topic area comes up.  But make sure "
            "you cover the relevant technical areas before signalling "
            "readiness — the architect cannot design without these details."
        )

        return "\n".join(parts)

    # ------------------------------------------------------------------ #
    # Internal — summary
    # ------------------------------------------------------------------ #

    def _produce_summary(self) -> str:
        """Ask the agent for a final structured requirements summary.

        If there were no exchanges (user immediately typed ``done``),
        return the raw conversation text.
        """
        if self._exchange_count == 0:
            user_msgs = [m.content for m in self._messages if m.role == "user"]
            return "\n\n".join(user_msgs).strip()

        summary = self._chat(
            "Please provide the final requirements summary for the cloud "
            "architect.  Use the exact summary format from your instructions "
            "with all required headings: Project Summary, Goals, Confirmed "
            "Functional Requirements, Confirmed Non-Functional Requirements, "
            "Constraints, Decisions, Open Items, Risks, Prototype Scope "
            "(with In Scope / Out of Scope / Deferred sub-sections), "
            "Azure Services, and Policy Overrides.  Do not skip any section "
            "— use 'None' for empty sections."
        )
        return self._clean(summary)

    # ------------------------------------------------------------------ #
    # Internal — helpers
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Internal — slash commands
    # ------------------------------------------------------------------ #

    def _handle_slash_command(self, command: str) -> None:
        """Handle slash commands like /open, /status, /confirmed."""
        if command == "/open":
            self._console.print()
            self._console.print(self._discovery_state.format_open_items())
            self._console.print()
        elif command == "/confirmed":
            self._console.print()
            self._console.print(self._discovery_state.format_confirmed_items())
            self._console.print()
        elif command == "/status":
            self._console.print()
            self._console.print(f"Discovery Status: {self._discovery_state.format_status_summary()}")
            self._console.print()
            if self._discovery_state.open_count > 0:
                self._console.print(self._discovery_state.format_open_items())
                self._console.print()
        elif command == "/summary":
            if not self._biz_agent or not self._context.ai_provider:
                self._console.print_warning("No AI agent available for summary.")
                return
            self._console.print()
            with self._console.spinner("Generating summary..."):
                summary = self._chat(
                    "Please provide a concise summary of everything we've "
                    "established so far — confirmed requirements, open questions, "
                    "constraints, and key decisions. This is a mid-session "
                    "checkpoint, not the final summary."
                )
            self._console.print_agent_response(self._clean(summary))
        elif command == "/restart":
            self._console.print()
            self._console.print_warning("Restarting discovery session...")
            self._discovery_state.reset()
            self._messages.clear()
            self._exchange_count = 0
            if self._biz_agent and self._context.ai_provider:
                opening = "I'd like to design a new Azure prototype."
                with self._console.spinner("Starting fresh..."):
                    response = self._chat(opening)
                self._exchange_count += 1
                self._discovery_state.update_from_exchange(opening, response, self._exchange_count)
                self._console.print_agent_response(self._clean(response))
        elif command == "/help":
            self._console.print()
            self._console.print_dim("Available commands:")
            self._console.print_dim("  /open      - List open items needing resolution")
            self._console.print_dim("  /confirmed - List confirmed requirements")
            self._console.print_dim("  /status    - Show overall discovery status")
            self._console.print_dim("  /summary   - Show a narrative summary of progress so far")
            self._console.print_dim("  /why <topic> - Find the exchange where a topic was discussed")
            self._console.print_dim("  /restart   - Clear state and restart discovery from scratch")
            self._console.print_dim("  /help      - Show this help message")
            self._console.print_dim("  done       - Complete discovery and proceed to design")
            self._console.print_dim("  quit       - Cancel and exit")
            self._console.print()

    def _handle_why_command(self, raw_input: str) -> None:
        """Handle ``/why <query>`` — find the exchange where a topic was discussed."""
        query = raw_input[4:].strip()
        if not query:
            self._console.print()
            self._console.print_dim("Usage: /why <topic>")
            self._console.print_dim("  Example: /why managed identity")
            self._console.print()
            return

        matches = self._discovery_state.search_history(query)
        self._console.print()
        if not matches:
            self._console.print_dim(f"No exchanges found mentioning '{query}'.")
        else:
            self._console.print_dim(f"Found {len(matches)} exchange(s) mentioning '{query}':")
            self._console.print()
            for m in matches:
                self._console.print_dim(f"  Exchange {m['exchange']}:")
                user_text = m.get("user", "")
                asst_text = m.get("assistant", "")
                user_snippet = user_text[:150] + ("..." if len(user_text) > 150 else "")
                asst_snippet = asst_text[:150] + ("..." if len(asst_text) > 150 else "")
                self._console.print_dim(f"    You: {user_snippet}")
                self._console.print_dim(f"    Agent: {asst_snippet}")
                self._console.print()
        self._console.print()

    def _extract_items_from_response(self, response: str) -> None:
        """Extract open questions and confirmed items from agent response.

        The agent is instructed to mark items with specific patterns:
        - [OPEN] or [?] for open questions
        - [CONFIRMED] or [✓] for confirmed items

        This is best-effort parsing — the agent may not always use these
        markers, but when it does, we track them.
        """
        lines = response.split("\n")
        for line in lines:
            line_stripped = line.strip()

            # Check for open item markers
            if any(marker in line_stripped for marker in ["[OPEN]", "[?]", "❓", "⚠️ Open:"]):
                # Extract the text after the marker
                for marker in ["[OPEN]", "[?]", "❓", "⚠️ Open:"]:
                    if marker in line_stripped:
                        item = line_stripped.replace(marker, "").strip(" -:*")
                        if item and len(item) > 5:  # Avoid noise
                            self._discovery_state.add_open_item(item)
                        break

            # Check for confirmed item markers
            if any(marker in line_stripped for marker in ["[CONFIRMED]", "[✓]", "✅", "✓ Confirmed:"]):
                for marker in ["[CONFIRMED]", "[✓]", "✅", "✓ Confirmed:"]:
                    if marker in line_stripped:
                        item = line_stripped.replace(marker, "").strip(" -:*")
                        if item and len(item) > 5:
                            # Add to confirmed, and remove from open if present
                            self._discovery_state.resolve_item(item, item)
                        break

    # ------------------------------------------------------------------ #
    # Internal — helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _clean(text: str) -> str:
        """Strip the ``[READY]`` marker so the user sees natural text."""
        return text.replace(_READY_MARKER, "").strip()

    @staticmethod
    def _extract_overrides(summary: str) -> list[dict[str, str]]:
        """Best-effort extraction of policy overrides from the summary.

        The agent is instructed to list overrides under a
        ``Policy Overrides`` heading.  We parse that section into
        structured dicts for ``design_state`` persistence.

        If parsing fails, returns an empty list — the information is
        still in the requirements text for the architect.
        """
        overrides: list[dict[str, str]] = []

        match = re.search(
            r"(?:^|\n)##?\s*Policy\s+Overrides?\s*\n(.*?)(?:\n##?\s|\Z)",
            summary,
            re.DOTALL | re.IGNORECASE,
        )
        if not match:
            return overrides

        section = match.group(1).strip()
        for line in section.split("\n"):
            line = line.strip().lstrip("-").lstrip("*").strip()
            if not line:
                continue
            parts = re.split(r"[:\u2014\u2013|]", line, maxsplit=1)
            name = parts[0].strip().strip("*").strip()
            reason = parts[1].strip() if len(parts) > 1 else ""
            if name:
                overrides.append(
                    {
                        "rule_id": name,
                        "policy_name": name,
                        "description": reason or "User chose to override",
                        "recommendation": "",
                        "user_text": reason,
                    }
                )

        return overrides
