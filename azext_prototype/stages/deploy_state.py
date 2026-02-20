"""Deploy state management — persistent YAML storage for deploy progress.

This module manages the ``.prototype/state/deploy.yaml`` file which captures
all deploy session state including stage deployment status, preflight results,
rollback history, and captured outputs.  The file is:

1. **Created on first deploy** — Stages imported from build state
2. **Updated incrementally** — After each stage deploy, state is persisted
3. **Re-entrant** — Stages already deployed can be skipped on re-run

The state structure tracks:
- Deployment stages (imported from build, enriched with deploy status)
- Preflight check results
- Per-stage deploy/rollback audit trail
- Captured Terraform/Bicep outputs
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEPLOY_STATE_FILE = ".prototype/state/deploy.yaml"


def _default_deploy_state() -> dict[str, Any]:
    """Return the default empty deploy state structure."""
    return {
        "iac_tool": "terraform",
        "subscription": "",
        "resource_group": "",
        "tenant": "",
        "deployment_stages": [],
        "preflight_results": [],
        "deploy_log": [],
        "rollback_log": [],
        "captured_outputs": {},
        "conversation_history": [],
        "_metadata": {
            "created": None,
            "last_updated": None,
            "iteration": 0,
        },
    }


class DeployState:
    """Manages persistent deploy state in YAML format.

    Provides:
    - Loading existing state on startup (re-entrant deploys)
    - Importing deployment stages from build state
    - Per-stage deploy status transitions with ordering enforcement
    - Preflight result tracking
    - Deploy and rollback audit logging
    - Formatting for display
    """

    def __init__(self, project_dir: str):
        self._project_dir = project_dir
        self._path = Path(project_dir) / DEPLOY_STATE_FILE
        self._state: dict[str, Any] = _default_deploy_state()
        self._loaded = False

    @property
    def exists(self) -> bool:
        """Check if a deploy.yaml file exists."""
        return self._path.exists()

    @property
    def state(self) -> dict[str, Any]:
        """Get the current state dict."""
        return self._state

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def load(self) -> dict[str, Any]:
        """Load existing deploy state from YAML.

        Returns the state dict (empty structure if file doesn't exist).
        """
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                self._state = _default_deploy_state()
                self._deep_merge(self._state, loaded)
                self._loaded = True
                logger.info("Loaded deploy state from %s", self._path)
            except (yaml.YAMLError, IOError) as e:
                logger.warning("Could not load deploy state: %s", e)
                self._state = _default_deploy_state()
        else:
            self._state = _default_deploy_state()

        return self._state

    def save(self) -> None:
        """Save the current state to YAML."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).isoformat()
        if not self._state["_metadata"]["created"]:
            self._state["_metadata"]["created"] = now
        self._state["_metadata"]["last_updated"] = now

        with open(self._path, "w", encoding="utf-8") as f:
            yaml.dump(
                self._state,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                width=120,
            )
        logger.info("Saved deploy state to %s", self._path)

    def reset(self) -> None:
        """Reset state to defaults and save."""
        self._state = _default_deploy_state()
        self._loaded = False
        self.save()

    # ------------------------------------------------------------------ #
    # Build-state bridge
    # ------------------------------------------------------------------ #

    def load_from_build_state(self, build_state_path: str | Path) -> bool:
        """Import deployment_stages from build.yaml, enriching with deploy fields.

        For each stage from the build state, adds deploy-specific fields:
        ``deploy_status``, ``deploy_timestamp``, ``deploy_output``,
        ``deploy_error``, ``rollback_timestamp``.

        Returns True if stages were imported, False if build.yaml not found
        or contained no deployment stages.
        """
        path = Path(build_state_path)
        if not path.exists():
            logger.warning("Build state not found at %s", path)
            return False

        try:
            with open(path, "r", encoding="utf-8") as f:
                build_data = yaml.safe_load(f) or {}
        except (yaml.YAMLError, IOError) as e:
            logger.warning("Could not read build state: %s", e)
            return False

        build_stages = build_data.get("deployment_stages", [])
        if not build_stages:
            logger.warning("Build state has no deployment_stages.")
            return False

        enriched: list[dict] = []
        for stage in build_stages:
            enriched_stage = dict(stage)
            enriched_stage.setdefault("deploy_status", "pending")
            enriched_stage.setdefault("deploy_timestamp", None)
            enriched_stage.setdefault("deploy_output", "")
            enriched_stage.setdefault("deploy_error", "")
            enriched_stage.setdefault("rollback_timestamp", None)
            enriched.append(enriched_stage)

        self._state["deployment_stages"] = enriched
        self._state["iac_tool"] = build_data.get("iac_tool", "terraform")
        self.save()

        logger.info("Imported %d stages from build state.", len(enriched))
        return True

    # ------------------------------------------------------------------ #
    # Stage status transitions
    # ------------------------------------------------------------------ #

    def mark_stage_deploying(self, stage_num: int) -> None:
        """Mark a stage as currently deploying."""
        stage = self.get_stage(stage_num)
        if stage:
            stage["deploy_status"] = "deploying"
            self.add_deploy_log_entry(stage_num, "deploying")
            self.save()

    def mark_stage_deployed(self, stage_num: int, output: str = "") -> None:
        """Mark a stage as successfully deployed."""
        stage = self.get_stage(stage_num)
        if stage:
            stage["deploy_status"] = "deployed"
            stage["deploy_timestamp"] = datetime.now(timezone.utc).isoformat()
            stage["deploy_output"] = output
            stage["deploy_error"] = ""
            self.add_deploy_log_entry(stage_num, "deployed")
            self.save()

    def mark_stage_failed(self, stage_num: int, error: str = "") -> None:
        """Mark a stage as failed."""
        stage = self.get_stage(stage_num)
        if stage:
            stage["deploy_status"] = "failed"
            stage["deploy_timestamp"] = datetime.now(timezone.utc).isoformat()
            stage["deploy_error"] = error
            self.add_deploy_log_entry(stage_num, "failed", error)
            self.save()

    def mark_stage_rolled_back(self, stage_num: int) -> None:
        """Mark a stage as rolled back."""
        stage = self.get_stage(stage_num)
        if stage:
            stage["deploy_status"] = "rolled_back"
            stage["rollback_timestamp"] = datetime.now(timezone.utc).isoformat()
            self.add_rollback_log_entry(stage_num)
            self.save()

    # ------------------------------------------------------------------ #
    # Stage queries
    # ------------------------------------------------------------------ #

    def get_stage(self, stage_num: int) -> dict | None:
        """Return a specific stage by number."""
        for stage in self._state["deployment_stages"]:
            if stage["stage"] == stage_num:
                return stage
        return None

    def get_pending_stages(self) -> list[dict]:
        """Return stages not yet deployed."""
        return [
            s for s in self._state["deployment_stages"]
            if s.get("deploy_status") == "pending"
        ]

    def get_deployed_stages(self) -> list[dict]:
        """Return stages that have been deployed."""
        return [
            s for s in self._state["deployment_stages"]
            if s.get("deploy_status") == "deployed"
        ]

    def get_failed_stages(self) -> list[dict]:
        """Return stages that failed deployment."""
        return [
            s for s in self._state["deployment_stages"]
            if s.get("deploy_status") == "failed"
        ]

    def get_rollback_candidates(self) -> list[dict]:
        """Return deployed stages in reverse order (highest stage number first).

        Only stages that can be safely rolled back are included.
        """
        deployed = self.get_deployed_stages()
        return sorted(deployed, key=lambda s: s["stage"], reverse=True)

    def can_rollback(self, stage_num: int) -> bool:
        """Check if a stage can be rolled back.

        A stage can only be rolled back if no higher-numbered stage has
        ``deploy_status == 'deployed'``.  This enforces the invariant:
        cannot roll back stage N before rolling back stage N+1.
        """
        for stage in self._state["deployment_stages"]:
            if stage["stage"] > stage_num and stage.get("deploy_status") == "deployed":
                return False
        return True

    # ------------------------------------------------------------------ #
    # Preflight
    # ------------------------------------------------------------------ #

    def set_preflight_results(self, results: list[dict]) -> None:
        """Store preflight check results.

        Each result dict: ``{name, status, message, fix_command?}``
        where ``status`` is ``'pass'``, ``'warn'``, or ``'fail'``.
        """
        self._state["preflight_results"] = results
        self.save()

    def get_preflight_failures(self) -> list[dict]:
        """Return preflight results where status is ``'fail'``."""
        return [
            r for r in self._state.get("preflight_results", [])
            if r.get("status") == "fail"
        ]

    # ------------------------------------------------------------------ #
    # Audit logging
    # ------------------------------------------------------------------ #

    def add_deploy_log_entry(
        self, stage_num: int, action: str, detail: str = "",
    ) -> None:
        """Append an entry to the deploy audit log."""
        self._state["deploy_log"].append({
            "stage": stage_num,
            "action": action,
            "detail": detail,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def add_rollback_log_entry(self, stage_num: int, detail: str = "") -> None:
        """Append an entry to the rollback audit log."""
        self._state["rollback_log"].append({
            "stage": stage_num,
            "detail": detail,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ------------------------------------------------------------------ #
    # Conversation tracking
    # ------------------------------------------------------------------ #

    def update_from_exchange(
        self, user_input: str, agent_response: str, exchange_number: int,
    ) -> None:
        """Record a conversation exchange."""
        self._state["conversation_history"].append({
            "exchange": exchange_number,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user": user_input,
            "assistant": agent_response,
        })
        self.save()

    # ------------------------------------------------------------------ #
    # Formatting
    # ------------------------------------------------------------------ #

    def format_deploy_report(self) -> str:
        """Format a full deployment report for display."""
        lines: list[str] = []

        lines.append("  Deploy Report")
        lines.append("  " + "=" * 40)
        lines.append("")

        sub = self._state.get("subscription", "")
        rg = self._state.get("resource_group", "")
        if sub:
            lines.append(f"  Subscription: {sub}")
        if rg:
            lines.append(f"  Resource Group: {rg}")
        lines.append(f"  IaC Tool: {self._state.get('iac_tool', 'terraform')}")
        lines.append("")

        stages = self._state.get("deployment_stages", [])
        deployed = len([s for s in stages if s.get("deploy_status") == "deployed"])
        failed = len([s for s in stages if s.get("deploy_status") == "failed"])
        rolled = len([s for s in stages if s.get("deploy_status") == "rolled_back"])

        lines.append(f"  Stages: {len(stages)} total, {deployed} deployed"
                      f"{f', {failed} failed' if failed else ''}"
                      f"{f', {rolled} rolled back' if rolled else ''}")
        lines.append("")

        for stage in stages:
            icon = _status_icon(stage.get("deploy_status", "pending"))
            line = f"  {icon} Stage {stage['stage']}: {stage['name']}"
            ts = stage.get("deploy_timestamp")
            if ts:
                line += f"  ({ts[:19]})"
            lines.append(line)

            services = stage.get("services", [])
            if services:
                svc_names = [s.get("computed_name") or s.get("name", "?") for s in services]
                lines.append(f"      Resources: {', '.join(svc_names)}")

            error = stage.get("deploy_error", "")
            if error:
                # Truncate long errors
                short = error[:120] + "..." if len(error) > 120 else error
                lines.append(f"      Error: {short}")

        # Captured outputs
        outputs = self._state.get("captured_outputs", {})
        if outputs:
            total_keys = sum(len(v) for v in outputs.values() if isinstance(v, dict))
            lines.append("")
            lines.append(f"  Captured outputs: {total_keys} key(s)")

        return "\n".join(lines)

    def format_stage_status(self) -> str:
        """Format a compact status summary of all stages."""
        stages = self._state.get("deployment_stages", [])
        if not stages:
            return "  No deployment stages loaded yet."

        lines: list[str] = []
        for stage in stages:
            status = stage.get("deploy_status", "pending")
            icon = _status_icon(status)
            svc_count = len(stage.get("services", []))
            line = f"  {icon} Stage {stage['stage']}: {stage['name']} ({stage.get('category', '?')})"
            if svc_count:
                line += f" - {svc_count} service(s)"
            lines.append(line)

        deployed = len([s for s in stages if s.get("deploy_status") == "deployed"])
        lines.append("")
        lines.append(f"  Progress: {deployed}/{len(stages)} stages deployed")

        metadata = self._state.get("_metadata", {})
        if metadata.get("last_updated"):
            lines.append(f"  Last updated: {metadata['last_updated'][:19]}")

        return "\n".join(lines)

    def format_preflight_report(self) -> str:
        """Format preflight check results for display."""
        results = self._state.get("preflight_results", [])
        if not results:
            return "  No preflight checks run yet."

        lines: list[str] = []
        lines.append("  Preflight Checks")
        lines.append("  " + "-" * 30)

        for r in results:
            status = r.get("status", "?")
            icon = {"pass": "v", "warn": "!", "fail": "x"}.get(status, "?")
            lines.append(f"  [{icon}] {r.get('name', '?')}: {r.get('message', '')}")

            fix = r.get("fix_command")
            if fix and status in ("warn", "fail"):
                lines.append(f"      Fix: {fix}")

        failures = [r for r in results if r.get("status") == "fail"]
        warnings = [r for r in results if r.get("status") == "warn"]
        passes = [r for r in results if r.get("status") == "pass"]

        lines.append("")
        lines.append(f"  Result: {len(passes)} passed, {len(warnings)} warning(s), {len(failures)} failed")

        return "\n".join(lines)

    def format_outputs(self) -> str:
        """Format captured deployment outputs for display."""
        outputs = self._state.get("captured_outputs", {})
        if not outputs:
            return "  No deployment outputs captured yet."

        lines: list[str] = []
        lines.append("  Deployment Outputs")
        lines.append("  " + "-" * 30)

        for provider, values in outputs.items():
            if provider == "last_capture":
                continue
            if isinstance(values, dict):
                lines.append(f"  {provider}:")
                for key, value in values.items():
                    val_str = str(value)
                    if len(val_str) > 80:
                        val_str = val_str[:77] + "..."
                    lines.append(f"    {key}: {val_str}")

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _deep_merge(self, base: dict, updates: dict) -> None:
        """Deep merge updates into base dict."""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value


def _status_icon(status: str) -> str:
    """Return a compact status icon for display."""
    return {
        "pending": "  ",
        "deploying": ">>",
        "deployed": " v",
        "failed": " x",
        "rolled_back": " ~",
    }.get(status, "  ")
