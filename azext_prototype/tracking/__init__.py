"""Change tracking for incremental deployments."""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class ChangeTracker:
    """Tracks file changes for incremental deployments.

    Maintains a manifest of file hashes so that subsequent
    deployments only process files that have actually changed.
    Separates tracking by scope (infra, apps, db, docs).
    """

    MANIFEST_FILE = ".prototype/state/change_manifest.json"

    def __init__(self, project_dir: str):
        self.project_dir = Path(project_dir)
        self.manifest_path = self.project_dir / self.MANIFEST_FILE
        self._manifest: dict = self._load_manifest()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_changed_files(self, scope: str = "all") -> dict:
        """Determine which files have changed since last deployment.

        Args:
            scope: 'all', 'infra', 'apps', 'db', or 'docs'.

        Returns:
            dict with keys: 'added', 'modified', 'deleted', each a list of paths.
        """
        current_files = self._scan_project(scope)
        previous_files = self._manifest.get("files", {}).get(scope, {})

        added = []
        modified = []
        deleted = []

        for filepath, current_hash in current_files.items():
            if filepath not in previous_files:
                added.append(filepath)
            elif previous_files[filepath] != current_hash:
                modified.append(filepath)

        for filepath in previous_files:
            if filepath not in current_files:
                deleted.append(filepath)

        return {
            "added": added,
            "modified": modified,
            "deleted": deleted,
            "total_changed": len(added) + len(modified) + len(deleted),
        }

    def has_changes(self, scope: str = "all") -> bool:
        """Check if there are any changes in the given scope."""
        changes = self.get_changed_files(scope)
        return changes["total_changed"] > 0

    def record_deployment(self, scope: str = "all"):
        """Record current file state after a successful deployment.

        Args:
            scope: The scope that was deployed.
        """
        current_files = self._scan_project(scope)

        if "files" not in self._manifest:
            self._manifest["files"] = {}

        self._manifest["files"][scope] = current_files

        if "deployments" not in self._manifest:
            self._manifest["deployments"] = []

        self._manifest["deployments"].append(
            {
                "scope": scope,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "files_count": len(current_files),
            }
        )

        self._save_manifest()
        logger.info("Deployment recorded for scope '%s' — %d files tracked.", scope, len(current_files))

    def get_deployment_history(self) -> list[dict]:
        """Return list of past deployments."""
        return self._manifest.get("deployments", [])

    def reset(self, scope: str | None = None):
        """Reset change tracking.

        Args:
            scope: If provided, reset only that scope. Otherwise reset all.
        """
        if scope:
            self._manifest.get("files", {}).pop(scope, None)
        else:
            self._manifest = {"files": {}, "deployments": []}

        self._save_manifest()

    # ------------------------------------------------------------------
    # Directory → Scope mapping
    # ------------------------------------------------------------------

    SCOPE_DIRS = {
        "infra": ["infra/"],
        "apps": ["apps/"],
        "db": ["db/"],
        "docs": ["docs/"],
    }

    def _scan_project(self, scope: str) -> dict[str, str]:
        """Scan project files and compute hashes for the given scope.

        Returns:
            dict mapping relative file paths to their SHA-256 hashes.
        """
        if scope == "all":
            dirs_to_scan = []
            for dirs in self.SCOPE_DIRS.values():
                dirs_to_scan.extend(dirs)
        else:
            dirs_to_scan = self.SCOPE_DIRS.get(scope, [])

        file_hashes = {}
        concept_dir = self.project_dir / "concept" if (self.project_dir / "concept").is_dir() else self.project_dir

        for dir_name in dirs_to_scan:
            scan_dir = concept_dir / dir_name
            if not scan_dir.is_dir():
                continue

            for file_path in scan_dir.rglob("*"):
                if file_path.is_file() and not self._should_ignore(file_path):
                    relative = str(file_path.relative_to(self.project_dir))
                    file_hashes[relative] = self._hash_file(file_path)

        return file_hashes

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _hash_file(self, path: Path) -> str:
        """Compute SHA-256 hash of a file."""
        sha256 = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except (IOError, OSError) as e:
            logger.warning("Could not hash file %s: %s", path, e)
            return ""

    def _should_ignore(self, path: Path) -> bool:
        """Check if a file should be ignored from tracking."""
        ignore_patterns = {
            ".git",
            "__pycache__",
            ".terraform",
            ".prototype",
            "node_modules",
            ".env",
            ".DS_Store",
        }
        parts = path.parts
        return any(part in ignore_patterns for part in parts)

    def _load_manifest(self) -> dict:
        """Load the change manifest from disk."""
        if not self.manifest_path.exists():
            return {"files": {}, "deployments": []}

        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Could not load change manifest: %s", e)
            return {"files": {}, "deployments": []}

    def _save_manifest(self):
        """Persist the change manifest to disk."""
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(self._manifest, f, indent=2)
