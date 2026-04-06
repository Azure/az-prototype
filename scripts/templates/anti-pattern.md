# Encryption
TLS enforcement, encryption at rest, and transport security detection

**Domain:** `encryption`

<hr />

### Checks (3)

<table>
<thead>
<tr>
<th width="185">Check</th><th>Description</th>
</tr>
</thead>
<tbody>
<tr><td><a href="#ANTI-ENC-001">ANTI-ENC-001</a></td><td>Detects TLS version below 1.2 which has known vulnerabilities</td></tr>
<tr><td><a href="#ANTI-ENC-002">ANTI-ENC-002</a></td><td>Detects HTTPS disabled on App Service or Function Apps</td></tr>
</tbody>
</table>

<hr />

## ANTI-ENC-001
Detects TLS version below 1.2 which has known vulnerabilities

**Rationale:** TLS 1.0 and 1.1 have known vulnerabilities (BEAST, POODLE) and are deprecated by compliance frameworks.  
**Agents:** `terraform-agent, bicep-agent`

### Targets

<table>
<thead>
<tr>
<th>Services</th><th>Triggers On</th><th>Correct Patterns</th>
</tr>
</thead>
<tbody>
<tr><td><ul><li>Microsoft.Storage/storageAccounts</li><li>Microsoft.Sql/servers</li><li>Microsoft.Cache/redis</li><li>Microsoft.Web/sites</li><li>Microsoft.KeyVault/vaults</li><li>Microsoft.ServiceBus/namespaces</li><li>Microsoft.DocumentDB/databaseAccounts</li></ul></td><td><ul><li>'min_tls_version = "1.0"'</li><li>'min_tls_version = "1.1"'</li><li>'minimum_tls_version = "1.0"'</li><li>'minimum_tls_version = "1.1"'</li><li>"tls1_0"</li><li>"tls1_1"</li></ul></td><td><ul><li>'min_tls_version = "1.2"'</li><li>'minimum_tls_version = "1.2"'</li><li>'minimalTlsVersion = "1.2"'</li><li>'minimumTlsVersion = "TLS1_2"'</li></ul></td></tr>
</tbody>
</table>

<hr />

## ANTI-ENC-002
Detects HTTPS disabled on App Service or Function Apps

**Rationale:** HTTP transmits data in plaintext, exposing credentials and data to network interception.  
**Agents:** `terraform-agent, bicep-agent`

### Targets

<table>
<thead>
<tr>
<th>Services</th><th>Triggers On</th><th>Correct Patterns</th>
</tr>
</thead>
<tbody>
<tr><td><ul><li>Microsoft.Web/sites</li></ul></td><td><ul><li>"https_only = false"</li><li>"https_required = false"</li></ul></td><td><ul><li>"https_only = true"</li><li>"httpsOnly = true"</li></ul></td></tr>
</tbody>
</table>

<hr />
