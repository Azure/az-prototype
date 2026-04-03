# Azure Communication Services
> Cloud-based communication platform for adding voice, video, chat, SMS, and email capabilities to applications without managing telephony infrastructure.

## When to Use

- **Voice and video calling** -- embed WebRTC-based calling into web and mobile apps
- **Chat** -- real-time messaging with typing indicators, read receipts, and thread management
- **SMS** -- send and receive SMS messages programmatically (toll-free or short codes)
- **Email** -- transactional email delivery at scale with custom domains
- **Teams interop** -- connect custom apps to Microsoft Teams meetings and chats
- **Phone system** -- PSTN calling with phone number management

Choose Communication Services over third-party APIs (Twilio, SendGrid) when you want native Azure integration, Teams interoperability, or unified billing through Azure. Choose third-party when you need broader international carrier coverage or specialized features.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Data location | United States | Data residency for communication data |
| Authentication | Managed identity + RBAC | Connection strings for quick POC start |
| Phone numbers | Not required for POC | Voice/SMS only; chat and video work without |
| Email | Optional | Requires linked Email Communication Services resource |
| Managed identity | User-assigned | For accessing ACS from backend services |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "communication" {
  type      = "Microsoft.Communication/communicationServices@2023-04-01"
  name      = var.name
  location  = "global"  # Communication Services are global resources
  parent_id = var.resource_group_id

  body = {
    properties = {
      dataLocation = "United States"  # Data residency
    }
  }

  tags = var.tags

  response_export_values = ["properties.hostName", "properties.immutableResourceId"]
}
```

### Email Communication Services

```hcl
resource "azapi_resource" "email" {
  type      = "Microsoft.Communication/emailServices@2023-04-01"
  name      = var.email_service_name
  location  = "global"
  parent_id = var.resource_group_id

  body = {
    properties = {
      dataLocation = "United States"
    }
  }

  tags = var.tags
}

# Azure-managed domain (for POC; custom domain for production)
resource "azapi_resource" "email_domain" {
  type      = "Microsoft.Communication/emailServices/domains@2023-04-01"
  name      = "AzureManagedDomain"
  parent_id = azapi_resource.email.id

  body = {
    location = "global"
    properties = {
      domainManagement  = "AzureManaged"
      userEngagementTracking = "Disabled"
    }
  }
}

# Link email to communication services
resource "azapi_update_resource" "link_email" {
  type        = "Microsoft.Communication/communicationServices@2023-04-01"
  resource_id = azapi_resource.communication.id

  body = {
    properties = {
      linkedDomains = [
        azapi_resource.email_domain.id
      ]
    }
  }
}
```

### RBAC Assignment

```hcl
# Communication Services Contributor -- full access to ACS resource
resource "azapi_resource" "acs_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.communication.id}${var.managed_identity_principal_id}acs-contributor")
  parent_id = azapi_resource.communication.id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/b24988ac-6180-42a0-ab88-20f7382dd24c"  # Contributor
      principalId      = var.managed_identity_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

Communication Services does not support private endpoints. All communication traffic is secured via TLS. Access tokens and connection strings are used for authentication.

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Communication Services resource')
param name string

@description('Data location for communication data residency')
param dataLocation string = 'United States'

@description('Tags to apply')
param tags object = {}

resource communication 'Microsoft.Communication/communicationServices@2023-04-01' = {
  name: name
  location: 'global'
  tags: tags
  properties: {
    dataLocation: dataLocation
  }
}

output id string = communication.id
output name string = communication.name
output hostName string = communication.properties.hostName
```

### Email Communication Services

```bicep
@description('Email service name')
param emailServiceName string

resource emailService 'Microsoft.Communication/emailServices@2023-04-01' = {
  name: emailServiceName
  location: 'global'
  properties: {
    dataLocation: 'United States'
  }
}

resource emailDomain 'Microsoft.Communication/emailServices/domains@2023-04-01' = {
  parent: emailService
  name: 'AzureManagedDomain'
  location: 'global'
  properties: {
    domainManagement: 'AzureManaged'
    userEngagementTracking: 'Disabled'
  }
}

output emailServiceId string = emailService.id
output emailDomainId string = emailDomain.id
output mailFromSenderDomain string = emailDomain.properties.mailFromSenderDomain
```

### RBAC Assignment

```bicep
@description('Principal ID for ACS management')
param principalId string

resource acsContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(communication.id, principalId, 'contributor')
  scope: communication
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c')  // Contributor
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| Connection string in client code | Secret exposed to end users | Use short-lived access tokens issued by your backend; never embed connection strings in frontend |
| Missing CORS configuration | Browser blocks WebRTC signaling | Configure CORS on your backend that issues tokens |
| Phone number provisioning delays | Can take days for toll-free or short codes | Start phone number acquisition early; use chat/video for initial POC |
| Email domain verification | Emails rejected without verified domain | Use Azure-managed domain for POC; verify custom domain for production |
| Token expiration | Calls/chats disconnected after token expires | Implement token refresh logic; default token lifetime is 24 hours |
| Data residency misconfiguration | Data stored in wrong region; compliance violations | Set `dataLocation` at creation time; cannot be changed later |
| Rate limits on SMS | Messages throttled or rejected | Implement retry logic with exponential backoff |
| Missing event subscription | No notifications for incoming messages/calls | Configure Event Grid subscriptions for real-time event handling |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Custom email domain | P2 | Configure and verify custom domain for branded email delivery |
| Phone number acquisition | P2 | Provision toll-free or local phone numbers for SMS and voice |
| Call recording | P3 | Enable server-side call recording with Azure Blob Storage |
| Teams interop | P2 | Configure Teams interop for joining meetings from custom apps |
| Event Grid integration | P1 | Subscribe to ACS events for incoming calls, messages, and delivery reports |
| Token management service | P1 | Build a secure backend service for issuing and refreshing access tokens |
| Call diagnostics | P3 | Enable call quality diagnostics and monitoring |
| Custom domain for chat | P3 | Configure custom domain for chat endpoint branding |
| PSTN connectivity | P2 | Set up direct routing or Azure-managed PSTN for phone calls |
| Compliance recording | P3 | Implement compliance recording for regulated industries |
