---
service_namespace: Microsoft.KeyVault/vaults/keys
display_name: Key Vault Key
depends_on:
  - Microsoft.KeyVault/vaults
---

# Key Vault Key

> A cryptographic key stored in Azure Key Vault. Used for encryption, signing, and wrapping operations — typically for customer-managed key (CMK) scenarios.

## When to Use
- Customer-managed encryption keys for storage, SQL, Cosmos DB, or disk encryption
- Application-level encryption/decryption operations
- Digital signature verification

## POC Defaults
- **Key type**: RSA (2048-bit)
- **Operations**: encrypt, decrypt, wrapKey, unwrapKey
- **Not typically needed for POC**: Service-managed keys are sufficient. Only create keys when CMK is a requirement.

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "kv_key" {
  type      = "Microsoft.KeyVault/vaults/keys@2023-07-01"
  name      = var.key_name
  parent_id = azapi_resource.key_vault.id

  body = {
    properties = {
      kty     = "RSA"
      keySize = 2048
      keyOps  = ["encrypt", "decrypt", "wrapKey", "unwrapKey"]
    }
  }

  response_export_values = ["*"]
}
```

### RBAC Assignment
```hcl
# Key access is granted at the vault level via RBAC:
# Key Vault Crypto User (use keys): 12338af0-0e69-4776-bea7-57ae8d297424
# Key Vault Crypto Officer (manage keys): 14b46e9e-c2b7-41b4-b07b-48a6ebf60603
```

## Bicep Patterns

### Basic Resource
```bicep
param keyName string

resource key 'Microsoft.KeyVault/vaults/keys@2023-07-01' = {
  parent: keyVault
  name: keyName
  properties: {
    kty: 'RSA'
    keySize: 2048
    keyOps: ['encrypt', 'decrypt', 'wrapKey', 'unwrapKey']
  }
}

output keyId string = key.id
output keyUri string = key.properties.keyUri
output keyUriWithVersion string = key.properties.keyUriWithVersion
```

## Application Code

### Python
```python
from azure.keyvault.keys import KeyClient
from azure.keyvault.keys.crypto import CryptographyClient, EncryptionAlgorithm
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
key_client = KeyClient(vault_url="https://<vault>.vault.azure.net/", credential=credential)

key = key_client.get_key("my-key")
crypto_client = CryptographyClient(key.id, credential=credential)

# Encrypt
result = crypto_client.encrypt(EncryptionAlgorithm.rsa_oaep, b"plaintext")
ciphertext = result.ciphertext

# Decrypt
result = crypto_client.decrypt(EncryptionAlgorithm.rsa_oaep, ciphertext)
plaintext = result.plaintext
```

### C#
```csharp
using Azure.Identity;
using Azure.Security.KeyVault.Keys;
using Azure.Security.KeyVault.Keys.Cryptography;

var credential = new DefaultAzureCredential();
var keyClient = new KeyClient(new Uri("https://<vault>.vault.azure.net/"), credential);

var key = await keyClient.GetKeyAsync("my-key");
var cryptoClient = new CryptographyClient(key.Value.Id, credential);

// Encrypt
var encrypted = await cryptoClient.EncryptAsync(EncryptionAlgorithm.RsaOaep, plaintext);
// Decrypt
var decrypted = await cryptoClient.DecryptAsync(EncryptionAlgorithm.RsaOaep, encrypted.Ciphertext);
```

### Node.js
```typescript
import { KeyClient, CryptographyClient } from "@azure/keyvault-keys";
import { DefaultAzureCredential } from "@azure/identity";

const credential = new DefaultAzureCredential();
const keyClient = new KeyClient("https://<vault>.vault.azure.net/", credential);

const key = await keyClient.getKey("my-key");
const cryptoClient = new CryptographyClient(key.id!, credential);

// Encrypt
const encrypted = await cryptoClient.encrypt("RSA-OAEP", Buffer.from("plaintext"));
// Decrypt
const decrypted = await cryptoClient.decrypt("RSA-OAEP", encrypted.result);
```

## Common Pitfalls
- **Key type immutability**: Key type (RSA, EC) cannot be changed after creation. Create a new key if you need a different type.
- **Purge protection blocks recreation**: With purge protection enabled, deleted keys cannot be recreated with the same name until the retention period expires.
- **CMK rotation**: When rotating customer-managed keys, update all services that reference the key. Azure Storage handles this automatically; other services may not.

## Production Backlog Items
- Automatic key rotation with rotation policy
- HSM-backed keys for higher security (Premium SKU or Managed HSM)
- Key expiration monitoring and alerting
- Separate keys per service for blast radius reduction
