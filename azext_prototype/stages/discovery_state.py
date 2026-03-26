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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DISCOVERY_FILE = ".prototype/state/discovery.yaml"


@dataclass
class TrackedItem:
    """A single tracked discovery item (topic, decision, etc.)."""

    heading: str
    detail: str  # Description / AI question text (was "questions")
    kind: str  # "topic" | "decision" (extensible)
    status: str  # "pending" | "answered" | "confirmed" | "skipped"
    answer_exchange: int | None  # exchange number where resolved

    def to_dict(self) -> dict[str, Any]:
        return {
            "heading": self.heading,
            "detail": self.detail,
            "kind": self.kind,
            "status": self.status,
            "answer_exchange": self.answer_exchange,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TrackedItem:
        return cls(
            heading=d["heading"],
            detail=d.get("detail", d.get("questions", "")),
            kind=d.get("kind", "topic"),
            status=d.get("status", "pending"),
            answer_exchange=d.get("answer_exchange"),
        )


# Backward-compat alias — existing code / tests may still reference Topic
Topic = TrackedItem


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
        "items": [],  # Unified TrackedItem list (replaces topics + open_items + confirmed_items)
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
        "artifact_inventory": {},  # {abs_path: {"hash": sha256_hex, "last_processed": iso_ts}}
        "context_hash": "",  # SHA256 of last --context string processed
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

        # Migrate legacy state (topics + open_items + confirmed_items → items)
        self._migrate_legacy_state()

        return self._state

    def save(self) -> None:
        """Save the current state to YAML."""
        from azext_prototype.debug_log import log_state_change

        log_state_change(
            "save",
            path=str(self._path),
            items=len(self._state.get("items", [])),
            exchanges=self._state.get("_metadata", {}).get("exchange_count", 0),
            inventory_files=len(self._state.get("artifact_inventory", {})),
        )
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

    # ------------------------------------------------------------------ #
    # Unified item counts
    # ------------------------------------------------------------------ #

    @property
    def open_count(self) -> int:
        """Count of all items with status == 'pending'."""
        return sum(1 for item in self._state.get("items", []) if item.get("status") == "pending")

    @property
    def confirmed_count(self) -> int:
        """Count of items with status in ('confirmed', 'answered')."""
        return sum(1 for item in self._state.get("items", []) if item.get("status") in ("confirmed", "answered"))

    # ------------------------------------------------------------------ #
    # Format methods
    # ------------------------------------------------------------------ #

    def format_open_items(self) -> str:
        """Format pending items for display, grouped by kind."""
        raw = self._state.get("items", [])
        pending = [i for i in raw if i.get("status") == "pending"]
        if not pending:
            return "No open items. All questions have been resolved."

        topics = [i for i in pending if i.get("kind") == "topic"]
        decisions = [i for i in pending if i.get("kind") == "decision"]

        lines = ["Open items requiring resolution:", ""]
        idx = 1
        if topics:
            lines.append("Topics:")
            for item in topics:
                lines.append(f"  {idx}. {item['heading']}")
                idx += 1
        if decisions:
            if topics:
                lines.append("")
            lines.append("Decisions:")
            for item in decisions:
                lines.append(f"  {idx}. {item['heading']}")
                idx += 1
        return "\n".join(lines)

    def format_confirmed_items(self) -> str:
        """Format answered/confirmed items for display, grouped by kind."""
        raw = self._state.get("items", [])
        done = [i for i in raw if i.get("status") in ("confirmed", "answered")]
        if not done:
            return "No items confirmed yet."

        topics = [i for i in done if i.get("kind") == "topic"]
        decisions = [i for i in done if i.get("kind") == "decision"]

        lines = ["Confirmed items:", ""]
        if topics:
            lines.append("Topics:")
            for item in topics:
                lines.append(f"  \u2713 {item['heading']}")
        if decisions:
            if topics:
                lines.append("")
            lines.append("Decisions:")
            for item in decisions:
                lines.append(f"  \u2713 {item['heading']}")
        return "\n".join(lines)

    def format_status_summary(self) -> str:
        """Format a brief status summary across all items."""
        raw = self._state.get("items", [])
        if not raw:
            return "No items tracked yet."

        pending = sum(1 for i in raw if i.get("status") == "pending")
        answered = sum(1 for i in raw if i.get("status") == "answered")
        confirmed = sum(1 for i in raw if i.get("status") == "confirmed")
        skipped = sum(1 for i in raw if i.get("status") == "skipped")

        parts = []
        if answered + confirmed > 0:
            parts.append(f"\u2713 {answered + confirmed} confirmed")
        if pending > 0:
            parts.append(f"{pending} open")
        if skipped > 0:
            parts.append(f"- {skipped} skipped")

        if not parts:
            return "No items tracked yet."
        return " \u00b7 ".join(parts)

    # ------------------------------------------------------------------ #
    # Item mutations
    # ------------------------------------------------------------------ #

    def add_open_item(self, item: str) -> None:
        """Add a decision item that needs resolution."""
        # Avoid duplicates by heading
        for existing in self._state["items"]:
            if existing["heading"] == item:
                return
        self._state["items"].append(
            TrackedItem(heading=item, detail=item, kind="decision", status="pending", answer_exchange=None).to_dict()
        )
        self.save()

    def add_confirmed_decision(self, text: str) -> None:
        """Record a context directive as a confirmed decision.

        This is used when ``--context`` adds information that doesn't
        warrant new topics (e.g. "change the app name to X").  The
        decision is stored in the ``decisions`` list so it reaches
        the architect via ``format_as_context()``.
        """
        if text and text not in self._state["decisions"]:
            self._state["decisions"].append(text)
            self.save()

    def resolve_item(self, item: str, confirmed_text: str | None = None) -> None:
        """Find matching item and mark it confirmed."""
        for existing in self._state["items"]:
            if existing["heading"] == item or existing["heading"] == confirmed_text:
                existing["status"] = "confirmed"
                self.save()
                return
        # If no existing item found and confirmed_text provided, add as confirmed decision
        if confirmed_text:
            self._state["items"].append(
                TrackedItem(
                    heading=confirmed_text,
                    detail=confirmed_text,
                    kind="decision",
                    status="confirmed",
                    answer_exchange=None,
                ).to_dict()
            )
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

        # Open items — query from unified items
        pending_items = [i for i in self._state.get("items", []) if i.get("status") == "pending"]
        if pending_items:
            parts.append("## Open Items (Still Need Resolution)")
            for item in pending_items:
                parts.append(f"- {item['heading']}")
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
        self._state["conversation_history"].append(
            {
                "exchange": exchange_number,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user": persist_text,
                "assistant": agent_response,
            }
        )

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

        # Handle open items — create decision TrackedItems
        if learnings.get("open_items"):
            for item_text in learnings["open_items"]:
                self.add_open_item(item_text)

        # Handle resolved items — mark matching items confirmed
        if learnings.get("resolved_items"):
            for item_text in learnings["resolved_items"]:
                for existing in self._state["items"]:
                    if existing["heading"] == item_text:
                        existing["status"] = "confirmed"

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

    def topic_at_exchange(self, exchange: int) -> str | None:
        """Return the topic heading that was being discussed at *exchange*.

        Uses the ``answer_exchange`` field on items to find the topic
        whose exchange range covers the given number.  Returns ``None``
        if no matching topic is found.
        """
        # Build a list of (answer_exchange, heading) for items that have been answered
        answered = []
        for item in self._state.get("items", []):
            ex = item.get("answer_exchange")
            if ex is not None:
                answered.append((ex, item["heading"]))
        if not answered:
            return None

        # Sort by exchange number
        answered.sort(key=lambda x: x[0])

        # Find the topic whose answer_exchange is >= the given exchange
        # (the topic being discussed AT exchange N gets answer_exchange >= N)
        for ex, heading in answered:
            if ex >= exchange:
                return heading
        # If all answer_exchanges are before the given exchange, it was
        # in the free-form conversation after all topics were covered
        return None

    # ------------------------------------------------------------------ #
    # Unified item persistence (replaces old topic-only methods)
    # ------------------------------------------------------------------ #

    @property
    def items(self) -> list[TrackedItem]:
        """Get all persisted items."""
        return [TrackedItem.from_dict(t) for t in self._state.get("items", [])]

    @property
    def topic_items(self) -> list[TrackedItem]:
        """Get only topic-kind items."""
        return [TrackedItem.from_dict(t) for t in self._state.get("items", []) if t.get("kind") == "topic"]

    def items_by_status(self, status: str) -> list[TrackedItem]:
        """Get items filtered by status."""
        return [TrackedItem.from_dict(t) for t in self._state.get("items", []) if t.get("status") == status]

    @property
    def has_items(self) -> bool:
        """Check if any items have been established."""
        return bool(self._state.get("items"))

    def set_items(self, items: list[TrackedItem]) -> None:
        """Persist items (first-run only)."""
        self._state["items"] = [t.to_dict() for t in items]
        self.save()

    def append_items(self, new_items: list[TrackedItem]) -> None:
        """Append new items, deduplicating by heading (case-insensitive)."""
        existing = self._state.get("items", [])
        existing_headings = {t["heading"].lower() for t in existing}
        for t in new_items:
            if t.heading.lower() not in existing_headings:
                existing.append(t.to_dict())
        self._state["items"] = existing
        self.save()

    def mark_item(self, heading: str, status: str, exchange: int | None = None) -> None:
        """Update an item's status and optionally record the exchange number."""
        from azext_prototype.debug_log import log_state_change

        log_state_change("mark_item", heading=heading, status=status, exchange=exchange)
        for t in self._state.get("items", []):
            if t["heading"] == heading:
                t["status"] = status
                if exchange is not None:
                    t["answer_exchange"] = exchange
                break
        self.save()

    def first_pending_index(self, kind: str | None = None) -> int | None:
        """Return the index of the first pending item, or None if all done.

        If kind is specified, only considers items of that kind.
        """
        for i, t in enumerate(self._state.get("items", [])):
            if t.get("status") == "pending":
                if kind is None or t.get("kind") == kind:
                    return i
        return None

    # ------------------------------------------------------------------ #
    # Artifact inventory — content hashing for change detection
    # ------------------------------------------------------------------ #

    def get_artifact_hashes(self) -> dict[str, str]:
        """Return a flat ``{path: hash}`` mapping from the inventory."""
        inv = self._state.get("artifact_inventory", {})
        return {path: entry["hash"] for path, entry in inv.items() if isinstance(entry, dict) and "hash" in entry}

    def update_artifact_inventory(self, entries: dict[str, str], timestamp: str | None = None) -> None:
        """Bulk-update the artifact inventory with ``{path: sha256_hex}`` entries.

        Additive — does NOT remove paths absent from *entries* so that
        different artifact directories across runs accumulate naturally.
        """
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        inv = self._state.setdefault("artifact_inventory", {})
        for path, hash_hex in entries.items():
            inv[path] = {"hash": hash_hex, "last_processed": ts}
        self.save()

    def get_context_hash(self) -> str:
        """Return the stored SHA256 hash of the last ``--context`` string."""
        return self._state.get("context_hash", "")

    def update_context_hash(self, hash_hex: str) -> None:
        """Store the SHA256 hash of the current ``--context`` string."""
        self._state["context_hash"] = hash_hex
        self.save()

    # ------------------------------------------------------------------ #
    # Backward-compat aliases (old names → new names)
    # ------------------------------------------------------------------ #

    @property
    def topics(self) -> list[TrackedItem]:
        """Alias for items — backward compat."""
        return self.items

    @property
    def has_topics(self) -> bool:
        """Alias for has_items — backward compat."""
        return self.has_items

    def set_topics(self, topics: list[TrackedItem]) -> None:
        """Alias for set_items — backward compat."""
        self.set_items(topics)

    def append_topics(self, new_topics: list[TrackedItem]) -> None:
        """Alias for append_items — backward compat."""
        self.append_items(new_topics)

    def mark_topic(self, heading: str, status: str, exchange: int | None = None) -> None:
        """Alias for mark_item — backward compat."""
        self.mark_item(heading, status, exchange)

    def first_pending_topic_index(self) -> int | None:
        """Alias for first_pending_index — backward compat."""
        return self.first_pending_index()

    # ------------------------------------------------------------------ #
    # Legacy migration
    # ------------------------------------------------------------------ #

    def _migrate_legacy_state(self) -> None:
        """Migrate old-format state (topics + open_items + confirmed_items) to unified items.

        Called at end of load(). Converts legacy fields into TrackedItem dicts
        in the unified ``items`` list, removes the legacy keys, and saves.
        """
        migrated = False

        # Migrate old topics → items with kind="topic"
        if "topics" in self._state and self._state["topics"]:
            existing_headings = {t["heading"].lower() for t in self._state.get("items", [])}
            for t in self._state["topics"]:
                if t.get("heading", "").lower() not in existing_headings:
                    self._state["items"].append(
                        {
                            "heading": t.get("heading", ""),
                            "detail": t.get("questions", t.get("detail", "")),
                            "kind": t.get("kind", "topic"),
                            "status": t.get("status", "pending"),
                            "answer_exchange": t.get("answer_exchange"),
                        }
                    )
                    existing_headings.add(t.get("heading", "").lower())
            del self._state["topics"]
            migrated = True

        # Migrate old open_items → items with kind="decision", status="pending"
        if "open_items" in self._state and self._state["open_items"]:
            existing_headings = {t["heading"].lower() for t in self._state.get("items", [])}
            for item_text in self._state["open_items"]:
                if item_text and item_text.lower() not in existing_headings:
                    self._state["items"].append(
                        {
                            "heading": item_text,
                            "detail": item_text,
                            "kind": "decision",
                            "status": "pending",
                            "answer_exchange": None,
                        }
                    )
                    existing_headings.add(item_text.lower())
            del self._state["open_items"]
            migrated = True

        # Migrate old confirmed_items → items with kind="decision", status="confirmed"
        if "confirmed_items" in self._state and self._state["confirmed_items"]:
            existing_headings = {t["heading"].lower() for t in self._state.get("items", [])}
            for item_text in self._state["confirmed_items"]:
                if item_text and item_text.lower() not in existing_headings:
                    self._state["items"].append(
                        {
                            "heading": item_text,
                            "detail": item_text,
                            "kind": "decision",
                            "status": "confirmed",
                            "answer_exchange": None,
                        }
                    )
                    existing_headings.add(item_text.lower())
            del self._state["confirmed_items"]
            migrated = True

        # Clean up empty legacy keys too
        for legacy_key in ("topics", "open_items", "confirmed_items"):
            if legacy_key in self._state:
                del self._state[legacy_key]
                migrated = True

        if migrated:
            self.save()

    def _deep_merge(self, base: dict, updates: dict) -> None:
        """Deep merge updates into base dict."""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
