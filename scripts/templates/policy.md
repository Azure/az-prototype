# Logic Apps
Governance policies for Logic Apps

**Domain:** `azure-management`

### Patterns

<table>
<thead>
<tr>
<th>Name</th><th>Description</th>
</tr>
</thead>
<tbody>
<tr><td>Logic App with managed identity and access control</td><td>Secure Logic App with managed identity, IP restrictions, and Key Vault-backed parameters</td></tr>
</tbody>
</table>

### Anti-Patterns

<table>
<thead>
<tr>
<th>Description</th><th>Instead</th>
</tr>
</thead>
<tbody>
<tr><td>Do not hardcode credentials in workflow parameters</td><td>Use managed identity for API connections and Key Vault references for secrets</td></tr>
<tr><td>Do not expose trigger URLs without access restrictions</td><td>Configure allowedCallerIpAddresses to restrict trigger invocation</td></tr>
</tbody>
</table>

### References

- [Logic Apps security overview](https://learn.microsoft.com/azure/logic-apps/logic-apps-securing-a-logic-app)
- [Logic Apps managed identity](https://learn.microsoft.com/azure/logic-apps/authenticate-with-managed-identity)

<hr />

### Checks (3)

<table>
<thead>
<tr>
<th width="185">Check</th><th>Severity</th><th>Description</th>
</tr>
</thead>
<tbody>
<tr><td><a href="#AZ-LA-001">AZ-LA-001</a></td><td>Required</td><td>Deploy Logic Apps Standard with managed identity, VNet integration, and disabled public access</td></tr>
<tr><td><a href="#AZ-LA-002">AZ-LA-002</a></td><td>Required</td><td>Use managed identity for all API connections instead of connection strings</td></tr>
</tbody>
</table>

<hr />

## AZ-LA-001
Deploy Logic Apps Standard with managed identity, VNet integration, and disabled public access

**Severity:** Required  
**Rationale:** Logic Apps process business workflows that often handle sensitive data; managed identity eliminates connection credentials.  
**Agents:** `terraform-agent, bicep-agent, cloud-architect`

### Targets

- Microsoft.Logic/workflows

### Companion Resources

<table>
<thead>
<tr>
<th>Resource</th><th>Name</th><th>Purpose</th>
</tr>
</thead>
<tbody>
<tr><td>Microsoft.Insights/diagnosticSettings</td><td>diag-logic-app</td><td>Diagnostic settings to route workflow run logs and trigger events to Log Analytics</td></tr>
<tr><td>Microsoft.Authorization/roleAssignments</td><td>Logic App Contributor</td><td>RBAC role assignments for Logic App management</td></tr>
</tbody>
</table>

### Prohibitions

- Never hardcode connection strings or credentials in workflow parameters
- Never leave accessControl IP restrictions empty in production — use VNet or specific IPs
- Never embed secrets directly in workflow definitions — use Key Vault references
- Never disable managed identity — it is required for secure API connections
- Never use shared access signature (SAS) trigger URLs without IP restrictions

<hr />

## AZ-LA-002
Use managed identity for all API connections instead of connection strings

**Severity:** Required  
**Rationale:** Connection strings are shared secrets; managed identity provides per-connection, auditable access.  
**Agents:** `terraform-agent, bicep-agent, cloud-architect, app-developer`

### Targets

- Microsoft.Logic/workflows

<hr />
