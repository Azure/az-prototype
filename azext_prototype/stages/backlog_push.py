"""Backlog push helpers — GitHub and Azure DevOps work item creation.

Provides reusable push utilities used by ``backlog_session.py``:

- **GitHub**: Create issues via ``gh`` CLI with task checklists
- **Azure DevOps**: Create Features/User Stories/Tasks via ``az boards``
- **Auth checks**: Verify CLI tools are authenticated
- **Formatters**: Convert structured items to provider-specific bodies
"""

import json
import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


# ======================================================================
# Auth Checks
# ======================================================================


def check_gh_auth() -> bool:
    """Verify gh CLI is installed and authenticated."""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def check_devops_ext() -> bool:
    """Verify az devops extension is installed and configured."""
    try:
        result = subprocess.run(
            ["az", "devops", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


# ======================================================================
# Formatters
# ======================================================================


def format_github_body(item: dict) -> str:
    """Format a backlog item as a GitHub issue body with task checklists.

    Produces markdown with Description, Acceptance Criteria, and Tasks
    sections. Tasks use GitHub-flavored task list syntax (``- [ ]``).
    """
    lines: list[str] = []

    description = item.get("description", "")
    if description:
        lines.append("## Description")
        lines.append(description)
        lines.append("")

    ac = item.get("acceptance_criteria", [])
    if ac:
        lines.append("## Acceptance Criteria")
        for i, criterion in enumerate(ac, 1):
            lines.append(f"{i}. {criterion}")
        lines.append("")

    tasks = item.get("tasks", [])
    if tasks:
        lines.append("## Tasks")
        for task in tasks:
            lines.append(f"- [ ] {task}")
        lines.append("")

    # Children (for hierarchical items)
    children = item.get("children", [])
    if children:
        lines.append("## Stories")
        for child in children:
            child_title = child.get("title", "")
            child_effort = child.get("effort", "")
            lines.append(f"### {child_title} [{child_effort}]")
            child_desc = child.get("description", "")
            if child_desc:
                lines.append(child_desc)
                lines.append("")
            child_ac = child.get("acceptance_criteria", [])
            if child_ac:
                for i, c in enumerate(child_ac, 1):
                    lines.append(f"{i}. {c}")
                lines.append("")
            child_tasks = child.get("tasks", [])
            if child_tasks:
                for t in child_tasks:
                    lines.append(f"- [ ] {t}")
                lines.append("")

    effort = item.get("effort", "")
    epic = item.get("epic", "")
    label_parts = []
    if epic:
        label_parts.append(epic.lower().replace(" ", "-"))
    if effort:
        label_parts.append(f"effort/{effort}")

    if label_parts:
        lines.append(f"**Labels:** {', '.join(f'`{lbl}`' for lbl in label_parts)}")

    return "\n".join(lines)


def format_devops_description(item: dict) -> str:
    """Format a backlog item as Azure DevOps HTML description."""
    parts: list[str] = []

    description = item.get("description", "")
    if description:
        parts.append(f"<p>{description}</p>")

    ac = item.get("acceptance_criteria", [])
    if ac:
        parts.append("<h3>Acceptance Criteria</h3><ol>")
        for criterion in ac:
            parts.append(f"<li>{criterion}</li>")
        parts.append("</ol>")

    tasks = item.get("tasks", [])
    if tasks:
        parts.append("<h3>Tasks</h3><ul>")
        for task in tasks:
            parts.append(f"<li>{task}</li>")
        parts.append("</ul>")

    effort = item.get("effort", "")
    if effort:
        parts.append(f"<p><strong>Effort:</strong> {effort}</p>")

    return "\n".join(parts)


# ======================================================================
# GitHub Push
# ======================================================================


def push_github_issue(
    org: str,
    project: str,
    item: dict,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    """Create a GitHub issue via gh CLI.

    Returns ``{url, number}`` on success or ``{error}`` on failure.
    """
    title = item.get("title", "Untitled")
    epic = item.get("epic", "")
    if epic:
        full_title = f"[{epic}] {title}"
    else:
        full_title = title

    body = format_github_body(item)

    cmd = [
        "gh",
        "issue",
        "create",
        "--title",
        full_title,
        "--body",
        body,
        "--repo",
        f"{org}/{project}",
    ]

    # Add labels
    all_labels = list(labels or [])
    effort = item.get("effort", "")
    if effort:
        all_labels.append(f"effort/{effort}")
    if epic:
        all_labels.append(epic.lower().replace(" ", "-"))

    for label in all_labels:
        cmd.extend(["--label", label])

    logger.info("Creating GitHub issue: %s", full_title)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            logger.error("gh issue create failed: %s", error)
            return {"error": error}

        url = result.stdout.strip()
        # Extract issue number from URL (last path segment)
        number = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
        return {"url": url, "number": number}

    except FileNotFoundError:
        return {"error": "gh CLI not found. Install: https://cli.github.com/"}


# ======================================================================
# Azure DevOps Push
# ======================================================================


def push_devops_feature(
    org: str,
    project: str,
    item: dict,
) -> dict[str, Any]:
    """Create an Azure DevOps Feature via az boards.

    Returns ``{url, id}`` on success or ``{error}`` on failure.
    """
    return _push_devops_work_item(org, project, item, work_item_type="Feature")


def push_devops_story(
    org: str,
    project: str,
    item: dict,
    parent_id: int | None = None,
) -> dict[str, Any]:
    """Create a User Story as child of Feature.

    Returns ``{url, id}`` on success or ``{error}`` on failure.
    """
    return _push_devops_work_item(
        org,
        project,
        item,
        work_item_type="User Story",
        parent_id=parent_id,
    )


def push_devops_task(
    org: str,
    project: str,
    item: dict,
    parent_id: int | None = None,
) -> dict[str, Any]:
    """Create a Task as child of User Story.

    Returns ``{url, id}`` on success or ``{error}`` on failure.
    """
    return _push_devops_work_item(
        org,
        project,
        item,
        work_item_type="Task",
        parent_id=parent_id,
    )


def _push_devops_work_item(
    org: str,
    project: str,
    item: dict,
    work_item_type: str = "User Story",
    parent_id: int | None = None,
) -> dict[str, Any]:
    """Create an Azure DevOps work item via az boards.

    Internal helper used by ``push_devops_feature``, ``push_devops_story``,
    and ``push_devops_task``.
    """
    title = item.get("title", "Untitled")
    description = format_devops_description(item)

    cmd = [
        "az",
        "boards",
        "work-item",
        "create",
        "--type",
        work_item_type,
        "--title",
        title,
        "--description",
        description,
        "--org",
        f"https://dev.azure.com/{org}",
        "--project",
        project,
        "--output",
        "json",
    ]

    # Add area path for epic grouping
    epic = item.get("epic", "")
    if epic:
        cmd.extend(["--area", f"{project}\\{epic}"])

    # Link to parent if provided
    # Note: parent linking requires a separate API call or --parent flag
    # which may not be available in all az boards versions

    logger.info("Creating DevOps %s: %s", work_item_type, title)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            logger.error("az boards work-item create failed: %s", error)
            return {"error": error}

        try:
            data = json.loads(result.stdout)
            wi_id = data.get("id", "")
            url = data.get("_links", {}).get("html", {}).get("href", "")
            if not url:
                url = data.get("url", "")

            # Link to parent if needed
            if parent_id and wi_id:
                _link_parent(org, project, wi_id, parent_id)

            return {"url": url, "id": wi_id}
        except (json.JSONDecodeError, KeyError):
            return {"url": "", "id": result.stdout.strip()}

    except FileNotFoundError:
        return {"error": "az CLI not found."}


def _link_parent(org: str, project: str, child_id: int, parent_id: int) -> None:
    """Link a child work item to a parent via az boards relation."""
    try:
        subprocess.run(
            [
                "az",
                "boards",
                "work-item",
                "relation",
                "add",
                "--id",
                str(child_id),
                "--relation-type",
                "parent",
                "--target-id",
                str(parent_id),
                "--org",
                f"https://dev.azure.com/{org}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        logger.warning("Could not link work item %s to parent %s", child_id, parent_id)
