# [PROJECT_NAME] Constitution

## Core Principles

### I. Azure-First Architecture
All infrastructure and services target Microsoft Azure. Resource selection follows Azure Well-Architected Framework pillars: reliability, security, cost optimization, operational excellence, and performance efficiency.

### II. Infrastructure as Code
All Azure resources are defined declaratively using [IAC_TOOL]. No manual portal changes in production. Infrastructure must be reproducible from code alone.

### III. Environment Isolation
Development, staging, and production environments are fully isolated. Resource naming follows the [NAMING_STRATEGY] naming convention to prevent cross-environment conflicts.

### IV. Security by Default
All data encrypted at rest and in transit. Managed identities preferred over connection strings. Network access restricted by default. Secrets stored in Azure Key Vault, never in code.

### V. Cost-Conscious Prototyping
Prototype uses cost-appropriate SKUs. Production scaling paths are documented but not provisioned. Cost estimates accompany all architecture decisions.

## Constraints

[CONSTRAINTS]

## Governance

- Constitution supersedes ad-hoc decisions during prototyping
- Amendments require documented justification
- All architecture changes must be traceable to a requirement

**Version**: 1.0.0 | **Ratified**: [DATE] | **Last Amended**: [DATE]
