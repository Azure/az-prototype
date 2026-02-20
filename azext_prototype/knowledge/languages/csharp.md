# C# / .NET Language Patterns for Azure Prototypes

Reference patterns for C#/.NET-based Azure prototype applications. Agents should use these patterns when generating .NET application code.

## ASP.NET Core Minimal API Structure (Recommended)

```
src/
  ProjectName.Api/
    Program.cs                # Application entry point + DI + endpoint mapping
    appsettings.json          # Base configuration
    appsettings.Development.json
    Dockerfile
    ProjectName.Api.csproj
    Models/
      ApiModels.cs            # Request/response DTOs
    Services/
      IAzureStorageService.cs
      AzureStorageService.cs
    Health/
      AzureStorageHealthCheck.cs
  ProjectName.Api.Tests/
    ProjectName.Api.Tests.csproj
    HealthTests.cs
    ApiTests.cs
    WebAppFixture.cs
```

### Program.cs (Minimal API)
```csharp
using Azure.Identity;
using Azure.Core;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using ProjectName.Api.Services;
using ProjectName.Api.Health;

var builder = WebApplication.CreateBuilder(args);

// --- Azure Identity ---
builder.Services.AddSingleton<TokenCredential>(sp =>
{
    var clientId = builder.Configuration["ManagedIdentity:ClientId"];
    return string.IsNullOrEmpty(clientId)
        ? new DefaultAzureCredential()
        : new ManagedIdentityCredential(clientId);
});

// --- Azure SDK Clients ---
builder.Services.AddSingleton(sp =>
{
    var credential = sp.GetRequiredService<TokenCredential>();
    var endpoint = builder.Configuration["AzureStorage:Endpoint"];
    return new Azure.Storage.Blobs.BlobServiceClient(new Uri(endpoint), credential);
});

builder.Services.AddSingleton(sp =>
{
    var credential = sp.GetRequiredService<TokenCredential>();
    var endpoint = builder.Configuration["KeyVault:Endpoint"];
    return new Azure.Security.KeyVault.Secrets.SecretClient(new Uri(endpoint), credential);
});

// --- Application Services ---
builder.Services.AddScoped<IAzureStorageService, AzureStorageService>();

// --- Health Checks ---
builder.Services.AddHealthChecks()
    .AddCheck<AzureStorageHealthCheck>("azure_storage", tags: new[] { "ready" });

// --- Logging ---
builder.Logging.AddConsole();
builder.Logging.AddJsonConsole(options =>
{
    options.TimestampFormat = "yyyy-MM-ddTHH:mm:ssZ";
    options.UseUtcTimestamp = true;
});

// --- OpenAPI ---
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

var app = builder.Build();

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}

// --- Error Handling Middleware ---
app.UseExceptionHandler(errorApp =>
{
    errorApp.Run(async context =>
    {
        context.Response.StatusCode = 500;
        context.Response.ContentType = "application/json";
        await context.Response.WriteAsJsonAsync(new { error = "Internal server error" });
    });
});

// --- Health Endpoints ---
app.MapHealthChecks("/health", new()
{
    Predicate = _ => false,  // Basic liveness
    ResponseWriter = async (context, report) =>
    {
        context.Response.ContentType = "application/json";
        await context.Response.WriteAsJsonAsync(new { status = "healthy" });
    }
});

app.MapHealthChecks("/healthz");

app.MapHealthChecks("/readyz", new()
{
    Predicate = check => check.Tags.Contains("ready"),
    ResponseWriter = async (context, report) =>
    {
        context.Response.ContentType = "application/json";
        var checks = report.Entries.ToDictionary(
            e => e.Key,
            e => e.Value.Status.ToString().ToLower()
        );
        await context.Response.WriteAsJsonAsync(new
        {
            status = report.Status.ToString().ToLower(),
            checks
        });
    }
});

// --- API Endpoints ---
app.MapGet("/api/v1/items", async (IAzureStorageService storage) =>
{
    var items = await storage.ListItemsAsync();
    return Results.Ok(items);
});

app.MapPost("/api/v1/items", async (CreateItemRequest request, IAzureStorageService storage) =>
{
    var id = await storage.CreateItemAsync(request);
    return Results.Created($"/api/v1/items/{id}", new { id });
});

app.Run();

// Make Program class accessible for integration tests
public partial class Program { }
```

## ASP.NET Core Web API Structure (Alternative)

For larger projects with controllers:

```csharp
// Controllers/ItemsController.cs
using Microsoft.AspNetCore.Mvc;

namespace ProjectName.Api.Controllers;

[ApiController]
[Route("api/v1/[controller]")]
public class ItemsController : ControllerBase
{
    private readonly IAzureStorageService _storage;
    private readonly ILogger<ItemsController> _logger;

    public ItemsController(IAzureStorageService storage, ILogger<ItemsController> logger)
    {
        _storage = storage;
        _logger = logger;
    }

    [HttpGet]
    public async Task<IActionResult> List()
    {
        _logger.LogInformation("Listing items");
        var items = await _storage.ListItemsAsync();
        return Ok(items);
    }

    [HttpPost]
    public async Task<IActionResult> Create([FromBody] CreateItemRequest request)
    {
        _logger.LogInformation("Creating item: {Name}", request.Name);
        var id = await _storage.CreateItemAsync(request);
        return CreatedAtAction(nameof(List), new { id }, new { id });
    }
}
```

## Azure SDK Initialization with DI

### Full DI Registration Pattern
```csharp
// Extensions/AzureServiceExtensions.cs
using Azure.Core;
using Azure.Identity;
using Azure.Storage.Blobs;
using Azure.Security.KeyVault.Secrets;
using Microsoft.Azure.Cosmos;
using Azure.Messaging.ServiceBus;

namespace ProjectName.Api.Extensions;

public static class AzureServiceExtensions
{
    public static IServiceCollection AddAzureClients(
        this IServiceCollection services,
        IConfiguration configuration)
    {
        // Shared credential
        services.AddSingleton<TokenCredential>(sp =>
        {
            var clientId = configuration["ManagedIdentity:ClientId"];
            return string.IsNullOrEmpty(clientId)
                ? new DefaultAzureCredential()
                : new ManagedIdentityCredential(clientId);
        });

        // Storage
        var storageEndpoint = configuration["AzureStorage:Endpoint"];
        if (!string.IsNullOrEmpty(storageEndpoint))
        {
            services.AddSingleton(sp =>
                new BlobServiceClient(
                    new Uri(storageEndpoint),
                    sp.GetRequiredService<TokenCredential>()));
        }

        // Key Vault
        var kvEndpoint = configuration["KeyVault:Endpoint"];
        if (!string.IsNullOrEmpty(kvEndpoint))
        {
            services.AddSingleton(sp =>
                new SecretClient(
                    new Uri(kvEndpoint),
                    sp.GetRequiredService<TokenCredential>()));
        }

        // Cosmos DB
        var cosmosEndpoint = configuration["CosmosDb:Endpoint"];
        if (!string.IsNullOrEmpty(cosmosEndpoint))
        {
            services.AddSingleton(sp =>
                new CosmosClient(
                    cosmosEndpoint,
                    sp.GetRequiredService<TokenCredential>(),
                    new CosmosClientOptions
                    {
                        SerializerOptions = new CosmosSerializationOptions
                        {
                            PropertyNamingPolicy = CosmosPropertyNamingPolicy.CamelCase,
                        }
                    }));
        }

        // Service Bus
        var sbNamespace = configuration["ServiceBus:Namespace"];
        if (!string.IsNullOrEmpty(sbNamespace))
        {
            services.AddSingleton(sp =>
                new ServiceBusClient(
                    sbNamespace,
                    sp.GetRequiredService<TokenCredential>()));
        }

        return services;
    }
}
```

Usage in `Program.cs`:
```csharp
builder.Services.AddAzureClients(builder.Configuration);
```

## Dockerfile Pattern (Multi-Stage Build)

```dockerfile
# Stage 1: Build
FROM mcr.microsoft.com/dotnet/sdk:9.0 AS build
WORKDIR /src

# Copy project file and restore (layer caching)
COPY ["src/ProjectName.Api/ProjectName.Api.csproj", "ProjectName.Api/"]
RUN dotnet restore "ProjectName.Api/ProjectName.Api.csproj"

# Copy source and publish
COPY src/ .
WORKDIR /src/ProjectName.Api
RUN dotnet publish -c Release -o /app/publish --no-restore

# Stage 2: Runtime
FROM mcr.microsoft.com/dotnet/aspnet:9.0 AS runtime

# Security: non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app
COPY --from=build /app/publish .

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8080
ENV ASPNETCORE_URLS=http://+:8080
ENV DOTNET_EnableDiagnostics=0

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

ENTRYPOINT ["dotnet", "ProjectName.Api.dll"]
```

## Health Check Endpoint (Built-in IHealthCheck)

```csharp
// Health/AzureStorageHealthCheck.cs
using Azure.Storage.Blobs;
using Microsoft.Extensions.Diagnostics.HealthChecks;

namespace ProjectName.Api.Health;

public class AzureStorageHealthCheck : IHealthCheck
{
    private readonly BlobServiceClient _blobClient;
    private readonly ILogger<AzureStorageHealthCheck> _logger;

    public AzureStorageHealthCheck(BlobServiceClient blobClient, ILogger<AzureStorageHealthCheck> logger)
    {
        _blobClient = blobClient;
        _logger = logger;
    }

    public async Task<HealthCheckResult> CheckHealthAsync(
        HealthCheckContext context,
        CancellationToken cancellationToken = default)
    {
        try
        {
            await _blobClient.GetAccountInfoAsync(cancellationToken);
            return HealthCheckResult.Healthy("Azure Storage is reachable");
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Azure Storage health check failed");
            return HealthCheckResult.Unhealthy("Azure Storage is unreachable", ex);
        }
    }
}
```

```csharp
// Health/CosmosDbHealthCheck.cs
using Microsoft.Azure.Cosmos;
using Microsoft.Extensions.Diagnostics.HealthChecks;

namespace ProjectName.Api.Health;

public class CosmosDbHealthCheck : IHealthCheck
{
    private readonly CosmosClient _cosmosClient;

    public CosmosDbHealthCheck(CosmosClient cosmosClient)
    {
        _cosmosClient = cosmosClient;
    }

    public async Task<HealthCheckResult> CheckHealthAsync(
        HealthCheckContext context,
        CancellationToken cancellationToken = default)
    {
        try
        {
            await _cosmosClient.ReadAccountAsync();
            return HealthCheckResult.Healthy("Cosmos DB is reachable");
        }
        catch (Exception ex)
        {
            return HealthCheckResult.Unhealthy("Cosmos DB is unreachable", ex);
        }
    }
}
```

## NuGet Package Management

### .csproj File
```xml
<Project Sdk="Microsoft.NET.Sdk.Web">

  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>

  <ItemGroup>
    <!-- Azure Identity (required) -->
    <PackageReference Include="Azure.Identity" Version="1.13.2" />

    <!-- Azure SDK services (add as needed) -->
    <PackageReference Include="Azure.Storage.Blobs" Version="12.23.0" />
    <PackageReference Include="Azure.Security.KeyVault.Secrets" Version="4.7.0" />
    <PackageReference Include="Microsoft.Azure.Cosmos" Version="3.43.1" />
    <PackageReference Include="Azure.Messaging.ServiceBus" Version="7.18.3" />

    <!-- Health checks -->
    <PackageReference Include="Microsoft.Extensions.Diagnostics.HealthChecks" Version="9.0.0" />

    <!-- OpenAPI -->
    <PackageReference Include="Swashbuckle.AspNetCore" Version="7.2.0" />
  </ItemGroup>

</Project>
```

### Test Project .csproj
```xml
<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
    <Nullable>enable</Nullable>
    <IsPackable>false</IsPackable>
  </PropertyGroup>

  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.12.0" />
    <PackageReference Include="xunit" Version="2.9.2" />
    <PackageReference Include="xunit.runner.visualstudio" Version="2.8.2" />
    <PackageReference Include="Moq" Version="4.20.72" />
    <PackageReference Include="Microsoft.AspNetCore.Mvc.Testing" Version="9.0.0" />
  </ItemGroup>

  <ItemGroup>
    <ProjectReference Include="..\ProjectName.Api\ProjectName.Api.csproj" />
  </ItemGroup>

</Project>
```

## appsettings.json / appsettings.Development.json

### appsettings.json
```json
{
  "Logging": {
    "LogLevel": {
      "Default": "Information",
      "Microsoft.AspNetCore": "Warning",
      "Azure.Core": "Warning",
      "Azure.Identity": "Warning"
    },
    "Console": {
      "FormatterName": "json",
      "FormatterOptions": {
        "TimestampFormat": "yyyy-MM-ddTHH:mm:ssZ",
        "UseUtcTimestamp": true
      }
    }
  },
  "ManagedIdentity": {
    "ClientId": ""
  },
  "AzureStorage": {
    "Endpoint": ""
  },
  "KeyVault": {
    "Endpoint": ""
  },
  "CosmosDb": {
    "Endpoint": "",
    "DatabaseName": ""
  },
  "ServiceBus": {
    "Namespace": ""
  },
  "AzureOpenAI": {
    "Endpoint": "",
    "DeploymentName": ""
  }
}
```

### appsettings.Development.json
```json
{
  "Logging": {
    "LogLevel": {
      "Default": "Debug",
      "Microsoft.AspNetCore": "Information"
    },
    "Console": {
      "FormatterName": "simple"
    }
  },
  "AzureStorage": {
    "Endpoint": "https://devstorageaccount.blob.core.windows.net"
  },
  "KeyVault": {
    "Endpoint": "https://dev-keyvault.vault.azure.net"
  }
}
```

## Logging with ILogger

```csharp
// Services/AzureStorageService.cs
using Azure.Storage.Blobs;

namespace ProjectName.Api.Services;

public interface IAzureStorageService
{
    Task<IEnumerable<string>> ListItemsAsync(string containerName = "items");
    Task<string> CreateItemAsync(CreateItemRequest request, string containerName = "items");
}

public class AzureStorageService : IAzureStorageService
{
    private readonly BlobServiceClient _blobClient;
    private readonly ILogger<AzureStorageService> _logger;

    public AzureStorageService(BlobServiceClient blobClient, ILogger<AzureStorageService> logger)
    {
        _blobClient = blobClient;
        _logger = logger;
    }

    public async Task<IEnumerable<string>> ListItemsAsync(string containerName = "items")
    {
        _logger.LogInformation("Listing blobs in container {ContainerName}", containerName);

        var container = _blobClient.GetBlobContainerClient(containerName);
        var items = new List<string>();

        await foreach (var blob in container.GetBlobsAsync())
        {
            items.Add(blob.Name);
        }

        _logger.LogInformation("Found {Count} blobs in {ContainerName}", items.Count, containerName);
        return items;
    }

    public async Task<string> CreateItemAsync(CreateItemRequest request, string containerName = "items")
    {
        var blobName = $"{Guid.NewGuid()}.json";
        _logger.LogInformation("Creating blob {BlobName} in {ContainerName}", blobName, containerName);

        var container = _blobClient.GetBlobContainerClient(containerName);
        await container.CreateIfNotExistsAsync();

        var blobClient = container.GetBlobClient(blobName);
        var content = System.Text.Json.JsonSerializer.Serialize(request);
        await blobClient.UploadAsync(BinaryData.FromString(content), overwrite: true);

        _logger.LogInformation("Created blob {BlobName} successfully", blobName);
        return blobName;
    }
}
```

## Error Handling Middleware

```csharp
// Middleware/ErrorHandlingMiddleware.cs
using System.Net;
using System.Text.Json;
using Azure;
using Azure.Identity;

namespace ProjectName.Api.Middleware;

public class ErrorHandlingMiddleware
{
    private readonly RequestDelegate _next;
    private readonly ILogger<ErrorHandlingMiddleware> _logger;

    public ErrorHandlingMiddleware(RequestDelegate next, ILogger<ErrorHandlingMiddleware> logger)
    {
        _next = next;
        _logger = logger;
    }

    public async Task InvokeAsync(HttpContext context)
    {
        try
        {
            await _next(context);
        }
        catch (AuthenticationFailedException ex)
        {
            _logger.LogError(ex, "Azure authentication failed");
            await WriteErrorResponse(context, HttpStatusCode.Unauthorized,
                "Authentication failed. Verify managed identity configuration.");
        }
        catch (RequestFailedException ex) when (ex.Status == 401)
        {
            _logger.LogError(ex, "Azure authorization failed (401)");
            await WriteErrorResponse(context, HttpStatusCode.Unauthorized,
                "Authentication failed. Verify identity has required RBAC role.");
        }
        catch (RequestFailedException ex) when (ex.Status == 403)
        {
            _logger.LogError(ex, "Azure authorization failed (403)");
            await WriteErrorResponse(context, HttpStatusCode.Forbidden,
                "Authorization failed. Check RBAC role assignments.");
        }
        catch (RequestFailedException ex) when (ex.Status == 404)
        {
            _logger.LogWarning(ex, "Azure resource not found");
            await WriteErrorResponse(context, HttpStatusCode.NotFound,
                "Resource not found.");
        }
        catch (RequestFailedException ex)
        {
            _logger.LogError(ex, "Azure SDK error (status={Status})", ex.Status);
            await WriteErrorResponse(context, HttpStatusCode.BadGateway,
                "Azure service error.");
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Unhandled exception on {Method} {Path}",
                context.Request.Method, context.Request.Path);
            await WriteErrorResponse(context, HttpStatusCode.InternalServerError,
                "Internal server error.");
        }
    }

    private static async Task WriteErrorResponse(HttpContext context, HttpStatusCode statusCode, string message)
    {
        context.Response.StatusCode = (int)statusCode;
        context.Response.ContentType = "application/json";
        var json = JsonSerializer.Serialize(new { error = message });
        await context.Response.WriteAsync(json);
    }
}
```

Register in `Program.cs`:
```csharp
app.UseMiddleware<ErrorHandlingMiddleware>();
```

## Testing Patterns (xUnit, WebApplicationFactory)

### WebAppFixture
```csharp
// Tests/WebAppFixture.cs
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Azure.Core;
using Azure.Storage.Blobs;
using Moq;

namespace ProjectName.Api.Tests;

public class WebAppFixture : WebApplicationFactory<Program>
{
    public Mock<BlobServiceClient> MockBlobClient { get; } = new();
    public Mock<TokenCredential> MockCredential { get; } = new();

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.ConfigureServices(services =>
        {
            // Remove real Azure clients
            RemoveService<TokenCredential>(services);
            RemoveService<BlobServiceClient>(services);

            // Register mocks
            services.AddSingleton<TokenCredential>(MockCredential.Object);
            services.AddSingleton(MockBlobClient.Object);
        });
    }

    private static void RemoveService<T>(IServiceCollection services)
    {
        var descriptor = services.SingleOrDefault(d => d.ServiceType == typeof(T));
        if (descriptor != null)
            services.Remove(descriptor);
    }
}
```

### Health Check Tests
```csharp
// Tests/HealthTests.cs
using System.Net;
using System.Text.Json;

namespace ProjectName.Api.Tests;

public class HealthTests : IClassFixture<WebAppFixture>
{
    private readonly HttpClient _client;

    public HealthTests(WebAppFixture fixture)
    {
        _client = fixture.CreateClient();
    }

    [Fact]
    public async Task Health_ReturnsOk()
    {
        var response = await _client.GetAsync("/health");
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var content = await response.Content.ReadAsStringAsync();
        var json = JsonDocument.Parse(content);
        Assert.Equal("healthy", json.RootElement.GetProperty("status").GetString());
    }

    [Fact]
    public async Task Healthz_ReturnsOk()
    {
        var response = await _client.GetAsync("/healthz");
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }
}
```

### API Tests
```csharp
// Tests/ApiTests.cs
using System.Net;
using System.Net.Http.Json;
using Azure;
using Azure.Storage.Blobs;
using Azure.Storage.Blobs.Models;
using Moq;

namespace ProjectName.Api.Tests;

public class ApiTests : IClassFixture<WebAppFixture>
{
    private readonly HttpClient _client;
    private readonly WebAppFixture _fixture;

    public ApiTests(WebAppFixture fixture)
    {
        _fixture = fixture;
        _client = fixture.CreateClient();
    }

    [Fact]
    public async Task ListItems_ReturnsOk()
    {
        // Arrange
        var containerMock = new Mock<BlobContainerClient>();
        var blobPages = AsyncPageable<BlobItem>.FromPages(new[]
        {
            Page<BlobItem>.FromValues(new[]
            {
                BlobsModelFactory.BlobItem("item1.json"),
                BlobsModelFactory.BlobItem("item2.json"),
            }, null, Mock.Of<Response>())
        });

        containerMock.Setup(c => c.GetBlobsAsync(default, default, default, default))
            .Returns(blobPages);
        _fixture.MockBlobClient
            .Setup(b => b.GetBlobContainerClient("items"))
            .Returns(containerMock.Object);

        // Act
        var response = await _client.GetAsync("/api/v1/items");

        // Assert
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var items = await response.Content.ReadFromJsonAsync<string[]>();
        Assert.Equal(2, items?.Length);
    }

    [Fact]
    public async Task CreateItem_ReturnsCreated()
    {
        // Arrange
        var containerMock = new Mock<BlobContainerClient>();
        var blobMock = new Mock<BlobClient>();
        containerMock.Setup(c => c.GetBlobClient(It.IsAny<string>())).Returns(blobMock.Object);
        containerMock.Setup(c => c.CreateIfNotExistsAsync(default, default, default, default))
            .ReturnsAsync(Mock.Of<Response<BlobContainerInfo>>());

        _fixture.MockBlobClient
            .Setup(b => b.GetBlobContainerClient("items"))
            .Returns(containerMock.Object);

        var request = new { Name = "Test Item", Description = "A test" };

        // Act
        var response = await _client.PostAsJsonAsync("/api/v1/items", request);

        // Assert
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
    }
}
```
