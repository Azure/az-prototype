"""Tests for knowledge contribution helpers.

Covers gap detection, formatting, submission via ``gh`` CLI, QA integration,
the fire-and-forget wrapper, and the CLI command ``az prototype knowledge
contribute``.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

_KC_MODULE = "azext_prototype.stages.knowledge_contributor"
_BP_MODULE = "azext_prototype.stages.backlog_push"
_CUSTOM_MODULE = "azext_prototype.custom"


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
# TestFormatContributionBody
# ======================================================================

class TestFormatContributionBody:
    """Tests for ``format_contribution_body()``."""

    def test_basic_format(self):
        from azext_prototype.stages.knowledge_contributor import format_contribution_body

        finding = _make_finding()
        body = format_contribution_body(finding)

        assert "## Knowledge Contribution" in body
        assert "**Type:** Pitfall" in body
        assert "**File:** `knowledge/services/cosmos-db.md`" in body
        assert "**Section:** Terraform Patterns" in body
        assert "### Context" in body
        assert "RU throughput" in body
        assert "### Rationale" in body
        assert "### Content to Add" in body
        assert "minimum_throughput = 400" in body
        assert "### Source" in body
        assert "QA diagnosis" in body

    def test_missing_fields_defaults(self):
        from azext_prototype.stages.knowledge_contributor import format_contribution_body

        finding = {"service": "redis"}
        body = format_contribution_body(finding)

        assert "**Type:** Pitfall" in body
        assert "`knowledge/services/redis.md`" in body
        assert "No context provided." in body
        assert "No rationale provided." in body
        assert "No specific content provided" in body

    def test_empty_content(self):
        from azext_prototype.stages.knowledge_contributor import format_contribution_body

        finding = _make_finding(content="")
        body = format_contribution_body(finding)

        assert "No specific content provided" in body


# ======================================================================
# TestFormatContributionTitle
# ======================================================================

class TestFormatContributionTitle:
    """Tests for ``format_contribution_title()``."""

    def test_basic_title(self):
        from azext_prototype.stages.knowledge_contributor import format_contribution_title

        finding = _make_finding()
        title = format_contribution_title(finding)

        assert title.startswith("[Knowledge] cosmos-db:")
        assert "RU throughput" in title

    def test_truncation_at_60(self):
        from azext_prototype.stages.knowledge_contributor import format_contribution_title

        long_context = "A" * 100
        finding = _make_finding(context=long_context)
        title = format_contribution_title(finding)

        # Title should contain truncated context + ellipsis
        assert "..." in title
        # The service prefix + 60 chars + "..." should be in there
        assert len(title) < 120

    def test_missing_service(self):
        from azext_prototype.stages.knowledge_contributor import format_contribution_title

        finding = _make_finding(service="")
        # Falls back to "unknown" since service key exists but is empty
        # Actually the default in the function is "unknown" for missing key
        finding.pop("service")
        title = format_contribution_title(finding)

        assert "[Knowledge] unknown:" in title

    def test_description_fallback(self):
        from azext_prototype.stages.knowledge_contributor import format_contribution_title

        finding = _make_finding(context="", description="fallback description")
        title = format_contribution_title(finding)

        assert "fallback description" in title


# ======================================================================
# TestCheckKnowledgeGap
# ======================================================================

class TestCheckKnowledgeGap:
    """Tests for ``check_knowledge_gap()``."""

    def test_no_file_is_gap(self):
        from azext_prototype.stages.knowledge_contributor import check_knowledge_gap

        loader = _make_loader("")  # empty = no file
        finding = _make_finding()

        assert check_knowledge_gap(finding, loader) is True

    def test_content_not_found_is_gap(self):
        from azext_prototype.stages.knowledge_contributor import check_knowledge_gap

        # Service file exists but doesn't contain the finding's context
        loader = _make_loader("Some unrelated content about key vault.")
        finding = _make_finding()

        assert check_knowledge_gap(finding, loader) is True

    def test_content_found_is_not_gap(self):
        from azext_prototype.stages.knowledge_contributor import check_knowledge_gap

        # The first 80 chars of context appear in the service file
        finding = _make_finding()
        context_snippet = finding["context"][:80].lower()
        loader = _make_loader(f"Some preamble. {context_snippet} and more details.")

        assert check_knowledge_gap(finding, loader) is False

    def test_empty_finding_is_not_gap(self):
        from azext_prototype.stages.knowledge_contributor import check_knowledge_gap

        loader = _make_loader("")
        assert check_knowledge_gap({}, loader) is False
        assert check_knowledge_gap(None, loader) is False

    def test_missing_service_is_not_gap(self):
        from azext_prototype.stages.knowledge_contributor import check_knowledge_gap

        loader = _make_loader("")
        finding = _make_finding(service="")
        assert check_knowledge_gap(finding, loader) is False

    def test_missing_context_is_not_gap(self):
        from azext_prototype.stages.knowledge_contributor import check_knowledge_gap

        loader = _make_loader("")
        finding = _make_finding(context="")
        assert check_knowledge_gap(finding, loader) is False

    def test_loader_exception_treated_as_gap(self):
        from azext_prototype.stages.knowledge_contributor import check_knowledge_gap

        loader = MagicMock()
        loader.load_service.side_effect = Exception("file not found")
        finding = _make_finding()

        # Exception means no content found => gap
        assert check_knowledge_gap(finding, loader) is True


# ======================================================================
# TestSubmitContribution
# ======================================================================

class TestSubmitContribution:
    """Tests for ``submit_contribution()``."""

    def test_success(self):
        from azext_prototype.stages.knowledge_contributor import submit_contribution

        with patch(f"{_BP_MODULE}.subprocess.run") as mock_auth, \
             patch(f"{_KC_MODULE}.subprocess.run") as mock_create:
            mock_auth.return_value = MagicMock(returncode=0)
            mock_create.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/Azure/az-prototype/issues/42\n",
            )

            result = submit_contribution(_make_finding())

            assert result["url"] == "https://github.com/Azure/az-prototype/issues/42"
            assert result["number"] == "42"

    def test_gh_not_authed(self):
        from azext_prototype.stages.knowledge_contributor import submit_contribution

        with patch(f"{_BP_MODULE}.subprocess.run") as mock_auth:
            mock_auth.return_value = MagicMock(returncode=1)

            result = submit_contribution(_make_finding())
            assert "error" in result
            assert "not authenticated" in result["error"].lower()

    def test_create_fails(self):
        from azext_prototype.stages.knowledge_contributor import submit_contribution

        with patch(f"{_BP_MODULE}.subprocess.run") as mock_auth, \
             patch(f"{_KC_MODULE}.subprocess.run") as mock_create:
            mock_auth.return_value = MagicMock(returncode=0)
            mock_create.return_value = MagicMock(
                returncode=1,
                stderr="label 'pitfall' not found",
                stdout="",
            )

            result = submit_contribution(_make_finding())
            assert "error" in result

    def test_labels_include_service_and_type(self):
        from azext_prototype.stages.knowledge_contributor import submit_contribution

        with patch(f"{_BP_MODULE}.subprocess.run") as mock_auth, \
             patch(f"{_KC_MODULE}.subprocess.run") as mock_create:
            mock_auth.return_value = MagicMock(returncode=0)
            mock_create.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/Azure/az-prototype/issues/99\n",
            )

            finding = _make_finding(service="key-vault", type="Service pattern update")
            submit_contribution(finding)

            # Check the command args include service and type labels
            call_args = mock_create.call_args[0][0]
            label_indices = [i for i, a in enumerate(call_args) if a == "--label"]
            labels = [call_args[i + 1] for i in label_indices]
            assert "knowledge-contribution" in labels
            assert "service/key-vault" in labels
            assert "pattern-update" in labels

    def test_custom_repo(self):
        from azext_prototype.stages.knowledge_contributor import submit_contribution

        with patch(f"{_BP_MODULE}.subprocess.run") as mock_auth, \
             patch(f"{_KC_MODULE}.subprocess.run") as mock_create:
            mock_auth.return_value = MagicMock(returncode=0)
            mock_create.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/myorg/myrepo/issues/1\n",
            )

            result = submit_contribution(_make_finding(), repo="myorg/myrepo")

            call_args = mock_create.call_args[0][0]
            repo_idx = call_args.index("--repo")
            assert call_args[repo_idx + 1] == "myorg/myrepo"
            assert result["url"] == "https://github.com/myorg/myrepo/issues/1"

    def test_gh_not_installed(self):
        from azext_prototype.stages.knowledge_contributor import submit_contribution

        # Mock check_gh_auth at its source (both modules share the subprocess object)
        with patch(f"{_BP_MODULE}.check_gh_auth", return_value=True), \
             patch(f"{_KC_MODULE}.subprocess.run") as mock_create:
            mock_create.side_effect = FileNotFoundError

            result = submit_contribution(_make_finding())
            assert "error" in result
            assert "not found" in result["error"].lower()


# ======================================================================
# TestBuildFindingFromQa
# ======================================================================

class TestBuildFindingFromQa:
    """Tests for ``build_finding_from_qa()``."""

    def test_builds_from_qa_text(self):
        from azext_prototype.stages.knowledge_contributor import build_finding_from_qa

        qa_text = "The Cosmos DB RU throughput was set below the minimum of 400."
        finding = build_finding_from_qa(qa_text, service="cosmos-db", source="Deploy failure: Stage 2")

        assert finding["service"] == "cosmos-db"
        assert finding["type"] == "Pitfall"
        assert finding["source"] == "Deploy failure: Stage 2"
        assert "cosmos-db" in finding["file"]
        assert "400" in finding["context"]
        assert "400" in finding["content"]

    def test_truncates_long_content(self):
        from azext_prototype.stages.knowledge_contributor import build_finding_from_qa

        long_text = "X" * 1000
        finding = build_finding_from_qa(long_text, service="redis")

        assert len(finding["context"]) <= 500
        assert len(finding["content"]) <= 200

    def test_empty_qa_text(self):
        from azext_prototype.stages.knowledge_contributor import build_finding_from_qa

        finding = build_finding_from_qa("", service="redis")
        assert finding["context"] == ""
        assert finding["content"] == ""

    def test_defaults(self):
        from azext_prototype.stages.knowledge_contributor import build_finding_from_qa

        finding = build_finding_from_qa("some content")
        assert finding["service"] == "unknown"
        assert finding["source"] == "QA diagnosis"


# ======================================================================
# TestSubmitIfGap
# ======================================================================

class TestSubmitIfGap:
    """Tests for ``submit_if_gap()``."""

    def test_submits_when_gap(self):
        from azext_prototype.stages.knowledge_contributor import submit_if_gap

        loader = _make_loader("")  # no content = gap
        printed: list[str] = []

        with patch(f"{_BP_MODULE}.subprocess.run") as mock_auth, \
             patch(f"{_KC_MODULE}.subprocess.run") as mock_create:
            mock_auth.return_value = MagicMock(returncode=0)
            mock_create.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/Azure/az-prototype/issues/7\n",
            )

            result = submit_if_gap(
                _make_finding(), loader,
                print_fn=printed.append,
            )

        assert result is not None
        assert result["url"] == "https://github.com/Azure/az-prototype/issues/7"
        assert any("submitted" in p.lower() for p in printed)

    def test_skips_when_no_gap(self):
        from azext_prototype.stages.knowledge_contributor import submit_if_gap

        # Content already exists in knowledge file
        finding = _make_finding()
        loader = _make_loader(finding["context"][:80].lower() + " more details")
        printed: list[str] = []

        result = submit_if_gap(finding, loader, print_fn=printed.append)

        assert result is None
        assert len(printed) == 0

    def test_never_raises(self):
        from azext_prototype.stages.knowledge_contributor import submit_if_gap

        # Loader throws an exception
        loader = MagicMock()
        loader.load_service.side_effect = RuntimeError("kaboom")

        # Even if gap check raises inside, submit_if_gap should not propagate
        # Actually check_knowledge_gap catches it and returns True, then
        # submit_contribution is called — let's make that fail too
        with patch(f"{_KC_MODULE}.submit_contribution") as mock_submit:
            mock_submit.side_effect = RuntimeError("double kaboom")

            result = submit_if_gap(_make_finding(), loader)

        # Should return None, not raise
        assert result is None

    def test_no_print_when_no_url(self):
        from azext_prototype.stages.knowledge_contributor import submit_if_gap

        loader = _make_loader("")  # gap
        printed: list[str] = []

        with patch(f"{_BP_MODULE}.subprocess.run") as mock_auth, \
             patch(f"{_KC_MODULE}.subprocess.run") as mock_create:
            mock_auth.return_value = MagicMock(returncode=0)
            mock_create.return_value = MagicMock(
                returncode=1,
                stderr="error",
                stdout="",
            )

            result = submit_if_gap(
                _make_finding(), loader,
                print_fn=printed.append,
            )

        # Error result, no URL to print
        assert len(printed) == 0


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
            )

        assert result["status"] == "draft"
        assert "cosmos-db" in result["title"]

    def test_noninteractive_submit(self, project_with_config):
        from azext_prototype.custom import prototype_knowledge_contribute

        cmd = MagicMock()
        with patch(f"{_CUSTOM_MODULE}._get_project_dir", return_value=str(project_with_config)), \
             patch(f"{_BP_MODULE}.subprocess.run") as mock_auth, \
             patch(f"{_KC_MODULE}.subprocess.run") as mock_create:
            mock_auth.return_value = MagicMock(returncode=0)
            mock_create.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/Azure/az-prototype/issues/55\n",
            )

            result = prototype_knowledge_contribute(
                cmd,
                service="cosmos-db",
                description="RU throughput must be >= 400",
            )

        assert result["status"] == "submitted"
        assert result["url"] == "https://github.com/Azure/az-prototype/issues/55"

    def test_gh_not_authed_raises(self, project_with_config):
        from azext_prototype.custom import prototype_knowledge_contribute
        from knack.util import CLIError

        cmd = MagicMock()
        with patch(f"{_CUSTOM_MODULE}._get_project_dir", return_value=str(project_with_config)), \
             patch(f"{_BP_MODULE}.subprocess.run") as mock_auth:
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
            )

        assert result["status"] == "draft"

    def test_file_not_found_raises(self, project_with_config):
        from azext_prototype.custom import prototype_knowledge_contribute
        from knack.util import CLIError

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
            )

        assert result["status"] == "draft"
        assert "Service pattern update" in result["body"]
        assert "Pitfalls" in result["body"]
