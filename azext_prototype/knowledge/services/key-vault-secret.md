---
service_namespace: Microsoft.KeyVault/vaults/secrets
display_name: Key Vault Secret
depends_on:
  - Microsoft.KeyVault/vaults
---

# Key Vault Secret

> A named secret value stored in Azure Key Vault. Used for external credentials, connection strings, and configuration that cannot use managed identity.

## When to Use
- Storing third-party API keys, external service credentials
- Connection strings for services that don't support managed identity (e.g., Redis connection strings, SignalR connection strings)
- Configuration values that must be rotatable without redeployment

## POC Defaults
- **Content type**: `text/plain` for simple strings; `application/x-pkcs12` for certificates
- **Enabled**: true
- **Expiration**: Not set for POC (set rotation policy for production)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "kv_secret" {
  type      = "Microsoft.KeyVault/vaults/secrets@2023-07-01"
  name      = var.secret_name
  parent_id = azapi_resource.key_vault.id

  body = {
    properties = {
      value       = var.secret_value
      contentType = "text/plain"
    }
  }
}
```

### RBAC Assignment
```hcl
# Secret access is granted at the vault level via RBAC:
# Key Vault Secrets User (read): 4633458b-17de-408a-b874-0445c86b69e6
# Key Vault Secrets Officer (read/write): b86a8fe4-44ce-4948-aee5-eccb2c155cd7
# See the key-vault knowledge file for role assignment patterns.
```

## Bicep Patterns

### Basic Resource
```bicep
param secretName string
@secure()
param secretValue string

resource secret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: secretName
  properties: {
    value: secretValue
    contentType: 'text/plain'
  }
}

output secretUri string = secret.properties.secretUri
output secretName string = secret.name
```

## Application Code

### Python
```python
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
client = SecretClient(vault_url="https://<vault>.vault.azure.net/", credential=credential)

# Read secret
secret = client.get_secret("my-secret")
print(secret.value)

# Set secret
client.set_secret("my-secret", "new-value")
```

### C#
```csharp
using Azure.Identity;
using Azure.Security.KeyVault.Secrets;

var credential = new DefaultAzureCredential();
var client = new SecretClient(new Uri("https://<vault>.vault.azure.net/"), credential);

// Read
KeyVaultSecret secret = await client.GetSecretAsync("my-secret");
Console.WriteLine(secret.Value);

// Set
await client.SetSecretAsync("my-secret", "new-value");
```

### Node.js
```typescript
import { SecretClient } from "@azure/keyvault-secrets";
import { DefaultAzureCredential } from "@azure/identity";

const credential = new DefaultAzureCredential();
const client = new SecretClient("https://<vault>.vault.azure.net/", credential);

// Read
const secret = await client.getSecret("my-secret");
console.log(secret.value);

// Set
await client.setSecret("my-secret", "new-value");
```

## Common Pitfalls
- **Secret values in Terraform state**: Secret values stored via Terraform are visible in the state file. Mark the variable as `sensitive` and consider using a deploy-time script instead.
- **Soft delete and purge protection**: Deleted secrets remain recoverable for the retention period. You cannot reuse a secret name until purged or the retention period expires.
- **Secret URI vs value**: `secretUri` is the reference (safe to store in config). `value` is the actual secret (never log or output it).
- **Container Apps Key Vault references**: Use `secretRef` with the Key Vault secret URI, not direct environment variable values.

## Production Backlog Items
- Automatic rotation policy with rotation event trigger
- Expiration dates with monitoring alerts
- Secret versioning and rollback procedures
- Access logging and anomaly detection via diagnostic settings
