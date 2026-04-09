"""Tests for knowledge_contributor — gap detection and contribution submission.

Covers:
- Namespace-to-filename conversion
- Knowledge file path resolution (namespace lookup, friendly name fallback)
- Gap detection with fallbacks (missing files, namespace resolution, empty finding)
- Contribution formatting (title, body, new-service type promotion)
- Submission with label retry (auth check, label retry fallback, FileNotFoundError)
- QA finding builder
- Fire-and-forget wrapper (submit_if_gap)
"""

from unittest.mock import MagicMock, patch

_KC_MODULE = "azext_prototype.stages.knowledge_contributor"
_BP_MODULE = "azext_prototype.stages.backlog_push"
_CUSTOM_MODULE = "azext_prototype.custom"

# ======================================================================
# _namespace_to_filename
# ======================================================================


class TestNamespaceToFilename:
    """Test ARM namespace to knowledge filename conversion."""

    def test_typical_namespace(self):
        from azext_prototype.stages.knowledge_contributor import _namespace_to_filename

        assert _namespace_to_filename("Microsoft.Sql/servers/databases") == "sql-servers-databases"

    def test_container_apps(self):
        from azext_prototype.stages.knowledge_contributor import _namespace_to_filename

        assert _namespace_to_filename("Microsoft.App/containerApps") == "app-containerapps"

    def test_empty_namespace(self):
        from azext_prototype.stages.knowledge_contributor import _namespace_to_filename

        assert _namespace_to_filename("") == "unknown"

    def test_double_hyphens_cleaned(self):
        from azext_prototype.stages.knowledge_contributor import _namespace_to_filename

        # Simulate edge case with consecutive separators
        result = _namespace_to_filename("Microsoft..Foo//bar")
        assert "--" not in result


# ======================================================================
# _resolve_knowledge_file_path
# ======================================================================


class TestResolveKnowledgeFilePath:
    """Test file path resolution with namespace vs friendly name."""

    def test_namespace_via_loader_index(self):
        from azext_prototype.stages.knowledge_contributor import _resolve_knowledge_file_path

        mock_loader_cls = MagicMock()
        mock_loader_cls.return_value._build_namespace_index.return_value = {
            "Microsoft.Web/sites": "app-service.md",
        }
        with patch("azext_prototype.knowledge.KnowledgeLoader", mock_loader_cls):
            result = _resolve_knowledge_file_path("Microsoft.Web/sites", "app-service")
        assert result == "knowledge/services/app-service.md"

    def test_namespace_not_in_index_generates_from_namespace(self):
        from azext_prototype.stages.knowledge_contributor import _resolve_knowledge_file_path

        mock_loader_cls = MagicMock()
        mock_loader_cls.return_value._build_namespace_index.return_value = {}
        with patch("azext_prototype.knowledge.KnowledgeLoader", mock_loader_cls):
            result = _resolve_knowledge_file_path("Microsoft.NewService/items", "new-service")
        assert result == "knowledge/services/newservice-items.md"

    def test_namespace_loader_import_fails(self):
        """When KnowledgeLoader construction fails, still generates from namespace."""
        from azext_prototype.stages.knowledge_contributor import _resolve_knowledge_file_path

        with patch(
            "azext_prototype.knowledge.KnowledgeLoader",
            side_effect=RuntimeError("loader broken"),
        ):
            result = _resolve_knowledge_file_path("Microsoft.Storage/storageAccounts", "storage")
        assert result == "knowledge/services/storage-storageaccounts.md"

    def test_friendly_name_fallback(self):
        """When namespace is empty, falls back to friendly name."""
        from azext_prototype.stages.knowledge_contributor import _resolve_knowledge_file_path

        result = _resolve_knowledge_file_path("", "cosmos-db")
        # Should contain the friendly name
        assert "cosmos-db" in result

    def test_no_namespace_no_service(self):
        from azext_prototype.stages.knowledge_contributor import _resolve_knowledge_file_path

        result = _resolve_knowledge_file_path("", "")
        assert result == "knowledge/services/unknown.md"


# ======================================================================
# check_knowledge_gap
# ======================================================================


class TestCheckKnowledgeGap:
    """Test gap detection logic."""

    def test_empty_finding_returns_false(self):
        from azext_prototype.stages.knowledge_contributor import check_knowledge_gap

        assert check_knowledge_gap({}, MagicMock()) is False
        assert check_knowledge_gap(None, MagicMock()) is False

    def test_no_service_or_context_returns_false(self):
        from azext_prototype.stages.knowledge_contributor import check_knowledge_gap

        assert check_knowledge_gap({"service": "cosmos-db"}, MagicMock()) is False
        assert check_knowledge_gap({"context": "some error"}, MagicMock()) is False

    def test_no_existing_content_is_gap(self):
        from azext_prototype.stages.knowledge_contributor import check_knowledge_gap

        loader = MagicMock()
        loader.load_service.return_value = ""
        finding = {"service": "cosmos-db", "context": "Missing retry logic for 429 errors"}
        assert check_knowledge_gap(finding, loader) is True

    def test_loader_exception_treated_as_gap(self):
        from azext_prototype.stages.knowledge_contributor import check_knowledge_gap

        loader = MagicMock()
        loader.load_service.side_effect = FileNotFoundError("not found")
        finding = {"service": "cosmos-db", "context": "Some new pitfall discovered"}
        assert check_knowledge_gap(finding, loader) is True

    def test_context_already_covered_no_gap(self):
        from azext_prototype.stages.knowledge_contributor import check_knowledge_gap

        loader = MagicMock()
        loader.load_service.return_value = "Common issue: missing retry logic for 429 errors and throttling"
        finding = {"service": "cosmos-db", "context": "Missing retry logic for 429 errors"}
        assert check_knowledge_gap(finding, loader) is False

    def test_context_not_in_content_is_gap(self):
        from azext_prototype.stages.knowledge_contributor import check_knowledge_gap

        loader = MagicMock()
        loader.load_service.return_value = "This file covers connection strings only."
        finding = {"service": "cosmos-db", "context": "Missing retry logic for 429 errors"}
        assert check_knowledge_gap(finding, loader) is True

    def test_prefers_namespace_for_resolution(self):
        from azext_prototype.stages.knowledge_contributor import check_knowledge_gap

        loader = MagicMock()
        loader.load_service.return_value = ""
        finding = {
            "service_namespace": "Microsoft.DocumentDB/databaseAccounts",
            "service": "cosmos-db",
            "context": "Issue found",
        }
        check_knowledge_gap(finding, loader)
        loader.load_service.assert_called_with("Microsoft.DocumentDB/databaseAccounts")

    def test_empty_context_snippet_returns_false(self):
        from azext_prototype.stages.knowledge_contributor import check_knowledge_gap

        loader = MagicMock()
        loader.load_service.return_value = "some content"
        finding = {"service": "x", "context": "   "}  # whitespace only
        assert check_knowledge_gap(finding, loader) is False


# ======================================================================
# format_contribution_title
# ======================================================================


class TestFormatContributionTitle:
    """Test issue title formatting."""

    def test_basic_title(self):
        from azext_prototype.stages.knowledge_contributor import format_contribution_title

        finding = {"service": "cosmos-db", "context": "Short context"}
        title = format_contribution_title(finding)
        assert title == "[Knowledge] cosmos-db: Short context"

    def test_namespace_preferred(self):
        from azext_prototype.stages.knowledge_contributor import format_contribution_title

        finding = {
            "service_namespace": "Microsoft.DocumentDB/databaseAccounts",
            "service": "cosmos-db",
            "context": "Some issue",
        }
        title = format_contribution_title(finding)
        assert "Microsoft.DocumentDB/databaseAccounts" in title

    def test_truncation_at_60_chars(self):
        from azext_prototype.stages.knowledge_contributor import format_contribution_title

        finding = {"service": "x", "context": "A" * 100}
        title = format_contribution_title(finding)
        assert title.endswith("...")
        # The context part should be 60 chars + ...
        assert "A" * 60 in title

    def test_empty_context_fallback(self):
        from azext_prototype.stages.knowledge_contributor import format_contribution_title

        finding = {"service": "x"}
        title = format_contribution_title(finding)
        assert "Knowledge contribution" in title


# ======================================================================
# format_contribution_body
# ======================================================================


class TestFormatContributionBody:
    """Test issue body formatting."""

    def test_basic_body_has_sections(self):
        from azext_prototype.stages.knowledge_contributor import format_contribution_body

        finding = {
            "service": "cosmos-db",
            "service_namespace": "Microsoft.DocumentDB/databaseAccounts",
            "context": "Missing retry",
            "section": "Common Pitfalls",
            "content": "Add retry for 429",
            "source": "QA diagnosis",
        }
        body = format_contribution_body(finding)
        assert "## Knowledge Contribution" in body
        assert "### Context" in body
        assert "### Rationale" in body
        assert "### Content to Add" in body
        assert "### Source" in body
        assert "Common Pitfalls" in body

    def test_new_service_type_promotion(self):
        """When file doesn't exist and type is Pitfall, promote to New service."""
        from azext_prototype.stages.knowledge_contributor import format_contribution_body

        finding = {
            "type": "Pitfall",
            "service": "brand-new",
            "file": "knowledge/services/nonexistent.md",
            "context": "New service info",
        }
        body = format_contribution_body(finding)
        assert "**Type:** New service" in body
        assert "### Required Knowledge File Sections" in body
        assert "NEW FILE" in body

    def test_no_content_placeholder(self):
        from azext_prototype.stages.knowledge_contributor import format_contribution_body

        finding = {"service": "x", "context": "some issue"}
        body = format_contribution_body(finding)
        assert "No specific content provided" in body


# ======================================================================
# submit_contribution
# ======================================================================


class TestSubmitContribution:
    """Test issue submission with auth check and label retry."""

    @patch("azext_prototype.stages.knowledge_contributor.format_contribution_body", return_value="body")
    @patch("azext_prototype.stages.knowledge_contributor.format_contribution_title", return_value="title")
    @patch("azext_prototype.stages.knowledge_contributor._run_gh_issue_create")
    @patch("azext_prototype.stages.backlog_push.check_gh_auth", return_value=True)
    @patch("azext_prototype.debug_log.log_flow")
    def test_success_first_try(self, mock_log, mock_auth, mock_create, mock_title, mock_body):
        from azext_prototype.stages.knowledge_contributor import submit_contribution

        mock_create.return_value = MagicMock(returncode=0, stdout="https://github.com/org/repo/issues/42\n")
        result = submit_contribution({"service": "x", "context": "y"})
        assert result["url"] == "https://github.com/org/repo/issues/42"
        assert result["number"] == "42"

    @patch("azext_prototype.stages.knowledge_contributor._run_gh_issue_create")
    @patch("azext_prototype.stages.backlog_push.check_gh_auth", return_value=True)
    @patch("azext_prototype.debug_log.log_flow")
    def test_label_retry_fallback(self, mock_log, mock_auth, mock_create):
        """First call fails (bad label), retry with fallback labels succeeds."""
        from azext_prototype.stages.knowledge_contributor import submit_contribution

        mock_create.side_effect = [
            MagicMock(returncode=1, stderr="label not found", stdout=""),
            MagicMock(returncode=0, stdout="https://github.com/org/repo/issues/99\n"),
        ]
        result = submit_contribution({"service": "x", "context": "y"})
        assert result["url"] == "https://github.com/org/repo/issues/99"
        assert mock_create.call_count == 2

    @patch("azext_prototype.stages.knowledge_contributor._run_gh_issue_create")
    @patch("azext_prototype.stages.backlog_push.check_gh_auth", return_value=True)
    @patch("azext_prototype.debug_log.log_flow")
    def test_both_attempts_fail(self, mock_log, mock_auth, mock_create):
        from azext_prototype.stages.knowledge_contributor import submit_contribution

        mock_create.side_effect = [
            MagicMock(returncode=1, stderr="error1", stdout=""),
            MagicMock(returncode=1, stderr="error2", stdout=""),
        ]
        result = submit_contribution({"service": "x", "context": "y"})
        assert "error" in result

    @patch("azext_prototype.stages.backlog_push.check_gh_auth", return_value=False)
    def test_auth_check_fails(self, mock_auth):
        from azext_prototype.stages.knowledge_contributor import submit_contribution

        result = submit_contribution({"service": "x", "context": "y"})
        assert "error" in result
        assert "authenticated" in result["error"]

    @patch("azext_prototype.stages.knowledge_contributor._run_gh_issue_create", side_effect=FileNotFoundError)
    @patch("azext_prototype.stages.backlog_push.check_gh_auth", return_value=True)
    @patch("azext_prototype.debug_log.log_flow")
    def test_gh_cli_not_found(self, mock_log, mock_auth, mock_create):
        from azext_prototype.stages.knowledge_contributor import submit_contribution

        result = submit_contribution({"service": "x", "context": "y"})
        assert "error" in result
        assert "gh CLI not found" in result["error"]

    @patch("azext_prototype.stages.knowledge_contributor._run_gh_issue_create")
    @patch("azext_prototype.stages.backlog_push.check_gh_auth", return_value=True)
    @patch("azext_prototype.debug_log.log_flow")
    def test_type_label_mapping(self, mock_log, mock_auth, mock_create):
        """Different contribution types map to correct labels."""
        from azext_prototype.stages.knowledge_contributor import submit_contribution

        mock_create.return_value = MagicMock(returncode=0, stdout="https://github.com/issues/1\n")

        for contrib_type, expected_label in [
            ("New service", "new-service"),
            ("Tool pattern", "tool-pattern"),
            ("Pitfall", "pitfall"),
        ]:
            submit_contribution({"service": "x", "context": "y", "type": contrib_type})
            call_args = mock_create.call_args
            labels = call_args[0][3] if len(call_args[0]) > 3 else call_args[1].get("labels", [])
            assert expected_label in labels


# ======================================================================
# build_finding_from_qa
# ======================================================================


class TestBuildFindingFromQa:
    """Test QA finding builder."""

    def test_basic_finding(self):
        from azext_prototype.stages.knowledge_contributor import build_finding_from_qa

        finding = build_finding_from_qa(
            "Error: timeout connecting to Cosmos DB",
            service="cosmos-db",
            service_namespace="Microsoft.DocumentDB/databaseAccounts",
            section="Common Pitfalls",
        )
        assert finding["service"] == "cosmos-db"
        assert finding["service_namespace"] == "Microsoft.DocumentDB/databaseAccounts"
        assert finding["section"] == "Common Pitfalls"
        assert finding["type"] == "Pitfall"
        assert "timeout" in finding["context"]

    def test_truncation(self):
        from azext_prototype.stages.knowledge_contributor import build_finding_from_qa

        long_text = "A" * 1000
        finding = build_finding_from_qa(long_text)
        assert len(finding["context"]) <= 500
        assert len(finding["content"]) <= 200

    def test_empty_qa_content(self):
        from azext_prototype.stages.knowledge_contributor import build_finding_from_qa

        finding = build_finding_from_qa("")
        assert finding["context"] == ""
        assert finding["content"] == ""


# ======================================================================
# submit_if_gap
# ======================================================================


class TestSubmitIfGap:
    """Test fire-and-forget wrapper."""

    @patch("azext_prototype.stages.knowledge_contributor.submit_contribution")
    @patch("azext_prototype.stages.knowledge_contributor.check_knowledge_gap", return_value=True)
    def test_gap_found_submits(self, mock_gap, mock_submit):
        from azext_prototype.stages.knowledge_contributor import submit_if_gap

        mock_submit.return_value = {"url": "https://github.com/issues/1"}
        printed = []
        result = submit_if_gap(
            {"service": "x", "context": "y"},
            MagicMock(),
            print_fn=printed.append,
        )
        assert result["url"] == "https://github.com/issues/1"
        assert any("submitted" in p for p in printed)

    @patch("azext_prototype.stages.knowledge_contributor.check_knowledge_gap", return_value=False)
    def test_no_gap_returns_none(self, mock_gap):
        from azext_prototype.stages.knowledge_contributor import submit_if_gap

        result = submit_if_gap({"service": "x", "context": "y"}, MagicMock())
        assert result is None

    @patch("azext_prototype.stages.knowledge_contributor.submit_contribution")
    @patch("azext_prototype.stages.knowledge_contributor.check_knowledge_gap", return_value=True)
    def test_submit_error_no_print(self, mock_gap, mock_submit):
        from azext_prototype.stages.knowledge_contributor import submit_if_gap

        mock_submit.return_value = {"error": "auth failed"}
        printed = []
        result = submit_if_gap(
            {"service": "x", "context": "y"},
            MagicMock(),
            print_fn=printed.append,
        )
        assert result["error"] == "auth failed"
        assert len(printed) == 0

    @patch("azext_prototype.stages.knowledge_contributor.check_knowledge_gap", side_effect=RuntimeError("boom"))
    def test_exception_caught_returns_none(self, mock_gap):
        from azext_prototype.stages.knowledge_contributor import submit_if_gap

        result = submit_if_gap({"service": "x", "context": "y"}, MagicMock())
        assert result is None

# --- Additional imports from merged flat test ---
import pytest


# ======================================================================
# Helpers
# ======================================================================


def _make_finding(**overrides) -> dict:
    """Create a minimal finding dict with optional overrides."""
    finding = {
        "service": "cosmos-db",
        "type": "Pitfall",
        "file": "knowledge/services/cosmos-db.md",
        "section": "Terraform Patterns",
        "context": "RU throughput must be set to at least 400 for serverless",
        "rationale": "Setting below 400 causes deployment failure",
        "content": "minimum_throughput = 400",
        "source": "QA diagnosis",
    }
    finding.update(overrides)
    return finding


def _make_loader(service_content: str = "") -> MagicMock:
    """Create a mock KnowledgeLoader that returns *service_content*."""
    loader = MagicMock()
    loader.load_service.return_value = service_content
    return loader


# ======================================================================
# TestKnowledgeContributeCommand
# ======================================================================


class TestKnowledgeContributeCommand:
    """Tests for ``prototype_knowledge_contribute()`` CLI command."""

    def test_draft_mode(self, project_with_config):
        from azext_prototype.custom import prototype_knowledge_contribute

        cmd = MagicMock()
        with patch(f"{_CUSTOM_MODULE}._get_project_dir", return_value=str(project_with_config)):
            result = prototype_knowledge_contribute(
                cmd,
                service="cosmos-db",
                description="RU throughput must be >= 400",
                draft=True,
                json_output=True,
            )

        assert result["status"] == "draft"
        assert "cosmos-db" in result["title"]

    def test_noninteractive_submit(self, project_with_config):
        from azext_prototype.custom import prototype_knowledge_contribute

        cmd = MagicMock()
        with patch(f"{_CUSTOM_MODULE}._get_project_dir", return_value=str(project_with_config)), patch(
            f"{_BP_MODULE}.subprocess.run"
        ) as mock_auth, patch(f"{_KC_MODULE}.subprocess.run") as mock_create:
            mock_auth.return_value = MagicMock(returncode=0)
            mock_create.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/Azure/az-prototype/issues/55\n",
            )

            result = prototype_knowledge_contribute(
                cmd,
                service="cosmos-db",
                description="RU throughput must be >= 400",
                json_output=True,
            )

        assert result["status"] == "submitted"
        assert result["url"] == "https://github.com/Azure/az-prototype/issues/55"

    def test_gh_not_authed_raises(self, project_with_config):
        from knack.util import CLIError

        from azext_prototype.custom import prototype_knowledge_contribute

        cmd = MagicMock()
        with patch(f"{_CUSTOM_MODULE}._get_project_dir", return_value=str(project_with_config)), patch(
            f"{_BP_MODULE}.subprocess.run"
        ) as mock_auth:
            mock_auth.return_value = MagicMock(returncode=1)

            with pytest.raises(CLIError, match="not authenticated"):
                prototype_knowledge_contribute(
                    cmd,
                    service="cosmos-db",
                    description="RU throughput",
                )

    def test_file_input(self, project_with_config):
        from azext_prototype.custom import prototype_knowledge_contribute

        # Create a finding file
        finding_file = project_with_config / "finding.md"
        finding_file.write_text(
            "Service: cosmos-db\nContext: RU must be >= 400\nContent: min_ru = 400",
            encoding="utf-8",
        )

        cmd = MagicMock()
        with patch(f"{_CUSTOM_MODULE}._get_project_dir", return_value=str(project_with_config)):
            result = prototype_knowledge_contribute(
                cmd,
                file=str(finding_file),
                draft=True,
                json_output=True,
            )

        assert result["status"] == "draft"

    def test_file_not_found_raises(self, project_with_config):
        from knack.util import CLIError

        from azext_prototype.custom import prototype_knowledge_contribute

        cmd = MagicMock()
        with patch(f"{_CUSTOM_MODULE}._get_project_dir", return_value=str(project_with_config)):
            with pytest.raises(CLIError, match="not found"):
                prototype_knowledge_contribute(
                    cmd,
                    file="/nonexistent/path/finding.md",
                    draft=True,
                )

    def test_contribution_type_forwarded(self, project_with_config):
        from azext_prototype.custom import prototype_knowledge_contribute

        cmd = MagicMock()
        with patch(f"{_CUSTOM_MODULE}._get_project_dir", return_value=str(project_with_config)):
            result = prototype_knowledge_contribute(
                cmd,
                service="redis",
                description="Cache eviction pitfall",
                contribution_type="Service pattern update",
                section="Pitfalls",
                draft=True,
                json_output=True,
            )

        assert result["status"] == "draft"
        assert "Service pattern update" in result["body"]
        assert "Pitfalls" in result["body"]
