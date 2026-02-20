# [PROJECT_NAME] - Configuration Guide

## Instructions for Claude Code

This template documents the complete configuration for all Azure services in the solution. Populate each section by analyzing:
- Infrastructure code in `concept/infra/terraform/` and `concept/infra/bicep/`
- Application code in `concept/apps/`
- The `concept/AZURE_CONFIG.json` for deployed resources
- Environment variables and app settings

Replace all `[PLACEHOLDER]` values with actual information. Remove sections for services not used in this engagement. Include all configuration values needed to recreate the environment.

**Key Principles:**
- Document ALL configuration, not just non-default values
- **No connection strings or access keys** — use Managed Identity patterns only
- Include environment variables and app settings (patterns, not actual secrets)
- Explain the purpose of each configuration
- Note dependencies between configurations

---

## Table of Contents

1. [Configuration Overview](#1-configuration-overview)
2. [AZURE_CONFIG.json Structure](#2-azure_configjson-structure)
3. [Environment Variables](#3-environment-variables)
4. [Azure Service Configurations](#4-azure-service-configurations)
5. [Application Configurations](#5-application-configurations)
6. [Security Configuration](#6-security-configuration)
7. [Networking Configuration](#7-networking-configuration)
8. [Monitoring Configuration](#8-monitoring-configuration)
9. [Configuration by Environment](#9-configuration-by-environment)

---

## 1. Configuration Overview

### Configuration Sources

| Source | Location | Purpose |
|--------|----------|---------|
| AZURE_CONFIG.json | `concept/AZURE_CONFIG.json` | Central configuration for all Azure resources (owned by `cloud-architect`) |
| Terraform Variables | `concept/infra/terraform/*.tfvars` | IaC deployment parameters |
| Bicep Parameters | `concept/infra/bicep/*.bicepparam` | IaC deployment parameters |
| Deployment Script | `concept/infra/deploy.sh` | Orchestrates staged deployments |
| Key Vault | `kv-{uid}-{purpose}` | Secrets and sensitive configuration |

### Configuration Hierarchy

```
Environment Variables (highest priority)
    ↓
App Settings / Configuration Files
    ↓
Key Vault References (via Managed Identity)
    ↓
AZURE_CONFIG.json Values
    ↓
Default Values (lowest priority)
```

### Required Tags

All resources must include the following tags:

| Tag | Description | Example |
|-----|-------------|---------|
| `Environment` | Deployment environment | `Dev`, `Stg`, `Prd` |
| `Stage` | Deployment stage | `Stage 1`, `Stage 2` |
| `Purpose` | Resource purpose | `Foundation`, `Data`, `Compute` |

---

## 2. AZURE_CONFIG.json Structure

The `concept/AZURE_CONFIG.json` file is the central configuration maintained by the deployment script and `cloud-architect`. All agents reference this file for resource details.

### Schema Overview

```json
{
  "project": {
    "name": "[PROJECT_NAME]",
    "customer": "[CUSTOMER_NAME]",
    "environment": "[dev|stg|prd]",
    "createdDate": "[YYYY-MM-DD]",
    "lastModified": "[YYYY-MM-DD]"
  },
  "subscription": {
    "id": "[SUBSCRIPTION_ID]",
    "name": "[SUBSCRIPTION_NAME]",
    "tenantId": "[TENANT_ID]",
    "resourceProviders": [
      "[PROVIDER_1]",
      "[PROVIDER_2]"
    ]
  },
  "tags": {
    "required": ["Environment", "Stage", "Purpose"],
    "optional": []
  },
  "stages": {
    "stage1": { ... },
    "stage2": { ... }
  }
}
```

### Stage Structure

Each stage in `concept/AZURE_CONFIG.json` follows this pattern:

```json
{
  "stages": {
    "stage[N]": {
      "name": "[STAGE_NAME]",
      "description": "[DESCRIPTION]",
      "resourceGroups": {
        "group1": {
          "name": "rg-{uid}-[purpose]",
          "location": "[LOCATION]",
          "tags": {
            "Environment": "[Dev|Stg|Prd]",
            "Stage": "Stage [N]",
            "Purpose": "[PURPOSE]"
          }
        }
      },
      "managedIdentities": {
        "[identityName]": {
          "name": "[IDENTITY_NAME]",
          "id": "[RESOURCE_ID]",
          "clientId": "[CLIENT_ID]",
          "principalId": "[PRINCIPAL_ID]"
        }
      },
      "resources": {
        "[resourceType]": {
          "name": "[RESOURCE_NAME]",
          "id": "[RESOURCE_ID]",
          "resourceGroup": "[RG_NAME]"
        }
      }
    }
  }
}
```

### Querying Configuration

Use `jq` to query values from `concept/AZURE_CONFIG.json`:

```bash
# Get resource group name for stage 1
jq -r '.stages.stage1.resourceGroups.group1.name' concept/AZURE_CONFIG.json

# Get Key Vault name
jq -r '.stages.stage1.resources.keyVault.name' concept/AZURE_CONFIG.json

# Get managed identity principal ID
jq -r '.stages.stage1.managedIdentities.[IDENTITY].principalId' concept/AZURE_CONFIG.json

# Get all resource names in a stage
jq -r '.stages.stage2.resources | keys[]' concept/AZURE_CONFIG.json
```

---

## 3. Environment Variables

### Required Environment Variables

| Variable | Description | Example | Required By |
|----------|-------------|---------|-------------|
| `[VAR_1]` | [DESCRIPTION] | `[EXAMPLE]` | [SERVICE] |
| `[VAR_2]` | [DESCRIPTION] | `[EXAMPLE]` | [SERVICE] |
| `[VAR_3]` | [DESCRIPTION] | `[EXAMPLE]` | [SERVICE] |
| `[VAR_4]` | [DESCRIPTION] | `[EXAMPLE]` | [SERVICE] |

### Optional Environment Variables

| Variable | Description | Default | Used By |
|----------|-------------|---------|---------|
| `[VAR_1]` | [DESCRIPTION] | `[DEFAULT]` | [SERVICE] |
| `[VAR_2]` | [DESCRIPTION] | `[DEFAULT]` | [SERVICE] |
| `[VAR_3]` | [DESCRIPTION] | `[DEFAULT]` | [SERVICE] |

### Environment Variable Patterns

**IMPORTANT:** No connection strings or access keys. Use Managed Identity endpoints only.

```bash
# [SERVICE_1] Configuration (Managed Identity)
[PREFIX]__blobServiceUri=https://{storage-account}.blob.core.windows.net
[PREFIX]__queueServiceUri=https://{storage-account}.queue.core.windows.net
[PREFIX]__credential=managedidentity

# [SERVICE_2] Configuration (Managed Identity)
[PREFIX]_ENDPOINT=https://{resource}.{service}.azure.com
[PREFIX]_DATABASE=[DATABASE_NAME]

# [SERVICE_3] Configuration
[PREFIX]_[SETTING_1]=[VALUE]
[PREFIX]_[SETTING_2]=[VALUE]
```

---

## 4. Azure Service Configurations

### [SERVICE_1]: [SERVICE_NAME]

**Resource Details (from AZURE_CONFIG.json):**
```bash
# Query from concept/AZURE_CONFIG.json
jq -r '.stages.[STAGE].resources.[RESOURCE_KEY]' concept/AZURE_CONFIG.json
```

- Resource Name Pattern: `[PREFIX]-{uid}-[PURPOSE]`
- SKU: `[SKU]`
- Location: `[LOCATION]`

**Configuration:**

| Setting | Value | Description |
|---------|-------|-------------|
| [SETTING_1] | `[VALUE]` | [DESCRIPTION] |
| [SETTING_2] | `[VALUE]` | [DESCRIPTION] |
| [SETTING_3] | `[VALUE]` | [DESCRIPTION] |
| [SETTING_4] | `[VALUE]` | [DESCRIPTION] |

**[CONTAINERS/COLLECTIONS/QUEUES] (if applicable):**

| Name | Configuration | Purpose |
|------|---------------|---------|
| `[NAME_1]` | [CONFIG] | [PURPOSE] |
| `[NAME_2]` | [CONFIG] | [PURPOSE] |
| `[NAME_3]` | [CONFIG] | [PURPOSE] |

---

### [SERVICE_2]: [SERVICE_NAME]

**Resource Details (from AZURE_CONFIG.json):**
```bash
jq -r '.stages.[STAGE].resources.[RESOURCE_KEY]' concept/AZURE_CONFIG.json
```

- Resource Name Pattern: `[PREFIX]-{uid}-[PURPOSE]`
- SKU: `[SKU]`
- Location: `[LOCATION]`

**Configuration:**

| Setting | Value | Description |
|---------|-------|-------------|
| [SETTING_1] | `[VALUE]` | [DESCRIPTION] |
| [SETTING_2] | `[VALUE]` | [DESCRIPTION] |
| [SETTING_3] | `[VALUE]` | [DESCRIPTION] |

**Authentication Pattern (Managed Identity):**
```bash
# No connection strings — use Managed Identity
ENDPOINT=$(jq -r '.stages.[STAGE].resources.[RESOURCE].endpoint' concept/AZURE_CONFIG.json)
# Application authenticates via DefaultAzureCredential or ManagedIdentityCredential
```

---

### [SERVICE_3]: [SERVICE_NAME]

**Resource Details (from AZURE_CONFIG.json):**
```bash
jq -r '.stages.[STAGE].resources.[RESOURCE_KEY]' concept/AZURE_CONFIG.json
```

- Resource Name Pattern: `[PREFIX]-{uid}-[PURPOSE]`
- SKU: `[SKU]`
- Location: `[LOCATION]`

**Configuration:**

| Setting | Value | Description |
|---------|-------|-------------|
| [SETTING_1] | `[VALUE]` | [DESCRIPTION] |
| [SETTING_2] | `[VALUE]` | [DESCRIPTION] |

**[DEPLOYMENTS/INSTANCES] (if applicable):**

| Name | Model/Type | Configuration |
|------|------------|---------------|
| `[NAME_1]` | [MODEL] | [CONFIG] |
| `[NAME_2]` | [MODEL] | [CONFIG] |

---

### [SERVICE_4]: [SERVICE_NAME]

**Resource Details (from AZURE_CONFIG.json):**
```bash
jq -r '.stages.[STAGE].resources.[RESOURCE_KEY]' concept/AZURE_CONFIG.json
```

- Resource Name Pattern: `[PREFIX]-{uid}-[PURPOSE]`
- SKU: `[SKU]`
- Location: `[LOCATION]`

**Configuration:**

| Setting | Value | Description |
|---------|-------|-------------|
| [SETTING_1] | `[VALUE]` | [DESCRIPTION] |
| [SETTING_2] | `[VALUE]` | [DESCRIPTION] |
| [SETTING_3] | `[VALUE]` | [DESCRIPTION] |

**Scaling Configuration:**

| Parameter | Value | Description |
|-----------|-------|-------------|
| Min Replicas | `[VALUE]` | [DESCRIPTION] |
| Max Replicas | `[VALUE]` | [DESCRIPTION] |
| Scale Trigger | `[TRIGGER]` | [DESCRIPTION] |
| CPU | `[VALUE]` | [DESCRIPTION] |
| Memory | `[VALUE]` | [DESCRIPTION] |

---

### [SERVICE_5]: Blob Storage

**Resource Details (from AZURE_CONFIG.json):**
```bash
jq -r '.stages.[STAGE].resources.storageAccount' concept/AZURE_CONFIG.json
```

- Resource Name Pattern: `st{uid}[purpose]` (no hyphens, lowercase)
- SKU: `[SKU]`
- Location: `[LOCATION]`

**Containers:**

| Container | Access Level | Purpose |
|-----------|--------------|---------|
| `[CONTAINER_1]` | Private | [PURPOSE] |
| `[CONTAINER_2]` | Private | [PURPOSE] |
| `[CONTAINER_3]` | Private | [PURPOSE] |

**Authentication (Managed Identity):**
```bash
# No shared keys — storage account configured with:
# allowSharedKeyAccess: false
# publicNetworkAccess: Enabled (for Managed Identity access)

# App Settings Pattern:
[PREFIX]__blobServiceUri=https://{storage-account}.blob.core.windows.net
[PREFIX]__credential=managedidentity
```

**Lifecycle Policies:**

| Rule | Scope | Action | Condition |
|------|-------|--------|-----------|
| [RULE_1] | [SCOPE] | [ACTION] | [CONDITION] |
| [RULE_2] | [SCOPE] | [ACTION] | [CONDITION] |

---

## 5. Application Configurations

Applications are located in `concept/apps/` as independent solutions.

### [APPLICATION_1]

**Location:** `concept/apps/[APP_DIRECTORY]/`

**Runtime:** [RUNTIME_AND_VERSION]

**App Settings:**

| Setting | Value/Pattern | Description |
|---------|---------------|-------------|
| `[SETTING_1]` | `[VALUE]` | [DESCRIPTION] |
| `[SETTING_2]` | `[VALUE]` | [DESCRIPTION] |
| `[SETTING_3]` | `@Microsoft.KeyVault(SecretUri=[URI])` | [DESCRIPTION] |
| `[SETTING_4]` | `[VALUE]` | [DESCRIPTION] |

**Configuration File (if applicable):**

Location: `concept/apps/[APP_DIRECTORY]/[CONFIG_FILE]`

```json
{
  "[SECTION_1]": {
    "[KEY_1]": "[VALUE]",
    "[KEY_2]": "[VALUE]"
  },
  "[SECTION_2]": {
    "[KEY_1]": "[VALUE]",
    "[KEY_2]": "[VALUE]"
  }
}
```

---

### [APPLICATION_2]

**Location:** `concept/apps/[APP_DIRECTORY]/`

**Runtime:** [RUNTIME_AND_VERSION]

**App Settings:**

| Setting | Value/Pattern | Description |
|---------|---------------|-------------|
| `[SETTING_1]` | `[VALUE]` | [DESCRIPTION] |
| `[SETTING_2]` | `[VALUE]` | [DESCRIPTION] |
| `[SETTING_3]` | `[VALUE]` | [DESCRIPTION] |

**Environment-Specific Overrides:**

| Setting | Dev | Stg | Prd |
|---------|-----|-----|-----|
| `[SETTING_1]` | [VALUE] | [VALUE] | [VALUE] |
| `[SETTING_2]` | [VALUE] | [VALUE] | [VALUE] |

---

## 6. Security Configuration

### Key Vault

**Resource (from AZURE_CONFIG.json):**
```bash
jq -r '.stages.stage1.resources.keyVault' concept/AZURE_CONFIG.json
```

- Resource Name Pattern: `kv-{uid}-[purpose]`

**Secrets:**

| Secret Name | Description | Rotation Policy |
|-------------|-------------|-----------------|
| `[SECRET_1]` | [DESCRIPTION] | [POLICY] |
| `[SECRET_2]` | [DESCRIPTION] | [POLICY] |
| `[SECRET_3]` | [DESCRIPTION] | [POLICY] |

**RBAC Configuration:**

| Identity | Type | Role |
|----------|------|------|
| [IDENTITY_1] | Managed Identity | Key Vault Secrets User |
| [IDENTITY_2] | Managed Identity | Key Vault Secrets User |

### Managed Identities

**Identities (from AZURE_CONFIG.json):**
```bash
jq -r '.stages.[STAGE].managedIdentities' concept/AZURE_CONFIG.json
```

| Identity | Type | Assigned To | Role Assignments |
|----------|------|-------------|------------------|
| [IDENTITY_1] | User-Assigned | [RESOURCE] | [ROLES] |
| [IDENTITY_2] | System-Assigned | [RESOURCE] | [ROLES] |

### RBAC Assignments

**CRITICAL:** All service-to-service authentication uses Managed Identity. No connection strings or access keys.

| Principal | Resource | Role |
|-----------|----------|------|
| [MANAGED_IDENTITY_1] | [STORAGE_ACCOUNT] | Storage Blob Data Contributor |
| [MANAGED_IDENTITY_1] | [KEY_VAULT] | Key Vault Secrets User |
| [MANAGED_IDENTITY_1] | [COSMOS_DB] | Cosmos DB Data Contributor |
| [MANAGED_IDENTITY_2] | [SQL_DATABASE] | db_datareader, db_datawriter |

---

## 7. Networking Configuration

### Virtual Network (if applicable)

| Setting | Value |
|---------|-------|
| VNet Name | `vnet-{uid}-[purpose]` |
| Address Space | `[ADDRESS_SPACE]` |
| Location | `[LOCATION]` |

**Subnets:**

| Subnet | Address Range | Delegations | NSG |
|--------|---------------|-------------|-----|
| [SUBNET_1] | [RANGE] | [DELEGATIONS] | [NSG] |
| [SUBNET_2] | [RANGE] | [DELEGATIONS] | [NSG] |

### Private Endpoints (if applicable)

| Resource | Private Endpoint | Subnet | Private DNS Zone |
|----------|------------------|--------|------------------|
| [RESOURCE_1] | [PE_NAME] | [SUBNET] | [DNS_ZONE] |
| [RESOURCE_2] | [PE_NAME] | [SUBNET] | [DNS_ZONE] |

### Firewall Rules

| Resource | Rule Name | Source | Destination | Action |
|----------|-----------|--------|-------------|--------|
| [RESOURCE_1] | [RULE] | [SOURCE] | [DEST] | Allow |
| [RESOURCE_2] | [RULE] | [SOURCE] | [DEST] | Allow |

---

## 8. Monitoring Configuration

### Application Insights

**Resource (from AZURE_CONFIG.json):**
```bash
jq -r '.stages.stage1.resources.appInsights' concept/AZURE_CONFIG.json
```

| Setting | Value |
|---------|-------|
| Name | `appi-{uid}-[purpose]` |
| Workspace | `[LOG_ANALYTICS_WORKSPACE]` |
| Sampling | `[SAMPLING_PERCENTAGE]`% |
| Retention | `[DAYS]` days |

### Log Analytics

**Resource (from AZURE_CONFIG.json):**
```bash
jq -r '.stages.stage1.resources.logAnalytics' concept/AZURE_CONFIG.json
```

| Setting | Value |
|---------|-------|
| Name | `log-{uid}-[purpose]` |
| SKU | `[SKU]` |
| Retention | `[DAYS]` days |

### Diagnostic Settings

| Resource | Logs | Metrics | Destination |
|----------|------|---------|-------------|
| [RESOURCE_1] | [CATEGORIES] | Enabled | Log Analytics |
| [RESOURCE_2] | [CATEGORIES] | Enabled | Log Analytics |

### Alerts (if configured)

| Alert Name | Condition | Threshold | Action |
|------------|-----------|-----------|--------|
| [ALERT_1] | [CONDITION] | [THRESHOLD] | [ACTION] |
| [ALERT_2] | [CONDITION] | [THRESHOLD] | [ACTION] |

---

## 9. Configuration by Environment

### Development (dev)

```bash
# Deployment
./concept/infra/deploy.sh -u [UID] -e dev -s all -l [LOCATION]

# Core Settings
ENVIRONMENT=dev
LOCATION=[LOCATION]

# Scaling (minimal for cost savings)
[SERVICE]_MIN_REPLICAS=0
[SERVICE]_MAX_REPLICAS=2

# Features
[FEATURE_1]=true
[FEATURE_2]=false
```

### Staging (stg)

```bash
# Deployment
./concept/infra/deploy.sh -u [UID] -e stg -s all -l [LOCATION]

# Core Settings
ENVIRONMENT=stg
LOCATION=[LOCATION]

# Scaling
[SERVICE]_MIN_REPLICAS=1
[SERVICE]_MAX_REPLICAS=10

# Features
[FEATURE_1]=true
[FEATURE_2]=true
```

### Production (prd)

```bash
# Deployment
./concept/infra/deploy.sh -u [UID] -e prd -s all -l [LOCATION]

# Core Settings
ENVIRONMENT=prd
LOCATION=[LOCATION]

# Scaling
[SERVICE]_MIN_REPLICAS=2
[SERVICE]_MAX_REPLICAS=100

# Features
[FEATURE_1]=true
[FEATURE_2]=true

# Production-only
[PROD_SETTING_1]=[VALUE]
[PROD_SETTING_2]=[VALUE]
```

### Environment Comparison

| Configuration | Dev | Stg | Prd |
|---------------|-----|-----|-----|
| [CONFIG_1] | [VALUE] | [VALUE] | [VALUE] |
| [CONFIG_2] | [VALUE] | [VALUE] | [VALUE] |
| [CONFIG_3] | [VALUE] | [VALUE] | [VALUE] |
| [CONFIG_4] | [VALUE] | [VALUE] | [VALUE] |

---

## Configuration Validation

### Pre-Deployment Checklist

- [ ] `concept/AZURE_CONFIG.json` has correct project and subscription details
- [ ] All required resource providers are registered
- [ ] Key Vault secrets are populated (if pre-existing secrets required)
- [ ] Managed Identity role assignments are planned
- [ ] Network configuration allows required connectivity
- [ ] Naming conventions follow `{prefix}-{uid}-{purpose}` pattern
- [ ] Required tags (Environment, Stage, Purpose) are defined

### Post-Deployment Validation

```bash
# Verify AZURE_CONFIG.json was updated
cat concept/AZURE_CONFIG.json | jq '.stages'

# Verify [SERVICE_1] configuration
jq -r '.stages.[STAGE].resources.[RESOURCE]' concept/AZURE_CONFIG.json

# Verify managed identity assignments
az role assignment list --assignee $(jq -r '.stages.[STAGE].managedIdentities.[IDENTITY].principalId' concept/AZURE_CONFIG.json) --output table

# Verify connectivity (example for storage)
az storage container list --account-name $(jq -r '.stages.[STAGE].resources.storageAccount.name' concept/AZURE_CONFIG.json) --auth-mode login
```

---

*Last updated: [DATE]*
