# Azure OpenAI Service
> Managed deployment of OpenAI language models (GPT-4o, GPT-4, GPT-3.5, DALL-E, Whisper, text-embedding) with Azure enterprise security, compliance, and regional availability.

## When to Use

- **Conversational AI** -- chatbots, virtual assistants, customer support automation
- **Content generation** -- summarization, translation, document drafting, code generation
- **RAG (Retrieval-Augmented Generation)** -- combine with Azure AI Search for grounded answers from your data
- **Embeddings** -- semantic search, document similarity, clustering, recommendations
- **Image generation** -- DALL-E for creative and design workflows
- **Audio transcription** -- Whisper for speech-to-text

Prefer Azure OpenAI over direct OpenAI API when you need: data residency guarantees, VNet/private endpoint access, Azure RBAC, content filtering, or integration with Azure AI Search for on-your-data scenarios.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Account kind | OpenAI | `kind = "OpenAI"` on Cognitive Services account |
| SKU | S0 | Standard tier; only option for OpenAI |
| Model | gpt-4o (2024-08-06) | Best quality/cost balance for POC |
| Embedding model | text-embedding-3-small | Lower cost than large variant |
| Deployment type | Standard | Global-Standard for higher rate limits |
| Tokens per minute | 10K-30K TPM | Start low for POC; increase as needed |
| Content filter | Default | Microsoft managed; customize if needed |
| Authentication | AAD (RBAC) | Disable API keys when possible |
| Public network access | Enabled | Flag private endpoint as production backlog item |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "openai" {
  type      = "Microsoft.CognitiveServices/accounts@2024-04-01-preview"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    kind = "OpenAI"
    sku = {
      name = "S0"
    }
    properties = {
      customSubDomainName  = var.custom_subdomain  # Required; must be globally unique
      publicNetworkAccess  = "Enabled"  # Disable for production
      disableLocalAuth     = true       # CRITICAL: Disable API keys, enforce AAD
      networkAcls = {
        defaultAction = "Allow"  # Change to "Deny" with private endpoint
      }
    }
  }

  tags = var.tags

  response_export_values = ["properties.endpoint"]
}
```

### Model Deployment

```hcl
resource "azapi_resource" "gpt4o_deployment" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview"
  name      = "gpt-4o"
  parent_id = azapi_resource.openai.id

  body = {
    sku = {
      name     = "Standard"
      capacity = 10  # Thousands of tokens per minute (10K TPM)
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-4o"
        version = "2024-08-06"
      }
      raiPolicyName = "Microsoft.DefaultV2"
    }
  }
}

resource "azapi_resource" "embedding_deployment" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview"
  name      = "text-embedding-3-small"
  parent_id = azapi_resource.openai.id

  body = {
    sku = {
      name     = "Standard"
      capacity = 30  # 30K TPM for embeddings
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = "text-embedding-3-small"
        version = "1"
      }
    }
  }

  depends_on = [azapi_resource.gpt4o_deployment]  # Deploy sequentially to avoid conflicts
}
```

### RBAC Assignment

```hcl
# Cognitive Services OpenAI User -- invoke models (chat, completions, embeddings)
resource "azapi_resource" "openai_user_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.openai.id}${var.managed_identity_principal_id}openai-user")
  parent_id = azapi_resource.openai.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"  # Cognitive Services OpenAI User
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Cognitive Services OpenAI Contributor -- manage deployments + invoke
resource "azapi_resource" "openai_contributor_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.openai.id}${var.managed_identity_principal_id}openai-contributor")
  parent_id = azapi_resource.openai.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/a001fd3d-188f-4b5d-821b-7da978bf7442"  # Cognitive Services OpenAI Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Azure OpenAI account')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Custom subdomain name (must be globally unique)')
param customSubDomainName string

@description('Tags to apply')
param tags object = {}

resource openai 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: name
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: customSubDomainName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

resource gpt4oDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: openai
  name: 'gpt-4o'
  sku: {
    name: 'Standard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o'
      version: '2024-08-06'
    }
    raiPolicyName: 'Microsoft.DefaultV2'
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: openai
  name: 'text-embedding-3-small'
  sku: {
    name: 'Standard'
    capacity: 30
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-small'
      version: '1'
    }
  }
  dependsOn: [gpt4oDeployment]
}

output id string = openai.id
output name string = openai.name
output endpoint string = openai.properties.endpoint
```

### RBAC Assignment

```bicep
@description('Principal ID of the managed identity')
param principalId string

// Cognitive Services OpenAI User -- invoke models
resource openaiUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openai.id, principalId, '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
  scope: openai
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')  // Cognitive Services OpenAI User
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Fix |
|---------|--------|-----|
| Missing `customSubDomainName` | Account creation fails; required for token-based auth | Set a globally unique subdomain name |
| Using API keys instead of AAD | Secrets in config, key rotation burden | Set `disableLocalAuth = true`, assign Cognitive Services OpenAI User role |
| Deploying models in parallel | ARM conflicts when multiple deployments target the same account simultaneously | Use `depends_on` to serialize model deployments |
| Exceeding TPM quota | Requests throttled (HTTP 429) | Start with conservative TPM, request quota increases as needed |
| Wrong model region availability | Not all models are available in all regions | Check [model availability matrix](https://learn.microsoft.com/azure/ai-services/openai/concepts/models) before selecting region |
| Content filter blocking legitimate requests | Requests rejected by default content filter | Review and customize content filtering policies if needed |
| Not using structured outputs | JSON parsing failures from unstructured responses | Use `response_format: { type: "json_object" }` or function calling for reliable structured output |

## Production Backlog Items

- [ ] Enable private endpoint and disable public network access
- [ ] Review and customize content filtering policies
- [ ] Configure diagnostic logging to Log Analytics workspace
- [ ] Set up monitoring alerts (token usage, throttling rate, error rate)
- [ ] Request production-level TPM quota increases
- [ ] Implement retry logic with exponential backoff in application code
- [ ] Configure customer managed keys for encryption at rest
- [ ] Set up model version pinning and upgrade schedule
- [ ] Review data, privacy, and abuse monitoring settings
- [ ] Consider provisioned throughput for predictable latency at scale
