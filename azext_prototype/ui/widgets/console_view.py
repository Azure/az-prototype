"""Scrollable console output widget wrapping Textual's RichLog.

Renders Rich renderables (Markdown, Panel, Table, Text) directly
and provides semantic convenience methods mirroring ``Console`` from
``console.py``.
"""

from __future__ import annotations

import re

from rich.markdown import Markdown
from rich.text import Text
from textual.widgets import RichLog

from azext_prototype.ui.theme import RICH_THEME

# Ordered list fix ported from console.py
_ORDERED_LIST_RE = re.compile(r"^(\s*)(\d+)\.\s", re.MULTILINE)


def _preprocess_markdown(content: str) -> str:
    return _ORDERED_LIST_RE.sub(r"**\2.** ", content)


class ConsoleView(RichLog):
    """Scrollable console panel for agent output, status messages, etc."""

    DEFAULT_CSS = """
    ConsoleView {
        background: $surface;
        scrollbar-size: 1 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(
            highlight=False,
            markup=True,
            auto_scroll=True,
            wrap=True,
            **kwargs,
        )

    # ------------------------------------------------------------------ #
    # Semantic write methods (mirror console.py Console)
    # ------------------------------------------------------------------ #

    def write_text(self, message: str, style: str = "") -> None:
        """Write a plain styled line."""
        self.write(Text(message, style=style))

    def write_markup(self, message: str) -> None:
        """Write a message with Rich markup tags preserved."""
        try:
            styled = Text.from_markup(message)
            self.write(styled)
        except Exception:
            self.write(Text(message))

    def write_dim(self, message: str) -> None:
        self.write(Text(message, style=RICH_THEME.styles.get("dim", "")))

    def write_success(self, message: str) -> None:
        text = Text()
        text.append("\u2713 ", style=str(RICH_THEME.styles.get("success", "")))
        text.append(message)
        self.write(text)

    def write_error(self, message: str) -> None:
        text = Text()
        text.append("\u2717 ", style=str(RICH_THEME.styles.get("error", "")))
        text.append(message)
        self.write(text)

    def write_warning(self, message: str) -> None:
        text = Text()
        text.append("! ", style=str(RICH_THEME.styles.get("warning", "")))
        text.append(message)
        self.write(text)

    def write_info(self, message: str) -> None:
        text = Text()
        text.append("\u2192 ", style=str(RICH_THEME.styles.get("info", "")))
        text.append(message)
        self.write(text)

    def write_header(self, title: str) -> None:
        from rich.style import Style

        self.write(Text())
        base = RICH_THEME.styles.get("accent")
        style = (base + Style(bold=True)) if base else Style(bold=True)
        self.write(Text(title, style=style))
        self.write(Text())

    def write_agent_response(self, content: str) -> None:
        """Render a markdown agent response."""
        self.write(Text())
        self.write(Markdown(_preprocess_markdown(content)))
        self.write(Text())

    def write_token_status(self, status_text: str) -> None:
        if status_text:
            self.write(Text(status_text, style=str(RICH_THEME.styles.get("muted", "")), justify="right"))

    def write_file_list(self, files: list[str], success: bool = True) -> None:
        style_name = "success" if success else "error"
        marker = "\u2713" if success else "\u2717"
        style = str(RICH_THEME.styles.get(style_name, ""))
        for f in files:
            text = Text()
            text.append(f"    {marker} ", style=style)
            text.append(f, style=str(RICH_THEME.styles.get("path", "")))
            self.write(text)
