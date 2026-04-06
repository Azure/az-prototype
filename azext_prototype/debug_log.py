"""Exhaustive debug logging for prototype sessions.

Activated by setting ``DEBUG_PROTOTYPE=true`` in the environment.
When active, writes to ``debug_YYYYMMDDHHMMSS.log`` in the project
directory.  When the variable is absent or not ``"true"``, every
function is a no-op with near-zero overhead.

The log is designed to be **diagnostic** — it captures full message
content, state mutations, decision branches, timing, and errors so
that developers, testers, or end-users can send it for examination
without needing to reproduce the issue.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

_debug_logger: logging.Logger | None = None
_log_path: Path | None = None

# Maximum chars to include from a single content field in the log.
# Set high intentionally — the log should be exhaustive, not abbreviated.
_CONTENT_LIMIT = 2000


# ------------------------------------------------------------------ #
# Initialization
# ------------------------------------------------------------------ #


def init_debug_log(project_dir: str) -> None:
    """Initialize debug logging if ``DEBUG_PROTOTYPE=true``."""
    if os.environ.get("DEBUG_PROTOTYPE", "").lower() != "true":
        return
    global _debug_logger, _log_path
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    _log_path = Path(project_dir) / f"debug_{ts}.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _debug_logger = logging.getLogger("prototype.debug")
    _debug_logger.setLevel(logging.DEBUG)
    # Avoid duplicate handlers on re-init
    if not _debug_logger.handlers:
        handler = logging.FileHandler(_log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        _debug_logger.addHandler(handler)
    _debug_logger.info("=== Prototype Debug Session ===")


def is_active() -> bool:
    """Return True when debug logging is active."""
    return _debug_logger is not None


def get_log_path() -> Path | None:
    """Return the path of the current debug log file, or None."""
    return _log_path


# ------------------------------------------------------------------ #
# Session context
# ------------------------------------------------------------------ #


def log_session_start(
    project_dir: str,
    ai_provider: str = "",
    model: str = "",
    timeout: int = 0,
    iac_tool: str = "",
    discovery_summary: str = "",
    extension_version: str = "",
) -> None:
    """Log a session header with environment and config context."""
    if _debug_logger is None:
        return
    lines = [
        f"  Python: {sys.version.split()[0]}",
        f"  OS: {platform.system()} {platform.release()} ({platform.machine()})",
        f"  Extension: {extension_version or 'unknown'}",
        f"  Project: {project_dir}",
        f"  AI Provider: {ai_provider} ({model})" if ai_provider else "  AI Provider: (none)",
        f"  Timeout: {timeout}s" if timeout else "  Timeout: default",
        f"  IaC Tool: {iac_tool}" if iac_tool else "  IaC Tool: (none)",
    ]
    if discovery_summary:
        lines.append(f"  Discovery: {discovery_summary}")
    _debug_logger.info("SESSION_START\n%s", "\n".join(lines))


# ------------------------------------------------------------------ #
# AI calls — the most critical section for troubleshooting
# ------------------------------------------------------------------ #


def _truncate(text: str | list, limit: int = _CONTENT_LIMIT) -> str:
    """Truncate text for logging, handling both str and multi-modal list."""
    if isinstance(text, list):
        # Multi-modal content array — extract text parts
        parts = []
        img_count = 0
        for item in text:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "image_url":
                    img_count += 1
        combined = "\n".join(parts)
        suffix = f"\n[{img_count} image(s) attached]" if img_count else ""
        if len(combined) > limit:
            return combined[:limit] + f"... ({len(combined)} chars total){suffix}"
        return combined + suffix
    if len(text) > limit:
        return text[:limit] + f"... ({len(text)} chars total)"
    return text


def log_ai_call(
    method: str,
    *,
    system_msgs: int = 0,
    system_chars: int = 0,
    history_msgs: int = 0,
    history_chars: int = 0,
    user_content: str | list = "",
    model: str = "",
    temperature: float = 0.0,
    max_tokens: int = 0,
) -> None:
    """Log an outgoing AI request with full payload details."""
    if _debug_logger is None:
        return
    user_chars = (
        len(user_content)
        if isinstance(user_content, str)
        else sum(len(p.get("text", "")) for p in user_content if isinstance(p, dict))
    )
    total = system_chars + history_chars + user_chars
    lines = [
        f"  System messages: {system_msgs} msgs, {system_chars:,} chars",
        f"  History messages: {history_msgs} msgs, {history_chars:,} chars",
        f"  User message: {user_chars:,} chars",
        f"  Total payload: {total:,} chars",
        f"  Model: {model}, Temperature: {temperature}, Max tokens: {max_tokens}",
        "  --- USER MESSAGE ---",
        f"  {_truncate(user_content)}",
        "  --- END USER MESSAGE ---",
    ]
    _debug_logger.info("AI_CALL %s\n%s", method, "\n".join(lines))


def log_ai_response(
    method: str,
    *,
    elapsed: float = 0.0,
    status: int = 0,
    response_content: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    """Log an AI response with timing and content."""
    if _debug_logger is None:
        return
    lines = [
        f"  Elapsed: {elapsed:.1f}s",
        f"  Status: {status}" if status else "  Status: (n/a)",
        f"  Response: {len(response_content):,} chars",
        f"  Tokens: prompt={prompt_tokens} completion={completion_tokens} total={total_tokens}",
        "  --- RESPONSE ---",
        f"  {_truncate(response_content)}",
        "  --- END RESPONSE ---",
    ]
    _debug_logger.info("AI_RESPONSE %s\n%s", method, "\n".join(lines))


# ------------------------------------------------------------------ #
# State mutations
# ------------------------------------------------------------------ #


def log_state_change(operation: str, **details: Any) -> None:
    """Log a state mutation (save, load, mark_item, etc.)."""
    if _debug_logger is None:
        return
    parts = [f"  {k}={v}" for k, v in details.items()]
    _debug_logger.info("STATE %s\n%s", operation, "\n".join(parts))


# ------------------------------------------------------------------ #
# Decision branches and control flow
# ------------------------------------------------------------------ #


def log_flow(method: str, msg: str, **context: Any) -> None:
    """Log a decision branch or flow transition."""
    if _debug_logger is None:
        return
    parts = [f"  {msg}"]
    for k, v in context.items():
        parts.append(f"  {k}={v}")
    _debug_logger.info("FLOW %s\n%s", method, "\n".join(parts))


# ------------------------------------------------------------------ #
# Slash commands
# ------------------------------------------------------------------ #


def log_command(command: str, **context: Any) -> None:
    """Log a slash command invocation."""
    if _debug_logger is None:
        return
    parts = [f"  command={command}"]
    for k, v in context.items():
        parts.append(f"  {k}={v}")
    _debug_logger.info("COMMAND\n%s", "\n".join(parts))


# ------------------------------------------------------------------ #
# Errors
# ------------------------------------------------------------------ #


def log_error(method: str, exc: BaseException, **context: Any) -> None:
    """Log an error with full traceback."""
    if _debug_logger is None:
        return
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    parts = [f"  exception={type(exc).__name__}: {exc}"]
    for k, v in context.items():
        parts.append(f"  {k}={v}")
    parts.append("  --- TRACEBACK ---")
    parts.extend(f"  {line.rstrip()}" for line in "".join(tb).splitlines())
    parts.append("  --- END TRACEBACK ---")
    _debug_logger.error("ERROR %s\n%s", method, "\n".join(parts))


# ------------------------------------------------------------------ #
# Timing
# ------------------------------------------------------------------ #


@contextmanager
def log_timer(method: str, msg: str) -> Iterator[None]:
    """Context manager that logs elapsed time for a block."""
    if _debug_logger is None:
        yield
        return
    start = time.perf_counter()
    _debug_logger.info("TIMER_START %s — %s", method, msg)
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        _debug_logger.info("TIMER_END %s — %s (%.2fs)", method, msg, elapsed)


# ------------------------------------------------------------------ #
# Backward-compat aliases (used by existing instrumentation)
# ------------------------------------------------------------------ #


def debug(method: str, msg: str, **kwargs: Any) -> None:
    """General-purpose debug log (alias for ``log_flow``)."""
    log_flow(method, msg, **kwargs)
