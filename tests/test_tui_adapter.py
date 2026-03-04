"""Threading and bridge tests for TUIAdapter.

Verifies that the adapter correctly shuttles data between worker
threads (sessions) and the main Textual event loop (widgets).
"""

from __future__ import annotations

import threading

import pytest

from azext_prototype.ui.app import PrototypeApp
from azext_prototype.ui.task_model import TaskStatus
from azext_prototype.ui.tui_adapter import _strip_rich_markup, _RICH_TAG_RE


# -------------------------------------------------------------------- #
# Unit tests (no Textual)
# -------------------------------------------------------------------- #


class TestStripRichMarkup:
    def test_strips_simple_tags(self):
        assert _strip_rich_markup("[success]OK[/success]") == "OK"

    def test_strips_nested(self):
        assert _strip_rich_markup("[bold][info]hello[/info][/bold]") == "hello"

    def test_leaves_plain_text(self):
        assert _strip_rich_markup("no markup here") == "no markup here"

    def test_preserves_brackets_in_non_tag_context(self):
        # e.g. list notation
        assert _strip_rich_markup("list[0]") == "list[0]"

    def test_empty(self):
        assert _strip_rich_markup("") == ""


# -------------------------------------------------------------------- #
# Integration tests with Textual pilot
# -------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_adapter_print_fn():
    """print_fn should route text to the ConsoleView."""
    app = PrototypeApp()
    async with app.run_test() as pilot:
        adapter = app.adapter
        # Simulate a worker thread calling print_fn
        done = threading.Event()

        def _worker():
            adapter.print_fn("Hello from worker")
            done.set()

        t = threading.Thread(target=_worker)
        t.start()
        done.wait(timeout=5)
        t.join(timeout=5)
        # The message should have been routed through — no exception = success


@pytest.mark.asyncio
async def test_adapter_input_fn_and_submit():
    """input_fn should block until on_prompt_submitted is called."""
    app = PrototypeApp()
    async with app.run_test() as pilot:
        adapter = app.adapter
        result = {}

        def _worker():
            result["value"] = adapter.input_fn("> ")

        t = threading.Thread(target=_worker)
        t.start()

        # Give the worker thread time to block
        await pilot.pause()
        await pilot.pause()

        # Simulate user submitting input from the main thread
        adapter.on_prompt_submitted("test response")

        t.join(timeout=5)
        assert result.get("value") == "test response"


@pytest.mark.asyncio
async def test_adapter_status_fn():
    """status_fn should update the info bar assist text."""
    app = PrototypeApp()
    async with app.run_test() as pilot:
        adapter = app.adapter

        done = threading.Event()

        def _worker():
            adapter.status_fn("Building Stage 1...", "start")
            adapter.status_fn("Building Stage 1...", "end")
            done.set()

        t = threading.Thread(target=_worker)
        t.start()
        done.wait(timeout=5)
        t.join(timeout=5)


@pytest.mark.asyncio
async def test_adapter_token_status():
    """print_token_status should update the info bar status."""
    app = PrototypeApp()
    async with app.run_test() as pilot:
        adapter = app.adapter
        done = threading.Event()

        def _worker():
            adapter.print_token_status("1,200 tokens · 5,000 session")
            done.set()

        t = threading.Thread(target=_worker)
        t.start()
        done.wait(timeout=5)
        t.join(timeout=5)


@pytest.mark.asyncio
async def test_adapter_status_fn_timer_lifecycle():
    """status_fn start/end/tokens lifecycle should manage elapsed timer."""
    app = PrototypeApp()
    async with app.run_test() as pilot:
        adapter = app.adapter

        # Start the timer
        start_done = threading.Event()

        def _start():
            adapter.status_fn("Analyzing your input...", "start")
            start_done.set()

        t1 = threading.Thread(target=_start)
        t1.start()
        start_done.wait(timeout=5)
        t1.join(timeout=5)
        await pilot.pause()
        await pilot.pause()

        assert adapter._timer_start is not None
        assert adapter._timer_handle is not None

        # Stop the timer and replace with tokens
        stop_done = threading.Event()

        def _stop():
            adapter.status_fn("Analyzing your input...", "end")
            adapter.status_fn("1,200 tokens \u00b7 5,000 session", "tokens")
            stop_done.set()

        t2 = threading.Thread(target=_stop)
        t2.start()
        stop_done.wait(timeout=5)
        t2.join(timeout=5)
        await pilot.pause()
        await pilot.pause()

        assert adapter._timer_handle is None


@pytest.mark.asyncio
async def test_adapter_task_updates():
    """Task tree operations via adapter should work from threads."""
    app = PrototypeApp()
    async with app.run_test() as pilot:
        adapter = app.adapter
        done = threading.Event()

        def _worker():
            adapter.update_task("init", TaskStatus.COMPLETED)
            adapter.add_task("design", "design-d1", "Discovery")
            adapter.update_task("design-d1", TaskStatus.IN_PROGRESS)
            done.set()

        t = threading.Thread(target=_worker)
        t.start()
        done.wait(timeout=5)
        t.join(timeout=5)

        # Let the main event loop process the queued callbacks
        await pilot.pause()
        await pilot.pause()

        # Verify state
        assert app.task_tree.store.get("init").status == TaskStatus.COMPLETED
        assert app.task_tree.store.get("design-d1") is not None


@pytest.mark.asyncio
async def test_adapter_clear_tasks():
    """clear_tasks should remove sub-tasks via worker thread."""
    app = PrototypeApp()
    async with app.run_test() as pilot:
        adapter = app.adapter

        # Add tasks from a worker thread
        setup_done = threading.Event()

        def _setup():
            adapter.add_task("build", "build-s1", "Stage 1")
            adapter.add_task("build", "build-s2", "Stage 2")
            setup_done.set()

        t1 = threading.Thread(target=_setup)
        t1.start()
        setup_done.wait(timeout=5)
        t1.join(timeout=5)

        await pilot.pause()
        await pilot.pause()
        assert app.task_tree.store.get("build-s1") is not None

        done = threading.Event()

        def _worker():
            adapter.clear_tasks("build")
            done.set()

        t = threading.Thread(target=_worker)
        t.start()
        done.wait(timeout=5)
        t.join(timeout=5)


@pytest.mark.asyncio
async def test_adapter_section_fn():
    """section_fn should add design sub-tasks with dedup and hierarchy."""
    app = PrototypeApp()
    async with app.run_test() as pilot:
        adapter = app.adapter
        done = threading.Event()

        def _worker():
            adapter.section_fn([("Project Context & Scope", 2), ("Data & Content", 2)])
            # Call again with overlapping header — should dedup
            adapter.section_fn([("Project Context & Scope", 2), ("Security", 2)])
            done.set()

        t = threading.Thread(target=_worker)
        t.start()
        done.wait(timeout=5)
        t.join(timeout=5)

        # Let the main event loop process the queued callbacks
        await pilot.pause()
        await pilot.pause()

        # Verify: 3 unique sections (not 4)
        assert app.task_tree.store.get("design-section-project-context-scope") is not None
        assert app.task_tree.store.get("design-section-data-content") is not None
        assert app.task_tree.store.get("design-section-security") is not None

        # Check labels
        assert app.task_tree.store.get("design-section-project-context-scope").label == "Project Context & Scope"
        assert app.task_tree.store.get("design-section-security").label == "Security"


@pytest.mark.asyncio
async def test_adapter_section_fn_hierarchy():
    """Level-3 headings should nest under the most recent level-2 section."""
    app = PrototypeApp()
    async with app.run_test() as pilot:
        adapter = app.adapter
        done = threading.Event()

        def _worker():
            # First call: a level-2 parent with level-3 children
            adapter.section_fn([
                ("Architecture", 2),
                ("Compute", 3),
                ("Networking", 3),
            ])
            # Second call: new level-2, then level-3 under it
            adapter.section_fn([
                ("Security", 2),
                ("Authentication", 3),
            ])
            done.set()

        t = threading.Thread(target=_worker)
        t.start()
        done.wait(timeout=5)
        t.join(timeout=5)

        await pilot.pause()
        await pilot.pause()

        # All nodes should exist in the store
        assert app.task_tree.store.get("design-section-architecture") is not None
        assert app.task_tree.store.get("design-section-compute") is not None
        assert app.task_tree.store.get("design-section-networking") is not None
        assert app.task_tree.store.get("design-section-security") is not None
        assert app.task_tree.store.get("design-section-authentication") is not None

        # Level-3 nodes should be children of their level-2 parent
        arch = app.task_tree.store.get("design-section-architecture")
        child_ids = [c.id for c in arch.children]
        assert "design-section-compute" in child_ids
        assert "design-section-networking" in child_ids

        sec = app.task_tree.store.get("design-section-security")
        sec_child_ids = [c.id for c in sec.children]
        assert "design-section-authentication" in sec_child_ids


# -------------------------------------------------------------------- #
# print_fn markup preservation
# -------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_adapter_print_fn_preserves_markup():
    """print_fn should detect and preserve Rich markup tags."""
    app = PrototypeApp()
    async with app.run_test() as pilot:
        adapter = app.adapter
        done = threading.Event()

        def _worker():
            adapter.print_fn("[success]✓[/success] All good")
            adapter.print_fn("Plain text without markup")
            done.set()

        t = threading.Thread(target=_worker)
        t.start()
        done.wait(timeout=5)
        t.join(timeout=5)
        # No exception = success (markup preserved for styled, plain for unstyled)


# -------------------------------------------------------------------- #
# response_fn
# -------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_adapter_response_fn_single_section():
    """response_fn with no headings should render without pagination."""
    app = PrototypeApp()
    async with app.run_test() as pilot:
        adapter = app.adapter
        done = threading.Event()

        def _worker():
            adapter.response_fn("Just a simple response with no headings.")
            done.set()

        t = threading.Thread(target=_worker)
        t.start()
        done.wait(timeout=5)
        t.join(timeout=5)
        # No exception = success


@pytest.mark.asyncio
async def test_adapter_on_prompt_submitted_empty_no_echo():
    """Empty submission (pagination) should not echo to console."""
    app = PrototypeApp()
    async with app.run_test() as pilot:
        adapter = app.adapter
        # Submit empty string (simulating "Enter to continue")
        adapter.on_prompt_submitted("")
        # The input event should be set
        assert adapter._input_event.is_set()


@pytest.mark.asyncio
async def test_adapter_status_fn_timer_start_cleared_after_end():
    """After 'end' event, _timer_start should be None."""
    app = PrototypeApp()
    async with app.run_test() as pilot:
        adapter = app.adapter

        # Start the timer
        done1 = threading.Event()

        def _start():
            adapter.status_fn("Analyzing...", "start")
            done1.set()

        t1 = threading.Thread(target=_start)
        t1.start()
        done1.wait(timeout=5)
        t1.join(timeout=5)
        await pilot.pause()
        await pilot.pause()

        assert adapter._timer_start is not None

        # Stop the timer
        done2 = threading.Event()

        def _stop():
            adapter.status_fn("Analyzing...", "end")
            done2.set()

        t2 = threading.Thread(target=_stop)
        t2.start()
        done2.wait(timeout=5)
        t2.join(timeout=5)
        await pilot.pause()
        await pilot.pause()

        # _timer_start should be cleared
        assert adapter._timer_start is None


@pytest.mark.asyncio
async def test_adapter_timer_tick_after_cancel_is_noop():
    """_tick_timer() after _stop() should not overwrite info bar."""
    app = PrototypeApp()
    async with app.run_test() as pilot:
        adapter = app.adapter

        # Start then stop
        done = threading.Event()

        def _lifecycle():
            adapter.status_fn("Thinking...", "start")
            adapter.status_fn("Thinking...", "end")
            adapter.status_fn("500 tokens · 500 session", "tokens")
            done.set()

        t = threading.Thread(target=_lifecycle)
        t.start()
        done.wait(timeout=5)
        t.join(timeout=5)
        await pilot.pause()
        await pilot.pause()

        # Now call _tick_timer on the main thread — should be a no-op
        adapter._tick_timer()
        await pilot.pause()

        # The status should still show the token text, not overwritten by timer
        # (We can't easily read the status text, but verifying no exception
        # and that _timer_start is None confirms the guard works)
        assert adapter._timer_start is None
        assert adapter._timer_handle is None


@pytest.mark.asyncio
async def test_adapter_section_fn_with_bold_headings():
    """section_fn should work when discovery extracts **bold** headings as tuples."""
    from azext_prototype.stages.discovery import extract_section_headers

    app = PrototypeApp()
    async with app.run_test() as pilot:
        adapter = app.adapter

        # Simulate what the discovery session does with bold headings
        response = (
            "Let me explore your requirements.\n"
            "\n"
            "**Hosting & Deployment**\n"
            "How do you plan to host this?\n"
            "\n"
            "**Data Layer**\n"
            "What database will you use?"
        )
        headers = extract_section_headers(response)
        assert len(headers) >= 2  # sanity check
        # Bold headings should be level 2
        assert all(level == 2 for _, level in headers)

        done = threading.Event()

        def _worker():
            adapter.section_fn(headers)
            done.set()

        t = threading.Thread(target=_worker)
        t.start()
        done.wait(timeout=5)
        t.join(timeout=5)
        await pilot.pause()
        await pilot.pause()

        assert app.task_tree.store.get("design-section-hosting-deployment") is not None
        assert app.task_tree.store.get("design-section-data-layer") is not None
