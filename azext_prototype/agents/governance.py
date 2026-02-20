"""Governance context — makes agents aware of policies and templates.

This module provides a lightweight bridge between the agent system and
the governance policies / workload templates.  It is designed so that
governance context is injected into agent system messages *without*
sending the full policy YAML or template manifests to the AI provider
— only a compact prompt summary is sent.

Usage in agents::

    from azext_prototype.agents.governance import GovernanceContext

    ctx = GovernanceContext()
    messages = ctx.get_system_messages(agent_name="cloud-architect")

The ``BaseAgent.get_system_messages()`` method calls this automatically
when governance is enabled (the default for all built-in agents).
"""

from __future__ import annotations

import logging

from azext_prototype.governance import anti_patterns
from azext_prototype.governance.policies import PolicyEngine
from azext_prototype.templates.registry import TemplateRegistry

logger = logging.getLogger(__name__)

# Singleton-style caches so we don't re-parse YAML on every agent call.
_policy_engine: PolicyEngine | None = None
_template_registry: TemplateRegistry | None = None


def _get_policy_engine() -> PolicyEngine:
    """Return a lazily-initialised, cached PolicyEngine."""
    global _policy_engine  # noqa: PLW0603
    if _policy_engine is None:
        _policy_engine = PolicyEngine()
        _policy_engine.load()
    return _policy_engine


def _get_template_registry() -> TemplateRegistry:
    """Return a lazily-initialised, cached TemplateRegistry."""
    global _template_registry  # noqa: PLW0603
    if _template_registry is None:
        _template_registry = TemplateRegistry()
        _template_registry.load()
    return _template_registry


def reset_caches() -> None:
    """Reset the module-level caches (useful in tests)."""
    global _policy_engine, _template_registry  # noqa: PLW0603
    _policy_engine = None
    _template_registry = None
    anti_patterns.reset_cache()


class GovernanceContext:
    """Provides governance-aware system messages for agents.

    Parameters
    ----------
    policy_engine:
        Optional pre-configured engine.  Falls back to the built-in
        policies shipped with the extension.
    template_registry:
        Optional pre-configured registry.  Falls back to the built-in
        workload templates.
    """

    def __init__(
        self,
        policy_engine: PolicyEngine | None = None,
        template_registry: TemplateRegistry | None = None,
    ) -> None:
        self._policy_engine = policy_engine or _get_policy_engine()
        self._template_registry = template_registry or _get_template_registry()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def format_policies(
        self,
        agent_name: str,
        services: list[str] | None = None,
    ) -> str:
        """Return governance policy text for *agent_name*.

        Only rules whose ``applies_to`` includes *agent_name* are
        returned.  If *services* is given, further narrows to
        policies relevant to those service types.
        """
        return self._policy_engine.format_for_prompt(agent_name, services)

    def format_templates(self, category: str | None = None) -> str:
        """Return a concise summary of available workload templates."""
        return self._template_registry.format_for_prompt(category)

    def format_all(
        self,
        agent_name: str,
        services: list[str] | None = None,
        include_templates: bool = True,
    ) -> str:
        """Return combined governance + template context.

        This is the primary method called by ``BaseAgent.get_system_messages()``.
        """
        parts: list[str] = []

        policy_text = self.format_policies(agent_name, services)
        if policy_text:
            parts.append(policy_text)

        if include_templates:
            tmpl_text = self.format_templates()
            if tmpl_text:
                parts.append(tmpl_text)

        return "\n\n".join(parts)

    # ------------------------------------------------------------------ #
    # Post-response validation helpers
    # ------------------------------------------------------------------ #

    def check_response_for_violations(
        self,
        agent_name: str,
        response_text: str,
    ) -> list[str]:
        """Scan AI output for anti-pattern matches.

        Uses the ``anti_patterns`` module which loads domain-specific
        YAML definitions.  Anti-patterns are independent from governance
        policies — some correlate with policies, many do not.

        Returns a list of human-readable warning strings (empty = clean).
        """
        return anti_patterns.scan(response_text)
