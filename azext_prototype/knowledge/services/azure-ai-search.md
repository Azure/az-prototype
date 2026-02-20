# Azure AI Search
> Fully managed search-as-a-service with AI enrichment, vector search, and semantic ranking for building rich search experiences over heterogeneous content.

## When to Use

- **RAG (Retrieval-Augmented Generation)** -- vector + keyword hybrid search as the retrieval layer for LLM-powered applications
- **Full-text search** -- structured and unstructured content with faceting, filters, scoring profiles
- **Knowledge mining** -- AI enrichment pipelines (skillsets) to extract structure from unstructured documents
- **E-commerce / catalog search** -- autocomplete, suggestions, faceted navigation

Azure AI Search is the recommended retrieval engine for RAG patterns on Azure. Pair with Azure OpenAI for the generation layer.

## POC Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| SKU | Basic | 2 GiB storage, 3 replicas max; sufficient for POC |
| Replicas | 1 | Scale up for availability SLA |
| Partitions | 1 | Scale up for storage/throughput |
| Semantic ranker | Free tier | 1,000 queries/month free on Basic+ |
| Authentication | API key (POC) | Flag RBAC-only as production backlog item |
| Public network access | Enabled (POC) | Flag private endpoint as production backlog item |

## Terraform Patterns

### Basic Resource

```hcl
resource "azurerm_search_service" "this" {
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  sku                           = "basic"
  replica_count                 = 1
  partition_count               = 1
  public_network_access_enabled = true  # Set false when using private endpoint
  local_authentication_enabled  = true  # Set false when using RBAC-only

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}
```

### RBAC Assignment

```hcl
# Search Index Data Contributor -- allows indexing documents
resource "azurerm_role_assignment" "search_index_contributor" {
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Index Data Contributor"
  principal_id         = var.managed_identity_principal_id
}

# Search Index Data Reader -- allows querying indexes
resource "azurerm_role_assignment" "search_index_reader" {
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Index Data Reader"
  principal_id         = var.app_identity_principal_id
}

# Search Service Contributor -- allows managing indexes, indexers, skillsets
resource "azurerm_role_assignment" "search_service_contributor" {
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Service Contributor"
  principal_id         = var.admin_identity_principal_id
}
```

RBAC role IDs:
- Search Index Data Reader: `1407120a-92aa-4202-b7e9-c0e197c71c8f`
- Search Index Data Contributor: `8ebe5a00-799e-43f5-93ac-243d3dce84a7`
- Search Service Contributor: `7ca78c08-252a-4471-8644-bb5ff32d4ba0`

### Private Endpoint

```hcl
resource "azurerm_private_endpoint" "search" {
  count = var.enable_private_endpoint && var.subnet_id != null ? 1 : 0

  name                = "pe-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.subnet_id

  private_service_connection {
    name                           = "psc-${var.name}"
    private_connection_resource_id = azurerm_search_service.this.id
    subresource_names              = ["searchService"]
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

Private DNS zone: `privatelink.search.windows.net`

## Bicep Patterns

### Basic Resource

```bicep
@description('Name of the search service (globally unique)')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Tags to apply')
param tags object = {}

resource search 'Microsoft.Search/searchServices@2024-03-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'basic'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    disableLocalAuth: false  // Set true when using RBAC-only
    semanticSearch: 'free'
  }
}

output id string = search.id
output name string = search.name
output endpoint string = 'https://${search.name}.search.windows.net'
```

### RBAC Assignment

```bicep
@description('Principal ID for index data operations')
param dataPrincipalId string

// Search Index Data Contributor role
var searchIndexContributorRoleId = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'

resource searchRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, dataPrincipalId, searchIndexContributorRoleId)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexContributorRoleId)
    principalId: dataPrincipalId
    principalType: 'ServicePrincipal'
  }
}
```

### Private Endpoint

```bicep
@description('Subnet ID for private endpoint')
param subnetId string = ''

@description('Private DNS zone ID')
param privateDnsZoneId string = ''

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = if (!empty(subnetId)) {
  name: 'pe-${name}'
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
          privateLinkServiceId: search.id
          groupIds: ['searchService']
        }
      }
    ]
  }
}

resource dnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = if (!empty(subnetId) && !empty(privateDnsZoneId)) {
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
```

## Application Code

### Python — Vector Search with Azure OpenAI Embeddings

```python
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    VectorSearch,
    HnswAlgorithmConfiguration,
    VectorSearchProfile,
    SearchableField,
    SimpleField,
)

credential = DefaultAzureCredential()
endpoint = "https://<search-name>.search.windows.net"

# Create index with vector field
index_client = SearchIndexClient(endpoint=endpoint, credential=credential)
index = SearchIndex(
    name="documents",
    fields=[
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SearchField(
            name="embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=1536,
            vector_search_profile_name="default",
        ),
    ],
    vector_search=VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
        profiles=[VectorSearchProfile(name="default", algorithm_configuration_name="hnsw")],
    ),
)
index_client.create_or_update_index(index)

# Search with vector query
from azure.search.documents.models import VectorizedQuery

search_client = SearchClient(endpoint=endpoint, index_name="documents", credential=credential)
results = search_client.search(
    search_text="user query",  # Hybrid: keyword + vector
    vector_queries=[
        VectorizedQuery(vector=query_embedding, k_nearest_neighbors=5, fields="embedding")
    ],
    query_type="semantic",
    semantic_configuration_name="default",
)
```

### C# — Vector Search

```csharp
using Azure.Identity;
using Azure.Search.Documents;
using Azure.Search.Documents.Indexes;
using Azure.Search.Documents.Models;

var credential = new DefaultAzureCredential();
var endpoint = new Uri("https://<search-name>.search.windows.net");

var searchClient = new SearchClient(endpoint, "documents", credential);

var options = new SearchOptions
{
    QueryType = SearchQueryType.Semantic,
    SemanticSearch = new SemanticSearchOptions
    {
        SemanticConfigurationName = "default",
    },
    VectorSearch = new VectorSearchOptions
    {
        Queries = {
            new VectorizedQuery(queryEmbedding)
            {
                KNearestNeighborsCount = 5,
                Fields = { "embedding" },
            }
        }
    },
};

SearchResults<SearchDocument> results = await searchClient.SearchAsync<SearchDocument>("user query", options);
```

### Node.js — Vector Search

```javascript
const { SearchClient } = require("@azure/search-documents");
const { DefaultAzureCredential } = require("@azure/identity");

const credential = new DefaultAzureCredential();
const client = new SearchClient(
  "https://<search-name>.search.windows.net",
  "documents",
  credential
);

const results = await client.search("user query", {
  queryType: "semantic",
  semanticSearchOptions: {
    configurationName: "default",
  },
  vectorSearchOptions: {
    queries: [
      {
        kind: "vector",
        vector: queryEmbedding,
        kNearestNeighborsCount: 5,
        fields: ["embedding"],
      },
    ],
  },
});
```

## Common Pitfalls

1. **Index schema changes require reindexing** -- Adding new fields is safe, but changing field types or analyzer settings requires deleting and recreating the index. Plan your schema carefully.
2. **Semantic ranker requires Standard tier or higher for production** -- Free tier is limited to 1,000 queries/month. Basic tier supports semantic ranker in free tier only.
3. **Vector dimensions must match embedding model** -- `text-embedding-ada-002` uses 1536 dimensions, `text-embedding-3-small` uses 1536 (default) or 512/256 with dimension reduction. Mismatch causes indexing errors.
4. **RBAC vs API keys** -- New deployments should use RBAC. API keys are simpler for POC but should be flagged for production migration. Set `disableLocalAuth: true` when ready.
5. **Skillset execution costs** -- AI enrichment (OCR, entity recognition, etc.) incurs Cognitive Services charges on top of search costs. Monitor carefully.
6. **Integrated vectorization vs push model** -- Integrated vectorization (preview) auto-generates embeddings during indexing. Push model requires you to generate embeddings before uploading. Push model is more mature.
7. **Indexer data source connection** -- When using indexers with Blob Storage or SQL, the search service needs network access to the data source. Private endpoints on both sides require shared private link.

## Production Backlog Items

- [ ] Switch from API key to RBAC-only authentication (`disableLocalAuth: true`)
- [ ] Enable private endpoint and disable public network access
- [ ] Configure shared private links for indexer data source access
- [ ] Upgrade to Standard tier for production semantic ranker quota
- [ ] Add replica for 99.9% availability SLA (2+ replicas required)
- [ ] Configure diagnostic settings for query analytics
- [ ] Implement index aliases for zero-downtime schema changes
- [ ] Set up scheduled indexer refresh for data source synchronization
- [ ] Configure custom analyzers for domain-specific content
- [ ] Add geo-redundancy if multi-region availability is needed
