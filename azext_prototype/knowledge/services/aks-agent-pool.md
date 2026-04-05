---
service_namespace: Microsoft.ContainerService/managedClusters/agentPools
display_name: AKS Agent Pool
depends_on:
  - Microsoft.ContainerService/managedClusters
---

# AKS Agent Pool

> Additional node pool in an AKS cluster, enabling workload isolation through separate VM sizes, scaling rules, OS types, and node labels/taints.

## When to Use
- **Workload isolation** -- separate system pods from application workloads on dedicated node pools
- **GPU workloads** -- add GPU-enabled VMs (NC/ND series) for ML inference or training
- **Windows containers** -- add Windows node pools alongside Linux system pool
- **Spot instances** -- cost savings for interruptible batch or dev/test workloads
- **Mixed VM sizes** -- different CPU/memory ratios for varied workload profiles (e.g., memory-intensive vs CPU-intensive)

Every AKS cluster has at least one System node pool. User node pools are added for application workloads. The System pool runs critical add-ons (CoreDNS, kube-proxy, metrics-server).

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Mode | User | System pool created with cluster; add User pools for workloads |
| VM size | Standard_D2s_v5 | 2 vCPU, 8 GiB; general-purpose for POC |
| Node count | 1 | Fixed for POC; enable autoscaler for production |
| OS type | Linux | Default; Windows for .NET Framework workloads |
| OS disk size | 50 GB | Ephemeral OS disk if VM supports it |
| Max pods per node | 110 | Azure CNI default; 250 max |
| Autoscaler | Disabled for POC | Enable with min/max for production |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "user_pool" {
  type      = "Microsoft.ContainerService/managedClusters/agentPools@2024-03-02-preview"
  name      = "workload"
  parent_id = azapi_resource.aks_cluster.id

  body = {
    properties = {
      vmSize             = "Standard_D2s_v5"
      count              = 1
      minCount           = 1
      maxCount           = 5
      enableAutoScaling  = false  # Enable for production
      osDiskSizeGB       = 50
      osDiskType         = "Managed"  # or "Ephemeral" for supported VMs
      mode               = "User"
      osType             = "Linux"
      osSKU              = "Ubuntu"   # or "AzureLinux"
      maxPods            = 110
      nodeLabels = {
        "workload-type" = "app"
      }
      upgradeSettings = {
        maxSurge = "10%"
      }
    }
  }
}
```

### RBAC Assignment

```hcl
# Agent pools inherit RBAC from the parent AKS cluster.
# No additional role assignments are needed at the pool level.
# Use Azure Kubernetes Service Cluster Admin (0ab0b1a8-8aac-4efd-b8c2-3ee1fb270be8)
# or Contributor on the cluster for node pool management.
```

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the agent pool (max 12 chars, lowercase, no hyphens)')
param poolName string

@description('VM size for the pool nodes')
param vmSize string = 'Standard_D2s_v5'

@description('Number of nodes')
param nodeCount int = 1

resource agentPool 'Microsoft.ContainerService/managedClusters/agentPools@2024-03-02-preview' = {
  parent: aksCluster
  name: poolName
  properties: {
    vmSize: vmSize
    count: nodeCount
    minCount: 1
    maxCount: 5
    enableAutoScaling: false
    osDiskSizeGB: 50
    osDiskType: 'Managed'
    mode: 'User'
    osType: 'Linux'
    osSKU: 'Ubuntu'
    maxPods: 110
    nodeLabels: {
      'workload-type': 'app'
    }
    upgradeSettings: {
      maxSurge: '10%'
    }
  }
}
```

## Application Code

### Python
Infrastructure -- transparent to application code. Node pools are a scheduling concern; applications target pools via node selectors and tolerations in Kubernetes manifests.

### C#
Infrastructure -- transparent to application code. Node pools are a scheduling concern; applications target pools via node selectors and tolerations in Kubernetes manifests.

### Node.js
Infrastructure -- transparent to application code. Node pools are a scheduling concern; applications target pools via node selectors and tolerations in Kubernetes manifests.

## Common Pitfalls

1. **Pool name constraints** -- Names must be lowercase alphanumeric, max 12 characters for Linux (6 for Windows). No hyphens, underscores, or uppercase letters. Deployment fails with a cryptic error otherwise.
2. **Cannot change VM size after creation** -- VM size is immutable on an existing pool. To change, create a new pool, cordon/drain the old one, and delete it.
3. **System pool cannot be deleted** -- A cluster must always have at least one System mode pool. Convert another pool to System before deleting the original.
4. **Spot pool eviction** -- Spot node pools can be evicted at any time. Only use for fault-tolerant workloads (batch, CI, dev/test). Never run stateful or production workloads on spot pools.
5. **Ephemeral OS disk size** -- If using `Ephemeral` OS disk type, the VM's cache or temp disk must be large enough for the OS disk. Otherwise, pool creation fails silently.
6. **Max pods per node vs subnet sizing** -- With Azure CNI, each pod gets a VNet IP. Setting `maxPods: 110` on a large pool can exhaust the subnet. Use CNI Overlay to avoid this.
7. **Node labels vs taints** -- Labels are for affinity; taints are for repulsion. Forgetting to add tolerations for custom taints causes pods to remain Pending indefinitely.
8. **Availability zone mismatch** -- If the cluster has zone-redundant system pool but user pool is in a single zone, pod scheduling may fail during a zone outage.

## Production Backlog Items

- [ ] Enable cluster autoscaler with appropriate min/max node counts
- [ ] Configure availability zones for zone-redundant node pools
- [ ] Add node taints for workload isolation (e.g., GPU, Windows)
- [ ] Switch to ephemeral OS disks for faster node scaling
- [ ] Implement pod disruption budgets for graceful upgrades
- [ ] Configure node pool auto-upgrade channel aligned with cluster
- [ ] Add dedicated system node pool with appropriate sizing for add-ons
- [ ] Evaluate AzureLinux OS SKU for reduced attack surface and faster boot
