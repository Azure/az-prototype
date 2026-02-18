# Telemetry

## Overview

The **az prototype** Azure CLI extension collects limited diagnostic
telemetry to improve reliability, performance, and service capacity
planning.

Telemetry helps the Microsoft engineering team understand how the
extension is being used so that we can improve features, ensure
appropriate regional resource availability, and enhance overall user
experience.

Telemetry collected by this extension is used solely for product
improvement and operational purposes.


## What Data Is Collected

When a command in az prototype is executed, the following information
may be collected:

| Field | Description | Why It Is Collected |
| ----- | ----------- | ------------------- |
| `tenantId` | Microsoft Entra ID tenant GUID | To understand tenant-level adoption and support enterprise engagement scenarios |
| `projectId` | Random GUID assigned to the project at init time | To correlate commands within the same prototype project |
| `commandName` | The az prototype command executed | To measure feature usage |
| `parameters` | Sanitized command parameters (JSON) | To understand usage patterns and aid troubleshooting |
| `provider` | The AI provider that was used. | To understand provider usage |
| `model` | The AI model that was used | To understand which models are being used for prototyping in order to improve our prompts |
| `resourceType` | Azure resource type (e.g., Microsoft.Compute/virtualMachines) | To understand demand for specific services |
| `location` | Azure region (e.g., WestUS3) | To support regional capacity planning |
| `sku` | Resource SKU (e.g., Standard_DSv2) | To assess SKU demand and availability |
| `extensionVersion` | Installed extension version | To track version adoption and compatibility |
| `success` | Whether the command succeeded or failed | To improve reliability |
| `error` | Error type and message when a command fails (truncated to 1 KB) | To diagnose failures and improve reliability |
| `timestamp` | Time of command execution | To analyze usage trends |

The `parameters` field records which flags and options were used (e.g., `--dry-run`,
`--scope infra`). Sensitive parameter values (`subscription`, `token`, `api_key`,
`password`, `secret`, `key`, `connection_string`) are redacted to `***` before
transmission. Non-serializable values (objects, functions) are replaced with their
type name. Parameters prefixed with `_` are excluded entirely.

**NOTE:** IP addresses are _sent_ to App Insights and mapped to region/country for the the purpose of understanding where the extension is being used. However, IP addresses are not stored.

## What Is Not Collected

The extension does **not** collect:

- Subscription IDs
- Resource names
- Resource tags
- User principal names (UPNs)
- Email addresses
- GitHub IDs
- Object IDs
- Customer content or configuration data

Only limited service metadata necessary for product improvement is collected.

## How Telemetry Is Used

Telemetry is used to:

- Improve reliability and performance
- Inform feature development and roadmap planning
- Support capacity planning for Azure services and regions
- Improve Microsoft support and enterprise engagement
- Identify service health or deployment trends

Telemetry is **not used for advertising or marketing purposes**.

## Telemetry Opt-Out

The az prototype extension honors the Azure CLI telemetry configuration.

If Azure CLI telemetry collection is disabled, az prototype will not collect or transmit telemetry data.

To disable Azure CLI telemetry, run:

    az config set core.collect_telemetry=no

To check your current telemetry setting:

    az config get core.collect_telemetry

For more information about Azure CLI telemetry, see the official Azure CLI documentation.

## Data Handling

- Telemetry is transmitted securely to Microsoft-controlled systems.
- Access to telemetry data is restricted to authorized Microsoft personnel.
- Data is handled in accordance with the Microsoft Privacy Statement and internal data governance policies.
- Aggregated reporting may be used for product and service planning.

For more information about Microsoft's privacy practices, see the Microsoft Privacy Statement:

https://privacy.microsoft.com/privacystatement

## Changes to This Document

If the scope of telemetry collection changes in a material way, this document will be updated accordingly.
