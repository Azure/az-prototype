"""Textual TUI application — main dashboard for interactive sessions.

Layout::

    ┌─────────────────────────────┬──────────────────┐
    │     Console (RichLog)       │  Tasks (Tree)    │
    │  scrollable, new at bottom  │  collapsible     │
    ├─────────────────────────────┴──────────────────┤
    │  Prompt (TextArea)                             │
    ├───────────────────────────┬────────────────────┤
    │  Assist (left 50%)        │ Status (right 50%) │
    └───────────────────────────┴────────────────────┘

Sessions run on ``@work(thread=True)`` workers.  ``call_from_thread``
is used to schedule widget updates on the main event loop.
"""

from __future__ import annotations

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal

from azext_prototype.ui.task_model import TaskStore
from azext_prototype.ui.theme import APP_CSS
from azext_prototype.ui.tui_adapter import TUIAdapter
from azext_prototype.ui.widgets.console_view import ConsoleView
from azext_prototype.ui.widgets.info_bar import InfoBar
from azext_prototype.ui.widgets.prompt_input import PromptInput
from azext_prototype.ui.widgets.task_tree import TaskTree


class PrototypeApp(App):
    """Main TUI application for ``az prototype`` interactive sessions."""

    CSS = APP_CSS

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
    ]

    def __init__(
        self,
        store: TaskStore | None = None,
        start_stage: str | None = None,
        project_dir: str | None = None,
        stage_kwargs: dict | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._store = store or TaskStore()
        self._start_stage = start_stage
        self._project_dir = project_dir
        self._stage_kwargs = stage_kwargs or {}
        self.adapter = TUIAdapter(self)

    # ------------------------------------------------------------------ #
    # Compose
    # ------------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield ConsoleView(id="console-view")
            yield TaskTree(store=self._store, id="task-tree")
        yield PromptInput(id="prompt-input")
        yield InfoBar(id="info-bar")

    # ------------------------------------------------------------------ #
    # Widget accessors
    # ------------------------------------------------------------------ #

    @property
    def console_view(self) -> ConsoleView:
        return self.query_one("#console-view", ConsoleView)

    @property
    def task_tree(self) -> TaskTree:
        return self.query_one("#task-tree", TaskTree)

    @property
    def prompt_input(self) -> PromptInput:
        return self.query_one("#prompt-input", PromptInput)

    @property
    def info_bar(self) -> InfoBar:
        return self.query_one("#info-bar", InfoBar)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def on_mount(self) -> None:
        """Set up the initial state after widgets are mounted."""
        self.title = "az prototype"
        self.info_bar.update_assist("Enter = submit | Ctrl+J = newline | Ctrl+C = quit")
        self.prompt_input.disable()

        # Write a welcome banner
        self.console_view.write_info("Welcome to az prototype")
        self.console_view.write_dim("")

        # Auto-start the orchestrator if project_dir is set
        if self._project_dir:
            self.start_orchestrator()

    def start_orchestrator(self) -> None:
        """Launch the stage orchestrator on a worker thread."""
        from azext_prototype.ui.stage_orchestrator import StageOrchestrator

        def _run() -> None:
            orchestrator = StageOrchestrator(
                self, self.adapter, self._project_dir,
                stage_kwargs=self._stage_kwargs,
            )
            orchestrator.run(start_stage=self._start_stage)

        self.run_worker(_run, thread=True)

    # ------------------------------------------------------------------ #
    # Shutdown
    # ------------------------------------------------------------------ #

    def on_unmount(self) -> None:
        """Signal the adapter so worker threads unblock and exit."""
        self.adapter.shutdown()

    # ------------------------------------------------------------------ #
    # Event handlers
    # ------------------------------------------------------------------ #

    @on(PromptInput.Submitted)
    def _on_prompt_submitted(self, event: PromptInput.Submitted) -> None:
        """Route prompt submissions to the adapter."""
        self.adapter.on_prompt_submitted(event.value)
