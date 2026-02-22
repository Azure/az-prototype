"""Interactive deploy session — Claude Code-inspired conversational deployment.

Follows the :class:`~.build_session.BuildSession` pattern: bordered prompts,
progress indicators, slash commands, and a review loop.  The deploy session
orchestrates staged deployments with preflight checks, QA-first error routing,
and ordered rollback support.

Phases:

1. **Load build state** — Import deployment stages from the build stage output
2. **Plan overview** — Display the deployment plan and confirm
3. **Preflight** — Validate subscription, resource providers, resource group, IaC tool
4. **Stage-by-stage deploy** — Execute each stage with progress tracking
5. **Output capture** — Capture Terraform/Bicep outputs after infra stages
6. **Deploy report** — Summary of what was deployed
7. **Interactive loop** — Slash commands for rollback, redeploy, status, etc.
"""

from __future__ import annotations

import logging
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from azext_prototype.agents.base import AgentCapability, AgentContext
from azext_prototype.agents.registry import AgentRegistry
from azext_prototype.ai.token_tracker import TokenTracker
from azext_prototype.config import ProjectConfig
from azext_prototype.stages.deploy_helpers import (
    DeploymentOutputCapture,
    RollbackManager,
    _az,
    build_deploy_env,
    check_az_login,
    deploy_app_stage,
    deploy_bicep,
    deploy_terraform,
    get_current_subscription,
    get_current_tenant,
    plan_terraform,
    resolve_stage_secrets,
    rollback_bicep,
    rollback_terraform,
    set_deployment_context,
    whatif_bicep,
)
from azext_prototype.stages.deploy_state import DeployState
from azext_prototype.stages.escalation import EscalationTracker
from azext_prototype.stages.qa_router import route_error_to_qa
from azext_prototype.tracking import ChangeTracker
from azext_prototype.ui.console import Console, DiscoveryPrompt
from azext_prototype.ui.console import console as default_console

logger = logging.getLogger(__name__)


def _lookup_deployer_object_id(client_id: str | None = None) -> str | None:
    """Resolve the AAD object ID of the deployer.

    - If *client_id* is given (service-principal auth): queries
      ``az ad sp show --id <client_id>`` for the SP's object ID.
    - Otherwise (interactive / user auth): queries
      ``az ad signed-in-user show`` for the logged-in user's object ID.

    Returns ``None`` if the lookup fails (not logged in, insufficient
    permissions, etc.).
    """
    try:
        if client_id:
            cmd = [_az(), "ad", "sp", "show", "--id", client_id, "--query", "id", "-o", "tsv"]
        else:
            cmd = [_az(), "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except FileNotFoundError:
        pass
    logger.debug("Could not resolve deployer object ID (client_id=%s)", client_id)
    return None


# -------------------------------------------------------------------- #
# Sentinels
# -------------------------------------------------------------------- #

_QUIT_WORDS = frozenset({"q", "quit", "exit"})
_DONE_WORDS = frozenset({"done", "finish", "accept", "lgtm"})
_SLASH_COMMANDS = frozenset(
    {
        "/status",
        "/stages",
        "/deploy",
        "/rollback",
        "/redeploy",
        "/plan",
        "/outputs",
        "/preflight",
        "/login",
        "/help",
    }
)


# -------------------------------------------------------------------- #
# DeployResult — public interface consumed by DeployStage
# -------------------------------------------------------------------- #


class DeployResult:
    """Result of a deploy session."""

    __slots__ = (
        "deployed_stages",
        "failed_stages",
        "rolled_back_stages",
        "captured_outputs",
        "cancelled",
    )

    def __init__(
        self,
        deployed_stages: list[dict[str, Any]] | None = None,
        failed_stages: list[dict[str, Any]] | None = None,
        rolled_back_stages: list[dict[str, Any]] | None = None,
        captured_outputs: dict[str, Any] | None = None,
        cancelled: bool = False,
    ) -> None:
        self.deployed_stages = deployed_stages or []
        self.failed_stages = failed_stages or []
        self.rolled_back_stages = rolled_back_stages or []
        self.captured_outputs = captured_outputs or {}
        self.cancelled = cancelled


# -------------------------------------------------------------------- #
# DeploySession
# -------------------------------------------------------------------- #


class DeploySession:
    """Interactive, multi-phase deploy conversation.

    Manages the full deploy lifecycle: preflight checks, staged deployment
    with progress tracking, output capture, QA-first error routing, and
    a conversational loop with slash commands for rollback and redeployment.

    Parameters
    ----------
    agent_context:
        Runtime context with AI provider and project config.
    registry:
        Agent registry for resolving the QA agent.
    console:
        Styled console for output.
    deploy_state:
        Pre-initialised deploy state (for re-entrant deploys).
    """

    def __init__(
        self,
        agent_context: AgentContext,
        registry: AgentRegistry,
        *,
        console: Console | None = None,
        deploy_state: DeployState | None = None,
    ) -> None:
        self._context = agent_context
        self._registry = registry
        self._console = console or default_console
        self._prompt = DiscoveryPrompt(self._console)
        self._deploy_state = deploy_state or DeployState(agent_context.project_dir)

        # Resolve QA agent for error routing
        qa_agents = registry.find_by_capability(AgentCapability.QA)
        self._qa_agent = qa_agents[0] if qa_agents else None

        # Escalation tracker
        self._escalation_tracker = EscalationTracker(agent_context.project_dir)
        if self._escalation_tracker.exists:
            self._escalation_tracker.load()

        # Project config
        config = ProjectConfig(agent_context.project_dir)
        config.load()
        self._config = config
        self._iac_tool: str = config.get("project.iac_tool", "terraform")

        # Token tracker
        self._token_tracker = TokenTracker()

        # Deployment helpers
        self._output_capture = DeploymentOutputCapture(agent_context.project_dir)
        self._rollback_mgr = RollbackManager(agent_context.project_dir)
        self._change_tracker = ChangeTracker(agent_context.project_dir)

        # Per-run deployment context (set in each run* method)
        self._subscription: str = ""
        self._resource_group: str = ""
        self._tenant: str | None = None
        self._deploy_env: dict[str, str] | None = None

    # ------------------------------------------------------------------ #
    # Internal — resolve deployment context
    # ------------------------------------------------------------------ #

    def _resolve_context(
        self,
        subscription: str | None,
        tenant: str | None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        """Resolve and cache subscription, resource group, tenant, and SP creds.

        CLI-provided ``client_id`` / ``client_secret`` take priority over
        values stored in the project config (``deploy.service_principal.*``).
        """
        self._subscription = subscription or self._config.get("deploy.subscription") or get_current_subscription()
        self._resource_group = self._config.get("deploy.resource_group") or ""
        self._tenant = tenant or self._config.get("deploy.tenant") or None

        # Set deployment context if tenant specified
        if self._tenant and self._subscription:
            ctx_result = set_deployment_context(self._subscription, self._tenant)
            if ctx_result["status"] == "failed":
                logger.warning("Could not set deployment context: %s", ctx_result.get("error", ""))

        # Resolve SP creds: CLI args > config
        sp_cfg = self._config.get("deploy.service_principal", {})
        resolved_client_id = client_id or (sp_cfg.get("client_id") if isinstance(sp_cfg, dict) else None)
        resolved_client_secret = client_secret or (sp_cfg.get("client_secret") if isinstance(sp_cfg, dict) else None)

        # Build auth env for subprocesses
        self._deploy_env = build_deploy_env(
            subscription=self._subscription,
            tenant=self._tenant,
            client_id=resolved_client_id,
            client_secret=resolved_client_secret,
        )

        # Resolve deployer object ID (SP object ID or signed-in user ID)
        deployer_oid = _lookup_deployer_object_id(resolved_client_id)
        if deployer_oid:
            self._deploy_env["TF_VAR_deployer_object_id"] = deployer_oid

    # ------------------------------------------------------------------ #
    # Public API — Interactive session
    # ------------------------------------------------------------------ #

    def run(
        self,
        *,
        subscription: str | None = None,
        tenant: str | None = None,
        force: bool = False,
        client_id: str | None = None,
        client_secret: str | None = None,
        input_fn: Callable[[str], str] | None = None,
        print_fn: Callable[[str], None] | None = None,
    ) -> DeployResult:
        """Run the interactive deploy session.

        Parameters
        ----------
        subscription:
            Azure subscription ID.  Falls back to config, then current context.
        tenant:
            Azure AD tenant ID for cross-tenant deployment.
        force:
            Bypass change tracking — deploy all stages.
        client_id / client_secret:
            Service principal credentials (forwarded from CLI flags).
        input_fn / print_fn:
            Injectable I/O for testing.
        """
        use_styled = input_fn is None and print_fn is None
        _input = input_fn or (lambda p: self._prompt.prompt(p))
        _print = print_fn or self._console.print

        # ---- Phase 1: Load build state ----
        if not self._deploy_state._state["deployment_stages"]:
            build_path = Path(self._context.project_dir) / ".prototype" / "state" / "build.yaml"
            if not self._deploy_state.load_from_build_state(build_path):
                _print("  No build state found. Run 'az prototype build' first.")
                return DeployResult(cancelled=True)

        # Resolve subscription / resource group / tenant / SP creds
        self._resolve_context(subscription, tenant, client_id, client_secret)

        self._deploy_state._state["subscription"] = self._subscription
        self._deploy_state._state["tenant"] = self._tenant or ""
        self._deploy_state.save()

        # ---- Phase 2: Plan overview ----
        _print("")
        _print("Deploy Stage")
        _print("=" * 40)
        _print("")

        if self._subscription:
            _print(f"Subscription: {self._subscription}")
        if self._tenant:
            _print(f"Tenant: {self._tenant}")
        if self._resource_group:
            _print(f"Resource Group: {self._resource_group}")
        _print(f"IaC Tool: {self._iac_tool}")
        _print("")
        _print(self._deploy_state.format_stage_status())
        _print("")
        _print("Press Enter to run preflight checks and start deploying.")
        _print("Type 'quit' to exit.")
        _print("")

        try:
            if use_styled:
                confirmation = self._prompt.simple_prompt("> ")
            else:
                confirmation = _input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return DeployResult(cancelled=True)

        if confirmation.lower() in _QUIT_WORDS:
            return DeployResult(cancelled=True)

        # ---- Phase 3: Preflight ----
        _print("")
        with self._maybe_spinner("Running preflight checks...", use_styled):
            preflight = self._run_preflight()

        self._deploy_state.set_preflight_results(preflight)
        _print(self._deploy_state.format_preflight_report())
        _print("")

        failures = self._deploy_state.get_preflight_failures()
        if failures:
            _print("  Some preflight checks failed. Fix the issues above,")
            _print("  then use /deploy to proceed or /preflight to re-check.")
            _print("")
        else:
            # ---- Phase 4: Stage-by-stage deploy ----
            self._deploy_pending_stages(force, use_styled, _print, _input)

        # ---- Phase 5 & 6: Report ----
        _print("")
        _print(self._deploy_state.format_deploy_report())
        _print("")

        # ---- Phase 7: Interactive loop ----
        _print("  Use slash commands to manage deployment. Type /help for a list.")
        _print("  Type 'done' to finish or 'quit' to exit.")
        _print("")

        while True:
            try:
                if use_styled:
                    user_input = self._prompt.prompt(
                        "> ",
                        instruction="Type 'done' to finish the deploy session.",
                        show_quit_hint=True,
                    )
                else:
                    user_input = _input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input:
                continue

            lower = user_input.lower().strip()

            if lower in _QUIT_WORDS:
                break

            if lower in _DONE_WORDS:
                break

            # Slash commands
            if lower.startswith("/"):
                self._handle_slash_command(
                    user_input,
                    force,
                    use_styled,
                    _print,
                    _input,
                )
                continue

            _print("  Use slash commands to manage deployment. Type /help for a list.")

        return self._build_result()

    # ------------------------------------------------------------------ #
    # Public API — Dry-run (non-interactive)
    # ------------------------------------------------------------------ #

    def run_dry_run(
        self,
        *,
        target_stage: int | None = None,
        subscription: str | None = None,
        tenant: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        print_fn: Callable[[str], None] | None = None,
    ) -> DeployResult:
        """Non-interactive what-if / terraform plan preview."""
        _print = print_fn or self._console.print

        # Load stages
        if not self._deploy_state._state["deployment_stages"]:
            build_path = Path(self._context.project_dir) / ".prototype" / "state" / "build.yaml"
            if not self._deploy_state.load_from_build_state(build_path):
                _print("  No build state found. Run 'az prototype build' first.")
                return DeployResult(cancelled=True)

        self._resolve_context(subscription, tenant, client_id, client_secret)

        stages = self._deploy_state._state["deployment_stages"]
        if target_stage is not None:
            stages = [s for s in stages if s["stage"] == target_stage]
            if not stages:
                _print(f"  Stage {target_stage} not found.")
                return DeployResult(cancelled=True)

        _print("")
        _print("  Dry-Run Preview")
        _print("  " + "=" * 40)
        _print("")

        for stage in stages:
            stage_num = stage["stage"]
            category = stage.get("category", "infra")
            stage_dir = Path(self._context.project_dir) / stage.get("dir", "")

            _print(f"  Stage {stage_num}: {stage['name']} ({category})")

            if not stage_dir.is_dir():
                _print(f"    Directory not found: {stage.get('dir', '?')}")
                _print("")
                continue

            if category in ("infra", "data", "integration"):
                dry_env = self._deploy_env
                if self._iac_tool == "terraform":
                    generated = resolve_stage_secrets(stage_dir, self._config)
                    if generated:
                        dry_env = dict(self._deploy_env) if self._deploy_env else {}
                        dry_env.update(generated)
                    result = plan_terraform(stage_dir, self._subscription, env=dry_env)
                else:
                    result = whatif_bicep(stage_dir, self._subscription, self._resource_group, env=self._deploy_env)

                if result.get("output"):
                    _print(result["output"])
                if result.get("error"):
                    _print(f"    Error: {result['error']}")
            else:
                _print("    (Application stage — no preview available)")

            _print("")

        return DeployResult()

    # ------------------------------------------------------------------ #
    # Public API — Single-stage deploy (non-interactive)
    # ------------------------------------------------------------------ #

    def run_single_stage(
        self,
        stage_num: int,
        *,
        subscription: str | None = None,
        tenant: str | None = None,
        force: bool = False,
        client_id: str | None = None,
        client_secret: str | None = None,
        print_fn: Callable[[str], None] | None = None,
    ) -> DeployResult:
        """Non-interactive single-stage deploy (for ``--stage N``)."""
        _print = print_fn or self._console.print

        # Load stages
        if not self._deploy_state._state["deployment_stages"]:
            build_path = Path(self._context.project_dir) / ".prototype" / "state" / "build.yaml"
            if not self._deploy_state.load_from_build_state(build_path):
                _print("  No build state found. Run 'az prototype build' first.")
                return DeployResult(cancelled=True)

        self._resolve_context(subscription, tenant, client_id, client_secret)

        stage = self._deploy_state.get_stage(stage_num)
        if not stage:
            _print(f"  Stage {stage_num} not found.")
            return DeployResult(cancelled=True)

        _print(f"  Deploying Stage {stage_num}: {stage['name']}...")

        result = self._deploy_single_stage(stage)

        if result.get("status") == "deployed":
            _print(f"  Stage {stage_num} deployed successfully.")

            # Capture outputs for infra stages
            if stage.get("category") in ("infra", "data", "integration"):
                self._capture_stage_outputs(stage)
        else:
            _print(f"  Stage {stage_num} failed: {result.get('error', 'unknown error')}")

        return self._build_result()

    # ------------------------------------------------------------------ #
    # Internal — Preflight checks
    # ------------------------------------------------------------------ #

    def _run_preflight(self) -> list[dict[str, Any]]:
        """Run all preflight checks using cached deployment context."""
        results: list[dict[str, Any]] = []
        results.append(self._check_subscription(self._subscription))
        if self._tenant:
            results.append(self._check_tenant(self._tenant))
        results.append(self._check_iac_tool())
        if self._resource_group:
            results.append(self._check_resource_group(self._subscription, self._resource_group))
        results.extend(self._check_resource_providers(self._subscription))
        if self._iac_tool == "terraform":
            results.extend(self._check_terraform_validate())
        return results

    def _check_subscription(self, subscription: str) -> dict[str, str]:
        """Verify Azure CLI login and subscription."""
        if not check_az_login():
            return {
                "name": "Azure Login",
                "status": "fail",
                "message": "Not logged into Azure CLI.",
                "fix_command": "az login",
            }

        if subscription:
            current = get_current_subscription()
            if current and current != subscription:
                return {
                    "name": "Subscription",
                    "status": "warn",
                    "message": f"Active subscription ({current[:8]}...) differs from target ({subscription[:8]}...).",
                    "fix_command": f"az account set --subscription {subscription}",
                }

        return {"name": "Azure Login", "status": "pass", "message": "Logged in."}

    def _check_tenant(self, tenant: str) -> dict[str, str]:
        """Verify the current Azure tenant matches the target."""
        current = get_current_tenant()
        if current and current != tenant:
            return {
                "name": "Tenant",
                "status": "warn",
                "message": f"Active tenant ({current[:8]}...) differs from target ({tenant[:8]}...).",
                "fix_command": f"az login --tenant {tenant}",
            }
        return {"name": "Tenant", "status": "pass", "message": f"Tenant: {tenant[:8]}..."}

    def _check_iac_tool(self) -> dict[str, str]:
        """Check if the IaC tool is available."""
        if self._iac_tool == "terraform":
            try:
                result = subprocess.run(
                    ["terraform", "--version"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    version = result.stdout.strip().split("\n")[0]
                    return {"name": "Terraform", "status": "pass", "message": version}
            except FileNotFoundError:
                pass
            return {
                "name": "Terraform",
                "status": "fail",
                "message": "terraform not found on PATH.",
                "fix_command": "brew install terraform  # or https://developer.hashicorp.com/terraform/install",
            }
        else:
            # Bicep uses az CLI which we already checked
            return {"name": "Bicep (via az CLI)", "status": "pass", "message": "Available via az CLI."}

    def _check_resource_group(self, subscription: str, resource_group: str) -> dict[str, str]:
        """Check if the target resource group exists."""
        try:
            cmd = [_az(), "group", "show", "--name", resource_group]
            if subscription:
                cmd.extend(["--subscription", subscription])

            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                return {"name": "Resource Group", "status": "pass", "message": f"'{resource_group}' exists."}
        except FileNotFoundError:
            pass

        location = self._config.get("project.location", "eastus")
        return {
            "name": "Resource Group",
            "status": "warn",
            "message": f"'{resource_group}' not found. Will be created during deployment.",
            "fix_command": f"az group create --name {resource_group} --location {location}",
        }

    def _extract_providers_from_files(self) -> set[str]:
        """Extract Microsoft.* resource provider namespaces from generated IaC files.

        Parses .tf files for azapi_resource type declarations and .bicep files
        for resource type declarations. Returns distinct Microsoft.* namespaces.
        """
        import re as _re

        namespaces: set[str] = set()

        # Patterns for extracting resource types
        # Terraform azapi: type = "Microsoft.Storage/storageAccounts@2025-06-01"
        tf_pattern = _re.compile(r'type\s*=\s*"(Microsoft\.[^"/@]+)/[^"]+@[^"]+"')
        # Bicep: resource foo 'Microsoft.Storage/storageAccounts@2025-06-01' = {
        bicep_pattern = _re.compile(r"resource\s+\w+\s+'(Microsoft\.[^'/@]+)/[^']+@[^']+'")

        for stage in self._deploy_state._state.get("deployment_stages", []):
            stage_dir = Path(self._context.project_dir) / stage.get("dir", "")
            if not stage_dir.is_dir():
                continue

            # Scan .tf files
            for tf_file in stage_dir.glob("*.tf"):
                try:
                    content = tf_file.read_text()
                    for m in tf_pattern.finditer(content):
                        namespaces.add(m.group(1))
                except OSError:
                    continue

            # Scan .bicep files
            for bicep_file in stage_dir.glob("*.bicep"):
                try:
                    content = bicep_file.read_text()
                    for m in bicep_pattern.finditer(content):
                        namespaces.add(m.group(1))
                except OSError:
                    continue

        return namespaces

    def _check_resource_providers(self, subscription: str) -> list[dict[str, str]]:
        """Check if required resource providers are registered."""
        # First try: extract from actual generated files (authoritative)
        namespaces = self._extract_providers_from_files()

        # Fallback: use service metadata from deployment plan
        if not namespaces:
            for stage in self._deploy_state._state.get("deployment_stages", []):
                for svc in stage.get("services", []):
                    rt = svc.get("resource_type", "")
                    if rt and "/" in rt:
                        ns = rt.split("/")[0]
                        if ns.startswith("Microsoft."):
                            namespaces.add(ns)

        if not namespaces:
            return []

        results: list[dict[str, str]] = []
        for ns in sorted(namespaces):
            try:
                cmd = [_az(), "provider", "show", "-n", ns, "--query", "registrationState", "-o", "tsv"]
                if subscription:
                    cmd.extend(["--subscription", subscription])

                result = subprocess.run(cmd, capture_output=True, text=True, check=False)
                state = result.stdout.strip()

                if state == "Registered":
                    results.append(
                        {
                            "name": f"Provider {ns}",
                            "status": "pass",
                            "message": "Registered.",
                        }
                    )
                else:
                    results.append(
                        {
                            "name": f"Provider {ns}",
                            "status": "warn",
                            "message": f"State: {state or 'unknown'}. May need registration.",
                            "fix_command": f"az provider register -n {ns}",
                        }
                    )
            except FileNotFoundError:
                break  # az CLI not found, already caught above

        return results

    def _check_terraform_validate(self) -> list[dict[str, str]]:
        """Validate Terraform syntax for all infrastructure stages before deployment."""
        results: list[dict[str, str]] = []
        for stage in self._deploy_state._state.get("deployment_stages", []):
            if stage.get("category") not in ("infra", "data", "integration"):
                continue
            stage_dir = Path(self._context.project_dir) / stage.get("dir", "")
            if not stage_dir.is_dir():
                continue
            tf_files = list(stage_dir.glob("*.tf"))
            if not tf_files:
                continue
            # Quick init + validate (no backend, no real provider download if cached)
            init = subprocess.run(
                ["terraform", "init", "-backend=false", "-input=false", "-no-color"],
                capture_output=True,
                text=True,
                cwd=str(stage_dir),
                check=False,
            )
            if init.returncode != 0:
                results.append(
                    {
                        "name": f"Terraform Validate (Stage {stage['stage']})",
                        "status": "fail",
                        "message": f"Init failed: {(init.stderr or init.stdout).strip()[:200]}",
                    }
                )
                continue
            val = subprocess.run(
                ["terraform", "validate", "-no-color"],
                capture_output=True,
                text=True,
                cwd=str(stage_dir),
                check=False,
            )
            if val.returncode != 0:
                error = (val.stderr or val.stdout).strip()[:200]
                results.append(
                    {
                        "name": f"Terraform Validate (Stage {stage['stage']})",
                        "status": "fail",
                        "message": error,
                    }
                )
            else:
                results.append(
                    {
                        "name": f"Terraform Validate (Stage {stage['stage']})",
                        "status": "pass",
                        "message": "Syntax valid.",
                    }
                )
        return results

    # ------------------------------------------------------------------ #
    # Internal — Stage deployment
    # ------------------------------------------------------------------ #

    def _deploy_pending_stages(
        self,
        force: bool,
        use_styled: bool,
        _print: Callable[[str], None],
        _input: Callable[[str], str],
    ) -> None:
        """Deploy all pending stages sequentially."""
        pending = self._deploy_state.get_pending_stages()
        total = len(self._deploy_state._state["deployment_stages"])
        deployed_count = len(self._deploy_state.get_deployed_stages())

        if not pending:
            _print("  All stages already deployed.")
            return

        for stage in pending:
            stage_num = stage["stage"]
            stage_name = stage["name"]
            category = stage.get("category", "infra")

            deployed_count += 1
            services = stage.get("services", [])
            svc_names = [s.get("computed_name") or s.get("name", "") for s in services]
            svc_display = ", ".join(svc_names[:3])
            if len(svc_names) > 3:
                svc_display += f" (+{len(svc_names) - 3} more)"

            _print(f"  [{deployed_count}/{total}] Stage {stage_num}: {stage_name}")
            if svc_display:
                _print(f"         Resources: {svc_display}")

            with self._maybe_spinner(f"Deploying Stage {stage_num}: {stage_name}...", use_styled):
                result = self._deploy_single_stage(stage)

            if result.get("status") == "deployed":
                _print("         Deployed successfully.")

                # Capture outputs after infra stages
                if category in ("infra", "data", "integration"):
                    self._capture_stage_outputs(stage)
            elif result.get("status") == "failed":
                _print(f"         Failed: {result.get('error', 'unknown error')[:120]}")
                self._handle_deploy_failure(stage, result, use_styled, _print, _input)
                break  # Stop sequential deployment — user decides via interactive loop
            else:
                _print(f"         Skipped: {result.get('reason', 'no action needed')}")

            _print("")

    def _deploy_single_stage(self, stage: dict[str, Any]) -> dict[str, Any]:
        """Deploy one stage and update state."""
        stage_num = stage["stage"]
        category = stage.get("category", "infra")
        stage_dir = Path(self._context.project_dir) / stage.get("dir", "")

        if not stage_dir.is_dir():
            return {"status": "skipped", "reason": f"Directory not found: {stage.get('dir', '?')}"}

        # Snapshot before deploy
        self._rollback_mgr.snapshot_stage(stage_num, category, self._iac_tool)
        self._deploy_state.mark_stage_deploying(stage_num)

        # Resolve generated secrets for Terraform stages (TF_VAR_* env vars)
        stage_env = self._deploy_env
        if self._iac_tool == "terraform":
            generated = resolve_stage_secrets(stage_dir, self._config)
            if generated:
                stage_env = dict(self._deploy_env) if self._deploy_env else {}
                stage_env.update(generated)

        # Dispatch by category
        if category in ("infra", "data", "integration"):
            if self._iac_tool == "terraform":
                result = deploy_terraform(stage_dir, self._subscription, env=stage_env)
            else:
                result = deploy_bicep(stage_dir, self._subscription, self._resource_group, env=self._deploy_env)
        elif category in ("app", "schema", "cicd", "external"):
            result = deploy_app_stage(stage_dir, self._subscription, self._resource_group, env=self._deploy_env)
        elif category == "docs":
            # Documentation stages don't deploy — mark as deployed
            self._deploy_state.mark_stage_deployed(stage_num)
            self._deploy_state.save()
            return {"status": "deployed"}
        else:
            # Unknown category — try IaC
            if self._iac_tool == "terraform":
                result = deploy_terraform(stage_dir, self._subscription, env=stage_env)
            else:
                result = deploy_bicep(stage_dir, self._subscription, self._resource_group, env=self._deploy_env)

        # Update state based on result
        if result.get("status") == "deployed":
            output = result.get("deployment_output", "")
            self._deploy_state.mark_stage_deployed(stage_num, output)
            self._deploy_state.save()
        elif result.get("status") == "failed":
            self._deploy_state.mark_stage_failed(stage_num, result.get("error", ""))
            self._deploy_state.save()
        # "skipped" doesn't change state

        return result

    # ------------------------------------------------------------------ #
    # Internal — Output capture
    # ------------------------------------------------------------------ #

    def _capture_stage_outputs(self, stage: dict[str, Any]) -> None:
        """Capture Terraform/Bicep outputs after a successful stage deploy."""
        stage_dir = Path(self._context.project_dir) / stage.get("dir", "")

        if self._iac_tool == "terraform":
            outputs = self._output_capture.capture_terraform(stage_dir)
        else:
            deploy_output = stage.get("deploy_output", "")
            outputs = self._output_capture.capture_bicep(deploy_output) if deploy_output else {}

        if outputs:
            self._deploy_state._state["captured_outputs"] = self._output_capture.get_all()
            self._deploy_state.save()

    # ------------------------------------------------------------------ #
    # Internal — QA error routing
    # ------------------------------------------------------------------ #

    def _handle_deploy_failure(
        self,
        stage: dict[str, Any],
        result: dict[str, Any],
        use_styled: bool,
        _print: Callable[[str], None],
        _input: Callable[[str], str],
    ) -> None:
        """Route deployment failure to QA agent for diagnosis."""
        error_text = result.get("error", "Unknown error")
        stage_info = f"Stage {stage['stage']}: {stage['name']}"

        services = stage.get("services", [])
        svc_names = [s.get("name", "") for s in services if s.get("name")]

        qa_result = route_error_to_qa(
            error_text,
            f"Deploy {stage_info}",
            self._qa_agent,
            self._context,
            self._token_tracker,
            _print,
            services=svc_names,
            escalation_tracker=self._escalation_tracker,
            source_agent="deploy-session",
            source_stage="deploy",
        )

        if not qa_result["diagnosed"]:
            _print("")
            _print(f"  Error: {error_text[:500]}")

        if use_styled and qa_result.get("response"):
            self._console.print_token_status(self._token_tracker.format_status())

        _print("")
        _print("  Options: /deploy (retry) | /rollback (undo) | /help | quit")

    # ------------------------------------------------------------------ #
    # Internal — Rollback
    # ------------------------------------------------------------------ #

    def _rollback_stage(
        self,
        stage_num: int,
        _print: Callable[[str], None],
    ) -> bool:
        """Roll back a single deployed stage. Returns True on success."""
        if not self._deploy_state.can_rollback(stage_num):
            higher = [
                s["stage"]
                for s in self._deploy_state._state["deployment_stages"]
                if s["stage"] > stage_num and s.get("deploy_status") == "deployed"
            ]
            _print(
                f"  Cannot roll back Stage {stage_num} — "
                f"Stage(s) {', '.join(str(s) for s in higher)} still deployed."
            )
            _print("  Roll back those stages first.")
            return False

        stage = self._deploy_state.get_stage(stage_num)
        if not stage:
            _print(f"  Stage {stage_num} not found.")
            return False

        if stage.get("deploy_status") != "deployed":
            _print(f"  Stage {stage_num} is not deployed (status: {stage.get('deploy_status')}).")
            return False

        stage_dir = Path(self._context.project_dir) / stage.get("dir", "")
        category = stage.get("category", "infra")

        _print(f"  Rolling back Stage {stage_num}: {stage['name']}...")

        if category in ("infra", "data", "integration"):
            if self._iac_tool == "terraform":
                result = rollback_terraform(stage_dir, env=self._deploy_env)
            else:
                result = rollback_bicep(stage_dir, self._subscription, self._resource_group, env=self._deploy_env)
        else:
            # App stages — no automated rollback, mark as rolled back
            result = {"status": "rolled_back"}

        if result.get("status") == "rolled_back":
            self._deploy_state.mark_stage_rolled_back(stage_num)
            _print(f"  Stage {stage_num} rolled back.")
            return True
        else:
            _print(f"  Rollback failed: {result.get('error', 'unknown error')[:200]}")
            return False

    def _rollback_all(
        self,
        _print: Callable[[str], None],
        _input: Callable[[str], str],
    ) -> None:
        """Roll back all deployed stages in reverse order."""
        candidates = self._deploy_state.get_rollback_candidates()
        if not candidates:
            _print("  No deployed stages to roll back.")
            return

        _print(f"  Rolling back {len(candidates)} stage(s) in reverse order...")
        _print("")

        for stage in candidates:
            stage_num = stage["stage"]
            _print(f"  Roll back Stage {stage_num}: {stage['name']}? (Y/n)")
            try:
                answer = _input("  > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                _print("  Rollback cancelled.")
                return

            if answer in ("n", "no"):
                _print(f"  Skipping Stage {stage_num}. Stopping rollback.")
                return  # Must stop — can't skip and continue in reverse order

            self._rollback_stage(stage_num, _print)
            _print("")

    # ------------------------------------------------------------------ #
    # Internal — Slash commands
    # ------------------------------------------------------------------ #

    def _handle_slash_command(
        self,
        command_line: str,
        force: bool,
        use_styled: bool,
        _print: Callable[[str], None],
        _input: Callable[[str], str],
    ) -> None:
        """Parse and dispatch slash commands."""
        parts = command_line.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/status", "/stages"):
            _print("")
            _print(self._deploy_state.format_stage_status())
            _print("")

        elif cmd == "/deploy":
            _print("")
            if arg == "all" or not arg:
                self._deploy_pending_stages(force, use_styled, _print, _input)
            else:
                try:
                    stage_num = int(arg)
                    stage = self._deploy_state.get_stage(stage_num)
                    if not stage:
                        _print(f"  Stage {stage_num} not found.")
                    elif stage.get("deploy_status") == "deployed":
                        _print(f"  Stage {stage_num} already deployed. Use /redeploy {stage_num}.")
                    else:
                        with self._maybe_spinner(f"Deploying Stage {stage_num}...", use_styled):
                            result = self._deploy_single_stage(stage)
                        if result.get("status") == "deployed":
                            _print(f"  Stage {stage_num} deployed successfully.")
                            if stage.get("category") in ("infra", "data", "integration"):
                                self._capture_stage_outputs(stage)
                        else:
                            _print(f"  Stage {stage_num} failed: {result.get('error', '?')[:120]}")
                except ValueError:
                    _print(f"  Invalid stage number: {arg}")
            _print("")

        elif cmd == "/rollback":
            _print("")
            if arg == "all" or not arg:
                self._rollback_all(_print, _input)
            else:
                try:
                    stage_num = int(arg)
                    self._rollback_stage(stage_num, _print)
                except ValueError:
                    _print(f"  Invalid stage number: {arg}")
            _print("")

        elif cmd == "/redeploy":
            _print("")
            if not arg:
                _print("  Usage: /redeploy N")
            else:
                try:
                    stage_num = int(arg)
                    stage = self._deploy_state.get_stage(stage_num)
                    if not stage:
                        _print(f"  Stage {stage_num} not found.")
                    else:
                        # Rollback first if deployed
                        if stage.get("deploy_status") == "deployed":
                            success = self._rollback_stage(stage_num, _print)
                            if not success:
                                _print("  Rollback failed. Cannot redeploy.")
                                return

                        # Reset status to pending and redeploy
                        stage["deploy_status"] = "pending"
                        self._deploy_state.save()

                        with self._maybe_spinner(f"Redeploying Stage {stage_num}...", use_styled):
                            result = self._deploy_single_stage(stage)

                        if result.get("status") == "deployed":
                            _print(f"  Stage {stage_num} redeployed successfully.")
                            if stage.get("category") in ("infra", "data", "integration"):
                                self._capture_stage_outputs(stage)
                        else:
                            _print(f"  Stage {stage_num} failed: {result.get('error', '?')[:120]}")
                except ValueError:
                    _print(f"  Invalid stage number: {arg}")
            _print("")

        elif cmd == "/plan":
            _print("")
            if not arg:
                _print("  Usage: /plan N")
            else:
                try:
                    stage_num = int(arg)
                    stage = self._deploy_state.get_stage(stage_num)
                    if not stage:
                        _print(f"  Stage {stage_num} not found.")
                    else:
                        stage_dir = Path(self._context.project_dir) / stage.get("dir", "")
                        if not stage_dir.is_dir():
                            _print(f"  Directory not found: {stage.get('dir', '?')}")
                        elif stage.get("category") in ("infra", "data", "integration"):
                            with self._maybe_spinner(f"Running plan for Stage {stage_num}...", use_styled):
                                if self._iac_tool == "terraform":
                                    plan_env = self._deploy_env
                                    generated = resolve_stage_secrets(stage_dir, self._config)
                                    if generated:
                                        plan_env = dict(self._deploy_env) if self._deploy_env else {}
                                        plan_env.update(generated)
                                    result = plan_terraform(stage_dir, self._subscription, env=plan_env)
                                else:
                                    result = whatif_bicep(
                                        stage_dir,
                                        self._subscription,
                                        self._resource_group,
                                        env=self._deploy_env,
                                    )
                            if result.get("output"):
                                _print(result["output"])
                            if result.get("error"):
                                _print(f"  Error: {result['error']}")
                        else:
                            _print(f"  Stage {stage_num} is an app stage — no plan preview.")
                except ValueError:
                    _print(f"  Invalid stage number: {arg}")
            _print("")

        elif cmd == "/outputs":
            _print("")
            _print(self._deploy_state.format_outputs())
            _print("")

        elif cmd == "/preflight":
            _print("")
            with self._maybe_spinner("Re-running preflight checks...", use_styled):
                preflight = self._run_preflight()
            self._deploy_state.set_preflight_results(preflight)
            _print(self._deploy_state.format_preflight_report())
            _print("")

        elif cmd == "/login":
            _print("")
            _print("  Running az login...")
            try:
                result = subprocess.run(
                    [_az(), "login"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    _print("  Login successful.")
                    _print("  Use /preflight to verify your session.")
                else:
                    error = result.stderr.strip() or result.stdout.strip()
                    _print(f"  Login failed: {error[:200]}")
            except FileNotFoundError:
                _print("  az CLI not found on PATH.")
            _print("")

        elif cmd == "/help":
            _print("")
            _print("  Available commands:")
            _print("    /status       - Show deployment progress per stage")
            _print("    /stages       - List all stages with status (alias)")
            _print("    /deploy [N]   - Deploy stage N or all pending stages")
            _print("    /rollback [N] - Roll back stage N or all (reverse order)")
            _print("    /redeploy N   - Rollback + redeploy stage N")
            _print("    /plan N       - Show what-if/terraform plan for stage N")
            _print("    /outputs      - Show captured deployment outputs")
            _print("    /preflight    - Re-run preflight checks")
            _print("    /login        - Run az login interactively")
            _print("    /help         - Show this help")
            _print("    done          - Accept deployment and exit")
            _print("    quit          - Exit deploy session")
            _print("")

        else:
            _print(f"  Unknown command: {cmd}. Type /help for a list.")

    # ------------------------------------------------------------------ #
    # Internal — utilities
    # ------------------------------------------------------------------ #

    def _build_result(self) -> DeployResult:
        """Build a DeployResult from the current state."""
        return DeployResult(
            deployed_stages=self._deploy_state.get_deployed_stages(),
            failed_stages=self._deploy_state.get_failed_stages(),
            rolled_back_stages=[
                s for s in self._deploy_state._state["deployment_stages"] if s.get("deploy_status") == "rolled_back"
            ],
            captured_outputs=self._deploy_state._state.get("captured_outputs", {}),
        )

    @contextmanager
    def _maybe_spinner(self, message: str, use_styled: bool) -> Iterator[None]:
        """Show a spinner when using styled output, otherwise no-op."""
        if use_styled:
            with self._console.spinner(message):
                yield
        else:
            yield
