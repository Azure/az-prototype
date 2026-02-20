# Azure OpenAI Service / Cognitive Services
> Managed AI platform for deploying OpenAI models (GPT-4o, GPT-4, GPT-3.5, DALL-E, Whisper, embeddings) and Azure AI services with enterprise security and compliance.

## When to Use

- **Generative AI** -- text generation, summarization, translation, code generation via GPT models
- **Chat applications** -- conversational AI with system prompts, function calling, and structured outputs
- **Embeddings** -- vector representations for semantic search, RAG patterns, and similarity matching
- **Image generation** -- DALL-E models for image creation from text prompts
- **Speech** -- Whisper models for speech-to-text transcription
- **RAG (Retrieval-Augmented Generation)** -- combine with Azure AI Search for grounded responses

Azure OpenAI is the preferred path for enterprise AI workloads. It provides the same models as OpenAI with Azure's security, networking, and compliance guarantees.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Account kind | OpenAI | For Azure OpenAI models |
| Account SKU | S0 | Standard tier; all features available |
| Model deployment | Separate resource | CRITICAL: Model deployments are separate from the account |
| Default model | gpt-4o | Best balance of capability and cost for POC |
| Embeddings model | text-embedding-ada-002 | Or text-embedding-3-small for newer workloads |
| Public network access | Enabled (POC) | Flag private endpoint as production backlog item |
| Local auth | Disabled | Use AAD authentication via managed identity |

**CRITICAL:** Model deployments are **separate resources** from the Cognitive Services account. Creating the account alone does not give you a usable model -- you must also deploy one or more models.

**CRITICAL:** Regional availability varies significantly by model. Not all models are available in all regions. Check [Azure OpenAI model availability](https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models) before selecting a region.

## Terraform Patterns

### Basic Resource

```hcl
resource "azurerm_cognitive_account" "this" {
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  kind                          = "OpenAI"
  sku_name                      = "S0"
  custom_subdomain_name         = var.name  # Required for token-based auth
  public_network_access_enabled = true      # Set false when using private endpoint
  local_auth_enabled            = false     # CRITICAL: Disable key-based auth

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

# Model deployment -- CRITICAL: separate resource
resource "azurerm_cognitive_deployment" "gpt4o" {
  name                 = "gpt-4o"
  cognitive_account_id = azurerm_cognitive_account.this.id

  model {
    format  = "OpenAI"
    name    = "gpt-4o"
    version = "2024-11-20"
  }

  sku {
    name     = "Standard"
    capacity = 10  # Thousands of tokens per minute (TPM)
  }
}

# Embeddings deployment
resource "azurerm_cognitive_deployment" "embeddings" {
  name                 = "text-embedding-3-small"
  cognitive_account_id = azurerm_cognitive_account.this.id

  model {
    format  = "OpenAI"
    name    = "text-embedding-3-small"
    version = "1"
  }

  sku {
    name     = "Standard"
    capacity = 120  # TPM
  }
}
```

### RBAC Assignment

```hcl
# Cognitive Services User -- invoke models (inference)
resource "azurerm_role_assignment" "openai_user" {
  scope                = azurerm_cognitive_account.this.id
  role_definition_name = "Cognitive Services User"
  principal_id         = var.managed_identity_principal_id
}

# Cognitive Services Contributor -- manage deployments and account settings
resource "azurerm_role_assignment" "openai_contributor" {
  scope                = azurerm_cognitive_account.this.id
  role_definition_name = "Cognitive Services Contributor"
  principal_id         = var.admin_identity_principal_id
}

# Cognitive Services OpenAI User -- specific to OpenAI operations (alternative to generic User)
resource "azurerm_role_assignment" "openai_specific_user" {
  scope                = azurerm_cognitive_account.this.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = var.managed_identity_principal_id
}
```

RBAC role IDs:
- Cognitive Services User: `a97b65f3-24c7-4388-baec-2e87135dc908`
- Cognitive Services Contributor: `25fbc0a9-bd7c-42a3-aa1a-3b75d497ee68`
- Cognitive Services OpenAI User: `5e0bd9bd-7b93-4f28-af87-19fc36ad61bd`

### Private Endpoint

```hcl
resource "azurerm_private_endpoint" "openai" {
  count = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0

  name                = "pe-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id

  private_service_connection {
    name                           = "psc-${var.name}"
    private_connection_resource_id = azurerm_cognitive_account.this.id
    subresource_names              = ["account"]
    is_manual_connection           = false
  }

  dynamic "private_dns_zone_group" {
    for_each = var.private_dns_zone_id != null ? [1] : []
    content {
      name                 = "dns-zone-group"
      private_dns_zone_ids = [var.private_dns_zone_id]
    }
  }

  tags = var.tags
}
```

Private DNS zone: `privatelink.openai.azure.com`

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Azure OpenAI account')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Tags to apply')
param tags object = {}

resource openai 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'  // Set 'Disabled' when using private endpoint
    disableLocalAuth: true  // CRITICAL: Disable key-based auth
  }
}

// Model deployment -- CRITICAL: separate resource
resource gpt4o 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
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
      version: '2024-11-20'
    }
  }
}

resource embeddings 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openai
  name: 'text-embedding-3-small'
  sku: {
    name: 'Standard'
    capacity: 120
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-small'
      version: '1'
    }
  }
  dependsOn: [gpt4o]  // Deploy sequentially to avoid conflicts
}

output id string = openai.id
output name string = openai.name
output endpoint string = openai.properties.endpoint
output principalId string = openai.identity.principalId
```

### RBAC Assignment

```bicep
@description('Principal ID of the managed identity for model inference')
param principalId string

var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'

resource userRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openai.id, principalId, cognitiveServicesUserRoleId)
  scope: openai
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Application Code

### Python

```python
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

credential = DefaultAzureCredential(managed_identity_client_id="<client-id>")
token_provider = get_bearer_token_provider(
    credential, "https://cognitiveservices.azure.com/.default"
)

client = AzureOpenAI(
    azure_endpoint="https://myopenai.openai.azure.com",
    azure_ad_token_provider=token_provider,
    api_version="2024-10-21",
)

# Chat completion
response = client.chat.completions.create(
    model="gpt-4o",  # Deployment name, not model name
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"},
    ],
    temperature=0.7,
    max_tokens=1000,
)
print(response.choices[0].message.content)

# Embeddings
embedding_response = client.embeddings.create(
    model="text-embedding-3-small",  # Deployment name
    input="The quick brown fox jumps over the lazy dog",
)
vector = embedding_response.data[0].embedding
```

### C# / .NET

```csharp
using Azure.Identity;
using Azure.AI.OpenAI;
using OpenAI.Chat;

var credential = new DefaultAzureCredential(new DefaultAzureCredentialOptions
{
    ManagedIdentityClientId = "<client-id>"
});

var client = new AzureOpenAIClient(
    new Uri("https://myopenai.openai.azure.com"),
    credential
);

// Chat completion
var chatClient = client.GetChatClient("gpt-4o");  // Deployment name
var response = await chatClient.CompleteChatAsync(
    new List<ChatMessage>
    {
        new SystemChatMessage("You are a helpful assistant."),
        new UserChatMessage("Hello!")
    },
    new ChatCompletionOptions
    {
        Temperature = 0.7f,
        MaxOutputTokenCount = 1000
    }
);

Console.WriteLine(response.Value.Content[0].Text);
```

### Node.js

```typescript
import { AzureOpenAI } from "openai";
import { DefaultAzureCredential, getBearerTokenProvider } from "@azure/identity";

const credential = new DefaultAzureCredential({
  managedIdentityClientId: "<client-id>",
});

const scope = "https://cognitiveservices.azure.com/.default";
const azureADTokenProvider = getBearerTokenProvider(credential, scope);

const client = new AzureOpenAI({
  azureADTokenProvider,
  endpoint: "https://myopenai.openai.azure.com",
  apiVersion: "2024-10-21",
  deployment: "gpt-4o",
});

// Chat completion
const response = await client.chat.completions.create({
  model: "gpt-4o",
  messages: [
    { role: "system", content: "You are a helpful assistant." },
    { role: "user", content: "Hello!" },
  ],
  temperature: 0.7,
  max_tokens: 1000,
});

console.log(response.choices[0].message.content);
```

## Common Pitfalls

1. **Model deployments are separate resources** -- Creating the Cognitive Services account alone is not enough. You must explicitly deploy models (GPT-4o, embeddings, etc.) as child resources. Without deployments, API calls fail with 404.
2. **Regional availability** -- Not all models are available in all regions. GPT-4o may be available in East US but not West Europe. Always check model availability before choosing a region.
3. **Deployment name vs model name** -- In SDK calls, use the **deployment name** (the name you gave when deploying), not the model name. These can differ.
4. **Token scope** -- Always use `https://cognitiveservices.azure.com/.default` as the token scope, not a service-specific URL. This scope covers all Cognitive Services including OpenAI.
5. **Rate limiting (TPM)** -- Token-per-minute (TPM) limits are set per deployment. A capacity of 10 means 10K TPM. Exceeding the limit returns 429 errors. Implement retry with exponential backoff.
6. **Content filtering** -- Azure OpenAI applies content filtering by default. Requests or responses flagged by the content filter return 400 errors. This cannot be fully disabled.
7. **Custom subdomain required** -- The `custom_subdomain_name` property is required for AAD authentication. Without it, only key-based auth works (which is prohibited by governance policies).
8. **Sequential model deployments** -- In Bicep, deploy models sequentially (use `dependsOn`) to avoid conflicts. Concurrent deployment operations on the same account can fail.
9. **Quota limits** -- TPM quotas are shared per subscription per region. Multiple deployments in the same region share the same quota pool.

## Production Backlog Items

- [ ] Configure private endpoint and disable public network access
- [ ] Review and configure content filtering policies for the specific use case
- [ ] Implement rate limiting and retry logic in application code
- [ ] Set up provisioned throughput (PTU) for predictable latency and cost
- [ ] Configure monitoring alerts for token usage, latency, and error rates
- [ ] Implement prompt caching and response caching where appropriate
- [ ] Set up logging for audit and compliance (Azure Monitor diagnostic settings)
- [ ] Review model versions and plan for model version upgrades
- [ ] Implement fallback logic for multi-region deployments (handle regional outages)
- [ ] Configure network ACLs to restrict access to known IP ranges or VNets
