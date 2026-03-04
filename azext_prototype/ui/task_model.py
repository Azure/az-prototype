"""Task data model for the TUI task tree.

Provides ``TaskItem`` and ``TaskStore`` for tracking stage/sub-task
progress displayed in the :class:`~.widgets.task_tree.TaskTree`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class TaskStatus(enum.Enum):
    """Lifecycle status for a task tree node."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


# Status → display symbol mapping
STATUS_SYMBOLS = {
    TaskStatus.PENDING: "\u25cb",      # ○
    TaskStatus.IN_PROGRESS: "\u25cf",  # ●
    TaskStatus.COMPLETED: "\u2713",    # ✓
    TaskStatus.FAILED: "\u2717",       # ✗
}


@dataclass
class TaskItem:
    """A single node in the task tree."""

    id: str
    label: str
    status: TaskStatus = TaskStatus.PENDING
    children: list[TaskItem] = field(default_factory=list)

    @property
    def symbol(self) -> str:
        return STATUS_SYMBOLS.get(self.status, "?")

    @property
    def display(self) -> str:
        return f"{self.symbol} {self.label}"


class TaskStore:
    """In-memory store for all task items, keyed by id.

    The store maintains four root tasks (one per stage) and allows
    dynamic addition/removal of sub-tasks.
    """

    def __init__(self) -> None:
        self._items: dict[str, TaskItem] = {}
        self._roots: list[str] = []
        self._init_roots()

    def _init_roots(self) -> None:
        """Create the four permanent stage root nodes."""
        for stage_id, label in [
            ("init", "Initialize"),
            ("design", "Design"),
            ("build", "Build"),
            ("deploy", "Deploy"),
        ]:
            item = TaskItem(id=stage_id, label=label, status=TaskStatus.PENDING)
            self._items[stage_id] = item
            self._roots.append(stage_id)

    @property
    def roots(self) -> list[TaskItem]:
        return [self._items[rid] for rid in self._roots]

    def get(self, task_id: str) -> TaskItem | None:
        return self._items.get(task_id)

    def update_status(self, task_id: str, status: TaskStatus) -> TaskItem | None:
        """Update a task's status. Returns the item if found."""
        item = self._items.get(task_id)
        if item:
            item.status = status
        return item

    def add_child(self, parent_id: str, child: TaskItem) -> bool:
        """Add a sub-task under a parent. Returns True on success."""
        parent = self._items.get(parent_id)
        if not parent:
            return False
        self._items[child.id] = child
        parent.children.append(child)
        return True

    def remove(self, task_id: str) -> bool:
        """Remove a task (and all its children) from the store."""
        item = self._items.pop(task_id, None)
        if not item:
            return False
        # Remove from any parent's children list
        for other in self._items.values():
            other.children = [c for c in other.children if c.id != task_id]
        # Recursively remove children
        for child in item.children:
            self._items.pop(child.id, None)
        return True

    def clear_children(self, parent_id: str) -> None:
        """Remove all children of a parent task."""
        parent = self._items.get(parent_id)
        if parent:
            for child in parent.children:
                self._items.pop(child.id, None)
            parent.children.clear()
