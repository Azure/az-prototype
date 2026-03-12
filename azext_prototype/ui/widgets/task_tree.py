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
        """Populate root nodes on mount.

        Root stage nodes start collapsed with no expand arrow.  The arrow
        appears automatically when the first child is added, and the node
        expands only when its status becomes ``IN_PROGRESS``.
        """
        self.root.expand()
        for root_item in self._store.roots:
            has_children = len(root_item.children) > 0
            node = self.root.add(self._render_label(root_item), data=root_item.id)
            node.allow_expand = has_children
            if has_children and root_item.status == TaskStatus.IN_PROGRESS:
                node.expand()
            self._node_map[root_item.id] = node
            # Add any existing children
            for child in root_item.children:
                self._add_child_node(node, child)

    # ------------------------------------------------------------------ #
    # Public API (called from main thread via call_from_thread)
    # ------------------------------------------------------------------ #

    def update_task(self, task_id: str, status: TaskStatus) -> None:
        """Update a task's status and refresh its tree label.

        Auto-expand when ``IN_PROGRESS`` (and ensure the parent is
        visible).  Auto-collapse when ``COMPLETED`` or ``FAILED``.
        """
        item = self._store.update_status(task_id, status)
        node = self._node_map.get(task_id)
        if item and node:
            node.set_label(self._render_label(item))
            if status == TaskStatus.IN_PROGRESS:
                if node.allow_expand:
                    node.expand()
                # Ensure parent is expanded so this node is visible
                if node.parent is not None and node.parent is not self.root:
                    node.parent.expand()
            elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                node.collapse()

    def add_task(self, parent_id: str, task: TaskItem) -> None:
        """Add a sub-task node under *parent_id*."""
        self._store.add_child(parent_id, task)
        parent_node = self._node_map.get(parent_id)
        if parent_node:
            self._add_child_node(parent_node, task)

    def add_section(self, parent_id: str, task: TaskItem) -> None:
        """Add a section node that can later accept children.

        Unlike :meth:`add_task` (which creates a permanent leaf), this
        node is registered so it can serve as a parent for level-3
        subsections.  It starts as a leaf (no expand arrow) and gains
        the arrow automatically when its first child is added.
        """
        self._store.add_child(parent_id, task)
        parent_node = self._node_map.get(parent_id)
        if parent_node:
            self._add_child_node(parent_node, task)

    def clear_children(self, parent_id: str) -> None:
        """Remove all sub-tasks under *parent_id*."""
        parent = self._store.get(parent_id)
        if parent:
            for child in list(parent.children):
                node = self._node_map.pop(child.id, None)
                if node:
                    node.remove()
        self._store.clear_children(parent_id)
        # Hide the expand arrow once all children are gone
        parent_node = self._node_map.get(parent_id)
        if parent_node:
            parent_node.allow_expand = False

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _add_child_node(self, parent_node: TreeNode[str], item: TaskItem) -> None:
        child_node = parent_node.add_leaf(self._render_label(item), data=item.id)
        self._node_map[item.id] = child_node
        # Enable expand arrow on parent when first child is added
        if not parent_node.allow_expand:
            parent_node.allow_expand = True
        # Auto-expand parent if it is currently in progress
        parent_item = self._store.get(parent_node.data) if parent_node.data else None
        if parent_item and parent_item.status == TaskStatus.IN_PROGRESS:
            parent_node.expand()

    @staticmethod
    def _render_label(item: TaskItem) -> str:
        """Build a Rich-markup label string with color."""
        color = TASK_COLORS.get(item.status.value, COLORS["dim"])
        return f"[{color}]{item.symbol}[/{color}] {item.label}"
