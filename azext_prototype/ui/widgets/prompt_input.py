"""Prompt input widget — growable TextArea with submit behavior.

Enter submits the input.  Shift+Enter or Ctrl+J inserts a newline.
The widget auto-grows vertically (up to a max) as the user types
multi-line text.  A ``"> "`` prefix is pre-filled when the prompt is
enabled.

Note: some terminals (e.g. Windows PowerShell) cannot distinguish
Shift+Enter from bare Enter.  Ctrl+J is provided as a universal
fallback that works everywhere.

Implementation note: TextArea processes Enter internally in ``_on_key``
to insert a newline *before* the BINDINGS system runs.  We must
intercept at the same level to override that behavior.
"""

from __future__ import annotations

from textual.message import Message
from textual.widgets import TextArea

_PROMPT_PREFIX = "> "


class PromptInput(TextArea):
    """Multi-line prompt that submits on Enter and grows upward."""

    DEFAULT_CSS = """
    PromptInput {
        height: auto;
        min-height: 3;
        max-height: 10;
        border-top: solid $accent;
        border-bottom: solid $accent;
        border-left: none;
        border-right: none;
    }
    """

    class Submitted(Message):
        """Posted when the user presses Enter to submit their input."""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(self, **kwargs) -> None:
        super().__init__(
            language=None,
            show_line_numbers=False,
            soft_wrap=True,
            **kwargs,
        )
        self._enabled = False
        self._allow_empty = False
        self.text = _PROMPT_PREFIX

    # ------------------------------------------------------------------ #
    # Enable / disable (blocks input while session is thinking)
    # ------------------------------------------------------------------ #

    def enable(self, placeholder: str = "Type your response...", allow_empty: bool = False) -> None:
        """Enable the prompt for user input.

        When *allow_empty* is True, pressing Enter with no text submits
        an empty string (used for "Enter to continue" pagination).
        In that mode the ``"> "`` prefix is hidden and a placeholder is
        shown instead, giving a clear visual distinction from input mode.
        """
        self._enabled = True
        self._allow_empty = allow_empty
        self.read_only = False
        if allow_empty:
            # Pagination mode — show placeholder, no "> " prefix
            self.text = ""
            self.placeholder = placeholder
        else:
            # Input mode — show "> " prefix
            self.text = _PROMPT_PREFIX
            self.placeholder = ""
            self.move_cursor_to_end_of_line()
        self.focus()

    def disable(self) -> None:
        """Disable the prompt (session is processing)."""
        self._enabled = False
        self.read_only = True

    def move_cursor_to_end_of_line(self) -> None:
        """Place the cursor after the '> ' prefix."""
        row = self.document.line_count - 1
        col = len(self.document.get_line(row))
        self.move_cursor((row, col))

    # ------------------------------------------------------------------ #
    # Key handling
    #
    # TextArea handles Enter in _on_key to insert a newline before the
    # BINDINGS system runs, so we must intercept at the same level.
    #
    #   enter       → submit
    #   shift+enter → newline (terminals with kitty keyboard protocol)
    #   ctrl+j      → newline (universal fallback)
    #   everything else → default TextArea behavior
    # ------------------------------------------------------------------ #

    def _on_key(self, event) -> None:
        # Always let Ctrl+C bubble up to the app's quit binding
        if event.key == "ctrl+c":
            return

        if not self._enabled:
            event.prevent_default()
            event.stop()
            return

        if event.key == "enter":
            # Bare Enter → submit the prompt
            event.prevent_default()
            event.stop()
            self._submit()
            return

        if event.key == "ctrl+j":
            # Ctrl+J → insert newline (universal fallback)
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return

        # shift+enter and all other keys → default TextArea behavior
        # (TextArea's _on_key inserts a newline for shift+enter)
        super()._on_key(event)

    # ------------------------------------------------------------------ #
    # Submit logic
    # ------------------------------------------------------------------ #

    def _submit(self) -> None:
        """Strip the prefix, post the Submitted message, and reset."""
        raw = self.text
        if raw.startswith(_PROMPT_PREFIX):
            raw = raw[len(_PROMPT_PREFIX):]
        value = raw.strip()
        if value or self._allow_empty:
            self.post_message(self.Submitted(value))
            if self._allow_empty:
                self.text = ""
            else:
                self.text = _PROMPT_PREFIX
                self.move_cursor_to_end_of_line()
