---
service_namespace: Microsoft.ApiManagement/service/apis/operations
display_name: API Management API Operation
depends_on:
  - Microsoft.ApiManagement/service/apis
---

# API Management API Operation

> A single HTTP operation (GET, POST, PUT, DELETE, etc.) within an APIM API that maps a frontend URL pattern to a backend endpoint with optional policies.

## When to Use
- Define individual endpoints within an API (e.g., GET /users, POST /orders)
- Apply operation-specific policies (caching on GET, validation on POST)
- Configure request/response schemas for developer portal documentation
- Map frontend URL templates to different backend paths
- Required for manually-defined APIs (auto-created when importing OpenAPI specs)

## POC Defaults
- **Method**: GET, POST, PUT, DELETE as needed
- **URL template**: RESTful pattern (e.g., `/users/{id}`)
- **Response**: 200 OK with JSON content type

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "apim_operation_get" {
  type      = "Microsoft.ApiManagement/service/apis/operations@2024-05-01"
  name      = "get-items"
  parent_id = azapi_resource.apim_api.id

  body = {
    properties = {
      displayName = "Get Items"
      method      = "GET"
      urlTemplate = "/items"
      description = "Retrieve all items"
      responses = [
        {
          statusCode  = 200
          description = "Success"
          representations = [
            { contentType = "application/json" }
          ]
        }
      ]
    }
  }
}

resource "azapi_resource" "apim_operation_get_by_id" {
  type      = "Microsoft.ApiManagement/service/apis/operations@2024-05-01"
  name      = "get-item-by-id"
  parent_id = azapi_resource.apim_api.id

  body = {
    properties = {
      displayName = "Get Item by ID"
      method      = "GET"
      urlTemplate = "/items/{id}"
      description = "Retrieve a single item by ID"
      templateParameters = [
        {
          name     = "id"
          type     = "string"
          required = true
        }
      ]
      responses = [
        {
          statusCode  = 200
          description = "Success"
          representations = [
            { contentType = "application/json" }
          ]
        },
        {
          statusCode  = 404
          description = "Not found"
        }
      ]
    }
  }
}

resource "azapi_resource" "apim_operation_post" {
  type      = "Microsoft.ApiManagement/service/apis/operations@2024-05-01"
  name      = "create-item"
  parent_id = azapi_resource.apim_api.id

  body = {
    properties = {
      displayName = "Create Item"
      method      = "POST"
      urlTemplate = "/items"
      description = "Create a new item"
      request = {
        representations = [
          { contentType = "application/json" }
        ]
      }
      responses = [
        {
          statusCode  = 201
          description = "Created"
          representations = [
            { contentType = "application/json" }
          ]
        }
      ]
    }
  }
}
```

### RBAC Assignment
```hcl
# Operation management inherits from the APIM service and API RBAC.
# API Management Service Contributor role allows operation management.
```

## Bicep Patterns

### Basic Resource
```bicep
resource getItems 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: api
  name: 'get-items'
  properties: {
    displayName: 'Get Items'
    method: 'GET'
    urlTemplate: '/items'
    description: 'Retrieve all items'
    responses: [
      {
        statusCode: 200
        description: 'Success'
        representations: [
          { contentType: 'application/json' }
        ]
      }
    ]
  }
}

resource getItemById 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: api
  name: 'get-item-by-id'
  properties: {
    displayName: 'Get Item by ID'
    method: 'GET'
    urlTemplate: '/items/{id}'
    templateParameters: [
      {
        name: 'id'
        type: 'string'
        required: true
      }
    ]
    responses: [
      { statusCode: 200, description: 'Success' }
      { statusCode: 404, description: 'Not found' }
    ]
  }
}
```

## Application Code

### Python
```python
# Operations are infrastructure — clients call the API through the APIM gateway.
# The operation defines the URL pattern; the backend handles the logic.
import requests

apim_base = "https://<apim-name>.azure-api.net/<api-path>"
headers = {"Ocp-Apim-Subscription-Key": subscription_key}

# GET /items (matches get-items operation)
items = requests.get(f"{apim_base}/items", headers=headers).json()

# GET /items/{id} (matches get-item-by-id operation)
item = requests.get(f"{apim_base}/items/123", headers=headers).json()

# POST /items (matches create-item operation)
new_item = requests.post(f"{apim_base}/items", json={"name": "Widget"}, headers=headers).json()
```

### C#
```csharp
using System.Net.Http;
using System.Text.Json;

var client = new HttpClient();
client.BaseAddress = new Uri("https://<apim-name>.azure-api.net/<api-path>/");
client.DefaultRequestHeaders.Add("Ocp-Apim-Subscription-Key", subscriptionKey);

// GET /items
var items = await client.GetFromJsonAsync<List<Item>>("items");

// GET /items/{id}
var item = await client.GetFromJsonAsync<Item>("items/123");

// POST /items
var response = await client.PostAsJsonAsync("items", new { name = "Widget" });
```

### Node.js
```typescript
const apimBase = "https://<apim-name>.azure-api.net/<api-path>";
const headers = { "Ocp-Apim-Subscription-Key": subscriptionKey };

// GET /items
const items = await fetch(`${apimBase}/items`, { headers }).then(r => r.json());

// GET /items/{id}
const item = await fetch(`${apimBase}/items/123`, { headers }).then(r => r.json());

// POST /items
const newItem = await fetch(`${apimBase}/items`, {
  method: "POST",
  headers: { ...headers, "Content-Type": "application/json" },
  body: JSON.stringify({ name: "Widget" }),
}).then(r => r.json());
```

## Common Pitfalls
- **Operation name uniqueness**: The `name` (resource name) must be unique within the API. It's used as the operation ID — use kebab-case descriptive names.
- **URL template conflicts**: Two operations with the same method and overlapping URL templates (e.g., `GET /items/{id}` and `GET /items/{name}`) create ambiguity. Use distinct patterns.
- **OpenAPI import overwrites**: If you import an OpenAPI spec and also define operations manually, the import replaces all operations. Choose one approach.
- **Template parameter declaration**: Parameters in the URL template (e.g., `{id}`) must be declared in `templateParameters`. Missing declarations cause 404 responses.
- **Policy scoping**: Operation-level policies override API-level policies. Use `<base />` in operation policies to inherit parent policies.
- **Method case sensitivity**: The `method` property must be uppercase (`GET`, `POST`, not `get`, `post`).

## Production Backlog Items
- Operation-level caching policies for GET endpoints
- Request validation policies (JSON schema, required headers)
- Response transformation and masking for sensitive data
- Per-operation rate limiting and quota policies
- Mock responses for development and testing
