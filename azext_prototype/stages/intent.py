"""Natural language intent classification for interactive sessions.

Provides a two-tier classifier:

1. **AI-powered** (primary) — when an AI provider is available, sends a
   short classification prompt listing the session's available commands.
   Uses low temperature (0.0) and low max_tokens (150) for fast,
   deterministic responses.
2. **Keyword/regex fallback** — when no AI provider is available (or the
   AI call fails), keyword/phrase/regex scoring runs as a zero-latency
   fallback.

Each session registers its own command definitions via factory functions.
The classifier picks AI or fallback automatically.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------- #
# Types
# -------------------------------------------------------------------- #


class IntentKind(Enum):
    """Classification of user input."""

    COMMAND = "command"
    READ_FILES = "read_files"
    CONVERSATIONAL = "conversational"


@dataclass
class IntentResult:
    """Result of classifying user input."""

    kind: IntentKind
    command: str = ""
    args: str = ""
    original_input: str = ""
    confidence: float = 0.0


@dataclass
class CommandDef:
    """Definition of a command for AI classification prompt."""

    command: str
    description: str
    has_args: bool = False
    arg_description: str = ""


@dataclass
class IntentPattern:
    """Keyword/regex pattern for fallback classification."""

    command: str
    keywords: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)
    regex_patterns: list[str] = field(default_factory=list)
    arg_extractor: Callable[[str], str] | None = None
    min_confidence: float = 0.5


# -------------------------------------------------------------------- #
# File-read regex — cross-session
# -------------------------------------------------------------------- #

_READ_FILE_RE = re.compile(
    r"(?:read|load|import)\s+(?:artifacts?|files?|documents?)\s+from\s+(.+)",
    re.IGNORECASE,
)


# -------------------------------------------------------------------- #
# IntentClassifier
# -------------------------------------------------------------------- #


class IntentClassifier:
    """Two-tier intent classifier: AI-first with keyword fallback.

    Parameters
    ----------
    ai_provider:
        Optional AI provider for AI-powered classification.
    token_tracker:
        Optional token tracker for recording classification costs.
    """

    def __init__(
        self,
        ai_provider: Any = None,
        token_tracker: Any = None,
    ) -> None:
        self._ai_provider = ai_provider
        self._token_tracker = token_tracker
        self._patterns: list[IntentPattern] = []
        self._command_defs: list[CommandDef] = []

    def register(self, pattern: IntentPattern) -> None:
        """Register a keyword/regex fallback pattern."""
        self._patterns.append(pattern)

    def register_many(self, patterns: list[IntentPattern]) -> None:
        """Register multiple keyword/regex fallback patterns."""
        self._patterns.extend(patterns)

    def add_command_def(self, cmd_def: CommandDef) -> None:
        """Add a command definition for the AI classification prompt."""
        self._command_defs.append(cmd_def)

    def add_command_defs(self, defs: list[CommandDef]) -> None:
        """Add multiple command definitions."""
        self._command_defs.extend(defs)

    # ------------------------------------------------------------------ #
    # Public — classify
    # ------------------------------------------------------------------ #

    def classify(self, user_input: str) -> IntentResult:
        """Classify user input as COMMAND, READ_FILES, or CONVERSATIONAL.

        1. Explicit slash commands (``/...``) → CONVERSATIONAL (pass-through)
        2. File-read regex → READ_FILES
        3. Keyword/regex scoring (fast, no network) → COMMAND if confident
        4. AI classification (if available, keywords uncertain) → COMMAND or CONVERSATIONAL
        """
        if not user_input or not user_input.strip():
            return IntentResult(kind=IntentKind.CONVERSATIONAL, original_input=user_input)

        stripped = user_input.strip()

        # 1. Explicit slash commands — let sessions handle directly
        if stripped.startswith("/"):
            return IntentResult(kind=IntentKind.CONVERSATIONAL, original_input=user_input)

        # 2. File-read detection
        m = _READ_FILE_RE.search(stripped)
        if m:
            path_str = m.group(1).strip().strip("'\"")
            return IntentResult(
                kind=IntentKind.READ_FILES,
                command="__read_files",
                args=path_str,
                original_input=user_input,
                confidence=0.9,
            )

        # 3. Keyword/regex scoring (fast path — no API call)
        keyword_result = self._classify_with_keywords(stripped)
        if keyword_result.kind == IntentKind.COMMAND:
            return keyword_result

        # 4. AI classification — only when keywords had SOME signal
        #    (confidence > 0 means some keywords matched but not enough).
        #    When confidence is 0.0, no keywords matched at all, so the
        #    input is almost certainly conversational — skip the AI call.
        if self._ai_provider and self._command_defs and keyword_result.confidence > 0:
            ai_result = self._classify_with_ai(stripped)
            if ai_result is not None:
                return ai_result

        return keyword_result

    # ------------------------------------------------------------------ #
    # Internal — AI classification
    # ------------------------------------------------------------------ #

    def _classify_with_ai(self, user_input: str) -> IntentResult | None:
        """Use the AI provider to classify the input.

        Returns None on any error, allowing fallback to keyword scoring.
        """
        from azext_prototype.ai.provider import AIMessage

        system_prompt = self._build_classification_prompt()
        messages = [
            AIMessage(role="system", content=system_prompt),
            AIMessage(role="user", content=user_input),
        ]

        try:
            response = self._ai_provider.chat(
                messages,
                temperature=0.0,
                max_tokens=150,
            )
            if self._token_tracker:
                self._token_tracker.record(response)

            return self._parse_ai_response(response.content, user_input)
        except Exception:
            logger.debug("AI classification failed, falling back to keywords", exc_info=True)
            return None

    def _build_classification_prompt(self) -> str:
        """Build the system prompt listing available commands."""
        lines = [
            "You are a command classifier. Given user input, determine if it "
            "maps to one of these commands or is conversational input for the "
            "AI assistant.",
            "",
            "Available commands:",
        ]

        for cmd_def in self._command_defs:
            if cmd_def.has_args:
                lines.append(f"- {cmd_def.command} <{cmd_def.arg_description}> — {cmd_def.description}")
            else:
                lines.append(f"- {cmd_def.command} — {cmd_def.description}")

        lines.extend(
            [
                "(Plus special: __prompt_context — user wants to provide new context/files)",
                "(Plus special: __read_files <path> — user wants to read files from a path)",
                "",
                'Respond with JSON only: {"command": "/open", "args": "", "is_command": true}',
                "If the input is conversational (design feedback, questions, etc.), "
                'respond: {"command": "", "args": "", "is_command": false}',
            ]
        )

        return "\n".join(lines)

    def _parse_ai_response(self, content: str, original_input: str) -> IntentResult | None:
        """Parse the AI's JSON response into an IntentResult."""
        text = content.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("Could not parse AI classification response: %s", text[:100])
            return None

        if not isinstance(data, dict):
            return None

        is_command = data.get("is_command", False)
        if not is_command:
            return IntentResult(
                kind=IntentKind.CONVERSATIONAL,
                original_input=original_input,
                confidence=0.8,
            )

        command = data.get("command", "")
        args = data.get("args", "")

        if not command:
            return IntentResult(
                kind=IntentKind.CONVERSATIONAL,
                original_input=original_input,
                confidence=0.5,
            )

        return IntentResult(
            kind=IntentKind.COMMAND,
            command=command,
            args=str(args),
            original_input=original_input,
            confidence=0.9,
        )

    # ------------------------------------------------------------------ #
    # Internal — keyword/regex fallback
    # ------------------------------------------------------------------ #

    def _classify_with_keywords(self, user_input: str) -> IntentResult:
        """Score registered patterns against user input."""
        lower = user_input.lower()
        best_score = 0.0
        best_pattern: IntentPattern | None = None

        for pattern in self._patterns:
            score = 0.0

            # Keyword scoring: +0.2 each
            for kw in pattern.keywords:
                if kw.lower() in lower:
                    score += 0.2

            # Phrase scoring: +0.4 each
            for phrase in pattern.phrases:
                if phrase.lower() in lower:
                    score += 0.4

            # Regex scoring: +0.6 each
            for rx in pattern.regex_patterns:
                if re.search(rx, user_input, re.IGNORECASE):
                    score += 0.6

            score = min(score, 1.0)

            if score > best_score:
                best_score = score
                best_pattern = pattern

        if best_pattern and best_score >= best_pattern.min_confidence:
            args = ""
            if best_pattern.arg_extractor:
                args = best_pattern.arg_extractor(user_input)

            return IntentResult(
                kind=IntentKind.COMMAND,
                command=best_pattern.command,
                args=args,
                original_input=user_input,
                confidence=best_score,
            )

        # Return the actual best_score even when below threshold — this
        # allows the caller to detect partial keyword signal and decide
        # whether to try AI classification.
        return IntentResult(
            kind=IntentKind.CONVERSATIONAL,
            original_input=user_input,
            confidence=best_score,
        )


# -------------------------------------------------------------------- #
# Arg extractors
# -------------------------------------------------------------------- #


def _extract_stage_numbers(text: str) -> str:
    """Extract stage numbers from text like 'stage 3' or 'stages 3 and 4'."""
    numbers = re.findall(r"\d+", text)
    return " ".join(numbers)


def _extract_why_args(text: str) -> str:
    """Extract the topic from 'why did we choose X' style input."""
    # Remove common prefixes
    cleaned = re.sub(
        r"^(?:why\s+(?:did\s+we\s+)?(?:choose|pick|select|use|go\s+with)?)\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip(" ?")
    return cleaned


def _extract_show_number(text: str) -> str:
    """Extract item number from 'show item 3' style input."""
    numbers = re.findall(r"\d+", text)
    return numbers[0] if numbers else ""


# -------------------------------------------------------------------- #
# Factory functions — per-session classifiers
# -------------------------------------------------------------------- #


def build_discovery_classifier(
    ai_provider: Any = None,
    token_tracker: Any = None,
) -> IntentClassifier:
    """Build an intent classifier for the discovery session."""
    c = IntentClassifier(ai_provider=ai_provider, token_tracker=token_tracker)

    # Command definitions (for AI prompt)
    c.add_command_defs(
        [
            CommandDef("/open", "Show open items needing resolution"),
            CommandDef("/confirmed", "Show confirmed requirements"),
            CommandDef("/status", "Show discovery progress"),
            CommandDef("/summary", "Generate a narrative summary"),
            CommandDef("/why", "Search for when a topic was discussed", has_args=True, arg_description="topic"),
            CommandDef("/restart", "Clear state and restart discovery"),
        ]
    )

    # Keyword/regex fallback patterns
    c.register_many(
        [
            IntentPattern(
                command="/open",
                keywords=["open"],
                phrases=["open items", "open questions", "what's open", "unresolved"],
                regex_patterns=[r"what(?:'s| are| is) (?:the )?open", r"what(?:'s| is) (?:still )?unresolved"],
            ),
            IntentPattern(
                command="/confirmed",
                keywords=["confirmed"],
                phrases=["confirmed requirements", "what's confirmed", "confirmed items"],
                regex_patterns=[r"what(?:'s| are| is) confirmed"],
            ),
            IntentPattern(
                command="/status",
                keywords=[],
                phrases=["where do we stand", "what's the status", "discovery status", "how far along"],
                regex_patterns=[r"(?:where|how)\s+(?:do\s+we|are\s+we)\s+stand"],
            ),
            IntentPattern(
                command="/summary",
                keywords=[],
                phrases=["give me a summary", "summarize", "show summary"],
                regex_patterns=[r"(?:give|show|generate)\s+(?:me\s+)?a?\s*summary"],
            ),
            IntentPattern(
                command="/why",
                keywords=[],
                phrases=[],
                regex_patterns=[r"why\s+did\s+we\s+(?:choose|pick|select|use|go\s+with)"],
                arg_extractor=_extract_why_args,
            ),
            IntentPattern(
                command="/restart",
                keywords=[],
                phrases=["start over", "restart", "start from scratch", "begin again"],
                regex_patterns=[r"(?:start|begin)\s+(?:over|from\s+scratch|again)"],
            ),
            IntentPattern(
                command="__prompt_context",
                keywords=[],
                phrases=["i have new context", "i have some context", "let me provide context"],
                regex_patterns=[r"i\s+have\s+(?:new|some|additional)\s+context"],
            ),
        ]
    )

    return c


def build_build_classifier(
    ai_provider: Any = None,
    token_tracker: Any = None,
) -> IntentClassifier:
    """Build an intent classifier for the build session."""
    c = IntentClassifier(ai_provider=ai_provider, token_tracker=token_tracker)

    c.add_command_defs(
        [
            CommandDef("/status", "Show stage completion summary"),
            CommandDef("/stages", "Show full deployment plan"),
            CommandDef("/files", "List all generated files"),
            CommandDef("/policy", "Show policy check summary"),
            CommandDef("/describe", "Show detailed description of a stage", has_args=True, arg_description="N"),
        ]
    )

    c.register_many(
        [
            IntentPattern(
                command="/status",
                keywords=[],
                phrases=["build status", "what's the status", "how's the build"],
                regex_patterns=[r"what(?:'s| is)\s+the\s+(?:build\s+)?status"],
            ),
            IntentPattern(
                command="/stages",
                keywords=[],
                phrases=["show stages", "list stages", "deployment plan"],
                regex_patterns=[r"(?:show|list|display)\s+(?:the\s+)?stages"],
            ),
            IntentPattern(
                command="/files",
                keywords=[],
                phrases=["generated files", "show files", "list files", "what files"],
                regex_patterns=[
                    r"(?:show|list|display)\s+(?:me\s+)?(?:the\s+)?(?:generated\s+)?files",
                    r"what(?:'s| are)\s+(?:the\s+)?(?:generated\s+)?files",
                ],
            ),
            IntentPattern(
                command="/policy",
                keywords=[],
                phrases=["policy status", "policy check", "policy summary"],
                regex_patterns=[r"(?:show|check)\s+(?:the\s+)?polic(?:y|ies)"],
            ),
            IntentPattern(
                command="/describe",
                keywords=[],
                phrases=[],
                regex_patterns=[
                    r"describe\s+stage\s+\d+",
                    r"what(?:'s| is)\s+(?:in|being\s+built\s+in)\s+stage\s+\d+",
                    r"show\s+(?:me\s+)?stage\s+\d+\s+details?",
                ],
                arg_extractor=_extract_stage_numbers,
            ),
        ]
    )

    return c


def build_deploy_classifier(
    ai_provider: Any = None,
    token_tracker: Any = None,
) -> IntentClassifier:
    """Build an intent classifier for the deploy session."""
    c = IntentClassifier(ai_provider=ai_provider, token_tracker=token_tracker)

    c.add_command_defs(
        [
            CommandDef("/deploy", "Deploy stage N or all pending stages", has_args=True, arg_description="N|all"),
            CommandDef("/rollback", "Roll back stage N or all", has_args=True, arg_description="N|all"),
            CommandDef("/redeploy", "Rollback + redeploy stage N", has_args=True, arg_description="N"),
            CommandDef("/plan", "Show what-if/terraform plan for stage N", has_args=True, arg_description="N"),
            CommandDef("/outputs", "Show captured deployment outputs"),
            CommandDef("/preflight", "Re-run preflight checks"),
            CommandDef("/login", "Run az login interactively"),
            CommandDef("/status", "Show deployment progress per stage"),
            CommandDef("/describe", "Show detailed description of a stage", has_args=True, arg_description="N"),
        ]
    )

    c.register_many(
        [
            IntentPattern(
                command="/deploy",
                keywords=[],
                phrases=[],
                regex_patterns=[
                    r"deploy\s+(?:stage\s+)?\d+",
                    r"deploy\s+(?:all\s+)?(?:pending\s+)?stages",
                    r"deploy\s+stages?\s+\d+(?:\s+and\s+\d+)*",
                    r"deploy\s+all",
                ],
                arg_extractor=lambda t: (
                    _extract_stage_numbers(t) or "all"
                    if re.search(r"\ball\b", t, re.IGNORECASE)
                    else _extract_stage_numbers(t)
                ),
            ),
            IntentPattern(
                command="/rollback",
                keywords=[],
                phrases=[],
                regex_patterns=[
                    r"rollback\s+(?:stage\s+)?\d+",
                    r"roll\s+back\s+(?:stage\s+)?\d+",
                    r"rollback\s+all",
                    r"roll\s+back\s+all",
                    r"undo\s+(?:stage\s+)?\d+",
                    r"undo\s+(?:the\s+)?deploy",
                ],
                arg_extractor=lambda t: "all" if re.search(r"\ball\b", t, re.IGNORECASE) else _extract_stage_numbers(t),
            ),
            IntentPattern(
                command="/redeploy",
                keywords=[],
                phrases=[],
                regex_patterns=[
                    r"redeploy\s+(?:stage\s+)?\d+",
                    r"re-deploy\s+(?:stage\s+)?\d+",
                ],
                arg_extractor=_extract_stage_numbers,
            ),
            IntentPattern(
                command="/plan",
                keywords=[],
                phrases=["show plan", "what-if", "terraform plan"],
                regex_patterns=[
                    r"(?:show\s+)?plan\s+(?:for\s+)?stage\s+\d+",
                    r"what.?if\s+(?:for\s+)?stage\s+\d+",
                ],
                arg_extractor=_extract_stage_numbers,
            ),
            IntentPattern(
                command="/outputs",
                keywords=[],
                phrases=["deployment outputs", "show outputs", "captured outputs"],
                regex_patterns=[r"(?:show|display|list)\s+(?:the\s+)?(?:deployment\s+)?outputs"],
            ),
            IntentPattern(
                command="/preflight",
                keywords=[],
                phrases=["run preflight", "preflight checks", "check prerequisites"],
                regex_patterns=[r"(?:run|re-?run)\s+preflight"],
            ),
            IntentPattern(
                command="/login",
                keywords=[],
                phrases=["az login", "azure login", "log in"],
                regex_patterns=[r"(?:az|azure)\s+login"],
            ),
            IntentPattern(
                command="/status",
                keywords=[],
                phrases=["deployment status", "deploy status", "what's deployed"],
                regex_patterns=[
                    r"what(?:'s| is)\s+(?:the\s+)?deploy(?:ment)?\s+status",
                    r"what(?:'s| is)\s+deployed",
                ],
            ),
            IntentPattern(
                command="/describe",
                keywords=[],
                phrases=[],
                regex_patterns=[
                    r"describe\s+stage\s+\d+",
                    r"what(?:'s| is)\s+(?:in|being\s+deployed\s+in)\s+stage\s+\d+",
                    r"show\s+(?:me\s+)?stage\s+\d+\s+details?",
                ],
                arg_extractor=_extract_stage_numbers,
            ),
        ]
    )

    return c


def build_backlog_classifier(
    ai_provider: Any = None,
    token_tracker: Any = None,
) -> IntentClassifier:
    """Build an intent classifier for the backlog session.

    Intentionally omits ``/add`` — "add a story about X" should fall
    through to the AI mutation path.
    """
    c = IntentClassifier(ai_provider=ai_provider, token_tracker=token_tracker)

    c.add_command_defs(
        [
            CommandDef("/list", "Show all items grouped by epic"),
            CommandDef("/show", "Show item N with full details", has_args=True, arg_description="N"),
            CommandDef("/remove", "Remove item N", has_args=True, arg_description="N"),
            CommandDef("/preview", "Show what will be pushed"),
            CommandDef("/save", "Save to concept/docs/BACKLOG.md"),
            CommandDef("/push", "Push all pending items or item N", has_args=True, arg_description="N"),
            CommandDef("/status", "Show push status per item"),
        ]
    )

    c.register_many(
        [
            IntentPattern(
                command="/list",
                keywords=[],
                phrases=["show all items", "list items", "list all", "show backlog", "show items"],
                regex_patterns=[r"(?:show|list|display)\s+(?:all\s+)?(?:the\s+)?(?:backlog\s+)?items"],
            ),
            IntentPattern(
                command="/show",
                keywords=[],
                phrases=[],
                regex_patterns=[
                    r"show\s+(?:me\s+)?item\s+\d+",
                    r"show\s+(?:me\s+)?(?:story|feature)\s+\d+",
                    r"details?\s+(?:for|of|on)\s+(?:item|story)\s+\d+",
                ],
                arg_extractor=_extract_show_number,
            ),
            IntentPattern(
                command="/remove",
                keywords=[],
                phrases=[],
                regex_patterns=[
                    r"remove\s+item\s+\d+",
                    r"delete\s+item\s+\d+",
                    r"remove\s+(?:story|feature)\s+\d+",
                ],
                arg_extractor=_extract_show_number,
            ),
            IntentPattern(
                command="/preview",
                keywords=[],
                phrases=["preview", "show preview", "what will be pushed"],
                regex_patterns=[r"(?:show\s+)?(?:the\s+)?preview"],
            ),
            IntentPattern(
                command="/save",
                keywords=[],
                phrases=["save backlog", "save to file", "save to markdown"],
                regex_patterns=[r"save\s+(?:the\s+)?(?:backlog|items)"],
            ),
            IntentPattern(
                command="/push",
                keywords=[],
                phrases=[],
                regex_patterns=[
                    r"push\s+(?:item\s+)?\d+",
                    r"push\s+(?:all|items|everything)",
                    r"create\s+(?:the\s+)?(?:issues?|work\s+items?)",
                ],
                arg_extractor=_extract_show_number,
            ),
            IntentPattern(
                command="/status",
                keywords=[],
                phrases=["push status", "item status", "what's been pushed"],
                regex_patterns=[r"what(?:'s| is)\s+(?:the\s+)?(?:push\s+)?status"],
            ),
        ]
    )

    return c


# -------------------------------------------------------------------- #
# File reading helper
# -------------------------------------------------------------------- #


def read_files_for_session(
    path_str: str,
    project_dir: str,
    print_fn: Callable[[str], None],
) -> tuple[str, list[dict]]:
    """Read files from a path for mid-session injection.

    Returns ``(text_content, images_list)`` where images_list contains
    dicts with ``filename``, ``data``, ``mime`` keys for vision API use.
    """
    from azext_prototype.parsers.binary_reader import read_file

    # Expand ~ and resolve relative paths
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = Path(project_dir) / path

    if not path.exists():
        print_fn(f"  Path not found: {path}")
        return "", []

    text_parts: list[str] = []
    images: list[dict] = []

    if path.is_file():
        files = [path]
    else:
        # Read all files in directory (non-recursive, skip hidden)
        files = sorted(f for f in path.iterdir() if f.is_file() and not f.name.startswith("."))

    for file_path in files:
        result = read_file(file_path)
        if result.error:
            print_fn(f"  Could not read {file_path.name}: {result.error}")
            continue

        if result.text:
            text_parts.append(f"## {file_path.name}\n\n{result.text}")

        if result.image_data and result.mime_type:
            images.append(
                {
                    "filename": file_path.name,
                    "data": result.image_data,
                    "mime": result.mime_type,
                }
            )

        for emb in result.embedded_images:
            images.append(
                {
                    "filename": f"{file_path.name}:{emb.source}",
                    "data": emb.data,
                    "mime": emb.mime_type,
                }
            )

    if text_parts:
        print_fn(f"  Read {len(text_parts)} file(s) from {path_str}")
    elif images:
        print_fn(f"  Read {len(images)} image(s) from {path_str}")
    else:
        print_fn(f"  No readable files found in {path_str}")

    return "\n\n---\n\n".join(text_parts), images
