"""Backlog state management — persistent YAML storage for backlog items.

This module manages the ``.prototype/state/backlog.yaml`` file which captures
all backlog session state including generated items, push status, and
conversation history.  The file is:

1. **Read on startup** — Previous backlog state is loaded when session restarts
2. **Updated incrementally** — After each mutation, state is persisted
3. **Re-entrant** — Items already pushed can be skipped on re-run

The state structure tracks:
- Items — structured list of backlog items (epics with children)
- Provider — github | devops
- Push status — per-item: pending | pushed | failed
- Push results — per-item: URL/ID of created work item
- Context hash — SHA-256 of design context + scope (cache key)
- Conversation history — for session resumption
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

BACKLOG_STATE_FILE = ".prototype/state/backlog.yaml"


def _default_backlog_state() -> dict[str, Any]:
    """Return the default empty backlog state structure."""
    return {
        "items": [],
        "provider": "",
        "org": "",
        "project": "",
        "push_status": [],
        "push_results": [],
        "context_hash": "",
        "conversation_history": [],
        "_metadata": {
            "created": None,
            "last_updated": None,
            "last_pushed": None,
            "iteration": 0,
        },
    }


class BacklogState:
    """Manages persistent backlog state in YAML format.

    Provides:
    - Loading existing state on startup (re-entrant sessions)
    - Item management (set, mark pushed/failed, query)
    - Push status tracking per item
    - Summary and detail formatting for display
    """

    def __init__(self, project_dir: str):
        self._project_dir = project_dir
        self._path = Path(project_dir) / BACKLOG_STATE_FILE
        self._state: dict[str, Any] = _default_backlog_state()
        self._loaded = False

    @property
    def exists(self) -> bool:
        """Check if a backlog.yaml file exists."""
        return self._path.exists()

    @property
    def state(self) -> dict[str, Any]:
        """Get the current state dict."""
        return self._state

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def load(self) -> dict[str, Any]:
        """Load existing backlog state from YAML.

        Returns the state dict (empty structure if file doesn't exist).
        """
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                self._state = _default_backlog_state()
                self._deep_merge(self._state, loaded)
                self._loaded = True
                logger.info("Loaded backlog state from %s", self._path)
            except (yaml.YAMLError, IOError) as e:
                logger.warning("Could not load backlog state: %s", e)
                self._state = _default_backlog_state()
        else:
            self._state = _default_backlog_state()

        return self._state

    def save(self) -> None:
        """Save the current state to YAML."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).isoformat()
        if not self._state["_metadata"]["created"]:
            self._state["_metadata"]["created"] = now
        self._state["_metadata"]["last_updated"] = now

        with open(self._path, "w", encoding="utf-8") as f:
            yaml.dump(
                self._state,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                width=120,
            )
        logger.info("Saved backlog state to %s", self._path)

    def reset(self) -> None:
        """Reset state to defaults and save."""
        self._state = _default_backlog_state()
        self._loaded = False
        self.save()

    # ------------------------------------------------------------------ #
    # Item management
    # ------------------------------------------------------------------ #

    def set_items(self, items: list[dict]) -> None:
        """Store generated backlog items.

        Each item should follow the structure::

            {
                "epic": "Infrastructure",
                "type": "feature",
                "title": "Azure Infrastructure Setup",
                "description": "...",
                "children": [
                    {
                        "type": "user_story",
                        "title": "Configure VNet",
                        "description": "...",
                        "acceptance_criteria": ["AC1"],
                        "tasks": ["Task 1"],
                        "effort": "M",
                    }
                ]
            }

        Or the flat format::

            {
                "epic": "Infra",
                "title": "Setup VNet",
                "description": "...",
                "acceptance_criteria": ["AC1"],
                "tasks": ["Task 1"],
                "effort": "M",
            }
        """
        self._state["items"] = items
        # Reset push status arrays to match new items
        self._state["push_status"] = ["pending"] * len(items)
        self._state["push_results"] = [None] * len(items)
        self.save()

    def mark_item_pushed(self, idx: int, url: str) -> None:
        """Record a successful push result for item at index."""
        if 0 <= idx < len(self._state["push_status"]):
            self._state["push_status"][idx] = "pushed"
            self._state["push_results"][idx] = url
            self._state["_metadata"]["last_pushed"] = datetime.now(timezone.utc).isoformat()
            self.save()

    def mark_item_failed(self, idx: int, error: str) -> None:
        """Record a push failure for item at index."""
        if 0 <= idx < len(self._state["push_status"]):
            self._state["push_status"][idx] = "failed"
            self._state["push_results"][idx] = f"error: {error}"
            self.save()

    def get_pending_items(self) -> list[tuple[int, dict]]:
        """Return items not yet pushed as (index, item) tuples."""
        result = []
        for i, item in enumerate(self._state.get("items", [])):
            if i < len(self._state.get("push_status", [])):
                if self._state["push_status"][i] == "pending":
                    result.append((i, item))
            else:
                result.append((i, item))
        return result

    def get_pushed_items(self) -> list[tuple[int, dict]]:
        """Return items already pushed as (index, item) tuples."""
        result = []
        for i, item in enumerate(self._state.get("items", [])):
            if i < len(self._state.get("push_status", [])):
                if self._state["push_status"][i] == "pushed":
                    result.append((i, item))
        return result

    def get_failed_items(self) -> list[tuple[int, dict]]:
        """Return items that failed push as (index, item) tuples."""
        result = []
        for i, item in enumerate(self._state.get("items", [])):
            if i < len(self._state.get("push_status", [])):
                if self._state["push_status"][i] == "failed":
                    result.append((i, item))
        return result

    # ------------------------------------------------------------------ #
    # Context hash (cache key)
    # ------------------------------------------------------------------ #

    def set_context_hash(self, design_context: str, scope: dict | None = None) -> None:
        """Compute and store a context hash for cache invalidation."""
        content = design_context
        if scope:
            import json

            content += json.dumps(scope, sort_keys=True)
        self._state["context_hash"] = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        self.save()

    def matches_context(self, design_context: str, scope: dict | None = None) -> bool:
        """Check if the stored context hash matches the current context."""
        content = design_context
        if scope:
            import json

            content += json.dumps(scope, sort_keys=True)
        current_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        return self._state.get("context_hash") == current_hash

    # ------------------------------------------------------------------ #
    # Conversation tracking
    # ------------------------------------------------------------------ #

    def update_from_exchange(
        self,
        user_input: str,
        agent_response: str,
        exchange_number: int,
    ) -> None:
        """Record a conversation exchange from the review loop."""
        self._state["conversation_history"].append(
            {
                "exchange": exchange_number,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user": user_input,
                "assistant": agent_response,
            }
        )
        self.save()

    # ------------------------------------------------------------------ #
    # Formatting
    # ------------------------------------------------------------------ #

    def format_backlog_summary(self) -> str:
        """Format a structured backlog summary for display."""
        items = self._state.get("items", [])
        if not items:
            return "  No backlog items generated yet."

        lines: list[str] = []
        lines.append(f"  Backlog Summary ({len(items)} item(s))")
        lines.append("  " + "=" * 40)
        lines.append("")

        # Group by epic
        epics: dict[str, list[tuple[int, dict]]] = {}
        for i, item in enumerate(items):
            epic = item.get("epic", "Ungrouped")
            epics.setdefault(epic, []).append((i, item))

        for epic, epic_items in epics.items():
            lines.append(f"  {epic}")

            for idx, item in epic_items:
                title = item.get("title", "Untitled")
                effort = item.get("effort", "?")

                # Push status indicator
                status = "  "
                if idx < len(self._state.get("push_status", [])):
                    s = self._state["push_status"][idx]
                    status = {"pending": "  ", "pushed": " v", "failed": " x"}.get(s, "  ")

                # Count children if hierarchical
                children = item.get("children", [])
                child_info = f" ({len(children)} stories)" if children else ""

                lines.append(f"    {status} {idx + 1}. {title} [{effort}]{child_info}")

            lines.append("")

        # Totals
        push_status = self._state.get("push_status", [])
        pushed = sum(1 for s in push_status if s == "pushed")
        failed = sum(1 for s in push_status if s == "failed")
        pending = sum(1 for s in push_status if s == "pending")

        parts = []
        if pushed:
            parts.append(f"{pushed} pushed")
        if failed:
            parts.append(f"{failed} failed")
        if pending:
            parts.append(f"{pending} pending")
        if parts:
            lines.append(f"  Status: {', '.join(parts)}")

        provider = self._state.get("provider", "")
        if provider:
            org = self._state.get("org", "")
            project = self._state.get("project", "")
            target = f"{org}/{project}" if org and project else provider
            lines.append(f"  Target: {provider} ({target})")

        return "\n".join(lines)

    def format_item_detail(self, idx: int) -> str:
        """Format a single item's full detail for display."""
        items = self._state.get("items", [])
        if idx < 0 or idx >= len(items):
            return f"  Item {idx + 1} not found."

        item = items[idx]
        lines: list[str] = []

        title = item.get("title", "Untitled")
        epic = item.get("epic", "")
        effort = item.get("effort", "?")

        lines.append(f"  [{epic}] {title} (Effort: {effort})")
        lines.append("")

        description = item.get("description", "")
        if description:
            lines.append(f"  Description: {description}")
            lines.append("")

        ac = item.get("acceptance_criteria", [])
        if ac:
            lines.append("  Acceptance Criteria:")
            for i, criterion in enumerate(ac, 1):
                lines.append(f"    {i}. {criterion}")
            lines.append("")

        tasks = item.get("tasks", [])
        if tasks:
            lines.append("  Tasks:")
            for task in tasks:
                lines.append(f"    - [ ] {task}")
            lines.append("")

        children = item.get("children", [])
        if children:
            lines.append(f"  Children ({len(children)}):")
            for child in children:
                child_title = child.get("title", "Untitled")
                child_effort = child.get("effort", "?")
                lines.append(f"    - {child_title} [{child_effort}]")
            lines.append("")

        # Push status
        push_status = self._state.get("push_status", [])
        push_results = self._state.get("push_results", [])
        if idx < len(push_status):
            status = push_status[idx]
            lines.append(f"  Push status: {status}")
            if idx < len(push_results) and push_results[idx]:
                lines.append(f"  Result: {push_results[idx]}")

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _deep_merge(self, base: dict, updates: dict) -> None:
        """Deep merge updates into base dict."""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
