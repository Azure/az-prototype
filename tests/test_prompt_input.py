"""Tests for the PromptInput Textual widget.

Tests cover enable/disable state management, submit logic, cursor
positioning, and key handling.  Async tests use Textual's ``run_test()``
pilot harness; synchronous tests exercise pure logic directly.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from azext_prototype.ui.app import PrototypeApp
from azext_prototype.ui.widgets.prompt_input import PromptInput, _PROMPT_PREFIX


# -------------------------------------------------------------------- #
# Submitted message
# -------------------------------------------------------------------- #


class TestSubmittedMessage:
    def test_submitted_message_stores_value(self):
        msg = PromptInput.Submitted("hello world")
        assert msg.value == "hello world"

    def test_submitted_message_empty_string(self):
        msg = PromptInput.Submitted("")
        assert msg.value == ""


# -------------------------------------------------------------------- #
# __init__ defaults
# -------------------------------------------------------------------- #


class TestPromptInputInit:
    @pytest.mark.asyncio
    async def test_default_state(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            assert prompt._enabled is False
            assert prompt._allow_empty is False
            assert prompt.text == _PROMPT_PREFIX

    @pytest.mark.asyncio
    async def test_read_only_by_default(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            assert prompt.read_only is True


# -------------------------------------------------------------------- #
# enable()
# -------------------------------------------------------------------- #


class TestEnable:
    @pytest.mark.asyncio
    async def test_sets_enabled_true(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            assert prompt._enabled is True

    @pytest.mark.asyncio
    async def test_sets_read_only_false(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            assert prompt.read_only is False

    @pytest.mark.asyncio
    async def test_sets_cursor_blink_true(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            assert prompt.cursor_blink is True

    @pytest.mark.asyncio
    async def test_sets_text_to_prefix(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            # Tamper with text, then enable to verify it resets
            prompt.text = "some old text"
            prompt.enable()
            assert prompt.text == _PROMPT_PREFIX

    @pytest.mark.asyncio
    async def test_clears_placeholder(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            assert prompt.placeholder == ""

    @pytest.mark.asyncio
    async def test_allow_empty_stored(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable(allow_empty=True)
            assert prompt._allow_empty is True

    @pytest.mark.asyncio
    async def test_default_no_allow_empty(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            assert prompt._allow_empty is False

    @pytest.mark.asyncio
    async def test_enable_after_disable_restores_state(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            prompt.disable()
            assert prompt._enabled is False
            prompt.enable()
            assert prompt._enabled is True
            assert prompt.read_only is False


# -------------------------------------------------------------------- #
# disable()
# -------------------------------------------------------------------- #


class TestDisable:
    @pytest.mark.asyncio
    async def test_sets_enabled_false(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            prompt.disable()
            assert prompt._enabled is False

    @pytest.mark.asyncio
    async def test_sets_read_only_true(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            prompt.disable()
            assert prompt.read_only is True

    @pytest.mark.asyncio
    async def test_sets_cursor_blink_false(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            prompt.disable()
            assert prompt.cursor_blink is False


# -------------------------------------------------------------------- #
# _submit()
# -------------------------------------------------------------------- #


class TestSubmit:
    @pytest.mark.asyncio
    async def test_submit_strips_prefix_and_posts(self):
        """Submit with text after prefix should post stripped value."""
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            await pilot.pause()
            prompt.text = "> hello world"
            messages = []
            with patch.object(
                prompt, "post_message",
                side_effect=lambda msg: messages.append(msg),
            ):
                prompt._submit()

            submitted = [m for m in messages if isinstance(m, PromptInput.Submitted)]
            assert len(submitted) == 1
            assert submitted[0].value == "hello world"

    @pytest.mark.asyncio
    async def test_submit_resets_text_to_prefix(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            await pilot.pause()
            prompt.text = "> test input"

            with patch.object(prompt, "post_message"):
                prompt._submit()

            assert prompt.text == _PROMPT_PREFIX

    @pytest.mark.asyncio
    async def test_submit_empty_without_allow_does_not_post(self):
        """With only prefix and allow_empty=False, no message should be posted."""
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable(allow_empty=False)
            await pilot.pause()
            prompt.text = "> "
            messages = []
            with patch.object(
                prompt, "post_message",
                side_effect=lambda msg: messages.append(msg),
            ):
                prompt._submit()

            submitted = [m for m in messages if isinstance(m, PromptInput.Submitted)]
            assert len(submitted) == 0

    @pytest.mark.asyncio
    async def test_submit_empty_with_allow_posts_empty_string(self):
        """With allow_empty=True, pressing Enter with no text posts empty string."""
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable(allow_empty=True)
            await pilot.pause()
            prompt.text = "> "
            messages = []
            with patch.object(
                prompt, "post_message",
                side_effect=lambda msg: messages.append(msg),
            ):
                prompt._submit()

            submitted = [m for m in messages if isinstance(m, PromptInput.Submitted)]
            assert len(submitted) == 1
            assert submitted[0].value == ""

    @pytest.mark.asyncio
    async def test_submit_whitespace_only_without_allow_does_not_post(self):
        """Whitespace-only text after prefix should not post without allow_empty."""
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable(allow_empty=False)
            await pilot.pause()
            prompt.text = ">    "
            messages = []
            with patch.object(
                prompt, "post_message",
                side_effect=lambda msg: messages.append(msg),
            ):
                prompt._submit()

            submitted = [m for m in messages if isinstance(m, PromptInput.Submitted)]
            assert len(submitted) == 0

    @pytest.mark.asyncio
    async def test_submit_without_prefix(self):
        """Text that doesn't start with prefix should still be submitted."""
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            await pilot.pause()
            prompt.text = "no prefix here"
            messages = []
            with patch.object(
                prompt, "post_message",
                side_effect=lambda msg: messages.append(msg),
            ):
                prompt._submit()

            submitted = [m for m in messages if isinstance(m, PromptInput.Submitted)]
            assert len(submitted) == 1
            assert submitted[0].value == "no prefix here"

    @pytest.mark.asyncio
    async def test_submit_strips_whitespace(self):
        """Submitted value should be stripped of leading/trailing whitespace."""
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            await pilot.pause()
            prompt.text = ">   padded   "
            messages = []
            with patch.object(
                prompt, "post_message",
                side_effect=lambda msg: messages.append(msg),
            ):
                prompt._submit()

            submitted = [m for m in messages if isinstance(m, PromptInput.Submitted)]
            assert len(submitted) == 1
            assert submitted[0].value == "padded"

    @pytest.mark.asyncio
    async def test_submit_multiline(self):
        """Multiline text should be submitted correctly."""
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            await pilot.pause()
            prompt.text = "> line one\nline two"
            messages = []
            with patch.object(
                prompt, "post_message",
                side_effect=lambda msg: messages.append(msg),
            ):
                prompt._submit()

            submitted = [m for m in messages if isinstance(m, PromptInput.Submitted)]
            assert len(submitted) == 1
            assert "line one" in submitted[0].value
            assert "line two" in submitted[0].value

    @pytest.mark.asyncio
    async def test_submit_does_not_reset_when_no_content(self):
        """When there's no content and allow_empty is False, text should not be reset."""
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable(allow_empty=False)
            await pilot.pause()
            prompt.text = "> "
            with patch.object(prompt, "post_message") as mock_post:
                prompt._submit()
                # Only non-Submitted messages (Changed, SelectionChanged) may fire,
                # but no Submitted message should be posted
                submitted_calls = [
                    c for c in mock_post.call_args_list
                    if isinstance(c[0][0], PromptInput.Submitted)
                ]
                assert len(submitted_calls) == 0
            # Text should remain unchanged (no reset since nothing was submitted)
            assert prompt.text == "> "


# -------------------------------------------------------------------- #
# move_cursor_to_end_of_line()
# -------------------------------------------------------------------- #


class TestMoveCursorToEndOfLine:
    @pytest.mark.asyncio
    async def test_cursor_at_end_of_prefix(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            prompt.text = "> "
            prompt.move_cursor_to_end_of_line()

            row, col = prompt.cursor_location
            assert row == 0
            assert col == len("> ")

    @pytest.mark.asyncio
    async def test_cursor_at_end_of_content(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            prompt.text = "> hello"
            prompt.move_cursor_to_end_of_line()

            row, col = prompt.cursor_location
            assert row == 0
            assert col == len("> hello")

    @pytest.mark.asyncio
    async def test_cursor_end_of_multiline(self):
        """For multiline text, cursor should be at end of last line."""
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            prompt.text = "> line1\nline2"
            prompt.move_cursor_to_end_of_line()

            row, col = prompt.cursor_location
            assert row == 1
            assert col == len("line2")


# -------------------------------------------------------------------- #
# _deferred_cursor_fix()
# -------------------------------------------------------------------- #


class TestDeferredCursorFix:
    @pytest.mark.asyncio
    async def test_moves_cursor_when_enabled(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            # Force cursor to wrong position
            prompt.cursor_location = (0, 0)

            prompt._deferred_cursor_fix()

            row, col = prompt.cursor_location
            assert col == len("> ")

    @pytest.mark.asyncio
    async def test_noop_when_disabled(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            # Not enabled
            prompt.text = "> "
            prompt.cursor_location = (0, 0)

            prompt._deferred_cursor_fix()

            # Cursor should not have moved
            row, col = prompt.cursor_location
            assert (row, col) == (0, 0)

    @pytest.mark.asyncio
    async def test_noop_when_text_missing_prefix(self):
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt._enabled = True
            prompt.text = "no prefix"
            prompt.cursor_location = (0, 0)

            prompt._deferred_cursor_fix()

            # Cursor should not have moved (text doesn't start with prefix)
            row, col = prompt.cursor_location
            assert (row, col) == (0, 0)


# -------------------------------------------------------------------- #
# _on_key() -- key handling
# -------------------------------------------------------------------- #


class TestOnKey:
    @pytest.mark.asyncio
    async def test_enter_submits_when_enabled(self):
        """Enter key should trigger submit when enabled."""
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            prompt.text = "> test input"
            prompt.focus()

            # Capture submitted messages
            messages = []
            original_post = prompt.post_message

            def _capture(msg):
                if isinstance(msg, PromptInput.Submitted):
                    messages.append(msg)
                return original_post(msg)

            prompt.post_message = _capture

            await pilot.press("enter")
            await pilot.pause()

            assert len(messages) == 1
            assert messages[0].value == "test input"

    @pytest.mark.asyncio
    async def test_keys_blocked_when_disabled(self):
        """When disabled, key events should be prevented."""
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            # prompt starts disabled
            original_text = prompt.text

            await pilot.press("a")
            await pilot.pause()

            # Text should not have changed
            assert prompt.text == original_text

    @pytest.mark.asyncio
    async def test_ctrl_j_inserts_newline(self):
        """Ctrl+J should insert a newline when enabled."""
        app = PrototypeApp()
        async with app.run_test() as pilot:  # noqa: F841
            prompt = app.prompt_input
            prompt.enable()
            prompt.focus()
            await pilot.pause()

            await pilot.press("ctrl+j")
            await pilot.pause()

            assert "\n" in prompt.text
