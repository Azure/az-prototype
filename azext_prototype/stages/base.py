"""Base stage class and state management."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from azext_prototype.agents.base import AgentContext
from azext_prototype.agents.registry import AgentRegistry


class StageState(str, Enum):
    """Possible states for a stage."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StageGuard:
    """A prerequisite check for a stage."""

    name: str
    description: str
    check_fn: Any  # Callable[[], bool]
    error_message: str


class BaseStage(ABC):
    """Base class for all prototype stages.

    Each stage (init, design, build, deploy) subclasses this
    and implements its specific workflow.
    """

    def __init__(self, name: str, description: str, reentrant: bool = False):
        """Initialize a stage.

        Args:
            name: Stage name (e.g., 'init', 'design').
            description: Human-readable description.
            reentrant: Whether this stage can be run multiple times.
        """
        self.name = name
        self.description = description
        self.reentrant = reentrant
        self._state = StageState.NOT_STARTED
        self._history: list[dict] = []

    @property
    def state(self) -> StageState:
        return self._state

    @state.setter
    def state(self, value: StageState):
        self._state = value
        self._history.append({
            "state": value.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    @abstractmethod
    def get_guards(self) -> list[StageGuard]:
        """Return prerequisite guards for this stage.

        Guards are checked before execution. If any fail,
        the stage won't run and the user gets actionable guidance.
        """

    @abstractmethod
    def execute(
        self,
        agent_context: AgentContext,
        registry: AgentRegistry,
        **kwargs,
    ) -> dict:
        """Execute the stage.

        Args:
            agent_context: Agent runtime context.
            registry: Agent registry for delegating work.
            **kwargs: Stage-specific parameters from CLI args.

        Returns:
            dict with stage results/outputs.
        """

    def can_run(self) -> tuple[bool, list[str]]:
        """Check if all guards pass.

        Returns:
            (can_run, list_of_failure_messages)
        """
        failures = []
        for guard in self.get_guards():
            try:
                if not guard.check_fn():
                    failures.append(f"{guard.name}: {guard.error_message}")
            except Exception as e:
                failures.append(f"{guard.name}: Check failed — {e}")

        return len(failures) == 0, failures

    def to_dict(self) -> dict:
        """Serialize stage state for display."""
        return {
            "name": self.name,
            "description": self.description,
            "state": self._state.value,
            "reentrant": self.reentrant,
            "history": self._history,
        }
