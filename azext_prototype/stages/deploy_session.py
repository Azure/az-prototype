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
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from azext_prototype.agents.base import AgentCapability, AgentContext
from azext_prototype.agents.registry import AgentRegistry
from azext_prototype.ai.token_tracker import TokenTracker
from azext_prototype.config import ProjectConfig
from azext_prototype.parsers.file_extractor import parse_file_blocks, write_parsed_files
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
from azext_prototype.stages.intent import IntentKind, build_deploy_classifier
from azext_prototype.stages.qa_router import route_error_to_qa
from azext_prototype.tracking import ChangeTracker
from azext_prototype.ui.console import Console, DiscoveryPrompt
from azext_prototype.ui.console import console as default_console

logger = logging.getLogger(__name__)

# Maximum auto-remediation cycles per stage before falling through to interactive
_MAX_DEPLOY_REMEDIATION_ATTEMPTS = 2

# Files that should never be written for each IaC tool.
_BLOCKED_FILES: dict[str, set[str]] = {
    "terraform": {"versions.tf"},
}


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
        "/describe",
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
        status_fn: Any = None,
    ) -> None:
        self._context = agent_context
        self._registry = registry
        self._console = console or default_console
        self._status_fn = status_fn
        self._prompt = DiscoveryPrompt(self._console)
        self._deploy_state = deploy_state or DeployState(agent_context.project_dir)

        # Resolve QA agent for error routing
        qa_agents = registry.find_by_capability(AgentCapability.QA)
        self._qa_agent = qa_agents[0] if qa_agents else None

        # Resolve IaC, dev, and architect agents for remediation
        self._iac_agents: dict[str, Any] = {}
        for cap, key in [(AgentCapability.TERRAFORM, "terraform"), (AgentCapability.BICEP, "bicep")]:
            agents = registry.find_by_capability(cap)
            if agents:
                self._iac_agents[key] = agents[0]

        dev_agents = registry.find_by_capability(AgentCapability.DEVELOP)
        self._dev_agent = dev_agents[0] if dev_agents else None

        architect_agents = registry.find_by_capability(AgentCapability.ARCHITECT)
        self._architect_agent = architect_agents[0] if architect_agents else None

        # Escalation tracker
        self._escalation_tracker = EscalationTracker(agent_context.project_dir)
        if self._escalation_tracker.exists:
            self._escalation_tracker.load()

        # Project config
        config = ProjectConfig(agent_context.project_dir)
        config.load()
        self._config = config
        self._iac_tool: str = config.get("project.iac_tool", "terraform")

        # Token tracker — auto-pushes status to UI after every AI call
        self._token_tracker = TokenTracker()
        if self._status_fn:
            self._token_tracker._on_update = lambda text: self._status_fn(text, "tokens")
        elif self._console:
            self._token_tracker._on_update = self._console.print_token_status

        # Intent classifier for natural language command detection
        self._intent_classifier = build_deploy_classifier(
            ai_provider=agent_context.ai_provider,
            token_tracker=self._token_tracker,
        )

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
        build_path = Path(self._context.project_dir) / ".prototype" / "state" / "build.yaml"
        if not self._deploy_state._state["deployment_stages"]:
            if not self._deploy_state.load_from_build_state(build_path):
                _print("  No build state found. Run 'az prototype build' first.")
                return DeployResult(cancelled=True)
        else:
            # Re-entry: sync with latest build state
            sync = self._deploy_state.sync_from_build_state(build_path)
            if sync.created or sync.orphaned or sync.updated_code:
                _print("")
                _print("  Build state changed since last deploy:")
                for detail in sync.details:
                    _print(f"    - {detail}")
                if sync.updated_code:
                    _print(f"    - {sync.updated_code} deployed stage(s) have updated code")
                _print("")

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

            # Natural language intent detection
            intent = self._intent_classifier.classify(user_input)
            if intent.kind == IntentKind.COMMAND:
                # Multi-stage support: "deploy stages 3 and 4"
                import re as _re

                numbers = _re.findall(r"\d+", intent.args) if intent.args else []
                if len(numbers) > 1 and intent.command in ("/deploy", "/rollback"):
                    for num in numbers:
                        self._handle_slash_command(
                            f"{intent.command} {num}",
                            force,
                            use_styled,
                            _print,
                            _input,
                        )
                else:
                    cmd_line = f"{intent.command} {intent.args}".strip()
                    self._handle_slash_command(
                        cmd_line,
                        force,
                        use_styled,
                        _print,
                        _input,
                    )
                continue

            _print("  Type /help for commands, or use natural language (e.g. 'deploy stage 3').")

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

            # Attempt non-interactive remediation
            remediated = self._remediate_deploy_failure(
                stage,
                result,
                False,
                _print,
                lambda p: "",
            )
            if remediated and remediated.get("status") == "deployed":
                _print(f"  Stage {stage_num} deployed after remediation.")

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
            elif result.get("status") == "awaiting_manual":
                instructions = result.get("instructions", "No instructions provided.")
                _print("         Manual step required:")
                _print(f"           {instructions}")
                _print("")
                _print("         When complete, enter: Done / Skip / Need help")
                try:
                    answer = _input("  > ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    _print("         Skipped.")
                    continue
                if answer in ("done", "d", "yes", "y"):
                    self._deploy_state.mark_stage_deployed(stage_num)
                    _print("         Marked as deployed.")
                elif answer in ("skip", "s"):
                    _print("         Skipped. Use /deploy to continue later.")
                else:
                    _print("         Pausing deployment. Use /deploy to continue.")
                    break
            elif result.get("status") == "failed":
                _print(f"         Failed: {result.get('error', 'unknown error')[:120]}")
                remediated = self._handle_deploy_failure(stage, result, use_styled, _print, _input)
                if remediated.get("status") != "deployed":
                    break  # Stop sequential deployment — user decides via interactive loop
            else:
                _print(f"         Skipped: {result.get('reason', 'no action needed')}")

            _print("")

    def _deploy_single_stage(self, stage: dict[str, Any]) -> dict[str, Any]:
        """Deploy one stage and update state."""
        stage_num = stage["stage"]
        category = stage.get("category", "infra")
        deploy_mode = stage.get("deploy_mode", "auto")

        # Manual steps don't execute — they return a special status
        if deploy_mode == "manual":
            self._deploy_state.mark_stage_awaiting_manual(stage_num)
            return {
                "status": "awaiting_manual",
                "instructions": stage.get("manual_instructions", "No instructions provided."),
            }

        stage_dir = Path(self._context.project_dir) / stage.get("dir", "")

        if not stage_dir.is_dir():
            return {"status": "skipped", "reason": f"Directory not found: {stage.get('dir', '?')}"}

        # Snapshot before deploy
        build_stage_id = stage.get("build_stage_id")
        self._rollback_mgr.snapshot_stage(stage_num, category, self._iac_tool, build_stage_id=build_stage_id)
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
    ) -> dict[str, Any]:
        """Attempt auto-remediation of a deploy failure, falling through to interactive options.

        Returns the final deploy result for the stage (may be ``"deployed"`` if
        remediation succeeds, or the original failure if remediation is exhausted
        or unavailable).
        """
        # Attempt auto-remediation when agents are available
        remediated = self._remediate_deploy_failure(stage, result, use_styled, _print, _input)
        if remediated and remediated.get("status") == "deployed":
            return remediated

        # Remediation not attempted — fall back to QA-only diagnosis
        if remediated is None:
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

        # Show interactive options
        _print("")
        _print("  Options: /deploy (retry) | /rollback (undo) | /help | quit")
        return remediated or result

    # ------------------------------------------------------------------ #
    # Internal — Deploy failure remediation
    # ------------------------------------------------------------------ #

    def _remediate_deploy_failure(
        self,
        stage: dict[str, Any],
        result: dict[str, Any],
        use_styled: bool,
        _print: Callable[[str], None],
        _input: Callable[[str], str],
    ) -> dict[str, Any] | None:
        """Closed-loop remediation: QA diagnoses -> architect guides -> IaC/dev fixes -> redeploy.

        Returns the final deploy result, or ``None`` if remediation cannot be
        attempted (no agents / no AI provider).
        """
        # Guard: need at minimum QA + one fix agent + AI provider
        has_fix_agent = bool(self._iac_agents.get(self._iac_tool) or self._dev_agent)
        if not self._qa_agent or not has_fix_agent or not self._context.ai_provider:
            return None

        error_text = result.get("error", "Unknown error")
        stage_num = stage["stage"]
        stage_info = f"Stage {stage_num}: {stage['name']}"
        services = stage.get("services", [])
        svc_names = [s.get("name", "") for s in services if s.get("name")]
        final_result = result

        for attempt in range(1, _MAX_DEPLOY_REMEDIATION_ATTEMPTS + 1):
            current_attempts = stage.get("remediation_attempts", 0)
            if current_attempts >= _MAX_DEPLOY_REMEDIATION_ATTEMPTS:
                _print(f"  Auto-remediation exhausted ({current_attempts} attempts) for {stage_info}.")
                break

            # 1. QA diagnosis
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
                break

            qa_diagnosis = qa_result.get("content", "")

            # 2. Architect fix guidance
            architect_guidance = self._get_architect_fix_guidance(stage, error_text, qa_diagnosis)

            # 3. Mark stage as remediating
            self._deploy_state.mark_stage_remediating(stage_num)
            _print(f"  Remediating {stage_info} (attempt {attempt})...")

            # 4. Build and execute fix task
            agent, task = self._build_fix_task(stage, error_text, qa_diagnosis, architect_guidance)
            if not agent:
                _print("  No suitable agent available for remediation.")
                break

            with self._maybe_spinner(f"Fixing {stage_info}...", use_styled):
                try:
                    fix_response = agent.execute(self._context, task)
                except Exception:
                    logger.debug("Fix agent failed during remediation", exc_info=True)
                    _print("  Fix agent encountered an error.")
                    break

            if fix_response:
                self._token_tracker.record(fix_response)

            fix_content = fix_response.content if fix_response else ""
            if not fix_content:
                _print("  Fix agent returned no content.")
                break

            # 5. Write fixed files
            written = self._write_stage_files(stage, fix_content)
            if written:
                _print(f"  Wrote {len(written)} file(s): {', '.join(Path(f).name for f in written[:5])}")
            else:
                _print("  No file blocks found in fix response.")
                break

            # 6. Check downstream impact
            downstream = self._check_downstream_impact(stage, architect_guidance)

            # 7. Reset and re-deploy
            self._deploy_state.reset_stage_to_pending(stage_num)

            with self._maybe_spinner(f"Re-deploying {stage_info}...", use_styled):
                final_result = self._deploy_single_stage(stage)

            if final_result.get("status") == "deployed":
                _print(f"  {stage_info} deployed successfully after remediation.")

                # Capture outputs for infra stages
                if stage.get("category") in ("infra", "data", "integration"):
                    self._capture_stage_outputs(stage)

                # Regenerate downstream stages if needed
                if downstream:
                    self._regenerate_downstream_stages(downstream, use_styled, _print)

                return final_result

            # Failed again — loop for next attempt
            error_text = final_result.get("error", "unknown error")
            _print(f"  Re-deploy failed: {error_text[:120]}")

        return final_result

    def _collect_stage_file_content(self, stage: dict, max_bytes: int = 20_000) -> str:
        """Collect content of generated files for a single deploy stage.

        Falls back to globbing the stage directory when the ``files`` list is
        empty (deploy stages may not always have files tracked).
        """
        project_root = Path(self._context.project_dir)
        parts: list[str] = []
        total = 0

        files = stage.get("files", [])

        # Fallback: glob the stage directory for common IaC/app file types
        if not files:
            stage_dir = project_root / stage.get("dir", "")
            if stage_dir.is_dir():
                for pattern in ("*.tf", "*.bicep", "*.sh", "*.py", "*.cs", "*.json", "*.yaml"):
                    for f in stage_dir.glob(pattern):
                        try:
                            rel = str(f.relative_to(project_root))
                            files.append(rel)
                        except ValueError:
                            files.append(str(f))

        if not files:
            return ""

        for filepath in files:
            if total >= max_bytes:
                parts.append("\n(remaining files omitted — size cap reached)")
                break

            full_path = project_root / filepath
            try:
                content = full_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                parts.append(f"```{filepath}\n(could not read file)\n```")
                continue

            per_file_cap = 8_000
            if len(content) > per_file_cap:
                content = content[:per_file_cap] + "\n... (truncated)"

            block = f"```{filepath}\n{content}\n```"
            total += len(block)
            parts.append(block)

        return "\n\n".join(parts)

    def _build_fix_task(
        self,
        stage: dict,
        deploy_error: str,
        qa_diagnosis: str,
        architect_guidance: str,
    ) -> tuple[Any | None, str]:
        """Build a fix prompt for the IaC/dev agent and select the appropriate agent.

        Returns ``(agent, task_prompt)`` or ``(None, "")`` when no suitable
        agent is available.
        """
        category = stage.get("category", "infra")

        # Select agent based on category (mirrors BuildSession._build_stage_task)
        if category in ("infra", "data", "integration"):
            agent = self._iac_agents.get(self._iac_tool)
        elif category in ("app", "schema", "cicd", "external"):
            agent = self._dev_agent
        else:
            agent = self._iac_agents.get(self._iac_tool) or self._dev_agent

        if not agent:
            return None, ""

        # Collect current stage files
        file_content = self._collect_stage_file_content(stage, max_bytes=20_000)

        # Service list
        services = stage.get("services", [])
        svc_lines = "\n".join(
            f"- {s.get('computed_name') or s.get('name', '?')}: "
            f"{s.get('resource_type', 'N/A')} (SKU: {s.get('sku') or 'n/a'})"
            for s in services
        )

        stage_dir = stage.get("dir", "concept")

        task = (
            f"Fix deployment Stage {stage['stage']}: {stage['name']}.\n\n"
            f"The deployment FAILED with the following error. You MUST fix ALL issues.\n\n"
            f"## Deploy Error\n```\n{deploy_error[:3000]}\n```\n\n"
            f"## QA Diagnosis\n{qa_diagnosis[:2000]}\n\n"
            f"## Architect Guidance\n{architect_guidance[:2000]}\n\n"
        )

        if file_content:
            task += f"## Current Stage Files\n{file_content}\n\n"

        if svc_lines:
            task += f"## Services in This Stage\n{svc_lines}\n\n"

        task += (
            f"## Requirements\n"
            f"- Fix ALL issues identified in the error and diagnosis above\n"
            f"- Preserve all working functionality — only change what's broken\n"
            f"- All files should be relative to {stage_dir}/\n"
            f"- Output COMPLETE file contents in fenced code blocks with filenames\n"
        )

        return agent, task

    def _write_stage_files(self, stage: dict, content: str) -> list[str]:
        """Extract file blocks from AI response and write to disk.

        Returns a list of written file paths relative to the project dir.
        """
        if not content:
            return []

        files = parse_file_blocks(content)
        if not files:
            return []

        stage_dir = stage.get("dir", "concept")
        output_dir = Path(self._context.project_dir) / stage_dir
        blocked = _BLOCKED_FILES.get(self._iac_tool, set())

        # Strip stage_dir prefix from filenames to avoid path duplication
        cleaned: dict[str, str] = {}
        for filename, file_content in files.items():
            normalized = filename.replace("\\", "/")
            stage_prefix = stage_dir.replace("\\", "/")
            if normalized.startswith(stage_prefix + "/"):
                normalized = normalized[len(stage_prefix) + 1 :]
            elif normalized.startswith(stage_prefix):
                normalized = normalized[len(stage_prefix) :]
            normalized = normalized or filename

            if normalized in blocked:
                logger.info("Dropped blocked file: %s (IaC tool: %s)", normalized, self._iac_tool)
                continue

            cleaned[normalized] = file_content

        written = write_parsed_files(cleaned, output_dir, verbose=False)

        project_root = Path(self._context.project_dir)
        written_relative = [str(p.relative_to(project_root)) for p in written]

        # Sync build state with updated file list
        self._sync_build_state(stage, written_relative)

        return written_relative

    def _sync_build_state(self, stage: dict, written_paths: list[str]) -> None:
        """Best-effort sync of build.yaml after remediation writes.

        Updates the matching stage's ``files`` list and marks it as
        ``generated`` so subsequent builds stay consistent.  Uses
        ``build_stage_id`` for matching when available, falling back
        to stage number for legacy state files.
        """
        try:
            from azext_prototype.stages.build_state import BuildState

            bs = BuildState(self._context.project_dir)
            if not bs.exists:
                return
            bs.load()

            build_stage_id = stage.get("build_stage_id")
            matched = False

            if build_stage_id:
                # Match by stable ID
                target = bs.get_stage_by_id(build_stage_id)
                if target:
                    target["files"] = written_paths
                    target["status"] = "generated"
                    matched = True

            if not matched:
                # Fallback: match by stage number
                stage_num = stage["stage"]
                for build_stage in bs.state.get("deployment_stages", []):
                    if build_stage["stage"] == stage_num:
                        build_stage["files"] = written_paths
                        build_stage["status"] = "generated"
                        break

            bs.save()
        except Exception:
            logger.debug("Could not sync build state after remediation", exc_info=True)

    def _get_architect_fix_guidance(self, stage: dict, deploy_error: str, qa_diagnosis: str) -> str:
        """Ask the architect agent for specific fix guidance.

        Returns guidance text, or a generic fallback if no architect is available.
        """
        if not self._architect_agent or not self._context.ai_provider:
            return "Fix the issues identified in the QA diagnosis. Ensure all resource references are correct."

        file_content = self._collect_stage_file_content(stage, max_bytes=10_000)

        task = (
            f"A deployment stage failed. Analyse the error and QA diagnosis, "
            f"then provide SPECIFIC code changes needed to fix it.\n\n"
            f"## Stage {stage['stage']}: {stage['name']}\n"
            f"Category: {stage.get('category', 'infra')}\n\n"
            f"## Deploy Error\n```\n{deploy_error[:2000]}\n```\n\n"
            f"## QA Diagnosis\n{qa_diagnosis[:1500]}\n\n"
        )

        if file_content:
            task += f"## Current Stage Files\n{file_content}\n\n"

        task += (
            "Provide:\n"
            "1. Root cause of the failure\n"
            "2. Specific code changes needed (which files, what to change)\n"
            "3. Whether downstream stages might be affected by this fix "
            "(e.g. changed outputs, renamed resources)\n"
        )

        try:
            response = self._architect_agent.execute(self._context, task)
            if response:
                self._token_tracker.record(response)
            if response and response.content:
                return response.content
        except Exception:
            logger.debug("Architect fix guidance failed", exc_info=True)

        return "Fix the issues identified in the QA diagnosis. Ensure all resource references are correct."

    def _check_downstream_impact(self, fixed_stage: dict, architect_guidance: str) -> list[int]:
        """Ask the architect whether downstream stages need regeneration.

        Returns a list of stage numbers that should be regenerated, or
        empty list if none are affected.
        """
        if not self._architect_agent or not self._context.ai_provider:
            return []

        stages = self._deploy_state._state.get("deployment_stages", [])
        fixed_num = fixed_stage["stage"]

        # Only consider downstream stages that are pending or failed
        downstream = [s for s in stages if s["stage"] > fixed_num and s.get("deploy_status") in ("pending", "failed")]
        if not downstream:
            return []

        import json

        stage_info = json.dumps(
            [
                {
                    "stage": s["stage"],
                    "name": s["name"],
                    "category": s.get("category", ""),
                    "build_stage_id": s.get("build_stage_id", ""),
                    "services": [svc.get("name", "") for svc in s.get("services", [])],
                }
                for s in downstream
            ],
            indent=2,
        )

        task = (
            f"Stage {fixed_num} ({fixed_stage['name']}) was just fixed during deployment.\n\n"
            f"## Fix Context\n{architect_guidance[:1500]}\n\n"
            f"## Downstream Stages\n```json\n{stage_info}\n```\n\n"
            "Which downstream stages need their code regenerated because of "
            "changed outputs, renamed resources, or modified dependencies from "
            "the fix above?\n\n"
            "Return ONLY a JSON array of affected stage numbers. "
            "Return [] if no stages are affected.\n"
            "Example: [3, 4]\n"
        )

        try:
            response = self._architect_agent.execute(self._context, task)
            if response:
                self._token_tracker.record(response)
            if response and response.content:
                return self._parse_stage_numbers(response.content, downstream)
        except Exception:
            logger.debug("Downstream impact check failed", exc_info=True)

        return []

    @staticmethod
    def _parse_stage_numbers(content: str, valid_stages: list[dict]) -> list[int]:
        """Parse a JSON array of stage numbers from AI response."""
        import json

        valid_nums = {s["stage"] for s in valid_stages}

        # Try to find a JSON array in the response
        match = re.search(r"\[[\d\s,]*\]", content)
        if match:
            try:
                numbers = json.loads(match.group())
                return [n for n in numbers if isinstance(n, int) and n in valid_nums]
            except (json.JSONDecodeError, TypeError):
                pass

        # Fallback: extract individual numbers
        numbers = [int(n) for n in re.findall(r"\d+", content)]
        return [n for n in numbers if n in valid_nums]

    def _regenerate_downstream_stages(
        self,
        stage_nums: list[int],
        use_styled: bool,
        _print: Callable[[str], None],
    ) -> None:
        """Regenerate code for downstream stages affected by an upstream fix.

        Only regenerates the code (writes files) — does NOT deploy.  The
        normal deploy loop handles deployment of these stages.
        """
        if not stage_nums:
            return

        _print(f"  Regenerating {len(stage_nums)} downstream stage(s) affected by fix...")

        for num in stage_nums:
            stage = self._deploy_state.get_stage(num)
            if not stage:
                continue

            category = stage.get("category", "infra")
            if category in ("infra", "data", "integration"):
                agent = self._iac_agents.get(self._iac_tool)
            elif category in ("app", "schema", "cicd", "external"):
                agent = self._dev_agent
            else:
                agent = self._iac_agents.get(self._iac_tool) or self._dev_agent

            if not agent:
                continue

            file_content = self._collect_stage_file_content(stage, max_bytes=15_000)
            stage_dir = stage.get("dir", "concept")

            task = (
                f"Regenerate code for Stage {num}: {stage['name']}.\n\n"
                f"An upstream stage was fixed during deployment. This stage may need "
                f"updates to match changed outputs or resource references.\n\n"
            )
            if file_content:
                task += f"## Current Stage Files\n{file_content}\n\n"

            task += (
                f"## Requirements\n"
                f"- Update any references to upstream resources if they changed\n"
                f"- Preserve all existing functionality\n"
                f"- All files should be relative to {stage_dir}/\n"
                f"- Output COMPLETE file contents in fenced code blocks with filenames\n"
            )

            with self._maybe_spinner(f"Regenerating Stage {num}...", use_styled):
                try:
                    response = agent.execute(self._context, task)
                except Exception:
                    logger.debug("Downstream regeneration failed for Stage %d", num, exc_info=True)
                    _print(f"  Could not regenerate Stage {num}.")
                    continue

            if response:
                self._token_tracker.record(response)

            if response and response.content:
                written = self._write_stage_files(stage, response.content)
                if written:
                    _print(f"  Stage {num}: regenerated {len(written)} file(s).")
                else:
                    _print(f"  Stage {num}: no file blocks in regeneration response.")

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

    @staticmethod
    def _parse_stage_ref(arg: str) -> tuple[int | None, str | None]:
        """Parse a stage reference like ``"5"`` or ``"5a"``.

        Returns ``(stage_num, substage_label)`` or ``(None, None)`` on failure.
        """
        from azext_prototype.stages.deploy_state import parse_stage_ref

        return parse_stage_ref(arg)

    def _resolve_stage_from_arg(
        self, arg: str, _print: Callable[[str], None]
    ) -> tuple[dict | None, int | None, str | None]:
        """Parse a stage ref from an arg string and look it up.

        Returns ``(stage_dict, stage_num, substage_label)`` or ``(None, None, None)``.
        """
        stage_num, label = self._parse_stage_ref(arg)
        if stage_num is None:
            _print(f"  Invalid stage reference: {arg}")
            return None, None, None

        if label:
            stage = self._deploy_state.get_stage_by_display_id(arg)
        else:
            stage = self._deploy_state.get_stage(stage_num)

        if not stage:
            _print(f"  Stage {arg} not found.")
            return None, stage_num, label

        return stage, stage_num, label

    def _deploy_stage_with_substages(
        self,
        stage_num: int,
        use_styled: bool,
        _print: Callable[[str], None],
        _input: Callable[[str], str],
    ) -> None:
        """Deploy all substages of a stage number in order."""
        all_stages = self._deploy_state.get_all_stages_for_num(stage_num)
        for stage in all_stages:
            if stage.get("deploy_status") == "deployed":
                from azext_prototype.stages.deploy_state import _format_display_id

                _print(f"  Stage {_format_display_id(stage)} already deployed.")
                continue
            self._deploy_one_stage_cmd(stage, use_styled, _print, _input)

    def _deploy_one_stage_cmd(
        self,
        stage: dict,
        use_styled: bool,
        _print: Callable[[str], None],
        _input: Callable[[str], str],
    ) -> None:
        """Deploy a single stage, handling manual mode and failures."""
        from azext_prototype.stages.deploy_state import _format_display_id

        display_id = _format_display_id(stage)
        stage_num = stage["stage"]

        with self._maybe_spinner(f"Deploying Stage {display_id}...", use_styled):
            result = self._deploy_single_stage(stage)

        if result.get("status") == "deployed":
            _print(f"  Stage {display_id} deployed successfully.")
            if stage.get("category") in ("infra", "data", "integration"):
                self._capture_stage_outputs(stage)
        elif result.get("status") == "awaiting_manual":
            instructions = result.get("instructions", "No instructions provided.")
            _print(f"  Stage {display_id} requires manual action:")
            _print(f"    {instructions}")
            _print("")
            _print("  When complete, enter: Done / Skip / Need help")
            try:
                answer = _input("  > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                _print("  Skipped.")
                return
            if answer in ("done", "d", "yes", "y"):
                self._deploy_state.mark_stage_deployed(stage_num)
                _print(f"  Stage {display_id} marked as deployed.")
            elif answer in ("need help", "help", "h"):
                _print("  Ask the QA engineer for guidance by describing your issue.")
            else:
                _print(f"  Stage {display_id} skipped. Use /deploy {display_id} when ready.")
        elif result.get("status") == "failed":
            _print(f"  Stage {display_id} failed: {result.get('error', '?')[:120]}")
            self._handle_deploy_failure(stage, result, use_styled, _print, _input)
        else:
            _print(f"  Stage {display_id} skipped: {result.get('reason', 'no action needed')}")

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
                stage, stage_num, label = self._resolve_stage_from_arg(arg, _print)
                if stage:
                    assert stage_num is not None  # guaranteed when stage is resolved
                    if stage.get("deploy_status") == "deployed" and not label:
                        _print(f"  Stage {arg} already deployed. Use /redeploy {arg}.")
                    elif label:
                        # Deploy specific substage
                        self._deploy_one_stage_cmd(stage, use_styled, _print, _input)
                    else:
                        # Deploy all substages for this number
                        all_for_num = self._deploy_state.get_all_stages_for_num(stage_num)
                        if len(all_for_num) > 1:
                            self._deploy_stage_with_substages(stage_num, use_styled, _print, _input)
                        else:
                            self._deploy_one_stage_cmd(stage, use_styled, _print, _input)
            _print("")

        elif cmd == "/rollback":
            _print("")
            if arg == "all" or not arg:
                self._rollback_all(_print, _input)
            else:
                stage_num, label = self._parse_stage_ref(arg)
                if stage_num is None:
                    _print(f"  Invalid stage reference: {arg}")
                elif label:
                    # Rollback specific substage
                    if self._deploy_state.can_rollback(stage_num, label):
                        self._rollback_stage(stage_num, _print)
                    else:
                        _print(f"  Cannot rollback {arg}: later substages still deployed.")
                else:
                    # Rollback all substages in reverse
                    all_for_num = self._deploy_state.get_all_stages_for_num(stage_num)
                    if len(all_for_num) > 1:
                        for s in reversed(all_for_num):
                            if s.get("deploy_status") == "deployed":
                                self._rollback_stage(s["stage"], _print)
                    else:
                        self._rollback_stage(stage_num, _print)
            _print("")

        elif cmd == "/redeploy":
            _print("")
            if not arg:
                _print("  Usage: /redeploy N")
            else:
                stage, stage_num, label = self._resolve_stage_from_arg(arg, _print)
                if stage:
                    assert stage_num is not None  # guaranteed when stage is resolved
                    # Rollback first if deployed
                    if stage.get("deploy_status") == "deployed":
                        success = self._rollback_stage(stage_num, _print)
                        if not success:
                            _print("  Rollback failed. Cannot redeploy.")
                            return

                    stage["deploy_status"] = "pending"
                    self._deploy_state.save()

                    from azext_prototype.stages.deploy_state import _format_display_id

                    display_id = _format_display_id(stage)
                    with self._maybe_spinner(f"Redeploying Stage {display_id}...", use_styled):
                        result = self._deploy_single_stage(stage)

                    if result.get("status") == "deployed":
                        _print(f"  Stage {display_id} redeployed successfully.")
                        if stage.get("category") in ("infra", "data", "integration"):
                            self._capture_stage_outputs(stage)
                    elif result.get("status") == "awaiting_manual":
                        _print(f"  Stage {display_id} requires manual action:")
                        _print(f"    {result.get('instructions', '')}")
                    else:
                        _print(f"  Stage {display_id} failed: {result.get('error', '?')[:120]}")
                        self._handle_deploy_failure(stage, result, use_styled, _print, _input)
            _print("")

        elif cmd == "/plan":
            _print("")
            if not arg:
                _print("  Usage: /plan N")
            else:
                stage, stage_num, _label = self._resolve_stage_from_arg(arg, _print)
                if stage:
                    stage_dir = Path(self._context.project_dir) / stage.get("dir", "")
                    if stage.get("deploy_mode") == "manual":
                        _print(f"  Stage {arg} is a manual step — no plan preview.")
                    elif not stage_dir.is_dir():
                        _print(f"  Directory not found: {stage.get('dir', '?')}")
                    elif stage.get("category") in ("infra", "data", "integration"):
                        with self._maybe_spinner(f"Running plan for Stage {arg}...", use_styled):
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
                        _print(f"  Stage {arg} is an app stage — no plan preview.")
            _print("")

        elif cmd == "/split":
            _print("")
            if not arg:
                _print("  Usage: /split N")
            else:
                stage, stage_num, _label = self._resolve_stage_from_arg(arg, _print)
                if stage and stage_num is not None:
                    if stage.get("_is_substage"):
                        _print(f"  Stage {arg} is already a substage. Cannot split further.")
                    else:
                        _print(f"  Splitting Stage {stage_num}: {stage['name']}")
                        _print("  Enter names for the substages (one per line, blank line to finish):")
                        substages: list[dict] = []
                        while True:
                            try:
                                name = _input("    Name: ").strip()
                            except (EOFError, KeyboardInterrupt):
                                break
                            if not name:
                                break
                            substages.append({"name": name, "dir": stage.get("dir", "")})
                        if len(substages) >= 2:
                            self._deploy_state.split_stage(stage_num, substages)
                            _print(f"  Split into {len(substages)} substages.")
                        else:
                            _print("  Split requires at least 2 substages. Cancelled.")
            _print("")

        elif cmd == "/destroy":
            _print("")
            if not arg:
                _print("  Usage: /destroy N")
            else:
                stage, stage_num, _label = self._resolve_stage_from_arg(arg, _print)
                if stage and stage_num is not None:
                    _print(f"  Are you sure you want to destroy Stage {arg}: {stage['name']}? (y/N)")
                    try:
                        answer = _input("  > ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        _print("  Cancelled.")
                        return
                    if answer in ("y", "yes"):
                        success = self._rollback_stage(stage_num, _print)
                        if success:
                            self._deploy_state.mark_stage_destroyed(stage_num)
                            _print(f"  Stage {arg} destroyed.")
                        else:
                            _print(f"  Could not destroy Stage {arg}.")
                    else:
                        _print("  Cancelled.")
            _print("")

        elif cmd == "/manual":
            _print("")
            if not arg:
                _print('  Usage: /manual N "instructions"')
            else:
                # Parse: /manual 5 "instructions text"
                manual_parts = arg.split(maxsplit=1)
                ref = manual_parts[0]
                instructions = manual_parts[1].strip('"').strip("'") if len(manual_parts) > 1 else ""
                stage, stage_num, _label = self._resolve_stage_from_arg(ref, _print)
                if stage:
                    if instructions:
                        stage["deploy_mode"] = "manual"
                        stage["manual_instructions"] = instructions
                        self._deploy_state.save()
                        _print(f"  Stage {ref} set to manual mode with instructions.")
                    else:
                        current = stage.get("manual_instructions", "")
                        if current:
                            _print(f"  Current instructions: {current}")
                        else:
                            _print(f"  No manual instructions set for Stage {ref}.")
                            _print('  Use: /manual N "your instructions here"')
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

        elif cmd == "/describe":
            self._handle_describe(arg, _print)

        elif cmd == "/help":
            _print("")
            _print("  Available commands:")
            _print("    /status       - Show deployment progress per stage")
            _print("    /stages       - List all stages with status (alias)")
            _print("    /deploy [N]   - Deploy stage N (or 5a for substage) or all")
            _print("    /rollback [N] - Roll back stage N or all (reverse order)")
            _print("    /redeploy N   - Rollback + redeploy stage N")
            _print("    /plan N       - Show what-if/terraform plan for stage N")
            _print("    /split N      - Split a stage into substages")
            _print("    /destroy N    - Destroy resources for a removed stage")
            _print("    /manual N     - Add/view manual step instructions")
            _print("    /describe N   - Show details for stage N")
            _print("    /outputs      - Show captured deployment outputs")
            _print("    /preflight    - Re-run preflight checks")
            _print("    /login        - Run az login interactively")
            _print("    /help         - Show this help")
            _print("    done          - Accept deployment and exit")
            _print("    quit          - Exit deploy session")
            _print("")
            _print("  Stage references accept substage labels: /deploy 5a")
            _print("")
            _print("  You can also use natural language:")
            _print("    'deploy stage 3'            instead of  /deploy 3")
            _print("    'rollback all'              instead of  /rollback all")
            _print("    'deploy stages 3 and 4'     deploys multiple stages")
            _print("    'describe stage 2'          instead of  /describe 2")
            _print("")

        else:
            _print(f"  Unknown command: {cmd}. Type /help for a list.")

    def _handle_describe(self, arg: str, _print: Callable[[str], None]) -> None:
        """Show detailed description of a deploy stage."""
        if not arg or not arg.strip():
            _print("  Usage: /describe N (stage number)")
            return

        numbers = re.findall(r"\d+", arg)
        if not numbers:
            _print("  Usage: /describe N (stage number)")
            return

        stage_num = int(numbers[0])
        stage = self._deploy_state.get_stage(stage_num)
        if not stage:
            _print(f"  Stage {stage_num} not found.")
            return

        _print("")
        _print(f"  Stage {stage_num}: {stage.get('name', '?')}")
        _print(f"  Category:      {stage.get('category', '?')}")
        _print(f"  Deploy status: {stage.get('deploy_status', 'pending')}")
        _print(f"  Dir:           {stage.get('dir', '?')}")

        timestamp = stage.get("deployed_at", "")
        if timestamp:
            _print(f"  Deployed at:   {timestamp}")

        services = stage.get("services", [])
        if services:
            _print(f"  Resources ({len(services)}):")
            for svc in services:
                name = svc.get("computed_name") or svc.get("name", "?")
                rtype = svc.get("resource_type", "")
                sku = svc.get("sku", "")
                line = f"    - {name}"
                if rtype:
                    line += f"  ({rtype})"
                if sku:
                    line += f"  [{sku}]"
                _print(line)

        files = stage.get("files", [])
        if files:
            _print(f"  Files ({len(files)}):")
            for f in files:
                _print(f"    - {f}")

        deploy_output = stage.get("deploy_output", "")
        if deploy_output:
            _print("  Deploy output:")
            for line in deploy_output.split("\n")[:10]:
                _print(f"    {line}")
            if deploy_output.count("\n") > 10:
                _print("    ... (truncated)")

        deploy_error = stage.get("deploy_error", "")
        if deploy_error:
            _print("  Deploy error:")
            _print(f"    {deploy_error[:200]}")

        _print("")

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
    def _maybe_spinner(self, message: str, use_styled: bool, *, status_fn: Callable | None = None) -> Iterator[None]:
        """Show a spinner/status when using styled output or TUI."""
        _sfn = status_fn or self._status_fn
        if use_styled:
            with self._console.spinner(message):
                yield
        elif _sfn:
            _sfn(message, "start")
            try:
                yield
            finally:
                _sfn(message, "end")
        else:
            yield
