# Azure Bot Service
> Managed platform for building, deploying, and managing intelligent bots that interact with users across channels like Teams, Web Chat, Slack, and more.

## When to Use

- **Conversational AI** -- chatbots powered by Azure OpenAI, Language Understanding, or custom NLP models
- **Microsoft Teams bots** -- internal enterprise bots for help desk, HR, IT automation
- **Multi-channel messaging** -- single bot deployed across Teams, Web Chat, Slack, Facebook, SMS
- **Customer support** -- automated FAQ, ticket routing, and live agent handoff
- **Virtual assistants** -- task-oriented bots for scheduling, ordering, or information retrieval

Choose Bot Service over a standalone web API when you need built-in channel connectors, conversation state management, and the Bot Framework SDK. Choose a plain API if the interaction is request/response without conversational context.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | F0 (Free) | 10K messages/month; sufficient for POC |
| Kind | azurebot | Multi-channel registration (not legacy "sdk") |
| Messaging endpoint | App Service or Container Apps URL | Bot logic runs on separate compute |
| Authentication | User-assigned managed identity | For Azure resource access from bot code |
| App type | SingleTenant | Multi-tenant if external users need access |
| Channels | Web Chat, Teams | Enable others as needed |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "bot" {
  type      = "Microsoft.BotService/botServices@2022-09-15"
  name      = var.name
  location  = "global"  # Bot registrations are global
  parent_id = var.resource_group_id

  body = {
    kind = "azurebot"
    sku = {
      name = "F0"  # Free tier for POC
    }
    properties = {
      displayName                 = var.display_name
      endpoint                    = var.messaging_endpoint  # https://<app>.azurewebsites.net/api/messages
      msaAppId                    = var.app_id              # Azure AD app registration
      msaAppType                  = "SingleTenant"
      msaAppTenantId              = var.tenant_id
      disableLocalAuth            = true  # Disable legacy auth
      isStreamingSupported        = false
      schemaTransformationVersion = "1.3"
    }
  }

  tags = var.tags
}
```

### Channel Configuration (Teams)

```hcl
resource "azapi_resource" "teams_channel" {
  type      = "Microsoft.BotService/botServices/channels@2022-09-15"
  name      = "MsTeamsChannel"
  parent_id = azapi_resource.bot.id

  body = {
    properties = {
      channelName = "MsTeamsChannel"
      properties = {
        isEnabled = true
      }
    }
  }
}
```

### Channel Configuration (Web Chat)

```hcl
resource "azapi_resource" "webchat_channel" {
  type      = "Microsoft.BotService/botServices/channels@2022-09-15"
  name      = "WebChatChannel"
  parent_id = azapi_resource.bot.id

  body = {
    properties = {
      channelName = "WebChatChannel"
      properties = {}
    }
  }
}
```

### RBAC Assignment

```hcl
# Bot Service does not use Azure RBAC for data-plane access.
# The bot authenticates to Azure resources using the managed identity
# of its hosting compute (App Service or Container Apps).

# Grant the hosting app's identity access to Azure OpenAI for chat completions
resource "azapi_resource" "openai_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.openai_account_id}${var.managed_identity_principal_id}cognitive-services-user")
  parent_id = var.openai_account_id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/a97b65f3-24c7-4388-baec-2e87135dc908"  # Cognitive Services User
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

Bot Service registrations are global resources and do not support private endpoints. The bot logic runs on App Service, Container Apps, or Azure Functions, which have their own private endpoint configurations. Secure the hosting compute instead.

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the bot registration')
param name string

@description('Display name for the bot')
param displayName string

@description('Messaging endpoint URL')
param messagingEndpoint string

@description('Azure AD app registration ID')
param msaAppId string

@description('Azure AD tenant ID')
param msaAppTenantId string

@description('Tags to apply')
param tags object = {}

resource bot 'Microsoft.BotService/botServices@2022-09-15' = {
  name: name
  location: 'global'
  tags: tags
  kind: 'azurebot'
  sku: {
    name: 'F0'
  }
  properties: {
    displayName: displayName
    endpoint: messagingEndpoint
    msaAppId: msaAppId
    msaAppType: 'SingleTenant'
    msaAppTenantId: msaAppTenantId
    disableLocalAuth: true
    isStreamingSupported: false
    schemaTransformationVersion: '1.3'
  }
}

resource teamsChannel 'Microsoft.BotService/botServices/channels@2022-09-15' = {
  parent: bot
  name: 'MsTeamsChannel'
  properties: {
    channelName: 'MsTeamsChannel'
    properties: {
      isEnabled: true
    }
  }
}

output id string = bot.id
output name string = bot.name
```

### RBAC Assignment

Bot Service uses Azure AD app registrations for authentication, not ARM RBAC. Grant roles to the hosting compute's managed identity for accessing backend Azure resources.

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Using legacy "sdk" kind | Creates deprecated v3 bot registration | Always use `kind: "azurebot"` for new bots |
| Wrong messaging endpoint | Bot unreachable; channels show errors | Endpoint must be HTTPS and end with `/api/messages` |
| Multi-tenant when single-tenant needed | Authentication failures for internal bots | Use `SingleTenant` for enterprise-only bots |
| Missing Azure AD app registration | Bot cannot authenticate to channels | Create an Azure AD app registration before the bot resource |
| Not configuring CORS on hosting app | Web Chat embed fails in browsers | Add the embedding domain to CORS allowed origins |
| Forgetting to enable Teams channel | Bot not visible in Teams | Explicitly add `MsTeamsChannel` resource |
| F0 tier message limits | Bot stops responding after 10K messages/month | Monitor usage; upgrade to S1 for production |
| Direct Line secret exposure | Unauthorized access to bot | Use Direct Line tokens (short-lived) instead of secrets in client code |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Upgrade to S1 SKU | P1 | Remove message limits for production traffic |
| App Insights integration | P2 | Enable bot telemetry with Application Insights for conversation analytics |
| Custom domain | P3 | Configure custom domain on hosting app for branded bot endpoints |
| Authentication (OAuth) | P2 | Add user authentication via OAuth connections for accessing user-scoped data |
| Proactive messaging | P3 | Implement proactive message support for notifications and alerts |
| Adaptive Cards | P3 | Build rich interactive card UIs for Teams and Web Chat |
| State management | P2 | Configure Cosmos DB or Blob Storage for durable conversation state |
| Rate limiting | P2 | Implement rate limiting to protect backend services from bot traffic spikes |
| Multi-language support | P3 | Add Translator integration for multi-language bot interactions |
| CI/CD pipeline | P2 | Automate bot deployment with staging slots and A/B testing |
