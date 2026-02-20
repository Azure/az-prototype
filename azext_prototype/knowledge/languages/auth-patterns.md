# Shared Authentication Patterns

This document contains standard authentication patterns for Azure SDK usage with Managed Identity. **All agents should reference these patterns when generating application code.**

Ported from the Innovation Factory `SHARED_AUTH_PATTERNS.md` for use by the `az prototype` CLI extension.

## Credential Creation

### C# / .NET
```csharp
using Azure.Identity;

// For User-Assigned Managed Identity (PREFERRED in production)
var credential = new ManagedIdentityCredential("<client-id>");

// For default credential chain (works locally with Azure CLI)
var credential = new DefaultAzureCredential();

// With explicit options
var credential = new DefaultAzureCredential(new DefaultAzureCredentialOptions
{
    ManagedIdentityClientId = "<client-id>"
});
```

### Python
```python
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

# For User-Assigned Managed Identity (PREFERRED in production)
credential = ManagedIdentityCredential(client_id="<client-id>")

# For default credential chain (works locally with Azure CLI)
credential = DefaultAzureCredential()

# With explicit managed identity client ID
credential = DefaultAzureCredential(managed_identity_client_id="<client-id>")
```

### Node.js / TypeScript
```typescript
import { DefaultAzureCredential, ManagedIdentityCredential } from "@azure/identity";

// For User-Assigned Managed Identity (PREFERRED in production)
const credential = new ManagedIdentityCredential("<client-id>");

// For default credential chain (works locally with Azure CLI)
const credential = new DefaultAzureCredential();

// With explicit options
const credential = new DefaultAzureCredential({
  managedIdentityClientId: "<client-id>"
});
```

## Configuration Pattern

Store configuration WITHOUT secrets:

```json
{
  "ServiceName": {
    "Endpoint": "https://<resource-name>.<service>.azure.com"
  },
  "ManagedIdentity": {
    "ClientId": "<user-assigned-managed-identity-client-id>"
  }
}
```

### Environment Variables Alternative
```bash
AZURE_CLIENT_ID=<user-assigned-managed-identity-client-id>
SERVICE_ENDPOINT=https://<resource-name>.<service>.azure.com
```

## Required Packages

### .NET (NuGet)
```xml
<!-- Core identity package (required for all Azure SDK usage) -->
<PackageReference Include="Azure.Identity" Version="1.13.2" />

<!-- Service-specific packages - add as needed -->
<PackageReference Include="Azure.Storage.Blobs" Version="12.23.0" />
<PackageReference Include="Azure.Security.KeyVault.Secrets" Version="4.7.0" />
<PackageReference Include="Microsoft.Azure.Cosmos" Version="3.43.1" />
<PackageReference Include="Azure.Messaging.ServiceBus" Version="7.18.3" />
<PackageReference Include="Azure.AI.OpenAI" Version="2.1.0" />
```

### Python (pip)
```text
# Core identity package (required for all Azure SDK usage)
azure-identity>=1.19.0

# Service-specific packages - add as needed
azure-storage-blob>=12.24.0
azure-keyvault-secrets>=4.9.0
azure-cosmos>=4.9.0
azure-servicebus>=7.13.0
openai>=1.58.0
```

### Node.js (npm)
```json
{
  "@azure/identity": "^4.5.0",
  "@azure/storage-blob": "^12.26.0",
  "@azure/keyvault-secrets": "^4.9.0",
  "@azure/cosmos": "^4.2.0",
  "@azure/service-bus": "^7.10.0",
  "openai": "^4.77.0"
}
```

## Dependency Injection Patterns

### .NET Dependency Injection
```csharp
// In Program.cs or Startup.cs
services.AddSingleton<TokenCredential>(sp =>
{
    var config = sp.GetRequiredService<IConfiguration>();
    var clientId = config["ManagedIdentity:ClientId"];

    return string.IsNullOrEmpty(clientId)
        ? new DefaultAzureCredential()
        : new ManagedIdentityCredential(clientId);
});

// Then inject into services
services.AddSingleton<IMyService>(sp =>
{
    var credential = sp.GetRequiredService<TokenCredential>();
    var endpoint = sp.GetRequiredService<IConfiguration>()["ServiceName:Endpoint"];
    return new MyService(endpoint, credential);
});
```

### Python Dependency Pattern
```python
from functools import lru_cache
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
import os

@lru_cache()
def get_credential():
    client_id = os.getenv("AZURE_CLIENT_ID")
    if client_id:
        return ManagedIdentityCredential(client_id=client_id)
    return DefaultAzureCredential()
```

### Node.js / TypeScript Pattern
```typescript
import { TokenCredential, DefaultAzureCredential, ManagedIdentityCredential } from "@azure/identity";

let credentialInstance: TokenCredential | null = null;

export function getCredential(): TokenCredential {
  if (!credentialInstance) {
    const clientId = process.env.AZURE_CLIENT_ID;
    credentialInstance = clientId
      ? new ManagedIdentityCredential(clientId)
      : new DefaultAzureCredential();
  }
  return credentialInstance;
}
```

## Error Handling

### Common Authentication Errors

| Error | Cause | Resolution |
|-------|-------|------------|
| `CredentialUnavailableException` | No valid credential found | Verify Managed Identity is assigned to resource |
| `AuthenticationFailedException` | Token acquisition failed | Check RBAC role assignment |
| 401 Unauthorized | Missing or invalid token | Verify identity has required RBAC role |
| 403 Forbidden | Insufficient permissions | Assign appropriate RBAC role |

### Retry Pattern (C#)
```csharp
try
{
    // Azure SDK operations
}
catch (RequestFailedException ex) when (ex.Status == 401)
{
    _logger.LogError("Authentication failed. Verify Managed Identity has required RBAC role.");
    throw;
}
catch (RequestFailedException ex) when (ex.Status == 403)
{
    _logger.LogError("Authorization failed. Check RBAC role assignments.");
    throw;
}
```

### Retry Pattern (Python)
```python
from azure.core.exceptions import HttpResponseError

try:
    # Azure SDK operations
    pass
except HttpResponseError as e:
    if e.status_code == 401:
        logger.error("Authentication failed. Verify Managed Identity has required RBAC role.")
        raise
    elif e.status_code == 403:
        logger.error("Authorization failed. Check RBAC role assignments.")
        raise
    raise
```

### Retry Pattern (Node.js / TypeScript)
```typescript
import { RestError } from "@azure/core-rest-pipeline";

try {
  // Azure SDK operations
} catch (error) {
  if (error instanceof RestError) {
    if (error.statusCode === 401) {
      logger.error("Authentication failed. Verify Managed Identity has required RBAC role.");
      throw error;
    }
    if (error.statusCode === 403) {
      logger.error("Authorization failed. Check RBAC role assignments.");
      throw error;
    }
  }
  throw error;
}
```

## Local Development

For local development, `DefaultAzureCredential` falls back through:
1. Environment variables
2. Managed Identity (if running in Azure)
3. Visual Studio credential
4. Azure CLI credential (`az login`)
5. Azure PowerShell credential

**Recommended local setup:**
```bash
# Login with Azure CLI
az login

# Set subscription (if multiple)
az account set --subscription "<subscription-id>"
```

## Token Scopes Reference

| Service | Token Scope |
|---------|-------------|
| Azure Storage | `https://storage.azure.com/.default` |
| Azure SQL | `https://database.windows.net/.default` |
| Key Vault | `https://vault.azure.net/.default` |
| Cosmos DB | `https://cosmos.azure.com/.default` |
| Service Bus | `https://servicebus.azure.net/.default` |
| Event Hubs | `https://eventhubs.azure.net/.default` |
| Azure OpenAI | `https://cognitiveservices.azure.com/.default` |
| Microsoft Graph | `https://graph.microsoft.com/.default` |
| Azure Resource Manager | `https://management.azure.com/.default` |
