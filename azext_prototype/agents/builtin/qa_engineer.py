"""QA Engineer built-in agent — error analysis and fix application.

Supports multiple input types:
  - Log files (text)
  - Error strings (inline)
  - Screenshots / images (base64-encoded vision input)

The agent analyzes the issue, identifies the root cause, determines which
agent should apply the fix, and instructs the user which CLI commands to
run to redeploy the fix.
"""

import base64
import logging
import mimetypes
from typing import Any

from azext_prototype.agents.base import (
    AgentCapability,
    AgentContext,
    AgentContract,
    BaseAgent,
)
from azext_prototype.ai.provider import AIMessage, AIResponse

logger = logging.getLogger(__name__)


class QAEngineerAgent(BaseAgent):
    """Analyze errors, apply fixes, and guide redeployment."""

    _temperature = 0.2
    _max_tokens = 8192
    _enable_web_search = True
    _include_templates = False
    _include_standards = False
    _keywords = [
        "error",
        "bug",
        "fail",
        "exception",
        "crash",
        "log",
        "trace",
        "debug",
        "fix",
        "issue",
        "broken",
        "screenshot",
        "analyze",
        "diagnose",
        "troubleshoot",
    ]
    _keyword_weight = 0.12
    _contract = AgentContract(
        inputs=[],
        outputs=["qa_diagnosis"],
        delegates_to=["terraform-agent", "bicep-agent", "app-developer", "cloud-architect"],
    )

    def __init__(self):
        super().__init__(
            name="qa-engineer",
            description=(
                "Analyze errors from logs, screenshots, or inline messages; " "coordinate fixes and guide redeployment"
            ),
            capabilities=[AgentCapability.QA, AgentCapability.ANALYZE],
            constraints=[
                "Always cite the specific error or log line that triggered the analysis",
                "Clearly identify the root cause before proposing a fix",
                "State which agent should apply the fix (e.g., terraform, app-developer)",
                "Provide exact 'az prototype ...' commands the user should run afterwards",
                "If the fix requires a code change, output the corrected file contents",
            ],
            system_prompt=QA_ENGINEER_PROMPT,
        )

    def get_system_messages(self):
        messages = super().get_system_messages()
        from azext_prototype.requirements import get_dependency_version

        api_ver = get_dependency_version("azure_api")
        if api_ver:
            messages.append(
                AIMessage(
                    role="system",
                    content=(
                        f"AZURE API VERSION: {api_ver}\n\n"
                        f"When reviewing Terraform code, all resources should be `azapi_resource` "
                        f"with type property using @{api_ver}.\n"
                        f"When reviewing Bicep code, all resource declarations should use @{api_ver}.\n\n"
                        f"Reference docs URL pattern:\n"
                        f"  https://learn.microsoft.com/en-us/azure/templates/"
                        f"<resource_provider>/{api_ver}/<resource_type>"
                        f"?pivots=deployment-language-<lang>\n"
                        f"where <lang> is 'terraform' or 'bicep'.\n\n"
                        f"If you need to verify a property exists, emit:\n"
                        f"  [SEARCH: azure arm template <resource_type> {api_ver} properties]"
                    ),
                )
            )
        return messages

    def execute_with_image(
        self,
        context: AgentContext,
        task: str,
        image_path: str,
    ) -> AIResponse:
        """Execute analysis with an image (screenshot) input.

        Constructs a multi-modal message using the OpenAI vision API
        format (content array with text + image_url).
        """
        messages = self.get_system_messages()
        messages.extend(context.conversation_history)

        # Build multi-modal content
        image_data = self._encode_image(image_path)
        mime = mimetypes.guess_type(image_path)[0] or "image/png"

        user_content = [
            {"type": "text", "text": task},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{image_data}",
                    "detail": "high",
                },
            },
        ]

        # We need to pass raw dict messages for vision support
        raw_messages: list[dict[str, Any]] = [{"role": m.role, "content": m.content} for m in messages]
        raw_messages.append({"role": "user", "content": user_content})

        # Use the provider's client directly for multi-modal
        try:
            client = getattr(context.ai_provider, "_client", None)
            if client is None:
                raise ValueError("AI provider does not expose a chat client for vision requests")
            response = client.chat.completions.create(
                model=context.ai_provider.default_model,
                messages=raw_messages,
                temperature=0.2,
                max_tokens=8192,
            )
            choice = response.choices[0]
            usage = {}
            if response.usage:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
            result = AIResponse(
                content=choice.message.content or "",
                model=response.model,
                usage=usage,
                finish_reason=choice.finish_reason or "stop",
            )
            # Post-response governance check
            warnings = self.validate_response(result.content)
            if warnings:
                for w in warnings:
                    logger.warning("Governance: %s", w)
                block = "\n\n---\n⚠ **Governance warnings:**\n" + "\n".join(f"- {w}" for w in warnings)
                result = AIResponse(
                    content=result.content + block,
                    model=result.model,
                    usage=result.usage,
                    finish_reason=result.finish_reason,
                )
            return result
        except Exception as e:
            logger.warning("Vision-based analysis failed, falling back to text: %s", e)
            messages.append(
                AIMessage(
                    role="user",
                    content=f"{task}\n\n[Image could not be processed: {image_path}]",
                )
            )
            return context.ai_provider.chat(messages, temperature=0.2, max_tokens=8192)

    @staticmethod
    def _encode_image(path: str) -> str:
        """Read and base64-encode an image file."""
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")


QA_ENGINEER_PROMPT = """You are an expert QA engineer and diagnostics specialist for Azure prototypes.

Your job is to analyze errors — whether they come from log files, inline error
messages, terminal output, or screenshots — identify the root cause, and produce
a concrete fix.

## Analysis Process

1. **Parse the input** — Extract the relevant error messages, stack traces, HTTP
   status codes, or resource identifiers from the provided input.
2. **Root-cause analysis** — Determine exactly what went wrong and why. Consider:
   - Terraform / Bicep deployment failures (missing providers, quota, naming)
   - Application runtime errors (auth, config, missing env vars)
   - Networking issues (NSGs, private endpoints, DNS)
   - Identity / RBAC issues (missing role assignments)
3. **Propose a fix** — Produce the exact code or configuration change needed.
   When outputting file changes, use the same ```filename.ext format so the
   build stage can ingest them.
4. **Agent delegation** — Identify which agent should apply the fix:
   - `terraform` / `bicep` for infrastructure issues
   - `app-developer` for application code issues
   - `cloud-architect` for architectural changes
5. **Redeployment guidance** — Tell the user exactly what commands to run:
   - `az prototype build --scope infra` then `az prototype deploy --scope infra`
   - Or `az prototype build --scope apps` then `az prototype deploy --scope apps`

## Mandatory Review Checklist (for code reviews)

When reviewing generated code (not diagnosing a runtime error), you MUST check
every item below. Report ONLY items that FAIL — do not list passed checks.
Classify each failure as CRITICAL (must fix before deploy) or WARNING (should fix).

### 1. Authentication & Identity Completeness
- [ ] Every service that disables local/key auth (local_authentication_disabled,
      shared_access_key_enabled=false, disableLocalAuth) MUST have a companion
      managed identity AND RBAC role assignment in the SAME stage
- [ ] No sensitive outputs (primary_key, connection_string, account_key) — if
      local auth is disabled, these outputs must NOT exist
- [ ] Managed identity client_id and principal_id are in outputs.tf

### 2. Cross-Stage References
- [ ] Stages that depend on prior stages use terraform_remote_state (Terraform)
      or parameter inputs (Bicep) — NOT hardcoded resource names
- [ ] Every resource name referenced from another stage is available as an output
      from that stage
- [ ] Backend configuration is consistent across all stages (same storage account,
      container, different keys)

### 3. Script Completeness
- [ ] deploy.sh is syntactically complete — no truncated strings, unclosed quotes,
      or missing closing braces
- [ ] deploy.sh includes error handling (set -euo pipefail, trap)
- [ ] deploy.sh exports outputs to JSON file for downstream stages
- [ ] deploy.sh includes Azure login verification

### 4. Output Completeness
- [ ] outputs.tf exports resource group name(s)
- [ ] outputs.tf exports all resource IDs/names referenced by downstream stages
- [ ] outputs.tf exports all endpoints needed by applications
- [ ] outputs.tf exports managed identity IDs

### 5. Structural Consistency
- [ ] All stages have consistent backend configuration (or none, consistently)
- [ ] Resource names match the naming convention (no invented names)
- [ ] Tags are consistent across all stages
- [ ] No duplicate resource definitions across stages

### 6. Code Completeness
- [ ] All referenced files exist (no imports of non-existent modules)
- [ ] All referenced variables are defined in variables.tf
- [ ] All referenced locals are defined in locals.tf
- [ ] Application code includes all referenced classes/models/DTOs

### 7. Terraform File Structure
- [ ] Every stage has exactly ONE file containing the terraform {} block (providers.tf, NOT versions.tf)
- [ ] No .tf file is trivially empty or contains only closing braces
- [ ] main.tf does NOT contain terraform {} or provider {} blocks
- [ ] All .tf files are syntactically valid HCL (properly opened/closed blocks)

## Output Format

Always structure your response as:

### Error Summary
One-line description.

### Root Cause
Detailed explanation.

### Fix
```filename.ext
corrected file contents
```

### Agent Responsible
Name of the agent that should apply this fix.

### Redeployment Steps
1. `az prototype build --scope <scope>`
2. `az prototype deploy --scope <scope> [--stage N]`

If the error is ambiguous or more context is needed, ask specific follow-up
questions and list what additional information would help.

When you need current Azure documentation or are uncertain about a service API,
SDK version, or configuration option, emit [SEARCH: your query] in your response.
The framework will fetch relevant Microsoft Learn documentation and re-invoke you
with the results. Use at most 2 search markers per response. Only search when your
built-in knowledge is insufficient.
"""
