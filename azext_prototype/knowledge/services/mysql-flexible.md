# Azure Database for MySQL Flexible Server
> Fully managed MySQL database service with flexible compute and storage scaling, built-in high availability, and automated backups.

## When to Use

- **MySQL workloads** -- teams with MySQL expertise or existing MySQL applications
- **WordPress / PHP applications** -- MySQL is the default database for WordPress and many PHP frameworks
- **Open-source CMS platforms** -- Drupal, Joomla, Magento, and other MySQL-native applications
- **Migration from on-premises MySQL** -- near drop-in compatibility with MySQL 5.7 and 8.0
- **Cost-sensitive relational workloads** -- Burstable tier starts lower than PostgreSQL equivalent

Choose MySQL Flexible Server over Azure SQL for MySQL-native applications. Choose PostgreSQL Flexible Server when the team prefers PostgreSQL or needs extensions like pgvector. Choose Azure SQL for .NET-heavy stacks or SQL Server-specific features.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Burstable B1ms | 1 vCore, 2 GiB RAM; lowest cost for POC |
| Storage | 20 GiB | Minimum; auto-grow enabled |
| MySQL version | 8.0 | Latest stable |
| High availability | Disabled | POC doesn't need zone-redundant HA |
| Backup retention | 7 days | Default; sufficient for POC |
| Authentication | MySQL auth + AAD | AAD for app, MySQL auth for admin bootstrap |
| Public network access | Enabled | Flag private access as production backlog item |
| SSL enforcement | Required | `require_secure_transport = ON` |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "mysql_server" {
  type      = "Microsoft.DBforMySQL/flexibleServers@2023-12-30"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Standard_B1ms"
      tier = "Burstable"
    }
    properties = {
      version                = "8.0.21"
      administratorLogin     = var.admin_username
      administratorLoginPassword = var.admin_password  # Store in Key Vault
      storage = {
        storageSizeGB = 20
        autoGrow      = "Enabled"
        autoIoScaling = "Enabled"
      }
      backup = {
        backupRetentionDays  = 7
        geoRedundantBackup   = "Disabled"  # Enable for production
      }
      highAvailability = {
        mode = "Disabled"  # Enable for production
      }
      network = {
        publicNetworkAccess = "Enabled"  # Disable for production
      }
    }
  }

  tags = var.tags

  response_export_values = ["properties.fullyQualifiedDomainName"]
}
```

### Firewall Rule (POC convenience)

```hcl
resource "azapi_resource" "mysql_firewall_allow_azure" {
  type      = "Microsoft.DBforMySQL/flexibleServers/firewallRules@2023-12-30"
  name      = "AllowAzureServices"
  parent_id = azapi_resource.mysql_server.id

  body = {
    properties = {
      startIpAddress = "0.0.0.0"
      endIpAddress   = "0.0.0.0"
    }
  }
}
```

### AAD Administrator

```hcl
resource "azapi_resource" "mysql_aad_admin" {
  type      = "Microsoft.DBforMySQL/flexibleServers/administrators@2023-12-30"
  name      = var.managed_identity_principal_id
  parent_id = azapi_resource.mysql_server.id

  body = {
    properties = {
      administratorType  = "ActiveDirectory"
      identityResourceId = var.managed_identity_id
      login              = var.aad_admin_login
      sid                = var.managed_identity_principal_id
      tenantId           = var.tenant_id
    }
  }
}
```

### RBAC Assignment

```hcl
# MySQL Flexible Server does not use Azure RBAC for data-plane access.
# Data-plane access is controlled via MySQL GRANT statements after AAD admin setup.
# The managed identity authenticates via AAD token, then MySQL GRANTs control permissions.
#
# Control-plane RBAC example: grant deployment identity Contributor on the server
resource "azapi_resource" "mysql_contributor_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.mysql_server.id}${var.managed_identity_principal_id}mysql-contributor")
  parent_id = azapi_resource.mysql_server.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/b24988ac-6180-42a0-ab88-20f7382dd24c"  # Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the MySQL server')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Administrator login name')
param administratorLogin string

@secure()
@description('Administrator login password')
param administratorLoginPassword string

@description('Tags to apply')
param tags object = {}

resource mysqlServer 'Microsoft.DBforMySQL/flexibleServers@2023-12-30' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '8.0.21'
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    storage: {
      storageSizeGB: 20
      autoGrow: 'Enabled'
      autoIoScaling: 'Enabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Enabled'
    }
  }
}

output id string = mysqlServer.id
output name string = mysqlServer.name
output fqdn string = mysqlServer.properties.fullyQualifiedDomainName
```

### RBAC Assignment

```bicep
@description('Principal ID of the managed identity for AAD admin')
param principalId string

@description('Login name for the AAD admin')
param aadAdminLogin string

@description('Managed identity resource ID')
param managedIdentityId string

@description('Tenant ID')
param tenantId string

resource mysqlAadAdmin 'Microsoft.DBforMySQL/flexibleServers/administrators@2023-12-30' = {
  parent: mysqlServer
  name: principalId
  properties: {
    administratorType: 'ActiveDirectory'
    identityResourceId: managedIdentityId
    login: aadAdminLogin
    sid: principalId
    tenantId: tenantId
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Fix |
|---------|--------|-----|
| Using connection strings with passwords | Secrets in config, rotation burden | Configure AAD admin and use `DefaultAzureCredential` token for MySQL auth |
| Burstable tier CPU credits exhaustion | Performance degrades to baseline after sustained load | Monitor CPU credit balance; upgrade to General Purpose for sustained workloads |
| Missing SSL enforcement | Connections unencrypted in transit | Ensure `require_secure_transport = ON` (default); use `ssl-mode=REQUIRED` in connection strings |
| Storage auto-grow disabled | Server becomes read-only when storage is full | Enable `autoGrow` on storage configuration |
| Wrong MySQL version string | Deployment fails with invalid version error | Use exact version: `8.0.21` or `5.7` (not just `8.0`) |
| Firewall 0.0.0.0 rule in production | All Azure services can connect | Use VNet integration or private endpoints for production |
| Not creating application database | App tries to use system database | Create application-specific database via MySQL GRANT after server provisioning |

## Production Backlog Items

- [ ] Enable private access (VNet integration) and disable public network access
- [ ] Enable zone-redundant high availability
- [ ] Upgrade to General Purpose or Business Critical tier for production workloads
- [ ] Enable geo-redundant backup for disaster recovery
- [ ] Configure read replicas for read-heavy workloads
- [ ] Set up monitoring alerts (CPU, memory, storage, connections, slow queries)
- [ ] Enable slow query log and audit log for diagnostics
- [ ] Configure diagnostic logging to Log Analytics workspace
- [ ] Review and tune server parameters (innodb_buffer_pool_size, max_connections)
- [ ] Implement connection pooling in application code
- [ ] Set up automated maintenance window during off-peak hours
