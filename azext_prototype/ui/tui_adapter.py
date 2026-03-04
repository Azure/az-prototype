"""Bridge between synchronous sessions and the async Textual TUI.

Sessions (Discovery, Build, Deploy, Backlog) run on Textual worker
threads and call ``input_fn`` / ``print_fn`` synchronously.  This
adapter translates those calls into Textual widget operations using
``call_from_thread`` (thread-safe scheduling on the main event loop)
and ``threading.Event`` (blocking the worker until the user submits).

On shutdown the adapter's :meth:`shutdown` method is called by the app.
This sets the ``_shutdown`` event so that any worker thread blocked in
``input_fn`` unblocks immediately and raises :class:`ShutdownRequested`.

Usage inside ``PrototypeApp``::

    adapter = TUIAdapter(app)
    # Then pass to a session:
    session.run(
        input_fn=adapter.input_fn,
        print_fn=adapter.print_fn,
    )
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import TYPE_CHECKING

from azext_prototype.ui.task_model import TaskItem, TaskStatus
from azext_prototype.ui.theme import COLORS

if TYPE_CHECKING:
    from azext_prototype.ui.app import PrototypeApp

logger = logging.getLogger(__name__)

# Rich markup tag pattern (e.g. [success], [/error], [info]...[/info])
_RICH_TAG_RE = re.compile(r"\[/?[a-zA-Z_.]+(?:\s[^\]]+)?\]")


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as ``12s`` or ``1m04s`` when >= 60."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{minutes}m{secs:02d}s"


def _strip_rich_markup(text: str) -> str:
    """Remove Rich-style markup tags for plain-text rendering."""
    return _RICH_TAG_RE.sub("", text)


class ShutdownRequested(Exception):
    """Raised on a worker thread when the app is shutting down."""


class TUIAdapter:
    """Thread-safe bridge from sync session I/O to Textual widgets.

    Constructed with a reference to the running :class:`PrototypeApp`.
    The three main callables — ``input_fn``, ``print_fn``, ``status_fn``
    — are suitable for passing directly to session ``run()`` methods.
    """

    def __init__(self, app: PrototypeApp) -> None:
        self._app = app
        # Synchronization for blocking input
        self._input_event = threading.Event()
        self._input_value: str = ""
        # Shutdown signal — unblocks any waiting worker thread
        self._shutdown = threading.Event()
        # Elapsed timer state (managed on the main thread)
        self._timer_start: float | None = None
        self._timer_handle = None  # Textual Timer reference
        # Track last level-2 section for nesting level-3 subsections
        self._last_l2_section_id: str = "design"

    # ------------------------------------------------------------------ #
    # Shutdown
    # ------------------------------------------------------------------ #

    def shutdown(self) -> None:
        """Signal all waiting worker threads to exit immediately."""
        self._shutdown.set()
        self._cancel_timer()
        # Also set the input event so any blocked wait() returns
        self._input_event.set()

    @property
    def is_shutdown(self) -> bool:
        """True if the adapter has been told to shut down."""
        return self._shutdown.is_set()

    # ------------------------------------------------------------------ #
    # Screen refresh helper
    # ------------------------------------------------------------------ #

    def _request_screen_update(self) -> None:
        """Force Textual to repaint the screen.

        Must be called on the main thread (inside a ``call_from_thread``
        callback).  ``call_from_thread`` runs callbacks as asyncio tasks
        *outside* Textual's message loop, so widget ``refresh()`` calls
        may not trigger a compositor pass.  Calling ``screen.refresh()``
        explicitly schedules one.
        """
        try:
            self._app.screen.refresh()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # print_fn — called from worker thread
    # ------------------------------------------------------------------ #

    def print_fn(self, message: str = "", **kwargs) -> None:
        """Write *message* to the ConsoleView widget.

        Called from a worker thread; delegates to the main thread via
        ``call_from_thread``.  If the message contains Rich markup tags
        (e.g. ``[success]✓[/success]``), they are preserved so the
        console renders colored output.
        """
        if self._shutdown.is_set():
            return

        msg = str(message)

        def _write() -> None:
            if _RICH_TAG_RE.search(msg):
                self._app.console_view.write_markup(msg)
            else:
                self._app.console_view.write_text(msg)
            self._request_screen_update()

        try:
            self._app.call_from_thread(_write)
        except Exception:
            pass  # App already torn down

    # ------------------------------------------------------------------ #
    # response_fn — render agent responses with color + pagination
    # ------------------------------------------------------------------ #

    def response_fn(self, content: str) -> None:
        """Render an agent response as colored Markdown — full content, no pagination."""
        if self._shutdown.is_set():
            return
        try:
            def _render():
                self._app.console_view.write_agent_response(content)
                self._request_screen_update()
            self._app.call_from_thread(_render)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # input_fn — called from worker thread, blocks until user submits
    # ------------------------------------------------------------------ #

    def input_fn(self, prompt_text: str = "> ") -> str:
        """Block the worker thread until the user submits input.

        1. Schedules prompt activation on the main thread.
        2. Waits on ``_input_event`` (checks for shutdown every 0.25 s).
        3. Returns the submitted text.

        Raises :class:`ShutdownRequested` if the app is shutting down.
        """
        if self._shutdown.is_set():
            raise ShutdownRequested()

        self._input_event.clear()

        def _enable_prompt() -> None:
            self._app.prompt_input.enable(placeholder=prompt_text)
            self._request_screen_update()

        try:
            self._app.call_from_thread(_enable_prompt)
        except Exception:
            raise ShutdownRequested()

        # Block worker thread — poll with a short timeout so we can
        # detect shutdown without waiting for user input.
        while not self._input_event.wait(timeout=0.25):
            if self._shutdown.is_set():
                raise ShutdownRequested()

        if self._shutdown.is_set():
            raise ShutdownRequested()

        return self._input_value

    def on_prompt_submitted(self, value: str) -> None:
        """Called on the main thread when PromptInput.Submitted fires.

        Stores the value, disables the prompt, echoes the input to the
        console (unless empty — e.g. pagination "Enter to continue"),
        and unblocks the worker.
        """
        self._input_value = value
        self._app.prompt_input.disable()
        # Echo user input to console (skip for empty pagination presses)
        if value:
            self._app.console_view.write_text(f"> {value}", style=COLORS["content"])
        # Unblock the waiting worker thread
        self._input_event.set()

    # ------------------------------------------------------------------ #
    # status_fn — called from worker thread for spinner replacement
    # ------------------------------------------------------------------ #

    def status_fn(self, message: str, event: str = "start") -> None:
        """Update the info bar as spinner replacement with elapsed timer.

        Called by ``_maybe_spinner(..., status_fn=adapter.status_fn)``.

        Events:
            ``"start"`` — show assist text and start an elapsed timer on
            the right side of the info bar.
            ``"end"`` — stop the timer, show final elapsed time (will be
            replaced by token usage shortly after).
            ``"tokens"`` — replace the timer/elapsed text with token usage.
        """
        if self._shutdown.is_set():
            return

        if event == "start":
            def _start() -> None:
                self._cancel_timer()
                self._timer_start = time.monotonic()
                self._app.info_bar.update_assist(f"\u23f3 {message}")
                self._app.info_bar.update_status("\u23f1 0s")
                self._timer_handle = self._app.set_interval(
                    1.0, self._tick_timer,
                )
                self._request_screen_update()

            try:
                self._app.call_from_thread(_start)
            except Exception:
                pass

        elif event == "end":
            def _stop() -> None:
                self._cancel_timer()
                if self._timer_start is not None:
                    elapsed = time.monotonic() - self._timer_start
                    self._app.info_bar.update_status(f"\u23f1 {_format_elapsed(elapsed)}")
                self._timer_start = None
                self._app.info_bar.update_assist(
                    "Enter = submit | Ctrl+J = newline | Ctrl+C = quit"
                )
                self._request_screen_update()

            try:
                self._app.call_from_thread(_stop)
            except Exception:
                pass

        elif event == "tokens":
            def _tokens() -> None:
                if message:
                    self._app.info_bar.update_status(message)
                    self._request_screen_update()

            try:
                self._app.call_from_thread(_tokens)
            except Exception:
                pass

    def _tick_timer(self) -> None:
        """Update the elapsed timer display (called on the main thread)."""
        if self._timer_start is None or self._timer_handle is None:
            return
        elapsed = time.monotonic() - self._timer_start
        self._app.info_bar.update_status(f"\u23f1 {_format_elapsed(elapsed)}")
        self._request_screen_update()

    def _cancel_timer(self) -> None:
        """Stop the interval timer if running."""
        if self._timer_handle is not None:
            self._timer_handle.stop()
            self._timer_handle = None

    # ------------------------------------------------------------------ #
    # Token status — called from worker thread
    # ------------------------------------------------------------------ #

    def print_token_status(self, status_text: str) -> None:
        """Update the right side of the info bar with token usage."""
        if self._shutdown.is_set():
            return

        def _update() -> None:
            self._app.info_bar.update_status(status_text)
            self._request_screen_update()

        try:
            self._app.call_from_thread(_update)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Task tree — called from worker thread
    # ------------------------------------------------------------------ #

    def _refresh_tree(self) -> None:
        """Force a tree re-render and screen repaint (must be on main thread)."""
        self._app.task_tree.refresh()
        self._request_screen_update()

    def update_task(self, task_id: str, status: TaskStatus) -> None:
        """Update a task's status in the tree widget."""
        if self._shutdown.is_set():
            return

        def _update() -> None:
            self._app.task_tree.update_task(task_id, status)
            self._refresh_tree()

        try:
            self._app.call_from_thread(_update)
        except Exception:
            pass

    def add_task(self, parent_id: str, task_id: str, label: str) -> None:
        """Add a sub-task to the tree widget."""
        if self._shutdown.is_set():
            return

        item = TaskItem(id=task_id, label=label)

        def _add() -> None:
            self._app.task_tree.add_task(parent_id, item)
            self._refresh_tree()

        try:
            self._app.call_from_thread(_add)
        except Exception:
            pass

    def clear_tasks(self, parent_id: str) -> None:
        """Remove all sub-tasks under a parent stage."""
        if self._shutdown.is_set():
            return

        def _clear() -> None:
            self._app.task_tree.clear_children(parent_id)
            self._refresh_tree()

        try:
            self._app.call_from_thread(_clear)
        except Exception:
            pass

    def section_fn(self, headers: list[tuple[str, int]]) -> None:
        """Add section headers as sub-tasks under 'design' with hierarchy.

        Level-2 headings become expandable sections directly under 'design'.
        Level-3 headings nest under the most recent level-2 section.
        """
        if self._shutdown.is_set():
            return

        def _add() -> None:
            changed = False
            for header_text, level in headers:
                slug = re.sub(r"[^a-z0-9]+", "-", header_text.lower()).strip("-")
                task_id = f"design-section-{slug}"
                if self._app.task_tree.store.get(task_id) is not None:
                    if level == 2:
                        self._last_l2_section_id = task_id
                    continue  # dedup
                item = TaskItem(id=task_id, label=header_text)
                if level == 2:
                    self._app.task_tree.add_section("design", item)
                    self._last_l2_section_id = task_id
                else:  # level 3 — nest under most recent level-2
                    parent = self._last_l2_section_id
                    self._app.task_tree.add_task(parent, item)
                changed = True
            if changed:
                self._refresh_tree()

        try:
            self._app.call_from_thread(_add)
        except Exception:
            logger.debug("section_fn call_from_thread failed", exc_info=True)
