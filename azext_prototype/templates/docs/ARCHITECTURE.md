# [PROJECT_NAME] - Architecture

## Instructions for Claude Code

This template documents the complete architecture of the solution. Populate each section by analyzing:
- The approved scope of work (`deliverables/SCOPE_OF_WORK.md`)
- Discovery artifacts in `artifacts/`
- Infrastructure code in `concept/infra/`
- Application code in `concept/apps/`
- The `concept/AZURE_CONFIG.json` for deployed resources

Replace all `[PLACEHOLDER]` values with actual information. Remove sections that are not applicable. All Mermaid diagrams should accurately reflect the implemented architecture.

**Key Principles:**
- Diagrams should be accurate and reflect actual implementation
- Include all Azure services and their relationships
- Document data flows clearly
- Capture scaling and error handling strategies

---

## Table of Contents

1. [High-Level Architecture](#1-high-level-architecture)
2. [Workflow Pipeline](#2-workflow-pipeline)
3. [Request Lifecycle](#3-request-lifecycle)
4. [Azure Services Infrastructure](#4-azure-services-infrastructure)
5. [Data Storage Architecture](#5-data-storage-architecture)
6. [Service Dependencies](#6-service-dependencies)
7. [Scaling & Autoscaling](#7-scaling--autoscaling)
8. [Error Handling & Retry Logic](#8-error-handling--retry-logic)

---

## 1. High-Level Architecture

[Provide a brief description of the overall architecture approach, key design decisions, and rationale.]

```mermaid
flowchart TB
    subgraph Clients["Client Layer"]
        CLIENT1["[CLIENT_TYPE_1]<br/>[Description]"]
        CLIENT2["[CLIENT_TYPE_2]<br/>[Description]"]
    end

    subgraph API["API Layer"]
        APIM["[API_GATEWAY]<br/>[Purpose]"]
        API_HOST["[API_HOST_SERVICE]<br/>[Runtime]"]
    end

    subgraph Messaging["Message Queue"]
        QUEUE_SVC["[QUEUE_SERVICE]<br/>[SKU]"]
        QUEUE[("[QUEUE_NAME]")]
    end

    subgraph Processing["Processing Layer"]
        COMPUTE["[COMPUTE_SERVICE]"]
        PROCESSOR["[PROCESSOR_NAME]<br/>[Description]"]
    end

    subgraph AI["AI Services"]
        AI_SVC["[AI_SERVICE]<br/>[Models]"]
    end

    subgraph Storage["Data Layer"]
        DB1[("[DATABASE_1]<br/>[Purpose]")]
        DB2[("[DATABASE_2]<br/>[Purpose]")]
        STORAGE[("[STORAGE]<br/>[Purpose]")]
    end

    subgraph Monitoring["Observability"]
        INSIGHTS["[MONITORING_SERVICE]"]
        LOGS["[LOG_SERVICE]"]
    end

    subgraph Security["Security"]
        KV["Key Vault"]
        IDENTITY["[IDENTITY_SERVICE]"]
    end

    %% Define relationships
    Clients --> APIM
    APIM --> API_HOST
    API_HOST --> DB1
    API_HOST --> QUEUE_SVC
    QUEUE_SVC --> QUEUE
    QUEUE --> PROCESSOR
    PROCESSOR --> AI_SVC
    PROCESSOR --> DB1
    PROCESSOR --> STORAGE
    PROCESSOR -.-> KV
    PROCESSOR --> INSIGHTS
    INSIGHTS --> LOGS
```

### Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| [DECISION_1] | [CHOICE] | [RATIONALE] |
| [DECISION_2] | [CHOICE] | [RATIONALE] |
| [DECISION_3] | [CHOICE] | [RATIONALE] |

---

## 2. Workflow Pipeline

[Describe the main processing workflow. If using an orchestration framework (LangGraph, Durable Functions, Logic Apps, etc.), document the node/step structure.]

```mermaid
flowchart LR
    START([Input])

    subgraph Phase1["[PHASE_1_NAME]"]
        N1["1. [STEP_1]"]
        N2["2. [STEP_2]"]
    end

    subgraph Phase2["[PHASE_2_NAME]"]
        N3["3. [STEP_3]"]
        N4["4. [STEP_4]"]
    end

    subgraph Phase3["[PHASE_3_NAME]"]
        N5["5. [STEP_5]"]
        N6["6. [STEP_6]"]
    end

    COMPLETE([Output])
    ERROR([Error])

    START --> N1
    N1 --> N2
    N2 --> N3
    N3 --> N4
    N4 --> N5
    N5 --> N6
    N6 -->|Success| COMPLETE
    N6 -->|Failure| ERROR

    style COMPLETE fill:#c8e6c9
    style ERROR fill:#ffccbc
```

### Step Details

| Step | Purpose | Technology | Key Outputs |
|------|---------|------------|-------------|
| 1. [STEP_1] | [PURPOSE] | [TECH] | [OUTPUTS] |
| 2. [STEP_2] | [PURPOSE] | [TECH] | [OUTPUTS] |
| 3. [STEP_3] | [PURPOSE] | [TECH] | [OUTPUTS] |
| 4. [STEP_4] | [PURPOSE] | [TECH] | [OUTPUTS] |
| 5. [STEP_5] | [PURPOSE] | [TECH] | [OUTPUTS] |
| 6. [STEP_6] | [PURPOSE] | [TECH] | [OUTPUTS] |

---

## 3. Request Lifecycle

[Document the complete lifecycle of a request from client submission to final response.]

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API Layer
    participant Q as Queue
    participant P as Processor
    participant AI as AI Service
    participant DB as Database
    participant S as Storage

    C->>A: [REQUEST_TYPE] (JSON)
    A->>DB: [INITIAL_ACTION]

    alt [FAST_PATH_CONDITION]
        DB-->>A: [FAST_PATH_RESPONSE]
        A-->>C: 200 + [RESPONSE]
    else [ASYNC_PATH_CONDITION]
        A->>DB: Create job (status: queued)
        A->>Q: Queue message
        A-->>C: 202 Accepted (jobId)

        Q-->>P: Deliver message
        P->>DB: Update status (processing)

        loop [PROCESSING_LOOP]
            P->>AI: Process step
            AI-->>P: Response
            P->>DB: Update progress
        end

        P->>S: Store output
        P->>DB: Update status (completed)

        C->>A: GET /status/{jobId}
        A->>DB: Read job
        A-->>C: Status + output URL
    end
```

---

## 4. Azure Services Infrastructure

[Document all Azure services, their SKUs, and configurations.]

```mermaid
flowchart TB
    subgraph Compute["Compute Services"]
        COMPUTE1["[SERVICE_1]<br/>([SKU])"]
        COMPUTE2["[SERVICE_2]<br/>([SKU])"]
    end

    subgraph Messaging["Messaging"]
        MSG1["[MESSAGING_SERVICE]<br/>([SKU])"]
        MSG_DETAIL["[QUEUE/TOPIC]: [NAME]<br/>[CONFIG_DETAILS]"]
    end

    subgraph DataStores["Data Stores"]
        subgraph PrimaryDB["[PRIMARY_DB] ([SKU])"]
            CONTAINER1["[CONTAINER_1]<br/>PK: [PARTITION_KEY]"]
            CONTAINER2["[CONTAINER_2]<br/>PK: [PARTITION_KEY]"]
        end

        subgraph SecondaryDB["[SECONDARY_DB] ([SKU])"]
            TABLE1["[TABLE_1]"]
            TABLE2["[TABLE_2]"]
        end

        subgraph BlobStore["Blob Storage ([SKU])"]
            BLOB1["[CONTAINER_1]/"]
            BLOB2["[CONTAINER_2]/"]
        end
    end

    subgraph Monitoring["Monitoring"]
        MON1["[MONITORING_1]"]
        MON2["[MONITORING_2]<br/>([RETENTION])"]
    end

    subgraph Security["Security"]
        SEC1["Key Vault<br/>([SKU])"]
        SEC2["[IDENTITY_SERVICE]"]
    end
```

### Resource Summary

| Resource Type | Resource Name | SKU/Tier | Configuration |
|---------------|---------------|----------|---------------|
| [TYPE] | [NAME] | [SKU] | [CONFIG] |
| [TYPE] | [NAME] | [SKU] | [CONFIG] |
| [TYPE] | [NAME] | [SKU] | [CONFIG] |

---

## 5. Data Storage Architecture

### [PRIMARY_DATABASE] Collections/Tables

```mermaid
erDiagram
    [ENTITY_1] {
        [TYPE] [FIELD_1] PK
        [TYPE] [FIELD_2]
        [TYPE] [FIELD_3]
        [TYPE] [FIELD_4]
        datetime [TIMESTAMP_FIELD]
    }

    [ENTITY_2] {
        [TYPE] [FIELD_1] PK
        [TYPE] [FIELD_2]
        [TYPE] [FIELD_3]
        datetime [TIMESTAMP_FIELD]
    }

    [ENTITY_1] ||--o{ [ENTITY_2] : has
```

### [SECONDARY_DATABASE] Schema

```mermaid
erDiagram
    [TABLE_1] ||--o{ [TABLE_2] : has
    [TABLE_1] ||--o{ [TABLE_3] : has

    [TABLE_1] {
        [TYPE] [FIELD_1] PK
        [TYPE] [FIELD_2]
        [TYPE] [FIELD_3]
    }

    [TABLE_2] {
        [TYPE] [FIELD_1] PK
        [TYPE] [FIELD_2] FK
        [TYPE] [FIELD_3]
    }
```

### Blob Storage Structure

```
[CONTAINER_1]/
├── [FOLDER_1]/
│   ├── [FILE_TYPE]
│   └── [FILE_TYPE]
├── [FOLDER_2]/
│   └── ...
└── [FOLDER_3]/
    └── ...

[CONTAINER_2]/
├── {[ID_PATTERN]}/
│   ├── [OUTPUT_FILE]
│   ├── [METADATA_FILE]
│   └── [SUBFOLDER]/
│       └── [GENERATED_FILES]
└── ...
```

---

## 6. Service Dependencies

```mermaid
flowchart TB
    subgraph External["External Clients"]
        EXT1["[CLIENT_1]"]
        EXT2["[CLIENT_2]"]
    end

    subgraph API["API Layer"]
        API1["[API_SERVICE]"]
    end

    subgraph Queue["Messaging"]
        Q1["[QUEUE_SERVICE]"]
    end

    subgraph Process["Processing"]
        P1["[PROCESSOR]"]
    end

    subgraph AI["AI"]
        AI1["[AI_SERVICE]"]
    end

    subgraph Data["Storage"]
        D1["[DATABASE_1]"]
        D2["[DATABASE_2]"]
        D3["[STORAGE]"]
    end

    %% Dependencies
    EXT1 -->|HTTP| API1
    EXT2 -->|HTTP| API1

    API1 -->|Read/Write| D1
    API1 -->|Queue Messages| Q1

    Q1 -->|Deliver Messages| P1

    P1 -->|AI Processing| AI1
    P1 -->|State Management| D1
    P1 -->|Store Outputs| D3
    P1 -->|Log Metrics| D2
```

### Dependency Matrix

| Service | Depends On | Depended By |
|---------|------------|-------------|
| [SERVICE_1] | [DEPENDENCIES] | [DEPENDENTS] |
| [SERVICE_2] | [DEPENDENCIES] | [DEPENDENTS] |
| [SERVICE_3] | [DEPENDENCIES] | [DEPENDENTS] |
| [SERVICE_4] | [DEPENDENCIES] | [DEPENDENTS] |

---

## 7. Scaling & Autoscaling

```mermaid
flowchart TB
    subgraph Service1["[SERVICE_1] ([SCALING_TYPE])"]
        S1_SCALE["Auto-scale: [MIN] → [MAX]<br/>Trigger: [TRIGGER]<br/>Plan: [PLAN]"]
    end

    subgraph Service2["[SERVICE_2] ([SCALING_TYPE])"]
        S2_SCALE["Capacity: [CAPACITY]<br/>Throughput: [THROUGHPUT]<br/>Scale: [SCALE_METHOD]"]
    end

    subgraph Service3["[SERVICE_3] ([SCALING_TYPE])"]
        S3_SCALE["Auto-scale: [CONFIG]<br/>Trigger: [TRIGGER]<br/>Per unit: [RESOURCES]"]
    end

    TRIGGER["Incoming Requests"] --> Service1
    Service1 --> Service2
    Service2 --> Service3
```

### Scaling Configuration

| Component | Min | Max | Trigger | Scale Time |
|-----------|-----|-----|---------|------------|
| [COMPONENT_1] | [MIN] | [MAX] | [TRIGGER] | [TIME] |
| [COMPONENT_2] | [MIN] | [MAX] | [TRIGGER] | [TIME] |
| [COMPONENT_3] | [MIN] | [MAX] | [TRIGGER] | [TIME] |

---

## 8. Error Handling & Retry Logic

```mermaid
flowchart TB
    subgraph MessageRetry["Message Queue Retry"]
        MSG["Message Received"]
        PROCESS["Process"]

        MSG --> PROCESS
        PROCESS -->|Success| COMPLETE["Complete"]
        PROCESS -->|Failure| CHECK{"Attempt < [MAX]?"}
        CHECK -->|Yes| ABANDON["Retry<br/>(Requeue)"]
        CHECK -->|No| DEADLETTER["Dead-letter"]
        ABANDON -.->|Backoff| MSG
    end

    subgraph ErrorLogging["Error Logging"]
        ERR_DB["[ERROR_STORE_1]"]
        ERR_LOG["[ERROR_STORE_2]"]
    end

    DEADLETTER --> ERR_DB
    DEADLETTER --> ERR_LOG

    style COMPLETE fill:#c8e6c9
    style DEADLETTER fill:#ffccbc
```

### Error Codes

| Code | Description | Retryable | Max Retries |
|------|-------------|-----------|-------------|
| `[ERROR_CODE_1]` | [DESCRIPTION] | [YES/NO] | [COUNT] |
| `[ERROR_CODE_2]` | [DESCRIPTION] | [YES/NO] | [COUNT] |
| `[ERROR_CODE_3]` | [DESCRIPTION] | [YES/NO] | [COUNT] |
| `[ERROR_CODE_4]` | [DESCRIPTION] | [YES/NO] | [COUNT] |

---

## Technology Stack Summary

| Layer | Technology | Version |
|-------|------------|---------|
| **API** | [TECHNOLOGY] | [VERSION] |
| **Processing** | [TECHNOLOGY] | [VERSION] |
| **AI** | [TECHNOLOGY] | [MODELS] |
| **Messaging** | [TECHNOLOGY] | [SKU] |
| **Primary Database** | [TECHNOLOGY] | [SKU] |
| **Secondary Database** | [TECHNOLOGY] | [SKU] |
| **Storage** | [TECHNOLOGY] | [SKU] |
| **Monitoring** | [TECHNOLOGY] | [TYPE] |
| **IaC** | [TECHNOLOGY] | [VERSION] |

---

*Last updated: [DATE]*
