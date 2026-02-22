"""Project configuration management."""

import logging
import re
import uuid
from pathlib import Path
from typing import Any

import yaml
from knack.util import CLIError

logger = logging.getLogger(__name__)


def _sanitize_for_yaml(data: Any) -> Any:
    """Recursively convert values to plain Python types for safe YAML.

    Azure CLI wraps parameter defaults in ``knack.validators.DefaultStr``
    (a *str* subclass).  ``yaml.dump`` serialises these with Python-specific
    type tags that ``yaml.safe_load`` cannot deserialise.  This helper
    strips any such wrapper types by coercing to the corresponding
    built-in type.
    """
    if isinstance(data, dict):
        return {str(k): _sanitize_for_yaml(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_sanitize_for_yaml(item) for item in data]
    # Order matters: bool before int (bool is an int subclass)
    if isinstance(data, bool):
        return bool(data)
    if isinstance(data, int):
        return int(data)
    if isinstance(data, float):
        return float(data)
    if isinstance(data, str):
        return str(data)
    return data


class _RepairLoader(yaml.SafeLoader):
    """SafeLoader extended to handle legacy knack DefaultStr tags.

    ``yaml.dump`` serialises ``knack.validators.DefaultStr`` as::

        !!python/object/new:knack.validators.DefaultStr
          args:
          - github-models
          state:
            is_default: true

    This loader maps that tag to a plain Python ``str`` by extracting the
    first element of ``args``.
    """


def _construct_default_str(loader: yaml.Loader, node: yaml.Node) -> str:
    """Extract the plain string value from a DefaultStr YAML node."""
    if isinstance(node, yaml.ScalarNode):
        return str(loader.construct_scalar(node))
    if isinstance(node, yaml.MappingNode):
        mapping = loader.construct_mapping(node, deep=True)
        args = mapping.get("args", [])
        return str(args[0]) if args else ""
    if isinstance(node, yaml.SequenceNode):
        items = loader.construct_sequence(node)
        return str(items[0]) if items else ""
    return ""


_RepairLoader.add_constructor(
    "tag:yaml.org,2002:python/object/new:knack.validators.DefaultStr",
    _construct_default_str,
)


def _safe_load_yaml(stream: Any) -> dict | None:
    """Load YAML with a fallback for corrupted files.

    Earlier versions of the extension used ``yaml.dump`` which serialised
    ``knack.validators.DefaultStr`` with Python-specific type tags.
    ``yaml.safe_load`` cannot read those tags back.  When that happens,
    fall back to ``_RepairLoader`` which maps the tag to a plain ``str``,
    then sanitise so subsequent saves are clean.
    """
    try:
        return yaml.safe_load(stream)
    except yaml.constructor.ConstructorError:
        # Re-read from the beginning if stream supports seek
        if hasattr(stream, "seek"):
            stream.seek(0)

        data = yaml.load(stream, Loader=_RepairLoader)  # noqa: S506
        logger.warning("Repaired legacy config — re-saving without Python type tags.")
        return _sanitize_for_yaml(data) if data else data


# --- Azure-only constraint validation helpers ---

_AZURE_OPENAI_ENDPOINT_PATTERN = re.compile(r"^https://[a-zA-Z0-9][a-zA-Z0-9\-]*\.openai\.azure\.com/?$")

_ALLOWED_AI_PROVIDERS = frozenset({"github-models", "azure-openai", "copilot"})

_BLOCKED_AI_PROVIDERS = frozenset(
    {
        "openai",
        "chatgpt",
        "public-openai",
        "anthropic",
        "cohere",
        "google",
        "aws-bedrock",
        "huggingface",
    }
)

_BLOCKED_ENDPOINTS = [
    "api.openai.com",
    "chat.openai.com",
    "platform.openai.com",
    "openai.com",
]

_ALLOWED_IAC_TOOLS = frozenset({"terraform", "bicep"})

# Known Azure regions (from naming module's REGION_SHORT_CODES + common extras).
# Not exhaustive but covers all GA regions as of 2025.
_KNOWN_AZURE_REGIONS = frozenset(
    {
        "eastus",
        "eastus2",
        "westus",
        "westus2",
        "westus3",
        "centralus",
        "northcentralus",
        "southcentralus",
        "westcentralus",
        "canadacentral",
        "canadaeast",
        "brazilsouth",
        "northeurope",
        "westeurope",
        "uksouth",
        "ukwest",
        "francecentral",
        "francesouth",
        "germanywestcentral",
        "norwayeast",
        "swedencentral",
        "switzerlandnorth",
        "australiaeast",
        "australiasoutheast",
        "eastasia",
        "southeastasia",
        "japaneast",
        "japanwest",
        "koreacentral",
        "koreasouth",
        "centralindia",
        "southindia",
        "westindia",
        "southafricanorth",
        "uaenorth",
        # Additional GA regions
        "brazilsoutheast",
        "norwaywest",
        "switzerlandwest",
        "germanynorth",
        "polandcentral",
        "italynorth",
        "israelcentral",
        "qatarcentral",
        "mexicocentral",
        "spaincentral",
        "newzealandnorth",
    }
)

# Keys whose values are considered sensitive and belong in the secrets file.
# A key matches if it starts with any of these prefixes.
# NOTE: Endpoints, resource group names, and org names are NOT sensitive —
# only passwords, API keys, subscription IDs, tokens, and similar credentials.
SECRET_KEY_PREFIXES = (
    "ai.azure_openai.api_key",
    "deploy.subscription",
    "deploy.service_principal",
    "deploy.generated_secrets",
    "backlog.token",
    "mcp.servers",
)

DEFAULT_CONFIG = {
    "project": {
        "id": "",
        "name": "",
        "location": "eastus",
        "environment": "dev",
        "created": "",
        "iac_tool": "terraform",
    },
    "naming": {
        "strategy": "microsoft-alz",
        "org": "",
        "env": "dev",
        "zone_id": "zd",
    },
    "ai": {
        "provider": "copilot",
        "model": "claude-sonnet-4.5",
        "azure_openai": {
            "endpoint": "",
            "deployment": "gpt-4o",
        },
    },
    "agents": {
        "custom_dir": ".prototype/agents/",
        "custom": {},
        "overrides": {},
    },
    "deploy": {
        "track_changes": True,
        "subscription": "",
        "resource_group": "",
        "tenant": "",
        "service_principal": {
            "client_id": "",
            "client_secret": "",
            "tenant_id": "",
        },
        "generated_secrets": {},
    },
    "backlog": {
        "provider": "github",
        "org": "",
        "project": "",
        "token": "",
    },
    "mcp": {
        "servers": [],
        "custom_dir": ".prototype/mcp/",
    },
    "stages": {
        "init": {"completed": False, "timestamp": None},
        "design": {"completed": False, "timestamp": None, "iterations": 0},
        "build": {"completed": False, "timestamp": None},
        "deploy": {"completed": False, "timestamp": None},
    },
}


class ProjectConfig:
    """Manages prototype.yaml project configuration.

    Provides dot-notation get/set for nested config values
    and handles persistence to disk.

    Sensitive values (API keys, subscription IDs, and similar credentials)
    are stored in a separate ``prototype.secrets.yaml`` that should be
    git-ignored.  Non-sensitive values like endpoints, resource group names,
    and org names remain in ``prototype.yaml`` for version control.
    """

    CONFIG_FILENAME = "prototype.yaml"
    SECRETS_FILENAME = "prototype.secrets.yaml"

    def __init__(self, project_dir: str):
        self.project_dir = Path(project_dir)
        self.config_path = self.project_dir / self.CONFIG_FILENAME
        self.secrets_path = self.project_dir / self.SECRETS_FILENAME
        self._config: dict = {}
        self._secrets: dict = {}

    # ------------------------------------------------------------------ #
    #  Persistence                                                        #
    # ------------------------------------------------------------------ #

    def load(self) -> dict:
        """Load configuration from prototype.yaml (and secrets if present).

        Returns:
            Merged config dict (secrets overlaid onto base config).

        Raises:
            CLIError if config file not found.
        """
        if not self.config_path.exists():
            raise CLIError(
                f"Configuration file not found: {self.config_path}\n" "Run 'az prototype init' to create a project."
            )

        with open(self.config_path, "r", encoding="utf-8") as f:
            self._config = _safe_load_yaml(f) or {}

        # Load secrets overlay
        self._secrets = {}
        if self.secrets_path.exists():
            with open(self.secrets_path, "r", encoding="utf-8") as f:
                self._secrets = _safe_load_yaml(f) or {}
            # Merge secrets into config so callers see a unified view
            self._apply_overrides_to(self._config, self._secrets)

        return self._config

    def save(self):
        """Persist current configuration to prototype.yaml."""
        self.project_dir.mkdir(parents=True, exist_ok=True)

        # Strip secret keys from the base config before writing
        clean_config = self._strip_secrets(self._config)

        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                _sanitize_for_yaml(clean_config),
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

        logger.debug("Configuration saved to %s", self.config_path)

    def save_secrets(self):
        """Persist current secrets to prototype.secrets.yaml."""
        if not self._secrets:
            return

        self.project_dir.mkdir(parents=True, exist_ok=True)

        with open(self.secrets_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                _sanitize_for_yaml(self._secrets),
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

        logger.debug("Secrets saved to %s", self.secrets_path)

    def create_default(self, overrides: dict | None = None) -> dict:
        """Create a new configuration with defaults.

        Args:
            overrides: Values to override in the default config.

        Returns:
            The new config dict.
        """
        import copy
        from datetime import datetime, timezone

        self._config = copy.deepcopy(DEFAULT_CONFIG)
        self._config["project"]["id"] = str(uuid.uuid4())
        self._config["project"]["created"] = datetime.now(timezone.utc).isoformat()
        self._secrets = {}

        if overrides:
            # Separate secret values from safe config before merging
            safe_overrides, secret_overrides = self._partition_overrides(overrides)
            self._apply_overrides_to(self._config, safe_overrides)
            if secret_overrides:
                self._secrets = secret_overrides
                # Also merge into _config so the in-memory view is complete
                self._apply_overrides_to(self._config, secret_overrides)

        self.save()
        self.save_secrets()
        return self._config

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by dot-separated key.

        Examples:
            config.get("project.name")
            config.get("ai.provider")
            config.get("deploy.subscription")
        """
        parts = key.split(".")
        current = self._config

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default

        return current

    def set(self, key: str, value: Any):
        """Set a config value by dot-separated key.

        Creates intermediate dicts as needed.
        Validates security constraints before persisting.
        Secret keys are automatically routed to prototype.secrets.yaml.
        """
        self._validate_config_value(key, value)

        # Always update the in-memory unified config
        self._set_nested(self._config, key, value)

        if self._is_secret_key(key):
            self._set_nested(self._secrets, key, value)
            self.save()
            self.save_secrets()
        else:
            self.save()

    # ------------------------------------------------------------------ #
    #  Security-constraint validation                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_config_value(key: str, value: Any):
        """Enforce constraints at config-set time.

        Rules:
          - ai.provider must be in the allowed set.
          - ai.azure_openai.endpoint must be *.openai.azure.com.
          - ai.azure_openai.api_key is no longer accepted.
          - project.iac_tool must be terraform or bicep.
          - project.location must be a known Azure region.
        """
        if key == "project.iac_tool":
            tool = str(value).lower().strip()
            if tool not in _ALLOWED_IAC_TOOLS:
                raise CLIError(
                    f"Unknown IaC tool: '{value}'.\n" f"Supported tools: {', '.join(sorted(_ALLOWED_IAC_TOOLS))}"
                )

        if key == "project.location":
            region = str(value).lower().strip()
            if region not in _KNOWN_AZURE_REGIONS:
                raise CLIError(
                    f"Unknown Azure region: '{value}'.\n"
                    "Use 'az account list-locations -o table' to see available regions."
                )

        if key == "ai.provider":
            provider = str(value).lower().strip()
            if provider in _BLOCKED_AI_PROVIDERS:
                raise CLIError(
                    f"AI provider '{value}' is not permitted.\n"
                    "Only Azure-hosted AI services are allowed.\n"
                    "Supported providers: 'github-models', 'azure-openai', 'copilot'."
                )
            if provider not in _ALLOWED_AI_PROVIDERS:
                raise CLIError(
                    f"Unknown AI provider: '{value}'.\n"
                    "Supported providers: 'github-models', 'azure-openai', 'copilot'."
                )

        if key == "ai.azure_openai.endpoint":
            endpoint = str(value).strip()
            for blocked in _BLOCKED_ENDPOINTS:
                if blocked in endpoint.lower():
                    raise CLIError(
                        f"Public OpenAI endpoints are not permitted: {value}\n"
                        "Only Azure-hosted OpenAI instances (*.openai.azure.com) are allowed."
                    )
            if not _AZURE_OPENAI_ENDPOINT_PATTERN.match(endpoint):
                raise CLIError(
                    f"Invalid Azure OpenAI endpoint: {value}\n"
                    "Endpoint must match: https://<resource>.openai.azure.com/\n"
                    "Public OpenAI, ChatGPT, or third-party hosted endpoints are not permitted."
                )

        if key == "ai.azure_openai.api_key":
            raise CLIError(
                "API-key authentication is not supported for Azure OpenAI.\n"
                "Authentication is handled via Azure identity (DefaultAzureCredential).\n"
                "Run 'az login' to authenticate instead."
            )

    def to_dict(self) -> dict:
        """Return the full config dict (includes merged secrets)."""
        return self._config.copy()

    def _apply_overrides_to(self, base: dict, overlay: dict):
        """Recursively merge *overlay* into *base*."""

        def merge(b: dict, o: dict):
            for key, value in o.items():
                if isinstance(value, dict) and isinstance(b.get(key), dict):
                    merge(b[key], value)
                else:
                    b[key] = value

        merge(base, overlay)

    # kept for backward-compat with existing call sites
    def _apply_overrides(self, overrides):
        return self._apply_overrides_to(self._config, overrides)

    @staticmethod
    def _set_nested(target: dict, key: str, value: Any):
        """Set a dot-separated *key* in *target*, creating intermediate dicts."""
        parts = key.split(".")
        current = target
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value

    @staticmethod
    def _is_secret_key(key: str) -> bool:
        """Return True if *key* should be stored in the secrets file."""
        return any(key.startswith(prefix) for prefix in SECRET_KEY_PREFIXES)

    def _strip_secrets(self, config: dict) -> dict:
        """Return a deep copy of *config* with secret leaf values replaced by empty strings."""
        import copy

        clean = copy.deepcopy(config)
        for prefix in SECRET_KEY_PREFIXES:
            parts = prefix.split(".")
            node = clean
            for part in parts[:-1]:
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    break
            else:
                leaf = parts[-1]
                if isinstance(node, dict) and leaf in node:
                    node[leaf] = ""
        return clean

    def _partition_overrides(self, overrides: dict) -> tuple[dict, dict]:
        """Split *overrides* into (safe, secrets) dicts.

        Walks the override tree and separates keys that match
        ``SECRET_KEY_PREFIXES`` into a parallel dict structure.
        """
        import copy

        safe = copy.deepcopy(overrides)
        secrets: dict = {}

        for prefix in SECRET_KEY_PREFIXES:
            parts = prefix.split(".")
            # Check if the value exists in overrides
            node = overrides
            found = True
            for part in parts:
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    found = False
                    break

            if found and node:  # non-empty value
                # Add to secrets tree
                self._set_nested(secrets, prefix, node)
                # Remove from safe tree
                safe_node = safe
                for part in parts[:-1]:
                    if isinstance(safe_node, dict) and part in safe_node:
                        safe_node = safe_node[part]
                    else:
                        break
                else:
                    leaf = parts[-1]
                    if isinstance(safe_node, dict) and leaf in safe_node:
                        safe_node[leaf] = ""

        return safe, secrets

    def exists(self) -> bool:
        """Check if config file exists."""
        return self.config_path.exists()
