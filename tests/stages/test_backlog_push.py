"""Tests for backlog push helpers — GitHub and Azure DevOps work item creation.

Tier 2: Conditional branches with multiple paths.

Covers:
- check_gh_auth(): success, failure, FileNotFoundError
- check_devops_ext(): success, failure, FileNotFoundError
- format_github_body(): description, acceptance criteria, tasks (str/dict/done),
  children with nested tasks, labels from epic/effort
- format_devops_description(): description, AC, tasks (str/dict/done), effort
- push_github_issue(): success (URL parsing), failure (returncode != 0),
  FileNotFoundError, labels from epic/effort, no-epic title
- push_devops_feature/story/task(): success with JSON, failure,
  FileNotFoundError, parent linking, JSON decode error
- _link_parent(): success, failure (swallowed)
"""

import json
from unittest.mock import MagicMock, patch

from azext_prototype.stages.backlog_push import (
    _link_parent,
    check_devops_ext,
    check_gh_auth,
    format_devops_description,
    format_github_body,
    push_devops_feature,
    push_devops_story,
    push_devops_task,
    push_github_issue,
)

# ------------------------------------------------------------------
# Auth checks
# ------------------------------------------------------------------


class TestCheckGhAuth:
    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_authenticated(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert check_gh_auth() is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["gh", "auth", "status"]

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_not_authenticated(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert check_gh_auth() is False

    @patch("azext_prototype.stages.backlog_push.subprocess.run", side_effect=FileNotFoundError)
    def test_gh_not_installed(self, mock_run):
        assert check_gh_auth() is False


class TestCheckDevopsExt:
    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_installed(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert check_devops_ext() is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["az", "devops", "--help"]

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_not_installed(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert check_devops_ext() is False

    @patch("azext_prototype.stages.backlog_push.subprocess.run", side_effect=FileNotFoundError)
    def test_az_not_found(self, mock_run):
        assert check_devops_ext() is False


# ------------------------------------------------------------------
# Formatters — GitHub
# ------------------------------------------------------------------


class TestFormatGithubBody:
    def test_description_section(self):
        body = format_github_body({"description": "Build an API"})
        assert "## Description" in body
        assert "Build an API" in body

    def test_no_description(self):
        body = format_github_body({"title": "Something"})
        assert "## Description" not in body

    def test_acceptance_criteria(self):
        body = format_github_body({"acceptance_criteria": ["AC1", "AC2"]})
        assert "## Acceptance Criteria" in body
        assert "1. AC1" in body
        assert "2. AC2" in body

    def test_empty_acceptance_criteria(self):
        body = format_github_body({"acceptance_criteria": []})
        assert "## Acceptance Criteria" not in body

    def test_tasks_as_strings(self):
        body = format_github_body({"tasks": ["Task A", "Task B"]})
        assert "## Tasks" in body
        assert "- [ ] Task A" in body
        assert "- [ ] Task B" in body

    def test_tasks_as_dicts_unchecked(self):
        body = format_github_body({"tasks": [{"title": "Task A", "done": False}]})
        assert "- [ ] Task A" in body

    def test_tasks_as_dicts_checked(self):
        body = format_github_body({"tasks": [{"title": "Task Done", "done": True}]})
        assert "- [x] Task Done" in body

    def test_empty_tasks(self):
        body = format_github_body({"tasks": []})
        assert "## Tasks" not in body

    def test_children_section(self):
        item = {
            "children": [
                {
                    "title": "Story 1",
                    "effort": "M",
                    "description": "Story desc",
                    "acceptance_criteria": ["AC1"],
                    "tasks": ["Sub task"],
                }
            ]
        }
        body = format_github_body(item)
        assert "## Stories" in body
        assert "### Story 1 [M]" in body
        assert "Story desc" in body
        assert "1. AC1" in body
        assert "- [ ] Sub task" in body

    def test_children_with_dict_tasks(self):
        item = {
            "children": [
                {
                    "title": "Story",
                    "effort": "S",
                    "tasks": [{"title": "Done task", "done": True}],
                }
            ]
        }
        body = format_github_body(item)
        assert "- [x] Done task" in body

    def test_labels_from_epic_and_effort(self):
        body = format_github_body({"epic": "Infrastructure", "effort": "L"})
        assert "`infrastructure`" in body
        assert "`effort/L`" in body

    def test_no_labels_without_epic_and_effort(self):
        body = format_github_body({"title": "Plain item"})
        assert "**Labels:**" not in body


# ------------------------------------------------------------------
# Formatters — Azure DevOps
# ------------------------------------------------------------------


class TestFormatDevopsDescription:
    def test_description_paragraph(self):
        html = format_devops_description({"description": "Build API"})
        assert "<p>Build API</p>" in html

    def test_no_description(self):
        html = format_devops_description({"title": "X"})
        assert "<p>" not in html

    def test_acceptance_criteria(self):
        html = format_devops_description({"acceptance_criteria": ["AC1", "AC2"]})
        assert "<h3>Acceptance Criteria</h3>" in html
        assert "<li>AC1</li>" in html
        assert "<li>AC2</li>" in html

    def test_empty_acceptance_criteria(self):
        html = format_devops_description({"acceptance_criteria": []})
        assert "Acceptance Criteria" not in html

    def test_tasks_as_strings(self):
        html = format_devops_description({"tasks": ["T1"]})
        assert "<h3>Tasks</h3>" in html
        assert "<li>T1</li>" in html

    def test_tasks_as_dicts_done(self):
        html = format_devops_description({"tasks": [{"title": "T", "done": True}]})
        assert "&#9745;" in html
        assert "T" in html

    def test_tasks_as_dicts_not_done(self):
        html = format_devops_description({"tasks": [{"title": "T", "done": False}]})
        assert "&#9744;" in html

    def test_effort_paragraph(self):
        html = format_devops_description({"effort": "XL"})
        assert "<strong>Effort:</strong> XL" in html

    def test_no_effort(self):
        html = format_devops_description({"title": "X"})
        assert "Effort:" not in html


# ------------------------------------------------------------------
# push_github_issue()
# ------------------------------------------------------------------


class TestPushGithubIssue:
    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="https://github.com/contoso/myproj/issues/42\n",
        )
        result = push_github_issue("contoso", "myproj", {"title": "Add Auth"})
        assert result["url"] == "https://github.com/contoso/myproj/issues/42"
        assert result["number"] == "42"

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_title_with_epic(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="https://github.com/o/p/issues/1\n")
        push_github_issue("o", "p", {"title": "Setup VNet", "epic": "Infrastructure"})
        cmd = mock_run.call_args[0][0]
        assert "[Infrastructure] Setup VNet" in cmd

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_title_without_epic(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="https://github.com/o/p/issues/1\n")
        push_github_issue("o", "p", {"title": "Plain task"})
        cmd = mock_run.call_args[0][0]
        assert "Plain task" in cmd
        # No bracket prefix
        for arg in cmd:
            if arg == "Plain task":
                break
        else:
            # If full_title is used, it should not have brackets
            title_idx = cmd.index("--title") + 1
            assert not cmd[title_idx].startswith("[")

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_labels_from_params_and_item(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="https://github.com/o/p/issues/1\n")
        push_github_issue(
            "o",
            "p",
            {"title": "T", "effort": "M", "epic": "Networking"},
            labels=["prototype", "poc"],
        )
        cmd = mock_run.call_args[0][0]
        # Should have --label for each label
        label_indices = [i for i, v in enumerate(cmd) if v == "--label"]
        labels = [cmd[i + 1] for i in label_indices]
        assert "prototype" in labels
        assert "poc" in labels
        assert "effort/M" in labels
        assert "networking" in labels

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_failure_stderr(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="HTTP 422: Validation Failed",
            stdout="",
        )
        result = push_github_issue("o", "p", {"title": "T"})
        assert "error" in result
        assert "422" in result["error"]

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_failure_stdout_fallback(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="",
            stdout="something went wrong",
        )
        result = push_github_issue("o", "p", {"title": "T"})
        assert "something went wrong" in result["error"]

    @patch("azext_prototype.stages.backlog_push.subprocess.run", side_effect=FileNotFoundError)
    def test_gh_not_found(self, mock_run):
        result = push_github_issue("o", "p", {"title": "T"})
        assert "error" in result
        assert "gh CLI not found" in result["error"]

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_repo_flag(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="https://github.com/o/p/issues/1\n")
        push_github_issue("contoso", "my-repo", {"title": "T"})
        cmd = mock_run.call_args[0][0]
        repo_idx = cmd.index("--repo") + 1
        assert cmd[repo_idx] == "contoso/my-repo"


# ------------------------------------------------------------------
# push_devops_feature / push_devops_story / push_devops_task
# ------------------------------------------------------------------


class TestPushDevopsWorkItem:
    def _mock_success(self, wi_id=123, url="https://dev.azure.com/o/p/_workitems/edit/123"):
        return MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "id": wi_id,
                    "_links": {"html": {"href": url}},
                }
            ),
        )

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_feature_success(self, mock_run):
        mock_run.return_value = self._mock_success(wi_id=10)
        result = push_devops_feature("myorg", "myproj", {"title": "Infra Setup"})
        assert result["id"] == 10
        assert "dev.azure.com" in result["url"]
        # Check work item type
        cmd = mock_run.call_args[0][0]
        type_idx = cmd.index("--type") + 1
        assert cmd[type_idx] == "Feature"

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_story_success(self, mock_run):
        mock_run.return_value = self._mock_success(wi_id=20)
        result = push_devops_story("myorg", "myproj", {"title": "API Story"})
        assert result["id"] == 20
        cmd = mock_run.call_args[0][0]
        type_idx = cmd.index("--type") + 1
        assert cmd[type_idx] == "User Story"

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    @patch("azext_prototype.stages.backlog_push._link_parent")
    def test_story_with_parent(self, mock_link, mock_run):
        mock_run.return_value = self._mock_success(wi_id=20)
        push_devops_story("org", "proj", {"title": "Story"}, parent_id=10)
        mock_link.assert_called_once_with("org", "proj", 20, 10)

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_task_success(self, mock_run):
        mock_run.return_value = self._mock_success(wi_id=30)
        result = push_devops_task("org", "proj", {"title": "Sub task"})
        assert result["id"] == 30
        cmd = mock_run.call_args[0][0]
        type_idx = cmd.index("--type") + 1
        assert cmd[type_idx] == "Task"

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    @patch("azext_prototype.stages.backlog_push._link_parent")
    def test_task_with_parent(self, mock_link, mock_run):
        mock_run.return_value = self._mock_success(wi_id=30)
        push_devops_task("org", "proj", {"title": "Task"}, parent_id=20)
        mock_link.assert_called_once_with("org", "proj", 30, 20)

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="TF401019: Access denied",
            stdout="",
        )
        result = push_devops_feature("org", "proj", {"title": "T"})
        assert "error" in result
        assert "Access denied" in result["error"]

    @patch("azext_prototype.stages.backlog_push.subprocess.run", side_effect=FileNotFoundError)
    def test_az_not_found(self, mock_run):
        result = push_devops_feature("org", "proj", {"title": "T"})
        assert "error" in result
        assert "az CLI not found" in result["error"]

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_json_decode_error(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="not valid json",
        )
        result = push_devops_feature("org", "proj", {"title": "T"})
        # Falls back to raw stdout
        assert result["url"] == ""
        assert result["id"] == "not valid json"

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_epic_area_path(self, mock_run):
        mock_run.return_value = self._mock_success()
        push_devops_feature("org", "proj", {"title": "T", "epic": "Infrastructure"})
        cmd = mock_run.call_args[0][0]
        area_idx = cmd.index("--area") + 1
        assert cmd[area_idx] == "proj\\Infrastructure"

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_no_epic_no_area(self, mock_run):
        mock_run.return_value = self._mock_success()
        push_devops_feature("org", "proj", {"title": "T"})
        cmd = mock_run.call_args[0][0]
        assert "--area" not in cmd

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_org_url_format(self, mock_run):
        mock_run.return_value = self._mock_success()
        push_devops_feature("contoso", "myproj", {"title": "T"})
        cmd = mock_run.call_args[0][0]
        org_idx = cmd.index("--org") + 1
        assert cmd[org_idx] == "https://dev.azure.com/contoso"

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_url_fallback_to_data_url(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "id": 99,
                    "_links": {},
                    "url": "https://dev.azure.com/o/p/_apis/wit/workItems/99",
                }
            ),
        )
        result = push_devops_feature("o", "p", {"title": "T"})
        assert result["url"] == "https://dev.azure.com/o/p/_apis/wit/workItems/99"

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    @patch("azext_prototype.stages.backlog_push._link_parent")
    def test_no_parent_link_when_parent_id_none(self, mock_link, mock_run):
        mock_run.return_value = self._mock_success(wi_id=50)
        push_devops_story("o", "p", {"title": "T"}, parent_id=None)
        mock_link.assert_not_called()


# ------------------------------------------------------------------
# _link_parent()
# ------------------------------------------------------------------


class TestLinkParent:
    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_link_parent_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        _link_parent("org", "proj", child_id=20, parent_id=10)
        cmd = mock_run.call_args[0][0]
        assert "relation" in cmd
        assert "add" in cmd
        assert "--id" in cmd
        assert "20" in cmd
        assert "--target-id" in cmd
        assert "10" in cmd
        assert "--relation-type" in cmd
        assert "parent" in cmd

    @patch("azext_prototype.stages.backlog_push.subprocess.run", side_effect=FileNotFoundError)
    def test_link_parent_file_not_found(self, mock_run):
        # Should not raise
        _link_parent("org", "proj", child_id=20, parent_id=10)

    @patch("azext_prototype.stages.backlog_push.subprocess.run")
    def test_link_parent_subprocess_error(self, mock_run):
        import subprocess

        mock_run.side_effect = subprocess.SubprocessError("broken pipe")
        # Should not raise
        _link_parent("org", "proj", child_id=20, parent_id=10)
