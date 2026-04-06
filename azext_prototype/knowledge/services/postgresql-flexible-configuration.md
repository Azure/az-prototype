---
service_namespace: Microsoft.DBforPostgreSQL/flexibleServers/configurations
display_name: PostgreSQL Flexible Server Configuration
depends_on:
  - Microsoft.DBforPostgreSQL/flexibleServers
---

# PostgreSQL Flexible Server Configuration

> Server-level parameters that control PostgreSQL engine behavior, extensions, and performance tuning on Azure Database for PostgreSQL Flexible Server.

## When to Use
- Enable PostgreSQL extensions (pgvector, PostGIS, pg_stat_statements, pgaadauth)
- Tune performance parameters (shared_buffers, work_mem, max_connections)
- Configure logging and auditing settings
- Enable connection pooling (PgBouncer) at the server level
- Required to enable `azure.extensions` before using any non-default extensions

## POC Defaults
- **azure.extensions**: pgcrypto,uuid-ossp (add pgvector for AI workloads, pgaadauth for Entra auth)
- **pgbouncer.enabled**: false (enable for connection pooling)
- **log_checkpoints**: on
- **log_connections**: on

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "pg_config_extensions" {
  type      = "Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-06-01-preview"
  name      = "azure.extensions"
  parent_id = azapi_resource.pg_server.id

  body = {
    properties = {
      value  = "pgcrypto,uuid-ossp,pgaadauth"
      source = "user-override"
    }
  }
}

resource "azapi_resource" "pg_config_pgbouncer" {
  type      = "Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-06-01-preview"
  name      = "pgbouncer.enabled"
  parent_id = azapi_resource.pg_server.id

  body = {
    properties = {
      value  = "true"
      source = "user-override"
    }
  }
}
```

### RBAC Assignment
```hcl
# Configuration changes require Contributor or Owner on the Flexible Server resource.
# No separate RBAC role exists for configuration management.
```

## Bicep Patterns

### Basic Resource
```bicep
resource extensionsConfig 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-06-01-preview' = {
  parent: pgServer
  name: 'azure.extensions'
  properties: {
    value: 'pgcrypto,uuid-ossp,pgaadauth'
    source: 'user-override'
  }
}

resource pgbouncerConfig 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-06-01-preview' = {
  parent: pgServer
  name: 'pgbouncer.enabled'
  properties: {
    value: 'true'
    source: 'user-override'
  }
}
```

## Application Code

### Python
Infrastructure — transparent to application code

### C#
Infrastructure — transparent to application code

### Node.js
Infrastructure — transparent to application code

## Common Pitfalls
- **Server restart required**: Some parameters (like `shared_preload_libraries`) require a server restart to take effect. The deployment may appear successful but changes won't apply until restart.
- **Extension allowlist first**: You must add extensions to `azure.extensions` before you can `CREATE EXTENSION` in SQL. Forgetting this step produces `extension not available` errors.
- **PgBouncer port differs**: When PgBouncer is enabled, applications connect on port 6432 (not 5432). Using the wrong port causes connection failures.
- **Dependent configurations**: Some parameters depend on others (e.g., `pgbouncer.default_pool_size` only works when `pgbouncer.enabled` is true).
- **Read-only parameters**: Some parameters (like `max_connections`) are read-only on certain SKUs — the API accepts the change but it has no effect.

## Production Backlog Items
- Performance tuning based on workload profiling (shared_buffers, work_mem, effective_cache_size)
- Enable pg_stat_statements for query performance monitoring
- Configure audit logging via pgaudit extension
- SSL enforcement and minimum TLS version configuration
