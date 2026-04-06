---
service_namespace: Microsoft.ApiManagement/service/apis/policies
display_name: API Management API Policy
depends_on:
  - Microsoft.ApiManagement/service/apis
---

# API Management API Policy

> XML-based policy document applied at the API scope to control inbound, backend, outbound, and error handling behavior for all operations under an API.

## When to Use
- Apply cross-cutting concerns (authentication, rate limiting, caching, CORS) to all operations in an API
- Transform request/response payloads (JSON-to-XML, header injection, URL rewrite)
- Authenticate to backend services using managed identity tokens
- Implement retry, circuit breaker, or fallback logic at the gateway level
- Enforce IP filtering or JWT validation before requests reach the backend

Policies can be applied at global, product, API, or operation scope. API-scope policies override global/product-scope and are overridden by operation-scope policies. Use `<base />` to inherit from parent scopes.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Policy format | XML (raw) | `format: "xml"` or `"xml-link"` for external URL |
| CORS | Allow all origins | Tighten for production |
| Backend auth | Managed identity | `<authentication-managed-identity>` in inbound |
| Rate limiting | None for POC | Add per-subscription or per-IP for production |
| Caching | Disabled | Enable for read-heavy APIs |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "api_policy" {
  type      = "Microsoft.ApiManagement/service/apis/policies@2023-09-01-preview"
  name      = "policy"
  parent_id = azapi_resource.api.id

  body = {
    properties = {
      format = "xml"
      value  = <<XML
<policies>
  <inbound>
    <base />
    <cors allow-credentials="false">
      <allowed-origins>
        <origin>*</origin>
      </allowed-origins>
      <allowed-methods>
        <method>GET</method>
        <method>POST</method>
        <method>PUT</method>
        <method>DELETE</method>
      </allowed-methods>
      <allowed-headers>
        <header>Content-Type</header>
        <header>Authorization</header>
      </allowed-headers>
    </cors>
    <authentication-managed-identity resource="${var.backend_app_id_uri}" />
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>
XML
    }
  }
}
```

### RBAC Assignment

```hcl
# Policy management inherits from the parent APIM service RBAC.
# API Management Service Contributor (312a565d-c81f-4fd8-895a-4e21e48d571c) covers policy management.
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Backend app ID URI for managed identity authentication')
param backendAppIdUri string

resource apiPolicy 'Microsoft.ApiManagement/service/apis/policies@2023-09-01-preview' = {
  parent: api
  name: 'policy'
  properties: {
    format: 'xml'
    value: '<policies><inbound><base /><cors allow-credentials="false"><allowed-origins><origin>*</origin></allowed-origins><allowed-methods><method>GET</method><method>POST</method></allowed-methods><allowed-headers><header>Content-Type</header><header>Authorization</header></allowed-headers></cors><authentication-managed-identity resource="${backendAppIdUri}" /></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>'
  }
}
```

**Tip:** For readability, store policy XML in a separate file and use `loadTextContent('policy.xml')` in Bicep.

## Application Code

### Python
Infrastructure -- transparent to application code. Policies execute at the APIM gateway layer; backend applications are unaware of them.

### C#
Infrastructure -- transparent to application code. Policies execute at the APIM gateway layer; backend applications are unaware of them.

### Node.js
Infrastructure -- transparent to application code. Policies execute at the APIM gateway layer; backend applications are unaware of them.

## Common Pitfalls

1. **Malformed XML breaks the entire API** -- A single missing closing tag or invalid attribute silently breaks request processing. Always validate XML before deployment.
2. **Forgetting `<base />`** -- Without `<base />` in each section, parent-scope policies (global, product) are not inherited. This can disable logging, rate limiting, or other global policies.
3. **Policy name must be `"policy"`** -- The resource name for an API-level policy must always be `"policy"`. Using any other name results in a deployment error.
4. **Managed identity resource vs audience confusion** -- The `resource` attribute on `<authentication-managed-identity>` is the backend App ID URI, not the APIM resource ID.
5. **CORS policy must come before authentication** -- If CORS preflight requests (OPTIONS) are blocked by authentication, browsers cannot reach the API at all.
6. **XML special characters** -- Values containing `<`, `>`, `&` must be XML-escaped or wrapped in CDATA sections. Terraform heredocs with embedded XML are prone to this.
7. **Policy size limit** -- Policies have a 256 KB size limit. Embedding large XSLT transforms or schemas can exceed this.

## Production Backlog Items

- [ ] Replace wildcard CORS origins with specific allowed domains
- [ ] Add `<rate-limit-by-key>` or `<quota-by-key>` policies for abuse protection
- [ ] Implement `<validate-jwt>` policy with Azure AD token validation
- [ ] Add response caching with `<cache-lookup>` / `<cache-store>` for read-heavy endpoints
- [ ] Configure `<retry>` policy for resilient backend communication
- [ ] Add `<set-header>` policies to strip internal headers from outbound responses
- [ ] Implement `<ip-filter>` policy to restrict access by client IP range
- [ ] Move policy XML to source-controlled files and deploy via CI/CD pipeline
