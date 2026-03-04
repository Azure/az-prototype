"""Textual CSS variables and Rich theme dict for the TUI dashboard.

Ports the Claude Code-inspired color scheme from console.py into both
Textual CSS custom properties and a Rich Theme for use inside RichLog
widgets.
"""

from __future__ import annotations

from rich.theme import Theme

# -------------------------------------------------------------------- #
# Shared color constants — single source of truth
# -------------------------------------------------------------------- #

COLORS = {
    "dim": "#888888",
    "muted": "#666666",
    "content": "bright_white",
    "success": "bright_green",
    "error": "bright_red",
    "warning": "bright_yellow",
    "info": "bright_cyan",
    "accent": "bright_magenta",
    "border": "#555555",
    "bg_panel": "#1a1a2e",
    "bg_surface": "#16213e",
    "bg_input": "#0f3460",
}

# -------------------------------------------------------------------- #
# Rich theme — used by RichLog widgets to render Rich renderables
# -------------------------------------------------------------------- #

# -------------------------------------------------------------------- #
# prompt_toolkit style dict — used by console.py DiscoveryPrompt
# -------------------------------------------------------------------- #

PT_STYLE_DICT = {
    "prompt": COLORS["dim"],
    "": "#ffffff",
    "bottom-toolbar": f"noreverse {COLORS['dim']}",
}

# -------------------------------------------------------------------- #
# Rich theme — used by RichLog widgets to render Rich renderables
# -------------------------------------------------------------------- #

RICH_THEME = Theme(
    {
        "dim": COLORS["dim"],
        "muted": COLORS["muted"],
        "content": COLORS["content"],
        "success": COLORS["success"],
        "error": COLORS["error"],
        "warning": COLORS["warning"],
        "info": COLORS["info"],
        "accent": COLORS["accent"],
        "prompt.border": COLORS["border"],
        "prompt.instruction": COLORS["info"],
        "prompt.input": COLORS["content"],
        "progress.description": COLORS["content"],
        "progress.percentage": COLORS["info"],
        "progress.bar.complete": COLORS["success"],
        "progress.bar.finished": COLORS["success"],
        "agent": "bright_magenta bold",
        "stage": "bright_cyan bold",
        "path": "bright_cyan",
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
        "markdown.hr": COLORS["border"],
    }
)

# -------------------------------------------------------------------- #
# Task-status colors (for the Tree widget)
# -------------------------------------------------------------------- #

TASK_COLORS = {
    "pending": COLORS["dim"],
    "in_progress": "bright_white bold",
    "completed": COLORS["success"],
    "failed": COLORS["error"],
}

# -------------------------------------------------------------------- #
# Textual CSS stylesheet
# -------------------------------------------------------------------- #

APP_CSS = """\
/* ── Layout ────────────────────────────────────────────────── */

Screen {
    layout: grid;
    grid-size: 1;
    grid-rows: 1fr auto auto;
}

#body {
    layout: horizontal;
    height: 1fr;
}

#console-view {
    width: 3fr;
    border-right: solid $accent;
}

#task-tree {
    width: 1fr;
    min-width: 24;
    max-width: 40;
}

/* ── Prompt ────────────────────────────────────────────────── */

#prompt-input {
    height: auto;
    min-height: 3;
    max-height: 10;
    border-top: solid $accent;
    border-bottom: solid $accent;
    border-left: none;
    border-right: none;
}

/* ── Info bar ──────────────────────────────────────────────── */

#info-bar {
    layout: horizontal;
    height: 1;
    dock: bottom;
}

#assist-label {
    width: 1fr;
    content-align-vertical: middle;
}

#status-label {
    width: 1fr;
    text-align: right;
    content-align-vertical: middle;
}
"""
