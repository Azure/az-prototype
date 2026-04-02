# Azure Key Vault

> Centralized secrets management, key management, and certificate management with hardware security module (HSM) backing.

## When to Use
- Storing and managing application secrets, connection strings, and API keys
- Managing encryption keys for data-at-rest encryption across Azure services
- Provisioning and managing TLS/SSL certificates

## POC Defaults
- **SKU**: Standard (HSM-backed keys available in Premium)
- **Authorization mode**: RBAC (`enable_rbac_authorization = true`)
- **Purge protection**: Enabled (required by many Azure services, cannot be disabled once enabled)
- **Soft delete**: Enabled with 90-day retention (enabled by default, cannot be disabled)

## Terraform Patterns

### Basic Resource
```hcl
data "azurerm_client_config" "current" {}

resource "azapi_resource" "key_vault" {
  type      = "Microsoft.KeyVault/vaults@2023-07-01"
  name      = var.key_vault_name
  location  = azapi_resource.resource_group.output.location
  parent_id = azapi_resource.resource_group.id

  body = {
    properties = {
      tenantId                 = data.azurerm_client_config.current.tenant_id
      sku = {
        family = "A"
        name   = "standard"
      }
      enableRbacAuthorization  = true    # CRITICAL: Use RBAC, NOT access policies
      enablePurgeProtection    = true
      enableSoftDelete         = true
      softDeleteRetentionInDays = 90
      networkAcls = {
        bypass        = "AzureServices"
        defaultAction = "Allow"          # Restrict to "Deny" for production
      }
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}

resource "azapi_resource" "key_vault_secret" {
  type      = "Microsoft.KeyVault/vaults/secrets@2023-07-01"
  name      = "example-secret"
  parent_id = azapi_resource.key_vault.id

  body = {
    properties = {
      value = var.secret_value
    }
  }

  depends_on = [azapi_resource.kv_secrets_officer_deployer]
}
```

### RBAC Assignment
```hcl
# Role IDs from service-registry.yaml:
#   Key Vault Secrets User:    4633458b-17de-408a-b874-0445c86b69e6
#   Key Vault Secrets Officer: b86a8fe4-44ce-4948-aee5-eccb2c155cd7
#   Key Vault Administrator:   00482a5a-887f-4fb3-b363-3b7fe8e74483

# Grant the app's managed identity read access to secrets
resource "azapi_resource" "kv_secrets_user" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("sha1", "${azapi_resource.key_vault.id}-${azapi_resource.user_assigned_identity.output.properties.principalId}-4633458b-17de-408a-b874-0445c86b69e6")
  parent_id = azapi_resource.key_vault.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/4633458b-17de-408a-b874-0445c86b69e6"
      principalId      = azapi_resource.user_assigned_identity.output.properties.principalId
      principalType    = "ServicePrincipal"
    }
  }
}

# Grant the deploying principal write access to secrets (needed during deployment)
resource "azapi_resource" "kv_secrets_officer_deployer" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("sha1", "${azapi_resource.key_vault.id}-${data.azurerm_client_config.current.object_id}-b86a8fe4-44ce-4948-aee5-eccb2c155cd7")
  parent_id = azapi_resource.key_vault.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/b86a8fe4-44ce-4948-aee5-eccb2c155cd7"
      principalId      = data.azurerm_client_config.current.object_id
      principalType    = "User"
    }
  }
}
```

### Private Endpoint
```hcl
resource "azapi_resource" "kv_private_endpoint" {
  type      = "Microsoft.Network/privateEndpoints@2023-11-01"
  name      = "${var.key_vault_name}-pe"
  location  = azapi_resource.resource_group.output.location
  parent_id = azapi_resource.resource_group.id

  body = {
    properties = {
      subnet = {
        id = azapi_resource.private_endpoints_subnet.id
      }
      privateLinkServiceConnections = [
        {
          name = "${var.key_vault_name}-psc"
          properties = {
            privateLinkServiceId = azapi_resource.key_vault.id
            groupIds             = ["vault"]
          }
        }
      ]
    }
  }

  tags = var.tags
}

resource "azapi_resource" "kv_dns_zone" {
  type      = "Microsoft.Network/privateDnsZones@2020-06-01"
  name      = "privatelink.vaultcore.azure.net"
  location  = "global"
  parent_id = azapi_resource.resource_group.id

  tags = var.tags
}

resource "azapi_resource" "kv_dns_zone_link" {
  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01"
  name      = "kv-dns-link"
  location  = "global"
  parent_id = azapi_resource.kv_dns_zone.id

  body = {
    properties = {
      virtualNetwork = {
        id = azapi_resource.virtual_network.id
      }
      registrationEnabled = false
    }
  }

  tags = var.tags
}

resource "azapi_resource" "kv_pe_dns_zone_group" {
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01"
  name      = "default"
  parent_id = azapi_resource.kv_private_endpoint.id

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "config"
          properties = {
            privateDnsZoneId = azapi_resource.kv_dns_zone.id
          }
        }
      ]
    }
  }
}
```

## Bicep Patterns

### Basic Resource
```bicep
param keyVaultName string
param location string = resourceGroup().location
param tenantId string = subscription().tenantId
param tags object = {}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true       // CRITICAL: Use RBAC, NOT access policies
    enablePurgeProtection: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'            // Restrict to 'Deny' for production
    }
  }
  tags: tags
}

resource secret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'example-secret'
  properties: {
    value: secretValue
  }
}

@secure()
param secretValue string

output keyVaultUri string = keyVault.properties.vaultUri
output keyVaultId string = keyVault.id
```

### RBAC Assignment
```bicep
param principalId string

// Key Vault Secrets User — read secrets
var secretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource secretsUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, principalId, secretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsUserRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Application Code

### Python
```python
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

credential = DefaultAzureCredential()
client = SecretClient(
    vault_url="https://<vault-name>.vault.azure.net/",
    credential=credential
)

# Get a secret
secret = client.get_secret("example-secret")
print(f"Secret value: {secret.value}")

# Set a secret
client.set_secret("new-secret", "secret-value")

# List secrets (metadata only, not values)
for secret_properties in client.list_properties_of_secrets():
    print(f"Secret name: {secret_properties.name}")
```

### C#
```csharp
using Azure.Identity;
using Azure.Security.KeyVault.Secrets;

var credential = new DefaultAzureCredential();
var client = new SecretClient(
    vaultUri: new Uri("https://<vault-name>.vault.azure.net/"),
    credential: credential
);

// Get a secret
KeyVaultSecret secret = await client.GetSecretAsync("example-secret");
Console.WriteLine($"Secret value: {secret.Value}");

// Set a secret
await client.SetSecretAsync("new-secret", "secret-value");

// List secrets (metadata only, not values)
await foreach (SecretProperties secretProperties in client.GetPropertiesOfSecretsAsync())
{
    Console.WriteLine($"Secret name: {secretProperties.Name}");
}
```

### Node.js
```typescript
import { DefaultAzureCredential } from "@azure/identity";
import { SecretClient } from "@azure/keyvault-secrets";

const credential = new DefaultAzureCredential();
const client = new SecretClient(
  "https://<vault-name>.vault.azure.net/",
  credential
);

// Get a secret
const secret = await client.getSecret("example-secret");
console.log(`Secret value: ${secret.value}`);

// Set a secret
await client.setSecret("new-secret", "secret-value");

// List secrets (metadata only, not values)
for await (const secretProperties of client.listPropertiesOfSecrets()) {
  console.log(`Secret name: ${secretProperties.name}`);
}
```

## Common Pitfalls
- **Using access policies instead of RBAC**: Always set `enable_rbac_authorization = true`. Access policies are the legacy model and do not support fine-grained, identity-based control.
- **Deployer cannot write secrets**: When using RBAC mode, the Terraform/Bicep deploying principal needs the "Key Vault Secrets Officer" role to create secrets during deployment. Without this, `azapi_resource` secret resources will fail with 403.
- **Purge protection is irreversible**: Once `purge_protection_enabled = true` is set, it cannot be turned off. Deleted vaults/secrets remain for the full retention period.
- **Soft-deleted vault name collision**: A deleted vault still occupies its name for the retention period. Use `az keyvault list-deleted` to check for name conflicts.
- **Secret rotation not automatic**: Key Vault stores secrets but does not rotate them. Rotation requires Azure Function or Event Grid integration.
- **Network ACLs timing**: When setting `default_action = "Deny"`, ensure all required IPs and VNets are whitelisted first, or the deployer will lock itself out.

## Production Backlog Items
- HSM-backed keys (Premium SKU) for regulatory compliance
- Network ACLs with default deny and explicit allow rules
- Diagnostic settings for audit logging (log all secret access)
- Key rotation policy with automated rotation via Event Grid
- Certificate management with auto-renewal
- Private endpoint with DNS integration
- Backup and disaster recovery procedures
- Integration with Azure Policy for compliance enforcement
