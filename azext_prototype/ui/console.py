"""Rich-based console utilities for styled CLI output.

Provides Claude Code-inspired UI with:
- Color scheme: dim gray for background, white for content, purple/green for callouts
- Bordered prompts that expand with content
- Multi-line input support (Shift+Enter or backslash continuation)
- Progress indicators for file operations and API calls
- System info display
"""

from __future__ import annotations

import re
import shutil
import sys
import time
from contextlib import contextmanager
from typing import Iterator

from prompt_toolkit import PromptSession
from prompt_toolkit.input import vt100_parser
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style as PTStyle
from rich.console import Console as RichConsole
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
)
from rich.style import Style
from rich.text import Text
from rich.theme import Theme

# -------------------------------------------------------------------- #
# Color scheme (Claude Code inspired)
# -------------------------------------------------------------------- #

THEME = Theme({
    # Background/secondary text
    "dim": "#888888",
    "muted": "#666666",

    # Primary content — bright for readability on dark terminals
    "content": "bright_white",

    # Callouts and highlights
    "success": "bright_green",
    "error": "bright_red",
    "warning": "bright_yellow",
    "info": "bright_cyan",
    "accent": "bright_magenta",  # Purple-ish for emphasis

    # Prompt styling — #555555 is visible on dark terminals without being bright
    "prompt.border": "#555555",
    "prompt.instruction": "bright_cyan",
    "prompt.input": "bright_white",

    # Progress indicators
    "progress.description": "bright_white",
    "progress.percentage": "bright_cyan",
    "progress.bar.complete": "bright_green",
    "progress.bar.finished": "bright_green",

    # Agent/stage names
    "agent": "bright_magenta bold",
    "stage": "bright_cyan bold",

    # File paths
    "path": "bright_cyan",

    # Markdown rendering — colour hierarchy for contrast on dark terminals:
    #   Headers: coloured + bold (stand out from body text)
    #   Body:    white (readable but not blinding)
    #   Bold:    bright_white bold (pops against regular white body)
    #   Code:    green (distinct from prose)
    #   Markers: cyan (numbered/bullet prefixes contrast with white text)
    "markdown.paragraph": "white",
    "markdown.h1": "bright_magenta bold underline",
    "markdown.h2": "bright_magenta bold",
    "markdown.h3": "bright_cyan bold",
    "markdown.h4": "bright_cyan italic",
    "markdown.bold": "bright_white bold",
    "markdown.italic": "white italic",
    "markdown.code": "bright_green",
    "markdown.code_block": "bright_green",
    "markdown.block_quote": "bright_yellow italic",
    "markdown.item.bullet": "bright_cyan",
    "markdown.item.number": "bright_cyan",
    "markdown.link": "bright_cyan underline",
    "markdown.link_url": "bright_cyan",
    "markdown.hr": "#555555",
})

# -------------------------------------------------------------------- #
# Markdown preprocessing
# -------------------------------------------------------------------- #

_ORDERED_LIST_RE = re.compile(r"^(\s*)(\d+)\.\s", re.MULTILINE)


def _preprocess_markdown(content: str) -> str:
    """Preprocess markdown for better terminal rendering.

    Rich strips periods from ordered list numbers (``1. text`` → ``1 text``).
    Convert ordered list items to bold-numbered paragraphs so the period is
    preserved:  ``1. text`` → ``**1.** text``.

    Leading whitespace is intentionally stripped (``\\1`` omitted) because the
    conversion breaks list structure—items become plain paragraphs.  Without
    this, numbered items that follow nested bullets inherit the bullet
    indentation and render with an unwanted tab offset.
    """
    return _ORDERED_LIST_RE.sub(r"**\2.** ", content)


# prompt_toolkit style for the input area
PT_STYLE = PTStyle.from_dict({
    "prompt": "#888888",
    "": "#ffffff",  # Default text color
    # Toolbar below the prompt — noreverse prevents the default white-on-white
    # inversion that prompt_toolkit applies.  Keep text dim like Claude Code's
    # status bar.
    "bottom-toolbar": "noreverse #888888",
})


class Console:
    """Styled console output with Claude Code-inspired theming.

    Provides:
    - Colored output with semantic styles
    - Progress indicators for long operations
    - Bordered panels for important messages
    - Consistent formatting throughout the CLI
    """

    def __init__(self):
        self._console = RichConsole(theme=THEME, highlight=False)

    # ------------------------------------------------------------------ #
    # Basic output
    # ------------------------------------------------------------------ #

    def print(self, message: str = "", style: str | None = None, **kwargs):
        """Print a message with optional styling."""
        self._console.print(message, style=style, **kwargs)

    def print_dim(self, message: str):
        """Print dimmed/secondary text."""
        self._console.print(message, style="dim")

    def print_success(self, message: str):
        """Print a success message (green)."""
        self._console.print(f"[success]✓[/success] {message}")

    def print_error(self, message: str):
        """Print an error message (red)."""
        self._console.print(f"[error]✗[/error] {message}")

    def print_warning(self, message: str):
        """Print a warning message (yellow)."""
        self._console.print(f"[warning]![/warning] {message}")

    def print_info(self, message: str):
        """Print an info message (cyan)."""
        self._console.print(f"[info]→[/info] {message}")

    def clear_last_line(self):
        """Erase the previous terminal line (cursor up + clear).

        Used after an interactive prompt where the user pressed Enter
        with no input, leaving a stale ``> `` line on screen.
        """
        # ANSI: CSI A = cursor up one line, CSI 2K = clear entire line
        sys.stdout.write("\033[A\033[2K\r")
        sys.stdout.flush()

    # ------------------------------------------------------------------ #
    # Structured output
    # ------------------------------------------------------------------ #

    def print_header(self, title: str):
        """Print a section header."""
        self._console.print()
        self._console.print(f"[accent bold]{title}[/accent bold]")
        self._console.print()

    def print_agent_response(self, content: str):
        """Print an agent's response with proper formatting.

        Renders markdown syntax (headings, bold, bullets, code blocks, etc.)
        as styled terminal output using Rich's Markdown renderer.
        """
        self._console.print()
        self._console.print(Markdown(_preprocess_markdown(content)))
        self._console.print()

    def print_token_status(self, status_text: str):
        """Print a right-justified dim token status line.

        Used after AI responses to show token usage::

            console.print_token_status("1,847 tokens this turn · 12,340 session · ~62%")
        """
        if not status_text:
            return
        width = self._console.size.width or 80
        padded = status_text.rjust(width)
        self._console.print(f"[muted]{padded}[/muted]", highlight=False)

    def print_file_list(self, files: list[str], success: bool = True):
        """Print a list of files with status indicators."""
        style = "success" if success else "error"
        marker = "✓" if success else "✗"
        for f in files:
            self._console.print(f"    [{style}]{marker}[/{style}] [path]{f}[/path]")

    # ------------------------------------------------------------------ #
    # Progress indicators
    # ------------------------------------------------------------------ #

    @contextmanager
    def progress_files(self, description: str = "Reading files") -> Iterator[Progress]:
        """Context manager for file reading progress.

        Usage:
            with console.progress_files("Reading artifacts") as progress:
                task = progress.add_task("Processing...", total=len(files))
                for f in files:
                    process(f)
                    progress.advance(task)
        """
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("({task.completed}/{task.total})"),
            console=self._console,
            transient=True,
        )
        with progress:
            yield progress

    @contextmanager
    def spinner(self, message: str) -> Iterator[None]:
        """Show a spinner while an operation is in progress.

        On completion, prints a persistent line with elapsed time so the
        user can see what completed and how long it took.

        Usage:
            with console.spinner("Generating architecture..."):
                result = agent.execute(task)
        """
        start = time.monotonic()
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=self._console,
            transient=True,
        )
        with progress:
            progress.add_task(message, total=None)
            yield
        # Print persistent completion line after spinner clears
        elapsed = time.monotonic() - start
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        self._console.print(
            f"[success]✓[/success] {message} completed. ({h}:{m:02d}:{s:02d})"
        )

    @contextmanager
    def status(self, message: str) -> Iterator[None]:
        """Show a status message with spinner (alias for spinner)."""
        with self.spinner(message):
            yield

    # ------------------------------------------------------------------ #
    # Panels and boxes
    # ------------------------------------------------------------------ #

    def panel(
        self,
        content: str,
        title: str | None = None,
        border_style: str = "prompt.border",
        padding: tuple[int, int] = (0, 1),
    ):
        """Print content in a bordered panel."""
        self._console.print(Panel(
            content,
            title=title,
            border_style=border_style,
            padding=padding,
        ))

    # ------------------------------------------------------------------ #
    # Raw console access
    # ------------------------------------------------------------------ #

    @property
    def raw(self) -> RichConsole:
        """Access the underlying Rich console for advanced usage."""
        return self._console


def _register_shift_enter():
    """Register Shift+Enter escape sequence with prompt_toolkit's VT100 parser.

    Modern terminals (Windows Terminal, iTerm2, etc.) send CSI u encoding
    for modified keys. Shift+Enter sends: ESC [ 13 ; 2 u (\\x1b[13;2u)

    This must be called before creating the PromptSession.
    """
    # CSI u encoding: ESC [ <keycode> ; <modifiers> u
    # Keycode 13 = Enter, Modifier 2 = Shift
    SHIFT_ENTER_SEQUENCE = "\x1b[13;2u"

    # Register the sequence if not already known
    if SHIFT_ENTER_SEQUENCE not in vt100_parser.ANSI_SEQUENCES:
        vt100_parser.ANSI_SEQUENCES[SHIFT_ENTER_SEQUENCE] = Keys.Vt100MouseEvent
        # Use a custom key name we can bind to
        Keys.ShiftEnter = "<shift-enter>"  # type: ignore[attr-defined]
        vt100_parser.ANSI_SEQUENCES[SHIFT_ENTER_SEQUENCE] = Keys.ShiftEnter  # type: ignore[attr-defined]


# Register Shift+Enter on module load
_register_shift_enter()


def _create_multiline_keybindings() -> KeyBindings:
    """Create key bindings for multi-line input.

    Methods to insert a newline without submitting:
    - Shift+Enter (in terminals that support CSI u encoding)
    - Backslash (\\) at end of line, then Enter

    Enter alone submits the input.
    """
    kb = KeyBindings()

    @kb.add("enter")
    def handle_enter(event):
        """Submit on Enter, unless line ends with backslash."""
        buffer = event.app.current_buffer
        text = buffer.text

        # If line ends with backslash, continue to next line
        if text.rstrip().endswith("\\"):
            # Remove the trailing backslash and add a newline
            stripped = text.rstrip()
            chars_to_delete = len(text) - len(stripped) + 1  # +1 for the backslash
            buffer.delete_before_cursor(count=chars_to_delete)
            buffer.insert_text("\n")
        else:
            # Submit the input
            buffer.validate_and_handle()

    # Shift+Enter - works in Windows Terminal, iTerm2, and other modern terminals.
    # Wrapped in try/except because <shift-enter> requires the custom key
    # registered by _register_shift_enter() and may not be recognised by all
    # prompt_toolkit versions.
    try:
        @kb.add("<shift-enter>")
        def insert_newline_shift_enter(event):
            """Insert a newline without submitting (Shift+Enter)."""
            event.app.current_buffer.insert_text("\n")
    except (ValueError, KeyError):
        pass  # Escape+Enter fallback below still provides multi-line support

    # Escape then Enter as fallback for terminals without Shift+Enter support
    @kb.add("escape", "enter")
    def insert_newline_escape(event):
        """Insert a newline without submitting (Escape+Enter fallback)."""
        event.app.current_buffer.insert_text("\n")

    return kb


class DiscoveryPrompt:
    """Interactive prompt for discovery conversations.

    Provides a Claude Code-inspired input experience with:
    - Full-width top and bottom borders that frame the prompt
    - Open items count and status display
    - Instruction text showing how to end the session
    - Multi-line input support (Shift+Enter or \\ to continue)
    - Slash commands (/open, /status)
    - System info below the bottom border
    - Expandable input area
    """

    INSTRUCTION = "Type 'done' when you have completed the discovery."
    QUIT_HINT = "Type 'quit' to cancel."

    def __init__(self, console: Console | None = None):
        self._console = console or Console()
        self._rc = self._console.raw
        self._session = PromptSession(
            key_bindings=_create_multiline_keybindings(),
            style=PT_STYLE,
            multiline=True,
            prompt_continuation=lambda width, line_num, wrap_count: "  ",  # Two spaces for continuation
        )

    def _get_terminal_width(self) -> int:
        """Get the terminal width for full-width borders."""
        return self._rc.size.width or 80

    def prompt(
        self,
        prompt_text: str = "> ",
        instruction: str | None = None,
        show_quit_hint: bool = True,
        open_count: int = 0,
        status_text: str = "",
    ) -> str:
        """Display a bordered prompt and get user input.

        Supports multi-line input:
        - Shift+Enter to insert a newline
        - Backslash (\\) at end of line continues to next line
        - Enter to submit

        Parameters
        ----------
        prompt_text:
            The prompt character(s) to display (default: "> ")
        instruction:
            Instruction text to show above the input (default: discovery instruction)
        show_quit_hint:
            Whether to show the quit hint below the input
        open_count:
            Number of open items to display in the status line
        status_text:
            Optional status summary text (e.g., "✓ 5 confirmed · ? 3 open").
            Token status should NOT be passed here — it is displayed above
            the border via ``print_token_status()``.

        Returns
        -------
        str:
            The user's input, stripped of leading/trailing whitespace
        """
        instruction = instruction if instruction is not None else self.INSTRUCTION
        width = self._get_terminal_width()

        # Top border (full width)
        self._rc.print(
            f"[prompt.border]{'─' * width}[/prompt.border]",
            highlight=False,
        )

        # Status line with open items count (in muted/gray)
        if open_count > 0:
            self._rc.print(
                f"[muted]Open items: {open_count} \u00b7 Type '/open' to list them.[/muted]",
                highlight=False,
            )

        if instruction:
            self._rc.print(
                f"[prompt.instruction]{instruction}[/prompt.instruction]",
                highlight=False,
            )
        # Blank line after instruction
        self._rc.print()

        # Build a multi-line bottom toolbar visible while typing (like Claude
        # Code's status bar).  Line 1 = border, line 2 = hint text.  Width is
        # computed inside the callable so it stays correct after terminal resize.
        if show_quit_hint:
            hint = self.QUIT_HINT

            def _toolbar():
                cols = shutil.get_terminal_size().columns
                return [
                    ("#555555", "─" * cols),
                    ("", "\n"),
                    ("#888888", hint),
                ]

            toolbar = _toolbar
        else:
            def _toolbar_border_only():
                cols = shutil.get_terminal_size().columns
                return [("#555555", "─" * cols)]

            toolbar = _toolbar_border_only

        # Get input using prompt_toolkit
        try:
            user_input = self._session.prompt(
                prompt_text,
                bottom_toolbar=toolbar,
            )
        except (EOFError, KeyboardInterrupt):
            self._rc.print()
            return ""

        # Bottom border (full width) after submission — the toolbar vanishes
        # when prompt returns, so re-print the border via Rich to separate
        # user input from the AI response that follows.
        self._rc.print(
            f"[prompt.border]{'─' * width}[/prompt.border]",
            highlight=False,
        )

        return user_input.strip()

    def simple_prompt(self, prompt_text: str = "> ") -> str:
        """Simple prompt without borders (for follow-up inputs).

        Still supports multi-line input.
        """
        try:
            return self._session.prompt(prompt_text).strip()
        except (EOFError, KeyboardInterrupt):
            return ""


# -------------------------------------------------------------------- #
# Module-level singleton
# -------------------------------------------------------------------- #

console = Console()
