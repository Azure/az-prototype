# Logic Apps
Governance policies for Logic Apps

**Domain:** `azure-management`

### Patterns

| Name | Description |
| ---- | ----------- |
| Logic App with managed identity and access control | Secure Logic App with managed identity, IP restrictions, and Key Vault-backed parameters |

### Anti-Patterns

| Description | Instead |
| ----------- | ------- |
| Do not hardcode credentials in workflow parameters | Use managed identity for API connections and Key Vault references for secrets |
| Do not expose trigger URLs without access restrictions | Configure allowedCallerIpAddresses to restrict trigger invocation |

### References

- [Logic Apps security overview](https://learn.microsoft.com/azure/logic-apps/logic-apps-securing-a-logic-app)
- [Logic Apps managed identity](https://learn.microsoft.com/azure/logic-apps/authenticate-with-managed-identity)

<hr />

### Checks (3)

| Check | Severity | Description |
| ----- | -------- | ----------- |
| <span style="text-wrap:nowrap;">[AZ-LA-001](#AZ-LA-001)</span> | Required | Deploy Logic Apps Standard with managed identity, VNet integration, and disabled public access |
| <span style="text-wrap:nowrap;">[AZ-LA-002](#AZ-LA-002)</span> | Required | Use managed identity for all API connections instead of connection strings |

<hr />

## AZ-LA-001
Deploy Logic Apps Standard with managed identity, VNet integration, and disabled public access

**Severity:** Required  
**Rationale:** Logic Apps process business workflows that often handle sensitive data; managed identity eliminates connection credentials.
**Agents:** `terraform-agent, bicep-agent, cloud-architect`

### Targets

- Microsoft.Logic/workflows

### Companion Resources

| Resource | Name | Purpose |
| -------- | ---- | ------- |
| <span style="text-wrap:nowrap;">Microsoft.Insights/diagnosticSettings</span> | <span style="text-wrap:nowrap;">diag-logic-app</span> | Diagnostic settings to route workflow run logs and trigger events to Log Analytics |
| <span style="text-wrap:nowrap;">Microsoft.Authorization/roleAssignments</span> | <span style="text-wrap:nowrap;">Logic App Contributor</span> | RBAC role assignments for Logic App management |

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