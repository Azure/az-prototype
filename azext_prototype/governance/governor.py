"""Governor — embedding-based policy retrieval and enforcement.

Provides three operations:

1. **retrieve(task)** — Find the most relevant policy rules for a task
   using embedding similarity (semantic search).
2. **brief(task)** — Retrieve relevant policies and format as a concise
   (<2KB) set of directives for injection into an agent's prompt.
3. **review(output)** — Review generated output against the full policy
   set using parallel chunked evaluation.

The governor replaces the previous approach of injecting ALL policies
(~40KB) into every agent's system prompt. Instead, only the relevant
rules (~1-2KB) are injected, and a thorough post-generation review
catches violations that the brief might not cover.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from azext_prototype.governance.embeddings import create_backend
from azext_prototype.governance.policy_index import IndexedRule, PolicyIndex

logger = logging.getLogger(__name__)

# Singleton index — built once per session
_policy_index: PolicyIndex | None = None


def _get_or_build_index(project_dir: str, status_fn: Any = None) -> PolicyIndex:
    """Get or lazily build the policy index."""
    global _policy_index
    if _policy_index is not None and _policy_index.rule_count > 0:
        return _policy_index

    from azext_prototype.debug_log import log_flow, log_timer
    from azext_prototype.governance.policies import PolicyEngine

    # 1. Try pre-computed embeddings shipped with the wheel (no deps, instant)
    index = PolicyIndex(backend=create_backend(prefer_neural=True, status_fn=status_fn))
    if index.load_precomputed():
        log_flow("governor._get_or_build_index", "Loaded pre-computed embeddings", rules=index.rule_count)
        _policy_index = index
        return index

    # 2. Try project-level cache
    if index.load_cache(project_dir):
        log_flow("governor._get_or_build_index", "Loaded from project cache", rules=index.rule_count)
        _policy_index = index
        return index

    # 3. Build from scratch (TF-IDF or neural if available)
    with log_timer("governor._get_or_build_index", "Building policy index"):
        engine = PolicyEngine()
        engine.load()
        index.build(engine.list_policies())
        index.save_cache(project_dir)

    log_flow("governor._get_or_build_index", "Built fresh index", rules=index.rule_count)
    _policy_index = index
    return index


def reset_index() -> None:
    """Clear the cached index (for tests or after policy changes)."""
    global _policy_index
    _policy_index = None


# ------------------------------------------------------------------ #
# Brief — concise policy directives for agent prompts
# ------------------------------------------------------------------ #


def brief(
    project_dir: str,
    task_description: str,
    agent_name: str = "",
    top_k: int = 10,
    status_fn: Any = None,
) -> str:
    """Retrieve relevant policies and format as concise directives.

    This is a **code-level operation** — no AI call is made. The output
    is a compact (~1-2KB) set of rules suitable for injection into an
    agent's system prompt, replacing the previous ~40KB full policy dump.

    Parameters
    ----------
    project_dir:
        Project directory (for index cache).
    task_description:
        Description of the current task (used as the retrieval query).
    agent_name:
        Name of the agent that will receive the brief. Rules are filtered
        by ``applies_to`` if set.
    top_k:
        Maximum number of rules to include.
    status_fn:
        Optional status callback for loading indicators.
    """
    from azext_prototype.debug_log import log_flow

    index = _get_or_build_index(project_dir, status_fn=status_fn)
    if agent_name:
        rules = index.retrieve_for_agent(task_description, agent_name, top_k=top_k)
    else:
        rules = index.retrieve(task_description, top_k=top_k)

    # Always include MUST rules with severity="required" regardless of
    # embedding similarity — these are universal governance constraints
    # (e.g. network isolation, managed identity) that apply to ALL infra stages.
    all_rules = index.retrieve(task_description, top_k=top_k * 3)
    must_rules = [r for r in all_rules if r.severity == "required" and r not in rules]
    combined = list(rules)
    for r in must_rules:
        if r.rule_id not in {existing.rule_id for existing in combined}:
            combined.append(r)

    log_flow("governor.brief", f"Retrieved {len(rules)} + {len(combined) - len(rules)} MUST rules", agent=agent_name)

    if not combined:
        return ""

    return _format_brief(combined)


def _format_brief(rules: list[IndexedRule]) -> str:
    """Format retrieved rules as concise directives with rationale."""
    lines = ["## Governance Policy Brief", ""]
    lines.append("The following governance rules apply to this task:")
    lines.append("")

    current_category = ""
    for rule in rules:
        if rule.category != current_category:
            current_category = rule.category
            lines.append(f"### {current_category.title()}")
        severity_marker = "MUST" if rule.severity == "required" else "SHOULD"
        lines.append(f"- **{rule.rule_id}** ({severity_marker}): {rule.description}")
        # Include rationale for MUST rules — tells the model HOW to comply
        if rule.severity == "required" and rule.rationale:
            lines.append(f"  Implementation: {rule.rationale}")

    # Append ALL anti-patterns as "NEVER GENERATE" directives.
    # Loaded from governance-managed YAML files — zero hardcoded logic.
    try:
        from azext_prototype.governance import anti_patterns

        ap_checks = anti_patterns.load()
        if ap_checks:
            lines.append("")
            lines.append("## Code Patterns That Will Be Rejected")
            lines.append("The following patterns trigger automatic build rejection:")
            lines.append("")
            for check in ap_checks:
                lines.append(f"- {check.warning_message}")
                for sp in check.search_patterns:
                    lines.append(f"  NEVER GENERATE: `{sp}`")
    except Exception:
        pass

    lines.append("")
    lines.append("Ensure generated code follows these rules.")
    return "\n".join(lines)


# ------------------------------------------------------------------ #
# Review — parallel chunked policy evaluation
# ------------------------------------------------------------------ #


def review(
    project_dir: str,
    output_text: str,
    ai_provider: Any,
    max_workers: int = 2,
    status_fn: Any = None,
) -> list[str]:
    """Review generated output against the full policy set.

    Splits policies into batches and evaluates each batch in parallel
    using the AI provider. Returns a list of violation descriptions.

    Parameters
    ----------
    project_dir:
        Project directory (for index cache).
    output_text:
        The generated code/architecture to review.
    ai_provider:
        AI provider instance for making review calls.
    max_workers:
        Maximum concurrent review threads.
    status_fn:
        Optional status callback.
    """
    from azext_prototype.ai.provider import AIMessage
    from azext_prototype.debug_log import log_flow
    from azext_prototype.governance.policies import PolicyEngine

    engine = PolicyEngine()
    engine.load()
    policies = engine.list_policies()

    if not policies:
        return []

    # Split into batches of 3-4 policies each
    batch_size = 3
    batches = [policies[i : i + batch_size] for i in range(0, len(policies), batch_size)]
    log_flow("governor.review", f"Reviewing against {len(policies)} policies in {len(batches)} batches")

    all_violations: list[str] = []

    def _review_batch(batch: list) -> list[str]:
        """Review one batch of policies against the output."""
        policy_text = "\n\n".join(_format_policy_for_review(p) for p in batch)
        prompt = (
            "You are a governance reviewer. Review the following generated output "
            "against the policy rules below. List ONLY actual violations — do not "
            "list rules that are followed correctly. If there are no violations, "
            "respond with exactly: [NO_VIOLATIONS]\n\n"
            f"## Generated Output\n```\n{output_text[:8000]}\n```\n\n"
            f"## Policy Rules\n{policy_text}"
        )
        system = AIMessage(role="system", content="You are a strict governance policy reviewer.")
        user_msg = AIMessage(role="user", content=prompt)
        try:
            response = ai_provider.chat([system, user_msg], temperature=0.1, max_tokens=2048)
            if "[NO_VIOLATIONS]" in response.content:
                return []
            return [line.strip() for line in response.content.strip().splitlines() if line.strip().startswith("-")]
        except Exception as exc:
            logger.warning("Governor review batch failed: %s", exc)
            return []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_review_batch, batch): i for i, batch in enumerate(batches)}
        for future in as_completed(futures):
            violations = future.result()
            all_violations.extend(violations)

    log_flow("governor.review", f"Review complete: {len(all_violations)} violations found")
    return all_violations


def _format_policy_for_review(policy: Any) -> str:
    """Format a single policy for the review prompt."""
    lines = [f"### {getattr(policy, 'name', 'unknown')} ({getattr(policy, 'category', '')})"]
    for rule in getattr(policy, "rules", []):
        severity = getattr(rule, "severity", "recommended")
        desc = getattr(rule, "description", "")
        lines.append(f"- [{severity.upper()}] {getattr(rule, 'id', '')}: {desc}")
    return "\n".join(lines)
