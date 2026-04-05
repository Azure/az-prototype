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

    Resolves by ``service_namespace`` (ARM resource type) first, then
    falls back to ``service`` (friendly name).
    """
    if not finding:
        return False

    # Prefer namespace for resolution
    service_id = finding.get("service_namespace") or finding.get("service", "")
    context = finding.get("context", "")
    if not service_id or not context:
        return False

    # Load the service knowledge file (KnowledgeLoader resolves by namespace first)
    try:
        content = knowledge_loader.load_service(service_id)
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

    Produces ``"[Knowledge] {namespace or service}: {context[:60]}"``.
    """
    namespace = finding.get("service_namespace", "")
    service = namespace or finding.get("service", "unknown")
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
    with Type, Namespace, File, Section, Context, Rationale, Content to Add,
    and Source sections.

    For new-service findings, includes the full 8-section template that
    the knowledge file must contain.
    """
    contribution_type = finding.get("type", "Pitfall")
    service = finding.get("service", "unknown")
    namespace = finding.get("service_namespace", "")
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
    if namespace:
        lines.append(f"**Service Namespace:** `{namespace}`")
    lines.append(f"**File:** `{file_path}`")
    if section:
        lines.append(f"**Section to update:** {section}")
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

    # For new services, include the required file template
    if contribution_type == "New service":
        lines.append("")
        lines.append("### Required Knowledge File Sections")
        lines.append("The new knowledge file MUST include ALL of these sections:")
        lines.append("1. **Description** (one-line summary)")
        lines.append("2. **When to Use** (scenarios and selection criteria)")
        lines.append("3. **POC Defaults** (default SKU, tier, configuration)")
        lines.append("4. **Terraform Patterns** (azapi_resource with RBAC)")
        lines.append("5. **Bicep Patterns** (ARM template resources)")
        lines.append("6. **Application Code** (Python, C#, Node.js — where applicable)")
        lines.append("7. **Common Pitfalls** (deployment failures, misconfigurations)")
        lines.append("8. **Production Backlog Items** (what changes for production)")

    return "\n".join(lines)


# ======================================================================
# Submission
# ======================================================================


def _run_gh_issue_create(title: str, body: str, repo: str, labels: list[str]) -> subprocess.CompletedProcess:
    """Run ``gh issue create`` with the given labels."""
    cmd = ["gh", "issue", "create", "--title", title, "--body", body, "--repo", repo]
    for label in labels:
        cmd.extend(["--label", label])
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


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

    from azext_prototype.debug_log import log_flow

    log_flow("knowledge_contributor.submit", "Creating issue", title=title, repo=repo, labels=labels)
    try:
        result = _run_gh_issue_create(title, body, repo, labels)
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            log_flow("knowledge_contributor.submit", "Failed with labels, retrying with fallback", error=error)

            # Retry with fallback labels — service label might not exist
            result = _run_gh_issue_create(title, body, repo, ["knowledge-contribution", "new-service"])
            if result.returncode != 0:
                error = result.stderr.strip() or result.stdout.strip()
                log_flow("knowledge_contributor.submit", "Fallback also failed", error=error)
                return {"error": error}

        url = result.stdout.strip()
        number = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
        log_flow("knowledge_contributor.submit", "Issue created", url=url, number=number)
        return {"url": url, "number": number}

    except FileNotFoundError:
        return {"error": "gh CLI not found. Install: https://cli.github.com/"}


# ======================================================================
# QA Integration
# ======================================================================


def build_finding_from_qa(
    qa_content: str,
    service: str = "unknown",
    service_namespace: str = "",
    source: str = "QA diagnosis",
    section: str = "",
) -> dict:
    """Convert raw QA text into a finding dict.

    Extracts a reasonable context snippet from the QA response and
    packages it as a finding suitable for ``submit_contribution()``.

    Parameters
    ----------
    service:
        Friendly service name (e.g., ``cosmos-db``).
    service_namespace:
        ARM resource type namespace (e.g., ``Microsoft.DocumentDB/databaseAccounts``).
        Preferred over ``service`` for file resolution.
    section:
        Which of the 8 knowledge sections needs updating (e.g.,
        ``"Common Pitfalls"``, ``"Terraform Patterns"``).
    """
    # Use the first 500 chars as context, first 200 as content
    context = qa_content[:500].strip() if qa_content else ""
    content = qa_content[:200].strip() if qa_content else ""

    return {
        "service": service,
        "service_namespace": service_namespace,
        "type": "Pitfall",
        "file": f"knowledge/services/{service}.md",
        "section": section,
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
        from azext_prototype.debug_log import log_flow

        if not check_knowledge_gap(finding, loader):
            log_flow("knowledge_contributor.submit_if_gap", "No gap detected, skipping", service=finding.get("service"))
            return None

        log_flow("knowledge_contributor.submit_if_gap", "Gap detected, submitting", service=finding.get("service"))
        result = submit_contribution(finding, repo=repo)

        if result.get("url") and print_fn:
            print_fn(f"  Knowledge contribution submitted: {result['url']}")
        elif result.get("error"):
            log_flow("knowledge_contributor.submit_if_gap", "Submission failed", error=result["error"])

        return result
    except Exception as exc:
        from azext_prototype.debug_log import log_error

        log_error("knowledge_contributor.submit_if_gap", exc)
        return None
