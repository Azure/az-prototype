"""Shared session utilities — DRY mixin for all session classes.

Provides common setup helpers and context-manager utilities used
by BuildSession, DeploySession, DiscoverySession, and BacklogSession.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any


class SessionMixin:
    """Mixin providing shared session infrastructure.

    Subclasses must set ``self._console``, ``self._status_fn``, and
    ``self._token_tracker`` before calling mixin methods.
    """

    _console: Any
    _status_fn: Any
    _token_tracker: Any

    # ------------------------------------------------------------------ #
    # Setup helpers
    # ------------------------------------------------------------------ #

    def _setup_token_tracker(self, *, status_fn: Any = None) -> None:
        """Initialize token tracker with appropriate callback."""
        from azext_prototype.ai.token_tracker import TokenTracker

        self._token_tracker = TokenTracker()
        if status_fn:
            self._token_tracker._on_update = lambda text: status_fn(text, "tokens")
        elif self._console:
            self._token_tracker._on_update = self._console.print_token_status

    def _setup_escalation_tracker(self, project_dir: str) -> None:
        """Initialize escalation tracker, loading existing state if present."""
        from azext_prototype.stages.escalation import EscalationTracker

        self._escalation_tracker = EscalationTracker(project_dir)
        if self._escalation_tracker.exists:
            self._escalation_tracker.load()

    # ------------------------------------------------------------------ #
    # Context managers
    # ------------------------------------------------------------------ #

    @contextmanager
    def _maybe_spinner(
        self,
        message: str,
        use_styled: bool,
        *,
        status_fn: Callable | None = None,
    ) -> Iterator[None]:
        """Show a spinner/status when using styled output or TUI."""
        _sfn = status_fn or getattr(self, "_status_fn", None)
        if use_styled:
            with self._console.spinner(message):
                yield
        elif _sfn:
            _sfn(message, "start")
            try:
                yield
            finally:
                _sfn(message, "end")
                token_text = self._token_tracker.format_status()
                if token_text:
                    _sfn(token_text, "tokens")
        else:
            yield

    # ------------------------------------------------------------------ #
    # Retry helpers
    # ------------------------------------------------------------------ #

    _TIMEOUT_BACKOFFS = [15, 30, 60, 120]

    def _countdown(
        self,
        seconds: int,
        attempt_num: int,
        max_attempts: int,
        label: str,
        _print: Callable,
    ) -> None:
        """Display a countdown timer before retrying."""
        _sfn = getattr(self, "_status_fn", None)
        for remaining in range(seconds, 0, -1):
            if _sfn:
                _sfn(
                    f"API timed out. Retrying in {remaining}s... (attempt {attempt_num}/{max_attempts})",
                    "update",
                )
            elif remaining == seconds:
                _print(f"       API timed out. Retrying in {remaining}s... " f"(attempt {attempt_num}/{max_attempts})")
            time.sleep(1)

        if _sfn:
            _sfn(f"Retrying {label}...", "update")
