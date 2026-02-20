"""Agent orchestrator — runs agent teams with sub-agent delegation.

Mirrors the Claude Code Innovation Factory pattern where a lead agent
(e.g., cloud-architect) can delegate sub-tasks to specialized agents
(e.g., terraform, app-developer).

Usage::

    orchestrator = AgentOrchestrator(registry, context)
    results = orchestrator.run_team(
        objective="Design and build a web API with database",
        agent_names=["cloud-architect", "terraform", "app-developer"],
    )
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from azext_prototype.agents.base import AgentContext
from azext_prototype.agents.registry import AgentRegistry
from azext_prototype.ai.provider import AIMessage, AIResponse

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Data structures
# ------------------------------------------------------------------


@dataclass
class AgentTask:
    """A task assigned to an agent, optionally with sub-tasks."""

    description: str
    assigned_agent: Optional[str] = None
    sub_tasks: list[AgentTask] = field(default_factory=list)
    result: Optional[AIResponse] = None
    status: str = "pending"  # pending | running | completed | failed


@dataclass
class TeamPlan:
    """Execution plan for a team of agents."""

    objective: str
    tasks: list[AgentTask] = field(default_factory=list)


# ------------------------------------------------------------------
# Orchestrator
# ------------------------------------------------------------------


class AgentOrchestrator:
    """Orchestrate agent teams — plan work, delegate, and collect results.

    The orchestrator decomposes an objective into tasks, assigns each
    task to the best-fit agent, and executes them in sequence.  Any
    agent can request the orchestrator to *delegate* a sub-task to
    another agent, enabling the same lead-agent → sub-agent pattern
    that was used in the original Innovation Factory.

    Parameters
    ----------
    registry : AgentRegistry
        Registry containing all available agents.
    context : AgentContext
        Shared runtime context (AI provider, project config, etc.).
    """

    def __init__(self, registry: AgentRegistry, context: AgentContext):
        self.registry = registry
        self.context = context
        self._execution_log: list[dict[str, object]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(
        self,
        objective: str,
        agent_names: list[str] | None = None,
    ) -> TeamPlan:
        """Use AI to decompose *objective* into agent-assigned tasks.

        If *agent_names* is provided only those agents are candidates;
        otherwise every registered agent is considered.
        """
        available = agent_names or self.registry.list_names()
        agent_descriptions = []
        for name in available:
            try:
                agent = self.registry.get(name)
                agent_descriptions.append(f"- {agent.name}: {agent.description}")
            except Exception:
                continue

        planning_prompt = (
            "You are a project planner. Decompose the following objective "
            "into discrete tasks and assign each to exactly one agent.\n\n"
            f"Objective: {objective}\n\n"
            "Available agents:\n"
            + "\n".join(agent_descriptions)
            + "\n\n"
            "Respond as a numbered list. Prefix each task with the agent "
            "name in square brackets.  Indent sub-tasks under their parent.\n"
            "Example:\n"
            "1. [cloud-architect] Design the overall architecture\n"
            "   1a. [terraform] Generate networking module\n"
            "2. [app-developer] Build the API service\n"
        )

        response = self.context.ai_provider.chat(
            [AIMessage(role="user", content=planning_prompt)],
            temperature=0.2,
            max_tokens=2048,
        )

        return self._parse_plan(objective, response.content, available)

    def check_contracts(self, plan: TeamPlan) -> list[str]:
        """Validate that artifact dependencies between agents are satisfiable.

        Returns a list of warning messages for missing inputs.  An empty
        list means all contracts are satisfied.  Warnings are informational
        — agents may still produce useful output with partial context.
        """
        warnings: list[str] = []
        available_outputs: set[str] = set(self.context.artifacts.keys())

        for task in plan.tasks:
            agent_name = task.assigned_agent
            if not agent_name:
                continue
            try:
                agent = self.registry.get(agent_name)
            except Exception:
                continue

            contract = agent.get_contract()
            for inp in contract.inputs:
                if inp not in available_outputs:
                    warnings.append(
                        f"Agent '{agent_name}' expects artifact '{inp}' "
                        f"which may not be available at execution time"
                    )

            # Assume this agent will produce its declared outputs
            available_outputs.update(contract.outputs)

        return warnings

    def execute_plan(self, plan: TeamPlan) -> list[AgentTask]:
        """Execute all tasks in *plan* sequentially (including sub-tasks)."""
        for task in plan.tasks:
            self._execute_task(task)
        return plan.tasks

    def execute_plan_parallel(
        self,
        plan: TeamPlan,
        max_workers: int = 4,
    ) -> list[AgentTask]:
        """Execute independent tasks in *plan* concurrently.

        Tasks with sub-tasks are treated as sequential chains (parent
        before children).  Top-level tasks with no cross-dependencies
        run in parallel via :class:`ThreadPoolExecutor`.

        Parameters
        ----------
        plan : TeamPlan
            The plan to execute.
        max_workers : int
            Maximum concurrent agent executions.
        """
        if not plan.tasks:
            return plan.tasks

        # Build dependency graph from contracts:
        # A task depends on another if its agent's input artifacts overlap
        # with the other agent's output artifacts.
        task_outputs: dict[int, set[str]] = {}
        task_inputs: dict[int, set[str]] = {}

        for i, task in enumerate(plan.tasks):
            agent_name = task.assigned_agent
            if agent_name:
                try:
                    agent = self.registry.get(agent_name)
                    contract = agent.get_contract()
                    task_outputs[i] = set(contract.outputs)
                    task_inputs[i] = set(contract.inputs)
                except Exception:
                    task_outputs[i] = set()
                    task_inputs[i] = set()
            else:
                task_outputs[i] = set()
                task_inputs[i] = set()

        # Build adjacency: task i depends on task j if j produces an
        # artifact that i needs.
        depends_on: dict[int, set[int]] = {i: set() for i in range(len(plan.tasks))}
        for i in range(len(plan.tasks)):
            for j in range(len(plan.tasks)):
                if i != j and task_inputs[i] & task_outputs[j]:
                    depends_on[i].add(j)

        # Topological execution with parallelism
        completed_indices: set[int] = set()
        remaining = set(range(len(plan.tasks)))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while remaining:
                # Find tasks whose dependencies are all completed
                ready = [
                    i for i in remaining
                    if depends_on[i].issubset(completed_indices)
                ]

                if not ready:
                    # Cycle detected or all remaining tasks are blocked
                    # Fall back to sequential for remaining
                    for i in sorted(remaining):
                        self._execute_task(plan.tasks[i])
                        completed_indices.add(i)
                        remaining.discard(i)
                    break

                # Submit ready tasks in parallel
                futures = {}
                for i in ready:
                    remaining.discard(i)
                    futures[executor.submit(self._execute_task, plan.tasks[i])] = i

                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        future.result()
                    except Exception as exc:
                        logger.error(
                            "Parallel task %d failed: %s",
                            idx, exc,
                        )
                        plan.tasks[idx].status = "failed"
                    completed_indices.add(idx)

        return plan.tasks

    def run_team(
        self,
        objective: str,
        agent_names: list[str] | None = None,
    ) -> list[AgentTask]:
        """Plan *and* execute in a single call — the main entry point."""
        plan = self.plan(objective, agent_names)
        return self.execute_plan(plan)

    def delegate(
        self,
        from_agent: str,
        to_agent_name: str,
        sub_task: str,
    ) -> AIResponse:
        """Allow one agent to delegate a sub-task to another.

        This is the key method that enables the team pattern: a lead
        agent can invoke ``delegate()`` to get specialised work done
        by another agent in the registry.
        """
        try:
            agent = self.registry.get(to_agent_name)
        except Exception:
            return AIResponse(
                content=f"Error: agent '{to_agent_name}' not found.",
                model="none",
                usage={},
            )

        self._execution_log.append({
            "type": "delegation",
            "from": from_agent,
            "to": to_agent_name,
            "task": sub_task,
        })

        # Build a sub-context that carries the parent conversation forward
        sub_context = AgentContext(
            ai_provider=self.context.ai_provider,
            project_config=self.context.project_config,
            project_dir=self.context.project_dir,
            conversation_history=list(self.context.conversation_history),
            artifacts=dict(self.context.artifacts),
            shared_state=dict(self.context.shared_state),
        )

        return agent.execute(sub_context, sub_task)

    # ------------------------------------------------------------------
    # Execution helpers
    # ------------------------------------------------------------------

    def _execute_task(self, task: AgentTask) -> None:
        """Execute a single task then recurse into its sub-tasks."""
        agent_name = task.assigned_agent

        # Auto-assign if missing — use priority chain
        if not agent_name:
            best = self.registry.find_agent_for_task(task.description)
            if best:
                agent_name = best.name
                task.assigned_agent = agent_name

        if not agent_name:
            task.status = "failed"
            logger.warning("No agent could be assigned for: %s", task.description)
            return

        try:
            agent = self.registry.get(agent_name)
        except Exception:
            task.status = "failed"
            return

        task.status = "running"
        self._execution_log.append({
            "type": "execution",
            "agent": agent_name,
            "task": task.description,
        })

        try:
            enriched_task = self._enrich_task_with_prior_results(task)
            task.result = agent.execute(self.context, enriched_task)
            task.status = "completed"

            # Feed the result into conversation history for downstream agents
            self.context.conversation_history.append(
                AIMessage(
                    role="assistant",
                    content=f"[{agent_name}]: {task.result.content}",
                )
            )

            # Execute sub-tasks
            for sub in task.sub_tasks:
                self._execute_task(sub)

        except Exception as exc:
            logger.error("Agent '%s' failed: %s", agent_name, exc)
            task.status = "failed"
            task.result = AIResponse(
                content=f"Error: {exc}",
                model="none",
                usage={},
            )

    def _enrich_task_with_prior_results(self, task: AgentTask) -> str:
        """Prepend completed task outputs so later agents have context."""
        prior = [
            f"[{e['agent']}]: completed"
            for e in self._execution_log
            if e["type"] == "execution" and e.get("task") != task.description
        ]

        if prior:
            context_block = "\n".join(prior)
            return (
                f"Previous agent work:\n{context_block}\n\n"
                f"Your task: {task.description}"
            )
        return task.description

    # ------------------------------------------------------------------
    # Plan parsing
    # ------------------------------------------------------------------

    def _parse_plan(
        self,
        objective: str,
        plan_text: str,
        available_agents: list[str],
    ) -> TeamPlan:
        """Parse the AI-generated plan text into a :class:`TeamPlan`."""
        plan = TeamPlan(objective=objective)
        current_task: AgentTask | None = None

        for line in plan_text.strip().splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            agent_name, description = self._parse_task_line(stripped, available_agents)
            if not description:
                continue

            # Detect sub-task (indented or numbered like 1a, 2b, ...)
            is_sub = line.startswith((" ", "\t")) or bool(
                re.match(r"^\d+[a-z]\.", stripped)
            )

            new_task = AgentTask(
                description=description,
                assigned_agent=agent_name,
            )

            if is_sub and current_task is not None:
                current_task.sub_tasks.append(new_task)
            else:
                current_task = new_task
                plan.tasks.append(new_task)

        return plan

    @staticmethod
    def _parse_task_line(
        line: str,
        available_agents: list[str],
    ) -> tuple[str | None, str | None]:
        """Extract ``(agent_name, description)`` from a single plan line."""
        # Strip leading numbering: "1. ", "1a. ", "- "
        cleaned = re.sub(r"^[\d]+[a-z]?\.\s*", "", line)
        cleaned = re.sub(r"^[-*]\s*", "", cleaned)

        if not cleaned:
            return None, None

        # Extract [agent-name] if present
        match = re.match(r"\[([^\]]+)]\s*(.*)", cleaned)
        if match:
            agent_name = match.group(1).strip()
            description = match.group(2).strip()
            if agent_name in available_agents:
                return agent_name, description
            return None, description

        return None, cleaned

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def execution_log(self) -> list[dict[str, object]]:
        """Return a copy of the execution log for debugging/tracking."""
        return list(self._execution_log)
