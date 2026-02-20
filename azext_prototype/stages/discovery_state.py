"""Discovery state management — persistent YAML storage for discovery learnings.

This module manages the `.prototype/state/discovery.yaml` file which captures
all learnings from the discovery conversation. The file is:

1. **Read on startup** — Previous context is loaded when design stage restarts
2. **Updated incrementally** — After each exchange, the agent extracts and
   persists new learnings
3. **Conflict-aware** — The agent is prompted to identify and resolve
   conflicts between new and existing information
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DISCOVERY_FILE = ".prototype/state/discovery.yaml"


def _default_discovery_state() -> dict[str, Any]:
    """Return the default empty discovery state structure."""
    return {
        "project": {
            "summary": "",
            "goals": [],
        },
        "requirements": {
            "functional": [],
            "non_functional": [],
        },
        "constraints": [],
        "decisions": [],
        "open_items": [],  # Items that need resolution
        "confirmed_items": [],  # Items that have been confirmed
        "risks": [],
        "scope": {
            "in_scope": [],
            "out_of_scope": [],
            "deferred": [],
        },
        "architecture": {
            "services": [],
            "integrations": [],
            "data_flow": "",
        },
        "conversation_history": [],
        "_metadata": {
            "created": None,
            "last_updated": None,
            "exchange_count": 0,
        },
    }


class DiscoveryState:
    """Manages persistent discovery state in YAML format.

    Provides:
    - Loading existing state on startup
    - Incremental updates after each exchange
    - Formatting state as context for the agent
    - Merging new learnings with existing state
    """

    def __init__(self, project_dir: str):
        self._project_dir = project_dir
        self._path = Path(project_dir) / DISCOVERY_FILE
        self._state: dict[str, Any] = _default_discovery_state()
        self._loaded = False

    @property
    def exists(self) -> bool:
        """Check if a discovery.yaml file exists."""
        return self._path.exists()

    @property
    def state(self) -> dict[str, Any]:
        """Get the current state dict."""
        return self._state

    def load(self) -> dict[str, Any]:
        """Load existing discovery state from YAML.

        Returns the state dict (empty structure if file doesn't exist).
        """
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                    # Merge with defaults to ensure all keys exist
                    self._state = _default_discovery_state()
                    self._deep_merge(self._state, loaded)
                    self._loaded = True
                    logger.info("Loaded discovery state from %s", self._path)
            except (yaml.YAMLError, IOError) as e:
                logger.warning("Could not load discovery state: %s", e)
                self._state = _default_discovery_state()
        else:
            self._state = _default_discovery_state()

        return self._state

    def save(self) -> None:
        """Save the current state to YAML."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # Update metadata
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
        logger.info("Saved discovery state to %s", self._path)

    @property
    def open_count(self) -> int:
        """Get the count of open items needing resolution."""
        return len(self._state.get("open_items", []))

    @property
    def confirmed_count(self) -> int:
        """Get the count of confirmed items."""
        return len(self._state.get("confirmed_items", []))

    def format_open_items(self) -> str:
        """Format open items for display to the user."""
        items = self._state.get("open_items", [])
        if not items:
            return "No open items. All questions have been resolved."

        lines = ["Open items requiring resolution:", ""]
        for i, item in enumerate(items, 1):
            lines.append(f"  {i}. {item}")
        return "\n".join(lines)

    def format_confirmed_items(self) -> str:
        """Format confirmed items for display."""
        items = self._state.get("confirmed_items", [])
        if not items:
            return "No items confirmed yet."

        lines = ["Confirmed items:", ""]
        for item in items:
            lines.append(f"  ✓ {item}")
        return "\n".join(lines)

    def format_status_summary(self) -> str:
        """Format a brief status summary."""
        open_count = self.open_count
        confirmed_count = self.confirmed_count

        parts = []
        if confirmed_count > 0:
            parts.append(f"✓ {confirmed_count} confirmed")
        if open_count > 0:
            parts.append(f"? {open_count} open")

        if not parts:
            return "No items tracked yet."
        return " · ".join(parts)

    def add_open_item(self, item: str) -> None:
        """Add an open item that needs resolution."""
        if item and item not in self._state["open_items"]:
            self._state["open_items"].append(item)
            self.save()

    def resolve_item(self, item: str, confirmed_text: str | None = None) -> None:
        """Move an item from open to confirmed."""
        if item in self._state["open_items"]:
            self._state["open_items"].remove(item)
        if confirmed_text:
            if confirmed_text not in self._state["confirmed_items"]:
                self._state["confirmed_items"].append(confirmed_text)
        self.save()

    def extract_conversation_summary(self) -> str:
        """Extract the requirements summary from conversation history.

        The last assistant message in the conversation typically contains
        the full structured requirements summary (with headings like
        ``## Project Summary``, ``## Confirmed Functional Requirements``,
        etc.) that the biz-analyst produced before the ``[READY]`` marker.

        This is far richer than the structured fields in discovery state,
        which may be empty if ``merge_learnings()`` has not yet been called
        (i.e. architecture generation hasn't completed).
        """
        history = self._state.get("conversation_history", [])
        for exchange in reversed(history):
            assistant_text = exchange.get("assistant", "")
            if not assistant_text:
                continue
            clean = assistant_text.replace("[READY]", "").strip()
            if "## Project Summary" in clean or "## Confirmed Functional Requirements" in clean:
                return clean
        return ""

    def format_as_context(self) -> str:
        """Format the current state as context for the agent.

        Returns a markdown-formatted string that can be included in
        the agent's prompt to provide awareness of existing learnings.

        Checks structured fields first.  When those are empty (common
        when ``merge_learnings()`` hasn't run yet), falls back to
        extracting the requirements summary from conversation history.
        """
        if not self._state or not self._loaded:
            return ""

        parts = []

        # Project summary
        if self._state["project"]["summary"]:
            parts.append("## Existing Project Understanding")
            parts.append(self._state["project"]["summary"])
            parts.append("")

        # Goals
        if self._state["project"]["goals"]:
            parts.append("## Established Goals")
            for goal in self._state["project"]["goals"]:
                parts.append(f"- {goal}")
            parts.append("")

        # Requirements
        if self._state["requirements"]["functional"]:
            parts.append("## Confirmed Functional Requirements")
            for req in self._state["requirements"]["functional"]:
                parts.append(f"- {req}")
            parts.append("")

        if self._state["requirements"]["non_functional"]:
            parts.append("## Confirmed Non-Functional Requirements")
            for req in self._state["requirements"]["non_functional"]:
                parts.append(f"- {req}")
            parts.append("")

        # Constraints
        if self._state["constraints"]:
            parts.append("## Established Constraints")
            for constraint in self._state["constraints"]:
                parts.append(f"- {constraint}")
            parts.append("")

        # Decisions
        if self._state["decisions"]:
            parts.append("## Decisions Made")
            for decision in self._state["decisions"]:
                parts.append(f"- {decision}")
            parts.append("")

        # Open items
        if self._state["open_items"]:
            parts.append("## Open Items (Still Need Resolution)")
            for item in self._state["open_items"]:
                parts.append(f"- {item}")
            parts.append("")

        # Scope
        scope = self._state.get("scope", {})
        if any(scope.get(k) for k in ("in_scope", "out_of_scope", "deferred")):
            parts.append("## Prototype Scope")
            if scope.get("in_scope"):
                parts.append("### In Scope")
                for item in scope["in_scope"]:
                    parts.append(f"- {item}")
                parts.append("")
            if scope.get("out_of_scope"):
                parts.append("### Out of Scope")
                for item in scope["out_of_scope"]:
                    parts.append(f"- {item}")
                parts.append("")
            if scope.get("deferred"):
                parts.append("### Deferred / Future Work")
                for item in scope["deferred"]:
                    parts.append(f"- {item}")
                parts.append("")

        # Architecture
        if self._state["architecture"]["services"]:
            parts.append("## Identified Azure Services")
            for svc in self._state["architecture"]["services"]:
                parts.append(f"- {svc}")
            parts.append("")

        if parts:
            return "\n".join(parts)

        # Structured fields are empty — fall back to conversation history
        return self.extract_conversation_summary()

    def update_from_exchange(
        self,
        user_input: str | list,
        agent_response: str,
        exchange_number: int,
    ) -> None:
        """Record an exchange and prepare for incremental update.

        The actual parsing of learnings from the response is done by
        the agent itself — this method just records the raw exchange
        for history and updates the exchange count.

        Full conversation is preserved to ensure nothing is forgotten.
        When ``user_input`` is a multi-modal content array (list),
        only the text portions are persisted — base64 image data is
        replaced with a placeholder to keep the YAML file manageable.
        """
        # Normalize multi-modal content to text for persistence
        if isinstance(user_input, list):
            text_parts: list[str] = []
            image_count = 0
            for part in user_input:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(part["text"])
                    elif part.get("type") == "image_url":
                        image_count += 1
            persist_text = "\n".join(text_parts)
            if image_count:
                persist_text += f"\n\n[{image_count} image(s) attached]"
        else:
            persist_text = user_input

        # Add to conversation history - store FULL text content
        self._state["conversation_history"].append({
            "exchange": exchange_number,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user": persist_text,
            "assistant": agent_response,
        })

        # Update exchange count
        self._state["_metadata"]["exchange_count"] = exchange_number

        # Save after each exchange
        self.save()

    def merge_learnings(self, learnings: dict[str, Any]) -> None:
        """Merge new learnings extracted by the agent into the state.

        The agent is responsible for extracting structured learnings
        from the conversation. This method merges them into the
        existing state, handling conflicts by preferring newer values.
        """
        # Merge project info
        if learnings.get("project"):
            if learnings["project"].get("summary"):
                self._state["project"]["summary"] = learnings["project"]["summary"]
            if learnings["project"].get("goals"):
                self._merge_list(self._state["project"]["goals"], learnings["project"]["goals"])

        # Merge requirements
        if learnings.get("requirements"):
            if learnings["requirements"].get("functional"):
                self._merge_list(
                    self._state["requirements"]["functional"],
                    learnings["requirements"]["functional"],
                )
            if learnings["requirements"].get("non_functional"):
                self._merge_list(
                    self._state["requirements"]["non_functional"],
                    learnings["requirements"]["non_functional"],
                )

        # Merge simple lists
        for key in ["constraints", "decisions", "risks"]:
            if learnings.get(key):
                self._merge_list(self._state[key], learnings[key])

        # Handle open items specially — can be added or resolved
        if learnings.get("open_items"):
            self._merge_list(self._state["open_items"], learnings["open_items"])
        if learnings.get("resolved_items"):
            for item in learnings["resolved_items"]:
                if item in self._state["open_items"]:
                    self._state["open_items"].remove(item)

        # Merge scope
        if learnings.get("scope"):
            for scope_key in ("in_scope", "out_of_scope", "deferred"):
                if learnings["scope"].get(scope_key):
                    self._merge_list(
                        self._state["scope"][scope_key],
                        learnings["scope"][scope_key],
                    )

        # Merge architecture
        if learnings.get("architecture"):
            if learnings["architecture"].get("services"):
                self._merge_list(
                    self._state["architecture"]["services"],
                    learnings["architecture"]["services"],
                )
            if learnings["architecture"].get("integrations"):
                self._merge_list(
                    self._state["architecture"]["integrations"],
                    learnings["architecture"]["integrations"],
                )
            if learnings["architecture"].get("data_flow"):
                self._state["architecture"]["data_flow"] = learnings["architecture"]["data_flow"]

        self.save()

    def _merge_list(self, existing: list, new: list) -> None:
        """Merge new items into existing list, avoiding duplicates."""
        for item in new:
            if item and item not in existing:
                existing.append(item)

    def reset(self) -> None:
        """Reset state to defaults and save, preserving the file path."""
        self._state = _default_discovery_state()
        self._loaded = False
        self.save()

    def search_history(self, query: str) -> list[dict]:
        """Search conversation history for exchanges mentioning the query.

        Performs case-insensitive substring matching against both user
        and assistant content in each exchange.
        """
        results = []
        q = query.lower()
        for exchange in self._state.get("conversation_history", []):
            if q in exchange.get("user", "").lower() or q in exchange.get("assistant", "").lower():
                results.append(exchange)
        return results

    def _deep_merge(self, base: dict, updates: dict) -> None:
        """Deep merge updates into base dict."""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value


def build_incremental_update_prompt(existing_context: str, new_input: str) -> str:
    """Build a prompt asking the agent to extract learnings from an exchange.

    This is called after each exchange to have the agent identify what
    new information was learned and format it for storage.
    """
    return f"""Based on this exchange, extract any new learnings to update our requirements document.

## Existing Understanding
{existing_context if existing_context else "(No existing context yet)"}

## New Information from User
{new_input}

Please identify:
1. Any new requirements (functional or non-functional)
2. Any new constraints or decisions
3. Any open items that need resolution
4. Any conflicts with existing understanding (if so, ask for clarification)
5. Any Azure services that should be considered

If there are conflicts between the new information and existing understanding,
ask a clarifying question to resolve the conflict before proceeding.

Format your response as a natural conversation, but internally track these learnings."""
