---
service_namespace: Microsoft.Batch/batchAccounts/pools
display_name: Batch Pool
depends_on:
  - Microsoft.Batch/batchAccounts
---

# Batch Pool

> A collection of compute nodes (VMs) within a Batch account that execute tasks. Pools define the VM size, OS, scaling rules, and networking for batch workloads.

## When to Use
- Run parallel compute tasks across multiple VMs
- Configure auto-scaling pools that grow/shrink based on workload
- Define specific VM images and sizes for batch processing
- Support GPU workloads (rendering, ML training) with specialized VM SKUs
- Separate pool definitions for different workload types (CPU-intensive, memory-intensive)

## POC Defaults
- **VM size**: Standard_D2s_v3 (general purpose, 2 vCPU, 8 GB RAM)
- **Target dedicated nodes**: 0 (scale up on demand)
- **Target low-priority nodes**: 2 (cost-effective for POC)
- **OS**: Ubuntu 22.04 LTS (via ImageReference)
- **Scaling**: Fixed for POC; auto-scale for production

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "batch_pool" {
  type      = "Microsoft.Batch/batchAccounts/pools@2024-07-01"
  name      = var.pool_name
  parent_id = azapi_resource.batch_account.id

  body = {
    properties = {
      vmSize = "standard_d2s_v3"
      deploymentConfiguration = {
        virtualMachineConfiguration = {
          imageReference = {
            publisher = "canonical"
            offer     = "0001-com-ubuntu-server-jammy"
            sku       = "22_04-lts"
            version   = "latest"
          }
          nodeAgentSkuId = "batch.node.ubuntu 22.04"
        }
      }
      scaleSettings = {
        fixedScale = {
          targetDedicatedNodes   = 0
          targetLowPriorityNodes = 2
          resizeTimeout          = "PT15M"
        }
      }
      taskSlotsPerNode = 1
    }
  }
}
```

### RBAC Assignment
```hcl
# Pool management inherits from the Batch Account RBAC.
# Batch Account Contributor role allows full pool management.
```

## Bicep Patterns

### Basic Resource
```bicep
param poolName string
param vmSize string = 'standard_d2s_v3'

resource pool 'Microsoft.Batch/batchAccounts/pools@2024-07-01' = {
  parent: batchAccount
  name: poolName
  properties: {
    vmSize: vmSize
    deploymentConfiguration: {
      virtualMachineConfiguration: {
        imageReference: {
          publisher: 'canonical'
          offer: '0001-com-ubuntu-server-jammy'
          sku: '22_04-lts'
          version: 'latest'
        }
        nodeAgentSkuId: 'batch.node.ubuntu 22.04'
      }
    }
    scaleSettings: {
      fixedScale: {
        targetDedicatedNodes: 0
        targetLowPriorityNodes: 2
        resizeTimeout: 'PT15M'
      }
    }
  }
}

output poolId string = pool.id
output poolName string = pool.name
```

## Application Code

### Python
```python
from azure.identity import DefaultAzureCredential
from azure.batch import BatchServiceClient
from azure.batch.models import PoolAddParameter, VirtualMachineConfiguration, ImageReference

credential = DefaultAzureCredential()
batch_client = BatchServiceClient(credential, batch_url=f"https://{account_name}.{region}.batch.azure.com")

# Submit a task to the pool
from azure.batch.models import TaskAddParameter
batch_client.task.add(
    job_id=job_id,
    task=TaskAddParameter(
        id="task-1",
        command_line="/bin/bash -c 'echo Hello Batch'"
    )
)
```

### C#
```csharp
using Azure.Identity;
using Microsoft.Azure.Batch;
using Microsoft.Azure.Batch.Auth;

var credential = new DefaultAzureCredential();
var token = await credential.GetTokenAsync(
    new Azure.Core.TokenRequestContext(new[] { "https://batch.core.windows.net/.default" }));

using var batchClient = BatchClient.Open(
    new BatchTokenCredentials($"https://{accountName}.{region}.batch.azure.com", () =>
        Task.FromResult(token.Token)));

batchClient.JobOperations.AddTask(jobId, new CloudTask("task-1", "echo Hello Batch"));
```

### Node.js
```typescript
import { DefaultAzureCredential } from "@azure/identity";
import { BatchServiceClient } from "@azure/batch";

const credential = new DefaultAzureCredential();
const batchClient = new BatchServiceClient(credential,
  `https://${accountName}.${region}.batch.azure.com`);

await batchClient.task.add(jobId, {
  id: "task-1",
  commandLine: "/bin/bash -c 'echo Hello Batch'",
});
```

## Common Pitfalls
- **Node agent SKU must match image**: The `nodeAgentSkuId` must correspond to the OS image. Mismatches cause pool creation to succeed but nodes fail to start.
- **Resize timeout**: If nodes can't be allocated within the resize timeout, the pool enters a resize error state. Low-priority nodes are especially prone to this.
- **Low-priority preemption**: Low-priority nodes can be preempted at any time. Tasks must be idempotent or use retry logic.
- **VNet integration complexity**: Pools in VNets require specific NSG rules allowing Batch node management traffic (ports 29876, 29877, 443).
- **Pool deletion is async**: Deleting a pool with running nodes can take several minutes. Terraform may timeout waiting for deletion.

## Production Backlog Items
- Auto-scale formulas based on pending task count
- VNet integration for network-isolated workloads
- Container-based task execution (Docker images)
- Start task for node initialization (install dependencies)
- Application packages for versioned task binaries
