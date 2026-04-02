# Azure Kubernetes Service (AKS)
> Managed Kubernetes cluster for deploying, scaling, and operating containerized applications with enterprise-grade security and governance.

## When to Use

- **Microservices at scale** -- multiple services with independent scaling, deployment, and lifecycle management
- **Existing Kubernetes expertise** -- teams already invested in Kubernetes tooling (Helm, Kustomize, ArgoCD)
- **Complex networking requirements** -- service mesh, network policies, ingress controllers with fine-grained control
- **Hybrid / multi-cloud portability** -- workloads that may need to run on other Kubernetes platforms
- **Stateful workloads** -- databases, message queues, or ML training that need persistent volumes

Choose AKS over Container Apps when you need full Kubernetes control, custom operators, service mesh, or have existing Kubernetes manifests. Choose Container Apps for simpler containerized apps where Kubernetes complexity isn't needed.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Free | No SLA; sufficient for POC |
| Node pool VM | Standard_B2s | 2 vCPU, 4 GiB; lowest practical for POC |
| Node count | 1-2 | Minimum; use auto-scaler for production |
| Kubernetes version | Latest stable | e.g., 1.29.x |
| Network plugin | Azure CNI Overlay | Simplest; avoids subnet sizing complexity |
| RBAC | Azure AD + Kubernetes RBAC | Integrated by default |
| Managed identity | System-assigned | For cluster operations |
| Workload identity | Enabled | For pod-level Azure service access |
| Container Registry | ACR with AcrPull | Managed identity-based image pulling |

## Terraform Patterns

### Basic Resource

```hcl
resource "azapi_resource" "this" {
  type      = "Microsoft.ContainerService/managedClusters@2024-03-02-preview"
  name      = var.name
  location  = var.location
  parent_id = var.resource_group_id

  identity {
    type = "SystemAssigned"
  }

  body = {
    sku = {
      name = "Base"
      tier = "Free"  # "Standard" for SLA
    }
    properties = {
      kubernetesVersion = var.kubernetes_version  # e.g., "1.29"
      dnsPrefix         = var.dns_prefix
      agentPoolProfiles = [
        {
          name                = "system"
          count               = 1
          vmSize              = "Standard_B2s"
          osDiskSizeGB        = 30
          mode                = "System"
          osType              = "Linux"
          upgradeSettings = {
            maxSurge = "10%"
          }
        }
      ]
      networkProfile = {
        networkPlugin    = "azure"
        networkPolicy    = "azure"    # or "calico" for more features
        networkDataplane = "cilium"   # Azure CNI Overlay with Cilium
        serviceCidr      = "10.0.0.0/16"
        dnsServiceIP     = "10.0.0.10"
      }
      oidcIssuerProfile = {
        enabled = true  # Required for workload identity
      }
      securityProfile = {
        workloadIdentity = {
          enabled = true  # Pod-level Azure AD auth
        }
      }
      aadProfile = {
        managed            = true
        enableAzureRBAC    = true
        adminGroupObjectIDs = var.admin_group_ids
      }
    }
  }

  tags = var.tags

  response_export_values = ["*"]
}
```

### User Node Pool

```hcl
resource "azapi_resource" "workload_pool" {
  type      = "Microsoft.ContainerService/managedClusters/agentPools@2024-03-02-preview"
  name      = "workload"
  parent_id = azapi_resource.this.id

  body = {
    properties = {
      vmSize             = "Standard_D2s_v5"
      count              = 1
      minCount           = 1
      maxCount           = 5
      enableAutoScaling  = true
      osDiskSizeGB       = 50
      mode               = "User"
      osType             = "Linux"
      nodeLabels = {
        "workload" = "app"
      }
    }
  }
}
```

### RBAC Assignment

```hcl
# AcrPull -- allow AKS to pull images from ACR
resource "azapi_resource" "acr_pull" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${var.container_registry_id}-acr-pull")
  parent_id = var.container_registry_id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"
      principalId      = azapi_resource.this.output.properties.identityProfile.kubeletidentity.objectId
    }
  }
}

# Azure Kubernetes Service Cluster User Role -- allows kubectl access
resource "azapi_resource" "cluster_user" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.this.id}-cluster-user")
  parent_id = azapi_resource.this.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/4abbcc35-e782-43d8-92c5-2d3f1bd2253f"
      principalId      = var.developer_group_principal_id
    }
  }
}

# Azure Kubernetes Service RBAC Writer -- namespace-scoped write access
resource "azapi_resource" "rbac_writer" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("oid", "${azapi_resource.this.id}-rbac-writer")
  parent_id = azapi_resource.this.id

  body = {
    properties = {
      roleDefinitionId = "/providers/Microsoft.Authorization/roleDefinitions/a7ffa36f-339b-4b5c-8bdf-e2c188b2c0eb"
      principalId      = var.developer_group_principal_id
    }
  }
}
```

RBAC role IDs:
- Azure Kubernetes Service Cluster User Role: `4abbcc35-e782-43d8-92c5-2d3f1bd2253f`
- Azure Kubernetes Service RBAC Admin: `3498e952-d568-435e-9b2c-8d77e338d7f7`
- Azure Kubernetes Service RBAC Writer: `a7ffa36f-339b-4b5c-8bdf-e2c188b2c0eb`
- Azure Kubernetes Service RBAC Reader: `7f6c6a51-bcf8-42ba-9220-52d62157d06d`

### Private Endpoint

AKS uses **private cluster** mode rather than traditional private endpoints:

```hcl
# For private cluster, add these properties to the managedClusters resource:
resource "azapi_resource" "this" {
  # ... (same as basic, plus in body.properties:)

  body = {
    properties = {
      # ... other properties ...
      apiServerAccessProfile = {
        enablePrivateCluster           = true
        privateDNSZone                 = "system"  # or custom zone resource ID
        enablePrivateClusterPublicFQDN = false
      }
    }
  }
}
```

Private DNS zone: `privatelink.<region>.azmk8s.io`

**Note:** Private clusters require VPN, ExpressRoute, or a jump box to access the API server. Unless told otherwise, public API server access should be disabled per governance policy — use a Bastion host or VPN for access.

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the AKS cluster')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('DNS prefix for the cluster')
param dnsPrefix string

@description('Kubernetes version')
param kubernetesVersion string = '1.29'

@description('Admin group object IDs for cluster admin access')
param adminGroupObjectIds array = []

@description('Tags to apply')
param tags object = {}

resource aks 'Microsoft.ContainerService/managedClusters@2024-03-02-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Base'
    tier: 'Free'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    kubernetesVersion: kubernetesVersion
    dnsPrefix: dnsPrefix
    agentPoolProfiles: [
      {
        name: 'system'
        count: 1
        vmSize: 'Standard_B2s'
        osDiskSizeGB: 30
        mode: 'System'
        osType: 'Linux'
      }
    ]
    networkProfile: {
      networkPlugin: 'azure'
      networkPolicy: 'azure'
      serviceCidr: '10.0.0.0/16'
      dnsServiceIP: '10.0.0.10'
    }
    oidcIssuerProfile: {
      enabled: true
    }
    securityProfile: {
      workloadIdentity: {
        enabled: true
      }
    }
    aadProfile: {
      managed: true
      enableAzureRBAC: true
      adminGroupObjectIDs: adminGroupObjectIds
    }
  }
}

output id string = aks.id
output name string = aks.name
output fqdn string = aks.properties.fqdn
output kubeletIdentityObjectId string = aks.properties.identityProfile.kubeletidentity.objectId
```

### RBAC Assignment

```bicep
@description('ACR resource ID for AcrPull')
param acrId string

// AcrPull role for kubelet identity
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acrId, aks.properties.identityProfile.kubeletidentity.objectId, acrPullRoleId)
  scope: resourceId('Microsoft.ContainerRegistry/registries', split(acrId, '/')[8])
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: aks.properties.identityProfile.kubeletidentity.objectId
    principalType: 'ServicePrincipal'
  }
}
```

## Application Code

AKS application code is standard Kubernetes -- Docker containers deployed via manifests, Helm charts, or Kustomize.

### Kubernetes Deployment with Workload Identity

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: myapp
spec:
  replicas: 2
  selector:
    matchLabels:
      app: myapp
  template:
    metadata:
      labels:
        app: myapp
        azure.workload.identity/use: "true"  # Enable workload identity
    spec:
      serviceAccountName: myapp-sa  # Linked to Azure managed identity
      containers:
        - name: myapp
          image: myregistry.azurecr.io/myapp:latest
          ports:
            - containerPort: 8080
          env:
            - name: AZURE_CLIENT_ID
              value: "<managed-identity-client-id>"  # From federated credential
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 256Mi
---
apiVersion: v1
kind: Service
metadata:
  name: myapp
spec:
  type: ClusterIP
  selector:
    app: myapp
  ports:
    - port: 80
      targetPort: 8080
```

### Workload Identity Service Account

```yaml
# service-account.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: myapp-sa
  annotations:
    azure.workload.identity/client-id: "<managed-identity-client-id>"
```

### Federated Credential (Terraform)

```hcl
resource "azapi_resource" "federated_credential" {
  type      = "Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31"
  name      = "aks-${var.namespace}-${var.service_account_name}"
  parent_id = var.managed_identity_id

  body = {
    properties = {
      audiences = ["api://AzureADTokenExchange"]
      issuer    = azapi_resource.this.output.properties.oidcIssuerProfile.issuerURL
      subject   = "system:serviceaccount:${var.namespace}:${var.service_account_name}"
    }
  }
}
```

### Application Code with DefaultAzureCredential

```python
# Application code is the same as any Azure SDK code
# Workload identity provides the token automatically
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

credential = DefaultAzureCredential()  # Picks up workload identity in AKS
client = BlobServiceClient(account_url="https://mystorageaccount.blob.core.windows.net", credential=credential)
```

## Common Pitfalls

1. **System node pool VM size too small** -- `Standard_B2s` works for POC but system pods (CoreDNS, kube-proxy, etc.) need ~500Mi memory. Don't go below B2s.
2. **Forgetting AcrPull role on kubelet identity** -- Without this, pods fail to pull images with `ImagePullBackOff`. Must assign the role to `kubelet_identity`, not the cluster identity.
3. **Network plugin choice is permanent** -- Cannot change from `kubelet` to `azure` or vice versa after cluster creation. Azure CNI Overlay is the recommended default.
4. **Workload identity requires OIDC issuer** -- Must enable `oidc_issuer_enabled` on the cluster AND create a `FederatedIdentityCredential` linking the Kubernetes service account to the Azure managed identity.
5. **Private cluster API server access** -- With `private_cluster_enabled = true`, `kubectl` commands fail unless you're on the VNet (VPN, bastion, or runner VM). For POC, keep API server public.
6. **Node pool naming constraints** -- Pool names must be lowercase, max 12 characters for Linux, 6 for Windows. No hyphens or underscores.
7. **Kubernetes version upgrades** -- AKS enforces version currency. Clusters on N-2 versions are auto-upgraded. Plan upgrade cadence.
8. **Ingress controller not included** -- AKS doesn't install an ingress controller by default. Deploy NGINX Ingress Controller or use the managed `app-routing` add-on.

## Production Backlog Items

- [ ] Upgrade to Standard tier for SLA (99.95% with availability zones)
- [ ] Enable availability zones on node pools
- [ ] Configure cluster auto-scaler with appropriate min/max
- [ ] Enable private cluster mode for API server
- [ ] Deploy network policies for namespace isolation
- [ ] Install and configure ingress controller (NGINX or app-routing add-on)
- [ ] Set up monitoring with Container Insights and Prometheus
- [ ] Configure Azure Policy for Kubernetes (pod security standards)
- [ ] Implement GitOps with Flux or ArgoCD for declarative deployments
- [ ] Configure node pool auto-upgrade channel
- [ ] Add separate user node pool for workloads
- [ ] Enable Defender for Containers for runtime threat protection
