# Azure WAF Policy
> Web Application Firewall policy providing centralized protection against common web exploits, bots, and vulnerabilities using OWASP rule sets, custom rules, and bot protection for Front Door and Application Gateway.

## When to Use

- **Web application protection** -- defend against OWASP Top 10 vulnerabilities (SQL injection, XSS, etc.)
- **Bot mitigation** -- identify and block malicious bots while allowing legitimate crawlers
- **Rate limiting** -- protect APIs and web apps from abuse and scraping
- **Geo-filtering** -- block or allow traffic from specific countries
- **Custom rules** -- IP-based, header-based, or query-string-based access control
- NOT suitable for: L3/L4 DDoS protection (use DDoS Protection Plan), network-level firewall rules (use Azure Firewall), or non-HTTP protocols

WAF policies attach to **Front Door** (global) or **Application Gateway** (regional). The ARM resource types differ: `Microsoft.Network/FrontDoorWebApplicationFirewallPolicies` for Front Door, `Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies` for Application Gateway.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Mode | Detection | Logs violations without blocking; switch to Prevention for enforcement |
| Rule set | Microsoft_DefaultRuleSet 2.1 | OWASP 3.2-based managed rules (Front Door) |
| Bot protection | Disabled (POC) | Enable BotManagerRuleSet for production |
| Custom rules | None | Add as needed for specific requirements |
| Request body check | Enabled | Inspect POST body up to 128 KB |
| File upload limit | 100 MB | Default; increase for file-upload-heavy apps |

## Terraform Patterns

### Front Door WAF Policy

```hcl
resource "azapi_resource" "waf_policy" {
  type      = "Microsoft.Network/FrontDoorWebApplicationFirewallPolicies@2024-02-01"
  name      = replace(var.name, "-", "")  # No hyphens allowed in WAF policy names
  location  = "global"
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Premium_AzureFrontDoor"  # WAF requires Premium tier
    }
    properties = {
      policySettings = {
        enabledState                  = "Enabled"
        mode                          = "Detection"  # "Prevention" for enforcement
        requestBodyCheck              = "Enabled"
        maxRequestBodySizeInKb        = 128
        requestBodyEnforcement        = "Enabled"
      }
      managedRules = {
        managedRuleSets = [
          {
            ruleSetType    = "Microsoft_DefaultRuleSet"
            ruleSetVersion = "2.1"
            ruleSetAction  = "Block"
          }
        ]
      }
    }
  }

  tags = var.tags
}
```

### Front Door WAF with Bot Protection and Custom Rules

```hcl
resource "azapi_resource" "waf_policy_full" {
  type      = "Microsoft.Network/FrontDoorWebApplicationFirewallPolicies@2024-02-01"
  name      = replace(var.name, "-", "")
  location  = "global"
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Premium_AzureFrontDoor"
    }
    properties = {
      policySettings = {
        enabledState           = "Enabled"
        mode                   = "Prevention"
        requestBodyCheck       = "Enabled"
        maxRequestBodySizeInKb = 128
      }
      managedRules = {
        managedRuleSets = [
          {
            ruleSetType    = "Microsoft_DefaultRuleSet"
            ruleSetVersion = "2.1"
            ruleSetAction  = "Block"
          }
          {
            ruleSetType    = "Microsoft_BotManagerRuleSet"
            ruleSetVersion = "1.1"
            ruleSetAction  = "Block"
          }
        ]
      }
      customRules = {
        rules = [
          {
            name         = "RateLimitPerIP"
            priority     = 100
            ruleType     = "RateLimitRule"
            action       = "Block"
            rateLimitDurationInMinutes = 1
            rateLimitThreshold         = 100
            matchConditions = [
              {
                matchVariable   = "RemoteAddr"
                operator        = "IPMatch"
                negateCondition = false
                matchValue      = ["0.0.0.0/0"]
              }
            ]
          }
          {
            name     = "BlockSpecificCountries"
            priority = 200
            ruleType = "MatchRule"
            action   = "Block"
            matchConditions = [
              {
                matchVariable   = "RemoteAddr"
                operator        = "GeoMatch"
                negateCondition = false
                matchValue      = var.blocked_countries  # e.g., ["CN", "RU"]
              }
            ]
          }
          {
            name     = "AllowListedIPs"
            priority = 50
            ruleType = "MatchRule"
            action   = "Allow"
            matchConditions = [
              {
                matchVariable   = "RemoteAddr"
                operator        = "IPMatch"
                negateCondition = false
                matchValue      = var.allowed_ips  # e.g., ["203.0.113.0/24"]
              }
            ]
          }
        ]
      }
    }
  }

  tags = var.tags
}
```

### Associate WAF with Front Door

```hcl
resource "azapi_resource" "security_policy" {
  type      = "Microsoft.Cdn/profiles/securityPolicies@2024-02-01"
  name      = "waf-security-policy"
  parent_id = var.front_door_profile_id

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
              {
                id = var.front_door_endpoint_id
              }
            ]
            patternsToMatch = ["/*"]
          }
        ]
      }
    }
  }
}
```

### Application Gateway WAF Policy

```hcl
resource "azapi_resource" "appgw_waf_policy" {
  type      = "Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-01-01"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  body = {
    properties = {
      policySettings = {
        state                  = "Enabled"
        mode                   = "Detection"  # "Prevention" for enforcement
        requestBodyCheck       = true
        maxRequestBodySizeInKb = 128
        fileUploadLimitInMb    = 100
      }
      managedRules = {
        managedRuleSets = [
          {
            ruleSetType    = "OWASP"
            ruleSetVersion = "3.2"
          }
          {
            ruleSetType    = "Microsoft_BotManagerRuleSet"
            ruleSetVersion = "1.0"
          }
        ]
      }
    }
  }

  tags = var.tags
}
```

### Rule Exclusions (False Positive Handling)

```hcl
resource "azapi_resource" "waf_policy_exclusions" {
  type      = "Microsoft.Network/FrontDoorWebApplicationFirewallPolicies@2024-02-01"
  name      = replace(var.name, "-", "")
  location  = "global"
  parent_id = var.resource_group_id

  body = {
    sku = {
      name = "Premium_AzureFrontDoor"
    }
    properties = {
      policySettings = {
        enabledState = "Enabled"
        mode         = "Prevention"
      }
      managedRules = {
        managedRuleSets = [
          {
            ruleSetType    = "Microsoft_DefaultRuleSet"
            ruleSetVersion = "2.1"
            ruleSetAction  = "Block"
            ruleGroupOverrides = [
              {
                ruleGroupName = "SQLI"
                rules = [
                  {
                    ruleId         = "942130"
                    enabledState   = "Enabled"
                    action         = "Log"  # Downgrade specific rule to log-only
                    exclusions = [
                      {
                        matchVariable         = "RequestBodyPostArgNames"
                        selectorMatchOperator = "Equals"
                        selector              = "description"  # Exclude "description" field from SQL injection check
                      }
                    ]
                  }
                ]
              }
            ]
          }
        ]
      }
    }
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# Network Contributor for WAF policy management
resource "azapi_resource" "waf_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.waf_policy.id}-${var.admin_principal_id}-network-contributor")
  parent_id = azapi_resource.waf_policy.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/4d97b98b-1d4f-4787-a291-c67834d212e7"  # Network Contributor
      principalId      = var.admin_principal_id
      principalType    = "ServicePrincipal"
    }
  }
}
```

### Private Endpoint

WAF Policy does not use private endpoints -- it is a configuration resource that attaches to Front Door or Application Gateway to define firewall rules.

## Bicep Patterns

### Front Door WAF Policy

```bicep
@description('Name of the WAF policy (no hyphens)')
param name string

@description('WAF mode')
@allowed(['Detection', 'Prevention'])
param mode string = 'Detection'

@description('Tags to apply')
param tags object = {}

resource wafPolicy 'Microsoft.Network/FrontDoorWebApplicationFirewallPolicies@2024-02-01' = {
  name: replace(name, '-', '')
  location: 'global'
  tags: tags
  sku: {
    name: 'Premium_AzureFrontDoor'
  }
  properties: {
    policySettings: {
      enabledState: 'Enabled'
      mode: mode
      requestBodyCheck: 'Enabled'
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
  }
}

output id string = wafPolicy.id
```

### Application Gateway WAF Policy

```bicep
@description('Name of the WAF policy')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('WAF mode')
@allowed(['Detection', 'Prevention'])
param mode string = 'Detection'

@description('Tags to apply')
param tags object = {}

resource appGwWafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-01-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    policySettings: {
      state: 'Enabled'
      mode: mode
      requestBodyCheck: true
      maxRequestBodySizeInKb: 128
      fileUploadLimitInMb: 100
    }
    managedRules: {
      managedRuleSets: [
        {
          ruleSetType: 'OWASP'
          ruleSetVersion: '3.2'
        }
      ]
    }
  }
}

output id string = appGwWafPolicy.id
```

### Associate with Front Door

```bicep
@description('Front Door profile resource')
param profileName string

@description('Front Door endpoint ID')
param endpointId string

resource securityPolicy 'Microsoft.Cdn/profiles/securityPolicies@2024-02-01' = {
  name: '${profileName}/waf-security-policy'
  properties: {
    parameters: {
      type: 'WebApplicationFirewall'
      wafPolicy: {
        id: wafPolicy.id
      }
      associations: [
        {
          domains: [
            {
              id: endpointId
            }
          ]
          patternsToMatch: ['/*']
        }
      ]
    }
  }
}
```

## Common Pitfalls

| Pitfall | Impact | Prevention |
|---------|--------|-----------|
| WAF policy name with hyphens (Front Door) | Deployment fails; hyphens not allowed | Use `replace(name, '-', '')` to strip hyphens |
| Starting in Prevention mode | Legitimate traffic blocked by false positives | Start in Detection mode; tune exclusions; then switch to Prevention |
| Not monitoring WAF logs | False positives go unnoticed; legitimate users blocked | Enable diagnostic logging and review blocked requests regularly |
| Wrong WAF type | Front Door and Application Gateway use different resource types | Use `FrontDoorWebApplicationFirewallPolicies` for Front Door, `ApplicationGatewayWebApplicationFirewallPolicies` for AppGW |
| Rule set version mismatch | Old rule sets miss new attack patterns | Use latest rule set versions (DRS 2.1, OWASP 3.2) |
| Over-broad exclusions | Security holes from excluding too many fields | Exclude specific fields and rules only; document each exclusion |
| Rate limit too aggressive | Legitimate users hitting rate limits | Start with generous limits (100-1000/min); tune based on traffic patterns |
| Not associating with Front Door | Policy exists but no traffic is inspected | Create `securityPolicy` resource linking WAF to Front Door endpoints |

## Production Backlog Items

| Item | Priority | Description |
|------|----------|-------------|
| Prevention mode | P1 | Switch from Detection to Prevention after tuning false positives |
| Bot protection | P1 | Enable BotManagerRuleSet to block malicious bots |
| Rate limiting | P1 | Add rate limit rules per IP to prevent abuse and scraping |
| Geo-filtering | P2 | Block traffic from countries not relevant to the application |
| Custom rules | P2 | Add IP allow/block lists and header-based rules |
| Diagnostic logging | P1 | Enable WAF logs to Log Analytics for blocked request analysis |
| False positive tuning | P2 | Review Detection mode logs and configure exclusions for legitimate traffic |
| Alert on blocks | P2 | Configure alerts for high block rates indicating attack or misconfiguration |
| Rule set updates | P2 | Monitor and update to latest managed rule set versions |
| Per-route policies | P3 | Create separate policies for API vs. web endpoints with different rules |
