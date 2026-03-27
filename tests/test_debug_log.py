"""Tests for azext_prototype.debug_log — exhaustive session-level diagnostics."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import azext_prototype.debug_log as debug_log

# ======================================================================
# Helpers
# ======================================================================


@pytest.fixture(autouse=True)
def _reset_debug_log_globals():
    """Ensure each test starts with a clean, inactive logger."""
    saved_logger = debug_log._debug_logger
    saved_path = debug_log._log_path
    debug_log._debug_logger = None
    debug_log._log_path = None
    yield
    # Restore (and close any file handlers we opened)
    if debug_log._debug_logger is not None:
        for handler in list(debug_log._debug_logger.handlers):
            handler.close()
            debug_log._debug_logger.removeHandler(handler)
    debug_log._debug_logger = saved_logger
    debug_log._log_path = saved_path


def _read_log(path: Path) -> str:
    """Read the full log file and return its content."""
    return path.read_text(encoding="utf-8")


# ======================================================================
# Initialization
# ======================================================================


class TestInitDebugLog:
    def test_init_with_debug_env_creates_log_file(self, tmp_path):
        """When DEBUG_PROTOTYPE=true, a log file is created."""
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        assert debug_log._debug_logger is not None
        assert debug_log._log_path is not None
        assert debug_log._log_path.exists()
        content = _read_log(debug_log._log_path)
        assert "Prototype Debug Session" in content

    def test_init_without_env_is_noop(self, tmp_path):
        """Without the env var, init is a no-op."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DEBUG_PROTOTYPE", None)
            debug_log.init_debug_log(str(tmp_path))

        assert debug_log._debug_logger is None
        assert debug_log._log_path is None

    def test_init_with_false_env_is_noop(self, tmp_path):
        """DEBUG_PROTOTYPE=false does not activate logging."""
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "false"}):
            debug_log.init_debug_log(str(tmp_path))

        assert debug_log._debug_logger is None

    def test_init_case_insensitive(self, tmp_path):
        """DEBUG_PROTOTYPE=True (capitalized) works."""
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "True"}):
            debug_log.init_debug_log(str(tmp_path))

        assert debug_log._debug_logger is not None

    def test_init_creates_parent_dirs(self, tmp_path):
        """init_debug_log creates missing parent directories."""
        deep_dir = tmp_path / "a" / "b" / "c"
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(deep_dir))

        assert debug_log._log_path is not None
        assert debug_log._log_path.parent.exists()

    def test_reinit_does_not_duplicate_handlers(self, tmp_path):
        """Calling init twice doesn't add duplicate handlers."""
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))
            handler_count_first = len(debug_log._debug_logger.handlers)
            debug_log.init_debug_log(str(tmp_path))
            handler_count_second = len(debug_log._debug_logger.handlers)

        assert handler_count_first == handler_count_second


# ======================================================================
# is_active / get_log_path
# ======================================================================


class TestIsActive:
    def test_inactive_by_default(self):
        assert debug_log.is_active() is False

    def test_active_after_init(self, tmp_path):
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        assert debug_log.is_active() is True

    def test_get_log_path_none_when_inactive(self):
        assert debug_log.get_log_path() is None

    def test_get_log_path_returns_path_when_active(self, tmp_path):
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        path = debug_log.get_log_path()
        assert path is not None
        assert path.exists()


# ======================================================================
# No-op behavior when inactive
# ======================================================================


class TestNoOpWhenInactive:
    """All logging functions must be silent no-ops when logger is inactive."""

    def test_log_session_start_noop(self):
        debug_log.log_session_start("/tmp/test")  # Should not raise

    def test_log_ai_call_noop(self):
        debug_log.log_ai_call("test_method", user_content="hello")

    def test_log_ai_response_noop(self):
        debug_log.log_ai_response("test_method", response_content="world")

    def test_log_state_change_noop(self):
        debug_log.log_state_change("save", path="/tmp/x")

    def test_log_flow_noop(self):
        debug_log.log_flow("method", "message", key="val")

    def test_log_command_noop(self):
        debug_log.log_command("/help", context="test")

    def test_log_error_noop(self):
        try:
            raise ValueError("boom")
        except ValueError as exc:
            debug_log.log_error("method", exc, context="test")

    def test_log_timer_noop(self):
        with debug_log.log_timer("method", "task"):
            pass  # Should not raise

    def test_debug_alias_noop(self):
        debug_log.debug("method", "msg", k="v")


# ======================================================================
# Active logging — content verification
# ======================================================================


class TestLogSessionStart:
    def test_writes_session_header(self, tmp_path):
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        debug_log.log_session_start(
            project_dir="/tmp/proj",
            ai_provider="copilot",
            model="gpt-4o",
            timeout=300,
            iac_tool="terraform",
            extension_version="1.0.0",
        )

        content = _read_log(debug_log._log_path)
        assert "SESSION_START" in content
        assert "copilot" in content
        assert "gpt-4o" in content
        assert "300s" in content
        assert "terraform" in content
        assert "1.0.0" in content

    def test_session_start_with_discovery_summary(self, tmp_path):
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        debug_log.log_session_start("/tmp/proj", discovery_summary="Build an API")

        content = _read_log(debug_log._log_path)
        assert "Discovery: Build an API" in content

    def test_session_start_minimal_fields(self, tmp_path):
        """Session start with empty optional fields."""
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        debug_log.log_session_start("/tmp/proj")

        content = _read_log(debug_log._log_path)
        assert "SESSION_START" in content
        assert "AI Provider: (none)" in content
        assert "Timeout: default" in content
        assert "IaC Tool: (none)" in content


class TestLogAiCall:
    def test_writes_ai_call_details(self, tmp_path):
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        debug_log.log_ai_call(
            "discovery._chat",
            system_msgs=2,
            system_chars=500,
            history_msgs=4,
            history_chars=1200,
            user_content="Build a web app",
            model="gpt-4o",
            temperature=0.5,
            max_tokens=8192,
        )

        content = _read_log(debug_log._log_path)
        assert "AI_CALL discovery._chat" in content
        assert "System messages: 2" in content
        assert "History messages: 4" in content
        assert "Build a web app" in content
        assert "gpt-4o" in content

    def test_ai_call_with_multimodal_content(self, tmp_path):
        """Multi-modal content array is handled correctly."""
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        multimodal = [
            {"type": "text", "text": "Analyze this image"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]
        debug_log.log_ai_call("vision_call", user_content=multimodal)

        content = _read_log(debug_log._log_path)
        assert "AI_CALL vision_call" in content
        assert "Analyze this image" in content


class TestLogAiResponse:
    def test_writes_response_details(self, tmp_path):
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        debug_log.log_ai_response(
            "discovery._chat",
            elapsed=2.5,
            status=200,
            response_content="Here is the architecture for your web app.",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )

        content = _read_log(debug_log._log_path)
        assert "AI_RESPONSE discovery._chat" in content
        assert "2.5s" in content
        assert "Status: 200" in content
        assert "architecture" in content
        assert "prompt=100" in content

    def test_response_with_no_status(self, tmp_path):
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        debug_log.log_ai_response("method", response_content="ok")

        content = _read_log(debug_log._log_path)
        assert "Status: (n/a)" in content


class TestLogStateChange:
    def test_writes_state_mutation(self, tmp_path):
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        debug_log.log_state_change("save", path="/tmp/state.yaml", items=5, exchanges=3)

        content = _read_log(debug_log._log_path)
        assert "STATE save" in content
        assert "path=/tmp/state.yaml" in content
        assert "items=5" in content
        assert "exchanges=3" in content


class TestLogFlow:
    def test_writes_flow_decision(self, tmp_path):
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        debug_log.log_flow("_run_reentry", "Resuming at topic 3", pending=2)

        content = _read_log(debug_log._log_path)
        assert "FLOW _run_reentry" in content
        assert "Resuming at topic 3" in content
        assert "pending=2" in content


class TestLogCommand:
    def test_writes_command(self, tmp_path):
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        debug_log.log_command("/help", topic="Auth", real_answers=2)

        content = _read_log(debug_log._log_path)
        assert "COMMAND" in content
        assert "command=/help" in content
        assert "topic=Auth" in content


class TestLogError:
    def test_writes_error_with_traceback(self, tmp_path):
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        try:
            raise RuntimeError("Something broke")
        except RuntimeError as exc:
            debug_log.log_error("build._generate", exc, stage="infra", attempt=2)

        content = _read_log(debug_log._log_path)
        assert "ERROR build._generate" in content
        assert "RuntimeError: Something broke" in content
        assert "TRACEBACK" in content
        assert "stage=infra" in content
        assert "attempt=2" in content

    def test_error_without_traceback(self, tmp_path):
        """Exception created outside a try/except has no traceback."""
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        exc = ValueError("no tb")
        debug_log.log_error("method", exc)

        content = _read_log(debug_log._log_path)
        assert "ValueError: no tb" in content


class TestLogTimer:
    def test_context_manager_logs_start_and_end(self, tmp_path):
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        with debug_log.log_timer("build", "generating IaC"):
            pass

        content = _read_log(debug_log._log_path)
        assert "TIMER_START build" in content
        assert "generating IaC" in content
        assert "TIMER_END build" in content

    def test_timer_logs_even_on_exception(self, tmp_path):
        """TIMER_END is written even when the block raises."""
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        with pytest.raises(ZeroDivisionError):
            with debug_log.log_timer("calc", "dividing"):
                1 / 0

        content = _read_log(debug_log._log_path)
        assert "TIMER_START calc" in content
        assert "TIMER_END calc" in content

    def test_timer_inactive_is_noop(self):
        """When inactive, log_timer yields without error."""
        with debug_log.log_timer("m", "msg"):
            x = 1 + 1  # noqa: F841


# ======================================================================
# _truncate helper
# ======================================================================


class TestTruncate:
    def test_short_string_unchanged(self):
        assert debug_log._truncate("hello") == "hello"

    def test_long_string_truncated(self):
        long = "x" * 3000
        result = debug_log._truncate(long)
        assert len(result) < 3000
        assert "3000 chars total" in result
        assert result.startswith("x" * 2000)

    def test_custom_limit(self):
        result = debug_log._truncate("abcdefgh", limit=4)
        assert result.startswith("abcd")
        assert "8 chars total" in result

    def test_multimodal_list_extracts_text(self):
        content = [
            {"type": "text", "text": "Hello"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            {"type": "text", "text": "World"},
        ]
        result = debug_log._truncate(content)
        assert "Hello" in result
        assert "World" in result
        assert "[1 image(s) attached]" in result

    def test_multimodal_list_multiple_images(self):
        content = [
            {"type": "image_url", "image_url": {}},
            {"type": "image_url", "image_url": {}},
        ]
        result = debug_log._truncate(content)
        assert "[2 image(s) attached]" in result

    def test_multimodal_list_no_images(self):
        content = [{"type": "text", "text": "just text"}]
        result = debug_log._truncate(content)
        assert "just text" in result
        assert "image" not in result

    def test_multimodal_long_text_truncated(self):
        content = [{"type": "text", "text": "x" * 3000}]
        result = debug_log._truncate(content, limit=100)
        assert len(result) < 3000
        assert "3000 chars total" in result

    def test_multimodal_empty_list(self):
        result = debug_log._truncate([])
        assert result == ""

    def test_multimodal_non_dict_items_ignored(self):
        """Non-dict items in the list are silently skipped."""
        content = [{"type": "text", "text": "ok"}, "stray_string", 42]
        result = debug_log._truncate(content)
        assert "ok" in result


# ======================================================================
# debug() alias
# ======================================================================


class TestDebugAlias:
    def test_debug_writes_as_flow(self, tmp_path):
        with patch.dict(os.environ, {"DEBUG_PROTOTYPE": "true"}):
            debug_log.init_debug_log(str(tmp_path))

        debug_log.debug("method", "a decision was made", reason="performance")

        content = _read_log(debug_log._log_path)
        assert "FLOW method" in content
        assert "a decision was made" in content
        assert "reason=performance" in content
