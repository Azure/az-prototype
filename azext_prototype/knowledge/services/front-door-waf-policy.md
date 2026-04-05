---
service_namespace: Microsoft.Network/FrontDoorWebApplicationFirewallPolicies
display_name: Front Door WAF Policy
---

# Front Door WAF Policy

> Web Application Firewall policy for Azure Front Door that protects web applications from common exploits (OWASP Top 10), bots, and custom-defined attack patterns with managed and custom rules.

## When to Use
- **OWASP protection** -- block SQL injection, XSS, command injection, and other OWASP Top 10 attacks
- **Bot protection** -- identify and block malicious bots while allowing legitimate crawlers
- **Geo-filtering** -- block or allow traffic from specific countries/regions
- **Rate limiting** -- prevent DDoS and brute-force attacks at the edge
- **Custom rules** -- match on headers, query strings, IP addresses, or request body for organization-specific protection

WAF policies are associated with Front Door endpoints or security policies. A single policy can protect multiple endpoints.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Premium_AzureFrontDoor | Classic is deprecated; Standard does not support WAF |
| Mode | Detection | Log but don't block for POC tuning |
| Managed rule set | Microsoft_DefaultRuleSet 2.1 | Latest DRS version |
| Bot rule set | Microsoft_BotManagerRuleSet 1.1 | Optional for POC |
| Custom rules | None | Add as needed |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "waf_policy" {
  type      = "Microsoft.Network/FrontDoorWebApplicationFirewallPolicies@2024-02-01"
  name      = var.name
  location  = "global"  # WAF policies are global
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Premium_AzureFrontDoor"
    }
    properties = {
      policySettings = {
        mode                       = "Detection"  # "Prevention" for production
        enabledState               = "Enabled"
        requestBodyCheck           = "Enabled"
        maxRequestBodySizeInKb     = 128
        customBlockResponseBody    = null
        customBlockResponseStatusCode = 403
      }
      managedRules = {
        managedRuleSets = [
          {
            ruleSetType    = "Microsoft_DefaultRuleSet"
            ruleSetVersion = "2.1"
            ruleSetAction  = "Block"
          },
          {
            ruleSetType    = "Microsoft_BotManagerRuleSet"
            ruleSetVersion = "1.1"
            ruleSetAction  = "Block"
          }
        ]
      }
      customRules = {
        rules = []
      }
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}
```

### RBAC Assignment

```hcl
# CDN Endpoint Contributor for managing WAF policies
resource "azapi_resource" "cdn_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.waf_policy.id}-${var.principal_id}-cdn-contributor")
  parent_id = azapi_resource.waf_policy.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/426e0c7f-0c7e-4658-b36f-ff54d6c29b45"
      principalId      = var.principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

## Bicep Patterns

### Basic Resource

```bicep
@description('WAF policy name')
param name string

@description('WAF mode')
@allowed(['Detection', 'Prevention'])
param mode string = 'Detection'

param tags object = {}

resource wafPolicy 'Microsoft.Network/FrontDoorWebApplicationFirewallPolicies@2024-02-01' = {
  name: name
  location: 'global'
  tags: tags
  sku: {
    name: 'Premium_AzureFrontDoor'
  }
  properties: {
    policySettings: {
      mode: mode
      enabledState: 'Enabled'
      requestBodyCheck: 'Enabled'
      maxRequestBodySizeInKb: 128
      customBlockResponseStatusCode: 403
    }
    managedRules: {
      managedRuleSets: [
        {
          ruleSetType: 'Microsoft_DefaultRuleSet'
          ruleSetVersion: '2.1'
          ruleSetAction: 'Block'
        }
        {
          ruleSetType: 'Microsoft_BotManagerRuleSet'
          ruleSetVersion: '1.1'
          ruleSetAction: 'Block'
        }
      ]
    }
    customRules: {
      rules: []
    }
  }
}

output id string = wafPolicy.id
output name string = wafPolicy.name
```

## Application Code

### Python
Infrastructure -- transparent to application code. WAF policies inspect and filter HTTP traffic at the Front Door edge; backend applications receive only allowed requests.

### C#
Infrastructure -- transparent to application code. WAF policies inspect and filter HTTP traffic at the Front Door edge; backend applications receive only allowed requests.

### Node.js
Infrastructure -- transparent to application code. WAF policies inspect and filter HTTP traffic at the Front Door edge; backend applications receive only allowed requests.

## Common Pitfalls

1. **Location must be `"global"`** -- WAF policies for Front Door are global resources. Specifying a region causes deployment failure.
2. **SKU must match Front Door profile** -- A `Premium_AzureFrontDoor` WAF policy only works with Premium Front Door profiles. Standard Front Door does not support WAF.
3. **Detection mode first** -- Always start in Detection mode to analyze logs before switching to Prevention. Enabling Prevention immediately blocks legitimate traffic that triggers false positives.
4. **Managed rule exclusions** -- When legitimate requests trigger managed rules (e.g., SQL-like query strings), add exclusions for specific rules rather than disabling the entire rule group.
5. **Custom rule priority matters** -- Custom rules execute in priority order (lowest number first). A high-priority Allow rule can bypass subsequent Block rules.
6. **Request body size limit** -- The default 128 KB limit blocks large file uploads. Increase `maxRequestBodySizeInKb` (up to 2 MB) for upload-heavy applications.
7. **WAF logs require diagnostic settings** -- WAF blocks/detections are logged to `FrontDoorWebApplicationFirewallLog`. Enable diagnostic settings to a Log Analytics workspace to see them.
8. **Classic vs Standard/Premium** -- Classic Front Door WAF (`Microsoft.Network/frontDoorWebApplicationFirewallPolicies` with `Classic` SKU) is deprecated. Always use the `Premium_AzureFrontDoor` SKU.

## Production Backlog Items

- [ ] Switch from Detection to Prevention mode after tuning
- [ ] Configure managed rule exclusions for known false positives
- [ ] Add custom rate-limiting rules for login and API endpoints
- [ ] Implement geo-filtering custom rules if geographic restriction is needed
- [ ] Enable diagnostic logging to Log Analytics for WAF event analysis
- [ ] Create workbook/dashboard for WAF metrics and blocked requests
- [ ] Add IP allowlist/blocklist custom rules for known good/bad IPs
- [ ] Configure custom block response page with branded error message
