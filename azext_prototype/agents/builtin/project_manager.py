"""Project Manager built-in agent — backlog generation for GitHub and Azure DevOps.

Generates a structured backlog from the current architecture design:
- **GitHub**: Creates issues with checkbox task lists in the description.
- **Azure DevOps**: Creates user stories with associated task work items.

Both modes include descriptions and acceptance criteria.

Invoked via: az prototype generate backlog
"""

import json
import logging

from azext_prototype.agents.base import BaseAgent, AgentCapability, AgentContext, AgentContract
from azext_prototype.ai.provider import AIMessage, AIResponse

logger = logging.getLogger(__name__)


class ProjectManagerAgent(BaseAgent):
    """Generate a structured backlog from the architecture design."""

    _temperature = 0.4
    _max_tokens = 8192
    _include_templates = False
    _include_standards = False
    _keywords = [
        "backlog", "story", "stories", "task", "tasks",
        "issue", "issues", "work item", "user story",
        "acceptance criteria", "sprint", "epics",
        "github", "devops", "board",
    ]
    _keyword_weight = 0.12
    _contract = AgentContract(
        inputs=["requirements", "scope", "architecture"],
        outputs=["backlog_items"],
        delegates_to=["cloud-architect", "cost-analyst"],
    )

    def __init__(self):
        super().__init__(
            name="project-manager",
            description=(
                "Analyze architecture to generate a structured backlog of "
                "user stories / issues with tasks and acceptance criteria"
            ),
            capabilities=[AgentCapability.BACKLOG_GENERATION, AgentCapability.ANALYZE],
            constraints=[
                "Every story/issue must include a clear description",
                "Every story/issue must include acceptance criteria",
                "Tasks must be concrete and actionable",
                "Estimate effort using t-shirt sizes (S/M/L/XL)",
                "Group stories by epic / feature area",
                "Include infrastructure, application, and testing stories",
            ],
            system_prompt=PROJECT_MANAGER_PROMPT,
        )

    def execute(self, context: AgentContext, task: str) -> AIResponse:
        """Execute backlog generation.

        1. Ask the AI to decompose the architecture into epics and stories.
        2. Format the output for the target provider (GitHub or Azure DevOps).
        """
        if context.ai_provider is None:
            return AIResponse(content="No AI provider configured.", model="none")

        provider = context.shared_state.get("backlog_provider", "github")

        messages = self.get_system_messages()
        messages.extend(context.conversation_history)

        # Step 1 — structured decomposition
        decompose_task = (
            "Analyze the following architecture and produce a comprehensive "
            "backlog of work items.\n\n"
            "For each item provide:\n"
            "- title\n"
            "- description (2-4 sentences explaining the purpose)\n"
            "- acceptance criteria (numbered list)\n"
            "- tasks (concrete actionable sub-tasks)\n"
            "- effort estimate (S / M / L / XL)\n"
            "- epic / feature area grouping\n\n"
            "Respond ONLY with a JSON array. Each element:\n"
            "```\n"
            "{\n"
            '  "epic": "...",\n'
            '  "title": "...",\n'
            '  "description": "...",\n'
            '  "acceptance_criteria": ["AC1", "AC2"],\n'
            '  "tasks": ["Task 1", "Task 2"],\n'
            '  "effort": "M"\n'
            "}\n"
            "```\n\n"
            "No markdown, no explanation — only the JSON array.\n\n"
            f"{task}"
        )
        messages.append(AIMessage(role="user", content=decompose_task))

        decompose_response = context.ai_provider.chat(
            messages, temperature=0.3, max_tokens=8192,
        )

        # Step 2 — parse structured items
        items = self._parse_items(decompose_response.content)

        # Step 3 — format for the target provider
        format_messages = self.get_system_messages()
        format_messages.extend(context.conversation_history)

        if provider == "github":
            format_instructions = GITHUB_FORMAT_INSTRUCTIONS
        else:
            format_instructions = DEVOPS_FORMAT_INSTRUCTIONS

        format_messages.append(AIMessage(
            role="user",
            content=(
                f"Format the following backlog items for **{provider}**.\n\n"
                f"{format_instructions}\n\n"
                f"## Backlog Items\n```json\n{json.dumps(items, indent=2)}\n```\n\n"
                f"## Original Architecture\n{task}\n\n"
                "Produce the complete, formatted output now."
            ),
        ))

        response = context.ai_provider.chat(
            format_messages, temperature=0.3, max_tokens=self._max_tokens,
        )

        # Post-response governance check
        warnings = self.validate_response(response.content)
        if warnings:
            for w in warnings:
                logger.warning("Governance: %s", w)
            block = "\n\n---\n⚠ **Governance warnings:**\n" + "\n".join(
                f"- {w}" for w in warnings
            )
            response = AIResponse(
                content=response.content + block,
                model=response.model,
                usage=response.usage,
                finish_reason=response.finish_reason,
            )
        return response

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_items(ai_output: str) -> list[dict]:
        """Parse the AI's JSON item list, tolerating markdown fences."""
        text = ai_output.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            items = json.loads(text)
            if isinstance(items, list):
                return items
        except json.JSONDecodeError:
            logger.warning("Could not parse backlog items from AI; returning raw text.")

        # Fall back — wrap the raw text as a single item so formatting step
        # can still attempt to produce useful output.
        return [{"title": "Backlog", "description": ai_output, "tasks": [], "acceptance_criteria": []}]


# ===================================================================== #
#  Prompts & format instructions                                        #
# ===================================================================== #

PROJECT_MANAGER_PROMPT = """\
You are an expert Azure project manager and scrum master.

Your job is to analyze an Azure architecture design and produce a comprehensive
backlog of user stories (or issues) that a development team can execute.

## Rules

1. **Decompose** the architecture into logical epics / feature areas
   (e.g., "Identity & Auth", "Data Layer", "API Services", "Infrastructure",
   "Monitoring & Observability", "CI/CD Pipeline", "Testing").
2. For each epic, create **user stories** written from the developer or
   operator perspective — e.g., "As a developer, I need…"
3. Every story MUST include:
   - A short, descriptive **title**
   - A **description** (2-4 sentences) explaining the goal and value
   - **Acceptance criteria** (numbered, testable statements)
   - **Tasks** — concrete, actionable steps to complete the story
   - **Effort estimate** — t-shirt size (S / M / L / XL)
4. Include stories for infrastructure provisioning, application code,
   database schemas, CI/CD setup, testing, documentation, and security.
5. Order stories with dependencies in mind — foundational items first.
6. Use Azure-specific terminology where applicable.

## Scope Awareness

If scope boundaries are provided:
- **In-scope items** — create stories only for these items
- **Out-of-scope items** — do NOT create stories for these items
- **Deferred items** — create a separate "Deferred / Future Work" epic
  for deferred items with lower-priority stories

## Hierarchical Structure

For GitHub: Epics → Issues (with task checklists in body).
For Azure DevOps: Features → User Stories → Tasks (as child work items).
"""

GITHUB_FORMAT_INSTRUCTIONS = """\
Format the backlog as **GitHub Issues** with the following conventions:

- Each story becomes one GitHub Issue.
- The issue title follows: `[Epic] Story title`
- The issue body contains:
  1. **Description** section
  2. **Acceptance Criteria** section (numbered list)
  3. **Tasks** section — a GitHub-flavored task list using `- [ ] Task text`
  4. **Effort** label suggestion (e.g., `effort/M`)

Use this template for each issue:

```
## Description
<description>

## Acceptance Criteria
1. <criterion 1>
2. <criterion 2>

## Tasks
- [ ] <task 1>
- [ ] <task 2>

**Labels:** `<epic>`, `effort/<size>`
```

Separate each issue with `---`.
"""

DEVOPS_FORMAT_INSTRUCTIONS = """\
Format the backlog as **Azure DevOps work items** with the following conventions:

- Each story becomes a **User Story** work item.
- Each task within the story becomes a **Task** work item (child of the story).
- Use the standard Azure DevOps fields:

For each User Story:
```
### User Story: <title>
**Area Path:** <epic>
**Effort:** <size>

**Description:**
<description>

**Acceptance Criteria:**
1. <criterion 1>
2. <criterion 2>

#### Tasks:
1. **Task:** <task title>
   **Description:** <task detail>
   **Remaining Work:** <hours estimate>
```

Separate each story with `---`.
"""
