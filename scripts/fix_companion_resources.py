#!/usr/bin/env python3
"""Fix companion_resources entries in policy YAML files using regex.

Two issues:
1. String entries like `- "Microsoft.Foo/Bar (context)"` -> proper dicts
2. Dict entries with type+description but missing `name` -> insert name

Uses regex-based line manipulation to preserve exact YAML formatting
(block scalars, indentation, comments).
"""

import re
import sys
from pathlib import Path

import yaml

# --- API version lookup ---
API_VERSIONS = {
    "Microsoft.Insights/diagnosticSettings": "2021-05-01-preview",
    "Microsoft.Insights/autoscaleSettings": "2022-10-01",
    "Microsoft.Insights/metricAlerts": "2018-03-01",
    "Microsoft.Insights/scheduledQueryRules": "2023-03-15-preview",
    "Microsoft.Insights/activityLogAlerts": "2020-10-01",
    "Microsoft.Insights/actionGroups": "2023-01-01",
    "Microsoft.Insights/dataCollectionRules": "2023-03-11",
    "Microsoft.Authorization/roleAssignments": "2022-04-01",
    "Microsoft.Network/privateDnsZones": "2020-06-01",
    "Microsoft.Network/privateDnsZones/virtualNetworkLinks": "2020-06-01",
    "Microsoft.Network/privateEndpoints": "2023-04-01",
    "Microsoft.Network/privateEndpoints/privateDnsZoneGroups": "2023-04-01",
    "Microsoft.Network/publicIPAddresses": "2023-04-01",
    "Microsoft.Network/virtualNetworks": "2024-01-01",
    "Microsoft.Network/virtualNetworks/subnets": "2024-01-01",
    "Microsoft.Network/networkSecurityGroups": "2023-04-01",
    "Microsoft.Network/networkInterfaces": "2023-04-01",
    "Microsoft.Network/loadBalancers": "2023-04-01",
    "Microsoft.Network/loadBalancers/outboundRules": "2023-04-01",
    "Microsoft.Network/loadBalancers/probes": "2023-04-01",
    "Microsoft.Network/natGateways": "2023-04-01",
    "Microsoft.Network/applicationGateways": "2024-01-01",
    "Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies": "2024-01-01",
    "Microsoft.Network/bastionHosts": "2023-04-01",
    "Microsoft.Network/ddosProtectionPlans": "2023-04-01",
    "Microsoft.Network/azureFirewalls": "2023-04-01",
    "Microsoft.Network/routeTables": "2023-04-01",
    "Microsoft.Network/routeTables/routes": "2023-04-01",
    "Microsoft.Network/virtualNetworkGateways": "2023-04-01",
    "Microsoft.Network/localNetworkGateways": "2023-04-01",
    "Microsoft.Network/connections": "2023-04-01",
    "Microsoft.Network/trafficManagerProfiles": "2022-04-01",
    "Microsoft.Network/trafficManagerProfiles/azureEndpoints": "2022-04-01",
    "Microsoft.Network/trafficManagerProfiles/externalEndpoints": "2022-04-01",
    "Microsoft.Network/expressRouteCircuits": "2023-04-01",
    "Microsoft.Network/expressRouteCircuits/peerings": "2023-04-01",
    "Microsoft.Network/privateLinkServices": "2023-11-01",
    "Microsoft.KeyVault/vaults": "2023-07-01",
    "Microsoft.KeyVault/vaults/keys": "2023-07-01",
    "Microsoft.ManagedIdentity/userAssignedIdentities": "2023-01-31",
    "Microsoft.OperationalInsights/workspaces": "2023-09-01",
    "Microsoft.Storage/storageAccounts": "2023-01-01",
    "Microsoft.Compute/diskEncryptionSets": "2024-03-01",
    "Microsoft.Compute/virtualMachines": "2024-03-01",
    "Microsoft.Compute/virtualMachineScaleSets": "2024-03-01",
    "Microsoft.Compute/snapshots": "2024-03-01",
    "Microsoft.Web/sites": "2023-12-01",
    "Microsoft.Web/serverfarms": "2023-12-01",
    "Microsoft.RecoveryServices/vaults": "2024-04-01",
    "Microsoft.RecoveryServices/vaults/backupPolicies": "2024-04-01",
    "Microsoft.RecoveryServices/vaults/backupFabrics/protectionContainers/protectedItems": "2024-04-01",
    "Microsoft.DataProtection/backupVaults": "2024-04-01",
    "Microsoft.DataProtection/backupVaults/backupPolicies": "2024-04-01",
    "Microsoft.DataProtection/backupVaults/backupInstances": "2024-04-01",
    "Microsoft.Consumption/budgets": "2023-05-01",
    "Microsoft.Cdn/profiles": "2024-02-01",
    "Microsoft.Cdn/profiles/securityPolicies": "2024-02-01",
    "Microsoft.Cdn/profiles/afdEndpoints": "2024-02-01",
    "Microsoft.Cdn/profiles/originGroups": "2024-02-01",
    "Microsoft.ContainerService/managedClusters": "2024-03-02-preview",
    "Microsoft.App/managedEnvironments": "2024-03-01",
    "Microsoft.App/containerApps": "2024-03-01",
    "Microsoft.DocumentDB/databaseAccounts": "2024-05-15",
    "Microsoft.Sql/servers": "2023-08-01-preview",
    "Microsoft.Sql/servers/databases": "2023-08-01-preview",
    "Microsoft.Sql/servers/failoverGroups": "2023-08-01-preview",
    "Microsoft.DBforPostgreSQL/flexibleServers": "2024-08-01",
    "Microsoft.Cache/redis": "2024-03-01",
    "Microsoft.ServiceBus/namespaces": "2024-01-01",
    "Microsoft.EventHub/namespaces": "2024-01-01",
    "Microsoft.SignalRService/signalR": "2024-03-01",
    "Microsoft.Devices/IotHubs": "2023-06-30",
    "Microsoft.ContainerRegistry/registries": "2023-07-01",
    "Microsoft.ContainerRegistry/registries/replications": "2023-07-01",
    "Microsoft.CognitiveServices/accounts": "2024-10-01",
}


def get_api_version(resource_type: str) -> str:
    """Lookup API version for a resource type."""
    return API_VERSIONS.get(resource_type, "2023-01-01")


def derive_name_from_type_and_desc(resource_type: str, description: str, rule_id: str = "") -> str:
    """Derive an appropriate name based on resource type and description context."""
    desc_lower = description.lower()

    # Diagnostic settings: diag-{service}
    if "diagnosticSettings" in resource_type:
        return _diag_name(desc_lower, rule_id)

    # Role assignments: role name
    if "roleAssignments" in resource_type:
        return _role_name(desc_lower)

    # Private DNS zones: FQDN
    if "privateDnsZones" in resource_type and "virtualNetworkLinks" not in resource_type:
        return _dns_zone_name(desc_lower)

    # Private DNS zone VNet links
    if "virtualNetworkLinks" in resource_type:
        return "link-vnet"

    # Private endpoints
    if "privateEndpoints" in resource_type and "privateDnsZoneGroups" in resource_type:
        return "default"
    if "privateEndpoints" in resource_type:
        return _pe_name(desc_lower, rule_id)

    # Public IPs
    if "publicIPAddresses" in resource_type:
        return _pip_name(desc_lower, rule_id)

    # Subnets
    if "subnets" in resource_type:
        return _subnet_name(desc_lower, rule_id)

    # NSGs
    if "networkSecurityGroups" in resource_type:
        return _nsg_name(desc_lower, rule_id)

    # Load balancers
    if "loadBalancers" in resource_type:
        if "outboundRules" in resource_type:
            return "outbound-rule"
        if "probes" in resource_type:
            return "health-probe"
        return _lb_name(desc_lower)

    # NAT gateways
    if "natGateways" in resource_type:
        return "nat-gw"

    # ExpressRoute
    if "expressRouteCircuits" in resource_type:
        if "peerings" in resource_type:
            return "private-peering"
        return "erc"

    # VNet gateways
    if "virtualNetworkGateways" in resource_type:
        if "expressroute" in desc_lower:
            return "ergw"
        return "vpngw"

    # Local network gateways
    if "localNetworkGateways" in resource_type:
        return "lgw-onprem"

    # Connections
    if resource_type == "Microsoft.Network/connections":
        if "expressroute" in desc_lower:
            return "erc-connection"
        return "s2s-connection"

    # Traffic Manager
    if "trafficManagerProfiles" in resource_type:
        if "azureEndpoints" in resource_type:
            return "ep-azure"
        if "externalEndpoints" in resource_type:
            return "ep-external"
        if "child" in desc_lower or "nested" in desc_lower:
            return "tm-child"
        return "tm-profile"

    # Network interfaces
    if "networkInterfaces" in resource_type:
        return "nic-vm"

    # Key Vaults
    if "KeyVault/vaults" in resource_type and "keys" not in resource_type:
        if "ssl" in desc_lower or "cert" in desc_lower:
            return "kv-certs"
        if "cmk" in desc_lower or "encrypt" in desc_lower:
            return "kv-cmk"
        return "kv-secrets"

    if "KeyVault/vaults/keys" in resource_type:
        return "encryption-key"

    # Log Analytics workspaces
    if "OperationalInsights/workspaces" in resource_type:
        return "log-analytics"

    # Managed identity
    if "userAssignedIdentities" in resource_type:
        return f"id-{_rule_short(rule_id)}"

    # Storage accounts
    if "Storage/storageAccounts" in resource_type:
        if "dead-letter" in desc_lower or "deadletter" in desc_lower:
            return "st-deadletter"
        if "checkpoint" in desc_lower:
            return "st-checkpoint"
        if "adls" in desc_lower or "data lake" in desc_lower:
            return "st-datalake"
        return "st-data"

    # DDoS protection
    if "ddosProtectionPlans" in resource_type:
        return "ddos-plan"

    # Bastion
    if "bastionHosts" in resource_type:
        return "bas-mgmt"

    # Application Gateway
    if "applicationGateways" in resource_type:
        return "agw"

    # WAF policy
    if "WebApplicationFirewallPolicies" in resource_type:
        return "waf-policy"

    # Route tables
    if "routeTables" in resource_type:
        if "/routes" in resource_type:
            return "default-route"
        return "rt-default"

    # Recovery Services
    if "RecoveryServices/vaults" in resource_type:
        if "backupPolicies" in resource_type:
            return "backup-policy"
        if "protectedItems" in resource_type:
            return "protected-item"
        return "recovery-vault"

    # Backup Vault
    if "DataProtection/backupVaults" in resource_type:
        if "backupPolicies" in resource_type:
            return "backup-policy"
        if "backupInstances" in resource_type:
            return "backup-instance"
        return "backup-vault"

    # Budgets
    if "Consumption/budgets" in resource_type:
        return "budget"

    # Action groups
    if "actionGroups" in resource_type:
        return "ag-ops"

    # CDN / Front Door
    if "Cdn/profiles" in resource_type:
        if "securityPolicies" in resource_type:
            return "waf-security-policy"
        if "afdEndpoints" in resource_type:
            return "afd-endpoint"
        if "originGroups" in resource_type:
            return "origin-group"
        return "fd-profile"

    # Private Link services
    if "privateLinkServices" in resource_type:
        return "pls-origin"

    # Container services
    if "ContainerService/managedClusters" in resource_type:
        return "aks-cluster"
    if "App/managedEnvironments" in resource_type:
        return "cae"
    if "App/containerApps" in resource_type:
        return "ca-app"

    # Database services
    if "DocumentDB/databaseAccounts" in resource_type:
        return "cosmos-account"
    if "Sql/servers" in resource_type:
        if "failoverGroups" in resource_type:
            return "sql-failover"
        if "databases" in resource_type:
            return "sql-db"
        return "sql-server"
    if "DBforPostgreSQL" in resource_type:
        return "pg-server"
    if "Cache/redis" in resource_type:
        return "redis-cache"
    if "ServiceBus/namespaces" in resource_type:
        return "sb-namespace"
    if "EventHub/namespaces" in resource_type:
        return "eh-namespace"
    if "SignalRService" in resource_type:
        return "signalr"
    if "Devices/IotHubs" in resource_type:
        return "iot-hub"

    # Container Registry
    if "ContainerRegistry/registries" in resource_type:
        if "replications" in resource_type:
            return "acr-replication"
        return "acr"

    # Cognitive Services
    if "CognitiveServices" in resource_type:
        return "cognitive-svc"

    # Compute
    if "Compute/diskEncryptionSets" in resource_type:
        return "des-cmk"
    if "Compute/virtualMachines" in resource_type:
        return "vm"
    if "Compute/virtualMachineScaleSets" in resource_type:
        return "vmss"
    if "Compute/snapshots" in resource_type:
        return "snapshot"

    # Web
    if "Web/sites" in resource_type:
        return "app"
    if "Web/serverfarms" in resource_type:
        return "asp"

    # Autoscale
    if "autoscaleSettings" in resource_type:
        return "autoscale"

    # Metric alerts
    if "metricAlerts" in resource_type:
        return "alert-metric"

    # Fallback: use last segment of type
    last = resource_type.rsplit("/", 1)[-1]
    return last[:20].lower()


def _rule_short(rule_id: str) -> str:
    """Extract short form from rule ID."""
    prefix_map = {
        "AZ-AGW": "agw", "AZ-BAS": "bastion", "AZ-DNS": "dns",
        "AZ-ER": "expressroute", "AZ-LB": "lb", "AZ-NAT": "nat",
        "AZ-NIC": "nic", "AZ-PIP": "pip", "AZ-UDR": "udr",
        "AZ-TM": "traffic-mgr", "AZ-VPN": "vpn",
        "WAF-REL": "rel", "WAF-COST": "cost", "WAF-PERF": "perf",
    }
    for prefix, short in prefix_map.items():
        if rule_id.startswith(prefix):
            return short
    return "resource"


def _diag_name(desc: str, rule_id: str) -> str:
    service_map = {
        "expressroute": "diag-expressroute", "load balancer": "diag-lb",
        "nat gateway": "diag-nat", "public ip": "diag-pip",
        "traffic manager": "diag-traffic-mgr", "vpn gateway": "diag-vpn",
        "bastion": "diag-bastion", "application gateway": "diag-agw",
        "front door": "diag-frontdoor", "aks": "diag-aks",
        "container app": "diag-ca", "cosmos": "diag-cosmos",
        "sql": "diag-sql", "postgresql": "diag-postgresql",
        "redis": "diag-redis", "service bus": "diag-servicebus",
        "event hub": "diag-eventhubs", "iot hub": "diag-iothub",
        "key vault": "diag-keyvault", "storage": "diag-storage",
        "app service": "diag-app-service", "function": "diag-function",
        "container registry": "diag-acr", "signalr": "diag-signalr",
        "firewall": "diag-firewall", "metric": "diag-metrics",
        "route": "diag-udr", "vpn": "diag-vpn", "nat": "diag-nat",
    }
    for keyword, name in service_map.items():
        if keyword in desc:
            return name
    return f"diag-{_rule_short(rule_id)}"


def _role_name(desc: str) -> str:
    if "network contributor" in desc:
        return "Network Contributor"
    if "data sender" in desc and "receiver" in desc:
        return "Data Sender/Receiver"
    if "data sender" in desc:
        return "Data Sender"
    if "data reader" in desc:
        return "Data Reader"
    if "backup contributor" in desc:
        return "Backup Contributor"
    if "blob data contributor" in desc:
        return "Storage Blob Data Contributor"
    if "crypto service" in desc:
        return "Key Vault Crypto Service Encryption User"
    if "crypto" in desc:
        return "Key Vault Crypto User"
    if "secrets user" in desc:
        return "Key Vault Secrets User"
    if "monitoring reader" in desc:
        return "Monitoring Reader"
    if "monitoring contributor" in desc:
        return "Monitoring Contributor"
    if "contributor" in desc:
        return "Contributor"
    if "owner" in desc:
        return "Owner"
    if "reader" in desc:
        return "Reader"
    return "role-assignment"


def _dns_zone_name(desc: str) -> str:
    m = re.search(r"(privatelink\.[a-z0-9._-]+)", desc)
    if m:
        return m.group(1)
    # Known patterns
    if "expressroute" in desc:
        return "privatelink.azure.com"
    if "traffic manager" in desc:
        return "privatelink.trafficmanager.net"
    return "privatelink.service.azure.com"


def _pe_name(desc: str, rule_id: str) -> str:
    service_map = {
        "expressroute": "pe-expressroute", "load balancer": "pe-lb",
        "nat gateway": "pe-nat", "vpn": "pe-vpn",
        "front door": "pe-frontdoor", "public endpoint": "pe-service",
    }
    for keyword, name in service_map.items():
        if keyword in desc:
            return name
    return f"pe-{_rule_short(rule_id)}"


def _pip_name(desc: str, rule_id: str) -> str:
    if "agw" in desc or "application gateway" in desc:
        return "pip-agw"
    if "bastion" in desc:
        return "pip-bastion"
    if "expressroute" in desc:
        return "pip-ergw"
    if "vpn" in desc:
        return "pip-vpngw"
    if "gateway" in desc:
        return "pip-gw"
    if "load balancer" in desc or "lb" in desc:
        return "pip-lb"
    if "outbound" in desc:
        return "pip-outbound"
    if "nat" in desc or "AZ-NAT" in rule_id:
        return "pip-nat"
    return f"pip-{_rule_short(rule_id)}"


def _subnet_name(desc: str, rule_id: str) -> str:
    if "azurebastionsubnet" in desc:
        return "AzureBastionSubnet"
    if "gatewaysubnet" in desc:
        return "GatewaySubnet"
    if "agw" in desc or "application gateway" in desc:
        return "snet-agw"
    if "nat" in desc or "AZ-NAT" in rule_id:
        return "snet-nat"
    if "route" in desc or "AZ-UDR" in rule_id:
        return "snet-workload"
    return f"snet-{_rule_short(rule_id)}"


def _nsg_name(desc: str, rule_id: str) -> str:
    if "bastion" in desc:
        return "nsg-bastion"
    if "inbound" in desc or "security" in desc:
        return f"nsg-{_rule_short(rule_id)}"
    return f"nsg-{_rule_short(rule_id)}"


def _lb_name(desc: str) -> str:
    if "internal" in desc:
        return "lb-internal"
    if "standard" in desc:
        return "lb-standard"
    return "lb"


# ============================================================
# Main processing: string conversion via regex
# ============================================================

# Pattern to match a string companion_resource line
# e.g., "  - Microsoft.Foo/Bar (some description)"
# or    "  - 'Microsoft.Foo/Bar (some description)'"
STRING_ENTRY_RE = re.compile(
    r"^(\s+)-\s+['\"]?(Microsoft\.[A-Za-z0-9/]+)(?:\s+or\s+[A-Za-z0-9./]+)?\s*\((.+?)\)['\"]?\s*$"
)

# Pattern to match a dict entry with type but no name
# type line, then description line — we need to look at two consecutive lines
# Handles both `  - type: X` (list item start) and `    type: X` (continuation)
TYPE_LINE_RE = re.compile(r"^(\s+)(?:-\s+)?type:\s+(Microsoft\.\S+@\S+)\s*$")
NAME_LINE_RE = re.compile(r"^(\s+)name:\s+")
DESC_LINE_RE = re.compile(r"^(\s+)description:\s+(.+)$")


def find_rule_id_for_line(lines: list, line_idx: int) -> str:
    """Walk backward to find the rule ID for context."""
    for i in range(line_idx, max(0, line_idx - 200), -1):
        m = re.match(r"\s*id:\s+(\S+)", lines[i])
        if m:
            return m.group(1)
    return ""


def fix_string_entries(content: str) -> tuple:
    """Convert string companion_resource entries to dicts. Returns (new_content, count)."""
    lines = content.split("\n")
    new_lines = []
    fixes = 0

    for i, line in enumerate(lines):
        m = STRING_ENTRY_RE.match(line)
        if m:
            indent = m.group(1)
            resource_type = m.group(2)
            ctx = m.group(3).strip()
            rule_id = find_rule_id_for_line(lines, i)

            api_ver = get_api_version(resource_type)
            typed = f"{resource_type}@{api_ver}"
            name = derive_name_from_type_and_desc(resource_type, ctx, rule_id)
            desc = ctx[0].upper() + ctx[1:] if ctx else "Companion resource"

            new_lines.append(f"{indent}- type: {typed}")
            new_lines.append(f"{indent}  name: {name}")
            new_lines.append(f"{indent}  description: {desc}")
            fixes += 1
        else:
            new_lines.append(line)

    return "\n".join(new_lines), fixes


def fix_missing_names(content: str) -> tuple:
    """Insert name field into dict entries that have type+description but no name."""
    lines = content.split("\n")
    new_lines = []
    fixes = 0
    i = 0

    while i < len(lines):
        type_m = TYPE_LINE_RE.match(lines[i])
        if type_m:
            indent = type_m.group(1)
            type_val = type_m.group(2)

            # Check if the NEXT line is "name:" — if so, already has name
            if i + 1 < len(lines) and NAME_LINE_RE.match(lines[i + 1]):
                new_lines.append(lines[i])
                i += 1
                continue

            # Check if the next line is "description:" — if so, we need to insert name
            if i + 1 < len(lines):
                desc_m = DESC_LINE_RE.match(lines[i + 1])
                if desc_m:
                    desc_indent = desc_m.group(1)  # use description's indent for name
                    desc_text = desc_m.group(2).strip().strip("'\"")
                    resource_type = type_val.split("@")[0]
                    rule_id = find_rule_id_for_line(lines, i)
                    name = derive_name_from_type_and_desc(resource_type, desc_text, rule_id)

                    new_lines.append(lines[i])  # type line
                    new_lines.append(f"{desc_indent}name: {name}")  # insert name at desc indent
                    fixes += 1
                    i += 1
                    continue

        new_lines.append(lines[i])
        i += 1

    return "\n".join(new_lines), fixes


def process_file(filepath: Path, dry_run: bool = False) -> int:
    """Process a single policy YAML file. Returns count of fixes."""
    with open(filepath) as f:
        content = f.read()

    new_content, string_fixes = fix_string_entries(content)
    new_content, name_fixes = fix_missing_names(new_content)

    total_fixes = string_fixes + name_fixes

    if total_fixes > 0:
        if dry_run:
            print(f"[DRY-RUN] {filepath}: {string_fixes} strings, {name_fixes} names")
        else:
            with open(filepath, "w") as f:
                f.write(new_content)
            print(f"Fixed {filepath}: {string_fixes} strings, {name_fixes} names")

    return total_fixes


def main():
    dry_run = "--dry-run" in sys.argv

    policies_dir = Path("azext_prototype/governance/policies")
    total_fixes = 0

    for filepath in sorted(policies_dir.rglob("*.policy.yaml")):
        fixes = process_file(filepath, dry_run=dry_run)
        total_fixes += fixes

    print(f"\nTotal fixes: {total_fixes}")


if __name__ == "__main__":
    main()
