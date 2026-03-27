"""Widget isolation tests for the Textual TUI dashboard.

Uses Textual's pilot test harness to mount individual widgets and
the full PrototypeApp in a headless terminal.
"""

from __future__ import annotations

import pytest

from azext_prototype.ui.app import PrototypeApp
from azext_prototype.ui.task_model import TaskItem, TaskStatus, TaskStore
from azext_prototype.ui.widgets.console_view import ConsoleView
from azext_prototype.ui.widgets.info_bar import InfoBar
from azext_prototype.ui.widgets.prompt_input import PromptInput
from azext_prototype.ui.widgets.task_tree import TaskTree

# -------------------------------------------------------------------- #
# TaskStore unit tests (no Textual needed)
# -------------------------------------------------------------------- #


class TestTaskStore:
    def test_roots_initialized(self):
        store = TaskStore()
        roots = store.roots
        assert len(roots) == 4
        assert [r.id for r in roots] == ["init", "design", "build", "deploy"]

    def test_update_status(self):
        store = TaskStore()
        item = store.update_status("init", TaskStatus.COMPLETED)
        assert item is not None
        assert item.status == TaskStatus.COMPLETED

    def test_update_nonexistent(self):
        store = TaskStore()
        assert store.update_status("nope", TaskStatus.COMPLETED) is None

    def test_add_child(self):
        store = TaskStore()
        child = TaskItem(id="design-req1", label="Gather requirements")
        assert store.add_child("design", child) is True
        assert len(store.get("design").children) == 1
        assert store.get("design-req1") is child

    def test_add_child_invalid_parent(self):
        store = TaskStore()
        child = TaskItem(id="orphan", label="Orphan")
        assert store.add_child("nonexistent", child) is False

    def test_remove(self):
        store = TaskStore()
        child = TaskItem(id="build-stage1", label="Stage 1")
        store.add_child("build", child)
        assert store.remove("build-stage1") is True
        assert store.get("build-stage1") is None
        assert len(store.get("build").children) == 0

    def test_clear_children(self):
        store = TaskStore()
        store.add_child("deploy", TaskItem(id="d1", label="Stage 1"))
        store.add_child("deploy", TaskItem(id="d2", label="Stage 2"))
        assert len(store.get("deploy").children) == 2
        store.clear_children("deploy")
        assert len(store.get("deploy").children) == 0
        assert store.get("d1") is None

    def test_display(self):
        item = TaskItem(id="t", label="Test", status=TaskStatus.COMPLETED)
        assert "\u2713" in item.display  # checkmark
        assert "Test" in item.display


# -------------------------------------------------------------------- #
# TaskItem unit tests
# -------------------------------------------------------------------- #


class TestTaskItem:
    def test_symbols(self):
        assert TaskItem(id="a", label="a", status=TaskStatus.PENDING).symbol == "\u25cb"
        assert TaskItem(id="b", label="b", status=TaskStatus.IN_PROGRESS).symbol == "\u25cf"
        assert TaskItem(id="c", label="c", status=TaskStatus.COMPLETED).symbol == "\u2713"
        assert TaskItem(id="d", label="d", status=TaskStatus.FAILED).symbol == "\u2717"


# -------------------------------------------------------------------- #
# Textual pilot tests
# -------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_app_mounts():
    """The app should mount all four panels without errors."""
    app = PrototypeApp()
    async with app.run_test() as pilot:  # noqa: F841
        # All four widget types should be queryable
        assert app.query_one("#console-view", ConsoleView)
        assert app.query_one("#task-tree", TaskTree)
        assert app.query_one("#prompt-input", PromptInput)
        assert app.query_one("#info-bar", InfoBar)


@pytest.mark.asyncio
async def test_console_view_write_text():
    """ConsoleView should accept text writes."""
    app = PrototypeApp()
    async with app.run_test() as pilot:  # noqa: F841
        cv = app.console_view
        cv.write_text("Hello, TUI!")
        cv.write_success("It worked")
        cv.write_error("Something failed")
        cv.write_warning("Watch out")
        cv.write_info("FYI")
        cv.write_header("Section")
        cv.write_dim("Quiet text")
        # No exception raised = success


@pytest.mark.asyncio
async def test_console_view_agent_response():
    """ConsoleView should render markdown agent responses."""
    app = PrototypeApp()
    async with app.run_test() as pilot:  # noqa: F841
        app.console_view.write_agent_response("# Hello\n\nThis is **bold**.")


@pytest.mark.asyncio
async def test_task_tree_roots():
    """TaskTree should show 4 root nodes on mount."""
    app = PrototypeApp()
    async with app.run_test() as pilot:  # noqa: F841
        tree = app.task_tree
        # Root should have 4 children (Init, Design, Build, Deploy)
        assert len(tree.root.children) == 4


@pytest.mark.asyncio
async def test_task_tree_update():
    """TaskTree should update status labels."""
    app = PrototypeApp()
    async with app.run_test() as pilot:  # noqa: F841
        tree = app.task_tree
        tree.update_task("init", TaskStatus.COMPLETED)
        item = tree.store.get("init")
        assert item.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_task_tree_add_child():
    """TaskTree should add and display sub-tasks."""
    app = PrototypeApp()
    async with app.run_test() as pilot:  # noqa: F841
        tree = app.task_tree
        child = TaskItem(id="design-discovery", label="Discovery conversation")
        tree.add_task("design", child)
        assert tree.store.get("design-discovery") is not None
        # Node should be in the map
        assert "design-discovery" in tree._node_map


@pytest.mark.asyncio
async def test_task_tree_add_section():
    """TaskTree.add_section() should create a node that accepts children.

    The section starts as a leaf (no expand arrow) and gains the arrow
    automatically when its first child is added.
    """
    app = PrototypeApp()
    async with app.run_test() as pilot:  # noqa: F841
        tree = app.task_tree
        section = TaskItem(id="design-section-arch", label="Architecture")
        tree.add_section("design", section)
        assert tree.store.get("design-section-arch") is not None
        assert "design-section-arch" in tree._node_map
        # Before children are added, the section should be a leaf (no arrow)
        node = tree._node_map["design-section-arch"]
        assert node.allow_expand is False

        # Adding a child should enable the expand arrow
        child = TaskItem(id="design-section-compute", label="Compute")
        tree.add_task("design-section-arch", child)
        assert tree.store.get("design-section-compute") is not None
        assert node.allow_expand is True


@pytest.mark.asyncio
async def test_info_bar_updates():
    """InfoBar should update assist and status text."""
    app = PrototypeApp()
    async with app.run_test() as pilot:  # noqa: F841
        app.info_bar.update_assist("Press Enter to continue")
        app.info_bar.update_status("1,200 tokens")
        # No exception = success


@pytest.mark.asyncio
async def test_prompt_input_disable():
    """PromptInput should be disabled by default."""
    app = PrototypeApp()
    async with app.run_test() as pilot:  # noqa: F841
        prompt = app.prompt_input
        assert prompt._enabled is False
        assert prompt.read_only is True


@pytest.mark.asyncio
async def test_prompt_input_enable():
    """PromptInput should allow enabling for input."""
    app = PrototypeApp()
    async with app.run_test() as pilot:  # noqa: F841
        prompt = app.prompt_input
        prompt.enable()
        assert prompt._enabled is True
        assert prompt.read_only is False


@pytest.mark.asyncio
async def test_file_list():
    """ConsoleView should render file lists."""
    app = PrototypeApp()
    async with app.run_test() as pilot:  # noqa: F841
        app.console_view.write_file_list(["main.tf", "variables.tf"], success=True)
        app.console_view.write_file_list(["broken.tf"], success=False)


# -------------------------------------------------------------------- #
# ConsoleView.write_markup tests
# -------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_console_view_write_markup():
    """write_markup should accept Rich markup without error."""
    app = PrototypeApp()
    async with app.run_test() as pilot:  # noqa: F841
        app.console_view.write_markup("[success]✓[/success] All good")
        app.console_view.write_markup("[info]→[/info] Starting session")
        # No exception = success


@pytest.mark.asyncio
async def test_console_view_write_markup_invalid_falls_back():
    """write_markup with invalid markup should fall back to plain text."""
    app = PrototypeApp()
    async with app.run_test() as pilot:  # noqa: F841
        # This has an unclosed tag — should not raise
        app.console_view.write_markup("[invalid_tag_that_wont_parse")


# -------------------------------------------------------------------- #
# PromptInput allow_empty tests
# -------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_prompt_input_allow_empty():
    """PromptInput with allow_empty=True should submit empty string."""
    app = PrototypeApp()
    async with app.run_test() as pilot:  # noqa: F841
        prompt = app.prompt_input
        prompt.enable(allow_empty=True)
        assert prompt._allow_empty is True
        assert prompt._enabled is True


@pytest.mark.asyncio
async def test_prompt_input_default_no_allow_empty():
    """PromptInput defaults to allow_empty=False."""
    app = PrototypeApp()
    async with app.run_test() as pilot:  # noqa: F841
        prompt = app.prompt_input
        prompt.enable()
        assert prompt._allow_empty is False


@pytest.mark.asyncio
async def test_prompt_input_input_mode():
    """In input mode (default), text has '> ' prefix and placeholder is empty."""
    app = PrototypeApp()
    async with app.run_test() as pilot:  # noqa: F841
        prompt = app.prompt_input
        prompt.enable()
        assert prompt._allow_empty is False
        assert prompt._enabled is True
        assert prompt.text == "> "
        assert prompt.placeholder == ""
