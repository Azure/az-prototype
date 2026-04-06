# Infrastructure Layer

The layer responsible for provisioning all Azure resources via IaC. Owned by the `infrastructure-architect`, implemented by `terraform-agent` or `bicep-agent`.

## Owner

- **Primary**: `infrastructure-architect`
- **Delegates to**: `terraform-agent` (Terraform projects) or `bicep-agent` (Bicep projects)
- **Security review**: `security-architect` reviews all infrastructure stages

## Service Categories

### Core Networking

Azure resources that form the network foundation:

- Virtual Networks (VNets), subnets
- Network Security Groups (NSGs)
- VNet peering, VPN/ExpressRoute gateways
- Load balancers (public and internal)
- Private endpoints and private DNS zones
- Azure Firewall, Application Gateway, Front Door
- Azure Bastion

**ARM Namespaces**: `Microsoft.Network/*`

### Compute Services

Azure resources that host application workloads:

- Container Apps environments and container apps
- App Service plans and web apps
- Azure Functions
- Azure Kubernetes Service (AKS)
- Container Registry
- Static Web Apps

**ARM Namespaces**: `Microsoft.App/*`, `Microsoft.Web/*`, `Microsoft.ContainerService/*`, `Microsoft.ContainerRegistry/*`

### Supporting Services

Azure resources that support application functionality but are not data stores:

- API Management
- Event Grid
- IoT Hub
- Notification Hubs
- Communication Services
- Azure AI / Cognitive Services
- Azure OpenAI
- Azure Search

**ARM Namespaces**: `Microsoft.ApiManagement/*`, `Microsoft.EventGrid/*`, `Microsoft.CognitiveServices/*`, `Microsoft.Search/*`, etc.

## What Does NOT Belong Here

- **Managed identity and observability foundations** -- those are Core layer (cloud-architect)
- **Database schemas, stored procedures, seed data** -- those are Data layer
- **Application source code** (APIs, workers, frontends, Dockerfiles) -- those are Application layer
- **Data service configuration beyond provisioning** -- Infrastructure provisions the Azure resource; Data layer owns the data model

## Key Boundary: Provisioning vs Usage

Infrastructure layer provisions the Azure resource (e.g., creates a Service Bus namespace via IaC). The Application layer creates the code that *uses* that resource (e.g., the `IMessageSender` interface). The Data layer owns data schemas and access patterns (e.g., SQL tables, Cosmos containers).

## Deployment Order

Infrastructure deploys **after Core**, in this order:

1. **Networking** -- VNet, subnets, NSGs, private DNS zones, private endpoints (one stage)
2. **Compute infrastructure** -- Container Apps Environment, AKS cluster, App Service Plan
3. **Supporting services** -- APIM, Event Grid, AI services
4. **Integration** -- resources that connect other services together

Each infrastructure stage references Core outputs (identity, monitoring) and Networking outputs (subnets, DNS zones).

## Inter-Layer Communication

| Consumer | What Infrastructure Provides |
|----------|------------------------------|
| Data | Private endpoint connectivity, subnet IDs for VNET-integrated data services |
| Application | Container Apps endpoint URLs, registry login server, compute environment config |
| Core | (Infrastructure does not provide to Core -- dependency flows downward) |

## Governance

- All networking resources belong in a single Networking stage (no per-service private endpoints)
- Every resource must have diagnostic settings pointing to the Core layer's Log Analytics workspace
- RBAC assignments for infrastructure resources use the Core layer's managed identity
- Private endpoints must disable public network access on target resources
- Container Registry must use `AcrPull` role (no admin credentials)
