"""Agent registry — resolution order: custom > override > built-in."""

import logging

from knack.util import CLIError

from azext_prototype.agents.base import BaseAgent, AgentCapability

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Central registry for all agents (built-in, overrides, custom).

    Resolution order when looking up an agent by name:
      1. Custom agents (user-defined)
      2. Override agents (user overrides of built-in)
      3. Built-in agents (ship with the extension)

    This allows customers to fully customize agent behavior while
    still benefiting from the built-in agent library.
    """

    def __init__(self):
        self._builtin: dict[str, BaseAgent] = {}
        self._overrides: dict[str, BaseAgent] = {}
        self._custom: dict[str, BaseAgent] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_builtin(self, agent: BaseAgent):
        """Register a built-in agent (ships with extension)."""
        logger.debug("Registering built-in agent: %s", agent.name)
        self._builtin[agent.name] = agent

    def register_override(self, agent: BaseAgent):
        """Register an override for a built-in agent."""
        if agent.name not in self._builtin:
            logger.warning(
                "Override registered for '%s' but no built-in agent with that name exists.",
                agent.name,
            )
        logger.info("Agent override registered: %s", agent.name)
        agent._is_builtin = False
        self._overrides[agent.name] = agent

    def register_custom(self, agent: BaseAgent):
        """Register a custom agent."""
        logger.info("Custom agent registered: %s", agent.name)
        agent._is_builtin = False
        self._custom[agent.name] = agent

    def remove_custom(self, name: str) -> bool:
        """Remove a custom agent. Returns True if found and removed."""
        if name in self._custom:
            del self._custom[name]
            return True
        return False

    def remove_override(self, name: str) -> bool:
        """Remove an override, restoring the built-in agent."""
        if name in self._overrides:
            del self._overrides[name]
            return True
        return False

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def get(self, name: str) -> BaseAgent:
        """Resolve an agent by name (custom > override > built-in).

        Args:
            name: Agent name.

        Returns:
            Resolved BaseAgent instance.

        Raises:
            CLIError if no agent with that name exists.
        """
        # Resolution order: custom → override → built-in
        if name in self._custom:
            return self._custom[name]
        if name in self._overrides:
            return self._overrides[name]
        if name in self._builtin:
            return self._builtin[name]

        raise CLIError(
            f"Agent '{name}' not found. "
            f"Available: {', '.join(self.list_names())}"
        )

    def find_by_capability(self, capability: AgentCapability) -> list[BaseAgent]:
        """Find all agents with a given capability.

        Returns resolved agents (respecting override/custom priority).
        """
        results = []
        seen_names = set()

        # Check custom agents first
        for agent in self._custom.values():
            if capability in agent.capabilities and agent.name not in seen_names:
                results.append(agent)
                seen_names.add(agent.name)

        # Then overrides
        for agent in self._overrides.values():
            if capability in agent.capabilities and agent.name not in seen_names:
                results.append(agent)
                seen_names.add(agent.name)

        # Then built-ins
        for agent in self._builtin.values():
            if capability in agent.capabilities and agent.name not in seen_names:
                results.append(agent)
                seen_names.add(agent.name)

        return results

    def find_best_for_task(self, task: str) -> BaseAgent | None:
        """Find the best agent for a given task description.

        Asks each agent to score the task versus its capabilities
        and returns the highest scorer.
        """
        all_agents = self.list_all()
        if not all_agents:
            return None

        scored = [(agent, agent.can_handle(task)) for agent in all_agents]
        scored.sort(key=lambda x: x[1], reverse=True)

        if scored[0][1] > 0:
            return scored[0][0]

        return None

    # ------------------------------------------------------------------
    # Priority-based resolution
    # ------------------------------------------------------------------

    _ERROR_SIGNALS = frozenset({
        "error", "fail", "failure", "exception", "bug", "broken", "crash",
        "traceback", "diagnose", "troubleshoot",
    })
    _DOC_SIGNALS = frozenset({
        "document", "readme", "guide", "runbook", "docs", "documentation",
    })
    _COST_SIGNALS = frozenset({
        "cost", "price", "pricing", "budget", "estimate",
    })
    _SCOPE_SIGNALS = frozenset({
        "scope", "requirement", "backlog", "story", "sprint", "coordinate",
    })
    _DISCOVERY_SIGNALS = frozenset({
        "discover", "requirements", "gap", "assumption", "stakeholder",
    })

    def find_agent_for_task(
        self,
        task: str,
        *,
        task_type: str | None = None,
        services: list[str] | None = None,
        iac_tool: str | None = None,
    ) -> BaseAgent | None:
        """Find the best agent using the governance priority chain.

        Implements the CLAUDE.md Agent Delegation Priority:

        1. Error/issue/bug → ``qa-engineer`` (QA owns troubleshooting)
        2. Service + iac_tool → ``terraform-agent`` / ``bicep-agent``
        3. Scope/requirements/communication → ``project-manager``
        4. Multiple services → ``cloud-architect``
        5. Discovery/requirements analysis → ``biz-analyst``
        6. Documentation → ``doc-agent``
        7. Cost estimation → ``cost-analyst``
        8. Fallback → ``find_best_for_task()``
        9. Ultimate fallback → ``project-manager``

        Parameters
        ----------
        task:
            Task description text.
        task_type:
            Explicit task type override: ``"error"``, ``"scope"``,
            ``"docs"``, ``"cost"``, ``"discovery"``.
        services:
            Service names relevant to the task.
        iac_tool:
            IaC tool in use (``"terraform"`` or ``"bicep"``).
        """
        all_agents = self.list_all()
        if not all_agents:
            return None

        words = set(task.lower().split())

        # 1. Error routing → QA agent
        if task_type == "error" or words & self._ERROR_SIGNALS:
            qa = self.find_by_capability(AgentCapability.QA)
            if qa:
                return qa[0]

        # 2. Single service + IaC tool → terraform/bicep agent
        if iac_tool and services and len(services) <= 2:
            cap = AgentCapability.TERRAFORM if iac_tool == "terraform" else AgentCapability.BICEP
            iac_agents = self.find_by_capability(cap)
            if iac_agents:
                return iac_agents[0]

        # 3. Scope signals → project-manager
        if task_type == "scope" or words & self._SCOPE_SIGNALS:
            pm = self.find_by_capability(AgentCapability.BACKLOG_GENERATION)
            if pm:
                return pm[0]

        # 4. Multiple services → cloud-architect
        if services and len(services) > 2:
            arch = self.find_by_capability(AgentCapability.ARCHITECT)
            if arch:
                return arch[0]

        # 5. Discovery signals → biz-analyst
        if task_type == "discovery" or words & self._DISCOVERY_SIGNALS:
            biz = self.find_by_capability(AgentCapability.BIZ_ANALYSIS)
            if biz:
                return biz[0]

        # 6. Documentation signals → doc-agent
        if task_type == "docs" or words & self._DOC_SIGNALS:
            doc = self.find_by_capability(AgentCapability.DOCUMENT)
            if doc:
                return doc[0]

        # 7. Cost signals → cost-analyst
        if task_type == "cost" or words & self._COST_SIGNALS:
            cost = self.find_by_capability(AgentCapability.COST_ANALYSIS)
            if cost:
                return cost[0]

        # 8. Fallback to keyword scoring
        best = self.find_best_for_task(task)
        if best:
            return best

        # 9. Ultimate fallback → project-manager
        pm = self.find_by_capability(AgentCapability.BACKLOG_GENERATION)
        if pm:
            return pm[0]

        return None

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_all(self) -> list[BaseAgent]:
        """List all resolved agents (respecting override/custom priority)."""
        resolved = {}

        # Built-ins first (lowest priority)
        for name, agent in self._builtin.items():
            resolved[name] = agent

        # Overrides replace built-ins
        for name, agent in self._overrides.items():
            resolved[name] = agent

        # Custom agents always included
        for name, agent in self._custom.items():
            resolved[name] = agent

        return list(resolved.values())

    def list_names(self) -> list[str]:
        """List all resolved agent names."""
        return [a.name for a in self.list_all()]

    def list_all_detailed(self) -> list[dict]:
        """List all agents with metadata including source layer."""
        result = []

        all_names = set(
            list(self._builtin.keys())
            + list(self._overrides.keys())
            + list(self._custom.keys())
        )

        for name in sorted(all_names):
            agent = self.get(name)
            info = agent.to_dict()

            # Determine source
            if name in self._custom:
                info["source"] = "custom"
            elif name in self._overrides:
                info["source"] = "override"
                info["overrides_builtin"] = True
            else:
                info["source"] = "builtin"

            result.append(info)

        return result

    def __len__(self) -> int:
        return len(self.list_all())

    def __contains__(self, name: str) -> bool:
        return name in self._custom or name in self._overrides or name in self._builtin
