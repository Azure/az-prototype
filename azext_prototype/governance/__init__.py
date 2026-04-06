"""Governance umbrella — policies, anti-patterns, and design standards."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def safe_load_yaml(path: Path) -> dict[str, Any] | None:
    """Load a YAML file, returning None on error (logged as warning)."""
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Could not load %s: %s", path.name, exc)
        return None
