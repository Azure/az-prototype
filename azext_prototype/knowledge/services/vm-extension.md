---
service_namespace: Microsoft.Compute/virtualMachines/extensions
display_name: Virtual Machine Extension
depends_on:
  - Microsoft.Compute/virtualMachines
---

# Virtual Machine Extension

> A post-deployment configuration agent that runs scripts, installs software, or configures settings on an Azure Virtual Machine. Common extensions include Custom Script, Azure Monitor Agent, and Dependency Agent.

## When to Use
- Run custom scripts after VM provisioning (bootstrap applications, configure OS settings)
- Install Azure Monitor Agent for metrics and log collection
- Install dependency agent for service map and network monitoring
- Configure anti-malware, disk encryption, or DSC (Desired State Configuration)
- Join VMs to Active Directory domains

## POC Defaults
- **Custom Script Extension**: For bootstrapping application installation
- **Auto-upgrade minor version**: true
- **Publisher**: Varies by extension type (Microsoft.Compute, Microsoft.Azure.Monitor, etc.)

## Terraform Patterns

### Basic Resource
```hcl
# Custom Script Extension (Linux)
resource "azapi_resource" "vm_custom_script" {
  type      = "Microsoft.Compute/virtualMachines/extensions@2024-07-01"
  name      = "CustomScript"
  parent_id = azapi_resource.vm.id
  location  = var.location

  body = {
    properties = {
      publisher               = "Microsoft.Azure.Extensions"
      type                    = "CustomScript"
      typeHandlerVersion      = "2.1"
      autoUpgradeMinorVersion = true
      settings = {
        fileUris = [var.script_uri]
      }
      protectedSettings = {
        commandToExecute = "bash install.sh"
      }
    }
  }
}

# Azure Monitor Agent (Linux)
resource "azapi_resource" "vm_ama" {
  type      = "Microsoft.Compute/virtualMachines/extensions@2024-07-01"
  name      = "AzureMonitorLinuxAgent"
  parent_id = azapi_resource.vm.id
  location  = var.location

  body = {
    properties = {
      publisher               = "Microsoft.Azure.Monitor"
      type                    = "AzureMonitorLinuxAgent"
      typeHandlerVersion      = "1.0"
      autoUpgradeMinorVersion = true
      enableAutomaticUpgrade  = true
    }
  }
}
```

### RBAC Assignment
```hcl
# VM Contributor role on the VM allows extension management.
# The VM's managed identity may need additional roles for the extension's actions.
```

## Bicep Patterns

### Basic Resource
```bicep
param location string
param scriptUri string

// Custom Script Extension (Linux)
resource customScript 'Microsoft.Compute/virtualMachines/extensions@2024-07-01' = {
  parent: vm
  name: 'CustomScript'
  location: location
  properties: {
    publisher: 'Microsoft.Azure.Extensions'
    type: 'CustomScript'
    typeHandlerVersion: '2.1'
    autoUpgradeMinorVersion: true
    settings: {
      fileUris: [scriptUri]
    }
    protectedSettings: {
      commandToExecute: 'bash install.sh'
    }
  }
}

// Azure Monitor Agent
resource ama 'Microsoft.Compute/virtualMachines/extensions@2024-07-01' = {
  parent: vm
  name: 'AzureMonitorLinuxAgent'
  location: location
  properties: {
    publisher: 'Microsoft.Azure.Monitor'
    type: 'AzureMonitorLinuxAgent'
    typeHandlerVersion: '1.0'
    autoUpgradeMinorVersion: true
    enableAutomaticUpgrade: true
  }
}
```

## Application Code

### Python
Infrastructure — transparent to application code

### C#
Infrastructure — transparent to application code

### Node.js
Infrastructure — transparent to application code

## Common Pitfalls
- **Only one extension of each type**: A VM can have only one instance of each extension type. Deploying a second Custom Script Extension replaces the first.
- **Linux vs Windows publishers differ**: Custom Script Extension uses `Microsoft.Azure.Extensions` (Linux) or `Microsoft.Compute` (Windows). Using the wrong publisher fails with cryptic errors.
- **protectedSettings for secrets**: Never put secrets in `settings` — they're visible in the VM's instance view. Use `protectedSettings` for scripts, passwords, and SAS tokens.
- **Script URI accessibility**: File URIs must be publicly accessible or use SAS tokens. Private storage URIs without SAS cause silent download failures.
- **Extension ordering**: Extensions run in parallel by default. Use `dependsOn` in IaC to enforce ordering when one extension depends on another.
- **Timeout**: Custom Script Extension has a 90-minute timeout. Long-running scripts may need to be daemonized and return immediately.

## Production Backlog Items
- Azure Monitor Agent with data collection rules for observability
- Disk encryption extension (Azure Disk Encryption) for data-at-rest
- DSC extension for configuration drift prevention
- Dependency agent for service map visualization
- Extension health monitoring and auto-remediation
