---
service_namespace: Microsoft.Cdn/profiles/securityPolicies
display_name: Front Door Security Policy (WAF Association)
depends_on:
  - Microsoft.Cdn/profiles
  - Microsoft.Cdn/profiles/afdEndpoints
---

# Front Door Security Policy

> Associates a WAF policy with one or more Front Door endpoints. Enables web application firewall protection for incoming traffic.

## When to Use
- Protect Front Door endpoints with WAF rules (OWASP, bot protection, custom rules)
- Required for production — recommended to configure early in POC
- One security policy can cover multiple endpoints

## POC Defaults
- **WAF mode**: Detection (logs but doesn't block) — switch to Prevention for production
- **Managed rule sets**: Microsoft Default Rule Set (DRS 2.1)

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "security_policy" {
  type      = "Microsoft.Cdn/profiles/securityPolicies@2024-02-01"
  name      = var.policy_name
  parent_id = azapi_resource.cdn_profile.id

  body = {
    properties = {
      parameters = {
        type = "WebApplicationFirewall"
        wafPolicy = {
          id = azapi_resource.waf_policy.id
        }
        associations = [
          {
            domains = [
              { id = azapi_resource.afd_endpoint.id }
            ]
            patternsToMatch = ["/*"]
          }
        ]
      }
    }
  }
}
```

### RBAC Assignment
```hcl
# Security policy management inherits from the parent CDN profile RBAC.
```

## Bicep Patterns

### Basic Resource
```bicep
param policyName string
param wafPolicyId string
param endpointId string

resource securityPolicy 'Microsoft.Cdn/profiles/securityPolicies@2024-02-01' = {
  parent: cdnProfile
  name: policyName
  properties: {
    parameters: {
      type: 'WebApplicationFirewall'
      wafPolicy: { id: wafPolicyId }
      associations: [
        {
          domains: [{ id: endpointId }]
          patternsToMatch: ['/*']
        }
      ]
    }
  }
}
```

## Application Code

### Python
```python
# Security policies are infrastructure — transparent to application code.
# WAF blocks are returned as HTTP 403 responses to the client.
```

### C#
```csharp
// Security policies are infrastructure — transparent to application code.
```

### Node.js
```typescript
// Security policies are infrastructure — transparent to application code.
```

## Common Pitfalls
- **Detection vs Prevention**: In Detection mode, WAF logs threats but doesn't block them. Switch to Prevention for actual protection.
- **Domain association**: The security policy must be associated with specific endpoints. Without association, WAF rules don't apply.
- **WAF policy SKU**: The WAF policy must match the CDN profile SKU (Standard vs Premium).

## Production Backlog Items
- Switch from Detection to Prevention mode
- Custom WAF rules for application-specific protection
- Bot management rules
- Rate limiting rules
- WAF log analysis via Log Analytics
