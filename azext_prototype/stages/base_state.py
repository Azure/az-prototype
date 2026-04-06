"""Base class for YAML-backed persistent state.

Provides the shared lifecycle (init, load, save, reset) and utility
methods (_deep_merge) that all four state managers use:
BuildState, DeployState, DiscoveryState, BacklogState.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class BaseState:
    """YAML-backed state with lazy load, save, and deep merge.

    Subclasses must set ``_STATE_FILE`` (relative path under
    ``.prototype/state/``) and implement ``_default_state()`` to
    return the initial state dict.

    Optional: override ``_post_load()`` for migrations or backfills
    that run after loading from disk.
    """

    _STATE_FILE: str = ""  # e.g., "build.yaml"

    def __init__(self, project_dir: str):
        self._project_dir = project_dir
        self._path = Path(project_dir) / self._STATE_FILE
        self._state: dict[str, Any] = self._default_state()
        self._loaded = False

    @staticmethod
    def _default_state() -> dict[str, Any]:
        """Return the initial empty state dict. Override in subclasses."""
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #

    @property
    def exists(self) -> bool:
        """Check if the state file exists on disk."""
        return self._path.exists()

    @property
    def state(self) -> dict[str, Any]:
        """Get the current state dict."""
        return self._state

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def load(self) -> dict[str, Any]:
        """Load existing state from YAML.

        Returns the state dict (empty structure if file doesn't exist).
        Calls ``_post_load()`` after merging for subclass-specific
        migrations or backfills.
        """
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                self._state = self._default_state()
                self._deep_merge(self._state, loaded)
                self._post_load()
                self._loaded = True
                logger.info("Loaded state from %s", self._path)
            except (yaml.YAMLError, IOError) as e:
                logger.warning("Could not load state from %s: %s", self._path, e)
                self._state = self._default_state()
        else:
            self._state = self._default_state()

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
        logger.info("Saved state to %s", self._path)

    def reset(self) -> None:
        """Reset state to defaults and save."""
        self._state = self._default_state()
        self._loaded = False
        self.save()

    # ------------------------------------------------------------------ #
    # Hooks
    # ------------------------------------------------------------------ #

    def _post_load(self) -> None:
        """Called after load() merges disk data. Override for migrations."""

    # ------------------------------------------------------------------ #
    # Utilities
    # ------------------------------------------------------------------ #

    def _deep_merge(self, base: dict, updates: dict) -> None:
        """Deep merge updates into base dict."""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
