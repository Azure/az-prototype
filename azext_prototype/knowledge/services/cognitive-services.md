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
| Public network access | Disabled (unless user overrides) | Flag private endpoint as production backlog item |
| Local auth | Disabled | Use AAD authentication via managed identity |

**CRITICAL:** Model deployments are **separate resources** from the Cognitive Services account. Creating the account alone does not give you a usable model -- you must also deploy one or more models.

**CRITICAL:** Regional availability varies significantly by model. Not all models are available in all regions. Check [Azure OpenAI model availability](https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models) before selecting a region.

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "openai" {
  type      = "Microsoft.CognitiveServices/accounts@2024-10-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type = "SystemAssigned"
  }

  body = {
    kind = "OpenAI"
    sku = {
      name = "S0"
    }
    properties = {
      customSubDomainName = var.name  # Required for token-based auth
      publicNetworkAccess = "Disabled"  # Unless told otherwise, disabled per governance policy
      disableLocalAuth    = true        # CRITICAL: Disable key-based auth
    }
  }

  tags = var.tags

  response_export_values = ["properties.endpoint"]
}

# Model deployment -- CRITICAL: separate resource
resource "azapi_resource" "gpt4o" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2024-10-01"
  name      = "gpt-4o"
  parent_id = azapi_resource.openai.id

  body = {
    sku = {
      name     = "Standard"
      capacity = 10  # Thousands of tokens per minute (TPM)
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-4o"
        version = "2024-11-20"
      }
    }
  }
}

# Embeddings deployment
resource "azapi_resource" "embeddings" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2024-10-01"
  name      = "text-embedding-3-small"
  parent_id = azapi_resource.openai.id

  body = {
    sku = {
      name     = "Standard"
      capacity = 120  # TPM
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = "text-embedding-3-small"
        version = "1"
      }
    }
  }

  depends_on = [azapi_resource.gpt4o]  # Deploy sequentially to avoid conflicts
}
```

### RBAC Assignment

```hcl
# Cognitive Services User -- invoke models (inference)
resource "azapi_resource" "openai_user_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.openai.id}${var.managed_identity_principal_id}cs-user")
  parent_id = azapi_resource.openai.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/a97b65f3-24c7-4388-baec-2e87135dc908"  # Cognitive Services User
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Cognitive Services Contributor -- manage deployments and account settings
resource "azapi_resource" "openai_contributor_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.openai.id}${var.admin_identity_principal_id}cs-contributor")
  parent_id = azapi_resource.openai.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/25fbc0a9-bd7c-42a3-aa1a-3b75d497ee68"  # Cognitive Services Contributor
      principalId      = var.admin_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}

# Cognitive Services OpenAI User -- specific to OpenAI operations (alternative to generic User)
resource "azapi_resource" "openai_specific_user_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.openai.id}${var.managed_identity_principal_id}cs-openai-user")
  parent_id = azapi_resource.openai.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"  # Cognitive Services OpenAI User
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

RBAC role IDs:
- Cognitive Services User: `a97b65f3-24c7-4388-baec-2e87135dc908`
- Cognitive Services Contributor: `25fbc0a9-bd7c-42a3-aa1a-3b75d497ee68`
- Cognitive Services OpenAI User: `5e0bd9bd-7b93-4f28-af87-19fc36ad61bd`

### Private Endpoint

```hcl
resource "azapi_resource" "private_endpoint" {
  count     = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints@2023-11-01"
  name      = "pe-${var.name}"
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      subnet = {
        id = var.subnet_id
      }
      privateLinkServiceConnections = [
        {
          name = "psc-${var.name}"
          properties = {
            privateLinkServiceId = azapi_resource.openai.id
            groupIds             = ["account"]
          }
        }
      ]
    }
  }

  tags = var.tags
}

resource "azapi_resource" "dns_zone_group" {
  count     = var.enable_private_endpoint && var.subnet_id != null && var.private_dns_zone_id != null ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01"
  name      = "dns-zone-group"
  parent_id = azapi_resource.private_endpoint[0].id

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "config"
          properties = {
            privateDnsZoneId = var.private_dns_zone_id
          }
        }
      ]
    }
  }
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
    publicNetworkAccess: 'Disabled'  // Unless told otherwise, disabled per governance policy
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
