# [PROJECT_NAME] - Development Guide

## Instructions for Claude Code

This template provides detailed step-by-step instructions for setting up the development environment and deploying all components. Populate each section by analyzing:
- Infrastructure code in `concept/infra/terraform/` and `concept/infra/bicep/`
- Application code in `concept/apps/`
- The `concept/AZURE_CONFIG.json` for resource configurations
- Database scripts in `concept/db/` (if applicable)

Replace all `[PLACEHOLDER]` values with actual information. Remove sections for components not used in this engagement. All commands should be copy-paste ready for developers.

**Key Principles:**
- Provide complete setup instructions from scratch
- Include both local development and Azure deployment
- Document all configuration steps in detail
- Provide verification steps after each major section
- **No connection strings or access keys** — use Managed Identity only

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Infrastructure Deployment](#2-infrastructure-deployment)
3. [Container Registry Setup](#3-container-registry-setup)
4. [Key Vault Configuration](#4-key-vault-configuration)
5. [AI Service Setup](#5-ai-service-setup)
6. [Application Deployment](#6-application-deployment)
7. [Database Schema Deployment](#7-database-schema-deployment)
8. [Asset Upload](#8-asset-upload)
9. [Local Development](#9-local-development)
10. [End-to-End Verification](#10-end-to-end-verification)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Prerequisites

### 1.1 Required Tools

Install the following tools on your development machine:

```bash
# Azure CLI (macOS)
brew install azure-cli

# Azure CLI (Windows) - run in PowerShell as Administrator
winget install Microsoft.AzureCLI

# [TOOL_1] (if applicable)
brew install [TOOL_1]  # macOS
[WINDOWS_INSTALL_COMMAND]  # Windows

# [TOOL_2]
brew install [TOOL_2]  # macOS
[WINDOWS_INSTALL_COMMAND]  # Windows

# Docker Desktop
brew install --cask docker  # macOS
# Windows: Download from https://www.docker.com/products/docker-desktop

# Terraform (if using Terraform)
brew install terraform  # macOS
choco install terraform  # Windows

# [LANGUAGE_RUNTIME]
brew install [RUNTIME]  # macOS
# Windows: Download from [URL]

# jq (JSON processor)
brew install jq  # macOS
choco install jq  # Windows
```

### 1.2 Azure Subscription Setup

```bash
# Login to Azure
az login

# Set your subscription
az account set --subscription "<your-subscription-id>"

# Verify subscription
az account show --output table

# Register required resource providers
az provider register --namespace [PROVIDER_1]
az provider register --namespace [PROVIDER_2]
az provider register --namespace [PROVIDER_3]
az provider register --namespace [PROVIDER_4]
az provider register --namespace [PROVIDER_5]

# Verify registration (wait for all to show "Registered")
az provider show --namespace [PROVIDER_1] --query "registrationState"
```

### 1.3 Clone Repository

```bash
git clone <repository-url>
cd [PROJECT_DIRECTORY]
```

---

## 2. Infrastructure Deployment

Infrastructure is deployed using the `concept/infra/deploy.sh` script with staged deployments.

### Deployment Script Parameters

```
Required Parameters:
  -l, --location          Azure region for resources (e.g., eastus, northeurope)
  -u, --uid               Unique root identifier for all services (e.g., 'my-app')
                          Resources derive names from this (e.g., 'rg-my-app-data')
  -e, --environment       Environment: dev, stg, or prd
  -s, --stage             Stage number to deploy (1, 2, 3, etc.) or 'all'

Optional Parameters:
  --[SERVICE]-location    Override location for specific service
  -h, --help              Show help message
```

### Option A: Deploy with Bicep (via deploy.sh)

#### 2.1 Make Script Executable

```bash
chmod +x concept/infra/deploy.sh
```

#### 2.2 Deploy Stages

```bash
# Set deployment variables
UID_NAME="[UNIQUE_ID]"        # e.g., "myapp" - used in all resource names
LOCATION="[LOCATION]"          # e.g., "eastus"
ENVIRONMENT="dev"              # dev, stg, or prd

# Deploy Stage 1: [STAGE_1_NAME]
./concept/infra/deploy.sh -u $UID_NAME -e $ENVIRONMENT -s 1 -l $LOCATION

# Deploy Stage 2: [STAGE_2_NAME]
./concept/infra/deploy.sh -u $UID_NAME -e $ENVIRONMENT -s 2 -l $LOCATION

# Deploy Stage 3: [STAGE_3_NAME]
./concept/infra/deploy.sh -u $UID_NAME -e $ENVIRONMENT -s 3 -l $LOCATION

# Deploy Stage 4: [STAGE_4_NAME]
./concept/infra/deploy.sh -u $UID_NAME -e $ENVIRONMENT -s 4 -l $LOCATION

# Deploy Stage 5: [STAGE_5_NAME] (if different location needed)
./concept/infra/deploy.sh -u $UID_NAME -e $ENVIRONMENT -s 5 -l $LOCATION --[SERVICE]-location [ALT_LOCATION]
```

Or deploy all stages at once:

```bash
./concept/infra/deploy.sh -u $UID_NAME -e $ENVIRONMENT -s all -l $LOCATION
```

#### 2.3 Verify Deployment Outputs

The deployment script automatically updates `concept/AZURE_CONFIG.json`:

```bash
# View full configuration
cat concept/AZURE_CONFIG.json | jq '.'

# View specific stage resources
jq '.stages.stage1.resources' concept/AZURE_CONFIG.json

# Get resource group names
jq -r '.stages[].resourceGroups[].name' concept/AZURE_CONFIG.json
```

### Option B: Deploy with Terraform

#### 2.1 Navigate to Terraform Directory

```bash
cd concept/infra/terraform
```

#### 2.2 Configure Terraform Variables

```bash
# Copy example tfvars
cp terraform.tfvars.example terraform.tfvars

# Edit terraform.tfvars with your values
cat > terraform.tfvars << 'EOF'
uid         = "[UNIQUE_ID]"
environment = "dev"
location    = "[LOCATION]"

[VARIABLE_1] = "[VALUE]"
[VARIABLE_2] = "[VALUE]"

tags = {
  Environment = "Dev"
  Stage       = "Stage 1"
  Purpose     = "[PURPOSE]"
}
EOF
```

#### 2.3 Initialize and Apply Terraform

```bash
# Initialize Terraform
terraform init

# Review the plan
terraform plan -out=tfplan

# Apply the configuration
terraform apply tfplan

# Update AZURE_CONFIG.json with outputs
terraform output -json > /tmp/tf-outputs.json
# (Merge with concept/AZURE_CONFIG.json as needed)
```

---

## 3. Container Registry Setup

### 3.1 Get Registry Details from AZURE_CONFIG.json

```bash
# Get ACR details
ACR_NAME=$(jq -r '.stages.[STAGE].resources.containerRegistry.name' concept/AZURE_CONFIG.json)
ACR_LOGIN_SERVER=$(jq -r '.stages.[STAGE].resources.containerRegistry.loginServer' concept/AZURE_CONFIG.json)

echo "ACR Name: $ACR_NAME"
echo "ACR Login Server: $ACR_LOGIN_SERVER"
```

### 3.2 Login to Container Registry

```bash
# Login with Azure CLI (recommended - uses Managed Identity pattern)
az acr login --name $ACR_NAME
```

### 3.3 Build and Push Images

```bash
# Navigate to application directory
cd concept/apps/[APP_DIRECTORY]

# Build the Docker image
docker build -t $ACR_LOGIN_SERVER/[IMAGE_NAME]:[TAG] .

# Push to ACR
docker push $ACR_LOGIN_SERVER/[IMAGE_NAME]:[TAG]

# Verify image
az acr repository list --name $ACR_NAME --output table
az acr repository show-tags --name $ACR_NAME --repository [IMAGE_NAME] --output table
```

---

## 4. Key Vault Configuration

### 4.1 Get Key Vault from AZURE_CONFIG.json

```bash
# Get Key Vault details
KV_NAME=$(jq -r '.stages.stage1.resources.keyVault.name' concept/AZURE_CONFIG.json)
RG_NAME=$(jq -r '.stages.stage1.resourceGroups.group1.name' concept/AZURE_CONFIG.json)

echo "Key Vault: $KV_NAME"
```

### 4.2 Store Secrets

```bash
# Store [SECRET_1]
az keyvault secret set \
    --vault-name $KV_NAME \
    --name "[SECRET_1_NAME]" \
    --value "[SECRET_VALUE]"

# Store [SECRET_2]
az keyvault secret set \
    --vault-name $KV_NAME \
    --name "[SECRET_2_NAME]" \
    --value "[SECRET_VALUE]"

# List all secrets
az keyvault secret list --vault-name $KV_NAME --output table
```

### 4.3 Grant Access to Managed Identities

```bash
# Get managed identity principal ID from AZURE_CONFIG.json
IDENTITY_PRINCIPAL=$(jq -r '.stages.[STAGE].managedIdentities.[IDENTITY].principalId' concept/AZURE_CONFIG.json)

# Grant Key Vault Secrets User role (RBAC)
az role assignment create \
    --assignee $IDENTITY_PRINCIPAL \
    --role "Key Vault Secrets User" \
    --scope "/subscriptions/$(jq -r '.subscription.id' concept/AZURE_CONFIG.json)/resourceGroups/$RG_NAME/providers/Microsoft.KeyVault/vaults/$KV_NAME"
```

---

## 5. AI Service Setup

[Include this section if using Azure OpenAI, Cognitive Services, or other AI services]

### 5.1 Get AI Service Details

```bash
# Get AI service details from AZURE_CONFIG.json
AI_NAME=$(jq -r '.stages.[STAGE].resources.azureOpenAI.name' concept/AZURE_CONFIG.json)
AI_ENDPOINT=$(jq -r '.stages.[STAGE].resources.azureOpenAI.endpoint' concept/AZURE_CONFIG.json)
RG_AI=$(jq -r '.stages.[STAGE].resourceGroups.group1.name' concept/AZURE_CONFIG.json)

echo "AI Service: $AI_NAME"
echo "Endpoint: $AI_ENDPOINT"
```

### 5.2 Deploy Models (if not deployed via IaC)

```bash
# Deploy [MODEL_1]
az cognitiveservices account deployment create \
    --name $AI_NAME \
    --resource-group $RG_AI \
    --deployment-name "[DEPLOYMENT_1_NAME]" \
    --model-name "[MODEL_NAME]" \
    --model-version "[VERSION]" \
    --model-format OpenAI \
    --sku-capacity [CAPACITY] \
    --sku-name "Standard"

# Deploy [MODEL_2] (if applicable)
az cognitiveservices account deployment create \
    --name $AI_NAME \
    --resource-group $RG_AI \
    --deployment-name "[DEPLOYMENT_2_NAME]" \
    --model-name "[MODEL_NAME]" \
    --model-version "[VERSION]" \
    --model-format OpenAI \
    --sku-capacity [CAPACITY] \
    --sku-name "Standard"
```

### 5.3 Verify Deployments

```bash
# List deployments
az cognitiveservices account deployment list \
    --name $AI_NAME \
    --resource-group $RG_AI \
    --output table
```

### 5.4 Grant Managed Identity Access

```bash
# Get managed identity
IDENTITY_PRINCIPAL=$(jq -r '.stages.[STAGE].managedIdentities.[IDENTITY].principalId' concept/AZURE_CONFIG.json)

# Grant Cognitive Services User role
az role assignment create \
    --assignee $IDENTITY_PRINCIPAL \
    --role "Cognitive Services User" \
    --scope "/subscriptions/$(jq -r '.subscription.id' concept/AZURE_CONFIG.json)/resourceGroups/$RG_AI/providers/Microsoft.CognitiveServices/accounts/$AI_NAME"
```

---

## 6. Application Deployment

Applications are located in `concept/apps/` as independent solutions.

### 6.1 [APPLICATION_1] Deployment

#### Get Resource Details

```bash
# Get resource details from AZURE_CONFIG.json
APP_NAME=$(jq -r '.stages.[STAGE].resources.[RESOURCE].name' concept/AZURE_CONFIG.json)
RG_NAME=$(jq -r '.stages.[STAGE].resourceGroups.group1.name' concept/AZURE_CONFIG.json)

echo "Application: $APP_NAME"
echo "Resource Group: $RG_NAME"
```

#### Configure Environment Variables

```bash
# Update with Managed Identity endpoints (no connection strings!)
az [SERVICE_TYPE] update \
    --name $APP_NAME \
    --resource-group $RG_NAME \
    --set-env-vars \
        "[VAR_1]=$(jq -r '.stages.[STAGE].resources.[RESOURCE].endpoint' concept/AZURE_CONFIG.json)" \
        "[VAR_2]=[VALUE]" \
        "[VAR_3]=secretref:[SECRET_NAME]"
```

#### Deploy Application

```bash
cd concept/apps/[APP_DIRECTORY]

# Install dependencies
[INSTALL_COMMAND]

# Deploy to Azure
[DEPLOY_COMMAND]
```

#### Verify Deployment

```bash
# Get application URL
APP_URL=$(jq -r '.stages.[STAGE].resources.[RESOURCE].url' concept/AZURE_CONFIG.json)

echo "Application URL: https://$APP_URL"

# Test health endpoint
curl -s "https://$APP_URL/health" | jq '.'
```

---

### 6.2 [APPLICATION_2] Deployment (if applicable)

#### Get Resource Details

```bash
APP_NAME=$(jq -r '.stages.[STAGE].resources.[RESOURCE].name' concept/AZURE_CONFIG.json)
RG_NAME=$(jq -r '.stages.[STAGE].resourceGroups.group1.name' concept/AZURE_CONFIG.json)
```

#### Configure and Deploy

```bash
cd concept/apps/[APP_DIRECTORY]

# Configure app settings
az [SERVICE_TYPE] config appsettings set \
    --name $APP_NAME \
    --resource-group $RG_NAME \
    --settings \
        "[SETTING_1]=[VALUE]" \
        "[SETTING_2]=[VALUE]"

# Deploy
[DEPLOY_COMMAND]

# Verify
az [SERVICE_TYPE] function list \
    --name $APP_NAME \
    --resource-group $RG_NAME \
    --output table
```

#### Test Endpoints

```bash
APP_URL=$(jq -r '.stages.[STAGE].resources.[RESOURCE].defaultHostName' concept/AZURE_CONFIG.json)

echo "Application URL: https://$APP_URL"

# Test endpoints
curl -s "https://$APP_URL/api/health" | jq '.'
curl -s "https://$APP_URL/api/[ENDPOINT]" | jq '.'
```

---

## 7. Database Schema Deployment

Database scripts are located in `concept/db/` with the following naming convention:
- `001_create_tables.sql` - Table definitions
- `002_create_views.sql` - View definitions
- `003_create_sprocs.sql` - Stored procedures
- `004_create_udfs.sql` - User-defined functions
- `005_seed_data.sql` - Initial data seeding

### 7.1 Get Database Server Details

```bash
# Get SQL Server details from AZURE_CONFIG.json
SQL_SERVER=$(jq -r '.stages.[STAGE].resources.azureSql.serverFqdn' concept/AZURE_CONFIG.json)
SQL_SERVER_NAME=$(jq -r '.stages.[STAGE].resources.azureSql.name' concept/AZURE_CONFIG.json)
SQL_DB=$(jq -r '.stages.[STAGE].resources.azureSql.databaseName' concept/AZURE_CONFIG.json)
RG_DATA=$(jq -r '.stages.[STAGE].resourceGroups.group1.name' concept/AZURE_CONFIG.json)

echo "SQL Server: $SQL_SERVER"
echo "Database: $SQL_DB"
```

### 7.2 Configure Firewall

```bash
# Get your current IP
MY_IP=$(curl -s ifconfig.me)

# Add firewall rule
az sql server firewall-rule create \
    --resource-group $RG_DATA \
    --server $SQL_SERVER_NAME \
    --name "AllowMyIP" \
    --start-ip-address $MY_IP \
    --end-ip-address $MY_IP
```

### 7.3 Add Yourself as Entra Admin

```bash
USER_EMAIL=$(az ad signed-in-user show --query userPrincipalName -o tsv)
USER_OBJECT_ID=$(az ad signed-in-user show --query id -o tsv)

az sql server ad-admin create \
    --resource-group $RG_DATA \
    --server-name $SQL_SERVER_NAME \
    --display-name "$USER_EMAIL" \
    --object-id $USER_OBJECT_ID
```

### 7.4 Deploy Schema

```bash
# Navigate to SQL scripts
cd concept/sql

# Deploy schema scripts in order using Entra authentication
sqlcmd -S "$SQL_SERVER" -d "$SQL_DB" -G -U "$USER_EMAIL" -i 001_create_tables.sql
sqlcmd -S "$SQL_SERVER" -d "$SQL_DB" -G -U "$USER_EMAIL" -i 002_create_views.sql
sqlcmd -S "$SQL_SERVER" -d "$SQL_DB" -G -U "$USER_EMAIL" -i 003_create_sprocs.sql
sqlcmd -S "$SQL_SERVER" -d "$SQL_DB" -G -U "$USER_EMAIL" -i 004_create_udfs.sql
sqlcmd -S "$SQL_SERVER" -d "$SQL_DB" -G -U "$USER_EMAIL" -i 005_seed_data.sql
```

### 7.5 Grant Access to Managed Identity

```sql
-- Get managed identity name from AZURE_CONFIG.json:
-- jq -r '.stages.[STAGE].managedIdentities.[IDENTITY].name' concept/AZURE_CONFIG.json

CREATE USER [<MANAGED_IDENTITY_NAME>] FROM EXTERNAL PROVIDER;
ALTER ROLE db_datareader ADD MEMBER [<MANAGED_IDENTITY_NAME>];
ALTER ROLE db_datawriter ADD MEMBER [<MANAGED_IDENTITY_NAME>];
```

### 7.6 Verify Schema

```bash
# List tables
sqlcmd -S "$SQL_SERVER" -d "$SQL_DB" -G -U "$USER_EMAIL" \
    -Q "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE'"

# List views
sqlcmd -S "$SQL_SERVER" -d "$SQL_DB" -G -U "$USER_EMAIL" \
    -Q "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS"

# List stored procedures
sqlcmd -S "$SQL_SERVER" -d "$SQL_DB" -G -U "$USER_EMAIL" \
    -Q "SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_TYPE='PROCEDURE'"
```

---

## 8. Asset Upload

### 8.1 Get Storage Account from AZURE_CONFIG.json

```bash
STORAGE_ACCOUNT=$(jq -r '.stages.[STAGE].resources.storageAccount.name' concept/AZURE_CONFIG.json)

echo "Storage Account: $STORAGE_ACCOUNT"
```

### 8.2 Upload Assets

```bash
# Upload [ASSET_TYPE_1]
az storage blob upload-batch \
    --account-name $STORAGE_ACCOUNT \
    --destination [CONTAINER_NAME] \
    --source ./[SOURCE_DIRECTORY] \
    --pattern "[PATTERN]" \
    --auth-mode login

# Upload [ASSET_TYPE_2] (if applicable)
az storage blob upload-batch \
    --account-name $STORAGE_ACCOUNT \
    --destination [CONTAINER_NAME] \
    --source ./[SOURCE_DIRECTORY] \
    --pattern "[PATTERN]" \
    --auth-mode login

# Verify uploads
az storage blob list \
    --account-name $STORAGE_ACCOUNT \
    --container-name [CONTAINER_NAME] \
    --auth-mode login \
    --output table
```

---

## 9. Local Development

### 9.1 Configure Local Environment

```bash
# Navigate to application directory
cd concept/apps/[APP_DIRECTORY]

# Create local configuration file
# NOTE: For local dev, you may need to use DefaultAzureCredential
# which will use your az login credentials
cat > [CONFIG_FILE] << EOF
{
  "[SECTION_1]": {
    "[KEY_1]": "[LOCAL_VALUE]",
    "[KEY_2]": "[LOCAL_VALUE]"
  },
  "[SECTION_2]": {
    "Endpoint": "$(jq -r '.stages.[STAGE].resources.[RESOURCE].endpoint' ../../../concept/AZURE_CONFIG.json)"
  }
}
EOF
```

### 9.2 Authenticate for Local Development

```bash
# Login to Azure (required for DefaultAzureCredential)
az login

# Set the subscription
az account set --subscription "$(jq -r '.subscription.id' concept/AZURE_CONFIG.json)"
```

### 9.3 Run Locally

```bash
# Restore dependencies
[RESTORE_COMMAND]

# Run the application
[RUN_COMMAND]

# Application available at [LOCAL_URL]
```

---

## 10. End-to-End Verification

### 10.1 Health Checks

```bash
echo "=== Health Check Summary ==="

# Get URLs from AZURE_CONFIG.json
APP_1_URL=$(jq -r '.stages.[STAGE].resources.[RESOURCE_1].url' concept/AZURE_CONFIG.json)
APP_2_URL=$(jq -r '.stages.[STAGE].resources.[RESOURCE_2].defaultHostName' concept/AZURE_CONFIG.json)

# [APPLICATION_1]
echo -n "[APPLICATION_1]: "
curl -s "https://$APP_1_URL/health" | jq -r '.status // "ERROR"'

# [APPLICATION_2]
echo -n "[APPLICATION_2]: "
curl -s "https://$APP_2_URL/api/health" | jq -r '.status // "ERROR"'
```

### 10.2 Submit Test Request

```bash
# Get API URL
API_URL=$(jq -r '.stages.[STAGE].resources.[RESOURCE].url' concept/AZURE_CONFIG.json)

# Create test payload
cat > /tmp/test-request.json << 'EOF'
{
  "[FIELD_1]": "[VALUE]",
  "[FIELD_2]": "[VALUE]",
  "[FIELD_3]": {
    "[NESTED_1]": "[VALUE]",
    "[NESTED_2]": "[VALUE]"
  }
}
EOF

# Submit request
RESPONSE=$(curl -s -X POST \
    "https://$API_URL/api/[ENDPOINT]" \
    -H "Content-Type: application/json" \
    -d @/tmp/test-request.json)

RESULT_ID=$(echo $RESPONSE | jq -r '.[ID_FIELD]')
echo "Result ID: $RESULT_ID"
```

### 10.3 Monitor Status (if async)

```bash
# Poll for status
while true; do
    STATUS=$(curl -s "https://$API_URL/api/status/$RESULT_ID")
    CURRENT_STATUS=$(echo $STATUS | jq -r '.status')
    PROGRESS=$(echo $STATUS | jq -r '.progress')

    echo "Status: $CURRENT_STATUS ($PROGRESS%)"

    if [ "$CURRENT_STATUS" == "completed" ] || [ "$CURRENT_STATUS" == "error" ]; then
        break
    fi

    sleep 5
done

# Get output
OUTPUT_URL=$(echo $STATUS | jq -r '.outputUrl')
echo "Output URL: $OUTPUT_URL"
```

### 10.4 Verify Data Storage

```bash
# Check database
sqlcmd -S "$SQL_SERVER" -d "$SQL_DB" -G -U "$USER_EMAIL" \
    -Q "SELECT TOP 5 * FROM [TABLE_NAME] ORDER BY [TIMESTAMP_COLUMN] DESC"
```

---

## 11. Troubleshooting

### 11.1 [SERVICE_1] Issues

```bash
# Get resource details
SERVICE_NAME=$(jq -r '.stages.[STAGE].resources.[RESOURCE].name' concept/AZURE_CONFIG.json)
RG_NAME=$(jq -r '.stages.[STAGE].resourceGroups.group1.name' concept/AZURE_CONFIG.json)

# View logs
az [SERVICE_TYPE] logs show \
    --name $SERVICE_NAME \
    --resource-group $RG_NAME \
    --follow

# Check status
az [SERVICE_TYPE] show \
    --name $SERVICE_NAME \
    --resource-group $RG_NAME \
    --output table

# Restart
az [SERVICE_TYPE] restart \
    --name $SERVICE_NAME \
    --resource-group $RG_NAME
```

### 11.2 [SERVICE_2] Issues

```bash
SERVICE_NAME=$(jq -r '.stages.[STAGE].resources.[RESOURCE].name' concept/AZURE_CONFIG.json)
RG_NAME=$(jq -r '.stages.[STAGE].resourceGroups.group1.name' concept/AZURE_CONFIG.json)

# Stream live logs
az webapp log tail \
    --name $SERVICE_NAME \
    --resource-group $RG_NAME

# Check app settings
az [SERVICE_TYPE] config appsettings list \
    --name $SERVICE_NAME \
    --resource-group $RG_NAME \
    --output table
```

### 11.3 Database Connection Issues

```bash
# Verify firewall rules
az sql server firewall-rule list \
    --resource-group $RG_DATA \
    --server $SQL_SERVER_NAME \
    --output table

# Verify Entra admin
az sql server ad-admin list \
    --resource-group $RG_DATA \
    --server $SQL_SERVER_NAME

# Test connectivity
sqlcmd -S "$SQL_SERVER" -d "$SQL_DB" -G -U "$USER_EMAIL" -Q "SELECT 1"
```

### 11.4 Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `[ERROR_1]` | [CAUSE] | [SOLUTION] |
| `[ERROR_2]` | [CAUSE] | [SOLUTION] |
| `[ERROR_3]` | [CAUSE] | [SOLUTION] |
| `[ERROR_4]` | [CAUSE] | [SOLUTION] |
| `[ERROR_5]` | [CAUSE] | [SOLUTION] |

---

## Quick Reference

### Shell Variables from AZURE_CONFIG.json

```bash
# Export common variables for shell session
export UID_NAME=$(jq -r '.project.name' concept/AZURE_CONFIG.json)
export ENVIRONMENT=$(jq -r '.project.environment' concept/AZURE_CONFIG.json)
export SUBSCRIPTION_ID=$(jq -r '.subscription.id' concept/AZURE_CONFIG.json)

# Stage 1 resources
export KV_NAME=$(jq -r '.stages.stage1.resources.keyVault.name' concept/AZURE_CONFIG.json)
export LOG_ANALYTICS=$(jq -r '.stages.stage1.resources.logAnalytics.name' concept/AZURE_CONFIG.json)

# Stage 2 resources (example)
export STORAGE_ACCOUNT=$(jq -r '.stages.stage2.resources.storageAccount.name' concept/AZURE_CONFIG.json)
export SQL_SERVER=$(jq -r '.stages.stage2.resources.azureSql.serverFqdn' concept/AZURE_CONFIG.json)
```

### Useful Commands

```bash
# View all AZURE_CONFIG.json
cat concept/AZURE_CONFIG.json | jq '.'

# List all resource groups
jq -r '.stages[].resourceGroups[].name' concept/AZURE_CONFIG.json

# List all resources in a stage
jq '.stages.stage1.resources | keys[]' concept/AZURE_CONFIG.json

# Redeploy a single stage
./concept/infra/deploy.sh -u $UID_NAME -e $ENVIRONMENT -s 1 -l [LOCATION]

# Redeploy all stages
./concept/infra/deploy.sh -u $UID_NAME -e $ENVIRONMENT -s all -l [LOCATION]
```

---

*Last updated: [DATE]*
