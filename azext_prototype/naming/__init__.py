"""Resource naming convention resolver.

Supports built-in strategies (Microsoft ALZ, Microsoft CAF, simple, enterprise)
and fully custom patterns. All agents use this to ensure consistent
resource naming across infrastructure and application code.
"""

import re
from knack.util import CLIError


# Microsoft Cloud Adoption Framework abbreviations
# https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ready/azure-best-practices/resource-abbreviations
CAF_ABBREVIATIONS = {
    "resource_group": "rg",
    "storage_account": "st",
    "app_service": "app",
    "app_service_plan": "asp",
    "function_app": "func",
    "key_vault": "kv",
    "cosmos_db": "cosmos",
    "sql_server": "sql",
    "sql_database": "sqldb",
    "container_registry": "cr",
    "container_app": "ca",
    "container_app_environment": "cae",
    "log_analytics": "log",
    "application_insights": "appi",
    "api_management": "apim",
    "service_bus": "sb",
    "event_hub": "evh",
    "event_grid": "evg",
    "virtual_network": "vnet",
    "subnet": "snet",
    "network_security_group": "nsg",
    "public_ip": "pip",
    "load_balancer": "lb",
    "front_door": "fd",
    "cdn_profile": "cdnp",
    "dns_zone": "dns",
    "private_endpoint": "pe",
    "managed_identity": "id",
    "redis_cache": "redis",
    "search_service": "srch",
    "cognitive_account": "cog",
    "openai_account": "oai",
    "signalr": "sigr",
    "static_web_app": "stapp",
    "web_pubsub": "wps",
    "data_factory": "adf",
    "databricks": "dbw",
    "machine_learning": "mlw",
    "monitor_action_group": "ag",
    "monitor_alert": "al",
}

# Azure Landing Zone identifiers
ALZ_ZONE_IDS = {
    "pc": "Connectivity Platform",
    "pi": "Identity Platform",
    "pm": "Management Platform",
    "zp": "Production Zone",
    "zs": "Staging Zone",
    "zd": "Development Zone",
    "zt": "Testing Zone",
}

# Map environment names to default zone IDs
_ENV_TO_ZONE = {
    "dev": "zd",
    "development": "zd",
    "test": "zt",
    "testing": "zt",
    "qa": "zt",
    "staging": "zs",
    "stg": "zs",
    "uat": "zs",
    "prod": "zp",
    "production": "zp",
}

# Azure naming constraints per resource type
RESOURCE_CONSTRAINTS = {
    "storage_account": {"max_length": 24, "allow_hyphens": False, "lowercase": True},
    "key_vault": {"max_length": 24, "allow_hyphens": True, "lowercase": False},
    "container_registry": {"max_length": 50, "allow_hyphens": False, "lowercase": True},
    "resource_group": {"max_length": 90, "allow_hyphens": True, "lowercase": False},
    "cosmos_db": {"max_length": 44, "allow_hyphens": True, "lowercase": True},
}

# Region short codes for naming
REGION_SHORT_CODES = {
    "eastus": "eus",
    "eastus2": "eus2",
    "westus": "wus",
    "westus2": "wus2",
    "westus3": "wus3",
    "centralus": "cus",
    "northcentralus": "ncus",
    "southcentralus": "scus",
    "westcentralus": "wcus",
    "canadacentral": "cac",
    "canadaeast": "cae",
    "brazilsouth": "brs",
    "northeurope": "neu",
    "westeurope": "weu",
    "uksouth": "uks",
    "ukwest": "ukw",
    "francecentral": "frc",
    "francesouth": "frs",
    "germanywestcentral": "gwc",
    "norwayeast": "noe",
    "swedencentral": "sec",
    "switzerlandnorth": "szn",
    "australiaeast": "aue",
    "australiasoutheast": "ause",
    "eastasia": "ea",
    "southeastasia": "sea",
    "japaneast": "jpe",
    "japanwest": "jpw",
    "koreacentral": "krc",
    "koreasouth": "krs",
    "centralindia": "inc",
    "southindia": "ins",
    "westindia": "inw",
    "southafricanorth": "san",
    "uaenorth": "uan",
}


class NamingStrategy:
    """Base class for naming strategies."""

    def __init__(self, config: dict) -> None:
        self.config = config
        naming: dict = config.get("naming") or {}
        project: dict = config.get("project") or {}
        self.org: str = naming.get("org") or project.get("name") or "proto"
        self.env: str = naming.get("env") or project.get("environment") or "dev"
        self.region: str = project.get("location") or "eastus"
        self.suffix: str = naming.get("suffix") or ""
        self.instance: str = naming.get("instance") or "001"
        self.overrides: dict = naming.get("overrides") or {}

    def resolve(self, resource_type: str, service_name: str = "") -> str:
        """Resolve a resource name.

        Args:
            resource_type: The Azure resource type key (e.g., 'resource_group', 'storage_account')
            service_name: Optional service/component name (e.g., 'api', 'web', 'data')

        Returns:
            The resolved resource name, conforming to Azure naming constraints.
        """
        # Check for per-resource override first
        if resource_type in self.overrides:
            name = self._interpolate(self.overrides[resource_type], resource_type, service_name)
        else:
            name = self._build_name(resource_type, service_name)

        return self._apply_constraints(name, resource_type)

    def _build_name(self, resource_type: str, service_name: str) -> str:
        """Build name from strategy pattern. Override in subclasses."""
        raise NotImplementedError

    def _interpolate(self, pattern: str, resource_type: str, service_name: str) -> str:
        """Replace placeholders in a pattern string."""
        abbrev = CAF_ABBREVIATIONS.get(resource_type, resource_type)
        region_short = REGION_SHORT_CODES.get(self.region, self.region[:4])
        zone_id = self._resolve_zone_id()

        return pattern.format(
            org=self.org,
            env=self.env,
            region=self.region,
            region_short=region_short,
            service=service_name or "core",
            type=abbrev,
            suffix=self.suffix,
            instance=self.instance,
            zoneid=zone_id,
        )

    def _resolve_zone_id(self) -> str:
        """Resolve the ALZ zone ID from config or environment."""
        naming = self.config.get("naming", {})
        # Explicit zone_id takes priority
        zone_id = naming.get("zone_id", "")
        if zone_id and zone_id in ALZ_ZONE_IDS:
            return zone_id
        # Fall back to environment mapping
        return _ENV_TO_ZONE.get(self.env.lower(), "zd")

    def _apply_constraints(self, name: str, resource_type: str) -> str:
        """Apply Azure naming constraints for a resource type."""
        constraints = RESOURCE_CONSTRAINTS.get(resource_type, {})

        if constraints.get("lowercase", False):
            name = name.lower()

        if not constraints.get("allow_hyphens", True):
            name = name.replace("-", "")

        # Remove any characters that aren't alphanumeric or hyphens
        name = re.sub(r"[^a-zA-Z0-9\\-]", "", name)

        max_len = constraints.get("max_length", 63)
        if len(name) > max_len:
            name = name[:max_len]

        return name

    def to_prompt_instructions(self) -> str:
        """Generate naming instructions for agent system prompts."""
        examples = []
        for rtype in ["resource_group", "storage_account", "app_service", "key_vault", "cosmos_db"]:
            examples.append(f"  - {rtype}: {self.resolve(rtype, 'api')}")

        return (
            f"NAMING CONVENTIONS:\n"
            f"  Strategy: {self.__class__.__name__}\n"
            f"  Organization: {self.org}\n"
            f"  Environment: {self.env}\n"
            f"  Region: {self.region}\n"
            f"\n"
            f"  Examples:\n" + "\n".join(examples) + "\n"
            "\n"
            "  IMPORTANT: Use these EXACT naming patterns for all resources.\n"
            "  Do NOT invent your own naming scheme.\n"
        )


class MicrosoftALZStrategy(NamingStrategy):
    """Microsoft Azure Landing Zone naming.

    Pattern: {zoneid}-{abbreviation}-{service}-{env}-{region_short}
    Example: zd-rg-api-dev-eus

    Zone IDs:
      pc — Connectivity Platform
      pi — Identity Platform
      pm — Management Platform
      zp — Production Zone
      zs — Staging Zone
      zd — Development Zone (default)
      zt — Testing Zone

    Reference: https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ready/landing-zone/
    """

    def _build_name(self, resource_type: str, service_name: str) -> str:
        abbrev = CAF_ABBREVIATIONS.get(resource_type, resource_type[:4])
        region_short = REGION_SHORT_CODES.get(self.region, self.region[:4])
        zone_id = self._resolve_zone_id()

        parts = [zone_id, abbrev]
        if service_name:
            parts.append(service_name)
        parts.extend([self.env, region_short])

        return "-".join(parts)

    def to_prompt_instructions(self) -> str:
        """Generate ALZ-specific naming instructions with zone ID context."""
        zone_id = self._resolve_zone_id()
        zone_label = ALZ_ZONE_IDS.get(zone_id, "Unknown")

        examples = []
        for rtype in ["resource_group", "storage_account", "app_service", "key_vault", "cosmos_db"]:
            examples.append(f"  - {rtype}: {self.resolve(rtype, 'api')}")

        zone_table = "\n".join(f"    {k} — {v}" for k, v in ALZ_ZONE_IDS.items())

        return (
            f"NAMING CONVENTIONS (Azure Landing Zone):\n"
            f"  Strategy: microsoft-alz\n"
            f"  Pattern: {{zoneid}}-{{type}}-{{service}}-{{env}}-{{region_short}}\n"
            f"  Organization: {self.org}\n"
            f"  Environment: {self.env}\n"
            f"  Region: {self.region}\n"
            f"  Active Zone: {zone_id} ({zone_label})\n"
            f"\n"
            f"  Zone IDs:\n{zone_table}\n"
            f"\n"
            f"  Examples (zone={zone_id}):\n" + "\n".join(examples) + "\n"
            "\n"
            "  PLATFORM resources (networking, identity, monitoring) use pc/pi/pm zones.\n"
            "  APPLICATION resources use zd/zt/zs/zp based on environment.\n"
            "\n"
            "  IMPORTANT: Use these EXACT naming patterns for all resources.\n"
            "  Do NOT invent your own naming scheme.\n"
        )


class MicrosoftCAFStrategy(NamingStrategy):
    """Microsoft Cloud Adoption Framework naming.

    Pattern: {abbreviation}-{org}-{service}-{env}-{region_short}-{instance}
    Example: rg-contoso-api-dev-eus-001
    Reference: https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ready/azure-best-practices/resource-naming
    """

    def _build_name(self, resource_type: str, service_name: str) -> str:
        abbrev = CAF_ABBREVIATIONS.get(resource_type, resource_type[:4])
        region_short = REGION_SHORT_CODES.get(self.region, self.region[:4])

        parts = [abbrev, self.org]
        if service_name:
            parts.append(service_name)
        parts.extend([self.env, region_short])
        if self.instance:
            parts.append(self.instance)

        return "-".join(parts)


class SimpleStrategy(NamingStrategy):
    """Simple naming for quick prototypes.

    Pattern: {org}-{service}-{type}-{env}
    Example: contoso-api-rg-dev
    """

    def _build_name(self, resource_type: str, service_name: str) -> str:
        abbrev = CAF_ABBREVIATIONS.get(resource_type, resource_type[:4])

        parts = [self.org]
        if service_name:
            parts.append(service_name)
        parts.extend([abbrev, self.env])

        return "-".join(parts)


class EnterpriseStrategy(NamingStrategy):
    """Enterprise naming with business unit and instance.

    Pattern: {abbreviation}-{bu}-{org}-{service}-{env}-{region_short}-{instance}
    Example: rg-it-contoso-api-dev-eus-001
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.business_unit = config.get("naming", {}).get("business_unit", "eng")

    def _build_name(self, resource_type: str, service_name: str) -> str:
        abbrev = CAF_ABBREVIATIONS.get(resource_type, resource_type[:4])
        region_short = REGION_SHORT_CODES.get(self.region, self.region[:4])

        parts = [abbrev, self.business_unit, self.org]
        if service_name:
            parts.append(service_name)
        parts.extend([self.env, region_short, self.instance])

        return "-".join(parts)


class CustomStrategy(NamingStrategy):
    """Fully custom naming using a user-defined pattern.

    Pattern is read from naming.pattern in config.
    Example pattern: "{org}-{type}-{service}-{env}-{region_short}"
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.pattern = config.get("naming", {}).get(
            "pattern", "{type}-{org}-{service}-{env}-{region_short}"
        )

    def _build_name(self, resource_type: str, service_name: str) -> str:
        return self._interpolate(self.pattern, resource_type, service_name)


# Strategy registry — microsoft-alz is first and default
_STRATEGIES = {
    "microsoft-alz": MicrosoftALZStrategy,
    "microsoft-caf": MicrosoftCAFStrategy,
    "simple": SimpleStrategy,
    "enterprise": EnterpriseStrategy,
    "custom": CustomStrategy,
}

DEFAULT_STRATEGY = "microsoft-alz"


def create_naming_strategy(config: dict) -> NamingStrategy:
    """Create a naming strategy from project configuration.

    Args:
        config: The full prototype.yaml config dict.

    Returns:
        A configured NamingStrategy instance.

    Raises:
        CLIError: If the strategy name is not recognized.
    """
    strategy_name = config.get("naming", {}).get("strategy", DEFAULT_STRATEGY)

    strategy_class = _STRATEGIES.get(strategy_name)
    if strategy_class is None:
        raise CLIError(
            f"Unknown naming strategy '{strategy_name}'.\n"
            f"Available strategies: {', '.join(_STRATEGIES.keys())}"
        )

    return strategy_class(config)


def get_available_strategies() -> list:
    """Return list of available strategy names."""
    return list(_STRATEGIES.keys())


def get_zone_ids() -> dict:
    """Return the ALZ zone ID mapping."""
    return dict(ALZ_ZONE_IDS)
