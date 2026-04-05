# Data Layer

The layer responsible for all data services, schemas, and access patterns. Owned by the `data-architect`, with IaC implemented by `terraform-agent` or `bicep-agent`.

## Owner

- **Primary**: `data-architect`
- **Delegates to**: `terraform-agent` or `bicep-agent` for Azure resource provisioning
- **Security review**: `security-architect` reviews data-plane security (RBAC, encryption, access patterns)

## Service Categories

### Relational Databases

- Azure SQL Server and databases
- PostgreSQL Flexible Server
- MySQL Flexible Server

**ARM Namespaces**: `Microsoft.Sql/*`, `Microsoft.DBforPostgreSQL/*`, `Microsoft.DBforMySQL/*`

### NoSQL / Document Databases

- Cosmos DB (all APIs: SQL, MongoDB, Cassandra, Gremlin, Table)

**ARM Namespaces**: `Microsoft.DocumentDB/*`

### Caching

- Azure Cache for Redis

**ARM Namespaces**: `Microsoft.Cache/*`

### Storage

- Storage Accounts (Blob, Table, Queue, File, Data Lake)

**ARM Namespaces**: `Microsoft.Storage/*`

### Messaging & Streaming

- Service Bus (namespaces, queues, topics)
- Event Hubs (namespaces, event hubs, consumer groups)

**ARM Namespaces**: `Microsoft.ServiceBus/*`, `Microsoft.EventHub/*`

### Data Processing

- Azure Databricks
- Azure Data Factory
- Azure Synapse Analytics

**ARM Namespaces**: `Microsoft.Databricks/*`, `Microsoft.DataFactory/*`, `Microsoft.Synapse/*`

### Secrets & Configuration

- Azure Key Vault (vaults, secrets, keys, certificates)

**ARM Namespaces**: `Microsoft.KeyVault/*`

## What Does NOT Belong Here

- **Network resources** for data services (private endpoints, DNS zones) -- those are Infrastructure layer
- **Application code** that reads/writes data (repositories, ORMs, query logic) -- that is Application layer (data access sub-layer)
- **Compute resources** that process data (Functions, Container Apps) -- those are Infrastructure layer
- **Observability** (Log Analytics, App Insights) -- those are Core layer

## Key Boundary: Data Service vs Data Access

The Data layer provisions the Azure data resource (e.g., creates a Cosmos DB account, database, and container via IaC) and defines the data model (schemas, indexes, partition keys). The Application layer's *data access sub-layer* contains the code that interacts with these resources (e.g., repository classes, ORM mappings, query builders).

## Deployment Order

Data deploys **after Core and Networking**, before Application:

1. **Key Vault** -- first, because other data services may store secrets in it
2. **Databases** -- SQL, Cosmos, PostgreSQL, etc.
3. **Storage** -- Storage Accounts
4. **Messaging** -- Service Bus, Event Hubs
5. **Data Processing** -- Databricks, Data Factory (if present)

Each data stage references:
- Core outputs: managed identity principal_id (for RBAC), Log Analytics workspace_id (for diagnostics)
- Networking outputs: private endpoint connectivity (created by Infrastructure layer)

## Inter-Layer Communication

| Consumer | What Data Provides |
|----------|-------------------|
| Application | Connection endpoints, Key Vault secret URIs, database connection metadata |
| Infrastructure | (Data does not typically provide to Infrastructure -- dependency flows downward) |
| Core | (Data does not provide to Core) |

## Governance

- All data services must use Entra-based authentication (managed identity RBAC, no connection strings with keys)
- Cosmos DB data-plane roles must use `sqlRoleAssignments`, not ARM `roleAssignments`
- Key Vault must use RBAC authorization model (not access policies)
- Encryption at rest must be enabled (service-managed keys minimum)
- TLS 1.2+ required for all data service connections
- Key Vault should deploy first among data services (other services reference its secrets)
