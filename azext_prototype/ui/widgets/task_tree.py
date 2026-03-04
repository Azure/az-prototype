"""Collapsible task tree widget showing stage and sub-task progress.

Four permanent root nodes — Initialize, Design, Build, Deploy — with
dynamic sub-tasks added/updated by sessions via the TUI adapter.
"""

from __future__ import annotations

from textual.widgets import Tree
from textual.widgets._tree import TreeNode

from azext_prototype.ui.task_model import TaskItem, TaskStatus, TaskStore
from azext_prototype.ui.theme import COLORS, TASK_COLORS


class TaskTree(Tree[str]):
    """Tree widget displaying stage progress with colored status symbols."""

    DEFAULT_CSS = """
    TaskTree {
        background: $surface;
        scrollbar-size: 1 1;
    }
    """

    def __init__(self, store: TaskStore | None = None, **kwargs) -> None:
        super().__init__("Stages", **kwargs)
        self._store = store or TaskStore()
        # Map task id → tree node for fast updates
        self._node_map: dict[str, TreeNode[str]] = {}

    @property
    def store(self) -> TaskStore:
        return self._store

    def on_mount(self) -> None:
        """Populate root nodes on mount."""
        self.root.expand()
        for root_item in self._store.roots:
            node = self.root.add(self._render_label(root_item), data=root_item.id)
            node.allow_expand = True
            node.expand()
            self._node_map[root_item.id] = node
            # Add any existing children
            for child in root_item.children:
                self._add_child_node(node, child)

    # ------------------------------------------------------------------ #
    # Public API (called from main thread via call_from_thread)
    # ------------------------------------------------------------------ #

    def update_task(self, task_id: str, status: TaskStatus) -> None:
        """Update a task's status and refresh its tree label."""
        item = self._store.update_status(task_id, status)
        node = self._node_map.get(task_id)
        if item and node:
            node.set_label(self._render_label(item))

    def add_task(self, parent_id: str, task: TaskItem) -> None:
        """Add a sub-task node under *parent_id*."""
        self._store.add_child(parent_id, task)
        parent_node = self._node_map.get(parent_id)
        if parent_node:
            self._add_child_node(parent_node, task)

    def add_section(self, parent_id: str, task: TaskItem) -> None:
        """Add an expandable section node that can have children.

        Unlike :meth:`add_task` (which creates a leaf node), this creates
        an expandable node so level-3 subsections can be nested under it.
        """
        self._store.add_child(parent_id, task)
        parent_node = self._node_map.get(parent_id)
        if parent_node:
            child_node = parent_node.add(self._render_label(task), data=task.id)
            child_node.allow_expand = True
            child_node.expand()
            self._node_map[task.id] = child_node

    def clear_children(self, parent_id: str) -> None:
        """Remove all sub-tasks under *parent_id*."""
        parent = self._store.get(parent_id)
        if parent:
            for child in list(parent.children):
                node = self._node_map.pop(child.id, None)
                if node:
                    node.remove()
        self._store.clear_children(parent_id)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _add_child_node(self, parent_node: TreeNode[str], item: TaskItem) -> None:
        child_node = parent_node.add_leaf(self._render_label(item), data=item.id)
        self._node_map[item.id] = child_node

    @staticmethod
    def _render_label(item: TaskItem) -> str:
        """Build a Rich-markup label string with color."""
        color = TASK_COLORS.get(item.status.value, COLORS["dim"])
        return f"[{color}]{item.symbol}[/{color}] {item.label}"
