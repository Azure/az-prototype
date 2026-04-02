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
- Build-deploy correspondence via stable ``build_stage_id``
- Substage splitting for 1:N divergence
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from azext_prototype.stages.base_state import BaseState
from azext_prototype.stages.build_state import _slugify

logger = logging.getLogger(__name__)

DEPLOY_STATE_FILE = ".prototype/state/deploy.yaml"


@dataclass
class SyncResult:
    """Result of syncing deploy state from build state."""

    matched: int = 0
    created: int = 0
    orphaned: int = 0
    updated_code: int = 0
    details: list[str] = field(default_factory=list)


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


def _enrich_deploy_fields(stage: dict) -> dict:
    """Ensure a stage dict has all deploy-specific fields."""
    stage.setdefault("deploy_status", "pending")
    stage.setdefault("deploy_timestamp", None)
    stage.setdefault("deploy_output", "")
    stage.setdefault("deploy_error", "")
    stage.setdefault("rollback_timestamp", None)
    stage.setdefault("remediation_attempts", 0)
    stage.setdefault("build_stage_id", None)
    stage.setdefault("deploy_mode", "auto")
    stage.setdefault("manual_instructions", None)
    stage.setdefault("substage_label", None)
    stage.setdefault("_is_substage", False)
    stage.setdefault("_destruction_declined", False)
    return stage


class DeployState(BaseState):
    """Manages persistent deploy state in YAML format.

    Provides:
    - Loading existing state on startup (re-entrant deploys)
    - Importing deployment stages from build state
    - Smart sync with build state (preserves deploy progress)
    - Stage splitting for 1:N build-deploy divergence
    - Per-stage deploy status transitions with ordering enforcement
    - Preflight result tracking
    - Deploy and rollback audit logging
    - Formatting for display
    """

    _STATE_FILE = DEPLOY_STATE_FILE

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return _default_deploy_state()

    def _post_load(self) -> None:
        self._backfill_build_stage_ids()

    # ------------------------------------------------------------------ #
    # Build-state bridge
    # ------------------------------------------------------------------ #

    def load_from_build_state(self, build_state_path: str | Path) -> bool:
        """Import deployment_stages from build.yaml, enriching with deploy fields.

        For each stage from the build state, adds deploy-specific fields:
        ``deploy_status``, ``deploy_timestamp``, ``deploy_output``,
        ``deploy_error``, ``rollback_timestamp``, ``build_stage_id``.

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
            # Set build_stage_id from the build stage's id field
            enriched_stage["build_stage_id"] = stage.get("id") or _slugify(stage.get("name", "stage"))
            _enrich_deploy_fields(enriched_stage)
            enriched.append(enriched_stage)

        self._state["deployment_stages"] = enriched
        self._state["iac_tool"] = build_data.get("iac_tool", "terraform")
        self.save()

        logger.info("Imported %d stages from build state.", len(enriched))
        return True

    def sync_from_build_state(self, build_state_path: str | Path) -> SyncResult:
        """Smart reconciliation of deploy stages with current build state.

        Unlike :meth:`load_from_build_state` (which overwrites), this method:

        - **Matches** existing deploy stages to build stages by ``build_stage_id``
        - **Updates** build-sourced fields (name, category, services, deploy_mode)
          while preserving deploy state (status, timestamps, substage structure)
        - **Creates** new deploy stages for new build stages
        - **Orphans** deploy stages whose build stage was removed (sets ``removed``)
        - Falls back to name+category matching for legacy stages

        Returns a :class:`SyncResult` summarising the changes.
        """
        result = SyncResult()
        path = Path(build_state_path)
        if not path.exists():
            result.details.append("Build state not found.")
            return result

        try:
            with open(path, "r", encoding="utf-8") as f:
                build_data = yaml.safe_load(f) or {}
        except (yaml.YAMLError, IOError) as e:
            result.details.append(f"Could not read build state: {e}")
            return result

        build_stages = build_data.get("deployment_stages", [])
        if not build_stages:
            result.details.append("Build state has no deployment_stages.")
            return result

        existing = self._state["deployment_stages"]

        # Index existing deploy stages by build_stage_id
        deploy_by_bid: dict[str, list[dict]] = {}
        for ds in existing:
            bid = ds.get("build_stage_id")
            if bid:
                deploy_by_bid.setdefault(bid, []).append(ds)

        # Track which build_stage_ids we've matched
        matched_bids: set[str] = set()
        new_stages: list[dict] = []

        for bs in build_stages:
            bid = bs.get("id") or _slugify(bs.get("name", "stage"))
            matched_bids.add(bid)

            if bid in deploy_by_bid:
                # Update matched deploy stages with build-sourced fields
                for ds in deploy_by_bid[bid]:
                    # Check if code changed
                    old_dir = ds.get("dir", "")
                    new_dir = bs.get("dir", "")
                    old_files = ds.get("files", [])
                    new_files = bs.get("files", [])
                    code_changed = (old_dir != new_dir) or (sorted(old_files) != sorted(new_files))

                    # Update build-sourced fields
                    ds["name"] = bs.get("name", ds["name"])
                    ds["category"] = bs.get("category", ds.get("category", "infra"))
                    ds["services"] = bs.get("services", ds.get("services", []))
                    ds["deploy_mode"] = bs.get("deploy_mode", ds.get("deploy_mode", "auto"))
                    ds["manual_instructions"] = bs.get("manual_instructions", ds.get("manual_instructions"))
                    if not ds.get("_is_substage"):
                        ds["dir"] = new_dir
                        ds["files"] = new_files

                    if code_changed and ds.get("deploy_status") == "deployed":
                        ds["_code_updated"] = True
                        result.updated_code += 1

                result.matched += 1
            else:
                # Legacy fallback: match by name+category
                legacy_match = None
                for ds in existing:
                    if (
                        not ds.get("build_stage_id")
                        and ds.get("name") == bs.get("name")
                        and ds.get("category") == bs.get("category")
                    ):
                        legacy_match = ds
                        break

                if legacy_match:
                    legacy_match["build_stage_id"] = bid
                    legacy_match["deploy_mode"] = bs.get("deploy_mode", "auto")
                    legacy_match["manual_instructions"] = bs.get("manual_instructions")
                    result.matched += 1
                    matched_bids.add(bid)
                else:
                    # Create new deploy stage
                    new_ds = dict(bs)
                    new_ds["build_stage_id"] = bid
                    _enrich_deploy_fields(new_ds)
                    new_stages.append(new_ds)
                    result.created += 1
                    result.details.append(f"New stage: {bs.get('name', '?')}")

        # Rebuild ordered list
        ordered: list[dict] = []
        processed_bids: set[str] = set()

        for bs in build_stages:
            bid = bs.get("id") or _slugify(bs.get("name", "stage"))
            if bid in deploy_by_bid and bid not in processed_bids:
                # Add existing deploy stages for this build stage (in existing order)
                ordered.extend(deploy_by_bid[bid])
                processed_bids.add(bid)
            elif bid not in processed_bids:
                # Add newly created stage
                for ns in new_stages:
                    if ns.get("build_stage_id") == bid:
                        ordered.append(ns)
                processed_bids.add(bid)

        # Also add legacy-matched stages that weren't in deploy_by_bid
        for ds in existing:
            if ds not in ordered and ds.get("build_stage_id") in matched_bids:
                ordered.append(ds)

        # Orphaned stages (build stage removed)
        for bid, deploy_stages in deploy_by_bid.items():
            if bid not in matched_bids:
                for ds in deploy_stages:
                    if ds.get("deploy_status") not in ("removed", "destroyed"):
                        ds["deploy_status"] = "removed"
                        result.orphaned += 1
                        result.details.append(f"Removed: {ds.get('name', '?')}")
                    ordered.append(ds)

        # Also catch any existing stages not yet in ordered
        for ds in existing:
            if ds not in ordered:
                if ds.get("build_stage_id") not in matched_bids:
                    if ds.get("deploy_status") not in ("removed", "destroyed"):
                        ds["deploy_status"] = "removed"
                        result.orphaned += 1
                ordered.append(ds)

        self._state["deployment_stages"] = ordered
        self._state["iac_tool"] = build_data.get("iac_tool", self._state.get("iac_tool", "terraform"))
        self.renumber_stages()

        return result

    # ------------------------------------------------------------------ #
    # Stage splitting
    # ------------------------------------------------------------------ #

    def split_stage(self, stage_num: int, substages: list[dict]) -> None:
        """Replace one deploy stage with N substages sharing the same ``build_stage_id``.

        Each substage gets a letter suffix: ``"a"``, ``"b"``, ``"c"``, etc.
        The original stage is removed from the list.

        Args:
            stage_num: The stage number to split.
            substages: List of substage dicts with at minimum ``name``, ``dir``.
        """
        stages = self._state["deployment_stages"]
        parent_idx = None
        parent = None

        for i, s in enumerate(stages):
            if s["stage"] == stage_num and not s.get("substage_label"):
                parent_idx = i
                parent = s
                break

        if parent is None or parent_idx is None:
            logger.warning("Stage %d not found for splitting.", stage_num)
            return

        build_stage_id = parent.get("build_stage_id")
        labels = [chr(ord("a") + i) for i in range(len(substages))]

        new_entries: list[dict] = []
        for label, sub in zip(labels, substages):
            entry = dict(parent)
            entry.update(sub)
            entry["stage"] = stage_num
            entry["substage_label"] = label
            entry["_is_substage"] = True
            entry["build_stage_id"] = build_stage_id
            _enrich_deploy_fields(entry)
            # Reset deploy state for new substages
            entry["deploy_status"] = "pending"
            entry["deploy_timestamp"] = None
            entry["deploy_output"] = ""
            entry["deploy_error"] = ""
            new_entries.append(entry)

        # Replace parent with substages
        stages[parent_idx : parent_idx + 1] = new_entries
        self.save()

    def get_stage_groups(self) -> dict[str | None, list[dict]]:
        """Group deploy stages by ``build_stage_id`` for tree rendering.

        Returns a dict mapping ``build_stage_id`` → list of deploy stages.
        Stages without a ``build_stage_id`` are grouped under ``None``.
        """
        groups: dict[str | None, list[dict]] = {}
        for s in self._state["deployment_stages"]:
            bid = s.get("build_stage_id")
            groups.setdefault(bid, []).append(s)
        return groups

    def get_stages_for_build_stage(self, build_stage_id: str) -> list[dict]:
        """Return all deploy stages linked to a given build stage."""
        return [s for s in self._state["deployment_stages"] if s.get("build_stage_id") == build_stage_id]

    def get_stage_by_display_id(self, display_id: str) -> dict | None:
        """Parse a display ID like ``"5"`` or ``"5a"`` and return the matching stage.

        Returns None if no match found.
        """
        stage_num, label = parse_stage_ref(display_id)
        if stage_num is None:
            return None

        for s in self._state["deployment_stages"]:
            if s["stage"] == stage_num:
                if label is None and not s.get("substage_label"):
                    return s
                if label is not None and s.get("substage_label") == label:
                    return s
                # If asking for bare number and stage has substages, return first
                if label is None and s.get("substage_label"):
                    return s
        return None

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

    def mark_stage_remediating(self, stage_num: int) -> None:
        """Mark a stage as undergoing remediation and bump attempt counter."""
        stage = self.get_stage(stage_num)
        if stage:
            stage["deploy_status"] = "remediating"
            stage["remediation_attempts"] = stage.get("remediation_attempts", 0) + 1
            self.add_deploy_log_entry(stage_num, "remediating", f"attempt {stage['remediation_attempts']}")
            self.save()

    def reset_stage_to_pending(self, stage_num: int) -> None:
        """Reset a failed/remediating stage back to pending for re-deploy."""
        stage = self.get_stage(stage_num)
        if stage:
            stage["deploy_status"] = "pending"
            stage["deploy_error"] = ""
            self.save()

    def mark_stage_removed(self, stage_num: int) -> None:
        """Mark a stage as removed (build stage was deleted)."""
        stage = self.get_stage(stage_num)
        if stage:
            stage["deploy_status"] = "removed"
            self.add_deploy_log_entry(stage_num, "removed")
            self.save()

    def mark_stage_destroyed(self, stage_num: int) -> None:
        """Mark a removed stage as destroyed (resources torn down)."""
        stage = self.get_stage(stage_num)
        if stage:
            stage["deploy_status"] = "destroyed"
            self.add_deploy_log_entry(stage_num, "destroyed")
            self.save()

    def mark_stage_awaiting_manual(self, stage_num: int) -> None:
        """Mark a manual stage as awaiting user confirmation."""
        stage = self.get_stage(stage_num)
        if stage:
            stage["deploy_status"] = "awaiting_manual"
            self.add_deploy_log_entry(stage_num, "awaiting_manual")
            self.save()

    def add_patch_stages(self, new_stages: list[dict]) -> None:
        """Insert new stages before the docs stage, enriched with deploy fields.

        Follows the same insertion pattern as
        :meth:`~.build_state.BuildState.add_stages`.
        """
        existing = self._state["deployment_stages"]

        # Find insertion point — before the docs stage
        insert_idx = len(existing)
        for i, s in enumerate(existing):
            if s.get("category") == "docs":
                insert_idx = i
                break

        for ns in new_stages:
            _enrich_deploy_fields(ns)
            ns.setdefault("services", [])
            ns.setdefault("files", [])
            ns.setdefault("dir", "")
            existing.insert(insert_idx, ns)
            insert_idx += 1

        self.renumber_stages()

    def renumber_stages(self) -> None:
        """Renumber stages sequentially.

        Top-level stages get sequential integers starting from 1.
        Substages inherit their parent's number (their labels are unchanged).
        A group of substages with the same ``build_stage_id`` counts as
        one logical stage for numbering purposes.
        """
        stages = self._state["deployment_stages"]
        current_num = 0
        seen_substage_bids: set[str | None] = set()

        for stage in stages:
            if not stage.get("substage_label"):
                # Top-level stage
                current_num += 1
                stage["stage"] = current_num
            else:
                # Substage — check if this is the first substage in its group
                bid = stage.get("build_stage_id")
                if bid not in seen_substage_bids:
                    current_num += 1
                    seen_substage_bids.add(bid)
                stage["stage"] = current_num

        self.save()

    # ------------------------------------------------------------------ #
    # Stage queries
    # ------------------------------------------------------------------ #

    def get_stage(self, stage_num: int) -> dict | None:
        """Return a specific stage by number.

        For stages with substages, returns the first matching stage
        (the first substage or the top-level stage).
        """
        for stage in self._state["deployment_stages"]:
            if stage["stage"] == stage_num:
                return stage
        return None

    def get_all_stages_for_num(self, stage_num: int) -> list[dict]:
        """Return all stages/substages with the given stage number."""
        return [s for s in self._state["deployment_stages"] if s["stage"] == stage_num]

    def get_pending_stages(self) -> list[dict]:
        """Return stages not yet deployed."""
        return [s for s in self._state["deployment_stages"] if s.get("deploy_status") == "pending"]

    def get_deployed_stages(self) -> list[dict]:
        """Return stages that have been deployed."""
        return [s for s in self._state["deployment_stages"] if s.get("deploy_status") == "deployed"]

    def get_failed_stages(self) -> list[dict]:
        """Return stages that failed deployment."""
        return [s for s in self._state["deployment_stages"] if s.get("deploy_status") == "failed"]

    def get_rollback_candidates(self) -> list[dict]:
        """Return deployed stages in reverse order (highest stage number first).

        Only stages that can be safely rolled back are included.
        """
        deployed = self.get_deployed_stages()
        return sorted(deployed, key=lambda s: (s["stage"], s.get("substage_label") or ""), reverse=True)

    def can_rollback(self, stage_num: int, substage_label: str | None = None) -> bool:
        """Check if a stage can be rolled back.

        A stage can only be rolled back if no higher-numbered stage has
        ``deploy_status == 'deployed'``.  For substages, checks within
        the same stage number that no later substage is still deployed.
        """
        for stage in self._state["deployment_stages"]:
            s_status = stage.get("deploy_status")
            if s_status != "deployed":
                continue
            s_num = stage["stage"]
            s_label = stage.get("substage_label")

            if s_num > stage_num:
                return False
            if s_num == stage_num and substage_label is not None and s_label is not None:
                if s_label > substage_label:
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
        return [r for r in self._state.get("preflight_results", []) if r.get("status") == "fail"]

    # ------------------------------------------------------------------ #
    # Audit logging
    # ------------------------------------------------------------------ #

    def add_deploy_log_entry(
        self,
        stage_num: int,
        action: str,
        detail: str = "",
    ) -> None:
        """Append an entry to the deploy audit log."""
        self._state["deploy_log"].append(
            {
                "stage": stage_num,
                "action": action,
                "detail": detail,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def add_rollback_log_entry(self, stage_num: int, detail: str = "") -> None:
        """Append an entry to the rollback audit log."""
        self._state["rollback_log"].append(
            {
                "stage": stage_num,
                "detail": detail,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    # ------------------------------------------------------------------ #
    # Conversation tracking
    # ------------------------------------------------------------------ #

    def update_from_exchange(
        self,
        user_input: str,
        agent_response: str,
        exchange_number: int,
    ) -> None:
        """Record a conversation exchange."""
        self._state["conversation_history"].append(
            {
                "exchange": exchange_number,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user": user_input,
                "assistant": agent_response,
            }
        )
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
        active_stages = [s for s in stages if s.get("deploy_status") not in ("removed", "destroyed")]
        deployed = len([s for s in stages if s.get("deploy_status") == "deployed"])
        failed = len([s for s in stages if s.get("deploy_status") == "failed"])
        rolled = len([s for s in stages if s.get("deploy_status") == "rolled_back"])
        removed = len([s for s in stages if s.get("deploy_status") in ("removed", "destroyed")])

        lines.append(
            f"  Stages: {len(active_stages)} active, {deployed} deployed"
            f"{f', {failed} failed' if failed else ''}"
            f"{f', {rolled} rolled back' if rolled else ''}"
            f"{f', {removed} removed' if removed else ''}"
        )
        lines.append("")

        for stage in stages:
            status = stage.get("deploy_status", "pending")
            icon = _status_icon(status)
            display_id = _format_display_id(stage)
            deploy_mode = stage.get("deploy_mode", "auto")

            if status in ("removed", "destroyed"):
                line = f"  {icon} Stage {display_id}: ~~{stage['name']}~~ (Removed)"
            else:
                line = f"  {icon} Stage {display_id}: {stage['name']}"
                if deploy_mode == "manual":
                    line += " [Manual]"

            ts = stage.get("deploy_timestamp")
            if ts:
                line += f"  ({ts[:19]})"
            lines.append(line)

            services = stage.get("services", [])
            if services and status not in ("removed", "destroyed"):
                svc_names = [s.get("computed_name") or s.get("name", "?") for s in services]
                lines.append(f"      Resources: {', '.join(svc_names)}")

            if deploy_mode == "manual" and stage.get("manual_instructions"):
                preview = stage["manual_instructions"][:80]
                lines.append(f"      Instructions: {preview}...")

            error = stage.get("deploy_error", "")
            if error:
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
            display_id = _format_display_id(stage)
            deploy_mode = stage.get("deploy_mode", "auto")

            if status in ("removed", "destroyed"):
                line = f"  {icon} Stage {display_id}: ~~{stage['name']}~~ ({stage.get('category', '?')}) (Removed)"
            else:
                line = f"  {icon} Stage {display_id}: {stage['name']} ({stage.get('category', '?')})"
                if deploy_mode == "manual":
                    line += " [Manual]"
                if svc_count:
                    line += f" - {svc_count} service(s)"
            lines.append(line)

        active = [s for s in stages if s.get("deploy_status") not in ("removed", "destroyed")]
        deployed = len([s for s in stages if s.get("deploy_status") == "deployed"])
        lines.append("")
        lines.append(f"  Progress: {deployed}/{len(active)} stages deployed")

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

    def _backfill_build_stage_ids(self) -> None:
        """Backfill ``build_stage_id`` and deploy fields on legacy state files."""
        for stage in self._state["deployment_stages"]:
            if not stage.get("build_stage_id"):
                stage["build_stage_id"] = _slugify(stage.get("name", "stage"))
            _enrich_deploy_fields(stage)


# ================================================================== #
# Module-level helpers
# ================================================================== #


def parse_stage_ref(arg: str) -> tuple[int | None, str | None]:
    """Parse a stage reference like ``"5"`` or ``"5a"`` into (stage_num, substage_label).

    Returns ``(None, None)`` if the string cannot be parsed.
    """
    m = re.match(r"^(\d+)([a-z]?)$", arg.strip())
    if not m:
        return None, None
    stage_num = int(m.group(1))
    label = m.group(2) or None
    return stage_num, label


def _format_display_id(stage: dict) -> str:
    """Format a stage's display identifier, e.g. ``"5"`` or ``"5a"``."""
    label = stage.get("substage_label") or ""
    return f"{stage['stage']}{label}"


def _status_icon(status: str) -> str:
    """Return a compact status icon for display."""
    return {
        "pending": "  ",
        "deploying": ">>",
        "deployed": " v",
        "failed": " x",
        "rolled_back": " ~",
        "remediating": "<>",
        "removed": "~~",
        "destroyed": "xx",
        "awaiting_manual": "!!",
    }.get(status, "  ")
