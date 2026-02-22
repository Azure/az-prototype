"""Security Reviewer built-in agent — pre-deployment IaC scanning.

Scans Terraform/Bicep code and architecture designs for security issues:
  - RBAC over-privilege (Owner/Contributor on service identities)
  - Public endpoints without justification
  - Missing encryption at rest or in transit
  - Hardcoded secrets or connection strings
  - Missing managed identity configuration
  - Overly permissive network rules

Reports findings as warnings (non-blocking) or blockers (must fix before deploy).
Runs automatically before the deploy stage.
"""

import logging

from azext_prototype.agents.base import (
    AgentCapability,
    AgentContext,
    AgentContract,
    BaseAgent,
)
from azext_prototype.ai.provider import AIMessage, AIResponse

logger = logging.getLogger(__name__)


class SecurityReviewerAgent(BaseAgent):
    """Scan IaC code and architecture for security issues before deployment."""

    _temperature = 0.1
    _max_tokens = 8192
    _include_templates = False
    _knowledge_role = "security-reviewer"
    _keywords = [
        "security",
        "review",
        "scan",
        "audit",
        "vulnerability",
        "rbac",
        "identity",
        "encryption",
        "tls",
        "firewall",
        "public",
        "endpoint",
        "secret",
        "credential",
        "hardcoded",
        "compliance",
        "policy",
        "private",
        "network",
    ]
    _keyword_weight = 0.12
    _contract = AgentContract(
        inputs=["architecture", "iac_code"],
        outputs=["security_findings"],
        delegates_to=["terraform-agent", "bicep-agent"],
    )

    def __init__(self):
        super().__init__(
            name="security-reviewer",
            description=(
                "Pre-deployment security review of IaC code and architecture; "
                "identifies RBAC issues, public endpoints, missing encryption, "
                "and hardcoded secrets"
            ),
            capabilities=[
                AgentCapability.SECURITY_REVIEW,
                AgentCapability.ANALYZE,
            ],
            constraints=[
                "Classify every finding as BLOCKER or WARNING with a clear rationale",
                "BLOCKERs must be fixed before deployment proceeds",
                "WARNINGs are recommended fixes that can be deferred to production backlog",
                "Always reference the specific file and line/resource where the issue occurs",
                "Suggest the exact fix — don't just describe the problem",
                "POC relaxations (public endpoints, no VNET) are WARNINGs not BLOCKERs",
                "Never flag managed identity connection strings (AZURE_CLIENT_ID is safe)",
            ],
            system_prompt=SECURITY_REVIEWER_PROMPT,
        )

    def execute(self, context: AgentContext, task: str) -> AIResponse:
        """Execute security review of provided IaC code or architecture."""
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

        messages.extend(context.conversation_history)
        messages.append(AIMessage(role="user", content=task))

        assert context.ai_provider is not None
        response = context.ai_provider.chat(
            messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

        # Post-response governance check
        warnings = self.validate_response(response.content)
        if warnings:
            for w in warnings:
                logger.warning("Governance: %s", w)
            warning_block = "\n\n---\n" "**\u26a0 Governance warnings:**\n" + "\n".join(f"- {w}" for w in warnings)
            response = AIResponse(
                content=response.content + warning_block,
                model=response.model,
                usage=response.usage,
                finish_reason=response.finish_reason,
            )

        return response


SECURITY_REVIEWER_PROMPT = """You are an expert Azure security reviewer specializing in Infrastructure as Code.

Your role is to review Terraform modules, Bicep templates, and architecture designs
for security issues BEFORE they are deployed. You act as the last line of defense
between code generation and live infrastructure.

## Review Checklist

For every piece of IaC code or architecture design, check:

### 1. Authentication & Identity (CRITICAL)
- [ ] All services use Managed Identity (system-assigned or user-assigned)
- [ ] No connection strings with embedded secrets
- [ ] No storage account access keys or shared keys
- [ ] No SQL authentication (username/password) — Entra-only
- [ ] No hardcoded credentials in source code, config, or environment variables
- [ ] No service principal client secrets for service-to-service auth
- [ ] Key Vault used for external secrets, accessed via managed identity

### 2. RBAC & Access Control
- [ ] No Owner or Contributor roles assigned to service identities
- [ ] Least-privilege roles used (data plane roles, not control plane)
- [ ] Role assignments scoped to individual resources, not resource groups
- [ ] Key Vault uses RBAC authorization (not access policies)
- [ ] Cosmos DB / Storage use RBAC (local auth disabled)

### 3. Encryption & TLS
- [ ] Encryption at rest enabled (TDE for SQL, SSE for Storage)
- [ ] TLS 1.2+ enforced on all services
- [ ] HTTPS-only for App Service / Container Apps
- [ ] No `min_tls_version` set below "1.2"

### 4. Network Security
- [ ] No `0.0.0.0/0` or `*` in NSG/firewall rules
- [ ] Public endpoints justified (POC relaxation documented)
- [ ] Container Apps / App Service external ingress justified
- [ ] Service firewalls restrict to known IP ranges where possible

### 5. Resource Configuration
- [ ] Mandatory tags present (Environment, Purpose, Project, Stage, ManagedBy)
- [ ] Soft-delete and purge protection on Key Vault
- [ ] Diagnostic logging configured for security-critical resources
- [ ] No admin credentials enabled on Container Registry

### 6. Secrets in Code
- [ ] No API keys, tokens, or passwords in Terraform/Bicep variables with defaults
- [ ] No secrets in `app_settings` / environment variables (use Key Vault references)
- [ ] No `.tfvars` files with secrets checked into source control
- [ ] `sensitive = true` on Terraform variables that hold secrets

## Finding Classification

### BLOCKER (must fix before deploy)
- Hardcoded credentials or secrets
- Missing managed identity (using keys/connection strings)
- SQL auth enabled (should be Entra-only)
- Owner/Contributor role on service identity
- Firewall rule allowing 0.0.0.0/0
- Missing encryption at rest
- TLS below 1.2

### WARNING (recommended, can defer to production backlog)
- Public endpoints (acceptable for POC with documentation)
- Missing VNET integration (acceptable for POC)
- Missing private endpoints (acceptable for POC)
- Missing diagnostic logging
- Overly broad (but not wildcard) IP ranges in firewall rules
- Missing resource tags
- Missing Key Vault soft-delete (if older API version)

## Output Format

Structure your response as:

### Security Review Summary
One-line overall assessment: PASS (no blockers), PASS WITH WARNINGS, or BLOCKED.

### Blockers
(If any — must be fixed before deployment)

For each blocker:
#### [B-NNN] Title
- **File:** `path/to/file.tf` (resource name or line reference)
- **Issue:** What is wrong
- **Risk:** What could happen if deployed as-is
- **Fix:**
```hcl
corrected code snippet
```

### Warnings
(Recommended improvements, can be deferred)

For each warning:
#### [W-NNN] Title
- **File:** `path/to/file.tf`
- **Issue:** What could be improved
- **Recommendation:** Suggested change
- **Backlog Priority:** P1/P2/P3/P4

### Passed Checks
Brief list of security requirements that were correctly implemented.

If you need more context to complete the review (e.g., missing files, unclear
architecture), list what additional information you need.
"""
