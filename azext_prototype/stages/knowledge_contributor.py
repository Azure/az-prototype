"""Knowledge contribution helpers — submit knowledge gaps as GitHub Issues.

Provides reusable utilities for submitting knowledge base contributions
when patterns or pitfalls are discovered during QA diagnosis or manually
via the CLI.  Follows the ``backlog_push.py`` pattern: module-level
functions, dict return values, no exceptions from public API.

- **Gap detection**: Check if a finding is already covered by knowledge files
- **Formatting**: Produce structured GitHub Issue bodies matching the template
- **Submission**: Create issues via ``gh`` CLI with appropriate labels
- **Fire-and-forget wrapper**: Check gap + submit + info line in one call
"""

import logging
import subprocess
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Default repository for knowledge contributions
_DEFAULT_REPO = "Azure/az-prototype"


# ======================================================================
# Gap Detection
# ======================================================================

def check_knowledge_gap(finding: dict, knowledge_loader: Any) -> bool:
    """Check whether a finding represents a gap in the knowledge base.

    Returns ``True`` if the finding describes something not currently
    covered by the relevant service knowledge file.  Returns ``False``
    if the content already exists or the finding is empty.

    Uses a substring match on the first 80 characters of the finding's
    ``context`` field against the loaded service file content.
    """
    if not finding:
        return False

    service = finding.get("service", "")
    context = finding.get("context", "")
    if not service or not context:
        return False

    # Load the service knowledge file
    try:
        content = knowledge_loader.load_service(service)
    except Exception:
        content = ""

    # If no file exists for this service, it's a gap
    if not content:
        return True

    # Check if the first 80 chars of context are already covered
    snippet = context[:80].strip()
    if not snippet:
        return False

    return snippet.lower() not in content.lower()


# ======================================================================
# Formatters
# ======================================================================

def format_contribution_title(finding: dict) -> str:
    """Format a finding as a GitHub Issue title.

    Produces ``"[Knowledge] {service}: {context[:60]}"``.
    """
    service = finding.get("service", "unknown")
    context = finding.get("context", "") or finding.get("description", "")
    if not context:
        context = "Knowledge contribution"
    truncated = context[:60].strip()
    if len(context) > 60:
        truncated += "..."
    return f"[Knowledge] {service}: {truncated}"


def format_contribution_body(finding: dict) -> str:
    """Format a finding as a structured GitHub Issue body.

    Produces markdown matching the knowledge-contribution issue template
    with Type, File, Section, Context, Rationale, Content to Add, and
    Source sections.
    """
    contribution_type = finding.get("type", "Pitfall")
    service = finding.get("service", "unknown")
    file_path = finding.get("file", f"knowledge/services/{service}.md")
    section = finding.get("section", "")
    context = finding.get("context", "")
    rationale = finding.get("rationale", context)
    content = finding.get("content", "")
    source = finding.get("source", "QA diagnosis")

    lines: list[str] = []
    lines.append("## Knowledge Contribution")
    lines.append("")
    lines.append(f"**Type:** {contribution_type}")
    lines.append(f"**File:** `{file_path}`")
    if section:
        lines.append(f"**Section:** {section}")
    lines.append("")
    lines.append("### Context")
    lines.append(context or "No context provided.")
    lines.append("")
    lines.append("### Rationale")
    lines.append(rationale or "No rationale provided.")
    lines.append("")
    lines.append("### Content to Add")
    if content:
        lines.append("```")
        lines.append(content)
        lines.append("```")
    else:
        lines.append("*(No specific content provided — review context above.)*")
    lines.append("")
    lines.append("### Source")
    lines.append(source)

    return "\n".join(lines)


# ======================================================================
# Submission
# ======================================================================

def submit_contribution(
    finding: dict,
    repo: str = _DEFAULT_REPO,
) -> dict[str, Any]:
    """Create a GitHub Issue for a knowledge contribution via ``gh`` CLI.

    Returns ``{url, number}`` on success or ``{error}`` on failure.
    Reuses ``check_gh_auth()`` from ``backlog_push`` for auth validation.
    """
    from azext_prototype.stages.backlog_push import check_gh_auth

    if not check_gh_auth():
        return {"error": "gh CLI not authenticated. Run: gh auth login"}

    title = format_contribution_title(finding)
    body = format_contribution_body(finding)

    # Build labels
    service = finding.get("service", "")
    contribution_type = finding.get("type", "Pitfall")
    labels = ["knowledge-contribution"]
    if service:
        labels.append(f"service/{service}")

    type_label_map = {
        "Service pattern update": "pattern-update",
        "New service": "new-service",
        "Tool pattern": "tool-pattern",
        "Language pattern": "language-pattern",
        "Pitfall": "pitfall",
    }
    type_label = type_label_map.get(contribution_type, "pitfall")
    labels.append(type_label)

    cmd = [
        "gh", "issue", "create",
        "--title", title,
        "--body", body,
        "--repo", repo,
    ]
    for label in labels:
        cmd.extend(["--label", label])

    logger.info("Creating knowledge contribution issue: %s", title)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            logger.error("gh issue create failed: %s", error)
            return {"error": error}

        url = result.stdout.strip()
        number = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
        return {"url": url, "number": number}

    except FileNotFoundError:
        return {"error": "gh CLI not found. Install: https://cli.github.com/"}


# ======================================================================
# QA Integration
# ======================================================================

def build_finding_from_qa(
    qa_content: str,
    service: str = "unknown",
    source: str = "QA diagnosis",
) -> dict:
    """Convert raw QA text into a finding dict.

    Extracts a reasonable context snippet from the QA response and
    packages it as a finding suitable for ``submit_contribution()``.
    """
    # Use the first 500 chars as context, first 200 as content
    context = qa_content[:500].strip() if qa_content else ""
    content = qa_content[:200].strip() if qa_content else ""

    return {
        "service": service,
        "type": "Pitfall",
        "file": f"knowledge/services/{service}.md",
        "section": "",
        "context": context,
        "rationale": context,
        "content": content,
        "source": source,
    }


# ======================================================================
# Fire-and-Forget Wrapper
# ======================================================================

def submit_if_gap(
    finding: dict,
    loader: Any,
    repo: str = _DEFAULT_REPO,
    print_fn: Callable[[str], None] | None = None,
) -> dict | None:
    """Check for a knowledge gap and submit if found.

    Fire-and-forget wrapper: checks the gap, submits the issue, and
    prints an info line.  Never raises — all exceptions are caught
    and logged silently.

    Returns the submission result dict or ``None`` if no gap or on error.
    """
    try:
        if not check_knowledge_gap(finding, loader):
            return None

        result = submit_contribution(finding, repo=repo)

        if result.get("url") and print_fn:
            print_fn(f"  Knowledge contribution submitted: {result['url']}")

        return result
    except Exception:
        logger.debug("Knowledge contribution failed silently", exc_info=True)
        return None
