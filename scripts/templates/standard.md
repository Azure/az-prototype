# Dotnet
Standards for generated .NET/C# application code, including project structure, dependency management, Azure SDK patterns, and Azure Functions isolated worker model requirements.

**Domain:** `application`

<hr />

### Checks (2)

| Check | Description |
| ----- | ----------- |
| <span style="text-wrap:nowrap;">[STAN-CS-001](#STAN-CS-001)</span> | Use Azure SDK with DefaultAzureCredential: Always use DefaultAzureCredential from Azure.Identity for authenticating to Azure services.  This works with managed identity in Azure and developer credentials locally. |
| <span style="text-wrap:nowrap;">[STAN-CS-002](#STAN-CS-002)</span> | Complete Project Structure: Every generated .NET app must include a .csproj file with all NuGet PackageReferences, a Program.cs entry point, and all model/DTO classes referenced by services.  No file may reference type that is not defined in the generated output. |

<hr />

## STAN-CS-001
Use Azure SDK with DefaultAzureCredential: Always use DefaultAzureCredential from Azure.Identity for authenticating to Azure services.  This works with managed identity in Azure and developer credentials locally.

**Rationale:** DefaultAzureCredential provides seamless authentication across local dev and managed identity in production.  
**Agents:** `app-developer`

### Examples

- using Azure.Identity;
- var credential = new DefaultAzureCredential();
- Never pass connection strings when managed identity is available

<hr />

## STAN-CS-002
Complete Project Structure: Every generated .NET app must include a .csproj file with all NuGet PackageReferences, a Program.cs entry point, and all model/DTO classes referenced by services.  No file may reference a type that is not defined in the generated output.

**Rationale:** Complete outputs enable downstream stages to reference resources without hardcoding.  
**Agents:** `app-developer`

### Examples

- MyApp.csproj — project file with all PackageReferences
- Program.cs — entry point with DI registration
- Models/Project.cs — every referenced model class must exist
- If a service references 'ProjectDto', that class must be generated

<hr />