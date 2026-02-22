"""Base agent class and supporting types."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from azext_prototype.ai.provider import AIMessage, AIProvider, AIResponse

logger = logging.getLogger(__name__)


class AgentCapability(str, Enum):
    """Capabilities an agent can declare."""

    ARCHITECT = "architect"
    DEVELOP = "develop"
    TERRAFORM = "terraform"
    BICEP = "bicep"
    ANALYZE = "analyze"
    DOCUMENT = "document"
    DEPLOY = "deploy"
    TEST = "test"
    COORDINATE = "coordinate"
    QA = "qa"
    BIZ_ANALYSIS = "biz_analysis"
    COST_ANALYSIS = "cost_analysis"
    BACKLOG_GENERATION = "backlog_generation"
    SECURITY_REVIEW = "security_review"
    MONITORING = "monitoring"


@dataclass
class AgentContract:
    """Declares what an agent expects as input and produces as output.

    Used by the orchestrator to validate that artifact dependencies
    are satisfied before executing an agent and to track what artifacts
    become available after execution.

    Attributes:
        inputs: Artifact keys this agent expects in ``AgentContext.artifacts``.
            Missing inputs are warnings (agent may still run with reduced context).
        outputs: Artifact keys this agent produces (added to ``AgentContext.artifacts``).
        delegates_to: Agent names this agent may delegate sub-tasks to.
    """

    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    delegates_to: list[str] = field(default_factory=list)


@dataclass
class AgentContext:
    """Runtime context provided to agents during execution.

    Contains project state, conversation history, and shared resources.
    """

    project_config: dict
    project_dir: str
    ai_provider: AIProvider | None
    conversation_history: list[AIMessage] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    shared_state: dict[str, Any] = field(default_factory=dict)
    mcp_manager: Any = None  # MCPManager | None — typed as Any to avoid circular import

    def add_artifact(self, key: str, value: Any):
        """Store an artifact for other agents to reference."""
        self.artifacts[key] = value

    def get_artifact(self, key: str, default: Any = None) -> Any:
        """Retrieve an artifact by key."""
        return self.artifacts.get(key, default)


class BaseAgent:
    """Base class for all agents (built-in and custom).

    Built-in agents subclass this in Python for optimized behavior.
    YAML agents are wrapped in a YAMLAgent (see loader.py) that
    delegates to the AI provider with the defined system prompt.

    Subclasses that only need the standard
    ``system_messages → history → user task → chat()`` flow can rely
    on the default :meth:`execute` implementation and simply set
    ``_temperature`` / ``_max_tokens`` at the class level.  Override
    ``execute`` for multi-step or specialized pipelines.

    Similarly, the default :meth:`can_handle` scores tasks by matching
    ``_keywords`` with ``_keyword_weight``.  Subclasses only need to
    declare those two attributes.
    """

    # -- Subclass-configurable defaults for the standard execute() --
    _temperature: float = 0.7
    _max_tokens: int = 4096

    # -- Subclass-configurable defaults for keyword-based can_handle() --
    _keywords: list[str] = []
    _keyword_weight: float = 0.1

    # -- Governance awareness --
    _governance_aware: bool = True
    _include_templates: bool = True
    _include_standards: bool = True

    # -- Knowledge system: declare what knowledge this agent needs --
    # Subclasses set these to have knowledge automatically injected
    # into system messages alongside governance context.
    _knowledge_role: str | None = None  # e.g. "architect", "infrastructure"
    _knowledge_tools: list[str] | None = None  # e.g. ["terraform"]
    _knowledge_languages: list[str] | None = None  # e.g. ["python", "csharp"]

    # -- Web search: opt-in for runtime documentation access --
    _enable_web_search: bool = False
    _SEARCH_PATTERN: re.Pattern = re.compile(r"\[SEARCH:\s*(.+?)\]")

    # -- MCP tool calling: opt-in for MCP server tool access --
    _enable_mcp_tools: bool = True
    _max_tool_iterations: int = 10

    # -- Coordination contract: declare inputs, outputs, and delegation targets --
    _contract: AgentContract | None = None

    def __init__(
        self,
        name: str,
        description: str,
        capabilities: list[AgentCapability] | None = None,
        constraints: list[str] | None = None,
        system_prompt: str = "",
    ):
        self.name = name
        self.description = description
        self.capabilities = capabilities or []
        self.constraints = constraints or []
        self.system_prompt = system_prompt
        self._is_builtin = True

    @property
    def is_builtin(self) -> bool:
        """Whether this is a built-in (Python) agent."""
        return self._is_builtin

    def execute(self, context: AgentContext, task: str) -> AIResponse:
        """Execute a task within the given context.

        The default implementation builds system messages, appends
        conversation history and the user task, then calls
        ``context.ai_provider.chat()``.  Override for multi-step
        pipelines (e.g., CostAnalystAgent, CloudArchitectAgent).

        When MCP tools are available (``_enable_mcp_tools`` and
        ``context.mcp_manager`` is set), the AI is given tool
        definitions and the agent handles the tool call loop:
        AI requests tool calls -> agent invokes via MCPManager ->
        feeds results back -> re-invokes AI -> repeats (max 10 iterations).

        After the AI responds, ``validate_response()`` is called to
        check for obvious governance violations.  Warnings are logged
        but do not block the response.
        """
        messages = self.get_system_messages()
        messages.extend(context.conversation_history)
        messages.append(AIMessage(role="user", content=task))

        # Gather MCP tools if available
        tools = self._get_mcp_tools(context)

        response = context.ai_provider.chat(
            messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            tools=tools,
        )

        # Tool call loop: handle tool_calls from the AI response
        if tools and response.tool_calls:
            response = self._handle_tool_call_loop(response, messages, tools, context)

        # Search marker interception (single pass, max 3 searches)
        if self._enable_web_search and self._SEARCH_PATTERN.search(response.content):
            response = self._resolve_searches(response, messages, context)

        # Post-response governance check
        warnings = self.validate_response(response.content)
        if warnings:
            for w in warnings:
                logger.warning("Governance: %s", w)
            # Append warnings as a note in the response
            warning_block = "\n\n---\n" "**Governance warnings:**\n" + "\n".join(f"- {w}" for w in warnings)
            response = AIResponse(
                content=response.content + warning_block,
                model=response.model,
                usage=response.usage,
                finish_reason=response.finish_reason,
            )

        return response

    def can_handle(self, task_description: str) -> float:
        """Score how well this agent can handle a task (0.0 to 1.0).

        The default implementation scores based on keyword matching
        using ``_keywords`` and ``_keyword_weight``.  If no keywords
        are defined, returns 0.5.
        """
        if not self._keywords:
            return 0.5

        task_lower = task_description.lower()
        matches = sum(1 for kw in self._keywords if kw in task_lower)
        return min(0.3 + (matches * self._keyword_weight), 1.0)

    def get_system_messages(self) -> list[AIMessage]:
        """Build system messages for this agent's AI interactions.

        When ``_governance_aware`` is True (default), governance policy
        rules and workload template summaries are injected as an
        additional system message.  This ensures the AI knows all
        required/recommended rules without agents needing to handle
        it themselves.

        When ``_include_standards`` is True (default), design principles
        and coding standards are injected so the AI follows DRY, SOLID,
        and other curated quality standards.

        When any of ``_knowledge_role``, ``_knowledge_tools``, or
        ``_knowledge_languages`` are set, the knowledge system composes
        relevant reference content (role templates, constraints, tool
        patterns, language patterns) and injects it as a system message.
        """
        messages = []

        if self.system_prompt:
            messages.append(AIMessage(role="system", content=self.system_prompt))

        if self.constraints:
            constraint_text = "CONSTRAINTS:\n" + "\n".join(f"- {c}" for c in self.constraints)
            messages.append(AIMessage(role="system", content=constraint_text))

        # Inject governance context
        if self._governance_aware:
            governance_text = self._get_governance_text()
            if governance_text:
                messages.append(AIMessage(role="system", content=governance_text))

        # Inject design standards
        if self._include_standards:
            standards_text = self._get_standards_text()
            if standards_text:
                messages.append(AIMessage(role="system", content=standards_text))

        # Inject knowledge context
        if self._knowledge_role or self._knowledge_tools or self._knowledge_languages:
            knowledge_text = self._get_knowledge_text()
            if knowledge_text:
                messages.append(AIMessage(role="system", content=knowledge_text))

        return messages

    def validate_response(self, response_text: str) -> list[str]:
        """Check AI output for obvious governance violations.

        Returns a list of warning strings (empty = clean).  Called
        automatically by the default ``execute()`` implementation.
        Subclasses with custom ``execute()`` should call this too.
        """
        if not self._governance_aware:
            return []
        try:
            from azext_prototype.agents.governance import GovernanceContext

            ctx = GovernanceContext()
            return ctx.check_response_for_violations(self.name, response_text)
        except Exception:  # pragma: no cover — never let validation break the agent
            return []

    def _get_governance_text(self) -> str:
        """Return formatted governance text for system messages."""
        try:
            from azext_prototype.agents.governance import GovernanceContext

            ctx = GovernanceContext()
            return ctx.format_all(
                agent_name=self.name,
                include_templates=self._include_templates,
            )
        except Exception:  # pragma: no cover — never let governance break the agent
            return ""

    def _get_standards_text(self) -> str:
        """Return formatted design standards for system messages."""
        try:
            from azext_prototype.governance import standards

            return standards.format_for_prompt(agent_name=self.name)
        except Exception:  # pragma: no cover — never let standards break the agent
            return ""

    def _get_knowledge_text(self) -> str:
        """Return composed knowledge context for system messages.

        Uses ``_knowledge_role``, ``_knowledge_tools``, and
        ``_knowledge_languages`` to compose context from the knowledge
        directory via :class:`KnowledgeLoader`.
        """
        try:
            from azext_prototype.knowledge import KnowledgeLoader

            loader = KnowledgeLoader()

            # Flatten tools list to a single tool (compose_context takes one)
            tool = self._knowledge_tools[0] if self._knowledge_tools else None

            # Compose context from all declared knowledge needs
            return loader.compose_context(
                role=self._knowledge_role,
                tool=tool,
                language=(self._knowledge_languages[0] if self._knowledge_languages else None),
                include_constraints=True,
            )
        except Exception:  # pragma: no cover — never let knowledge break the agent
            return ""

    def _get_mcp_tools(self, context: AgentContext) -> list[dict] | None:
        """Get MCP tools in OpenAI schema format if available."""
        if not self._enable_mcp_tools or context.mcp_manager is None:
            return None

        # Determine current stage from shared_state (set by session orchestrators)
        stage = context.shared_state.get("current_stage")
        tools = context.mcp_manager.get_tools_as_openai_schema(
            stage=stage,
            agent=self.name,
        )
        return tools or None

    def _handle_tool_call_loop(
        self,
        response: AIResponse,
        messages: list[AIMessage],
        tools: list[dict],
        context: AgentContext,
    ) -> AIResponse:
        """Handle the tool call loop: invoke tools and re-call AI until done.

        The loop continues until the AI responds without tool_calls or
        the maximum iteration count is reached.
        """
        import json as _json

        total_usage: dict[str, int] = dict(response.usage)

        for _iteration in range(self._max_tool_iterations):
            if not response.tool_calls:
                break

            # Append assistant message with tool calls to history
            messages.append(
                AIMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )

            # Invoke each tool and append results
            for tc in response.tool_calls:
                try:
                    args = _json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
                except (_json.JSONDecodeError, TypeError):
                    args = {}

                result = context.mcp_manager.call_tool(tc.name, args)

                tool_content = result.content
                if result.is_error:
                    tool_content = f"Error: {result.error_message}"

                messages.append(
                    AIMessage(
                        role="tool",
                        content=tool_content,
                        tool_call_id=tc.id,
                    )
                )

            # Re-call AI with tool results
            response = context.ai_provider.chat(
                messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                tools=tools,
            )

            # Merge usage
            for k, v in response.usage.items():
                total_usage[k] = total_usage.get(k, 0) + v

        # Return final response with merged usage
        return AIResponse(
            content=response.content,
            model=response.model,
            usage=total_usage,
            finish_reason=response.finish_reason,
            tool_calls=response.tool_calls,
        )

    def _resolve_searches(
        self,
        response: AIResponse,
        messages: list[AIMessage],
        context: AgentContext,
    ) -> AIResponse:
        """Detect ``[SEARCH: query]`` markers, fetch docs, and re-call the AI.

        Attaches a :class:`~azext_prototype.knowledge.search_cache.SearchCache`
        to *context* on first use so it is shared across agents in the same
        session.
        """
        from azext_prototype.knowledge.search_cache import SearchCache
        from azext_prototype.knowledge.web_search import search_and_fetch

        cache = getattr(context, "_search_cache", None)
        if cache is None:
            cache = SearchCache()
            context._search_cache = cache  # type: ignore[attr-defined]

        markers = self._SEARCH_PATTERN.findall(response.content)[:3]
        results: list[str] = []
        for query in markers:
            cached = cache.get(query)
            if cached:
                results.append(cached)
            else:
                fetched = search_and_fetch(query, max_results=2, max_chars_per_result=2000)
                if fetched:
                    cache.put(query, fetched)
                    results.append(fetched)

        if not results:
            return response  # No results found, return original

        # Re-call with search results injected
        search_context = "DOCUMENTATION SEARCH RESULTS:\n\n" + "\n\n---\n\n".join(results)
        messages.append(AIMessage(role="assistant", content=response.content))
        messages.append(AIMessage(role="system", content=search_context))
        messages.append(
            AIMessage(
                role="user",
                content=(
                    "Search results are now available above. Please continue "
                    "your response using the documentation provided. Do not "
                    "emit further [SEARCH:] markers."
                ),
            )
        )

        final = context.ai_provider.chat(
            messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

        # Merge usage from both calls
        merged_usage = {
            k: response.usage.get(k, 0) + final.usage.get(k, 0)
            for k in set(list(response.usage.keys()) + list(final.usage.keys()))
        }
        return AIResponse(
            content=final.content,
            model=final.model,
            usage=merged_usage,
            finish_reason=final.finish_reason,
        )

    def get_contract(self) -> AgentContract:
        """Return this agent's coordination contract.

        Returns the declared ``_contract`` or an empty one if not set.
        """
        return self._contract or AgentContract()

    def to_dict(self) -> dict:
        """Serialize agent metadata for display."""
        d = {
            "name": self.name,
            "description": self.description,
            "capabilities": [c.value for c in self.capabilities],
            "constraints": self.constraints,
            "is_builtin": self._is_builtin,
        }
        contract = self.get_contract()
        if contract.inputs or contract.outputs or contract.delegates_to:
            d["contract"] = {
                "inputs": contract.inputs,
                "outputs": contract.outputs,
                "delegates_to": contract.delegates_to,
            }
        return d

    def __repr__(self) -> str:
        kind = "builtin" if self._is_builtin else "custom"
        return f"<Agent {self.name} ({kind})>"
