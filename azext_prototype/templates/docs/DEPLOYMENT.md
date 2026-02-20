# [PROJECT_NAME] - Deployment Guide

## Instructions for Claude Code

This template provides step-by-step deployment instructions for the solution. Populate each section by analyzing:
- Infrastructure code in `concept/infra/`
- Application code in `concept/apps/`
- The `concept/AZURE_CONFIG.json` for resource configurations
- Database scripts in `concept/db/` (if applicable)

Replace all `[PLACEHOLDER]` values with actual information. Remove sections for components not used in this engagement. All commands should be copy-paste ready.

**Key Principles:**
- Commands should work as written (after replacing placeholders)
- Include prerequisites for each section
- Document the order of operations clearly
- Provide troubleshooting for common failures

---

## Overview

This guide covers deploying [PROJECT_NAME] to Azure. The solution uses the following components:

- **[COMPONENT_1]** - [PURPOSE]
- **[COMPONENT_2]** - [PURPOSE]
- **[COMPONENT_3]** - [PURPOSE]
- **[COMPONENT_4]** - [PURPOSE]
- **[COMPONENT_5]** - [PURPOSE]

## Prerequisites

### Required Tools

```bash
# Azure CLI
brew install azure-cli  # macOS
# or visit: https://docs.microsoft.com/cli/azure/install-azure-cli

# [TOOL_2]
[INSTALLATION_COMMAND]

# [TOOL_3]
[INSTALLATION_COMMAND]

# [TOOL_4] (if using [OPTION])
[INSTALLATION_COMMAND]

# jq (JSON processor)
brew install jq  # macOS
```

### Azure Subscription

- Active Azure subscription with Owner or Contributor access
- Registered resource providers:
  - [PROVIDER_1]
  - [PROVIDER_2]
  - [PROVIDER_3]
  - [PROVIDER_4]
  - [PROVIDER_5]

```bash
# Register required resource providers
az provider register --namespace [PROVIDER_1]
az provider register --namespace [PROVIDER_2]
az provider register --namespace [PROVIDER_3]
az provider register --namespace [PROVIDER_4]
az provider register --namespace [PROVIDER_5]

# Verify registration
az provider show --namespace [PROVIDER_1] --query "registrationState"
```

## Staged Deployment Architecture

The infrastructure is deployed in [N] stages for better isolation and easier troubleshooting:

| Stage | Resource Group Pattern | Resources |
|-------|------------------------|-----------|
| 1 - [STAGE_1_NAME] | `rg-{uid}-[PURPOSE]` | [RESOURCES] |
| 2 - [STAGE_2_NAME] | `rg-{uid}-[PURPOSE]` | [RESOURCES] |
| 3 - [STAGE_3_NAME] | `rg-{uid}-[PURPOSE]` | [RESOURCES] |
| 4 - [STAGE_4_NAME] | `rg-{uid}-[PURPOSE]` | [RESOURCES] |
| 5 - [STAGE_5_NAME] | `rg-{uid}-[PURPOSE]` | [RESOURCES] |

### Benefits of Staged Deployment

- **Isolation**: Each stage in its own resource group for easy cleanup
- **Debugging**: Failed stages can be deleted and redeployed without affecting others
- **Regional Flexibility**: Certain resources can be deployed to regions with availability
- **Incremental Deployment**: Deploy and verify each stage before proceeding

## Deployment Script

The deployment script (`concept/infra/deploy.sh`) accepts the following parameters:

```
Required Parameters:
  -l, --location          Azure region for resources (e.g., eastus, northeurope)
  -u, --uid               Unique root identifier for all services (e.g., 'my-app')
                          Resources derive names from this (e.g., 'rg-my-app-data')
  -e, --environment       Environment: dev, stg, or prd
  -s, --stage             Stage number to deploy (1, 2, 3, etc.) or 'all'

Optional Parameters:
  --[SERVICE]-location    Override location for specific service (e.g., --openai-location eastus2)
  -h, --help              Show help message
```

**Resource Naming Convention:**
All resources include stage, environment, and purpose in their labels:
- Resource Groups: `rg-{uid}-{purpose}`
- App Services: `appsvc-{uid}-{purpose}`
- Functions: `func-{uid}-{purpose}`

---

## Option A: Bicep Deployment

### 1. Login to Azure

```bash
az login
az account set --subscription <subscription-id>
```

### 2. Make Scripts Executable

```bash
chmod +x concept/infra/deploy.sh
```

### 3. Deploy Stages

Deploy each stage individually for better control:

```bash
# Stage 1: [STAGE_1_NAME]
./concept/infra/deploy.sh -u [UID] -e dev -s 1 -l [LOCATION]

# Stage 2: [STAGE_2_NAME]
./concept/infra/deploy.sh -u [UID] -e dev -s 2 -l [LOCATION]

# Stage 3: [STAGE_3_NAME]
./concept/infra/deploy.sh -u [UID] -e dev -s 3 -l [LOCATION]

# Stage 4: [STAGE_4_NAME]
./concept/infra/deploy.sh -u [UID] -e dev -s 4 -l [LOCATION]

# Stage 5: [STAGE_5_NAME] (if different location needed for specific services)
./concept/infra/deploy.sh -u [UID] -e dev -s 5 -l [LOCATION] --[SERVICE]-location [ALT_LOCATION]
```

Or deploy all stages at once:

```bash
./concept/infra/deploy.sh -u [UID] -e dev -s all -l [LOCATION]
```

### Configuration Output

Deployment outputs are automatically saved to `concept/AZURE_CONFIG.json`:

```bash
cat concept/AZURE_CONFIG.json | jq .
```

---

## Option B: Terraform Deployment

### 1. Login to Azure

```bash
az login
az account set --subscription <subscription-id>
```

### 2. Navigate to Terraform Directory

```bash
cd concept/infra/terraform
```

### 3. Initialize and Deploy Stages

```bash
# Initialize Terraform
terraform init

# Deploy Stage 1
terraform apply -target=module.stage1 -var="uid=[UID]" -var="environment=dev" -var="location=[LOCATION]"

# Deploy Stage 2
terraform apply -target=module.stage2 -var="uid=[UID]" -var="environment=dev" -var="location=[LOCATION]"

# Continue for remaining stages...

# Or deploy all at once
terraform apply -var="uid=[UID]" -var="environment=dev" -var="location=[LOCATION]"
```

### 4. Update AZURE_CONFIG.json

After Terraform deployment, update the central configuration:

```bash
# Export Terraform outputs to AZURE_CONFIG.json
terraform output -json > /tmp/tf-outputs.json
# Merge with existing config (script should handle this)
```

### Destroying Stages (Terraform)

```bash
# Destroy a specific stage
terraform destroy -target=module.stage2

# Destroy all stages
terraform destroy
```

---

## Post-Deployment Steps

### 1. Deploy Database Schema (if applicable)

Database scripts are located in `concept/db/` with the following naming convention:
- `001_create_tables.sql` - Table definitions
- `002_create_views.sql` - View definitions
- `003_create_sprocs.sql` - Stored procedures
- `004_create_udfs.sql` - User-defined functions
- `005_seed_data.sql` - Initial data seeding

NOTE: The above is the path for _SQL_ scripts, however, other folders may exist for other types of repositories.

#### Option A: Use Azure Portal Query Editor

1. Go to Azure Portal → SQL Database → `[DATABASE_NAME]`
2. Click "Query editor (preview)"
3. Login with your Entra ID account
4. Execute scripts in order:
   - Paste and run `concept/db/001_create_tables.sql`
   - Paste and run `concept/db/002_create_views.sql`
   - Paste and run `concept/db/003_create_sprocs.sql`
   - Paste and run `concept/db/004_create_udfs.sql`
   - Paste and run `concept/db/005_seed_data.sql`

#### Option B: Use Command Line

```bash
# Get database server details from AZURE_CONFIG.json
SQL_SERVER=$(jq -r '.stages.[STAGE].resources.azureSql.serverFqdn' concept/AZURE_CONFIG.json)
SQL_DB=$(jq -r '.stages.[STAGE].resources.azureSql.databaseName' concept/AZURE_CONFIG.json)

# Add yourself as Entra admin
USER_EMAIL=$(az ad signed-in-user show --query userPrincipalName -o tsv)
USER_OBJECT_ID=$(az ad signed-in-user show --query id -o tsv)
RG_DATA=$(jq -r '.stages.[STAGE].resourceGroups.group1.name' concept/AZURE_CONFIG.json)
SQL_SERVER_NAME=$(jq -r '.stages.[STAGE].resources.azureSql.name' concept/AZURE_CONFIG.json)

az sql server ad-admin create \
    --resource-group $RG_DATA \
    --server-name $SQL_SERVER_NAME \
    --display-name "$USER_EMAIL" \
    --object-id $USER_OBJECT_ID

# Deploy schema scripts in order
sqlcmd -S "$SQL_SERVER" -d "$SQL_DB" -G -U "$USER_EMAIL" -i concept/db/001_create_tables.sql
sqlcmd -S "$SQL_SERVER" -d "$SQL_DB" -G -U "$USER_EMAIL" -i concept/db/002_create_views.sql
sqlcmd -S "$SQL_SERVER" -d "$SQL_DB" -G -U "$USER_EMAIL" -i concept/db/003_create_sprocs.sql
sqlcmd -S "$SQL_SERVER" -d "$SQL_DB" -G -U "$USER_EMAIL" -i concept/db/004_create_udfs.sql
sqlcmd -S "$SQL_SERVER" -d "$SQL_DB" -G -U "$USER_EMAIL" -i concept/db/005_seed_data.sql
```

#### Grant Access to Managed Identity

```sql
-- Get managed identity name from AZURE_CONFIG.json:
-- jq -r '.stages.[STAGE].managedIdentities.[IDENTITY].name' concept/AZURE_CONFIG.json

CREATE USER [<MANAGED_IDENTITY_NAME>] FROM EXTERNAL PROVIDER;
ALTER ROLE db_datareader ADD MEMBER [<MANAGED_IDENTITY_NAME>];
ALTER ROLE db_datawriter ADD MEMBER [<MANAGED_IDENTITY_NAME>];
```

---

### 2. Build and Push Container Image (if applicable)

```bash
# Get container registry from AZURE_CONFIG.json
ACR_LOGIN=$(jq -r '.stages.[STAGE].resources.containerRegistry.loginServer' concept/AZURE_CONFIG.json)

# Login to ACR
az acr login --name ${ACR_LOGIN%%.*}

# Build and push from concept/apps/[APP_NAME]
docker build -t ${ACR_LOGIN}/[IMAGE_NAME]:[TAG] ./concept/apps/[APP_DIRECTORY]
docker push ${ACR_LOGIN}/[IMAGE_NAME]:[TAG]

# Update Container App (if applicable)
CONTAINER_APP=$(jq -r '.stages.[STAGE].resources.containerApp.name' concept/AZURE_CONFIG.json)
RG_COMPUTE=$(jq -r '.stages.[STAGE].resourceGroups.group1.name' concept/AZURE_CONFIG.json)

az containerapp update \
    --name $CONTAINER_APP \
    --resource-group $RG_COMPUTE \
    --image ${ACR_LOGIN}/[IMAGE_NAME]:[TAG]
```

---

### 3. Upload Assets/Templates (if applicable)

```bash
# Get storage account from AZURE_CONFIG.json
STORAGE_ACCOUNT=$(jq -r '.stages.[STAGE].resources.storageAccount.name' concept/AZURE_CONFIG.json)

# Upload files to blob storage
az storage blob upload \
    --account-name $STORAGE_ACCOUNT \
    --container-name [CONTAINER_NAME] \
    --name "[BLOB_NAME]" \
    --file ./[LOCAL_PATH] \
    --auth-mode login

# Or batch upload
az storage blob upload-batch \
    --account-name $STORAGE_ACCOUNT \
    --destination [CONTAINER_NAME] \
    --source ./[LOCAL_DIRECTORY] \
    --pattern "[PATTERN]" \
    --auth-mode login

# Verify uploads
az storage blob list \
    --account-name $STORAGE_ACCOUNT \
    --container-name [CONTAINER_NAME] \
    --output table
```

---

### 4. Deploy Application Code

Applications are located in `concept/apps/` as independent solutions.

#### [APPLICATION_1]

```bash
# Get resource details from AZURE_CONFIG.json
APP_NAME=$(jq -r '.stages.[STAGE].resources.[RESOURCE].name' concept/AZURE_CONFIG.json)
RG_NAME=$(jq -r '.stages.[STAGE].resourceGroups.group1.name' concept/AZURE_CONFIG.json)

cd concept/apps/[APP_DIRECTORY]

# Install dependencies (if applicable)
[INSTALL_COMMAND]

# Deploy
[DEPLOY_COMMAND]
```

#### [APPLICATION_2] (if applicable)

```bash
APP_NAME=$(jq -r '.stages.[STAGE].resources.[RESOURCE].name' concept/AZURE_CONFIG.json)

cd concept/apps/[APP_DIRECTORY]
[DEPLOY_COMMANDS]
```

---

## Troubleshooting Deployments

### Cleaning Up Failed Stages

If a stage fails, delete its resource group and retry:

```bash
# Get resource group names from AZURE_CONFIG.json
RG_STAGE1=$(jq -r '.stages.stage1.resourceGroups.group1.name' concept/AZURE_CONFIG.json)
RG_STAGE2=$(jq -r '.stages.stage2.resourceGroups.group1.name' concept/AZURE_CONFIG.json)

# Delete specific stage's resource group
az group delete --name $RG_STAGE1 --yes --no-wait
az group delete --name $RG_STAGE2 --yes --no-wait
# Continue for all stages...
```

### Purging Soft-Deleted Resources

Some resources go into soft-delete state. Purge before redeploying:

```bash
# List and purge deleted Key Vaults
az keyvault list-deleted --query "[].{name:name, location:properties.location}" -o table
az keyvault purge --name <vault-name> --location <location>

# List and purge deleted Cognitive Services (if applicable)
az cognitiveservices account list-deleted -o table
az cognitiveservices account purge --name <account-name> --resource-group <rg> --location <location>
```

### Regional Availability Issues

If you encounter quota or availability errors:

1. **[SERVICE_1]**: Try [RECOMMENDED_REGIONS]
2. **[SERVICE_2]**: Check subscription restrictions, try different regions
3. **[SERVICE_3]**: Use [RECOMMENDED_REGIONS] for availability
4. **[SERVICE_4]**: [SPECIFIC_GUIDANCE]

### Common Errors

| Error | Solution |
|-------|----------|
| `[ERROR_1]` | [SOLUTION] |
| `[ERROR_2]` | [SOLUTION] |
| `[ERROR_3]` | [SOLUTION] |
| `[ERROR_4]` | [SOLUTION] |
| `[ERROR_5]` | [SOLUTION] |

---

## Environment-Specific Configurations

### Development

```bash
./concept/infra/deploy.sh -u [UID] -e dev -s all -l [LOCATION]
```

### Staging

```bash
./concept/infra/deploy.sh -u [UID] -e stg -s all -l [LOCATION]
```

### Production

```bash
./concept/infra/deploy.sh -u [UID] -e prd -s all -l [LOCATION]
```

---

## Resource Sizing by Environment

| Resource | Dev | Staging | Prod |
|----------|-----|---------|------|
| [SERVICE_1] | [DEV_SKU] | [STAGING_SKU] | [PROD_SKU] |
| [SERVICE_2] | [DEV_SKU] | [STAGING_SKU] | [PROD_SKU] |
| [SERVICE_3] | [DEV_SKU] | [STAGING_SKU] | [PROD_SKU] |
| [SERVICE_4] | [DEV_REPLICAS] | [STAGING_REPLICAS] | [PROD_REPLICAS] |

---

## Monitoring

### Application Insights

Access via Azure Portal: Application Insights → `[APP_INSIGHTS_NAME]`

### Log Analytics Queries

```kusto
// [COMPONENT_1] logs
[TABLE_NAME]
| where [FILTER_CONDITION]
| where TimeGenerated > ago(1h)
| order by TimeGenerated desc

// [COMPONENT_2] errors
[TABLE_NAME]
| where Level == "Error"
| where TimeGenerated > ago(24h)
```

---

## Security

- All resources use TLS 1.2+
- Entra ID (Azure AD) authentication by default
- Blob storage has public access disabled
- Key Vault uses RBAC authorization
- Managed identities used for service-to-service auth
- **No connection strings or access keys** — Managed Identity only

### Managed Identity Configuration

[Describe any special managed identity configurations required]

**Required Role Assignments:**

| Identity | Resource | Role |
|----------|----------|------|
| [IDENTITY_1] | [RESOURCE] | [ROLE] |
| [IDENTITY_2] | [RESOURCE] | [ROLE] |

### Production Recommendations

1. Enable VNet integration for [SERVICES]
2. Configure Private Endpoints for PaaS services
3. Use Managed Identity for all service connections
4. Enable Azure Defender for all services
5. [ADDITIONAL_RECOMMENDATION]

---

## Cleanup

Delete all resource groups (get names from AZURE_CONFIG.json):

```bash
# Read resource groups from config
jq -r '.stages[].resourceGroups[].name' concept/AZURE_CONFIG.json | while read rg; do
    echo "Deleting $rg..."
    az group delete --name "$rg" --yes --no-wait
done
```

Or delete specific stages:

```bash
RG_STAGE1=$(jq -r '.stages.stage1.resourceGroups.group1.name' concept/AZURE_CONFIG.json)
az group delete --name $RG_STAGE1 --yes --no-wait
```

Or use Terraform destroy:

```bash
cd concept/infra/terraform
terraform destroy
```

---

*Last updated: [DATE]*
