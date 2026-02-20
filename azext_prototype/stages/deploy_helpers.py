"""Deployment helpers — execution primitives, output capture, script generation, rollback.

Provides reusable deployment utilities used by both ``deploy_stage.py`` and
``deploy_session.py``:

- **Execution primitives**: Terraform/Bicep/app deploy, plan, and rollback functions
- **DeploymentOutputCapture**: collect and persist Terraform/Bicep outputs
- **DeployScriptGenerator**: create deploy.sh scripts for app directories
- **RollbackManager**: track deployment state for potential rollback
"""

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ======================================================================
# Execution Primitives
# ======================================================================

def _find_az() -> str:
    """Resolve the ``az`` CLI executable path.

    When running inside an Azure CLI extension the subprocess PATH may
    not include the directory where ``az`` was installed.  We try, in
    order:

    1. ``shutil.which("az")`` — standard PATH lookup
    2. The ``bin/`` directory next to the running Python interpreter
       (``az`` is a Python entry-point script installed alongside it)
    3. Fall back to bare ``"az"`` and let the OS resolve it.
    """
    found = shutil.which("az")
    if found:
        return found

    # az is usually a sibling of the Python interpreter
    bin_dir = os.path.dirname(sys.executable)
    candidate = os.path.join(bin_dir, "az")
    if os.path.isfile(candidate):
        return candidate
    # Windows variant
    candidate_cmd = candidate + ".cmd"
    if os.path.isfile(candidate_cmd):
        return candidate_cmd

    return "az"


# Module-level cache so we resolve once per process
_AZ: str | None = None


def _az() -> str:
    """Return the cached az CLI path."""
    global _AZ
    if _AZ is None:
        _AZ = _find_az()
    return _AZ


# Canonical mapping: deploy context → env vars.
# Each entry maps a deploy parameter to one or more env var names.
# ARM_* → Azure provider auth (Terraform azurerm, Bicep CLI).
# TF_VAR_* → Terraform HCL input variables (auto-resolved, no -var flag needed).
# Plain names → legacy / deploy-script conventions.
DEPLOY_ENV_MAPPING: dict[str, list[str]] = {
    "subscription": [
        "ARM_SUBSCRIPTION_ID",
        "TF_VAR_subscription_id",
        "SUBSCRIPTION_ID",          # legacy, for deploy.sh scripts
    ],
    "tenant": [
        "ARM_TENANT_ID",
        "TF_VAR_tenant_id",
    ],
    "client_id": [
        "ARM_CLIENT_ID",
        "TF_VAR_client_id",
    ],
    "client_secret": [
        "ARM_CLIENT_SECRET",
        "TF_VAR_client_secret",
    ],
}


def build_deploy_env(
    subscription: str | None = None,
    tenant: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> dict[str, str]:
    """Build a subprocess environment dict with Azure auth context.

    Merges ``os.environ`` with the variables defined in
    :data:`DEPLOY_ENV_MAPPING` so that Terraform, Bicep, and deploy
    scripts all receive consistent credentials.

    ``TF_VAR_*`` entries mean Terraform automatically resolves HCL
    ``variable`` blocks without explicit ``-var`` flags.
    """
    env = {**os.environ}
    values = {
        "subscription": subscription,
        "tenant": tenant,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    for param, value in values.items():
        if value:
            for env_key in DEPLOY_ENV_MAPPING[param]:
                env[env_key] = value
    return env


# ======================================================================
# Terraform Secret Variable Scanning & Resolution
# ======================================================================

# Secret variable name patterns (suffixes).
# Only patterns where the user must *create* a value — not Azure-provisioned
# outputs like connection strings, storage keys, or SAS tokens.
_SECRET_VAR_SUFFIXES = ("_secret", "_password")

# Variables already handled by DEPLOY_ENV_MAPPING or _lookup_deployer_object_id
_KNOWN_VARS = frozenset({
    "subscription_id", "tenant_id", "client_id", "client_secret",
    "deployer_object_id",
})

# Regex to find `variable "<name>" {` blocks and optional `default` values.
_TF_VAR_BLOCK_RE = re.compile(
    r'variable\s+"([^"]+)"\s*\{([^}]*)\}',
    re.DOTALL,
)

_TF_DEFAULT_RE = re.compile(
    r'default\s*=\s*"([^"]*)"',
)


def scan_tf_secret_variables(stage_dir: Path) -> list[str]:
    """Scan .tf files for variable blocks matching secret name patterns.

    Returns variable names that need generated values.
    """
    needed: list[str] = []

    for tf_file in sorted(stage_dir.glob("*.tf")):
        try:
            content = tf_file.read_text(encoding="utf-8")
        except OSError:
            continue

        for match in _TF_VAR_BLOCK_RE.finditer(content):
            var_name = match.group(1)
            block_body = match.group(2)

            # Skip variables that don't match secret suffixes
            if not any(var_name.endswith(suffix) for suffix in _SECRET_VAR_SUFFIXES):
                continue

            # Skip known auth variables already handled elsewhere
            if var_name in _KNOWN_VARS:
                continue

            # Skip variables with a non-empty default value
            default_match = _TF_DEFAULT_RE.search(block_body)
            if default_match and default_match.group(1):
                continue

            if var_name not in needed:
                needed.append(var_name)

    return needed


def resolve_stage_secrets(stage_dir: Path, config: Any) -> dict[str, str]:
    """Resolve secrets for a Terraform stage.

    Checks config for previously generated secrets, generates new ones
    for any unresolved variables, and persists them for reuse.
    Returns a dict of TF_VAR_* env vars ready to merge into deploy env.
    """
    needed = scan_tf_secret_variables(stage_dir)
    if not needed:
        return {}

    env_vars: dict[str, str] = {}
    stored = config.get("deploy.generated_secrets") or {}

    for var_name in needed:
        existing = stored.get(var_name) if isinstance(stored, dict) else None
        if existing:
            env_vars[f"TF_VAR_{var_name}"] = existing
        else:
            # Generate a cryptographically random secret
            value = secrets.token_hex(32)
            config.set(f"deploy.generated_secrets.{var_name}", value)
            env_vars[f"TF_VAR_{var_name}"] = value

    return env_vars


def check_az_login() -> bool:
    """Check if Azure CLI is logged in."""
    try:
        result = subprocess.run(
            [_az(), "account", "show"],
            capture_output=True, text=True, check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def get_current_subscription() -> str:
    """Get the currently active Azure subscription ID."""
    try:
        result = subprocess.run(
            [_az(), "account", "show", "--query", "id", "-o", "tsv"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def get_current_tenant() -> str:
    """Get the currently active Azure tenant ID."""
    try:
        result = subprocess.run(
            [_az(), "account", "show", "--query", "tenantId", "-o", "tsv"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def login_service_principal(client_id: str, client_secret: str, tenant_id: str) -> dict:
    """Authenticate using a service principal.

    Runs ``az login --service-principal`` and returns a result dict with
    ``status`` (``"ok"`` or ``"failed"``), optional ``error``, and
    optional ``subscription`` (the default subscription after login).
    """
    try:
        result = subprocess.run(
            [
                _az(), "login", "--service-principal",
                "-u", client_id,
                "-p", client_secret,
                "--tenant", tenant_id,
            ],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            return {"status": "failed", "error": error}

        # Get the default subscription after login
        sub = get_current_subscription()
        return {"status": "ok", "subscription": sub}
    except FileNotFoundError:
        return {"status": "failed", "error": "az CLI not found on PATH."}


def set_deployment_context(subscription: str, tenant: str | None = None) -> dict:
    """Set the active Azure subscription (and optionally tenant).

    Runs ``az account set --subscription <sub>`` with an optional
    ``--tenant`` flag.  Returns a result dict with ``status`` and
    optional ``error``.
    """
    cmd = [_az(), "account", "set", "--subscription", subscription]
    if tenant:
        cmd.extend(["--tenant", tenant])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            return {"status": "failed", "error": error}
        return {"status": "ok"}
    except FileNotFoundError:
        return {"status": "failed", "error": "az CLI not found on PATH."}


def find_bicep_params(infra_dir: Path, template_file: Path) -> Path | None:
    """Discover a parameter file matching the template.

    Search order:
    1. ``<template_stem>.parameters.json``
    2. ``<template_stem>.bicepparam``
    3. ``parameters.json``
    """
    stem = template_file.stem
    candidates = [
        infra_dir / f"{stem}.parameters.json",
        infra_dir / f"{stem}.bicepparam",
        infra_dir / "parameters.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def is_subscription_scoped(bicep_file: Path) -> bool:
    """Check if a Bicep file targets subscription scope."""
    try:
        content = bicep_file.read_text(encoding="utf-8")
        return "targetScope" in content and "'subscription'" in content
    except OSError:
        return False


def get_deploy_location(infra_dir: Path) -> str | None:
    """Try to read location from a parameters file."""
    for params_file in (infra_dir / "parameters.json", infra_dir / "main.parameters.json"):
        if params_file.exists():
            try:
                data = json.loads(params_file.read_text(encoding="utf-8"))
                params = data.get("parameters", data)
                loc = params.get("location", {})
                if isinstance(loc, dict):
                    return loc.get("value")
                if isinstance(loc, str):
                    return loc
            except (json.JSONDecodeError, OSError):
                pass
    return None


def _terraform_init(infra_dir: Path, env: dict[str, str] | None = None) -> dict:
    """Run ``terraform init`` with automatic backend fallback.

    1. Attempt normal init.
    2. If it fails due to missing backend config (``required field is not
       set``) or duplicate provider blocks, fix what we can and retry
       with ``-backend=false`` so the POC uses local state instead.

    Returns ``{"ok": True}`` on success or ``{"ok": False, "error": ...}``.
    """
    result = subprocess.run(
        ["terraform", "init", "-input=false", "-no-color"],
        capture_output=True, text=True,
        cwd=str(infra_dir), check=False, env=env,
    )
    if result.returncode == 0:
        return {"ok": True}

    error = result.stderr.strip() or result.stdout.strip()

    # --- Fix: duplicate required_providers blocks ---
    # Merge all required_providers into a single terraform block.
    if "Duplicate required providers" in error:
        _deduplicate_providers(infra_dir)
        # Retry after dedup
        result = subprocess.run(
            ["terraform", "init", "-input=false", "-no-color"],
            capture_output=True, text=True,
            cwd=str(infra_dir), check=False, env=env,
        )
        if result.returncode == 0:
            return {"ok": True}
        error = result.stderr.strip() or result.stdout.strip()

    # --- Fallback: backend config fields missing → use local state ---
    backend_missing = "required field is not set" in error
    if backend_missing:
        logger.warning(
            "Remote backend config incomplete in %s — falling back to local state.",
            infra_dir,
        )
        result = subprocess.run(
            ["terraform", "init", "-input=false", "-no-color", "-backend=false"],
            capture_output=True, text=True,
            cwd=str(infra_dir), check=False, env=env,
        )
        if result.returncode == 0:
            return {"ok": True, "warning": "Using local state (remote backend config incomplete)."}
        error = result.stderr.strip() or result.stdout.strip()

    return {"ok": False, "error": error}


def _deduplicate_providers(infra_dir: Path) -> None:
    """Remove duplicate ``required_providers`` blocks.

    Scans all ``.tf`` files in the directory.  If ``required_providers``
    appears in more than one file, strips the ``terraform { ... }``
    wrapper from every file *except* the first one found (alphabetically)
    that contains it, leaving only the provider requirements inside the
    remaining HCL.

    This is a best-effort fix for generated code that accidentally
    declares ``required_providers`` in both ``main.tf`` and ``versions.tf``.
    """
    import re

    tf_files = sorted(infra_dir.glob("*.tf"))
    files_with_block: list[Path] = []

    for tf in tf_files:
        try:
            content = tf.read_text(encoding="utf-8")
        except OSError:
            continue
        if "required_providers" in content:
            files_with_block.append(tf)

    if len(files_with_block) <= 1:
        return  # Nothing to deduplicate

    # Keep the first file's terraform block, strip from the rest.
    # The simple approach: remove the entire terraform { required_providers { ... } }
    # wrapper from secondary files, since the primary already has it.
    pattern = re.compile(
        r'terraform\s*\{[^}]*required_providers\s*\{[^}]*\}[^}]*\}',
        re.DOTALL,
    )

    for secondary in files_with_block[1:]:
        try:
            content = secondary.read_text(encoding="utf-8")
            new_content = pattern.sub("", content).strip()
            if new_content != content.strip():
                secondary.write_text(new_content + "\n", encoding="utf-8")
                logger.info(
                    "Removed duplicate required_providers from %s",
                    secondary.name,
                )
        except OSError:
            pass


def _terraform_validate(infra_dir: Path, env: dict[str, str] | None = None) -> dict:
    """Run ``terraform validate``.  Requires init to have run first.

    Returns ``{"ok": True}`` on success or ``{"ok": False, "error": ...}``.
    """
    result = subprocess.run(
        ["terraform", "validate", "-no-color"],
        capture_output=True, text=True,
        cwd=str(infra_dir), check=False, env=env,
    )
    if result.returncode == 0:
        return {"ok": True}
    error = result.stderr.strip() or result.stdout.strip()
    return {"ok": False, "error": error}


def deploy_terraform(
    infra_dir: Path, subscription: str, env: dict[str, str] | None = None,
) -> dict:
    """Execute Terraform deployment in the given directory.

    Runs ``terraform init`` (with backend fallback), ``terraform plan``,
    and ``terraform apply`` sequentially.  All commands use
    ``-input=false`` and ``-no-color`` to prevent interactive prompts
    and produce clean captured output.

    Returns a result dict with ``status`` and optional ``error`` /
    ``command`` keys.
    """
    # Phase 1: init with automatic backend/provider dedup fallback
    init = _terraform_init(infra_dir, env=env)
    if not init["ok"]:
        return {"status": "failed", "error": init["error"], "command": "terraform init"}

    # Phase 2: validate
    validate = _terraform_validate(infra_dir, env=env)
    if not validate["ok"]:
        return {"status": "failed", "error": validate["error"], "command": "terraform validate"}

    # Phase 3: plan + apply
    commands = [
        ["terraform", "plan", "-input=false", "-no-color",
         "-var", f"subscription_id={subscription}", "-out=tfplan"],
        ["terraform", "apply", "-input=false", "-no-color", "tfplan"],
    ]

    for cmd in commands:
        cmd_str = " ".join(cmd)
        logger.info("Running: %s (cwd=%s)", cmd_str, infra_dir)
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(infra_dir), check=False, env=env,
        )
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            logger.error("Terraform error: %s", error)
            return {"status": "failed", "error": error, "command": cmd_str}

    return {"status": "deployed", "tool": "terraform"}


def deploy_bicep(
    infra_dir: Path, subscription: str, resource_group: str,
    env: dict[str, str] | None = None,
) -> dict:
    """Execute Bicep deployment for a stage directory.

    Supports subscription-level and resource-group-level scopes.
    Auto-discovers template and parameter files.
    """
    main_bicep = infra_dir / "main.bicep"
    if not main_bicep.exists():
        bicep_files = sorted(infra_dir.glob("*.bicep"))
        if not bicep_files:
            return {"status": "skipped", "reason": f"No .bicep files found in {infra_dir}."}
        main_bicep = bicep_files[0]
        logger.info("No main.bicep; using %s", main_bicep.name)

    params_file = find_bicep_params(infra_dir, main_bicep)
    sub_scoped = is_subscription_scoped(main_bicep)

    if sub_scoped:
        cmd_parts = [
            _az(), "deployment", "sub", "create",
            "--location", get_deploy_location(infra_dir) or "eastus",
            "--template-file", str(main_bicep),
            "--subscription", subscription,
        ]
    else:
        if not resource_group:
            return {"status": "failed", "error": "Resource group required for resource-group-scoped Bicep deployment."}
        cmd_parts = [
            _az(), "deployment", "group", "create",
            "--resource-group", resource_group,
            "--template-file", str(main_bicep),
            "--subscription", subscription,
        ]

    if env and env.get("ARM_TENANT_ID"):
        cmd_parts.extend(["--tenant", env["ARM_TENANT_ID"]])

    if params_file:
        cmd_parts.extend(["--parameters", str(params_file)])

    logger.info("Running: %s", " ".join(cmd_parts))
    result = subprocess.run(cmd_parts, capture_output=True, text=True, check=False, env=env)

    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip()
        logger.error("Bicep deployment error: %s", error)
        return {"status": "failed", "error": error}

    return {
        "status": "deployed",
        "tool": "bicep",
        "template": main_bicep.name,
        "scope": "subscription" if sub_scoped else "resourceGroup",
        "deployment_output": result.stdout,
    }


def plan_terraform(
    infra_dir: Path, subscription: str, env: dict[str, str] | None = None,
) -> dict:
    """Run ``terraform plan`` for display (no ``-out``).

    Returns the plan output text for preview / dry-run mode.
    """
    init = _terraform_init(infra_dir, env=env)
    if not init["ok"]:
        return {"status": "failed", "error": init["error"]}

    result = subprocess.run(
        ["terraform", "plan", "-input=false", "-no-color",
         "-var", f"subscription_id={subscription}"],
        capture_output=True, text=True,
        cwd=str(infra_dir), check=False, env=env,
    )

    return {
        "status": "previewed",
        "output": result.stdout.strip(),
        "error": result.stderr.strip() if result.returncode != 0 else None,
    }


def whatif_bicep(
    infra_dir: Path, subscription: str, resource_group: str,
    env: dict[str, str] | None = None,
) -> dict:
    """Run ``az deployment group what-if`` to preview Bicep changes."""
    main_bicep = infra_dir / "main.bicep"
    if not main_bicep.exists():
        bicep_files = sorted(infra_dir.glob("*.bicep"))
        if not bicep_files:
            return {"status": "skipped", "reason": "No .bicep files found."}
        main_bicep = bicep_files[0]

    params_file = find_bicep_params(infra_dir, main_bicep)
    sub_scoped = is_subscription_scoped(main_bicep)

    if sub_scoped:
        cmd_parts = [
            _az(), "deployment", "sub", "what-if",
            "--location", get_deploy_location(infra_dir) or "eastus",
            "--template-file", str(main_bicep),
            "--subscription", subscription,
        ]
    else:
        if not resource_group:
            return {"status": "skipped", "reason": "Resource group required for what-if."}
        cmd_parts = [
            _az(), "deployment", "group", "what-if",
            "--resource-group", resource_group,
            "--template-file", str(main_bicep),
            "--subscription", subscription,
        ]

    if env and env.get("ARM_TENANT_ID"):
        cmd_parts.extend(["--tenant", env["ARM_TENANT_ID"]])

    if params_file:
        cmd_parts.extend(["--parameters", str(params_file)])

    result = subprocess.run(cmd_parts, capture_output=True, text=True, check=False, env=env)

    return {
        "status": "previewed",
        "output": result.stdout.strip(),
        "error": result.stderr.strip() if result.returncode != 0 else None,
    }


def deploy_app_stage(
    stage_dir: Path, subscription: str, resource_group: str,
    env: dict[str, str] | None = None,
) -> dict:
    """Deploy a single application stage directory.

    Looks for ``deploy.sh`` in the stage directory or in subdirectories.
    """
    # Build app env: merge auth env with legacy SUBSCRIPTION_ID / RESOURCE_GROUP
    app_env = dict(env) if env else {**os.environ}
    app_env["SUBSCRIPTION_ID"] = subscription or app_env.get("SUBSCRIPTION_ID", "")
    app_env["RESOURCE_GROUP"] = resource_group or ""

    deploy_script = stage_dir / "deploy.sh"
    if deploy_script.exists():
        logger.info("Running deploy script: %s", deploy_script)
        result = subprocess.run(
            ["bash", str(deploy_script)],
            capture_output=True, text=True, check=False,
            cwd=str(stage_dir),
            env=app_env,
        )
        if result.returncode != 0:
            return {"status": "failed", "error": result.stderr.strip()}
        return {"status": "deployed", "method": "deploy_script"}

    # Look for sub-app directories with their own deploy scripts
    deployed_apps = []
    for app_dir in sorted(stage_dir.iterdir()):
        if app_dir.is_dir():
            app_deploy = app_dir / "deploy.sh"
            if app_deploy.exists():
                logger.info("Deploying app: %s", app_dir.name)
                result = subprocess.run(
                    ["bash", str(app_deploy)],
                    capture_output=True, text=True, check=False,
                    cwd=str(app_dir),
                    env=app_env,
                )
                if result.returncode == 0:
                    deployed_apps.append(app_dir.name)
                else:
                    logger.warning("App %s deploy failed: %s", app_dir.name, result.stderr)

    if deployed_apps:
        return {"status": "deployed", "apps": deployed_apps}

    return {"status": "skipped", "reason": "No deploy scripts found"}


def rollback_terraform(infra_dir: Path, env: dict[str, str] | None = None) -> dict:
    """Run ``terraform destroy`` to roll back a Terraform stage."""
    result = subprocess.run(
        ["terraform", "destroy", "-auto-approve", "-input=false", "-no-color"],
        capture_output=True, text=True,
        cwd=str(infra_dir), check=False, env=env,
    )
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip()
        return {"status": "failed", "error": error}
    return {"status": "rolled_back", "tool": "terraform"}


def rollback_bicep(
    infra_dir: Path, subscription: str, resource_group: str,
    env: dict[str, str] | None = None,
) -> dict:
    """Roll back a Bicep stage by redeploying with ``--mode Complete`` and an empty template.

    This removes all resources in the resource group that are not in the
    (empty) template, effectively destroying the stage's resources.
    For safety, this only works at resource-group scope.
    """
    if not resource_group:
        return {"status": "failed", "error": "Resource group required for Bicep rollback."}

    cmd_parts = [
        _az(), "deployment", "group", "create",
        "--resource-group", resource_group,
        "--template-file", str(infra_dir / "main.bicep"),
        "--mode", "Complete",
        "--subscription", subscription,
    ]
    if env and env.get("ARM_TENANT_ID"):
        cmd_parts.extend(["--tenant", env["ARM_TENANT_ID"]])

    result = subprocess.run(
        cmd_parts,
        capture_output=True, text=True, check=False, env=env,
    )
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip()
        return {"status": "failed", "error": error}
    return {"status": "rolled_back", "tool": "bicep"}


# ======================================================================
# Output Capture
# ======================================================================

class DeploymentOutputCapture:
    """Capture and persist deployment outputs from Terraform / Bicep.

    After infrastructure is deployed, other stages (apps, SQL) often
    need connection strings, endpoints, and resource IDs.  This class
    captures those outputs into a well-known JSON file so that
    subsequent deploy.sh scripts and build agents can reference them.
    """

    OUTPUT_FILE = ".prototype/state/deployment_outputs.json"

    def __init__(self, project_dir: str):
        self.project_dir = Path(project_dir)
        self._outputs: dict = self._load()

    # --- Helpers ---

    @staticmethod
    def _flatten_outputs(outputs: dict) -> dict:
        """Flatten {value, type} wrapper dicts into plain key-value pairs."""
        flat = {}
        for key, obj in outputs.items():
            if isinstance(obj, dict) and "value" in obj:
                flat[key] = obj["value"]
            else:
                flat[key] = obj
        return flat

    # --- Terraform ---

    def capture_terraform(self, infra_dir: Path) -> dict:
        """Run `terraform output -json` and persist results."""
        try:
            result = subprocess.run(
                ["terraform", "output", "-json"],
                capture_output=True, text=True, check=True,
                cwd=str(infra_dir),
            )
            outputs = json.loads(result.stdout)
            flat = self._flatten_outputs(outputs)

            self._outputs["terraform"] = flat
            self._outputs["last_capture"] = datetime.now(timezone.utc).isoformat()
            self._save()
            logger.info("Captured %d Terraform outputs.", len(flat))
            return flat
        except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning("Could not capture Terraform outputs: %s", e)
            return {}

    # --- Bicep ---

    def capture_bicep(self, deployment_output: str) -> dict:
        """Parse Bicep deployment JSON output and persist results."""
        try:
            data = json.loads(deployment_output)
            outputs = data.get("properties", {}).get("outputs", {})
            flat = self._flatten_outputs(outputs)

            self._outputs["bicep"] = flat
            self._outputs["last_capture"] = datetime.now(timezone.utc).isoformat()
            self._save()
            logger.info("Captured %d Bicep outputs.", len(flat))
            return flat
        except (json.JSONDecodeError, KeyError, AttributeError) as e:
            logger.warning("Could not parse Bicep deployment output: %s", e)
            return {}

    # --- Accessors ---

    def get(self, key: str, default: Any = None) -> Any:
        """Get a captured output value by key.

        Searches across all providers (terraform, bicep).
        """
        for provider in ("terraform", "bicep"):
            provider_outputs = self._outputs.get(provider, {})
            if key in provider_outputs:
                return provider_outputs[key]
        return default

    def get_all(self) -> dict:
        """Return all captured outputs."""
        return self._outputs.copy()

    def to_env_vars(self) -> dict[str, str]:
        """Convert captured outputs to environment variable mapping.

        This is used by deploy.sh scripts so they can reference
        infrastructure outputs without hard-coding values.
        """
        env_vars = {}
        for provider in ("terraform", "bicep"):
            provider_outputs = self._outputs.get(provider, {})
            for key, value in provider_outputs.items():
                env_name = f"PROTOTYPE_{key.upper()}"
                env_vars[env_name] = str(value)
        return env_vars

    # --- Persistence ---

    def _load(self) -> dict:
        path = self.project_dir / self.OUTPUT_FILE
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {}

    def _save(self):
        path = self.project_dir / self.OUTPUT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._outputs, f, indent=2)


# ======================================================================
# Deploy Script Generator
# ======================================================================

class DeployScriptGenerator:
    """Generate deploy.sh scripts for application directories.

    Each generated script:
    - Reads infrastructure outputs from deployment_outputs.json
    - Sets environment variables for connection strings / endpoints
    - Deploys the application using az webapp deploy, az containerapp up, etc.
    """

    SCRIPT_HEADER = """\
#!/usr/bin/env bash
# ---------------------------------------------------------------
# Auto-generated by az prototype — do not edit manually.
# Re-generate with: az prototype build --scope apps
# ---------------------------------------------------------------
set -euo pipefail

ENVIRONMENT="${{1:-dev}}"
SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "🚀 Deploying: {app_name} (environment: $ENVIRONMENT)"

# Load infrastructure outputs
OUTPUTS_FILE="$PROJECT_ROOT/.prototype/state/deployment_outputs.json"
if [ -f "$OUTPUTS_FILE" ]; then
    echo "   Loading infrastructure outputs..."
    # Export all PROTOTYPE_* env vars from outputs
    while IFS='=' read -r key value; do
        export "$key"="$value"
    done < <(python3 -c "
import json, sys
with open('$OUTPUTS_FILE') as f:
    data = json.load(f)
for provider in ('terraform', 'bicep'):
    for k, v in data.get(provider, {{}}).items():
        print(f'PROTOTYPE_{{k.upper()}}={{v}}')
")
else
    echo "⚠️  No deployment outputs found. Infrastructure may not be deployed yet."
fi

"""

    DEPLOY_WEBAPP = """\
# --- Deploy to Azure App Service ---
echo "   Deploying to App Service..."
RESOURCE_GROUP="${{PROTOTYPE_RESOURCE_GROUP_NAME:-{resource_group}}}"
APP_NAME="${{PROTOTYPE_APP_SERVICE_NAME:-{app_name}}}"

az webapp deploy \\
    --resource-group "$RESOURCE_GROUP" \\
    --name "$APP_NAME" \\
    --src-path "$SCRIPT_DIR" \\
    --type zip

echo "✅ App Service deployment complete: $APP_NAME"
"""

    DEPLOY_CONTAINER_APP = """\
# --- Deploy to Azure Container Apps ---
echo "   Deploying to Container Apps..."
RESOURCE_GROUP="${{PROTOTYPE_RESOURCE_GROUP_NAME:-{resource_group}}}"
APP_NAME="{app_name}"
REGISTRY="${{PROTOTYPE_CONTAINER_REGISTRY_LOGIN_SERVER:-{registry}}}"
IMAGE="$REGISTRY/$APP_NAME:$ENVIRONMENT"

echo "   Building container image..."
az acr build \\
    --registry "${{REGISTRY%%.*}}" \\
    --image "$APP_NAME:$ENVIRONMENT" \\
    --file "$SCRIPT_DIR/Dockerfile" \\
    "$SCRIPT_DIR"

echo "   Updating Container App..."
az containerapp update \\
    --resource-group "$RESOURCE_GROUP" \\
    --name "$APP_NAME" \\
    --image "$IMAGE"

echo "✅ Container App deployment complete: $APP_NAME"
"""

    DEPLOY_FUNCTION = """\
# --- Deploy to Azure Functions ---
echo "   Deploying to Azure Functions..."
RESOURCE_GROUP="${{PROTOTYPE_RESOURCE_GROUP_NAME:-{resource_group}}}"
FUNC_NAME="${{PROTOTYPE_FUNCTION_APP_NAME:-{func_name}}}"

cd "$SCRIPT_DIR"
func azure functionapp publish "$FUNC_NAME" --python

echo "✅ Function App deployment complete: $FUNC_NAME"
"""

    @classmethod
    def generate(
        cls,
        app_dir: Path,
        app_name: str,
        deploy_type: str = "webapp",
        resource_group: str = "",
        registry: str = "",
    ) -> str:
        """Generate a deploy.sh script for an application directory.

        Args:
            app_dir: Target directory for the script.
            app_name: Application name (used in Azure resource names).
            deploy_type: 'webapp', 'container_app', or 'function'.
            resource_group: Default resource group name.
            registry: Default container registry (for container_app).

        Returns:
            The generated script content.
        """
        script = cls.SCRIPT_HEADER.format(app_name=app_name)

        if deploy_type == "container_app":
            script += cls.DEPLOY_CONTAINER_APP.format(
                app_name=app_name,
                resource_group=resource_group,
                registry=registry or "myregistry.azurecr.io",
            )
        elif deploy_type == "function":
            script += cls.DEPLOY_FUNCTION.format(
                func_name=app_name,
                resource_group=resource_group,
            )
        else:
            script += cls.DEPLOY_WEBAPP.format(
                app_name=app_name,
                resource_group=resource_group,
            )

        deploy_path = app_dir / "deploy.sh"
        deploy_path.write_text(script, encoding="utf-8")
        logger.info("Generated deploy script: %s", deploy_path)

        return script


# ======================================================================
# Rollback Manager
# ======================================================================

class RollbackManager:
    """Track deployment state for rollback capability.

    Before each deployment, takes a snapshot of the deployment state.
    If deployment fails, provides guidance on how to roll back.
    """

    ROLLBACK_FILE = ".prototype/state/rollback.json"

    def __init__(self, project_dir: str):
        self.project_dir = Path(project_dir)
        self._state: dict = self._load()

    def snapshot_before_deploy(self, scope: str, iac_tool: str):
        """Record pre-deployment state for potential rollback."""
        snapshot = {
            "scope": scope,
            "iac_tool": iac_tool,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "terraform_state_version": self._get_terraform_state_version() if iac_tool == "terraform" else None,
        }

        if "snapshots" not in self._state:
            self._state["snapshots"] = []

        self._state["snapshots"].append(snapshot)
        self._save()

        logger.info("Pre-deployment snapshot recorded for scope '%s'.", scope)
        return snapshot

    def get_rollback_instructions(self, scope: str = "all") -> list[str]:
        """Generate rollback instructions based on deployment history.

        Returns:
            List of CLI commands the user can run to rollback.
        """
        instructions = []
        snapshots = self._state.get("snapshots", [])

        if not snapshots:
            return ["No deployment snapshots found. Nothing to roll back."]

        latest = snapshots[-1]
        iac_tool = latest.get("iac_tool", "terraform")

        if iac_tool == "terraform":
            instructions.extend([
                "# Terraform rollback options:",
                "cd concept/infra/terraform",
                "",
                "# Option 1: Revert to previous state",
                "terraform state pull > current.tfstate.backup",
                "terraform apply -target=<resource>  # selective revert",
                "",
                "# Option 2: Destroy and re-deploy",
                "terraform destroy -auto-approve",
                "az prototype deploy --force",
            ])
        else:
            instructions.extend([
                "# Bicep rollback options:",
                "",
                "# Option 1: Re-deploy previous version",
                "az deployment group create \\",
                "    --resource-group <rg-name> \\",
                "    --template-file concept/infra/bicep/main.bicep \\",
                "    --mode Complete  # removes resources not in template",
                "",
                "# Option 2: Delete resource group",
                "az group delete --name <rg-name> --yes --no-wait",
                "az prototype deploy --force",
            ])

        return instructions

    def snapshot_stage(self, stage_num: int, scope: str, iac_tool: str) -> dict:
        """Record per-stage pre-deployment snapshot."""
        snapshot = {
            "stage": stage_num,
            "scope": scope,
            "iac_tool": iac_tool,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if "stage_snapshots" not in self._state:
            self._state["stage_snapshots"] = []
        self._state["stage_snapshots"].append(snapshot)
        self._save()
        logger.info("Stage %d snapshot recorded.", stage_num)
        return snapshot

    def get_last_snapshot(self) -> dict | None:
        """Return the most recent deployment snapshot."""
        snapshots = self._state.get("snapshots", [])
        return snapshots[-1] if snapshots else None

    # --- Internal ---

    def _get_terraform_state_version(self) -> str | None:
        """Get the current Terraform state serial number."""
        state_file = self.project_dir / "concept" / "infra" / "terraform" / "terraform.tfstate"
        if state_file.exists():
            try:
                with open(state_file, "r") as f:
                    data = json.load(f)
                return str(data.get("serial", "unknown"))
            except (json.JSONDecodeError, IOError):
                pass
        return None

    def _load(self) -> dict:
        path = self.project_dir / self.ROLLBACK_FILE
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {}

    def _save(self):
        path = self.project_dir / self.ROLLBACK_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)
