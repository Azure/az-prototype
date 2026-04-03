# Azure Machine Learning
> Enterprise-grade platform for building, training, deploying, and managing machine learning models at scale, with MLOps capabilities, experiment tracking, and managed compute.

## When to Use

- **ML model training** -- train models at scale using managed compute clusters (CPU/GPU)
- **MLOps pipelines** -- automated ML workflows for data prep, training, evaluation, and deployment
- **Model registry** -- version control and governance for ML models
- **Managed endpoints** -- deploy models as real-time REST APIs or batch inference pipelines
- **Responsible AI** -- model explainability, fairness, and error analysis dashboards
- **AutoML** -- automated model selection and hyperparameter tuning
- **Notebook-based experimentation** -- Jupyter notebooks with managed compute instances

Prefer Azure ML over Azure OpenAI when you need custom model training on your own data. Use Azure OpenAI for pre-trained language models (GPT, embeddings). Use Azure Databricks when ML is part of a larger data engineering and analytics platform.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Basic | No SLA; sufficient for experimentation |
| Compute instance | Standard_DS3_v2 | 4 vCores, 14 GiB RAM; for notebooks/dev |
| Compute cluster | Standard_DS3_v2, 0-2 nodes | Scale to 0 when idle to minimize cost |
| Storage account | Required | Workspace default storage for datasets and artifacts |
| Key Vault | Required | Workspace secrets management |
| Application Insights | Required | Experiment and endpoint monitoring |
| Container Registry | Optional | Created on first model deployment |
| Public network access | Enabled | Flag private endpoint as production backlog item |
| Managed identity | System-assigned (workspace) | Plus user-assigned for compute if needed |

## Terraform Patterns

### Basic Resource

```hcl
# Prerequisites: Storage Account, Key Vault, App Insights must exist
resource "azapi_resource" "ml_workspace" {
  type      = "Microsoft.MachineLearningServices/workspaces@2024-04-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type = "SystemAssigned"
  }

  body = {
    sku = {
      name = "Basic"
      tier = "Basic"
    }
    properties = {
      friendlyName        = var.friendly_name
      storageAccount      = var.storage_account_id
      keyVault            = var.key_vault_id
      applicationInsights = var.app_insights_id
      containerRegistry   = null  # Created on first model deployment
      publicNetworkAccess = "Enabled"  # Disable for production
      v1LegacyMode        = false
    }
  }

  tags = var.tags

  response_export_values = ["properties.workspaceId", "properties.discoveryUrl"]
}
```

### Compute Instance (Dev/Notebook)

```hcl
resource "azapi_resource" "compute_instance" {
  type      = "Microsoft.MachineLearningServices/workspaces/computes@2024-04-01"
  name      = var.compute_instance_name
  location  = var.location
  parent_id = azapi_resource.ml_workspace.id

  body = {
    properties = {
      computeType = "ComputeInstance"
      properties = {
        vmSize                       = "Standard_DS3_v2"
        enableNodePublicIp           = false
        idleTimeBeforeShutdown       = "PT30M"  # Auto-shutdown after 30 min idle
      }
    }
  }

  tags = var.tags
}
```

### Compute Cluster (Training)

```hcl
resource "azapi_resource" "compute_cluster" {
  type      = "Microsoft.MachineLearningServices/workspaces/computes@2024-04-01"
  name      = var.cluster_name
  location  = var.location
  parent_id = azapi_resource.ml_workspace.id

  body = {
    properties = {
      computeType = "AmlCompute"
      properties = {
        vmSize           = "Standard_DS3_v2"
        vmPriority       = "LowPriority"  # Cost savings for POC
        scaleSettings = {
          maxNodeCount                = 2
          minNodeCount                = 0  # Scale to 0 when idle
          nodeIdleTimeBeforeScaleDown = "PT5M"
        }
        enableNodePublicIp = false
      }
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# AzureML Data Scientist -- run experiments, manage models, submit jobs
resource "azapi_resource" "ml_data_scientist_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.ml_workspace.id}${var.managed_identity_principal_id}ml-data-scientist")
  parent_id = azapi_resource.ml_workspace.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/f6c7c914-8db3-469d-8ca1-694a8f32e121"  # AzureML Data Scientist
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Workspace identity needs access to storage, key vault, and ACR
resource "azapi_resource" "ml_storage_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.storage_account_id}${azapi_resource.ml_workspace.identity[0].principal_id}storage-blob-contributor")
  parent_id = var.storage_account_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"  # Storage Blob Data Contributor
      principalId      = azapi_resource.ml_workspace.identity[0].principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the ML workspace')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Friendly display name')
param friendlyName string = name

@description('Storage account resource ID')
param storageAccountId string

@description('Key Vault resource ID')
param keyVaultId string

@description('Application Insights resource ID')
param applicationInsightsId string

@description('Tags to apply')
param tags object = {}

resource mlWorkspace 'Microsoft.MachineLearningServices/workspaces@2024-04-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'Basic'
    tier: 'Basic'
  }
  properties: {
    friendlyName: friendlyName
    storageAccount: storageAccountId
    keyVault: keyVaultId
    applicationInsights: applicationInsightsId
    publicNetworkAccess: 'Enabled'
    v1LegacyMode: false
  }
}

output id string = mlWorkspace.id
output name string = mlWorkspace.name
output workspaceId string = mlWorkspace.properties.workspaceId
output principalId string = mlWorkspace.identity.principalId
```

### RBAC Assignment

```bicep
@description('Principal ID of the user or service principal')
param principalId string

// AzureML Data Scientist
resource mlDataScientistRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(mlWorkspace.id, principalId, 'f6c7c914-8db3-469d-8ca1-694a8f32e121')
  scope: mlWorkspace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'f6c7c914-8db3-469d-8ca1-694a8f32e121')  // AzureML Data Scientist
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Fix |
|---------|--------|-----|
| Forgetting prerequisite resources | Workspace creation fails without Storage, Key Vault, App Insights | Create all three dependencies before the workspace |
| Compute left running | Compute instances charge per hour even when idle | Enable auto-shutdown (`idleTimeBeforeShutdown`) on compute instances |
| Using dedicated VMs for POC | Unnecessary cost for intermittent training | Use `LowPriority` VMs and scale-to-zero for training clusters |
| Not registering models | Trained models lost, no version control | Register models in the workspace model registry after training |
| Missing workspace identity RBAC | Workspace cannot access storage, key vault, or ACR | Grant workspace system-assigned identity roles on dependent resources |
| Public compute with sensitive data | Data exposed on public network | Disable `enableNodePublicIp` on compute instances and clusters |
| Large datasets in workspace storage | Slow upload, high storage costs | Use Azure Data Lake Storage and register as a datastore |

## Production Backlog Items

- [ ] Enable private endpoint and disable public network access
- [ ] Configure managed VNet for workspace (workspace-managed VNet isolation)
- [ ] Set up compute quotas and budgets to prevent cost overruns
- [ ] Enable diagnostic logging to Log Analytics workspace
- [ ] Configure model registry with approval workflows
- [ ] Set up CI/CD pipelines for MLOps (train, evaluate, deploy)
- [ ] Enable customer managed keys for encryption at rest
- [ ] Configure data access governance with workspace datastores
- [ ] Review and right-size compute SKUs based on training workload profiles
- [ ] Set up monitoring alerts (training job failures, endpoint latency, drift detection)
- [ ] Implement model monitoring for data drift and prediction quality
