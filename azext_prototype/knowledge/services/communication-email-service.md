---
service_namespace: Microsoft.Communication/emailServices
display_name: Azure Communication Services Email
---

# Azure Communication Services Email

> Managed email sending service within Azure Communication Services, enabling applications to send transactional and bulk emails with high deliverability, DKIM/SPF authentication, and tracking.

## When to Use
- **Transactional email** -- order confirmations, password resets, notifications from your application
- **Bulk email** -- marketing campaigns, newsletters with engagement tracking
- **Azure-native email** -- tighter integration with other ACS capabilities (SMS, chat, voice)
- **Custom domain sending** -- send from your own domain with full DKIM, SPF, DMARC authentication

Choose ACS Email over SendGrid when you want a fully Azure-native solution without third-party dependencies. Choose SendGrid for its more mature template engine and analytics dashboard.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Data location | United States | Or appropriate geography for compliance |
| Domain type | AzureManagedDomain | Free Azure-managed `*.azurecomm.net` domain for POC |
| Custom domain | Optional | Add for production with DKIM/SPF |
| Sender address | `DoNotReply@<guid>.azurecomm.net` | Default sender on managed domain |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "email_service" {
  type      = "Microsoft.Communication/emailServices@2023-04-01"
  name      = var.name
  location  = "global"  # Email services are global resources
  parent_id = var.resource_group_id

  body = {
    properties = {
      dataLocation = var.data_location  # e.g., "United States"
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}
```

### RBAC Assignment

```hcl
# Contributor on the email service for management
resource "azapi_resource" "email_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.email_service.id}-${var.principal_id}-contributor")
  parent_id = azapi_resource.email_service.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/b24988ac-6180-42a0-ab88-20f7382dd24c"
      principalId      = var.principal_id
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the email service')
param name string

@description('Data residency location')
@allowed(['United States', 'Europe', 'Asia Pacific', 'Australia', 'UK', 'Japan', 'France', 'Germany'])
param dataLocation string = 'United States'

param tags object = {}

resource emailService 'Microsoft.Communication/emailServices@2023-04-01' = {
  name: name
  location: 'global'
  tags: tags
  properties: {
    dataLocation: dataLocation
  }
}

output id string = emailService.id
output name string = emailService.name
```

## Application Code

### Python

```python
from azure.communication.email import EmailClient
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
client = EmailClient(
    endpoint="https://<acs-resource>.communication.azure.com",
    credential=credential
)

message = {
    "senderAddress": "DoNotReply@<guid>.azurecomm.net",
    "recipients": {
        "to": [{"address": "user@example.com", "displayName": "Recipient"}]
    },
    "content": {
        "subject": "Welcome to our service",
        "plainText": "Hello from Azure Communication Services Email.",
        "html": "<h1>Hello</h1><p>Welcome to our service.</p>"
    }
}

poller = client.begin_send(message)
result = poller.result()
print(f"Message ID: {result.id}, Status: {result.status}")
```

### C#

```csharp
using Azure.Communication.Email;
using Azure.Identity;

var client = new EmailClient(
    new Uri("https://<acs-resource>.communication.azure.com"),
    new DefaultAzureCredential());

var emailMessage = new EmailMessage(
    senderAddress: "DoNotReply@<guid>.azurecomm.net",
    recipientAddress: "user@example.com",
    content: new EmailContent("Welcome")
    {
        PlainText = "Hello from Azure Communication Services Email.",
        Html = "<h1>Hello</h1><p>Welcome to our service.</p>"
    });

EmailSendOperation operation = await client.SendAsync(
    WaitUntil.Completed, emailMessage);
Console.WriteLine($"Status: {operation.Value.Status}");
```

### Node.js

```typescript
import { EmailClient } from "@azure/communication-email";
import { DefaultAzureCredential } from "@azure/identity";

const client = new EmailClient(
  "https://<acs-resource>.communication.azure.com",
  new DefaultAzureCredential()
);

const message = {
  senderAddress: "DoNotReply@<guid>.azurecomm.net",
  recipients: {
    to: [{ address: "user@example.com", displayName: "Recipient" }],
  },
  content: {
    subject: "Welcome to our service",
    plainText: "Hello from Azure Communication Services Email.",
    html: "<h1>Hello</h1><p>Welcome to our service.</p>",
  },
};

const poller = await client.beginSend(message);
const result = await poller.pollUntilDone();
console.log(`Message ID: ${result.id}, Status: ${result.status}`);
```

## Common Pitfalls

1. **Location must be `"global"`** -- Email services are global resources. Specifying a region like `eastus` causes deployment failure.
2. **Data location vs resource location** -- `location` is always `global`, but `dataLocation` controls where data is stored for compliance (e.g., `United States`, `Europe`).
3. **Azure-managed domain limitations** -- The `*.azurecomm.net` domain has sending limits and cannot be customized. Use a custom domain for production.
4. **Linking to Communication Services** -- The email service must be linked to an ACS resource via `Microsoft.Communication/communicationServices` linked domains before sending.
5. **Asynchronous sending** -- `begin_send()` returns a poller. Emails are queued and may take seconds to minutes for delivery. Check the operation status.
6. **Rate limits** -- Default rate limits are 30 messages/minute and 100 recipients/hour for new services. Request increases via support.
7. **DKIM/SPF for custom domains** -- Custom domains require DNS TXT records for DKIM and SPF verification. Incomplete verification blocks sending from that domain.

## Production Backlog Items

- [ ] Configure custom sending domain with DKIM, SPF, and DMARC authentication
- [ ] Request sending limit increases based on projected volume
- [ ] Set up suppression list management for bounced addresses
- [ ] Implement email tracking (open, click) via engagement tracking
- [ ] Configure diagnostic logging to Log Analytics for delivery monitoring
- [ ] Add email templates for consistent branding across transactional emails
- [ ] Plan IP warm-up strategy if sending high-volume email from new domain
