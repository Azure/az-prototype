---
service_namespace: Microsoft.MachineLearningServices/workspaces/computes
display_name: Machine Learning Compute
depends_on:
  - Microsoft.MachineLearningServices/workspaces
---

# Machine Learning Compute

> A compute target within an Azure Machine Learning workspace for running training jobs, inference endpoints, or interactive notebooks. Includes compute instances, compute clusters, and attached computes.

## When to Use
- **Compute instance**: Interactive development (Jupyter notebooks, VS Code remote)
- **Compute cluster**: Scalable training jobs that auto-scale to zero when idle
- **Managed online endpoint**: Real-time inference hosting (separate resource, not covered here)
- **Attached compute**: Use existing AKS, Databricks, or VMs as ML compute
- Every ML training job needs a compute target

## POC Defaults
- **Compute instance**: Standard_DS3_v2 (4 vCPU, 14 GB RAM)
- **Compute cluster**: Standard_DS3_v2, min nodes 0, max nodes 2
- **Idle seconds before scale down**: 1800 (30 minutes)
- **Identity**: System-assigned managed identity

## Terraform Patterns

### Basic Resource
```hcl
# Compute instance for development
resource "azapi_resource" "ml_compute_instance" {
  type      = "Microsoft.MachineLearningServices/workspaces/computes@2024-10-01"
  name      = var.compute_instance_name
  parent_id = azapi_resource.ml_workspace.id
  location  = var.location

  body = {
    properties = {
      computeType = "ComputeInstance"
      properties = {
        vmSize                        = "Standard_DS3_v2"
        enableNodePublicIp            = false
        idleTimeBeforeShutdown        = "PT30M"
        applicationSharingPolicy      = "Personal"
      }
    }
  }
}

# Compute cluster for training
resource "azapi_resource" "ml_compute_cluster" {
  type      = "Microsoft.MachineLearningServices/workspaces/computes@2024-10-01"
  name      = var.cluster_name
  parent_id = azapi_resource.ml_workspace.id
  location  = var.location

  body = {
    properties = {
      computeType = "AmlCompute"
      properties = {
        vmSize                 = "Standard_DS3_v2"
        vmPriority             = "Dedicated"
        scaleSettings = {
          minNodeCount                = 0
          maxNodeCount                = 2
          nodeIdleTimeBeforeScaleDown = "PT1800S"
        }
        enableNodePublicIp = false
      }
    }
  }
}
```

### RBAC Assignment
```hcl
# Azure ML Data Scientist role allows submitting jobs and using computes.
# Azure ML Compute Operator role allows managing compute resources.
resource "azapi_resource" "ml_compute_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = var.role_assignment_name
  parent_id = azapi_resource.ml_workspace.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/f6c7c914-8db3-469d-8ca1-694a8f32e121"
      principalId      = var.data_scientist_principal_id
      principalType    = "User"
    }
  }
}
```

## Bicep Patterns

### Basic Resource
```bicep
param clusterName string
param location string
param vmSize string = 'Standard_DS3_v2'

resource computeCluster 'Microsoft.MachineLearningServices/workspaces/computes@2024-10-01' = {
  parent: mlWorkspace
  name: clusterName
  location: location
  properties: {
    computeType: 'AmlCompute'
    properties: {
      vmSize: vmSize
      vmPriority: 'Dedicated'
      scaleSettings: {
        minNodeCount: 0
        maxNodeCount: 2
        nodeIdleTimeBeforeScaleDown: 'PT1800S'
      }
      enableNodePublicIp: false
    }
  }
}

output computeId string = computeCluster.id
```

## Application Code

### Python
```python
from azure.identity import DefaultAzureCredential
from azure.ai.ml import MLClient, command

credential = DefaultAzureCredential()
ml_client = MLClient(credential, subscription_id, rg_name, workspace_name)

# Submit a training job to the compute cluster
job = command(
    code="./src",
    command="python train.py --epochs 10 --lr 0.001",
    environment="AzureML-sklearn-1.0-ubuntu20.04-py38-cpu:1",
    compute=cluster_name,
)
returned_job = ml_client.jobs.create_or_update(job)
print(f"Job name: {returned_job.name}, Status: {returned_job.status}")
```

### C#
```csharp
using Azure.Identity;
using Azure.ResourceManager;
using Azure.ResourceManager.MachineLearning;

var credential = new DefaultAzureCredential();
var client = new ArmClient(credential);

var workspace = client.GetMachineLearningWorkspaceResource(
    MachineLearningWorkspaceResource.CreateResourceIdentifier(
        subscriptionId, rgName, workspaceName));

var computes = workspace.GetMachineLearningComputes();
await foreach (var compute in computes.GetAllAsync())
{
    Console.WriteLine($"Compute: {compute.Data.Name}, Type: {compute.Data.Properties.ComputeType}");
}
```

### Node.js
```typescript
import { DefaultAzureCredential } from "@azure/identity";
import { MachineLearningClient } from "@azure/arm-machinelearning";

const credential = new DefaultAzureCredential();
const client = new MachineLearningClient(credential, subscriptionId);

const computes = client.computeOperations.list(rgName, workspaceName);
for await (const compute of computes) {
  console.log(`Compute: ${compute.name}, Type: ${compute.properties?.computeType}`);
}
```

## Common Pitfalls
- **Compute instance is single-user**: Compute instances are assigned to one user. Use `applicationSharingPolicy: "Personal"` and specify the assigned user.
- **Scale-down delay**: Even with `minNodeCount: 0`, nodes don't shut down immediately. The idle timeout controls when scale-down begins.
- **VNet integration requirements**: Disabling public IP (`enableNodePublicIp: false`) requires VNet integration and a private endpoint on the workspace.
- **Spot/low-priority preemption**: Using `vmPriority: "LowPriority"` saves costs but jobs may be preempted. Training scripts must support checkpointing.
- **Location must match workspace**: The compute location must match the parent workspace location.
- **Quota limits**: Compute creation fails if the subscription's regional VM quota is exhausted. Check quota before deploying large clusters.

## Production Backlog Items
- GPU compute clusters for deep learning workloads
- Auto-scale policies based on job queue depth
- Managed identity for compute-to-data-store access
- VNet-integrated compute for network isolation
- Scheduled start/stop for compute instances
