# Bicep Patterns for Azure

Standard Bicep patterns for Azure resource deployment. **All Bicep agents must reference these patterns.** This extension deploys directly -- deployment commands are executed by the CLI, not handed to the user. Use `--dry-run` for what-if previews.

## Project Structure

```
infrastructure/bicep/
├── modules/
│   ├── <service-name>/
│   │   ├── main.bicep
│   │   └── private-endpoint.bicep    # if applicable
│   └── ...
├── environments/
│   ├── dev.bicepparam
│   └── prod.bicepparam
├── main.bicep                         # orchestration (subscription scope)
└── deploy.sh                          # staged deployment script (see deploy-scripts.md)
```

## Standard Module Parameters

### main.bicep (Module Template)

```bicep
@description('Name of the resource')
param name string

@description('Azure region for resources')
param location string = resourceGroup().location

@description('Tags to apply to all resources')
param tags object = {}

// Private Endpoint Parameters (include if service supports private endpoints)
@description('Enable private endpoint')
param enablePrivateEndpoint bool = true

@description('Subnet resource ID for private endpoint')
param subnetId string = ''

@description('Private DNS zone resource ID')
param privateDnsZoneId string = ''
```

## Private Endpoint Pattern

### private-endpoint.bicep

```bicep
@description('Name of the private endpoint')
param name string

@description('Location for the private endpoint')
param location string

@description('Tags to apply')
param tags object = {}

@description('Resource ID of the service to connect to')
param privateLinkServiceId string

@description('Group ID for the private link (e.g., blob, vault, sqlServer)')
param groupId string

@description('Subnet ID for the private endpoint')
param subnetId string

@description('Private DNS zone ID (optional)')
param privateDnsZoneId string = ''

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'psc-${name}'
        properties: {
          privateLinkServiceId: privateLinkServiceId
          groupIds: [
            groupId
          ]
        }
      }
    ]
  }
}

resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = if (!empty(privateDnsZoneId)) {
  parent: privateEndpoint
  name: 'dns-zone-group'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config'
        properties: {
          privateDnsZoneId: privateDnsZoneId
        }
      }
    ]
  }
}

output privateEndpointId string = privateEndpoint.id
output privateIpAddress string = privateEndpoint.properties.customDnsConfigs[0].ipAddresses[0]
```

### Using Private Endpoint Module

```bicep
module privateEndpoint 'private-endpoint.bicep' = if (enablePrivateEndpoint && !empty(subnetId)) {
  name: 'pe-${name}-deployment'
  params: {
    name: 'pe-${name}'
    location: location
    tags: tags
    privateLinkServiceId: mainResource.id
    groupId: '<group_id>'  // See service-registry.yaml
    subnetId: subnetId
    privateDnsZoneId: privateDnsZoneId
  }
}
```

## RBAC Assignment Pattern

```bicep
@description('Principal ID of the managed identity')
param principalId string

@description('Role definition ID or built-in role name')
param roleDefinitionId string

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(mainResource.id, principalId, roleDefinitionId)
  scope: mainResource
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

### Common Role Definition IDs

```bicep
// Storage
var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var storageBlobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var storageQueueDataContributor = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'

// Key Vault
var keyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var keyVaultSecretsOfficer = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
var keyVaultCryptoUser = '12338af0-0e69-4776-bea7-57ae8d297424'

// Cosmos DB
var cosmosDbDataContributor = '00000000-0000-0000-0000-000000000002'  // Built-in Cosmos role

// Service Bus
var serviceBusDataOwner = '090c5cfd-751d-490a-894a-3ce6f1109419'
var serviceBusDataSender = '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
var serviceBusDataReceiver = '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'

// Cognitive Services / Azure OpenAI
var cognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var cognitiveServicesOpenAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
```

## Standard Outputs

```bicep
@description('Resource ID')
output id string = mainResource.id

@description('Resource name')
output name string = mainResource.name

@description('Resource endpoint (if applicable)')
output endpoint string = mainResource.properties.<endpointProperty>

@description('Private endpoint IP (if enabled)')
output privateEndpointIp string = enablePrivateEndpoint && !empty(subnetId) ? privateEndpoint.outputs.privateIpAddress : ''
```

## Orchestration Pattern

### main.bicep (Root)

```bicep
targetScope = 'subscription'

@description('Environment name')
@allowed(['dev', 'test', 'prod'])
param environment string

@description('Project name')
param projectName string

@description('Azure region')
param location string = 'eastus'

@description('Tags for all resources')
param tags object = {}

var resourceGroupName = 'rg-${projectName}-${environment}'
var commonTags = union(tags, {
  Environment: environment
  Project: projectName
  ManagedBy: 'Bicep'
})

// Resource Group
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: commonTags
}

// Deploy foundation modules first
module networking 'modules/networking/main.bicep' = {
  scope: rg
  name: 'networking-deployment'
  params: {
    projectName: projectName
    environment: environment
    location: location
    tags: commonTags
  }
}

module identity 'modules/identity/main.bicep' = {
  scope: rg
  name: 'identity-deployment'
  params: {
    projectName: projectName
    location: location
    tags: commonTags
  }
}

// Deploy data services (depend on foundation)
module storage 'modules/storage/main.bicep' = {
  scope: rg
  name: 'storage-deployment'
  params: {
    name: 'st${projectName}${environment}'
    location: location
    tags: commonTags
    subnetId: networking.outputs.dataSubnetId
    enablePrivateEndpoint: true
  }
  dependsOn: [
    networking
  ]
}

// Deploy compute services (depend on data)
module appService 'modules/app-service/main.bicep' = {
  scope: rg
  name: 'app-service-deployment'
  params: {
    name: 'app-${projectName}-${environment}'
    location: location
    tags: commonTags
    managedIdentityId: identity.outputs.principalId
  }
  dependsOn: [
    storage
  ]
}
```

## Parameters File

### dev.bicepparam

```bicep
using '../main.bicep'

param environment = 'dev'
param projectName = 'myproject'
param location = 'eastus'
param tags = {
  CostCenter: '12345'
  Owner: 'team@company.com'
}
```

## Staged Deployment Script Pattern

The deploy stage uses a staged deployment script (`deploy.sh`) to deploy infrastructure in dependency order. See `deploy-scripts.md` for the full pattern. The Bicep-specific commands within each stage are:

```bash
# Per-stage Bicep deployment (subscription scope)
deploy_bicep_stage_sub() {
  local template="$1"
  local params_file="$2"
  local stage_name="$3"
  local deploy_name="${stage_name}-$(date +%Y%m%d-%H%M%S)"

  if [ "$DRY_RUN" = "true" ]; then
    az deployment sub what-if \
      --location "$LOCATION" \
      --template-file "$template" \
      --parameters "$params_file" \
      --name "$deploy_name"
  else
    az deployment sub create \
      --location "$LOCATION" \
      --template-file "$template" \
      --parameters "$params_file" \
      --name "$deploy_name"
  fi
}

# Per-stage Bicep deployment (resource group scope)
deploy_bicep_stage_rg() {
  local template="$1"
  local params_file="$2"
  local stage_name="$3"
  local rg_name="$4"
  local deploy_name="${stage_name}-$(date +%Y%m%d-%H%M%S)"

  if [ "$DRY_RUN" = "true" ]; then
    az deployment group what-if \
      --resource-group "$rg_name" \
      --template-file "$template" \
      --parameters "$params_file" \
      --name "$deploy_name"
  else
    az deployment group create \
      --resource-group "$rg_name" \
      --template-file "$template" \
      --parameters "$params_file" \
      --name "$deploy_name"
  fi
}
```

Stage ordering follows the pattern: foundation (resource group, networking, identity) then data then compute then applications. See `deploy-scripts.md` for the complete staged deployment framework.

## Deployment Commands

These commands are executed directly by the deploy stage. `--dry-run` uses `what-if` mode.

```bash
# Navigate to bicep directory
cd infrastructure/bicep

# Validate template syntax
az bicep build --file main.bicep

# Validate deployment (what-if at subscription scope)
az deployment sub what-if \
  --location eastus \
  --template-file main.bicep \
  --parameters environments/dev.bicepparam

# Deploy at subscription scope (execute mode)
az deployment sub create \
  --location eastus \
  --template-file main.bicep \
  --parameters environments/dev.bicepparam \
  --name "deployment-$(date +%Y%m%d-%H%M%S)"

# For resource group scoped deployments
az deployment group validate \
  --resource-group rg-myproject-dev \
  --template-file modules/<service>/main.bicep \
  --parameters @environments/dev.bicepparam

az deployment group what-if \
  --resource-group rg-myproject-dev \
  --template-file modules/<service>/main.bicep \
  --parameters @environments/dev.bicepparam

az deployment group create \
  --resource-group rg-myproject-dev \
  --template-file modules/<service>/main.bicep \
  --parameters @environments/dev.bicepparam \
  --name "deployment-$(date +%Y%m%d-%H%M%S)"

# Delete a resource group (rollback / teardown)
az group delete --name rg-myproject-dev --yes --no-wait
```

## Common Patterns

### Conditional Resources

```bicep
resource optionalResource 'Microsoft.Example/resources@2024-01-01' = if (enableFeature) {
  name: name
  // ...
}

// Output with conditional
output optionalResourceId string = enableFeature ? optionalResource.id : ''
```

### Loops

```bicep
@description('List of container names to create')
param containerNames array

resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for containerName in containerNames: {
  name: containerName
  // ...
}]
```

### Existing Resources

```bicep
@description('Name of existing Key Vault')
param keyVaultName string

@description('Resource group of existing Key Vault')
param keyVaultResourceGroup string

resource existingKeyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
  scope: resourceGroup(keyVaultResourceGroup)
}

// Use existing resource
var keyVaultUri = existingKeyVault.properties.vaultUri
```

### User-Defined Types

```bicep
@description('Network configuration')
type networkConfig = {
  subnetId: string
  privateDnsZoneId: string?
  enablePrivateEndpoint: bool
}

param networkSettings networkConfig
```

### Batch Deployment with Dependency Ordering

```bicep
// Use dependsOn for explicit ordering when implicit dependencies are insufficient
module dataLayer 'modules/data/main.bicep' = {
  scope: rg
  name: 'data-deployment'
  params: { /* ... */ }
  dependsOn: [
    foundationLayer
  ]
}

module computeLayer 'modules/compute/main.bicep' = {
  scope: rg
  name: 'compute-deployment'
  params: {
    storageAccountId: dataLayer.outputs.storageAccountId
    // implicit dependency via output reference
  }
}
```

## Security Checklist

All Bicep templates MUST:

- [ ] Disable public network access where supported (`publicNetworkAccess: 'Disabled'`)
- [ ] Enable private endpoints for data services
- [ ] Use Managed Identity for authentication (avoid keys/connection strings)
- [ ] Enable TLS 1.2+ minimum (`minTlsVersion: 'TLS1_2'`)
- [ ] Enable diagnostic logging
- [ ] Apply required tags (Environment, Project, ManagedBy at minimum)
- [ ] Never hardcode secrets -- use Key Vault references or `@secure()` decorator

```bicep
@secure()
@description('Admin password')
param adminPassword string
```

## API Versions

Use recent stable API versions. Reference:

- Resource Groups: `2024-03-01`
- Storage: `2023-05-01`
- Key Vault: `2023-07-01`
- Cosmos DB: `2024-05-15`
- SQL: `2023-08-01-preview`
- Service Bus: `2024-01-01`
- Container Apps: `2024-03-01`
- App Service: `2024-04-01`
- Private Endpoints: `2024-01-01`
- Role Assignments: `2022-04-01`
- Managed Identity: `2023-01-31`
- Virtual Network: `2024-01-01`
- Azure OpenAI: `2024-10-01`

## Service-Specific Values

Refer to `service-registry.yaml` for per-service details:

- Private endpoint group IDs
- RBAC role definition IDs
- SKU options and defaults
- Resource naming prefixes

## Critical Reminders

1. **Direct execution** -- This extension runs `az deployment` commands directly. Always validate with `what-if` first.
2. **Always include private endpoint** -- Use the module pattern above for any service that supports it.
3. **Use parameters** -- No hardcoded values in modules.
4. **Export outputs** -- Other modules depend on these values.
5. **Follow naming conventions** -- Use project/environment variables from the naming strategy.
6. **Use `.bicepparam` files** -- Prefer the new Bicep parameter file format over JSON parameter files.
7. **Deployment names** -- Always include a timestamp in deployment names to avoid conflicts.
