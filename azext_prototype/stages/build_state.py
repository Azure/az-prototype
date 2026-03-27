"""Build state management — persistent YAML storage for build progress.

This module manages the ``.prototype/state/build.yaml`` file which captures
all build session state including deployment stages, policy resolutions,
generated files, and conversation history.  The file is:

1. **Read on startup** — Previous build state is loaded when build stage restarts
2. **Updated incrementally** — After each stage generation, state is persisted
3. **Re-entrant** — Stages already generated can be skipped on re-run

The state structure tracks:
- Templates used as starting points (array — may be empty)
- Fine-grained deployment stages with computed resource names and SKUs
- Policy check results and user-approved overrides
- Build conversation history for the review loop
- Aggregated resource list for multi-resource telemetry
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """Convert a stage name to a URL-safe slug for use as a stable ID.

    Example: "Data Layer" → "data-layer"
    """
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "stage"


def _ensure_unique_id(slug: str, existing: set[str]) -> str:
    """Append a numeric suffix if *slug* already exists in *existing*."""
    if slug not in existing:
        return slug
    for i in range(2, 1000):
        candidate = f"{slug}-{i}"
        if candidate not in existing:
            return candidate
    return f"{slug}-{len(existing)}"


BUILD_STATE_FILE = ".prototype/state/build.yaml"


def _default_build_state() -> dict[str, Any]:
    """Return the default empty build state structure."""
    return {
        "templates_used": [],
        "iac_tool": "terraform",
        "services_detected": [],
        "deployment_stages": [],
        "generation_log": [],
        "policy_checks": [],
        "policy_overrides": [],
        "files_generated": [],
        "review_decisions": [],
        "conversation_history": [],
        "resources": [],
        "stage_contexts": {},  # {stage_num: condensed_context_str}
        "design_snapshot": {
            "iteration": None,
            "architecture_hash": None,
            "architecture_text": None,
        },
        "_metadata": {
            "created": None,
            "last_updated": None,
            "iteration": 0,
            "scope": "all",
        },
    }


class BuildState:
    """Manages persistent build state in YAML format.

    Provides:
    - Loading existing state on startup (re-entrant builds)
    - Incremental updates after each stage is generated
    - Deployment plan tracking with computed names and SKUs
    - Policy resolution persistence
    - Build report formatting
    """

    def __init__(self, project_dir: str):
        self._project_dir = project_dir
        self._path = Path(project_dir) / BUILD_STATE_FILE
        self._state: dict[str, Any] = _default_build_state()
        self._loaded = False

    @property
    def exists(self) -> bool:
        """Check if a build.yaml file exists."""
        return self._path.exists()

    @property
    def state(self) -> dict[str, Any]:
        """Get the current state dict."""
        return self._state

    def load(self) -> dict[str, Any]:
        """Load existing build state from YAML.

        Returns the state dict (empty structure if file doesn't exist).
        """
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                self._state = _default_build_state()
                self._deep_merge(self._state, loaded)
                self._backfill_ids()
                self._loaded = True
                logger.info("Loaded build state from %s", self._path)
            except (yaml.YAMLError, IOError) as e:
                logger.warning("Could not load build state: %s", e)
                self._state = _default_build_state()
        else:
            self._state = _default_build_state()

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
        logger.info("Saved build state to %s", self._path)

    def reset(self) -> None:
        """Reset state to defaults and save."""
        self._state = _default_build_state()
        self._loaded = False
        self.save()

    # ------------------------------------------------------------------ #
    # Deployment plan management
    # ------------------------------------------------------------------ #

    def set_deployment_plan(self, stages: list[dict]) -> None:
        """Set the full deployment stage plan.

        Each stage dict should contain::

            {
                "stage": 1,
                "name": "Foundation",
                "category": "infra",
                "services": [
                    {
                        "name": "key-vault",
                        "computed_name": "zd-kv-api-dev-eus",
                        "resource_type": "Microsoft.KeyVault/vaults",
                        "sku": "standard",
                    },
                ],
                "status": "pending",
                "dir": "",
                "files": [],
            }
        """
        self._state["deployment_stages"] = stages
        self._assign_stable_ids()
        # Rebuild the aggregated resources list
        self._rebuild_resources()
        self.save()

    def mark_stage_generated(
        self,
        stage_num: int,
        files: list[str],
        agent_name: str,
    ) -> None:
        """Mark a deployment stage as generated and record the result."""
        for stage in self._state["deployment_stages"]:
            if stage["stage"] == stage_num:
                stage["status"] = "generated"
                stage["files"] = files
                break

        self._state["generation_log"].append(
            {
                "stage": stage_num,
                "agent": agent_name,
                "files": files,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        # Add files to the global list
        for f in files:
            if f not in self._state["files_generated"]:
                self._state["files_generated"].append(f)

        self.save()

    def mark_stage_accepted(self, stage_num: int) -> None:
        """Mark a deployment stage as accepted after review."""
        for stage in self._state["deployment_stages"]:
            if stage["stage"] == stage_num:
                stage["status"] = "accepted"
                break
        self.save()

    def get_pending_stages(self) -> list[dict]:
        """Return stages that have not yet been generated."""
        return [s for s in self._state["deployment_stages"] if s.get("status") == "pending"]

    def get_generated_stages(self) -> list[dict]:
        """Return stages that have been generated (but may not be accepted)."""
        return [s for s in self._state["deployment_stages"] if s.get("status") in ("generated", "accepted")]

    def get_stage(self, stage_num: int) -> dict | None:
        """Return a specific stage by number."""
        for stage in self._state["deployment_stages"]:
            if stage["stage"] == stage_num:
                return stage
        return None

    def get_stage_by_id(self, stage_id: str) -> dict | None:
        """Return a specific stage by its stable ``id``."""
        for stage in self._state["deployment_stages"]:
            if stage.get("id") == stage_id:
                return stage
        return None

    # ------------------------------------------------------------------ #
    # Design snapshot — change detection for incremental rebuilds
    # ------------------------------------------------------------------ #

    def set_design_snapshot(self, design: dict) -> None:
        """Store a snapshot of the current design for future change detection.

        Captures the design iteration number, a content hash of the
        architecture text, and the full architecture text for diffing.
        """
        architecture = design.get("architecture", "")
        self._state["design_snapshot"] = {
            "iteration": design.get("_metadata", {}).get("iteration"),
            "architecture_hash": hashlib.sha256(architecture.encode("utf-8")).hexdigest()[:16],
            "architecture_text": architecture,
        }
        self.save()

    def design_has_changed(self, design: dict) -> bool:
        """Check whether the design has changed since the last build.

        Returns ``True`` when the architecture content hash differs from
        the stored snapshot, or when no snapshot exists (legacy builds).
        """
        snapshot = self._state.get("design_snapshot", {})
        stored_hash = snapshot.get("architecture_hash")
        if not stored_hash:
            return True

        architecture = design.get("architecture", "")
        current_hash = hashlib.sha256(architecture.encode("utf-8")).hexdigest()[:16]
        return current_hash != stored_hash

    def get_previous_architecture(self) -> str | None:
        """Return the stored architecture text from the last build, if any."""
        snapshot = self._state.get("design_snapshot", {})
        return snapshot.get("architecture_text")

    def mark_stages_stale(self, stage_nums: list[int]) -> None:
        """Reset specific stages to ``pending`` without clearing their files.

        This allows the generation phase to re-generate only these stages
        while preserving previously generated work on unaffected stages.
        """
        for stage in self._state["deployment_stages"]:
            if stage["stage"] in stage_nums:
                stage["status"] = "pending"
        self.save()

    def remove_stages(self, stage_nums: list[int]) -> None:
        """Remove stages by number and clean up file references."""
        nums_set = set(stage_nums)
        removed_files: list[str] = []
        for stage in self._state["deployment_stages"]:
            if stage["stage"] in nums_set:
                removed_files.extend(stage.get("files", []))

        self._state["deployment_stages"] = [s for s in self._state["deployment_stages"] if s["stage"] not in nums_set]

        # Remove from files_generated
        if removed_files:
            removed_set = set(removed_files)
            self._state["files_generated"] = [f for f in self._state["files_generated"] if f not in removed_set]

        self._rebuild_resources()
        self.save()

    def add_stages(self, new_stages: list[dict]) -> None:
        """Insert new stages before the docs stage and assign sequential numbers.

        New stages are inserted just before the last documentation stage
        (if one exists), otherwise appended at the end.
        """
        existing = self._state["deployment_stages"]

        # Find insertion point — before the docs stage
        insert_idx = len(existing)
        for i, s in enumerate(existing):
            if s.get("category") == "docs":
                insert_idx = i
                break

        for ns in new_stages:
            ns.setdefault("status", "pending")
            ns.setdefault("files", [])
            ns.setdefault("dir", "")
            existing.insert(insert_idx, ns)
            insert_idx += 1

        self._assign_stable_ids()
        self.renumber_stages()

    def renumber_stages(self) -> None:
        """Renumber all stages sequentially starting from 1."""
        for idx, stage in enumerate(self._state["deployment_stages"], start=1):
            stage["stage"] = idx
        self.save()

    # ------------------------------------------------------------------ #
    # Policy tracking
    # ------------------------------------------------------------------ #

    def add_policy_check(
        self,
        stage_num: int,
        violations: list[str],
        overrides: list[dict],
    ) -> None:
        """Record policy check results for a stage."""
        self._state["policy_checks"].append(
            {
                "stage": stage_num,
                "violations": violations,
                "overrides": overrides,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.save()

    def add_policy_override(self, rule_id: str, justification: str) -> None:
        """Record a user-approved policy override."""
        self._state["policy_overrides"].append(
            {
                "rule_id": rule_id,
                "justification": justification,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.save()

    # ------------------------------------------------------------------ #
    # Review loop tracking
    # ------------------------------------------------------------------ #

    def add_review_decision(self, feedback: str, iteration: int) -> None:
        """Record user feedback from the review loop."""
        self._state["review_decisions"].append(
            {
                "feedback": feedback,
                "iteration": iteration,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._state["_metadata"]["iteration"] = iteration
        self.save()

    # ------------------------------------------------------------------ #
    # Conversation tracking
    # ------------------------------------------------------------------ #

    def update_from_exchange(
        self,
        user_input: str,
        agent_response: str,
        exchange_number: int,
    ) -> None:
        """Record a conversation exchange from the review loop."""
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
    # Resource aggregation (for telemetry)
    # ------------------------------------------------------------------ #

    def get_all_resources(self) -> list[dict[str, str]]:
        """Flatten all resources from deployment stages for telemetry.

        Returns a list of ``{"resourceType": "...", "sku": "..."}`` dicts.
        """
        resources: list[dict[str, str]] = []
        seen: set[str] = set()

        for stage in self._state.get("deployment_stages", []):
            for svc in stage.get("services", []):
                rt = svc.get("resource_type", "")
                sku = svc.get("sku", "")
                key = f"{rt}:{sku}"
                if key not in seen and rt:
                    seen.add(key)
                    resources.append({"resourceType": rt, "sku": sku})

        return resources

    def _rebuild_resources(self) -> None:
        """Rebuild the aggregated resources list from deployment stages."""
        self._state["resources"] = self.get_all_resources()

    # ------------------------------------------------------------------ #
    # Formatting
    # ------------------------------------------------------------------ #

    def format_build_report(self) -> str:
        """Format a structured build report for display.

        Shows template(s) used, IaC tool, per-stage summary with
        computed names and SKUs, policy results, and total files.
        """
        lines: list[str] = []
        iteration = self._state["_metadata"].get("iteration", 0)

        lines.append(f"Build Report (Iteration {iteration})")
        lines.append("=" * 40)
        lines.append("")

        # Templates
        templates = self._state.get("templates_used", [])
        if templates:
            names = ", ".join(templates)
            lines.append(f"Template(s): {names}")
        else:
            lines.append("Template(s): None (built from architecture)")

        lines.append(f"IaC Tool: {self._state.get('iac_tool', 'terraform')}")

        stages = self._state.get("deployment_stages", [])
        lines.append(f"Deployment Stages: {len(stages)}")
        lines.append("")

        # Per-stage summary
        for stage in stages:
            status_icon = {"pending": " ", "generated": "+", "accepted": "v"}.get(stage.get("status", "pending"), " ")
            lines.append(f"  [{status_icon}] Stage {stage['stage']}: {stage['name']}")

            services = stage.get("services", [])
            if services:
                svc_names = [s.get("computed_name") or s.get("name", "?") for s in services]
                lines.append(f"      Resources: {', '.join(svc_names)}")

                skus = [s.get("sku", "") for s in services if s.get("sku")]
                if skus:
                    lines.append(f"      SKUs: {', '.join(skus)}")

            files = stage.get("files", [])
            if files:
                stage_dir = stage.get("dir", "")
                dir_label = f" ({stage_dir})" if stage_dir else ""
                lines.append(f"      Files: {len(files)}{dir_label}")

            # Policy results for this stage
            policy_checks = [pc for pc in self._state.get("policy_checks", []) if pc.get("stage") == stage["stage"]]
            if policy_checks:
                latest = policy_checks[-1]
                violations = len(latest.get("violations", []))
                overrides = len(latest.get("overrides", []))
                if violations == 0 and overrides == 0:
                    lines.append("      Policy: Clean")
                else:
                    parts = []
                    if violations:
                        parts.append(f"{violations} violation(s)")
                    if overrides:
                        parts.append(f"{overrides} override(s)")
                    lines.append(f"      Policy: {', '.join(parts)}")

            lines.append("")

        # Totals
        total_files = len(self._state.get("files_generated", []))
        lines.append(f"Total files generated: {total_files}")

        # Global overrides
        global_overrides = self._state.get("policy_overrides", [])
        if global_overrides:
            lines.append(f"Policy overrides: {len(global_overrides)}")
            for ov in global_overrides:
                lines.append(f"  - {ov.get('rule_id', '?')}: {ov.get('justification', '')}")

        return "\n".join(lines)

    def format_stage_status(self) -> str:
        """Format a compact status summary of all stages."""
        stages = self._state.get("deployment_stages", [])
        if not stages:
            return "No deployment stages defined yet."

        lines: list[str] = []
        for stage in stages:
            status = stage.get("status", "pending")
            icon = {"pending": "  ", "generated": "++ ", "accepted": "v "}.get(status, "  ")
            svc_count = len(stage.get("services", []))
            file_count = len(stage.get("files", []))
            line = f"  {icon}Stage {stage['stage']}: {stage['name']} ({stage.get('category', '?')})"
            if file_count:
                line += f" - {file_count} file(s)"
            elif svc_count:
                line += f" - {svc_count} service(s)"
            lines.append(line)

        generated = len([s for s in stages if s.get("status") in ("generated", "accepted")])
        lines.append("")
        lines.append(f"Progress: {generated}/{len(stages)} stages generated")

        metadata = self._state.get("_metadata", {})
        if metadata.get("last_updated"):
            lines.append(f"Last updated: {metadata['last_updated']}")

        return "\n".join(lines)

    def format_files_list(self) -> str:
        """Format the list of all generated files."""
        files = self._state.get("files_generated", [])
        if not files:
            return "No files generated yet."

        lines = [f"Generated files ({len(files)}):", ""]
        for f in sorted(files):
            lines.append(f"  {f}")
        return "\n".join(lines)

    def format_policy_summary(self) -> str:
        """Format a summary of all policy checks and overrides."""
        checks = self._state.get("policy_checks", [])
        overrides = self._state.get("policy_overrides", [])

        if not checks and not overrides:
            return "No policy checks performed yet."

        lines: list[str] = []

        if checks:
            lines.append("Policy checks by stage:")
            for check in checks:
                v_count = len(check.get("violations", []))
                o_count = len(check.get("overrides", []))
                status = "Clean" if v_count == 0 else f"{v_count} violation(s)"
                if o_count:
                    status += f", {o_count} override(s)"
                lines.append(f"  Stage {check['stage']}: {status}")
            lines.append("")

        if overrides:
            lines.append("Approved policy overrides:")
            for ov in overrides:
                lines.append(f"  - {ov.get('rule_id', '?')}: {ov.get('justification', '')}")

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _assign_stable_ids(self) -> None:
        """Ensure every deployment stage has a unique ``id`` field.

        Stages that already have an ``id`` keep it.  Stages without one
        get an ID derived from :func:`_slugify` on their name.
        """
        existing_ids: set[str] = set()
        for stage in self._state["deployment_stages"]:
            sid = stage.get("id")
            if sid:
                existing_ids.add(sid)

        for stage in self._state["deployment_stages"]:
            if not stage.get("id"):
                slug = _slugify(stage.get("name", "stage"))
                stage["id"] = _ensure_unique_id(slug, existing_ids)
                existing_ids.add(stage["id"])
            # Ensure deploy_mode defaults
            stage.setdefault("deploy_mode", "auto")
            stage.setdefault("manual_instructions", None)

    def _backfill_ids(self) -> None:
        """Backfill ``id``, ``deploy_mode``, and ``manual_instructions`` on legacy state files."""
        self._assign_stable_ids()

    def _deep_merge(self, base: dict, updates: dict) -> None:
        """Deep merge updates into base dict."""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
