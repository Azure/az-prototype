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
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterator

from azext_prototype.agents.base import AgentCapability, AgentContext
from azext_prototype.agents.registry import AgentRegistry
from azext_prototype.ai.provider import AIMessage
from azext_prototype.ai.token_tracker import TokenTracker
from azext_prototype.stages.discovery_state import DiscoveryState, TrackedItem
from azext_prototype.stages.intent import (
    IntentKind,
    build_discovery_classifier,
    read_files_for_session,
)
from azext_prototype.stages.qa_router import route_error_to_qa
from azext_prototype.ui.console import Console, DiscoveryPrompt
from azext_prototype.ui.console import console as default_console

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------- #
# Section header extraction
# -------------------------------------------------------------------- #

_SECTION_HEADING_RE = re.compile(r"^#{2,3}\s+(.+?)\s*$", re.MULTILINE)

# Matches **Bold Heading** on its own line (common in conversational responses)
_BOLD_HEADING_RE = re.compile(r"^\*\*([^*\n]{3,60})\*\*\s*$", re.MULTILINE)

_SKIP_HEADINGS = frozenset(
    {
        "summary",
        "policy overrides",
        "policy override",
        "next steps",
        "what i've understood so far",
        "what we've covered",
        "what i've understood",
        "what we've established",
    }
)


def extract_section_headers(response: str) -> list[tuple[str, int]]:
    """Extract ## / ### headings and **bold headings** from an AI response.

    Returns a list of ``(heading_text, level)`` tuples sorted by position.
    Level 2 = top-level section (``##`` or ``**bold**``), level 3 = subsection (``###``).

    Filters out structural headings (Summary, Policy Overrides, Next Steps,
    "What I've Understood So Far", etc.) and very short matches.
    """
    matches: list[tuple[int, str, int]] = []  # (position, text, level)
    for m in _SECTION_HEADING_RE.finditer(response):
        text = m.group(1).strip().rstrip(":")
        hashes = len(m.group(0)) - len(m.group(0).lstrip("#"))
        level = min(hashes, 3)  # ## = 2, ### = 3
        matches.append((m.start(), text, level))
    for m in _BOLD_HEADING_RE.finditer(response):
        text = m.group(1).strip().rstrip(":")
        matches.append((m.start(), text, 2))
    matches.sort(key=lambda x: x[0])

    # Only return level-2 headings — level-3 subsections are part of
    # their parent topic and should not become separate tree entries.
    seen: set[str] = set()
    headers: list[tuple[str, int]] = []
    for _, text, level in matches:
        if level > 2:
            continue
        lower = text.lower()
        if lower in _SKIP_HEADINGS or len(text) < 3 or lower in seen:
            continue
        seen.add(lower)
        headers.append((text, level))
    return headers


# -------------------------------------------------------------------- #
# Section parsing — code-level gating for one-at-a-time display
# -------------------------------------------------------------------- #


@dataclass
class Section:
    """A parsed section from an AI response."""

    heading: str
    level: int  # 2=##, 3=###
    content: str  # text from heading to next heading (includes heading line)
    task_id: str  # "design-section-{slug}"


def parse_sections(response: str) -> tuple[str, list[Section]]:
    """Split *response* into ``(preamble, sections)``.

    Preamble = text before the first heading.  Sections are filtered by
    ``_SKIP_HEADINGS`` (same filter as :func:`extract_section_headers`).
    """
    # Collect heading positions
    matches: list[tuple[int, str, int]] = []  # (position, text, level)
    for m in _SECTION_HEADING_RE.finditer(response):
        text = m.group(1).strip().rstrip(":")
        hashes = len(m.group(0)) - len(m.group(0).lstrip("#"))
        level = min(hashes, 3)
        matches.append((m.start(), text, level))
    for m in _BOLD_HEADING_RE.finditer(response):
        text = m.group(1).strip().rstrip(":")
        matches.append((m.start(), text, 2))
    matches.sort(key=lambda x: x[0])

    if not matches:
        return response, []

    # Only create sections from level-2 (##) headings.
    # Level-3 (###) subsections are folded into their parent's content.
    level2 = [(pos, text) for pos, text, level in matches if level == 2]

    if not level2:
        return response, []

    preamble = response[: level2[0][0]].strip()

    seen: set[str] = set()
    sections: list[Section] = []
    for idx, (pos, text) in enumerate(level2):
        lower = text.lower()
        if lower in _SKIP_HEADINGS or len(text) < 3 or lower in seen:
            continue
        seen.add(lower)

        # Content runs to the next level-2 heading (or end of response).
        # This naturally includes any ### subsections within this topic.
        end = level2[idx + 1][0] if idx + 1 < len(level2) else len(response)
        content = response[pos:end].strip()

        slug = re.sub(r"[^a-z0-9]+", "-", lower).strip("-")
        task_id = f"design-section-{slug}"
        sections.append(Section(heading=text, level=2, content=content, task_id=task_id))

    return preamble, sections


# -------------------------------------------------------------------- #
# Sentinels
# -------------------------------------------------------------------- #

# User inputs that end the session
_QUIT_WORDS = frozenset({"q", "quit", "exit"})
_DONE_WORDS = frozenset({"done", "end", "finish", "accept", "lgtm", "continue"})

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
        if self._console:
            self._token_tracker._on_update = self._console.print_token_status

        # Resolve agents for joint discovery
        biz_agents = registry.find_by_capability(AgentCapability.BIZ_ANALYSIS)
        self._biz_agent = biz_agents[0] if biz_agents else None

        architect_agents = registry.find_by_capability(AgentCapability.ARCHITECT)
        self._architect_agent = architect_agents[0] if architect_agents else None

        qa_agents = registry.find_by_capability(AgentCapability.QA)
        self._qa_agent = qa_agents[0] if qa_agents else None

        # Intent classifier for natural language command detection
        self._intent_classifier = build_discovery_classifier(
            ai_provider=agent_context.ai_provider,
            token_tracker=self._token_tracker,
        )

    # ------------------------------------------------------------------ #
    # Spinner helper (mirrors build/deploy pattern)
    # ------------------------------------------------------------------ #

    @contextmanager
    def _maybe_spinner(self, message: str, use_styled: bool, *, status_fn: Callable | None = None) -> Iterator[None]:
        """Show a spinner when using styled output, otherwise no-op."""
        if use_styled:
            with self._console.spinner(message):
                yield
        elif status_fn:
            status_fn(message, "start")
            try:
                yield
            finally:
                status_fn(message, "end")
        else:
            yield

    # ------------------------------------------------------------------ #
    # Display helpers
    # ------------------------------------------------------------------ #

    def _show_content(self, content: str, use_styled: bool, _print: Callable) -> None:
        """Display content using the appropriate output channel."""
        if use_styled:
            self._console.print_agent_response(content)
            self._console.print_token_status(self._token_tracker.format_status())
        elif self._response_fn:
            self._response_fn(content)
        else:
            _print(content)

    def _handle_read_files(
        self,
        args: str,
        _print: Callable,
        use_styled: bool,
    ) -> None:
        """Read files into the session and display the AI's analysis."""
        text, images = read_files_for_session(args, self._context.project_dir, _print)
        if not (text or images):
            return
        content: str | list = text
        if images:
            parts: list[dict] = []
            if text:
                parts.append({"type": "text", "text": f"Here are the files I'd like you to review:\n\n{text}"})
            for img in images:
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{img['mime']};base64,{img['data']}", "detail": "high"},
                    }
                )
            content = parts if parts else text
        elif text:
            content = f"Here are the files I'd like you to review:\n\n{text}"
        self._exchange_count += 1
        with self._maybe_spinner("Analyzing files...", use_styled, status_fn=self._status_fn):
            response = self._chat(content)
        self._process_response(f"[Read files from {args}]", response, use_styled, _print)

    # ------------------------------------------------------------------ #
    # Section-at-a-time gating
    # ------------------------------------------------------------------ #

    _SKIP_WORDS = frozenset({"skip", "next", "move on"})

    def _run_section_loop(
        self,
        sections: list[Section],
        preamble: str,
        _input: Callable[[str], str],
        _print: Callable[[str], None],
        use_styled: bool,
    ) -> str | None:
        """Walk sections one at a time.

        Returns ``"cancelled"``, ``"done"``, ``"restart"``, or ``None``
        (all sections covered, fall through to free-form loop).
        """
        if preamble:
            self._show_content(preamble, use_styled, _print)

        from azext_prototype.debug_log import log_command

        all_confirmed = True

        for i, section in enumerate(sections):
            if self._update_task_fn:
                self._update_task_fn(section.task_id, "in_progress")

            self._show_content(section.content, use_styled, _print)
            self._update_token_status()

            # Tell the user what to do with this section
            hint = (
                f"[Topic {i + 1} of {len(sections)}] " "Reply to discuss, 'skip' for next topic, or 'done' to finish."
            )
            if use_styled:
                self._console.print_info(hint)
            else:
                _print(hint)

            # Inner follow-up loop (max 5 real AI exchanges per section).
            # Slash commands and empty inputs do NOT count as exchanges.
            section_confirmed = False
            section_skipped = False
            real_answers = 0
            while real_answers < 5:
                try:
                    user_input = _input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    if self._update_task_fn:
                        self._update_task_fn(section.task_id, "completed")
                    return "done"

                if not user_input:
                    continue

                lower = user_input.lower()
                if lower in _QUIT_WORDS:
                    return "cancelled"
                if lower in _DONE_WORDS:
                    # Mark remaining sections as completed
                    for s in sections[i:]:
                        if self._update_task_fn:
                            self._update_task_fn(s.task_id, "completed")
                    return "done"
                if lower in self._SKIP_WORDS:
                    self._discovery_state.mark_item(section.heading, "skipped")
                    section_skipped = True
                    break  # Advance to next section

                # Handle slash commands — these do NOT count as exchanges
                if lower in _SLASH_COMMANDS:
                    log_command(lower, topic=section.heading, real_answers=real_answers)
                    cmd_result = self._handle_slash_command(lower)
                    if cmd_result == "restart":
                        return "restart"
                    continue
                if lower.startswith("/why"):
                    log_command(lower, topic=section.heading, real_answers=real_answers)
                    self._handle_why_command(user_input)
                    continue

                # Normal answer — this is the ONLY path that counts
                real_answers += 1
                self._exchange_count += 1
                topic = section.heading
                prompt = (
                    f"The user answered about **{topic}**: {user_input}\n"
                    f"Do you have follow-up questions about **{topic}**? "
                    f'If fully covered, respond ONLY with the word "Yes" '
                    f"(meaning yes, this section is complete). "
                    f"Otherwise, ask your follow-up questions."
                )
                with self._maybe_spinner("Thinking...", use_styled, status_fn=self._status_fn):
                    response = self._chat(prompt)

                self._discovery_state.update_from_exchange(user_input, response, self._exchange_count)
                self._extract_items_from_response(response)

                # Check if the AI confirmed the section is complete
                stripped = response.strip().rstrip(".").lower()
                if stripped == "yes":
                    section_confirmed = True
                    self._discovery_state.mark_item(section.heading, "answered", self._exchange_count)
                    break  # Section complete — advance

                clean = self._clean(response)
                self._show_content(clean, use_styled, _print)
                self._update_token_status()

            if not section_confirmed:
                all_confirmed = False
                if not section_skipped:
                    self._discovery_state.mark_item(section.heading, "answered", self._exchange_count)

            if self._update_task_fn:
                self._update_task_fn(section.task_id, "completed")

        # All sections walked
        _print("")
        if all_confirmed:
            _print("All topics covered! Type anything to keep discussing, or 'continue' to generate architecture.")
        else:
            _print("Type anything to keep discussing, or 'continue' to proceed.")
        return None

    # ------------------------------------------------------------------ #
    # Re-entry — resume at first unanswered topic
    # ------------------------------------------------------------------ #

    def _run_reentry(
        self,
        seed_context: str,
        artifacts: str,
        artifact_images: list[dict] | None,
        _input: Callable[[str], str],
        _print: Callable[[str], None],
        use_styled: bool,
        context_only: bool,
        status_fn: Callable | None,
    ) -> DiscoveryResult | None:
        """Resume discovery at the first unanswered topic.

        Returns a ``DiscoveryResult`` if the session ends (cancelled/done),
        or ``None`` if all topics are covered and the caller should fall
        through to the free-form conversation loop.
        """
        # Handle incremental context from new artifacts
        has_new_topics = False
        if (seed_context or artifacts or artifact_images) and self._biz_agent:
            has_new_topics = self._handle_incremental_context(
                seed_context, artifacts, artifact_images, _print, use_styled, status_fn
            )

        # When --context only and no new topics, exit immediately.
        # The context was recorded as a decision — no need to force
        # the user through pending topics for a simple directive.
        if context_only and not has_new_topics:
            if use_styled:
                self._console.print_info("Context recorded. Use 'az prototype design' to resume discovery.")
            else:
                _print("Context recorded. Use 'az prototype design' to resume discovery.")
            return DiscoveryResult(
                requirements=self._discovery_state.format_as_context(),
                conversation=list(self._messages),
                policy_overrides=[],
                exchange_count=self._exchange_count,
            )

        # Find first pending topic (topic kind only — decisions are not walked)
        all_topics = self._discovery_state.topic_items
        pending_topics = [t for t in all_topics if t.status == "pending"]
        if not pending_topics:
            return None  # All topics done — fall through to free-form

        answered_count = len(all_topics) - len(pending_topics)

        # Show progress
        msg = f"Resuming discovery: {answered_count}/{len(all_topics)} topics covered"
        if use_styled:
            self._console.print_info(msg)
        else:
            _print(msg)

        # Populate TUI task tree with topics only (not decisions)
        if self._section_fn:
            self._section_fn([(t.heading, 2) for t in all_topics])
        # Mark already-completed topics
        if self._update_task_fn:
            for t in all_topics:
                if t.status in ("answered", "skipped"):
                    slug = re.sub(r"[^a-z0-9]+", "-", t.heading.lower()).strip("-")
                    task_id = f"design-section-{slug}"
                    self._update_task_fn(task_id, "completed")

        # Seed message history with compact summary (NOT full conversation)
        existing_context = self._discovery_state.format_as_context()
        if existing_context:
            self._messages = [
                AIMessage(role="user", content=f"Here's what we've established so far:\n\n{existing_context}"),
                AIMessage(role="assistant", content="Understood. Let's continue where we left off."),
            ]

        # Restore exchange count from metadata
        self._exchange_count = self._discovery_state.state.get("_metadata", {}).get("exchange_count", 0)

        # Build Section objects from pending topics
        pending_sections = []
        for t in pending_topics:
            slug = re.sub(r"[^a-z0-9]+", "-", t.heading.lower()).strip("-")
            task_id = f"design-section-{slug}"
            pending_sections.append(Section(heading=t.heading, level=2, content=t.detail, task_id=task_id))

        # Reuse existing section loop
        outcome = self._run_section_loop(pending_sections, "", _input, _print, use_styled)
        if outcome == "cancelled":
            return DiscoveryResult(
                requirements="",
                conversation=list(self._messages),
                policy_overrides=[],
                exchange_count=self._exchange_count,
                cancelled=True,
            )
        if outcome == "restart":
            return None  # Fall through — state was already reset by /restart handler
        if outcome == "done":
            with self._maybe_spinner("Generating requirements summary...", use_styled, status_fn=status_fn):
                summary = self._produce_summary()
                overrides = self._extract_overrides(summary)
            return DiscoveryResult(
                requirements=summary,
                conversation=list(self._messages),
                policy_overrides=overrides,
                exchange_count=self._exchange_count,
            )

        # All pending sections walked — fall through to free-form loop
        return None

    def _handle_incremental_context(
        self,
        seed_context: str,
        artifacts: str,
        artifact_images: list[dict] | None,
        _print: Callable[[str], None],
        use_styled: bool,
        status_fn: Callable | None,
    ) -> bool:
        """Ask AI to identify new topics from new artifacts/context.

        Only called on re-entry when new content is provided. Appends
        new topics without replacing existing ones.

        Returns ``True`` if new topics were added, ``False`` if the AI
        determined no new topics are needed.  When no new topics are
        needed, the seed context (if any) is recorded as a confirmed
        decision so it reaches the architect.
        """
        existing_topics = self._discovery_state.items
        existing_headings = [t.heading for t in existing_topics]

        parts = ["We already have these discovery topics established:"]
        for h in existing_headings:
            parts.append(f"- {h}")
        parts.append("")

        if seed_context:
            parts.append(f"New context provided:\n{seed_context}\n")
        if artifacts:
            parts.append(f"New artifacts provided:\n{artifacts}\n")

        parts.append(
            "Based on the new information above, identify any NEW topics "
            "that are not already covered by the existing topics. "
            "For each new topic, respond with a ## Heading and 2-4 focused "
            "questions underneath. If no new topics are needed, respond "
            "with exactly: [NO_NEW_TOPICS]"
        )

        prompt: str | list = "\n".join(parts)

        # Add images if present
        if artifact_images:
            content: list[dict] = [{"type": "text", "text": prompt}]
            for img in artifact_images:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{img['mime']};base64,{img['data']}", "detail": "high"},
                    }
                )
            prompt = content

        with self._maybe_spinner("Analyzing new content for additional topics...", use_styled, status_fn=status_fn):
            # Use lightweight chat — this is a classification task that
            # doesn't need the full 69KB governance/template/architect payload.
            if isinstance(prompt, str):
                response = self._chat_lightweight(prompt)
            else:
                # Multi-modal (images) — must use full _chat() for vision support
                response = self._chat(prompt)

        if "[NO_NEW_TOPICS]" in response:
            # Record the context as a confirmed decision so it persists
            # and reaches the architect via format_as_context()
            if seed_context:
                self._discovery_state.add_confirmed_decision(seed_context)
                msg = f"Context recorded as decision: {seed_context}"
            else:
                msg = "No new topics needed from provided content."
            if use_styled:
                self._console.print_info(msg)
            else:
                _print(msg)
            return False

        _, new_sections = parse_sections(self._clean(response))
        if new_sections:
            new_topics = [
                TrackedItem(
                    heading=s.heading,
                    detail=s.content,
                    kind="topic",
                    status="pending",
                    answer_exchange=None,
                )
                for s in new_sections
            ]
            self._discovery_state.append_items(new_topics)
            added = len(self._discovery_state.items) - len(existing_topics)
            if added > 0:
                msg = f"Added {added} new topic(s) from new content."
                if use_styled:
                    self._console.print_info(msg)
                else:
                    _print(msg)
                return True

        # No sections parsed — record context as decision if provided
        if seed_context:
            self._discovery_state.add_confirmed_decision(seed_context)
        return False

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
        status_fn: Callable | None = None,
        section_fn: Callable[[list[tuple[str, int]]], None] | None = None,
        response_fn: Callable[[str], None] | None = None,
        update_task_fn: Callable[[str, str], None] | None = None,
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
        _print = print_fn or self._console.print
        # Store for use by slash command handlers
        self._use_styled = use_styled
        self._print = _print
        self._status_fn = status_fn
        self._section_fn = section_fn
        self._response_fn = response_fn
        self._update_task_fn = update_task_fn

        from azext_prototype.debug_log import log_flow

        # Load existing discovery state for context
        existing_context = ""
        if self._discovery_state.exists:
            self._discovery_state.load()
            existing_context = self._discovery_state.format_as_context()
            if existing_context and use_styled:
                self._console.print_info("Loaded existing discovery context from previous session.")
        else:
            self._discovery_state.load()  # Initialize empty state

        log_flow(
            "DiscoverySession.run",
            "Entry",
            has_items=self._discovery_state.has_items,
            item_count=len(self._discovery_state.items),
            pending=self._discovery_state.open_count,
            seed_context_len=len(seed_context),
            artifacts_len=len(artifacts),
            images=len(artifact_images) if artifact_images else 0,
            context_only=context_only,
            existing_context_len=len(existing_context),
        )

        # ---- Re-entry path: resume at first unanswered topic ----
        _reentry_handled = False
        if self._discovery_state.has_items:
            result = self._run_reentry(
                seed_context, artifacts, artifact_images, _input, _print, use_styled, context_only, status_fn
            )
            if result is not None:
                return result
            # None = all topics done, skip opening and fall through to free-form loop
            _reentry_handled = True

        # ---- Fallback when no agent is available ----
        if not self._biz_agent:
            if use_styled:
                self._console.print_warning("No biz-analyst agent available. Enter your requirements:")
            else:
                _print("No biz-analyst agent available. Enter your requirements:")
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

        # ---- Kick off the conversation (skipped on re-entry) ----
        if not _reentry_handled:
            opening = self._build_opening(seed_context, artifacts, existing_context, images=artifact_images)

            with self._maybe_spinner("Analyzing your input...", use_styled, status_fn=status_fn):
                response = self._chat(opening)

            # Update discovery state with the initial exchange
            self._exchange_count += 1
            self._discovery_state.update_from_exchange(opening, response, self._exchange_count)

            clean_response = self._clean(response)
            preamble, sections = parse_sections(clean_response)

            # Persist topics on first run so re-entry can resume them
            if sections and not self._discovery_state.has_items:
                topics = [
                    TrackedItem(
                        heading=s.heading,
                        detail=s.content,
                        kind="topic",
                        status="pending",
                        answer_exchange=None,
                    )
                    for s in sections
                ]
                self._discovery_state.set_items(topics)

            if sections:
                # Populate tree with ALL sections upfront
                if self._section_fn:
                    self._section_fn([(s.heading, s.level) for s in sections])

                # Section-at-a-time loop
                outcome = self._run_section_loop(sections, preamble, _input, _print, use_styled)
                if outcome == "cancelled":
                    return DiscoveryResult(
                        requirements="",
                        conversation=list(self._messages),
                        policy_overrides=[],
                        exchange_count=self._exchange_count,
                        cancelled=True,
                    )
                if outcome == "restart":
                    pass  # Fall through to free-form loop — state was reset by /restart
                elif outcome == "done":
                    # Jump to summary production
                    with self._maybe_spinner("Generating requirements summary...", use_styled, status_fn=status_fn):
                        summary = self._produce_summary()
                        overrides = self._extract_overrides(summary)
                    return DiscoveryResult(
                        requirements=summary,
                        conversation=list(self._messages),
                        policy_overrides=overrides,
                        exchange_count=self._exchange_count,
                    )
            else:
                # No sections → show full response (backward compat / conversational response)
                self._show_content(clean_response, use_styled, _print)
                self._update_token_status()
                if self._section_fn and not extract_section_headers(clean_response):
                    self._section_fn([("Discovery conversation", 2)])

            # ---- Check if agent needs more information ----
            # If context_only mode and agent signals READY, skip interactive loop
            if context_only and _READY_MARKER in response:
                if use_styled:
                    self._console.print_info("Context is sufficient. Proceeding with design.")
                else:
                    _print("Context is sufficient. Proceeding with design.")
                summary = self._produce_summary()
                overrides = self._extract_overrides(summary)
                return DiscoveryResult(
                    requirements=summary,
                    conversation=list(self._messages),
                    policy_overrides=overrides,
                    exchange_count=self._exchange_count,
                )

        # ---- Call-to-action so the user knows what to do next ----
        _cta = "Let me know if I missed anything above. Otherwise, are you ready to continue?"
        if use_styled:
            self._console.print_info(_cta)
        else:
            _print(_cta)

        # ---- Main conversation loop ----
        first_prompt = True
        while True:
            try:
                if use_styled:
                    # Use bordered prompt with instruction and status
                    instruction = DiscoveryPrompt.INSTRUCTION
                    user_input = self._prompt.prompt(
                        "> ",
                        instruction=instruction,
                        show_quit_hint=first_prompt,
                        open_count=self._discovery_state.open_count,
                    )
                    first_prompt = False
                else:
                    if first_prompt:
                        _print("[dim]Type 'continue' when finished, or 'quit' to cancel.[/dim]")
                        first_prompt = False
                    user_input = _input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input:
                continue

            # Check quit/done FIRST — before intent classifier to avoid
            # a wasteful AI call and ensure reliable exit behavior
            lower_input = user_input.lower()
            if lower_input in _QUIT_WORDS:
                return DiscoveryResult(
                    requirements="",
                    conversation=list(self._messages),
                    policy_overrides=[],
                    exchange_count=self._exchange_count,
                    cancelled=True,
                )

            if lower_input in _DONE_WORDS:
                break

            # Handle slash commands
            if lower_input in _SLASH_COMMANDS:
                cmd_result = self._handle_slash_command(lower_input)
                if cmd_result == "restart":
                    break  # Session was reset — exit free-form loop
                continue
            if lower_input.startswith("/why"):
                self._handle_why_command(user_input)
                continue

            # Natural language intent detection
            intent = self._intent_classifier.classify(user_input)
            if intent.kind == IntentKind.COMMAND:
                if intent.command == "/why":
                    self._handle_why_command(f"/why {intent.args}")
                else:
                    cmd_result = self._handle_slash_command(intent.command)
                    if cmd_result == "restart":
                        break
                continue
            if intent.kind == IntentKind.READ_FILES:
                self._handle_read_files(intent.args, _print, use_styled)
                continue

            self._exchange_count += 1

            with self._maybe_spinner("Thinking...", use_styled, status_fn=status_fn):
                response = self._chat(user_input)

            self._process_response(user_input, response, use_styled, _print)

            # Agent signalled convergence
            if _READY_MARKER in response:
                if use_styled:
                    self._console.print_info("Discovery complete. Press Enter to proceed, or keep typing.")
                else:
                    _print("Discovery complete. Press Enter to proceed, or keep typing.")
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
                with self._maybe_spinner("Thinking...", use_styled, status_fn=status_fn):
                    response = self._chat(more)
                self._process_response(more, response, use_styled, _print)

        # ---- Produce the final summary ----
        with self._maybe_spinner("Generating requirements summary...", use_styled, status_fn=status_fn):
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
        from azext_prototype.debug_log import log_ai_call, log_ai_response, log_error

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

        sys_chars = sum(len(m.content) if isinstance(m.content, str) else 0 for m in full)
        hist_chars = sum(len(m.content) if isinstance(m.content, str) else 0 for m in self._messages)
        log_ai_call(
            "DiscoverySession._chat",
            system_msgs=len(full),
            system_chars=sys_chars,
            history_msgs=len(self._messages),
            history_chars=hist_chars,
            user_content=user_content,
            model=getattr(self._context.ai_provider, "_model", "unknown"),
            temperature=self._biz_agent._temperature,
            max_tokens=self._biz_agent._max_tokens,
        )

        full.extend(self._messages)

        _t0 = __import__("time").perf_counter()
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
                log_error("DiscoverySession._chat", exc)
                raise

        _elapsed = __import__("time").perf_counter() - _t0
        usage = response.usage if hasattr(response, "usage") and response.usage else {}
        log_ai_response(
            "DiscoverySession._chat",
            elapsed=_elapsed,
            response_content=response.content,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        )

        self._token_tracker.record(response)
        self._messages.append(
            AIMessage(role="assistant", content=response.content),
        )
        return response.content

    def _chat_lightweight(self, user_content: str) -> str:
        """Call the AI with a minimal system prompt for classification tasks.

        Skips governance policies, templates, and architect context to
        keep the payload small (~0.5KB vs ~69KB).  Used for lightweight
        operations like identifying new topics from incremental context.

        Does NOT add messages to ``self._messages`` — the response is
        ephemeral.
        """
        from azext_prototype.debug_log import log_ai_call, log_ai_response

        assert self._context.ai_provider is not None

        system = AIMessage(
            role="system",
            content=(
                "You are a business analyst helping discover requirements for an Azure prototype. "
                "Respond concisely and precisely. Follow the formatting instructions in the user message."
            ),
        )
        user_msg = AIMessage(role="user", content=user_content)
        log_ai_call(
            "DiscoverySession._chat_lightweight",
            system_msgs=1,
            system_chars=len(system.content),
            history_msgs=0,
            history_chars=0,
            user_content=user_content,
            model=getattr(self._context.ai_provider, "_model", "unknown"),
            temperature=0.3,
            max_tokens=4096,
        )
        _t0 = __import__("time").perf_counter()
        response = self._context.ai_provider.chat(
            [system, user_msg],
            temperature=0.3,
            max_tokens=4096,
        )
        _elapsed = __import__("time").perf_counter() - _t0
        log_ai_response(
            "DiscoverySession._chat_lightweight",
            elapsed=_elapsed,
            response_content=response.content,
        )
        self._token_tracker.record(response)
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
            "In your initial response, cover ALL relevant technical areas "
            "using separate ## headings for each.  Ask 2–4 focused questions "
            "per area.  The system will present them to the user one at a "
            "time.  Make sure you cover the relevant areas before signalling "
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
            user_msgs = [
                m.content if isinstance(m.content, str) else str(m.content) for m in self._messages if m.role == "user"
            ]
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

    def _handle_slash_command(self, command: str) -> str | None:
        """Handle slash commands like /open, /status, /confirmed.

        Returns ``"restart"`` when ``/restart`` is executed so the caller
        can break out of the current loop.  Returns ``None`` otherwise.
        """
        _p = self._print
        styled = self._use_styled
        if command == "/open":
            _p("")
            _p(self._discovery_state.format_open_items())
            _p("")
        elif command == "/confirmed":
            _p("")
            _p(self._discovery_state.format_confirmed_items())
            _p("")
        elif command == "/status":
            _p("")
            _p(f"Discovery Status: {self._discovery_state.format_status_summary()}")
            _p("")
            if self._discovery_state.open_count > 0:
                _p(self._discovery_state.format_open_items())
                _p("")
        elif command == "/summary":
            if not self._biz_agent or not self._context.ai_provider:
                if styled:
                    self._console.print_warning("No AI agent available for summary.")
                else:
                    _p("No AI agent available for summary.")
                return
            _p("")
            with self._maybe_spinner("Generating summary...", styled, status_fn=self._status_fn):
                summary = self._chat(
                    "Please provide a concise summary of everything we've "
                    "established so far — confirmed requirements, open questions, "
                    "constraints, and key decisions. This is a mid-session "
                    "checkpoint, not the final summary."
                )
            if styled:
                self._console.print_agent_response(self._clean(summary))
            elif self._response_fn:
                self._response_fn(self._clean(summary))
            else:
                _p(self._clean(summary))
        elif command == "/restart":
            _p("")
            if styled:
                self._console.print_warning("Restarting discovery session...")
            else:
                _p("Restarting discovery session...")
            self._discovery_state.reset()
            self._messages.clear()
            self._exchange_count = 0
            if self._biz_agent and self._context.ai_provider:
                opening = "I'd like to design a new Azure prototype."
                with self._maybe_spinner("Starting fresh...", styled, status_fn=self._status_fn):
                    response = self._chat(opening)
                self._exchange_count += 1
                self._discovery_state.update_from_exchange(opening, response, self._exchange_count)
                if styled:
                    self._console.print_agent_response(self._clean(response))
                elif self._response_fn:
                    self._response_fn(self._clean(response))
                else:
                    _p(self._clean(response))
            return "restart"
        elif command == "/help":
            _p("")
            _p("Available commands:")
            _p("  /open      - List open items needing resolution")
            _p("  /confirmed - List confirmed requirements")
            _p("  /status    - Show overall discovery status")
            _p("  /summary   - Show a narrative summary of progress so far")
            _p("  /why <topic> - Find the exchange where a topic was discussed")
            _p("  /restart   - Clear state and restart discovery from scratch")
            _p("  /help      - Show this help message")
            _p("  done       - Complete discovery and proceed to design")
            _p("  quit       - Cancel and exit")
            _p("")
            _p("  You can also use natural language:")
            _p("    'what are the open items'     instead of  /open")
            _p("    'where do we stand'           instead of  /status")
            _p("    'give me a summary'           instead of  /summary")
            _p("    'why did we choose Cosmos DB'  instead of  /why Cosmos DB")
            _p("    'read artifacts from ./specs'  reads files into the session")
            _p("")
        return None

    def _handle_why_command(self, raw_input: str) -> None:
        """Handle ``/why <query>`` — find the exchange where a topic was discussed."""
        _p = self._print
        query = raw_input[4:].strip()
        if not query:
            _p("")
            _p("Usage: /why <topic>")
            _p("  Example: /why managed identity")
            _p("")
            return

        matches = self._discovery_state.search_history(query)
        _p("")
        if not matches:
            _p(f"No exchanges found mentioning '{query}'.")
        else:
            _p(f"Found {len(matches)} exchange(s) mentioning '{query}':")
            _p("")
            for m in matches:
                ex_num = m.get("exchange", "?")
                topic = self._discovery_state.topic_at_exchange(ex_num) if isinstance(ex_num, int) else None
                header = f"  Exchange {ex_num}"
                if topic:
                    header += f' (topic: "{topic}")'
                _p(f"{header}:")
                user_text = m.get("user", "")
                asst_text = m.get("assistant", "")
                user_snippet = user_text[:500] + ("..." if len(user_text) > 500 else "")
                asst_snippet = asst_text[:500] + ("..." if len(asst_text) > 500 else "")
                _p(f"    You: {user_snippet}")
                _p(f"    Agent: {asst_snippet}")
                _p("")
        _p("")

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

    def _process_response(
        self,
        user_input: str,
        response: str,
        use_styled: bool,
        _print: Callable,
    ) -> str:
        """Common post-response pipeline: persist, extract items, display, and emit sections.

        Returns the cleaned response text.
        """
        self._discovery_state.update_from_exchange(user_input, response, self._exchange_count)
        self._extract_items_from_response(response)
        clean = self._clean(response)
        self._show_content(clean, use_styled, _print)
        self._update_token_status()
        self._emit_sections(clean)
        return clean

    def _emit_sections(self, response: str) -> None:
        """Notify section_fn callback with any headings found in *response*."""
        if not self._section_fn:
            return
        headers = extract_section_headers(response)
        if headers:
            self._section_fn(headers)

    def _update_token_status(self) -> None:
        """Push token usage to the TUI status bar via ``status_fn("tokens")``.

        Always pushes an update after an AI call — if the provider didn't
        return usage data, shows a turn counter instead of leaving the
        elapsed timer stuck.
        """
        if self._status_fn:
            token_text = self._token_tracker.format_status()
            if not token_text:
                turns = self._token_tracker.turn_count
                token_text = f"Turn {turns}" if turns > 0 else ""
            if token_text:
                self._status_fn(token_text, "tokens")

    @staticmethod
    def _clean(text: str) -> str:
        """Strip invisible markers so the user sees natural text."""
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
