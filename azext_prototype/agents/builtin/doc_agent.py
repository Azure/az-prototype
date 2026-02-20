"""Documentation built-in agent — generates project documentation."""

from azext_prototype.agents.base import BaseAgent, AgentCapability, AgentContract


class DocumentationAgent(BaseAgent):
    """Generates project documentation, guides, and runbooks."""

    _temperature = 0.4
    _max_tokens = 4096
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
- ARCHITECTURE.md — Solution architecture with diagrams and service descriptions
- CONFIGURATION.md — Service configuration guide with all settings documented
- DEPLOYMENT.md — Step-by-step deployment runbook with commands
- DEVELOPMENT.md — Local development setup and workflow guide
- README.md — Project overview, quick start, and structure

Documentation standards:
- Use proper Markdown headings and structure
- Include Mermaid diagrams for architecture and flows
- Provide copy-pasteable CLI commands
- List all prerequisites and dependencies
- Include troubleshooting sections for common issues
- Keep it prototype-focused — note production considerations but don't over-document

When generating files, wrap each file in a code block labeled with its path:
```docs/ARCHITECTURE.md
<content>
```
"""
