"""Shared IaC rules injected into both Terraform and Bicep agent prompts.

These rules are tool-agnostic ARM/Azure constraints that apply equally
to Terraform (azapi) and Bicep code generation.  Tool-specific rules
(file layout, cross-stage patterns, provider config) remain in each
agent's own prompt.
"""

SHARED_IAC_RULES = """
## CRITICAL: NETWORKING STAGE RULES
When generating a networking stage (VNet, subnets, DNS zones):
- Do **NOT** create placeholder private endpoints. PEs belong in their respective
  service stages (e.g., Key Vault PE in the Key Vault stage), not the networking
  stage. The networking stage **ONLY** exports PE subnet ID and DNS zone IDs
  for downstream stages to consume.
- NSGs do **NOT** support diagnostic settings at all (no log categories, no metric
  categories). Do **NOT** create `Microsoft.Insights/diagnosticSettings` for NSG
  resources — ARM will reject with HTTP 400.
- VNet diagnostic settings support **ONLY** `AllMetrics` (category), **NOT**
  `allLogs` (categoryGroup). Use metrics with category = "AllMetrics" only.
- Private DNS zone names **MUST** be exact Azure FQDNs from Microsoft documentation
  (e.g., `privatelink.vaultcore.azure.net`, `privatelink.database.windows.net`).
  Do **NOT** use computed naming convention patterns for DNS zone names.
  If the task prompt provides DNS zone names, use them exactly as given.

## CRITICAL: EXTENSION RESOURCES
`Microsoft.Insights/diagnosticSettings`, `Microsoft.Authorization/roleAssignments`,
and `Microsoft.Authorization/locks` are ARM extension resources:
- They do **NOT** support the `tags` property. **NEVER** add tags to these resources.
  ARM will reject the deployment with HTTP 400 `InvalidRequestContent`.
- Diagnostic settings **MUST** use API version `@2021-05-01-preview` (required for
  `categoryGroup` support). Do **NOT** use `@2016-09-01` — it does not support
  `categoryGroup = "allLogs"`.
- Role assignments **MUST** use API version `@2022-04-01`.

## CRITICAL: ARM PROPERTY PLACEMENT
- `disableLocalAuth` is a **top-level** property under `properties`, **NOT** inside
  `properties.features`. The ARM API silently drops it if nested inside `features`.
  CORRECT: `properties = { disableLocalAuth = true, features = { ... } }`
  WRONG: `properties = { features = { disableLocalAuth = true } }`

## CRITICAL: SUBNET RESOURCES — PREVENT DRIFT
When creating a VNet with subnets, **NEVER** define subnets inline in the VNet body.
Always create subnets as separate child resources.

## MANAGED IDENTITY + RBAC (MANDATORY)
When **ANY** service disables local/key auth, you **MUST** also:
1. Create a user-assigned managed identity
2. Create RBAC role assignments granting the identity access
3. Output the identity's clientId and principalId

## DIAGNOSTIC SETTINGS (MANDATORY)
Every PaaS data service **MUST** have a diagnostic settings resource using `allLogs`
category group and `AllMetrics`. NSGs and VNets are exceptions (see Networking rules).
- Diagnostic settings on blob storage **MUST** target an explicit blob service child
  resource (`Microsoft.Storage/storageAccounts/blobServices`), **NOT** string
  interpolation like `"${storage.id}/blobServices/default"`.
- When using diagnostic settings API `@2021-05-01-preview`, include `retentionPolicy`
  in each log/metric category block: `retentionPolicy = { enabled = false, days = 0 }`.

## CRITICAL: CROSS-STAGE DEPENDENCIES — NO DEAD CODE
- **ONLY** declare `terraform_remote_state` or parameter inputs for stages whose
  outputs you _actually reference_ in resource definitions or locals.
- Do **NOT** declare remote state data sources "for completeness" or "in case needed."
  Terraform validates state files at plan time — an unreferenced data source pointing
  to a nonexistent state file causes plan failure.
- Every `data.terraform_remote_state` block **MUST** have at least one output
  referenced in `locals.tf` or `main.tf`. If it doesn't, _remove it_.

## CRITICAL: RBAC ROLE ASSIGNMENTS — UNCONDITIONAL FOR KNOWN IDENTITIES
- RBAC assignments for the _worker managed identity_ (from Stage 1) **MUST** be
  unconditional (no `count`). The worker identity exists before any service stage runs.
- RBAC assignments for identities created in _later stages_ (e.g., Container App
  system identity) may use `count` conditional on a variable, but document that the
  role must be applied _after_ the identity stage deploys.

## CRITICAL: deploy.sh STATE DIRECTORY
deploy.sh **MUST** create the Terraform state directory before `terraform init`:
```bash
STATE_DIR="$(cd "$(dirname "$0")/../../.." && pwd)/.terraform-state"
mkdir -p "${STATE_DIR}"
```
Without this, `terraform init` fails on first run in a clean environment.
""".strip()
