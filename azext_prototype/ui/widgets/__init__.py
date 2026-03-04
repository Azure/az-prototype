"""TUI dashboard widgets for the prototype extension."""

from azext_prototype.ui.widgets.console_view import ConsoleView
from azext_prototype.ui.widgets.info_bar import InfoBar
from azext_prototype.ui.widgets.prompt_input import PromptInput
from azext_prototype.ui.widgets.task_tree import TaskTree

__all__ = [
    "ConsoleView",
    "InfoBar",
    "PromptInput",
    "TaskTree",
]
