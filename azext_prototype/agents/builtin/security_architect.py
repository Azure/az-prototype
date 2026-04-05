"""Security Architect built-in agent — cross-cutting security oversight."""

import logging

from azext_prototype.agents.base import (
    AgentCapability,
    AgentContext,
    AgentContract,
    BaseAgent,
)
from azext_prototype.ai.provider import AIMessage, AIResponse

logger = logging.getLogger(__name__)


class SecurityArchitectAgent(BaseAgent):
    """Cross-cutting security -- RBAC, identity, encryption, inter-layer access.

    Reviews and enforces security across all layers (infrastructure,
    data, application).  Does not generate code directly but reviews
    output from other agents and directs corrections.
    """

    _temperature = 0.1
    _max_tokens = 8192
    _enable_web_search = False
    _include_templates = False
    _knowledge_role = "security-architect"
    _keywords = [
        "security",
        "rbac",
        "identity",
        "encryption",
        "tls",
        "firewall",
        "access",
        "secret",
        "credential",
        "authentication",
        "authorization",
        "managed identity",
        "key vault",
        "private",
        "public",
        "network",
        "compliance",
    ]
    _keyword_weight = 0.1
    _contract = AgentContract(
        inputs=["architecture", "infrastructure_code", "application_code"],
        outputs=["security_review"],
        delegates_to=[],
    )

    def __init__(self):
        super().__init__(
            name="security-architect",
            description="Cross-cutting security — RBAC, identity, encryption, inter-layer access",
            capabilities=[
                AgentCapability.SECURITY_ARCHITECT,
                AgentCapability.SECURITY_REVIEW,
                AgentCapability.ANALYZE,
            ],
            constraints=[
                "Cross-cutting across ALL layers — infrastructure, data, and application",
                "Review RBAC assignments, identity configuration, encryption settings, inter-layer access control",
                "Do NOT generate infrastructure or application code directly — review and direct corrections",
                "Enforce managed identity everywhere — no connection strings or access keys",
                "Enforce RBAC least-privilege — no Owner or Contributor on service identities",
                "Enforce encryption at rest and in transit on all services",
                "Ensure private network access where architecturally appropriate",
                "Ensure Key Vault is used for external secrets, accessed via managed identity",
                "Verify no hardcoded credentials in any layer",
            ],
            system_prompt=SECURITY_ARCHITECT_PROMPT,
        )

    def execute(self, context: AgentContext, task: str) -> AIResponse:
        """Execute security architecture review."""
        messages = self.get_system_messages()

        # Add project context
        project_config = context.project_config
        iac_tool = project_config.get("project", {}).get("iac_tool", "terraform")
        environment = project_config.get("project", {}).get("environment", "dev")
        messages.append(
            AIMessage(
                role="system",
                content=(
                    f"PROJECT CONTEXT:\n"
                    f"- Name: {project_config.get('project', {}).get('name', 'unnamed')}\n"
                    f"- Region: {project_config.get('project', {}).get('location', 'eastus')}\n"
                    f"- IaC Tool: {iac_tool}\n"
                    f"- Environment: {environment}\n"
                    f"- This is a {'prototype/POC' if environment == 'dev' else 'production'} deployment\n"
                ),
            )
        )

        # Include any architecture artifacts for cross-reference
        architecture = context.get_artifact("architecture")
        if architecture:
            messages.append(
                AIMessage(
                    role="system",
                    content=f"ARCHITECTURE CONTEXT:\n{architecture}",
                )
            )

        # Include infrastructure code if available
        infrastructure = context.get_artifact("infrastructure_code")
        if infrastructure:
            messages.append(
                AIMessage(
                    role="system",
                    content=f"INFRASTRUCTURE CODE:\n{infrastructure}",
                )
            )

        # Include application code if available
        application = context.get_artifact("application_code")
        if application:
            messages.append(
                AIMessage(
                    role="system",
                    content=f"APPLICATION CODE:\n{application}",
                )
            )

        # Add conversation history
        messages.extend(context.conversation_history)

        # Add the task
        messages.append(AIMessage(role="user", content=task))

        assert context.ai_provider is not None
        response = context.ai_provider.chat(
            messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

        return self._apply_governance_check(response, context)


SECURITY_ARCHITECT_PROMPT = """You are an expert Azure security architect providing cross-cutting security \
oversight across all layers of a prototype.

Your role is to review and enforce security across infrastructure, data, and application layers. \
You do NOT generate code directly — you review output from other agents and direct corrections.

## Cross-Layer Security Oversight

### Identity & Authentication
- Managed identity MUST be used for ALL service-to-service authentication
- No connection strings with embedded secrets anywhere
- No storage account access keys or shared keys
- No SQL authentication (username/password) — Entra-only
- No hardcoded credentials in source code, config, or environment variables
- No service principal client secrets for service-to-service auth
- Key Vault used for external secrets, accessed via managed identity
- Application layer uses DefaultAzureCredential (or language equivalent)

### RBAC & Access Control
- No Owner or Contributor roles assigned to service identities
- Least-privilege data-plane roles (not control plane) for service identities
- Role assignments scoped to individual resources, not resource groups
- Key Vault uses RBAC authorization (not access policies)
- Cosmos DB / Storage use RBAC (local auth disabled)

### Encryption
- Encryption at rest enabled on all data services
- TLS 1.2+ enforced on all services
- HTTPS-only for all web-facing services
- No min_tls_version set below "1.2"

### Network Security
- No 0.0.0.0/0 or * in NSG/firewall rules
- Public endpoints justified (POC relaxation documented if needed)
- Service firewalls restrict to known IP ranges where possible

### Inter-Layer Access
- Data layer access from application layer MUST use managed identity
- Application layer does NOT access infrastructure directly (uses endpoints/SDKs)
- Frontend does NOT access Azure services directly — uses backend API endpoints
- Background services use the same identity and access patterns as the main application

## Finding Classification

### BLOCKER (must fix before deploy)
- Hardcoded credentials or secrets in any layer
- Missing managed identity (using keys/connection strings)
- Owner/Contributor role on service identity
- Wildcard firewall rules (0.0.0.0/0)
- Missing encryption at rest
- TLS below 1.2

### WARNING (recommended, can defer)
- Missing diagnostic logging
- Overly broad IP ranges in firewall rules
- Missing resource tags
- Public endpoints without documented justification

## Output Format
Structure your response as:

### Security Review Summary
One-line overall assessment: PASS, PASS WITH WARNINGS, or BLOCKED.

### Blockers (if any)
#### [B-NNN] Title
- **Layer:** Infrastructure / Data / Application
- **File:** path or resource reference
- **Issue:** What is wrong
- **Risk:** What could happen
- **Fix:** Exact correction needed

### Warnings (if any)
#### [W-NNN] Title
- **Layer:** Infrastructure / Data / Application
- **Issue:** What could be improved
- **Recommendation:** Suggested change

### Passed Checks
Brief list of security requirements correctly implemented across all layers.
"""
