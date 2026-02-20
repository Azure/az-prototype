"""Escalation tracking — persistent blocker tracking and automatic escalation.

Implements the CLAUDE.md escalation procedure:

1. Document blocker and attempted solutions
2. Escalate to ``cloud-architect`` (technical) or ``project-manager`` (scope)
3. After extended blocking: expand to web search
4. If still blocked: flag to human
5. Do NOT proceed with workarounds without human approval

State persists to ``.prototype/state/escalation.yaml`` so blockers survive
session restarts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

logger = logging.getLogger(__name__)


# ======================================================================
# Data structures
# ======================================================================

@dataclass
class EscalationEntry:
    """A single tracked blocker with escalation history."""

    task_description: str
    blocker: str
    attempted_solutions: list[str] = field(default_factory=list)
    escalation_level: int = 1  # 1=document, 2=agent, 3=web search, 4=human
    source_agent: str = ""
    source_stage: str = ""
    created_at: str = ""
    last_escalated_at: str = ""
    resolved: bool = False
    resolution: str = ""

    def to_dict(self) -> dict:
        return {
            "task_description": self.task_description,
            "blocker": self.blocker,
            "attempted_solutions": list(self.attempted_solutions),
            "escalation_level": self.escalation_level,
            "source_agent": self.source_agent,
            "source_stage": self.source_stage,
            "created_at": self.created_at,
            "last_escalated_at": self.last_escalated_at,
            "resolved": self.resolved,
            "resolution": self.resolution,
        }

    @classmethod
    def from_dict(cls, data: dict) -> EscalationEntry:
        return cls(
            task_description=data.get("task_description", ""),
            blocker=data.get("blocker", ""),
            attempted_solutions=data.get("attempted_solutions", []),
            escalation_level=data.get("escalation_level", 1),
            source_agent=data.get("source_agent", ""),
            source_stage=data.get("source_stage", ""),
            created_at=data.get("created_at", ""),
            last_escalated_at=data.get("last_escalated_at", ""),
            resolved=data.get("resolved", False),
            resolution=data.get("resolution", ""),
        )


# ======================================================================
# Escalation Tracker
# ======================================================================

_SCOPE_KEYWORDS = frozenset({
    "scope", "requirement", "backlog", "story", "feature",
    "stakeholder", "priority", "sprint",
})


class EscalationTracker:
    """Track blockers and manage escalation through the governance chain.

    Persists to ``.prototype/state/escalation.yaml``.

    Escalation levels:
        1. Documented — blocker recorded with initial context
        2. Agent — escalated to architect (technical) or PM (scope)
        3. Web search — expanded to external documentation search
        4. Human — flagged for manual intervention
    """

    def __init__(self, project_dir: str) -> None:
        self._project_dir = project_dir
        self._entries: list[EscalationEntry] = []
        self._state_path = (
            Path(project_dir) / ".prototype" / "state" / "escalation.yaml"
        )

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Save entries to YAML."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"entries": [e.to_dict() for e in self._entries]}
        with open(self._state_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def load(self) -> None:
        """Load entries from YAML if the file exists."""
        if self._state_path.exists():
            with open(self._state_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._entries = [
                EscalationEntry.from_dict(e)
                for e in data.get("entries", [])
            ]

    @property
    def exists(self) -> bool:
        return self._state_path.exists()

    # ------------------------------------------------------------------
    # Blocker management
    # ------------------------------------------------------------------

    def record_blocker(
        self,
        task: str,
        blocker: str,
        source_agent: str,
        source_stage: str,
    ) -> EscalationEntry:
        """Record a new blocker at level 1 (documented)."""
        now = datetime.now(timezone.utc).isoformat()
        entry = EscalationEntry(
            task_description=task,
            blocker=blocker,
            source_agent=source_agent,
            source_stage=source_stage,
            created_at=now,
            last_escalated_at=now,
            escalation_level=1,
        )
        self._entries.append(entry)
        self.save()
        return entry

    def record_attempted_solution(self, entry: EscalationEntry, solution: str) -> None:
        """Record an attempted solution for a blocker."""
        entry.attempted_solutions.append(solution)
        self.save()

    def resolve(self, entry: EscalationEntry, resolution: str) -> None:
        """Mark a blocker as resolved."""
        entry.resolved = True
        entry.resolution = resolution
        self.save()

    def get_active_blockers(self) -> list[EscalationEntry]:
        """Return all unresolved blockers."""
        return [e for e in self._entries if not e.resolved]

    # ------------------------------------------------------------------
    # Escalation
    # ------------------------------------------------------------------

    def escalate(
        self,
        entry: EscalationEntry,
        registry: Any,
        agent_context: Any,
        print_fn: Callable[[str], None],
    ) -> dict:
        """Escalate a blocker to the next level.

        Returns ``{"escalated": bool, "level": int, "content": str}``.
        """
        from azext_prototype.agents.base import AgentCapability

        current = entry.escalation_level
        now = datetime.now(timezone.utc).isoformat()

        if current >= 4:
            return {"escalated": False, "level": 4, "content": "Already at human level"}

        next_level = current + 1
        entry.escalation_level = next_level
        entry.last_escalated_at = now

        result = {"escalated": True, "level": next_level, "content": ""}

        if next_level == 2:
            # Escalate to architect (technical) or PM (scope)
            result["content"] = self._escalate_to_agent(
                entry, registry, agent_context, print_fn,
            )

        elif next_level == 3:
            # Expand to web search
            result["content"] = self._escalate_to_web_search(
                entry, print_fn,
            )

        elif next_level == 4:
            # Flag for human
            result["content"] = self._escalate_to_human(entry, print_fn)

        self.save()
        return result

    def should_auto_escalate(
        self,
        entry: EscalationEntry,
        *,
        timeout_seconds: int = 120,
    ) -> bool:
        """Check whether a blocker should be auto-escalated based on time."""
        if entry.resolved:
            return False
        if entry.escalation_level >= 4:
            return False

        try:
            last = datetime.fromisoformat(entry.last_escalated_at)
            now = datetime.now(timezone.utc)
            elapsed = (now - last).total_seconds()
            return elapsed >= timeout_seconds
        except (ValueError, TypeError):
            return False

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def format_escalation_report(self) -> str:
        """Format a human-readable escalation report."""
        active = self.get_active_blockers()
        resolved = [e for e in self._entries if e.resolved]

        lines = ["  Escalation Report", "  " + "=" * 40]

        if not self._entries:
            lines.append("  No blockers recorded.")
            return "\n".join(lines)

        if active:
            lines.append(f"\n  Active Blockers ({len(active)}):")
            for i, e in enumerate(active, 1):
                level_label = {1: "Documented", 2: "Agent", 3: "Web Search", 4: "Human"}.get(
                    e.escalation_level, "Unknown"
                )
                lines.append(f"    {i}. [{level_label}] {e.task_description}")
                lines.append(f"       Blocker: {e.blocker}")
                if e.attempted_solutions:
                    lines.append(f"       Attempts: {len(e.attempted_solutions)}")

        if resolved:
            lines.append(f"\n  Resolved ({len(resolved)}):")
            for i, e in enumerate(resolved, 1):
                lines.append(f"    {i}. {e.task_description} — {e.resolution}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal — escalation chain
    # ------------------------------------------------------------------

    def _escalate_to_agent(
        self,
        entry: EscalationEntry,
        registry: Any,
        agent_context: Any,
        print_fn: Callable[[str], None],
    ) -> str:
        """Level 2: Escalate to architect or project-manager."""
        from azext_prototype.agents.base import AgentCapability

        # Determine if it's a scope issue or technical issue
        blocker_lower = entry.blocker.lower()
        is_scope = any(kw in blocker_lower for kw in _SCOPE_KEYWORDS)

        if is_scope:
            cap = AgentCapability.BACKLOG_GENERATION
            role = "project-manager"
        else:
            cap = AgentCapability.ARCHITECT
            role = "cloud-architect"

        agents = registry.find_by_capability(cap)
        if not agents or not agent_context or not agent_context.ai_provider:
            print_fn(f"  Escalation: No {role} available for level-2 escalation")
            return f"No {role} available"

        agent = agents[0]
        attempts = "\n".join(f"- {s}" for s in entry.attempted_solutions) or "None"

        task = (
            f"A blocker has been escalated to you for resolution.\n\n"
            f"## Task\n{entry.task_description}\n\n"
            f"## Blocker\n{entry.blocker}\n\n"
            f"## Attempted Solutions\n{attempts}\n\n"
            f"## Source\nAgent: {entry.source_agent}, Stage: {entry.source_stage}\n\n"
            "Provide a concrete resolution or workaround. If this requires "
            "human decision-making, say so explicitly."
        )

        try:
            response = agent.execute(agent_context, task)
            content = response.content if response else ""
            if content:
                print_fn(f"\n  Escalation ({role}):")
                print_fn(content[:1500])
            return content
        except Exception as exc:
            logger.debug("Agent escalation failed: %s", exc)
            return f"Agent escalation failed: {exc}"

    def _escalate_to_web_search(
        self,
        entry: EscalationEntry,
        print_fn: Callable[[str], None],
    ) -> str:
        """Level 3: Expand to web search for documentation."""
        try:
            from azext_prototype.knowledge.web_search import search_and_fetch

            query = f"{entry.blocker} Azure {entry.source_agent}"
            print_fn(f"\n  Escalation: Searching web for: {query}")

            results = search_and_fetch(query, max_results=3)
            if results:
                content = "\n".join(
                    f"- {r.get('title', 'No title')}: {r.get('snippet', '')}"
                    for r in results
                )
                print_fn("  Web search results found.")
                return content
            else:
                print_fn("  No relevant web results found.")
                return "No web results found"
        except Exception as exc:
            logger.debug("Web search escalation failed: %s", exc)
            print_fn("  Web search unavailable.")
            return f"Web search failed: {exc}"

    @staticmethod
    def _escalate_to_human(
        entry: EscalationEntry,
        print_fn: Callable[[str], None],
    ) -> str:
        """Level 4: Flag for human intervention."""
        attempts = "\n".join(f"  - {s}" for s in entry.attempted_solutions) or "  None"

        message = (
            "\n  *** HUMAN INTERVENTION REQUIRED ***\n"
            f"  Task: {entry.task_description}\n"
            f"  Blocker: {entry.blocker}\n"
            f"  Source: {entry.source_agent} ({entry.source_stage})\n"
            f"  Attempted solutions:\n{attempts}\n"
            "  Please resolve this blocker manually and resume."
        )
        print_fn(message)
        return "Flagged for human intervention"
