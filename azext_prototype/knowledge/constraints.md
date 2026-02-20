# Mandatory Constraints for All Agents

This is the **single source of truth** for constraints that every agent must follow when generating architecture designs, infrastructure code, application code, deployment plans, and cost estimates.

Individual agent system prompts reference this document. If a constraint here conflicts with an inline agent constraint, **this document wins**.

---

## 1. Authentication Requirements

### 1.1 Managed Identity (MANDATORY)

All Azure services **must** use Managed Identity for service-to-service authentication.

| Pattern | When to Use |
|---------|------------|
| System-assigned managed identity | Single-service resources with a 1:1 lifecycle |
| User-assigned managed identity | Identity shared across multiple resources, or when the identity must survive resource recreation |

Use the appropriate Azure Identity library:

| Language | Library | Credential |
|----------|---------|------------|
| Python | `azure-identity` | `DefaultAzureCredential` or `ManagedIdentityCredential` |
| Node.js | `@azure/identity` | `DefaultAzureCredential` or `ManagedIdentityCredential` |
| .NET | `Azure.Identity` | `DefaultAzureCredential` or `ManagedIdentityCredential` |
| Java | `azure-identity` | `DefaultAzureCredentialBuilder` |

The authentication flow is always:

```
Service --> Managed Identity --> RBAC Role Assignment --> Target Azure Resource
```

### 1.2 Prohibited Authentication Patterns

The following are **never** acceptable in generated code or configuration:

- Connection strings with embedded secrets or keys
- Storage account access keys or shared keys
- SQL authentication (username/password) -- use Microsoft Entra authentication only
- Hardcoded credentials in source code, config files, or environment variables
- Service principal client secrets for service-to-service auth
- SAS tokens where Managed Identity is supported
- Container registry admin credentials -- use `AcrPull` role assignment instead

### 1.3 External Secrets

When a service requires credentials that cannot use managed identity (third-party APIs, external SaaS), store them in **Azure Key Vault** and access them via managed identity:

```
Application --> Managed Identity --> Key Vault --> Secret Value
```

Use Key Vault references in App Service and Container Apps configuration instead of plaintext environment variables.

---

## 2. Network Requirements

### 2.1 Private Endpoints

All data and backend services should use private endpoints to eliminate public internet exposure for the data plane.

#### POC Relaxation

For POC/prototype environments, private endpoints are **recommended but not mandatory**. Public endpoints are acceptable for rapid prototyping to reduce complexity and setup time. When public endpoints are used:

- Flag private endpoint configuration as a **production backlog item**
- Document which services are publicly exposed
- Ensure service firewalls restrict access to known IP ranges where possible
- Never set firewall rules to `0.0.0.0/0` or `0.0.0.0-255.255.255.255`

For production readiness, all services in the private endpoint reference table below must use private endpoints.

### 2.2 VNET Integration

When the architecture includes Container Apps, App Service, or Functions:

- Deploy workloads in a dedicated subnet within a VNET
- Use NSGs to restrict traffic between subnets to only required ports
- Enable diagnostic logging on NSGs for traffic auditing

#### POC Relaxation

For POCs, VNET integration is **recommended but not mandatory**. If omitted, document it as a production backlog item. Container Apps Environment without VNET integration is acceptable for prototyping.

### 2.3 Connectivity Pattern (Production Target)

```
Source Service --> Managed Identity --> Private Endpoint --> Target Service
```

### 2.4 Public Ingress Exceptions

The following resources legitimately require public IP addresses or public ingress:

| Resource | Reason |
|----------|--------|
| Application Gateway / Front Door | External user traffic ingress |
| API Management (External mode) | Public API gateway |
| Azure Firewall | Network egress control |
| Bastion Host | Secure remote access |
| Load Balancer (public) | External traffic distribution |
| Container Apps (external ingress) | When no APIM gateway is present (POC only) |

All other services should be internal-only.

---

## 3. Security Requirements

### 3.1 Encryption

| Requirement | Severity | Details |
|-------------|----------|---------|
| Encryption at rest | **Required** | TDE for SQL, SSE for Storage, service-managed keys at minimum. Never disable default encryption. |
| Encryption in transit | **Required** | TLS 1.2+ for all connections. Disable TLS 1.0 and 1.1. |
| Customer-managed keys | Recommended | Only when regulatory or compliance requirements demand it; not required for POCs. |

### 3.2 Access Control

- Use **RBAC authorization** over access policies (Key Vault, Cosmos DB, Storage)
- Assign **least-privilege** roles -- never use Owner or Contributor at resource group scope for service identities
- Use the narrowest scope possible for role assignments

### 3.3 Key Vault Configuration

When the architecture includes Azure Key Vault:

- Enable soft-delete and purge protection
- Use RBAC authorization model (not access policies)
- Access via managed identity only
- Enable diagnostic logging to Log Analytics

### 3.4 Database Security

| Database | Requirements |
|----------|-------------|
| Azure SQL | Entra-only authentication (disable SQL auth), TDE enabled, Advanced Threat Protection enabled |
| Cosmos DB | Entra RBAC for data-plane access, disable local/key-based authentication |

---

## 4. Tagging Requirements (MANDATORY)

All Azure resources must include these tags:

| Tag | Purpose | Example Values |
|-----|---------|---------------|
| `Environment` | Deployment environment | `dev`, `test`, `staging`, `prod` |
| `Purpose` | Why this resource exists | `prototype`, `poc`, `demo` |
| `Project` | Project identifier | From `prototype.yaml` project name |
| `Stage` | Deployment stage that created it | `foundation`, `data`, `compute`, `apps` |
| `ManagedBy` | Tooling that manages the resource | `az-prototype`, `terraform`, `bicep` |

### Implementation

- **Terraform**: Use `default_tags` in the provider block for common tags, plus resource-specific tags
- **Bicep**: Define a `tags` parameter with default values and apply to every resource

---

## 5. Naming Conventions

- Follow the project's configured naming strategy **exactly** -- do not invent names
- If no naming strategy is configured, use Microsoft Cloud Adoption Framework conventions
- Resource names must include: resource type prefix, project identifier, environment suffix, and region code where applicable
- See the `naming/` module for available strategies: `microsoft-alz`, `microsoft-caf`, `simple`, `enterprise`, `custom`

---

## 6. RBAC Role Reference

### 6.1 Common Data Plane Roles

| Service | Role | Purpose |
|---------|------|---------|
| Key Vault | `Key Vault Secrets User` | Read secrets |
| Key Vault | `Key Vault Secrets Officer` | Read/write secrets |
| Key Vault | `Key Vault Crypto User` | Use keys for crypto operations |
| Key Vault | `Key Vault Certificates Officer` | Manage certificates |
| Storage (Blob) | `Storage Blob Data Reader` | Read blobs |
| Storage (Blob) | `Storage Blob Data Contributor` | Read/write blobs |
| Storage (Queue) | `Storage Queue Data Contributor` | Read/write queue messages |
| Storage (Table) | `Storage Table Data Contributor` | Read/write table entities |
| Cosmos DB | `Cosmos DB Built-in Data Reader` | Read data |
| Cosmos DB | `Cosmos DB Built-in Data Contributor` | Read/write data |
| Azure SQL | `db_datareader` / `db_datawriter` | Database-level read/write (via Entra group) |
| Service Bus | `Azure Service Bus Data Sender` | Send messages |
| Service Bus | `Azure Service Bus Data Receiver` | Receive messages |
| Event Hubs | `Azure Event Hubs Data Sender` | Send events |
| Event Hubs | `Azure Event Hubs Data Receiver` | Receive events |
| Container Registry | `AcrPull` | Pull container images |
| Container Registry | `AcrPush` | Push container images |
| AI Services | `Cognitive Services OpenAI User` | Call OpenAI endpoints |
| AI Services | `Cognitive Services User` | Call cognitive service endpoints |
| Search | `Search Index Data Reader` | Read search index |
| Search | `Search Index Data Contributor` | Read/write search index |

### 6.2 Common Control Plane Roles

| Role | Purpose | Typical Assignee |
|------|---------|-----------------|
| `Reader` | Read-only access to resources | Monitoring identities |
| `Monitoring Reader` | Read monitoring data | Dashboard / alerting |
| `Log Analytics Reader` | Query Log Analytics workspaces | Diagnostic tools |
| `Website Contributor` | Manage App Service / Function Apps | Deployment pipelines |
| `Azure Kubernetes Service Cluster User Role` | List cluster credentials | CI/CD pipelines |

### 6.3 Assignment Principles

- Assign at the **narrowest scope** (individual resource > resource group > subscription)
- Never assign `Owner` or `Contributor` to service identities
- Prefer built-in roles over custom role definitions
- Document every role assignment with its justification

---

## 7. Private Endpoint Reference

| Service | Private Link Sub-resource | Private DNS Zone |
|---------|--------------------------|-----------------|
| Key Vault | `vault` | `privatelink.vaultcore.azure.net` |
| Azure SQL | `sqlServer` | `privatelink.database.windows.net` |
| Cosmos DB (SQL API) | `Sql` | `privatelink.documents.azure.com` |
| Cosmos DB (MongoDB) | `MongoDB` | `privatelink.mongo.cosmos.azure.com` |
| Storage (Blob) | `blob` | `privatelink.blob.core.windows.net` |
| Storage (Table) | `table` | `privatelink.table.core.windows.net` |
| Storage (Queue) | `queue` | `privatelink.queue.core.windows.net` |
| Storage (File) | `file` | `privatelink.file.core.windows.net` |
| Container Registry | `registry` | `privatelink.azurecr.io` |
| App Service | `sites` | `privatelink.azurewebsites.net` |
| Functions | `sites` | `privatelink.azurewebsites.net` |
| Event Hubs | `namespace` | `privatelink.servicebus.windows.net` |
| Service Bus | `namespace` | `privatelink.servicebus.windows.net` |
| AI Services | `account` | `privatelink.cognitiveservices.azure.com` |
| Azure OpenAI | `account` | `privatelink.openai.azure.com` |
| Search | `searchService` | `privatelink.search.windows.net` |
| Redis Cache | `redisCache` | `privatelink.redis.cache.windows.net` |
| PostgreSQL Flexible | `postgresqlServer` | `privatelink.postgres.database.azure.com` |
| MySQL Flexible | `mysqlServer` | `privatelink.mysql.database.azure.com` |
| SignalR | `signalr` | `privatelink.service.signalr.net` |

---

## 8. Configuration Storage Rules

### Allowed in `prototype.yaml`

- Project name, location, environment
- IaC tool selection (terraform/bicep)
- AI provider configuration (provider name, model, endpoint URL)
- Naming strategy
- Stage state and history
- Template selections

### Must go in `prototype.secrets.yaml` (git-ignored)

- API keys (AI provider keys, external service keys)
- Azure subscription IDs
- Tenant IDs
- Service principal credentials (for deployment only, not for service-to-service)
- Backlog integration tokens (GitHub PAT, Azure DevOps PAT)
- Any value whose prefix matches: `ai.azure_openai.api_key`, `deploy.subscription`, `backlog.token`

### Never stored anywhere in config

- Azure resource access keys
- Database passwords
- Storage account keys
- Connection strings with embedded credentials

---

## 9. POC vs Production

This section clearly delineates what is acceptable in a POC versus what must be addressed before production. Items in the "Production Required" column feed directly into backlog generation via `az prototype generate`.

| Area | POC Acceptable | Production Required |
|------|---------------|-------------------|
| **Authentication** | Managed identity (same as production) | Managed identity (no relaxation) |
| **Network isolation** | Public endpoints with service firewalls | Private endpoints for all data services |
| **VNET integration** | Optional; Container Apps external ingress OK | Mandatory; VNET-integrated environments with NSGs |
| **Private DNS zones** | Not required | Required for all private endpoints |
| **SKUs** | Free / dev-test / consumption tiers | Production-appropriate SKUs with SLAs |
| **Redundancy** | Locally redundant (LRS), single region | Zone-redundant or geo-redundant as needed |
| **Backup** | Default backup policies | Custom retention policies, tested restore procedures |
| **Monitoring** | Basic App Insights + Log Analytics | Full observability stack, alerting, dashboards |
| **Scaling** | Fixed or minimal replica counts | Autoscale rules, load testing validation |
| **TLS certificates** | Azure-managed / self-signed for internal | CA-signed certificates, automated rotation |
| **WAF** | Not required | Application Gateway / Front Door with WAF policies |
| **DDoS protection** | Not required | Azure DDoS Protection Standard |
| **Compliance** | Tagging only | Full Azure Policy assignments, regulatory compliance |
| **Secrets rotation** | Manual / static secrets in Key Vault | Automated rotation via Key Vault rotation policies |
| **Disaster recovery** | Not required | Documented and tested DR procedures |
| **Cost governance** | Awareness via cost-analyst estimates | Budgets, alerts, and reserved instance commitments |
| **Documentation** | Architecture doc + README from prototype | Full operational runbooks, architecture decision records |

### What POCs Must Still Enforce

Even in a prototype, these constraints are **non-negotiable**:

1. **Managed identity** for all service-to-service authentication
2. **No hardcoded secrets** in code, config, or environment variables
3. **Encryption at rest** enabled (default settings are sufficient)
4. **TLS 1.2+** for all connections
5. **RBAC** over access policies (Key Vault, Cosmos DB)
6. **Entra-only auth** for databases (no SQL auth)
7. **Resource tagging** on all resources
8. **Naming conventions** followed consistently

### Production Backlog Items

When a POC takes an acceptable shortcut, agents must:

1. Document the shortcut in the architecture design output
2. Add a corresponding item to the production backlog (via `az prototype generate`)
3. Classify the backlog item with the appropriate priority:
   - **P1 (Security)**: Network isolation, WAF, DDoS -- must be addressed before any production traffic
   - **P2 (Reliability)**: Redundancy, backup, DR -- must be addressed before production launch
   - **P3 (Operations)**: Monitoring, scaling, rotation -- should be addressed within first sprint post-launch
   - **P4 (Governance)**: Compliance policies, cost governance -- plan within first quarter

---

## 10. Direct Execution Policy

This extension **executes deployment commands directly** (`terraform apply`, `az deployment group create`, etc.) rather than generating commands for a human to run.

### Safeguards

- The `--dry-run` flag provides what-if/plan output without executing any changes
- Preflight checks validate prerequisites before any deployment runs:
  - Azure CLI login status and subscription selection
  - Required resource provider registrations
  - Quota availability for requested SKUs
  - Naming collision detection
- All deployments proceed in dependency-ordered stages
- Failed stages halt the pipeline (no auto-continue to dependent stages)
- Rollback is available for deployed stages, enforced in reverse dependency order

### Agent Responsibilities

- **terraform-agent** / **bicep-agent**: Generate the IaC code; the deploy stage handles execution
- **qa-engineer**: Receives all deployment failures for root-cause analysis before any retry
- **cloud-architect**: Consulted for architectural changes required by deployment failures
- **project-manager**: Consulted for scope changes when deployment blockers arise
