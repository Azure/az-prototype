# Governance Policies

> [!NOTE]
> This reference is part of the **prototype** extension for the Azure CLI. See [COMMANDS.md](COMMANDS.md) for the full command reference.

## Overview

Governance policies are declarative rules that guide how AI agents generate infrastructure and application code. Every agent in the `az prototype` extension is **governance-aware by default** — the `PolicyEngine` loads all applicable `*.policy.yaml` files and the `GovernanceContext` automatically injects compact policy summaries into every agent's system prompt.

Policies serve three purposes:

1. **Guard rails during generation** — agents receive rules as part of their system prompt and follow them when producing Terraform, Bicep, or application code.
2. **Automated compliance checks** — rules with a `template_check` block are evaluated against workload templates at load time, surfacing violations before any code is generated.
3. **Interactive policy resolution** — during the build stage, the `PolicyResolver` checks generated code against policies and presents violations conversationally: accept compliant (default), override with justification, or regenerate.

Policy violations detected during build are recorded in `.prototype/state/build.yaml` with full audit trail (rule ID, resolution, justification if overridden).

## Severity Levels

Each rule carries a severity that maps to a prompt keyword:

| Level | Prompt Keyword | Meaning |
|---|---|---|
| `required` | **MUST** | Agent must follow this rule. A violation is a defect. |
| `recommended` | **SHOULD** | Agent should follow unless there is a justified reason not to. |
| `optional` | **MAY** | Best practice. Agent may skip if not relevant to the current context. |

## Policy Summary

| Policy | Category | Services | Rules |
|---|---|---|---|
| [container-apps](#container-apps) | azure | container-apps, container-registry | CA-001 through CA-004 |
| [key-vault](#key-vault) | azure | key-vault | KV-001 through KV-005 |
| [sql-database](#sql-database) | azure | sql-database | SQL-001 through SQL-005 |
| [cosmos-db](#cosmos-db) | azure | cosmos-db | CDB-001 through CDB-004 |
| [managed-identity](#managed-identity) | security | container-apps, app-service, functions, key-vault, sql-database, cosmos-db, storage | MI-001 through MI-004 |
| [authentication](#authentication) | security | container-apps, app-service, functions, api-management, sql-database, cosmos-db | AUTH-001 through AUTH-003 |
| [data-protection](#data-protection) | security | sql-database, cosmos-db, storage, key-vault | DP-001 through DP-004 |
| [network-isolation](#network-isolation) | security | container-apps, app-service, key-vault, sql-database, cosmos-db, storage | NET-001 through NET-004 |
| [apim-to-container-apps](#apim-to-container-apps) | integration | api-management, container-apps | INT-001 through INT-004 |

---

## Azure Policies

Service-specific rules for Azure PaaS resources. These policies ensure that infrastructure code follows Azure best practices for security, cost, and reliability.

### Container Apps

Applies to: `container-apps`, `container-registry`

| Rule | Severity | Description |
|---|---|---|
| CA-001 | required | Use managed identity for all service-to-service auth |
| CA-002 | required | Deploy Container Apps in a VNET-integrated environment |
| CA-003 | recommended | Use consumption plan for dev/test, dedicated for production |
| CA-004 | recommended | Set min replicas to 0 for non-critical services in dev |

**Patterns:**
- **Container App with Key Vault references** — use Key Vault references for secrets instead of environment variables
- **Health probes** — always configure liveness and readiness probes

**Anti-patterns:**
- Do not store secrets in environment variables or app settings — use Key Vault references with managed identity
- Do not use admin credentials for container registry — use managed identity with AcrPull role assignment

**References:**
- [Container Apps landing zone accelerator](https://learn.microsoft.com/azure/container-apps/landing-zone-accelerator)
- [Container Apps networking](https://learn.microsoft.com/azure/container-apps/networking)

---

### Key Vault

Applies to: `key-vault`

| Rule | Severity | Description |
|---|---|---|
| KV-001 | required | Enable soft-delete and purge protection |
| KV-002 | required | Use RBAC authorization model, not access policies |
| KV-003 | required | Access Key Vault via managed identity, never service principal secrets |
| KV-004 | recommended | Enable diagnostic logging to Log Analytics |
| KV-005 | recommended | Use private endpoints in production environments |

**Patterns:**
- **Key Vault with RBAC** — create Key Vault with `enable_rbac_authorization = true`, soft-delete retention, and purge protection

**Anti-patterns:**
- Do not use access policies for authorization — set `enable_rbac_authorization = true` and use role assignments
- Do not disable soft-delete — keep soft-delete enabled with at least 7-day retention

**References:**
- [Key Vault best practices](https://learn.microsoft.com/azure/key-vault/general/best-practices)

---

### SQL Database

Applies to: `sql-database`

| Rule | Severity | Description |
|---|---|---|
| SQL-001 | required | Use Microsoft Entra authentication, disable SQL auth where possible |
| SQL-002 | required | Enable Transparent Data Encryption (TDE) |
| SQL-003 | required | Enable Advanced Threat Protection |
| SQL-004 | recommended | Use serverless tier for dev/test workloads |
| SQL-005 | recommended | Configure geo-replication for production databases |

**Patterns:**
- **SQL with Entra auth** — configure SQL Server with `azuread_authentication_only = true`

**Anti-patterns:**
- Do not use SQL authentication with username/password — use Microsoft Entra authentication with managed identity
- Do not set firewall rule `0.0.0.0-255.255.255.255` — use private endpoints or specific IP ranges

**References:**
- [SQL Database security best practices](https://learn.microsoft.com/azure/azure-sql/database/security-best-practice)

---

### Cosmos DB

Applies to: `cosmos-db`

| Rule | Severity | Description |
|---|---|---|
| CDB-001 | required | Use Microsoft Entra RBAC for data-plane access |
| CDB-002 | recommended | Configure appropriate consistency level (not Strong unless required) |
| CDB-003 | recommended | Use autoscale throughput for variable workloads |
| CDB-004 | recommended | Design partition keys based on query patterns, not just cardinality |

**Patterns:**
- **Cosmos DB with RBAC** — disable key-based auth with `local_authentication_disabled = true`

**Anti-patterns:**
- Do not use account-level keys for application access — use Microsoft Entra RBAC with managed identity
- Do not use unlimited containers without TTL policy — set TTL on containers with transient data

**References:**
- [Cosmos DB security baseline](https://learn.microsoft.com/azure/cosmos-db/security-baseline)

---

## Security Policies

Cross-cutting security rules that apply across multiple Azure services. These policies enforce credential hygiene, data protection, identity management, and network isolation.

### Managed Identity

Applies to: `container-apps`, `app-service`, `functions`, `key-vault`, `sql-database`, `cosmos-db`, `storage`

| Rule | Severity | Description |
|---|---|---|
| MI-001 | required | Use system-assigned managed identity for single-service resources |
| MI-002 | required | Use user-assigned managed identity when identity is shared across resources |
| MI-003 | required | Never use service principal client secrets for service-to-service auth |
| MI-004 | recommended | Assign least-privilege RBAC roles, never Owner or Contributor at resource group scope |

**Patterns:**
- **System-assigned identity with role** — enable system identity and assign a specific role (e.g., `Key Vault Secrets User`)

**Anti-patterns:**
- Do not store client secrets or certificates in application config — use managed identity; the Azure SDK handles token acquisition automatically

**References:**
- [Managed identities overview](https://learn.microsoft.com/entra/identity/managed-identities-azure-resources/overview)

---

### Authentication

Applies to: `container-apps`, `app-service`, `functions`, `api-management`, `sql-database`, `cosmos-db`

| Rule | Severity | Description |
|---|---|---|
| AUTH-001 | required | Never hardcode credentials, API keys, or secrets in source code, config files, or environment variables |
| AUTH-002 | recommended | Assign least-privilege RBAC roles for all service principals and user accounts |
| AUTH-003 | recommended | Prefer app registrations with scoped permissions over shared API keys for client authentication |

**Patterns:**
- **Managed identity for service-to-service** — use `DefaultAzureCredential()` which works with managed identity in Azure and developer credentials locally
- **Key Vault for external secrets** — store third-party API keys or connection strings in Key Vault

**Anti-patterns:**
- Do not embed API keys or passwords in application source code — use managed identity or Key Vault
- Do not assign Owner or Contributor roles at subscription or resource group scope — use the most specific built-in role at the narrowest scope

**References:**
- [Azure RBAC best practices](https://learn.microsoft.com/azure/role-based-access-control/best-practices)
- [DefaultAzureCredential overview](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential)

---

### Data Protection

Applies to: `sql-database`, `cosmos-db`, `storage`, `key-vault`

| Rule | Severity | Description |
|---|---|---|
| DP-001 | required | Enable encryption at rest for all data services (TDE, SSE, or service-managed keys) |
| DP-002 | required | Enforce TLS 1.2+ for all data-in-transit connections |
| DP-003 | recommended | Store application secrets and connection configuration in Azure Key Vault, not in code or environment variables |
| DP-004 | recommended | Use Azure Key Vault references in App Service and Container Apps configuration instead of plaintext secrets |

**Patterns:**
- **Key Vault reference in Container Apps** — reference a Key Vault secret from a Container App environment variable using `key_vault_secret_id` with system identity

**Anti-patterns:**
- Do not hardcode secrets, API keys, or connection strings in application code or config files — use Key Vault references or managed identity
- Do not disable TDE or encryption at rest on any data service — leave default encryption settings enabled

**References:**
- [Azure encryption at rest overview](https://learn.microsoft.com/azure/security/fundamentals/encryption-atrest)
- [Key Vault references for App Service](https://learn.microsoft.com/azure/app-service/app-service-key-vault-references)

---

### Network Isolation

Applies to: `container-apps`, `app-service`, `key-vault`, `sql-database`, `cosmos-db`, `storage`

| Rule | Severity | Description |
|---|---|---|
| NET-001 | required | Use private endpoints for all PaaS data services in production |
| NET-002 | required | Deploy workloads in a dedicated subnet within the landing zone VNET |
| NET-003 | recommended | Use NSGs to restrict traffic between subnets to only required ports |
| NET-004 | recommended | Enable diagnostic logging on NSGs for traffic auditing |

**Patterns:**
- **Private endpoint for Key Vault** — create private endpoint and disable public access

**Anti-patterns:**
- Do not allow `0.0.0.0/0` in any NSG or firewall rule — use specific IP ranges or service tags
- Do not rely solely on service firewalls without VNET integration — use private endpoints + VNET integration for defense in depth

**References:**
- [Private Link overview](https://learn.microsoft.com/azure/private-link/private-link-overview)

---

## Integration Policies

Rules governing how Azure services communicate with each other. These policies ensure secure, observable, and efficient service-to-service connectivity.

### APIM to Container Apps

Applies to: `api-management`, `container-apps`

| Rule | Severity | Description |
|---|---|---|
| INT-001 | required | Route all external API traffic through API Management |
| INT-002 | required | Use APIM managed identity to authenticate to Container Apps |
| INT-003 | recommended | Set Container App ingress to internal-only when fronted by APIM |
| INT-004 | recommended | Configure APIM caching policies for read-heavy endpoints |

**Patterns:**
- **APIM backend with managed identity** — configure APIM backend pointing to internal Container App FQDN

**Anti-patterns:**
- Do not expose Container App endpoints directly to the internet — use APIM as the gateway; set Container App ingress to internal

**References:**
- [APIM with Container Apps](https://learn.microsoft.com/azure/api-management/integrate-container-app)

---

## Custom Policies

Add `.policy.yaml` files to `.prototype/policies/` in your project directory to extend or override built-in policies. Custom policies use the same schema as built-in files and are loaded automatically by the `PolicyEngine`.

### Policy Schema

```yaml
apiVersion: v1
kind: policy
metadata:
  name: my-service
  category: azure | security | integration | cost | data | general
  services: [service-name-1, service-name-2]
  last_reviewed: "2025-01-01"

rules:
  - id: XX-001
    severity: required | recommended | optional
    description: "What to do"
    rationale: "Why"
    applies_to: [cloud-architect, terraform-agent, bicep-agent, app-developer]

patterns:
  - name: "Pattern name"
    description: "When to use it"
    example: |
      code example here

anti_patterns:
  - description: "What NOT to do"
    instead: "What to do instead"

references:
  - title: "Doc title"
    url: "https://..."
```

### Schema Fields

| Field | Required | Description |
|---|---|---|
| `apiVersion` | no | Schema version. Currently `v1`. |
| `kind` | no | Document kind. Must be `policy`. |
| `metadata.name` | yes | Policy name (e.g., `my-service`). |
| `metadata.category` | yes | One of: `azure`, `security`, `integration`, `cost`, `data`, `general`. |
| `metadata.services` | yes | Azure service types this policy applies to. |
| `metadata.last_reviewed` | no | Date the policy was last reviewed (`YYYY-MM-DD`). |
| `rules` | yes | Array of governance rules. |
| `rules[].id` | yes | Unique rule identifier (e.g., `XX-001`). |
| `rules[].severity` | yes | `required`, `recommended`, or `optional`. |
| `rules[].description` | yes | What the rule requires. |
| `rules[].rationale` | no | Why this rule exists. |
| `rules[].applies_to` | yes | Agent names this rule applies to. |
| `rules[].template_check` | no | Automated compliance check for workload templates. |
| `patterns` | no | Implementation patterns agents should generate. |
| `anti_patterns` | no | Anti-patterns agents must avoid. |
| `references` | no | Documentation references for agents to cite. |

### Template Check Fields

Rules with a `template_check` block are evaluated automatically against workload templates:

| Field | Description |
|---|---|
| `scope` | Service types to check (per-service). Only matching services are evaluated. |
| `require_config` | Config keys that must be truthy on matching services. |
| `require_config_value` | Config key-value pairs that must match exactly. |
| `reject_config_value` | Config key-value pairs that must NOT match. |
| `require_service` | Service types that must exist in the template (template-level). |
| `when_services_present` | Only apply this check when all listed service types are present. |
| `severity` | Override violation severity: `error` (default for required) or `warning`. |
| `error_message` | Templated message. Placeholders: `{service_name}`, `{service_type}`, `{config_key}`, `{rule_id}`. |

## Validation

Policy files are validated automatically at multiple stages:

- **Pre-commit hook** — install with `pre-commit install` or `python scripts/install-hooks.py`
- **CI pipeline** — runs `python -m azext_prototype.policies.validate --strict` on every push
- **Release pipeline** — validates before building the wheel
- **Manual** — `python -m azext_prototype.policies.validate --dir azext_prototype/policies/ --strict`
