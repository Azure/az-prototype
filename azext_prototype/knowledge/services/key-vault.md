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

resource "azurerm_key_vault" "this" {
  name                        = var.key_vault_name
  location                    = azurerm_resource_group.this.location
  resource_group_name         = azurerm_resource_group.this.name
  tenant_id                   = data.azurerm_client_config.current.tenant_id
  sku_name                    = "standard"
  enable_rbac_authorization   = true    # CRITICAL: Use RBAC, NOT access policies
  purge_protection_enabled    = true
  soft_delete_retention_days  = 90

  network_acls {
    bypass         = "AzureServices"
    default_action = "Allow"            # Restrict to "Deny" for production
  }

  tags = var.tags
}

resource "azurerm_key_vault_secret" "example" {
  name         = "example-secret"
  value        = var.secret_value
  key_vault_id = azurerm_key_vault.this.id

  depends_on = [azurerm_role_assignment.kv_secrets_officer_deployer]
}
```

### RBAC Assignment
```hcl
# Role IDs from service-registry.yaml:
#   Key Vault Secrets User:    4633458b-17de-408a-b874-0445c86b69e6
#   Key Vault Secrets Officer: b86a8fe4-44ce-4948-aee5-eccb2c155cd7
#   Key Vault Administrator:   00482a5a-887f-4fb3-b363-3b7fe8e74483

# Grant the app's managed identity read access to secrets
resource "azurerm_role_assignment" "kv_secrets_user" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.this.principal_id
}

# Grant the deploying principal write access to secrets (needed during deployment)
resource "azurerm_role_assignment" "kv_secrets_officer_deployer" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}
```

### Private Endpoint
```hcl
resource "azurerm_private_endpoint" "kv" {
  name                = "${var.key_vault_name}-pe"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  subnet_id           = azurerm_subnet.private_endpoints.id

  private_service_connection {
    name                           = "${var.key_vault_name}-psc"
    private_connection_resource_id = azurerm_key_vault.this.id
    is_manual_connection           = false
    subresource_names              = ["vault"]
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [azurerm_private_dns_zone.kv.id]
  }
}

resource "azurerm_private_dns_zone" "kv" {
  name                = "privatelink.vaultcore.azure.net"
  resource_group_name = azurerm_resource_group.this.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "kv" {
  name                  = "kv-dns-link"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.kv.name
  virtual_network_id    = azurerm_virtual_network.this.id
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
- **Deployer cannot write secrets**: When using RBAC mode, the Terraform/Bicep deploying principal needs the "Key Vault Secrets Officer" role to create secrets during deployment. Without this, `azurerm_key_vault_secret` resources will fail with 403.
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
