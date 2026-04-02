"""Private DNS zone lookup for Azure Private Endpoint configuration.

Maps ARM resource types to their required private DNS zone names and
subresource (group) IDs. Data sourced from:
https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-dns

Used by the build session to inject exact DNS zone names into the
networking stage task prompt, eliminating guesswork by the AI model.
"""

from __future__ import annotations

# Keyed by lowercase ARM resource type.
# Each entry is a list of dicts with "subresource" and "zone" keys.
# Multiple entries per resource type when different subresources
# require different DNS zones (e.g., Cosmos DB SQL vs MongoDB).
PRIVATE_DNS_ZONES: dict[str, list[dict[str, str]]] = {
    # --- Databases ---
    "microsoft.sql/servers": [
        {"subresource": "sqlServer", "zone": "privatelink.database.windows.net"},
    ],
    "microsoft.documentdb/databaseaccounts": [
        {"subresource": "Sql", "zone": "privatelink.documents.azure.com"},
        {"subresource": "MongoDB", "zone": "privatelink.mongo.cosmos.azure.com"},
        {"subresource": "Cassandra", "zone": "privatelink.cassandra.cosmos.azure.com"},
        {"subresource": "Gremlin", "zone": "privatelink.gremlin.cosmos.azure.com"},
        {"subresource": "Table", "zone": "privatelink.table.cosmos.azure.com"},
    ],
    "microsoft.dbforpostgresql/flexibleservers": [
        {"subresource": "postgresqlServer", "zone": "privatelink.postgres.database.azure.com"},
    ],
    "microsoft.dbforpostgresql/servers": [
        {"subresource": "postgresqlServer", "zone": "privatelink.postgres.database.azure.com"},
    ],
    "microsoft.dbformysql/flexibleservers": [
        {"subresource": "mysqlServer", "zone": "privatelink.mysql.database.azure.com"},
    ],
    "microsoft.cache/redis": [
        {"subresource": "redisCache", "zone": "privatelink.redis.cache.windows.net"},
    ],
    "microsoft.cache/redisenterprise": [
        {"subresource": "redisEnterprise", "zone": "privatelink.redisenterprise.cache.azure.net"},
    ],
    # --- Storage ---
    "microsoft.storage/storageaccounts": [
        {"subresource": "blob", "zone": "privatelink.blob.core.windows.net"},
        {"subresource": "file", "zone": "privatelink.file.core.windows.net"},
        {"subresource": "table", "zone": "privatelink.table.core.windows.net"},
        {"subresource": "queue", "zone": "privatelink.queue.core.windows.net"},
        {"subresource": "web", "zone": "privatelink.web.core.windows.net"},
        {"subresource": "dfs", "zone": "privatelink.dfs.core.windows.net"},
    ],
    # --- Security ---
    "microsoft.keyvault/vaults": [
        {"subresource": "vault", "zone": "privatelink.vaultcore.azure.net"},
    ],
    "microsoft.appconfiguration/configurationstores": [
        {"subresource": "configurationStores", "zone": "privatelink.azconfig.io"},
    ],
    # --- Web ---
    "microsoft.web/sites": [
        {"subresource": "sites", "zone": "privatelink.azurewebsites.net"},
    ],
    "microsoft.signalrservice/signalr": [
        {"subresource": "signalr", "zone": "privatelink.service.signalr.net"},
    ],
    "microsoft.signalrservice/webpubsub": [
        {"subresource": "webpubsub", "zone": "privatelink.webpubsub.azure.com"},
    ],
    "microsoft.search/searchservices": [
        {"subresource": "searchService", "zone": "privatelink.search.windows.net"},
    ],
    # --- Containers ---
    "microsoft.containerregistry/registries": [
        {"subresource": "registry", "zone": "privatelink.azurecr.io"},
    ],
    "microsoft.app/managedenvironments": [
        {"subresource": "managedEnvironments", "zone": "privatelink.{regionName}.azurecontainerapps.io"},
    ],
    # --- AI + Machine Learning ---
    "microsoft.cognitiveservices/accounts": [
        {"subresource": "account", "zone": "privatelink.cognitiveservices.azure.com"},
    ],
    # --- Analytics ---
    "microsoft.eventhub/namespaces": [
        {"subresource": "namespace", "zone": "privatelink.servicebus.windows.net"},
    ],
    "microsoft.servicebus/namespaces": [
        {"subresource": "namespace", "zone": "privatelink.servicebus.windows.net"},
    ],
    "microsoft.datafactory/factories": [
        {"subresource": "dataFactory", "zone": "privatelink.datafactory.azure.net"},
    ],
    "microsoft.eventgrid/topics": [
        {"subresource": "topic", "zone": "privatelink.eventgrid.azure.net"},
    ],
    "microsoft.eventgrid/domains": [
        {"subresource": "domain", "zone": "privatelink.eventgrid.azure.net"},
    ],
    # --- Management ---
    "microsoft.insights/privatelinkscopes": [
        {"subresource": "azuremonitor", "zone": "privatelink.monitor.azure.com"},
    ],
    "microsoft.automation/automationaccounts": [
        {"subresource": "Webhook", "zone": "privatelink.azure-automation.net"},
    ],
    # --- IoT ---
    "microsoft.devices/iothubs": [
        {"subresource": "iotHub", "zone": "privatelink.azure-devices.net"},
    ],
}


def get_dns_zones(resource_type: str) -> list[dict[str, str]]:
    """Look up private DNS zones for an ARM resource type.

    Parameters
    ----------
    resource_type:
        ARM resource type (e.g., ``"Microsoft.KeyVault/vaults"``).
        Case-insensitive.

    Returns
    -------
    list[dict]:
        List of ``{"subresource": ..., "zone": ...}`` dicts.
        Empty list if no mapping exists.
    """
    return PRIVATE_DNS_ZONES.get(resource_type.lower(), [])


def get_dns_zone(resource_type: str, subresource: str | None = None) -> str | None:
    """Look up a single private DNS zone name.

    Parameters
    ----------
    resource_type:
        ARM resource type (case-insensitive).
    subresource:
        Specific subresource/group ID. If None, returns the first zone.

    Returns
    -------
    str | None:
        The DNS zone FQDN, or None if not found.
    """
    entries = get_dns_zones(resource_type)
    if not entries:
        return None
    if subresource:
        for entry in entries:
            if entry["subresource"].lower() == subresource.lower():
                return entry["zone"]
    return entries[0]["zone"]


def get_zones_for_services(services: list[dict]) -> dict[str, str]:
    """Given deployment plan services, return all needed DNS zones.

    Parameters
    ----------
    services:
        List of service dicts from the deployment plan. Each must have
        a ``resource_type`` key (ARM type).

    Returns
    -------
    dict[str, str]:
        Mapping of DNS zone FQDN → ARM resource type that needs it.
        Deduplicated (same zone used by multiple services appears once).
    """
    zones: dict[str, str] = {}
    for svc in services:
        rt = svc.get("resource_type", "")
        if not rt:
            continue
        for entry in get_dns_zones(rt):
            zone = entry["zone"]
            if zone not in zones:
                zones[zone] = rt
    return zones
