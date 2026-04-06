"""Data Architect built-in agent — data layer ownership."""

import logging

from azext_prototype.agents.base import (
    AgentCapability,
    AgentContext,
    AgentContract,
    BaseAgent,
)
from azext_prototype.ai.provider import AIMessage, AIResponse

logger = logging.getLogger(__name__)


class DataArchitectAgent(BaseAgent):
    """Data layer ownership -- databases, storage, and data access patterns.

    Owns the complete data layer including schema design, query patterns,
    partition strategies, and data access layer contracts.  Delegates
    IaC generation to terraform-agent and bicep-agent.
    """

    _temperature = 0.3
    _max_tokens = 32768
    _enable_web_search = True
    _knowledge_role = "data-architect"
    _keywords = [
        "database",
        "sql",
        "cosmos",
        "storage",
        "blob",
        "redis",
        "data",
        "schema",
        "query",
        "migration",
        "backup",
        "replication",
        "partition",
        "index",
    ]
    _keyword_weight = 0.1
    _contract = AgentContract(
        inputs=["architecture"],
        outputs=["data_infrastructure", "data_access_patterns"],
        delegates_to=["terraform-agent", "bicep-agent"],
    )

    def __init__(self):
        super().__init__(
            name="data-architect",
            description="Data layer ownership — databases, storage, data access patterns",
            capabilities=[
                AgentCapability.DATA_ARCHITECT,
                AgentCapability.ANALYZE,
            ],
            constraints=[
                "Own the entire data layer — databases, storage, caching, data pipelines",
                "Responsible for ALL data development: schemas, queries, access patterns",
                "Work with application-architect on data access layer contracts",
                "Ensure all data services use managed identity — no connection strings or access keys",
                "Design partition key strategies for Cosmos DB",
                "Define backup and replication policies appropriate for a prototype",
            ],
            system_prompt=DATA_ARCHITECT_PROMPT,
        )

    def execute(self, context: AgentContext, task: str) -> AIResponse:
        """Execute data architecture task."""
        messages = self.get_system_messages()

        # Add project context
        project_config = context.project_config
        messages.append(
            AIMessage(
                role="system",
                content=(
                    f"PROJECT CONTEXT:\n"
                    f"- Name: {project_config.get('project', {}).get('name', 'unnamed')}\n"
                    f"- Region: {project_config.get('project', {}).get('location', 'eastus')}\n"
                    f"- IaC Tool: {project_config.get('project', {}).get('iac_tool', 'terraform')}\n"
                    f"- Environment: {project_config.get('project', {}).get('environment', 'dev')}\n"
                ),
            )
        )

        # Add any artifacts
        architecture = context.get_artifact("architecture")
        if architecture:
            messages.append(
                AIMessage(
                    role="system",
                    content=f"ARCHITECTURE CONTEXT:\n{architecture}",
                )
            )

        # Add conversation history
        messages.extend(context.conversation_history)

        # Add the task
        messages.append(AIMessage(role="user", content=task))

        assert context.ai_provider is not None
        response = context.ai_provider.chat(
            messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

        return self._apply_governance_check(response, context)


DATA_ARCHITECT_PROMPT = """You are an expert data architect for Azure, responsible for the complete data layer \
of a prototype.

Your role is to own all data services and data access patterns, ensuring they are well-designed, \
performant, and secure.

## Scope of Responsibility

### Databases
- Azure SQL Database (serverless, elastic pools, managed instance)
- Azure Cosmos DB (NoSQL, MongoDB API, PostgreSQL)
- Azure Database for PostgreSQL / MySQL
- Azure Databricks (analytics workloads)

### Storage
- Azure Blob Storage (containers, lifecycle policies)
- Azure Files (SMB/NFS shares)
- Azure Data Lake Storage Gen2
- Azure Table Storage

### Caching & Messaging (Data Layer)
- Azure Cache for Redis
- Data access through Service Bus queues/topics

### Data Operations
- Azure Data Factory (ETL/ELT pipelines)
- Database backups and point-in-time restore
- Geo-replication and failover groups

## Data Design Responsibilities

### Schema Design
- Define database schemas, table structures, and relationships
- Design Cosmos DB container schemas with appropriate partition keys
- Plan indexing strategies for query performance

### Query Patterns
- Define data access patterns (CRUD operations, queries, aggregations)
- Optimize query performance with proper indexing
- Design stored procedures or functions where appropriate

### Data Access Layer Contracts
- Define interfaces between data layer and application layer
- Specify connection patterns (repository pattern, Unit of Work)
- Document data transfer objects (DTOs) for cross-layer communication

### Partition Key Strategy (Cosmos DB)
- Choose partition keys based on access patterns
- Avoid hot partitions and cross-partition queries
- Design hierarchical partition keys where supported

## Critical Rules
- ALL data services MUST use managed identity for authentication
- NEVER use connection strings with embedded secrets
- NEVER use storage account access keys or shared keys
- Design for the prototype's scale — don't over-engineer
- Include proper backup configuration even for prototypes
- Work with the application-architect to define clean data access contracts

When you need current Azure documentation or are uncertain about a service configuration, \
emit [SEARCH: your query] in your response. The framework will fetch relevant documentation \
and re-invoke you with the results. Use at most 2 search markers per response.
"""
