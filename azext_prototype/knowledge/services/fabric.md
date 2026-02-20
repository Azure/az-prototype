# Microsoft Fabric
> Unified analytics platform combining data engineering, data science, real-time analytics, data warehousing, and business intelligence in a single SaaS experience with OneLake storage.

## When to Use

- **Unified analytics** -- single platform for data engineering (Spark), warehousing (T-SQL), real-time analytics (KQL), and BI (Power BI)
- **Lakehouse architecture** -- Delta Lake format on OneLake with both Spark and T-SQL access
- **Power BI integration** -- native semantic models, DirectLake mode for sub-second queries over large datasets
- **Data mesh / domain-oriented analytics** -- workspaces as domain boundaries with shared OneLake storage
- **Simplified data platform** -- replace separate Synapse, Data Factory, Power BI, and ADLS resources with one platform

Choose Fabric over individual Azure services (Synapse, ADF, ADLS) when you want a unified experience with simplified management. Choose individual services when you need fine-grained ARM control, VNet integration, or have existing Synapse/ADF investments.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Capacity SKU | F2 | Smallest Fabric capacity; 2 CUs, ~$0.36/hr |
| Capacity auto-pause | Enabled | Pause after inactivity to reduce cost |
| Workspace | Default | One workspace per POC; add more as domains emerge |
| OneLake | Included | Automatic; no separate storage account needed |
| Trial | 60-day free trial | Available per tenant; no capacity purchase needed |

## Terraform Patterns

### Basic Resource

Fabric capacities can be deployed via Terraform. Workspaces, lakehouses, and other items are managed through Fabric REST APIs or the Fabric portal.

```hcl
resource "azurerm_fabric_capacity" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location

  sku {
    name = "F2"  # F2, F4, F8, F16, F32, F64, etc.
    tier = "Fabric"
  }

  administration {
    members = var.admin_upns  # UPNs of capacity admins
  }

  tags = var.tags
}
```

### Workspace (via REST API / PowerShell)

Fabric workspaces cannot be created via Terraform or Bicep. Use the Fabric REST API or PowerShell:

```bash
# Create workspace via Fabric REST API
curl -X POST "https://api.fabric.microsoft.com/v1/workspaces" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "displayName": "my-poc-workspace",
    "capacityId": "<capacity-id>",
    "description": "POC workspace"
  }'
```

```powershell
# Or via PowerShell
Install-Module -Name MicrosoftPowerBIMgmt
Connect-PowerBIServiceAccount
New-PowerBIWorkspace -Name "my-poc-workspace"
# Assign to capacity via portal or REST API
```

### RBAC Assignment

Fabric uses its own workspace-level role system rather than ARM RBAC:

```hcl
# ARM-level: Fabric capacity roles
resource "azurerm_role_assignment" "fabric_contributor" {
  scope                = azurerm_fabric_capacity.this.id
  role_definition_name = "Contributor"
  principal_id         = var.admin_identity_principal_id
}
```

Workspace-level roles (Admin, Member, Contributor, Viewer) are managed through the Fabric portal or REST API, not ARM.

### Private Endpoint

Fabric supports private endpoints for the capacity resource:

```hcl
resource "azurerm_private_endpoint" "fabric" {
  count = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0

  name                = "pe-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id

  private_service_connection {
    name                           = "psc-${var.name}"
    private_connection_resource_id = azurerm_fabric_capacity.this.id
    subresource_names              = ["fabric"]
    is_manual_connection           = false
  }

  tags = var.tags
}
```

**Note:** Fabric private endpoints secure connectivity to the capacity from your VNet. OneLake data access goes through the Fabric service — use managed private endpoints in Fabric for data source connections.

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Fabric capacity')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Admin UPNs for the capacity')
param adminMembers array

@description('Tags to apply')
param tags object = {}

resource fabricCapacity 'Microsoft.Fabric/capacities@2023-11-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'F2'
    tier: 'Fabric'
  }
  properties: {
    administration: {
      members: adminMembers
    }
  }
}

output id string = fabricCapacity.id
output name string = fabricCapacity.name
```

### RBAC Assignment

No data-plane RBAC via ARM -- workspace roles managed through Fabric portal/API.

## Application Code

### Python — Spark Notebook (Fabric Runtime)

```python
# Fabric Spark notebook -- runs in the Fabric Spark runtime
# No pip install needed; libraries pre-installed

# Read from lakehouse
df = spark.read.format("delta").load("Tables/customers")

# Transform
from pyspark.sql.functions import col, current_timestamp

df_enriched = df.withColumn("processed_at", current_timestamp()) \
    .filter(col("status") == "active")

# Write to lakehouse table
df_enriched.write.format("delta").mode("overwrite").save("Tables/active_customers")
```

### Python — Fabric REST API (External)

```python
from azure.identity import DefaultAzureCredential
import requests

credential = DefaultAzureCredential()
token = credential.get_token("https://api.fabric.microsoft.com/.default")

headers = {
    "Authorization": f"Bearer {token.token}",
    "Content-Type": "application/json",
}

# List workspaces
response = requests.get("https://api.fabric.microsoft.com/v1/workspaces", headers=headers)
workspaces = response.json()["value"]

# Run notebook
response = requests.post(
    f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/items/{notebook_id}/jobs/instances?jobType=RunNotebook",
    headers=headers,
)
```

### T-SQL — Warehouse / SQL Endpoint

```sql
-- Fabric SQL endpoint (read-only over lakehouse tables)
-- or Fabric Warehouse (read-write T-SQL)
SELECT
    c.customer_id,
    c.name,
    SUM(o.total) AS total_spend
FROM lakehouse.dbo.customers c
JOIN lakehouse.dbo.orders o ON c.customer_id = o.customer_id
GROUP BY c.customer_id, c.name
ORDER BY total_spend DESC;
```

### KQL — Real-Time Analytics

```kusto
// Fabric KQL database for real-time streaming data
Events
| where Timestamp > ago(1h)
| summarize Count = count(), AvgDuration = avg(Duration) by bin(Timestamp, 5m), EventType
| order by Timestamp desc
```

## Common Pitfalls

1. **Capacity vs workspace confusion** -- Capacity is the compute resource (ARM-deployed). Workspaces are containers for items (portal/API-managed). Capacity must exist before workspace can be assigned.
2. **Capacity auto-pause delays** -- After pausing, resuming takes 1-2 minutes. First query after resume may time out. Design retry logic.
3. **OneLake shortcuts vs copies** -- Shortcuts provide zero-copy access to external data (ADLS, S3, other lakehouses). Data is not duplicated. Use shortcuts for POC to avoid data movement.
4. **Workspace roles are Fabric-specific** -- Not Azure RBAC. Admin, Member, Contributor, Viewer roles are managed in the Fabric portal, not ARM templates.
5. **Delta Lake format required** -- OneLake tables must be Delta Lake format. Parquet files in the "Files" section are accessible but not queryable via SQL endpoint without conversion.
6. **Capacity Units (CU) throttling** -- F2 has limited CUs. Concurrent Spark jobs and SQL queries share the capacity. Monitor utilization and scale up if throttled.
7. **No VNet injection** -- Unlike Synapse, Fabric doesn't support VNet injection for Spark clusters. Use managed private endpoints and trusted workspace access for data source connectivity.

## Production Backlog Items

- [ ] Upgrade capacity from F2 to appropriate size based on workload
- [ ] Configure managed private endpoints for data source connectivity
- [ ] Enable private endpoint access for the capacity
- [ ] Set up Git integration for workspace version control
- [ ] Configure deployment pipelines (Dev → Test → Prod)
- [ ] Implement data governance with Microsoft Purview integration
- [ ] Set up monitoring and alerts for capacity utilization
- [ ] Configure workspace-level access controls and row-level security
- [ ] Enable audit logging for compliance
- [ ] Implement data lineage tracking across items
