"""Tests for azext_prototype.tracking — ChangeTracker."""

import json


from azext_prototype.tracking import ChangeTracker


class TestChangeTracker:
    """Test incremental change tracking."""

    def test_no_changes_on_empty_project(self, tmp_project):
        tracker = ChangeTracker(str(tmp_project))
        changes = tracker.get_changed_files("all")
        assert changes["total_changed"] == 0

    def test_detects_added_files(self, tmp_project):
        # Record initial empty state
        tracker = ChangeTracker(str(tmp_project))
        tracker.record_deployment("infra")

        # Add a file
        infra_dir = tmp_project / "concept" / "infra"
        infra_dir.mkdir(parents=True, exist_ok=True)
        (infra_dir / "main.tf").write_text("resource {}")

        # Should detect the new file
        tracker2 = ChangeTracker(str(tmp_project))
        changes = tracker2.get_changed_files("infra")
        assert len(changes["added"]) > 0

    def test_detects_modified_files(self, tmp_project):
        infra_dir = tmp_project / "concept" / "infra"
        infra_dir.mkdir(parents=True, exist_ok=True)
        tf_file = infra_dir / "main.tf"
        tf_file.write_text("resource {} # v1")

        # Record with v1
        tracker = ChangeTracker(str(tmp_project))
        tracker.record_deployment("infra")

        # Modify file
        tf_file.write_text("resource {} # v2 modified")

        # Should detect the change
        tracker2 = ChangeTracker(str(tmp_project))
        changes = tracker2.get_changed_files("infra")
        assert len(changes["modified"]) > 0

    def test_detects_deleted_files(self, tmp_project):
        infra_dir = tmp_project / "concept" / "infra"
        infra_dir.mkdir(parents=True, exist_ok=True)
        tf_file = infra_dir / "main.tf"
        tf_file.write_text("resource {}")

        # Record
        tracker = ChangeTracker(str(tmp_project))
        tracker.record_deployment("infra")

        # Delete file
        tf_file.unlink()

        # Should detect deletion
        tracker2 = ChangeTracker(str(tmp_project))
        changes = tracker2.get_changed_files("infra")
        assert len(changes["deleted"]) > 0

    def test_has_changes(self, tmp_project):
        tracker = ChangeTracker(str(tmp_project))
        assert tracker.has_changes("infra") is False

    def test_record_deployment_creates_manifest(self, tmp_project):
        tracker = ChangeTracker(str(tmp_project))
        tracker.record_deployment("all")

        manifest_path = tmp_project / ".prototype" / "state" / "change_manifest.json"
        assert manifest_path.exists()

        with open(manifest_path, "r") as f:
            manifest = json.load(f)
        assert len(manifest["deployments"]) == 1
        assert manifest["deployments"][0]["scope"] == "all"

    def test_deployment_history(self, tmp_project):
        tracker = ChangeTracker(str(tmp_project))
        tracker.record_deployment("infra")
        tracker.record_deployment("apps")

        history = tracker.get_deployment_history()
        assert len(history) == 2
        assert history[0]["scope"] == "infra"
        assert history[1]["scope"] == "apps"

    def test_reset_clears_all(self, tmp_project):
        tracker = ChangeTracker(str(tmp_project))
        tracker.record_deployment("infra")
        tracker.reset()

        assert tracker.get_deployment_history() == []

    def test_reset_clears_scope(self, tmp_project):
        tracker = ChangeTracker(str(tmp_project))
        tracker.record_deployment("infra")
        tracker.record_deployment("apps")
        tracker.reset(scope="infra")

        # infra should be cleared, apps untouched
        assert "infra" not in tracker._manifest.get("files", {})

    def test_ignores_gitignore_patterns(self, tmp_project):
        infra_dir = tmp_project / "concept" / "infra" / "__pycache__"
        infra_dir.mkdir(parents=True, exist_ok=True)
        (infra_dir / "cached.pyc").write_text("bytecode")

        tracker = ChangeTracker(str(tmp_project))
        files = tracker._scan_project("infra")
        assert not any("__pycache__" in f for f in files)
