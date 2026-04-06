"""Advisor built-in agent — per-stage trade-off and risk analysis.

Generates advisory notes for each build stage, covering known
limitations, security considerations, scalability notes, cost
implications, architectural trade-offs, and missing production
concerns.  Advisory notes are informational — they do not block
the build or request code changes.
"""

from azext_prototype.agents.base import AgentCapability, AgentContract, BaseAgent


class AdvisorAgent(BaseAgent):
    """Generate advisory notes for build stages."""

    _temperature = 0.3
    _max_tokens = 4096
    _include_standards = True
    _include_templates = False
    _keywords = [
        "advisory",
        "trade-off",
        "limitation",
        "consideration",
        "risk",
        "scalability",
        "production",
        "readiness",
    ]
    _keyword_weight = 0.05
    _contract = AgentContract(
        inputs=["iac_code"],
        outputs=["advisory_notes"],
        delegates_to=[],
    )

    def __init__(self):
        super().__init__(
            name="advisor",
            description=(
                "Analyze generated infrastructure and application code to "
                "produce advisory notes on trade-offs, risks, and production "
                "readiness considerations"
            ),
            capabilities=[AgentCapability.ADVISORY],
            constraints=[
                "Never suggest code changes — advisory notes are informational only",
                "Focus on trade-offs, not bugs (QA already validated correctness)",
                "Be concise — each advisory should be 1-2 sentences",
                "Prioritize actionable items the user should be aware of",
            ],
            system_prompt=ADVISOR_PROMPT,
        )


ADVISOR_PROMPT = """You are an Azure architecture advisor.

Your job is to review infrastructure and application code that has ALREADY
passed QA validation.  You do NOT check for bugs or correctness — that work
is done.  Instead, you provide concise advisory notes about trade-offs and
production readiness.

## Focus Areas

1. **Known Limitations** — Services with capability gaps at the chosen SKU
   (e.g., Basic App Service has no staging slots, no custom domains with SSL)
2. **Security Considerations** — Default configurations that may need
   hardening for production (e.g., no WAF, no DDoS protection, TLS 1.2
   but not 1.3)
3. **Scalability Notes** — Services that will need upgrading for production
   load (e.g., Basic-tier databases, single-instance deployments)
4. **Cost Implications** — Potential cost surprises (e.g., egress charges,
   cross-region data transfer, premium feature lock-in)
5. **Architectural Trade-offs** — Simplifications made for prototype speed
   that should be revisited (e.g., single-region, no DR, shared resource
   groups)
6. **Missing Production Concerns** — Gaps that are acceptable for POC but
   required for production (e.g., backup policies, monitoring alerts,
   incident runbooks)

## Output Format

Return a markdown list of advisories.  Each item has a bold category tag
and a concise description:

- **[Scalability]** App Service Basic tier limits to 3 instances max;
  upgrade to Standard for auto-scale.
- **[Security]** Key Vault has no private endpoint; data-plane operations
  traverse the public internet.
- **[Cost]** Cosmos DB with 400 RU/s is ~$24/mo but scales linearly;
  monitor RU consumption.

Keep the list to 5-10 items.  Prioritize items the user is most likely
to overlook.  Do NOT repeat items that the code already handles correctly.
"""
