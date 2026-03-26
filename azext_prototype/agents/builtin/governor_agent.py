"""Governor built-in agent — embedding-based policy enforcement.

Replaces the previous approach of injecting ALL governance policies
(~40KB) into every agent's system prompt. Instead, the governor:

1. **brief()** — Retrieves the most relevant policy rules for a task
   using semantic similarity and formats them as a concise (~1-2KB)
   set of directives for the working agent's prompt.
2. **review()** — Reviews generated output against the full policy set
   using parallel chunked AI evaluation.

The governor is engaged:
- **Design**: Brief for the architect agent's context
- **Build**: Pre-brief before generation, post-review of generated code
- **Deploy**: Pre-deploy review of the deployment plan
"""

import logging

from azext_prototype.agents.base import (
    AgentCapability,
    AgentContext,
    AgentContract,
    BaseAgent,
)
from azext_prototype.ai.provider import AIResponse

logger = logging.getLogger(__name__)

GOVERNOR_PROMPT = """\
You are a governance reviewer for Azure cloud prototypes.

Your role is to ensure that generated code, architecture designs, and
deployment plans comply with the project's governance policies. You are
precise, thorough, and cite specific rule IDs when reporting violations.

When reviewing output:
- List ONLY actual violations — do not list rules that are followed.
- For each violation, cite the rule ID and explain what is wrong.
- Suggest a concrete fix for each violation.
- If there are no violations, say so clearly.
"""


class GovernorAgent(BaseAgent):
    """Governance enforcement agent using embedding-based policy retrieval."""

    _temperature = 0.1
    _max_tokens = 4096
    _governance_aware = False  # Governor IS governance — no recursion
    _include_templates = False
    _include_standards = False
    _keywords = [
        "governance",
        "policy",
        "compliance",
        "violation",
        "enforce",
        "review",
        "audit",
        "rules",
        "standards",
        "regulations",
    ]
    _keyword_weight = 0.15
    _contract = AgentContract(
        inputs=["task_description", "generated_output"],
        outputs=["policy_brief", "policy_violations"],
        delegates_to=[],
    )

    def __init__(self) -> None:
        super().__init__(
            name="governor",
            description="Governance policy enforcement via embedding-based retrieval and review",
            capabilities=[AgentCapability.GOVERNANCE],
            constraints=[
                "Never generate code — only review and advise",
                "Always cite specific rule IDs when reporting violations",
                "Do not block on recommended rules — only required rules are blockers",
            ],
            system_prompt=GOVERNOR_PROMPT,
        )

    def brief(self, context: AgentContext, task_description: str, agent_name: str = "", top_k: int = 10) -> str:
        """Retrieve relevant policies and produce a concise directive brief.

        This is a code-level operation — no AI call is made. Fast and
        deterministic.
        """
        from azext_prototype.governance.governor import brief as _brief

        return _brief(
            project_dir=context.project_dir,
            task_description=task_description,
            agent_name=agent_name,
            top_k=top_k,
        )

    def review(self, context: AgentContext, output_text: str, max_workers: int = 2) -> list[str]:
        """Review generated output against the full policy set.

        Uses parallel chunked evaluation via the AI provider.
        """
        if not context.ai_provider:
            logger.warning("Governor review skipped — no AI provider available")
            return []

        from azext_prototype.governance.governor import review as _review

        return _review(
            project_dir=context.project_dir,
            output_text=output_text,
            ai_provider=context.ai_provider,
            max_workers=max_workers,
        )

    def execute(self, context: AgentContext, task: str) -> AIResponse:
        """Execute a governance review task.

        When called via the orchestrator, performs a full review of the
        task content against all policies.
        """
        violations = self.review(context, task)
        if violations:
            content = "## Governance Violations Found\n\n" + "\n".join(violations)
        else:
            content = "No governance violations found."
        return AIResponse(content=content, model="governor", usage={})
