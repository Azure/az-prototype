---
service_namespace: Microsoft.Sql/servers
display_name: Azure SQL Server
---

# Azure SQL Server

> Logical server instance that hosts Azure SQL databases. Manages authentication, firewall rules, TLS, and server-level configuration.

## When to Use
- Parent resource for all Azure SQL databases
- Centralized authentication via Microsoft Entra (Azure AD)
- Server-level firewall and network access control

## POC Defaults
- **Authentication**: Azure AD-only (no SQL authentication)
- **TLS**: Minimum version 1.2
- **Firewall**: Allow Azure services (0.0.0.0 rule) for managed identity access

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "sql_server" {
  type      = "Microsoft.Sql/servers@2023-08-01-preview"
  name      = var.sql_server_name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      minimalTlsVersion = "1.2"
      administrators = {
        administratorType           = "ActiveDirectory"
        principalType               = "Group"     # or "User", "Application"
        login                       = var.aad_admin_login
        sid                         = var.aad_admin_object_id
        tenantId                    = var.tenant_id
        azureADOnlyAuthentication   = true        # CRITICAL: Disable SQL authentication entirely
      }
    }
  }

  tags = var.tags
  response_export_values = ["*"]
}

# Allow Azure services to connect (for managed identity access)
resource "azapi_resource" "sql_firewall_allow_azure" {
  type      = "Microsoft.Sql/servers/firewallRules@2023-08-01-preview"
  name      = "AllowAzureServices"
  parent_id = azapi_resource.sql_server.id

  body = {
    properties = {
      startIpAddress = "0.0.0.0"
      endIpAddress   = "0.0.0.0"
    }
  }
}
```

### RBAC Assignment (Control Plane Only)
```hcl
# CRITICAL: This is a CONTROL PLANE role only — it does NOT grant data access.
# Data access uses T-SQL contained users (see Microsoft.Sql/servers/databases knowledge).
resource "azapi_resource" "sql_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("sha1", "${azapi_resource.sql_server.id}-${var.principal_id}-6d8ee4ec")
  parent_id = azapi_resource.sql_server.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/6d8ee4ec-f05a-4a1d-8b00-a9b17e38b437"  # SQL Server Contributor
      principalId      = var.principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource
```bicep
param sqlServerName string
param location string = resourceGroup().location
param aadAdminLogin string
param aadAdminObjectId string
param tags object = {}

resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: sqlServerName
  location: location
  properties: {
    minimalTlsVersion: '1.2'
    administrators: {
      administratorType: 'ActiveDirectory'
      principalType: 'Group'
      login: aadAdminLogin
      sid: aadAdminObjectId
      tenantId: subscription().tenantId
      azureADOnlyAuthentication: true
    }
  }
  tags: tags
}

resource firewallRule 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  parent: sqlServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

output sqlServerId string = sqlServer.id
output sqlServerName string = sqlServer.name
output sqlServerFqdn string = sqlServer.properties.fullyQualifiedDomainName
```

## Common Pitfalls
- **Leaving SQL authentication enabled**: Always set `azureADOnlyAuthentication = true`. Without this, password-based SQL logins remain available.
- **Firewall for Azure services**: The `0.0.0.0` to `0.0.0.0` rule allows ALL Azure services, not just your own. Use private endpoints for tighter control.
- **Confusing control-plane vs data-plane roles**: SQL Server Contributor is a control-plane role (manage server settings). Data access requires T-SQL contained users on the database.

## Production Backlog Items
- Private endpoint with DNS integration (remove public firewall rules)
- Advanced Threat Protection and vulnerability assessments
- Auditing to Log Analytics or Storage Account
- Transparent Data Encryption with customer-managed keys (CMK)
