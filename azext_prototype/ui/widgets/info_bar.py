"""Info bar widget — assist label (left) + token status (right).

Sits at the very bottom of the TUI layout as a single-line status area.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static


class InfoBar(Horizontal):
    """Bottom info bar with assist and token status regions."""

    DEFAULT_CSS = """
    InfoBar {
        height: 1;
        dock: bottom;
        background: $surface;
    }

    InfoBar > #assist-label {
        width: 1fr;
        color: $text-muted;
    }

    InfoBar > #status-label {
        width: 1fr;
        text-align: right;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="assist-label")
        yield Static("", id="status-label")

    def update_assist(self, text: str) -> None:
        """Update the left-side assist/instruction text."""
        self.query_one("#assist-label", Static).update(text)

    def update_status(self, text: str) -> None:
        """Update the right-side token/status text."""
        self.query_one("#status-label", Static).update(text)
