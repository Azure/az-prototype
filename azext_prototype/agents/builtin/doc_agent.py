"""Documentation built-in agent — generates project documentation."""

from azext_prototype.agents.base import AgentCapability, AgentContract, BaseAgent


class DocumentationAgent(BaseAgent):
    """Generates project documentation, guides, and runbooks."""

    _temperature = 0.4
    _max_tokens = 204800
    _include_templates = False
    _include_standards = False
    _keywords = ["document", "readme", "guide", "runbook", "docs", "configuration"]
    _keyword_weight = 0.15
    _contract = AgentContract(
        inputs=["architecture", "requirements"],
        outputs=["documentation"],
        delegates_to=[],
    )

    def __init__(self):
        super().__init__(
            name="doc-agent",
            description="Generate project documentation and guides",
            capabilities=[AgentCapability.DOCUMENT],
            constraints=[
                "Documentation must be in Markdown format",
                "Include practical, copy-pasteable examples",
                "Keep documentation concise — this is a prototype",
                "Cross-reference related docs where appropriate",
                "Include prerequisites and assumptions",
            ],
            system_prompt=DOCUMENTATION_PROMPT,
        )


DOCUMENTATION_PROMPT = """You are a technical documentation specialist for Azure prototypes.

Generate clear, practical documentation in Markdown:
- architecture.md — Solution architecture with diagrams and service descriptions
- deployment-guide.md — Step-by-step deployment runbook with commands

Documentation standards:
- Use proper Markdown headings and structure
- Include Mermaid diagrams for architecture and flows
- Provide copy-pasteable CLI commands
- List all prerequisites and dependencies
- Include troubleshooting sections for common issues (at least 5 common failure scenarios)
- Include rollback procedures
- Include CI/CD integration examples (Azure DevOps YAML + GitHub Actions)
- Include a production backlog section organized by concern area

## CRITICAL: Context Handling
You will receive a summary of ALL previously generated stages with their resource names,
outputs, and RBAC assignments. Use this information to populate architecture diagrams,
deployment runbooks, and configuration tables. Do NOT invent resource names — use the
EXACT names from the stage summaries.

## CRITICAL: Completeness Requirement
Your response MUST be complete. Do NOT truncate any file. If a document is long,
that is acceptable — completeness is mandatory. Every opened section must be closed.
Every started file must be finished. Every stage referenced in the architecture must
appear in both the architecture document and the deployment guide.

When generating files, wrap each file in a code block labeled with its filename:
```architecture.md
<content>
```
"""
