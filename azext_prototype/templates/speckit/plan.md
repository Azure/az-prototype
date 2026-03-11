# Implementation Plan: [PROJECT_NAME]

**Date**: [DATE]
**IaC Tool**: [IAC_TOOL]
**Location**: [LOCATION]

## Summary

[PLAN_SUMMARY]

## Technical Context

**Cloud Provider**: Microsoft Azure
**IaC Tool**: [IAC_TOOL]
**Naming Strategy**: [NAMING_STRATEGY]
**Target Environment**: [ENVIRONMENT]

## Constitution Check

- [ ] Azure-First Architecture verified
- [ ] Infrastructure as Code — all resources in [IAC_TOOL]
- [ ] Environment isolation — naming convention applied
- [ ] Security by default — encryption, managed identities, network restrictions
- [ ] Cost-conscious — appropriate SKUs selected

## Deployment Stages

[DEPLOYMENT_STAGES]

## Deploy Status

[DEPLOY_STATUS]

## Project Structure

```text
concept/
├── infra/
│   └── [IAC_TOOL]/        # Infrastructure as Code
├── apps/                   # Application code
├── docs/                   # Generated documentation
└── .specify/               # This spec kit
    ├── constitution.md
    ├── spec.md
    ├── plan.md
    ├── tasks.md
    └── production.md
```

## Cost Estimate

[COST_ESTIMATE]
