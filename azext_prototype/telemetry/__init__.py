"""Telemetry collection via Application Insights (direct HTTP ingestion).

Sends lightweight usage events to App Insights so the engineering team
can understand adoption, regional demand, and reliability.  See
TELEMETRY.md for the full list of fields and privacy commitments.

Design principles:
* **Honour Azure CLI telemetry** — if ``az config set core.disable_telemetry=true``
  has been run, no events are emitted.  The legacy ``core.collect_telemetry=no``
  key is also respected.  When neither key is set, telemetry is **enabled** by
  default.
* **Graceful degradation** — if the connection string is missing, the
  network is unreachable, or any other error occurs, telemetry is silently
  skipped.  No error messages are ever shown to the user for telemetry
  failures.
* **Connection string priority** —
  1. ``APPINSIGHTS_CONNECTION_STRING`` environment variable (local testing).
  2. ``_BUILTIN_CONNECTION_STRING`` constant (injected at build time by the
     release pipeline).
* **No opencensus dependency** — earlier versions used ``AzureLogHandler``
  from ``opencensus-ext-azure`` but its ``BaseLogHandler.createLock()``
  override sets ``self.lock = None`` which is incompatible with Python
  3.13+ where ``logging.Handler.handle()`` uses ``with self.lock:``.  We
  now POST directly to the ``/v2/track`` ingestion endpoint which is
  synchronous and guaranteed to complete before the CLI process exits.
"""

import json
import logging
import os
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------
# Build-time placeholder
# ---------------------------------------------------------------
# For local testing / override, set the APPINSIGHTS_CONNECTION_STRING
# env var.  Otherwise this embedded value is used.
_BUILTIN_CONNECTION_STRING = ""

# ---------------------------------------------------------------
# Module-level singletons (lazily initialised)
# ---------------------------------------------------------------
_ingestion_endpoint: str | None = None
_instrumentation_key: str | None = None
_enabled: bool | None = None


# ---------------------------------------------------------------
# Azure CLI telemetry opt-out check
# ---------------------------------------------------------------


def _is_cli_telemetry_enabled() -> bool:
    """Return *True* if the user has not disabled Azure CLI telemetry.

    Checks (in order):
    1. ``AZURE_CORE_COLLECT_TELEMETRY`` env var — "no"/"false"/"0" disables.
    2. ``[core] disable_telemetry`` in the az config file — ``true`` disables.
       Missing / null / ``false`` means **enabled** (the default).
    3. ``[core] collect_telemetry`` (legacy) — ``false``/``no`` disables.
    """
    try:
        # 1. Environment variable takes precedence
        env_val = os.environ.get("AZURE_CORE_COLLECT_TELEMETRY")
        if env_val is not None:
            return env_val.lower() not in ("no", "false", "0", "off")

        # 2. Fall back to the az config file
        import configparser

        from azure.cli.core._environment import get_config_dir

        config_path = os.path.join(get_config_dir(), "config")
        if os.path.exists(config_path):
            parser = configparser.ConfigParser()
            parser.read(config_path)

            # Preferred key: core.disable_telemetry (true → disabled)
            # Missing / null treated as *not* disabled (i.e. enabled).
            if parser.has_option("core", "disable_telemetry"):
                return not parser.getboolean("core", "disable_telemetry")

            # Legacy key: core.collect_telemetry (false → disabled)
            if parser.has_option("core", "collect_telemetry"):
                return parser.getboolean("core", "collect_telemetry")

        return True  # Default — enabled
    except Exception:
        return True


# ---------------------------------------------------------------
# Connection string helpers
# ---------------------------------------------------------------


def _get_connection_string() -> str:
    """Return the App Insights connection string.

    Priority: environment variable → built-in (build-time injected).
    """
    return os.environ.get("APPINSIGHTS_CONNECTION_STRING", "") or _BUILTIN_CONNECTION_STRING


# ---------------------------------------------------------------
# Public API — enabled check
# ---------------------------------------------------------------


def is_enabled() -> bool:
    """Return *True* when telemetry can and should be sent.

    This checks **both** the Azure CLI telemetry setting *and* whether a
    connection string is available.  The result is cached for the lifetime
    of the process.
    """
    global _enabled
    if _enabled is not None:
        return _enabled
    try:
        _enabled = _is_cli_telemetry_enabled() and bool(_get_connection_string())
    except Exception:
        _enabled = False
    return _enabled


def reset() -> None:
    """Reset cached state — useful for tests."""
    global _enabled, _ingestion_endpoint, _instrumentation_key
    _enabled = None
    _ingestion_endpoint = None
    _instrumentation_key = None


# ---------------------------------------------------------------
# Connection string parsing
# ---------------------------------------------------------------


def _parse_connection_string(cs: str) -> tuple[str, str]:
    """Parse an App Insights connection string into (endpoint, ikey).

    Returns ``("", "")`` if the string is empty or malformed.
    """
    if not cs:
        return "", ""
    try:
        parts = dict(p.split("=", 1) for p in cs.split(";") if "=" in p)
        ikey = parts.get("InstrumentationKey", "")
        endpoint = parts.get("IngestionEndpoint", "").rstrip("/")
        if ikey and endpoint:
            return endpoint + "/v2/track", ikey
    except Exception:
        pass
    return "", ""


def _get_ingestion_config() -> tuple[str, str]:
    """Return ``(endpoint_url, instrumentation_key)``, cached.

    Parses the connection string on first call and caches the result.
    """
    global _ingestion_endpoint, _instrumentation_key
    if _ingestion_endpoint is not None:
        return _ingestion_endpoint, _instrumentation_key or ""

    endpoint, ikey = _parse_connection_string(_get_connection_string())
    _ingestion_endpoint = endpoint
    _instrumentation_key = ikey
    return endpoint, ikey


# ---------------------------------------------------------------
# Dimension helpers
# ---------------------------------------------------------------


def _get_extension_version() -> str:
    """Return the installed extension version.

    Uses ``importlib.metadata`` (the actual installed package version from the
    wheel) as the primary source of truth, falling back to ``azext_metadata.json``
    if the package isn't installed in editable/normal mode.
    """
    try:
        from importlib.metadata import version as pkg_version

        return pkg_version("prototype")
    except Exception:
        pass

    try:
        meta_path = Path(__file__).resolve().parent.parent / "azext_metadata.json"
        with open(meta_path, encoding="utf-8") as f:
            return json.load(f).get("version", "unknown")
    except Exception:
        return "unknown"


def _get_tenant_id(cmd) -> str:
    """Try to extract the tenant ID from the CLI authentication context."""
    try:
        from azure.cli.core._profile import Profile

        profile = Profile(cli_ctx=cmd.cli_ctx)
        sub = profile.get_subscription()
        return sub.get("tenantId", "")
    except Exception:
        return ""


# ---------------------------------------------------------------
# Default models per provider (used when config file is not yet
# available, e.g. during ``prototype init``).
# ---------------------------------------------------------------

_DEFAULT_PROVIDER_MODELS: dict[str, str] = {
    "copilot": "claude-sonnet-4.5",
    "github-models": "gpt-4o",
    "azure-openai": "gpt-4o",
}


# ---------------------------------------------------------------
# Public API — event tracking
# ---------------------------------------------------------------


def _get_ai_config() -> tuple[str, str]:
    """Try to read AI provider and model from the current project config.

    Returns ``(provider, model)`` — both empty strings on any failure.
    """
    try:
        # The canonical config file is 'prototype.yaml' at the project root.
        config_path = Path.cwd() / "prototype.yaml"
        if not config_path.exists():
            return "", ""
        import yaml  # lazy — avoids import cost when telemetry is off

        with open(config_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        ai = data.get("ai", {})
        return ai.get("provider", ""), ai.get("model", "")
    except Exception:
        return "", ""


def _get_project_id() -> str:
    """Try to read the project ID from the current project config.

    Returns an empty string on any failure.
    """
    try:
        config_path = Path.cwd() / "prototype.yaml"
        if not config_path.exists():
            return ""
        import yaml

        with open(config_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data.get("project", {}).get("id", "")
    except Exception:
        return ""


def _send_envelope(envelope: dict, endpoint: str) -> bool:
    """POST a single envelope to the App Insights ingestion endpoint.

    Returns *True* on success (HTTP 200 with items accepted),
    *False* on any error.  Never raises.
    """
    try:
        import requests  # lazy — avoids import cost when telemetry is off

        resp = requests.post(
            endpoint,
            data=json.dumps([envelope]),
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        return resp.status_code == 200
    except Exception:
        return False


def track_command(
    command_name: str,
    *,
    cmd=None,
    success: bool = True,
    error: str = "",
    parameters: dict | None = None,
    tenant_id: str = "",
    project_id: str = "",
    provider: str = "",
    model: str = "",
    resource_type: str = "",
    location: str = "",
    sku: str = "",
) -> None:
    """Send a ``cli_command_executed`` telemetry event.

    Parameters match the fields documented in TELEMETRY.md.  All errors
    are silently swallowed.
    """
    if not is_enabled():
        return

    endpoint, ikey = _get_ingestion_config()
    if not endpoint or not ikey:
        return

    if not tenant_id and cmd is not None:
        tenant_id = _get_tenant_id(cmd)

    if not project_id:
        project_id = _get_project_id()

    try:
        properties: dict[str, str] = {
            "commandName": command_name,
            "tenantId": tenant_id,
            "projectId": project_id,
            "provider": provider,
            "model": model,
            "resourceType": resource_type,
            "location": location,
            "sku": sku,
            "extensionVersion": _get_extension_version(),
            "success": str(success).lower(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if parameters:
            properties["parameters"] = json.dumps(_sanitize_parameters(parameters))

        if error:
            properties["error"] = error[:1024]  # Cap at 1KB

        envelope = {
            "name": "Microsoft.ApplicationInsights.Event",
            "time": datetime.now(timezone.utc).isoformat(),
            "iKey": ikey,
            "tags": {
                "ai.cloud.role": "az-prototype",
                "ai.internal.sdkVersion": "py-direct:1.0.0",
            },
            "data": {
                "baseType": "EventData",
                "baseData": {
                    "ver": 2,
                    "name": "cli_command_executed",
                    "properties": properties,
                },
            },
        }
        _send_envelope(envelope, endpoint)
    except Exception:
        pass  # Never surface telemetry errors to the user


# Keys whose values must never be sent in telemetry.
_SENSITIVE_PARAM_KEYS = frozenset(
    {
        "api_key",
        "token",
        "secret",
        "password",
        "key",
        "subscription",
        "connection_string",
    }
)


def _sanitize_parameters(params: dict) -> dict:
    """Return a copy of *params* with sensitive values redacted.

    Only includes JSON-serialisable scalar values (str, int, float, bool,
    None).  Non-serialisable values (objects, functions) are dropped.
    """
    clean: dict[str, object] = {}
    for k, v in params.items():
        if k.startswith("_"):
            continue
        if k in _SENSITIVE_PARAM_KEYS:
            clean[k] = "***"
        elif isinstance(v, (str, int, float, bool, type(None))):
            clean[k] = v
        else:
            clean[k] = str(type(v).__name__)
    return clean


def track_build_resources(
    command_name: str,
    *,
    cmd=None,
    success: bool = True,
    error: str = "",
    parameters: dict | None = None,
    resources: list[dict[str, str]] | None = None,
    tenant_id: str = "",
    project_id: str = "",
    provider: str = "",
    model: str = "",
    location: str = "",
) -> None:
    """Send a telemetry event for a build with multiple resources.

    Each entry in *resources* should be a dict with ``resourceType`` and
    ``sku`` keys.  The aggregated list is serialised as a JSON string in
    the ``resources`` property, and ``resourceCount`` records the total.

    For backward compatibility the first resource's type and SKU are also
    written to the legacy ``resourceType`` / ``sku`` scalar fields.
    """
    if not is_enabled():
        return

    endpoint, ikey = _get_ingestion_config()
    if not endpoint or not ikey:
        return

    if not tenant_id and cmd is not None:
        tenant_id = _get_tenant_id(cmd)

    if not project_id:
        project_id = _get_project_id()

    resources = resources or []

    try:
        properties: dict[str, str] = {
            "commandName": command_name,
            "tenantId": tenant_id,
            "projectId": project_id,
            "provider": provider,
            "model": model,
            "location": location,
            "resources": json.dumps(resources),
            "resourceCount": str(len(resources)),
            "extensionVersion": _get_extension_version(),
            "success": str(success).lower(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if parameters:
            properties["parameters"] = json.dumps(_sanitize_parameters(parameters))

        if error:
            properties["error"] = error[:1024]

        # Backward compat: first resource as legacy scalar fields
        if resources:
            properties["resourceType"] = resources[0].get("resourceType", "")
            properties["sku"] = resources[0].get("sku", "")
        else:
            properties["resourceType"] = ""
            properties["sku"] = ""

        envelope = {
            "name": "Microsoft.ApplicationInsights.Event",
            "time": datetime.now(timezone.utc).isoformat(),
            "iKey": ikey,
            "tags": {
                "ai.cloud.role": "az-prototype",
                "ai.internal.sdkVersion": "py-direct:1.0.0",
            },
            "data": {
                "baseType": "EventData",
                "baseData": {
                    "ver": 2,
                    "name": "cli_command_executed",
                    "properties": properties,
                },
            },
        }
        _send_envelope(envelope, endpoint)
    except Exception:
        pass  # Never surface telemetry errors to the user


# ---------------------------------------------------------------
# Public API — decorator
# ---------------------------------------------------------------


def track(command_name: str):
    """Decorator that records command-execution telemetry.

    Wraps a CLI command handler so that a telemetry event is sent in the
    ``finally`` block — capturing both successes and failures.

    The decorated function **must** accept ``cmd`` as its first positional
    argument (standard Azure CLI convention).

    Usage::

        @track("prototype init")
        def prototype_init(cmd, name=None, location="eastus", ...):
            ...
    """

    def decorator(func):
        @wraps(func)
        def wrapper(cmd, *args, **kwargs):
            success = True
            error_msg = ""
            try:
                return func(cmd, *args, **kwargs)
            except Exception as exc:
                success = False
                error_msg = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                try:
                    # Commands that collect values interactively (e.g.
                    # ``config init``) can attach telemetry overrides to
                    # ``cmd`` so the decorator picks them up even though
                    # they aren't in kwargs.
                    _raw = getattr(cmd, "_telemetry_overrides", None)
                    overrides = _raw if isinstance(_raw, dict) else {}

                    # Extract common dimensions from kwargs when present.
                    # ai_provider / model may be direct kwargs (e.g. init)
                    # or stored in prototype.yaml (all other commands).
                    location = overrides.get("location") or kwargs.get("location", "")
                    provider = overrides.get("ai_provider") or kwargs.get("ai_provider", "")
                    model = overrides.get("model") or kwargs.get("model", "")
                    if not provider or not model:
                        cfg_provider, cfg_model = _get_ai_config()
                        provider = provider or cfg_provider
                        model = model or cfg_model
                    # When provider is known but model is still empty
                    # (e.g. prototype init creates the config in a
                    # subdirectory so _get_ai_config can't find it),
                    # fall back to the provider's default model.
                    if provider and not model:
                        model = _DEFAULT_PROVIDER_MODELS.get(provider, "")

                    # Merge overrides into the parameter dict so the
                    # telemetry event contains the chosen values.
                    params = {**kwargs, **overrides}

                    track_command(
                        command_name,
                        cmd=cmd,
                        success=success,
                        error=error_msg,
                        parameters=params,
                        location=location,
                        provider=provider,
                        model=model,
                    )
                except Exception:
                    pass  # Telemetry must never break the command

        return wrapper

    return decorator
