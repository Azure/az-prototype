# Azure Databricks
> Unified analytics platform for data engineering, data science, and machine learning built on Apache Spark with collaborative notebooks and Delta Lake.

## When to Use

- **Large-scale data processing** -- Spark-based ETL/ELT for petabyte-scale data
- **Machine learning** -- MLflow-based experiment tracking, model training, and deployment
- **Delta Lake** -- ACID transactions, schema enforcement, and time travel on data lakes
- **Real-time streaming** -- Structured Streaming for continuous data processing
- **Collaborative analytics** -- shared notebooks for data engineers, scientists, and analysts
- **Unity Catalog governance** -- centralized data cataloging, lineage, and access control

Choose Databricks over Fabric when you need advanced Spark tuning, custom ML pipelines, multi-cloud portability, or have existing Databricks investments. Choose Fabric for simpler analytics with Power BI integration and T-SQL access.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Pricing tier | Premium | Required for Unity Catalog, RBAC; not significantly more expensive |
| Cluster type | Single-node | Smallest for development; no worker nodes |
| Node type | Standard_D4s_v5 | 4 vCPU, 16 GiB; good balance for POC |
| Auto-termination | 30 minutes | Prevent idle cluster costs |
| Runtime | Latest LTS | e.g., 14.3 LTS with Spark 3.5 |
| Unity Catalog | Enabled | Free with Premium tier; required for governance |
| Public network access | Enabled (POC) | Flag VNet injection as production backlog item |

## Terraform Patterns

### Basic Resource

```hcl
resource "azurerm_databricks_workspace" "this" {
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  sku                           = "premium"  # Required for Unity Catalog
  managed_resource_group_name   = "${var.resource_group_name}-databricks-managed"
  public_network_access_enabled = true  # Set false for VNet injection

  tags = var.tags
}
```

### VNet Injection

```hcl
resource "azurerm_databricks_workspace" "this" {
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  sku                           = "premium"
  managed_resource_group_name   = "${var.resource_group_name}-databricks-managed"
  public_network_access_enabled = false

  custom_parameters {
    virtual_network_id                                   = var.vnet_id
    public_subnet_name                                   = var.public_subnet_name
    private_subnet_name                                  = var.private_subnet_name
    public_subnet_network_security_group_association_id   = var.public_nsg_association_id
    private_subnet_network_security_group_association_id  = var.private_nsg_association_id
    no_public_ip                                         = true  # Secure cluster connectivity
  }

  tags = var.tags
}
```

### Unity Catalog Metastore

```hcl
# Storage account for Unity Catalog metastore
resource "azurerm_storage_account" "unity" {
  name                     = var.unity_storage_name
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  is_hns_enabled           = true  # Hierarchical namespace (ADLS Gen2)

  tags = var.tags
}

resource "azurerm_storage_container" "unity" {
  name                  = "unity-catalog"
  storage_account_name  = azurerm_storage_account.unity.name
  container_access_type = "private"
}

# Unity Catalog metastore (via Databricks provider)
resource "databricks_metastore" "this" {
  name          = "poc-metastore"
  storage_root  = "abfss://unity-catalog@${azurerm_storage_account.unity.name}.dfs.core.windows.net/"
  force_destroy = true  # POC only
  owner         = var.admin_group_name
}

resource "databricks_metastore_assignment" "this" {
  workspace_id         = azurerm_databricks_workspace.this.workspace_id
  metastore_id         = databricks_metastore.this.id
  default_catalog_name = "main"
}
```

### RBAC Assignment

```hcl
# Contributor on workspace (ARM-level management)
resource "azurerm_role_assignment" "dbw_contributor" {
  scope                = azurerm_databricks_workspace.this.id
  role_definition_name = "Contributor"
  principal_id         = var.admin_identity_principal_id
}

# Grant Databricks managed identity access to storage for Unity Catalog
resource "azurerm_role_assignment" "unity_blob_contributor" {
  scope                = azurerm_storage_account.unity.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = databricks_metastore.this.delta_sharing_organization_name  # Access connector ID
}
```

**Note:** Databricks uses its own ACL system for data-plane access (workspace groups, Unity Catalog grants). ARM RBAC controls management-plane access only.

### Private Endpoint

Databricks uses **VNet injection** (see above) rather than traditional private endpoints. For additional frontend private endpoint access:

```hcl
resource "azurerm_private_endpoint" "databricks" {
  count = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0

  name                = "pe-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id

  private_service_connection {
    name                           = "psc-${var.name}"
    private_connection_resource_id = azurerm_databricks_workspace.this.id
    subresource_names              = ["databricks_ui_api"]
    is_manual_connection           = false
  }

  dynamic "private_dns_zone_group" {
    for_each = var.private_dns_zone_id != null ? [1] : []
    content {
      name                 = "dns-zone-group"
      private_dns_zone_ids = [var.private_dns_zone_id]
    }
  }

  tags = var.tags
}
```

Private DNS zone: `privatelink.azuredatabricks.net`

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the Databricks workspace')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Managed resource group name')
param managedResourceGroupName string = '${resourceGroup().name}-databricks-managed'

@description('Tags to apply')
param tags object = {}

resource workspace 'Microsoft.Databricks/workspaces@2024-05-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'premium'
  }
  properties: {
    managedResourceGroupId: subscriptionResourceId('Microsoft.Resources/resourceGroups', managedResourceGroupName)
    publicNetworkAccess: 'Enabled'
    requiredNsgRules: 'AllRules'
  }
}

output id string = workspace.id
output name string = workspace.name
output url string = 'https://${workspace.properties.workspaceUrl}'
output workspaceId string = workspace.properties.workspaceId
```

### VNet Injection (Bicep)

```bicep
@description('VNet ID')
param vnetId string

@description('Public subnet name')
param publicSubnetName string

@description('Private subnet name')
param privateSubnetName string

resource workspace 'Microsoft.Databricks/workspaces@2024-05-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'premium'
  }
  properties: {
    managedResourceGroupId: subscriptionResourceId('Microsoft.Resources/resourceGroups', managedResourceGroupName)
    publicNetworkAccess: 'Disabled'
    requiredNsgRules: 'NoAzureDatabricksRules'
    parameters: {
      customVirtualNetworkId: {
        value: vnetId
      }
      customPublicSubnetName: {
        value: publicSubnetName
      }
      customPrivateSubnetName: {
        value: privateSubnetName
      }
      enableNoPublicIp: {
        value: true
      }
    }
  }
}
```

### RBAC Assignment

No data-plane RBAC via ARM -- use Databricks workspace ACLs and Unity Catalog grants.

## Application Code

### Python — Databricks SDK (External)

```python
from databricks.sdk import WorkspaceClient

# Authenticate using Azure AD (DefaultAzureCredential)
w = WorkspaceClient(
    host="https://adb-1234567890.1.azuredatabricks.net",
    azure_workspace_resource_id="/subscriptions/.../resourceGroups/.../providers/Microsoft.Databricks/workspaces/...",
)

# List clusters
for c in w.clusters.list():
    print(f"{c.cluster_name}: {c.state}")

# Run a notebook job
from databricks.sdk.service.jobs import Task, NotebookTask

run = w.jobs.submit(
    run_name="my-etl-job",
    tasks=[
        Task(
            task_key="etl",
            existing_cluster_id="0123-456789-abcdef",
            notebook_task=NotebookTask(
                notebook_path="/Repos/etl/transform",
                base_parameters={"date": "2024-01-01"},
            ),
        )
    ],
).result()
```

### Python — Notebook Code (Databricks Runtime)

```python
# Runs inside a Databricks notebook
# Unity Catalog table access
df = spark.read.table("main.default.customers")

# Transform with Delta Lake
from pyspark.sql.functions import col, current_timestamp

df_processed = (
    df.filter(col("status") == "active")
    .withColumn("processed_at", current_timestamp())
)

# Write to Unity Catalog table
df_processed.write.mode("overwrite").saveAsTable("main.default.active_customers")

# Access Azure Blob Storage via Unity Catalog external location
df_external = spark.read.format("csv").load("abfss://data@mystorageaccount.dfs.core.windows.net/raw/")
```

### SQL — Databricks SQL

```sql
-- Unity Catalog SQL queries
USE CATALOG main;
USE SCHEMA default;

CREATE TABLE IF NOT EXISTS orders (
    order_id BIGINT GENERATED ALWAYS AS IDENTITY,
    customer_id BIGINT,
    total DECIMAL(10, 2),
    created_at TIMESTAMP DEFAULT current_timestamp()
)
USING DELTA
TBLPROPERTIES ('delta.enableChangeDataFeed' = true);

-- Time travel
SELECT * FROM orders VERSION AS OF 5;
SELECT * FROM orders TIMESTAMP AS OF '2024-01-01T00:00:00Z';
```

## Common Pitfalls

1. **Managed resource group conflicts** -- Databricks creates a managed resource group for VMs, disks, and NSGs. The name must not already exist. Use a predictable naming convention.
2. **VNet injection subnet sizing** -- Each cluster node uses one IP. Public and private subnets need /26 minimum (64 IPs) for small clusters, /22 for production. Under-sized subnets cause cluster launch failures.
3. **Unity Catalog access connector** -- Unity Catalog needs a Databricks Access Connector resource with managed identity and Storage Blob Data Contributor on the metastore storage account. Without this, catalog operations fail.
4. **DBU pricing model** -- Costs are per DBU (Databricks Unit), not per VM. Different workload types (Jobs, SQL, All-Purpose) have different DBU rates. All-Purpose clusters are 2-3x more expensive than Jobs clusters.
5. **Cluster auto-termination** -- Default is 120 minutes. Set to 30 minutes for POC to reduce costs. Interactive clusters left running over weekends can be expensive.
6. **Spark version compatibility** -- Libraries pinned to specific Spark versions may break on runtime upgrades. Use LTS runtimes and test library compatibility.
7. **Secret management** -- Never hardcode secrets in notebooks. Use Databricks secret scopes backed by Azure Key Vault.
8. **Premium tier required for key features** -- Unity Catalog, RBAC, cluster policies, audit logs all require Premium tier. Standard tier is rarely sufficient.

## Production Backlog Items

- [ ] Enable VNet injection with no-public-IP for secure cluster connectivity
- [ ] Configure Unity Catalog with production metastore and access controls
- [ ] Set up cluster policies to control cost and compliance
- [ ] Enable audit logging to Log Analytics
- [ ] Configure IP access lists for workspace access control
- [ ] Implement CI/CD with Databricks Asset Bundles or Repos
- [ ] Set up automated job clusters (cheaper than interactive clusters)
- [ ] Configure disaster recovery with workspace replication
- [ ] Enable customer-managed keys for encryption at rest
- [ ] Implement data lineage tracking with Unity Catalog
- [ ] Set up cost monitoring and budget alerts per workspace
