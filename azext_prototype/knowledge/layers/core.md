# Core Layer

The foundational layer that all other layers depend on. Owned exclusively by the `cloud-architect`.

## Owner

- **Primary**: `cloud-architect`
- **No delegation** -- the cloud-architect directly manages all Core Layer concerns

## Services

Core Layer services are cross-cutting foundations, not individual Azure resources:

| Concern | Examples |
|---------|----------|
| Management Groups & Subscriptions | Resource group strategy, subscription alignment |
| Regions & Availability | Primary/secondary regions, zone selection |
| Naming Conventions | Project naming strategy applied to all resources |
| Security & Identity | Managed identity (user-assigned), Entra ID configuration |
| Observability Foundation | Log Analytics workspace, Application Insights |

### ARM Namespaces in This Layer

- `Microsoft.ManagedIdentity/userAssignedIdentities`
- `Microsoft.OperationalInsights/workspaces`
- `Microsoft.Insights/components` (Application Insights)

## What Does NOT Belong Here

- **Network resources** -- VNets, subnets, NSGs, private endpoints belong in the Infrastructure layer
- **Data services** -- databases, storage, messaging belong in the Data layer
- **Compute resources** -- Container Apps, App Service, Functions belong in the Infrastructure layer
- **Application code** -- all source code belongs in the Application layer

## Deployment Order

Core deploys **first**. All other layers depend on Core outputs:

- `principal_id` from managed identity (used for RBAC in every downstream stage)
- `workspace_id` from Log Analytics (used for diagnostic settings in every downstream stage)
- `instrumentation_key` / `connection_string` from Application Insights

## Inter-Layer Communication

| Consumer | What Core Provides |
|----------|-------------------|
| Infrastructure | Managed identity principal_id for RBAC, Log Analytics workspace_id for diagnostics |
| Data | Same as Infrastructure -- identity for data-plane RBAC, workspace for diagnostics |
| Application | Application Insights connection string for telemetry |

## Governance

- All resources must follow the project naming strategy
- Managed identity is mandatory -- no service principal secrets for service-to-service auth
- Log Analytics workspace must be created before any resource that emits diagnostics
- Application Insights must use workspace-based mode (not classic)
