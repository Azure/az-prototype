---
service_namespace: Microsoft.CognitiveServices/accounts/deployments
display_name: Cognitive Services / OpenAI Model Deployment
depends_on:
  - Microsoft.CognitiveServices/accounts
---

# Cognitive Services Model Deployment

> Deploys a specific AI model (GPT-4, GPT-3.5-turbo, text-embedding-ada-002, etc.) within a Cognitive Services or Azure OpenAI account.

## When to Use
- Every Azure OpenAI application needs at least one model deployment
- Deploy different models for different tasks (chat, embeddings, completions)
- Control capacity allocation per model via TPM (tokens per minute)

## POC Defaults
- **Model**: gpt-4o or gpt-4o-mini for chat; text-embedding-3-small for embeddings
- **Capacity**: 10K TPM (tokens per minute) — sufficient for POC
- **SKU**: Standard

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "openai_deployment" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2024-10-01"
  name      = var.deployment_name
  parent_id = azapi_resource.openai_account.id

  body = {
    sku = {
      name     = "Standard"
      capacity = 10
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.model_name      # e.g., "gpt-4o"
        version = var.model_version   # e.g., "2024-08-06"
      }
    }
  }
}
```

### RBAC Assignment
```hcl
# Model deployment access is granted at the account level:
# Cognitive Services OpenAI User: 5e0bd9bd-7b93-4f28-af87-19fc36ad61bd
# Cognitive Services OpenAI Contributor: a001fd3d-188f-4b5d-821b-7da978bf7442
```

## Bicep Patterns

### Basic Resource
```bicep
param deploymentName string
param modelName string
param modelVersion string

resource deployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openaiAccount
  name: deploymentName
  sku: {
    name: 'Standard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
  }
}
```

## Application Code

### Python
```python
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

credential = DefaultAzureCredential()
token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")

client = AzureOpenAI(
    azure_endpoint="https://<account>.openai.azure.com/",
    azure_ad_token_provider=token_provider,
    api_version="2024-08-01-preview"
)

response = client.chat.completions.create(
    model=deployment_name,
    messages=[{"role": "user", "content": "Hello"}]
)
print(response.choices[0].message.content)
```

### C#
```csharp
using Azure.Identity;
using Azure.AI.OpenAI;

var credential = new DefaultAzureCredential();
var client = new AzureOpenAIClient(
    new Uri("https://<account>.openai.azure.com/"), credential);

var chatClient = client.GetChatClient(deploymentName);
var response = await chatClient.CompleteChatAsync(
    new[] { new UserChatMessage("Hello") });
Console.WriteLine(response.Value.Content[0].Text);
```

### Node.js
```typescript
import { AzureOpenAI } from "openai";
import { DefaultAzureCredential, getBearerTokenProvider } from "@azure/identity";

const credential = new DefaultAzureCredential();
const tokenProvider = getBearerTokenProvider(credential, "https://cognitiveservices.azure.com/.default");

const client = new AzureOpenAI({
  azureADTokenProvider: tokenProvider,
  endpoint: "https://<account>.openai.azure.com/",
  apiVersion: "2024-08-01-preview",
});

const response = await client.chat.completions.create({
  model: deploymentName,
  messages: [{ role: "user", content: "Hello" }],
});
console.log(response.choices[0].message.content);
```

## Common Pitfalls
- **Model availability varies by region**: Not all models are available in all regions. Check regional availability before deployment.
- **Capacity is shared per model**: TPM capacity is shared across all deployments of the same model in the same account.
- **Deployment name != model name**: The deployment name is user-defined; the model name is the Azure-internal model identifier (e.g., `gpt-4o`).
- **API version matters**: Different API versions support different features. Use the latest stable version.

## Production Backlog Items
- Content filtering configuration for responsible AI
- Provisioned throughput for guaranteed capacity
- Multiple deployments for A/B testing different models
- Rate limiting and quota management
