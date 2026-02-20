"""Stage framework — guards, state, and base stage class."""

from azext_prototype.stages.base import BaseStage, StageState, StageGuard
from azext_prototype.stages.guards import check_prerequisites

__all__ = ["BaseStage", "StageState", "StageGuard", "check_prerequisites"]
