"""Agent system — built-in Python agents, YAML/Python overrides, and registry."""

from azext_prototype.agents.base import (
    AgentCapability,
    AgentContext,
    AgentContract,
    BaseAgent,
)
from azext_prototype.agents.governance import GovernanceContext
from azext_prototype.agents.loader import load_python_agent, load_yaml_agent
from azext_prototype.agents.registry import AgentRegistry

__all__ = [
    "BaseAgent",
    "AgentCapability",
    "AgentContext",
    "AgentContract",
    "AgentRegistry",
    "GovernanceContext",
    "load_yaml_agent",
    "load_python_agent",
]
