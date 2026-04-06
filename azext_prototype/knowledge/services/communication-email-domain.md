---
service_namespace: Microsoft.Communication/emailServices/domains
display_name: Communication Email Domain
depends_on:
  - Microsoft.Communication/emailServices
---

# Communication Email Domain

> Custom or Azure-managed email domain configuration within an ACS Email Service, controlling sender addresses, DKIM/SPF verification, and sender authentication for email delivery.

## When to Use
- **Custom domain sending** -- send from `noreply@yourcompany.com` instead of the Azure-managed domain
- **Brand consistency** -- emails appear from your own domain, improving trust and open rates
- **Compliance requirements** -- certain industries require email from a verified corporate domain
- **Azure-managed domain (POC)** -- use the built-in `*.azurecomm.net` domain for quick POC setup

Every email service automatically provisions an Azure-managed domain. Custom domains require DNS verification (DKIM, SPF, DMARC).

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Domain management | AzureManagedDomain | Automatic for POC; no DNS changes needed |
| User engagement tracking | Disabled | Enable for production analytics |
| Sender username | DoNotReply | Default sender on managed domain |

## Terraform Patterns

### Basic Resource

```hcl
# Azure-managed domain (created automatically, but can be explicit)
resource "azapi_resource" "managed_domain" {
  type      = "Microsoft.Communication/emailServices/domains@2023-04-01"
  name      = "AzureManagedDomain"
  parent_id = azapi_resource.email_service.id
  location  = "global"

  body = {
    properties = {
      domainManagement       = "AzureManagedDomain"
      userEngagementTracking = "Disabled"
    }
  }

  response_export_values = ["properties.fromSenderDomain", "properties.mailFromSenderDomain"]
}

# Custom domain
resource "azapi_resource" "custom_domain" {
  type      = "Microsoft.Communication/emailServices/domains@2023-04-01"
  name      = var.custom_domain_name  # e.g., "contoso.com"
  parent_id = azapi_resource.email_service.id
  location  = "global"

  body = {
    properties = {
      domainManagement       = "CustomerManaged"
      userEngagementTracking = "Disabled"
    }
  }

  response_export_values = ["properties.verificationStates"]
}
```

### RBAC Assignment

```hcl
# Domain management inherits from the parent email service RBAC.
# Contributor on the email service covers domain operations.
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Custom domain name (e.g., contoso.com)')
param domainName string

@description('Domain management type')
@allowed(['AzureManagedDomain', 'CustomerManaged', 'CustomerManagedInExchangeOnline'])
param domainManagement string = 'AzureManagedDomain'

resource domain 'Microsoft.Communication/emailServices/domains@2023-04-01' = {
  parent: emailService
  name: domainManagement == 'AzureManagedDomain' ? 'AzureManagedDomain' : domainName
  location: 'global'
  properties: {
    domainManagement: domainManagement
    userEngagementTracking: 'Disabled'
  }
}

output domainId string = domain.id
output fromSenderDomain string = domain.properties.fromSenderDomain
output mailFromSenderDomain string = domain.properties.mailFromSenderDomain
```

## Application Code

### Python
Infrastructure -- transparent to application code. The domain configuration determines which sender addresses are available; application code references the sender address directly in the `EmailMessage`.

### C#
Infrastructure -- transparent to application code. The domain configuration determines which sender addresses are available; application code references the sender address directly in the `EmailMessage`.

### Node.js
Infrastructure -- transparent to application code. The domain configuration determines which sender addresses are available; application code references the sender address directly in the `EmailMessage`.

## Common Pitfalls

1. **Azure-managed domain name must be exactly `"AzureManagedDomain"`** -- Using any other name for the managed domain type causes deployment failure.
2. **Custom domain DNS verification is manual** -- After deploying a `CustomerManaged` domain, you must add DKIM, SPF, and DMARC TXT records to your DNS zone. The domain remains `NotStarted` until verified.
3. **DKIM has three verification records** -- Unlike typical email setups, ACS requires three CNAME records for DKIM. Missing any one blocks verification.
4. **Domain must be linked to ACS resource** -- After domain creation and verification, the domain must be linked to the parent Communication Services resource before it can be used for sending.
5. **Sender usernames must be provisioned** -- Custom domains require explicit sender username resources (`Microsoft.Communication/emailServices/domains/senderUsernames`) before you can send from an address.
6. **Engagement tracking affects DNS** -- Enabling `userEngagementTracking` requires additional DNS records for click/open tracking. Deploy without tracking first, add later.
7. **Verification timeout** -- Custom domain verification must be completed within 7 days of resource creation. After that, delete and recreate.

## Production Backlog Items

- [ ] Configure custom domain with full DKIM, SPF, and DMARC DNS records
- [ ] Verify custom domain and confirm `VerificationStatus` is `Verified` for all record types
- [ ] Create sender username resources for all required sending addresses
- [ ] Enable user engagement tracking with appropriate DNS records
- [ ] Link verified domain to the parent Communication Services resource
- [ ] Set up DMARC reporting to monitor email authentication failures
- [ ] Plan domain verification for any additional sending domains
