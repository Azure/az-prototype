"""QA error routing — shared helper for routing errors to the QA agent.

Provides a reusable ``route_error_to_qa()`` function used by build, deploy,
discovery, and backlog sessions.  Follows the ``deploy_helpers.py`` pattern:
module-level functions, dict return values, no exceptions from public API.

When a QA agent is available it diagnoses the error and optionally submits
a knowledge contribution.  When no QA agent is present the function returns
gracefully with the raw error text.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def route_error_to_qa(
    error: str | Exception,
    context_label: str,
    qa_agent: Any | None,
    agent_context: Any,
    token_tracker: Any | None,
    print_fn: Callable[[str], None],
    *,
    services: list[str] | None = None,
    max_error_chars: int = 2000,
    max_display_chars: int = 1500,
    escalation_tracker: Any | None = None,
    source_agent: str = "",
    source_stage: str = "",
) -> dict:
    """Route an error to the QA agent for diagnosis.

    Parameters
    ----------
    error:
        The error text or exception to diagnose.
    context_label:
        Human label for where the error occurred (e.g. "Build Stage 3: Data Layer").
    qa_agent:
        Resolved QA agent instance, or ``None`` if unavailable.
    agent_context:
        Runtime context with AI provider.
    token_tracker:
        Optional :class:`~azext_prototype.ai.token_tracker.TokenTracker`.
    print_fn:
        Callable to display output to the user.
    services:
        Optional service names related to the error (for knowledge contribution).
    max_error_chars:
        Maximum characters of error text sent to QA.
    max_display_chars:
        Maximum characters of QA diagnosis shown to user.
    escalation_tracker:
        Optional :class:`~.escalation.EscalationTracker` for blocker tracking.
    source_agent:
        Name of the agent that encountered the error.
    source_stage:
        Stage where the error occurred (e.g. "build", "deploy").

    Returns
    -------
    dict
        ``{"diagnosed": bool, "content": str, "response": AIResponse | None}``
    """
    error_text = str(error)[:max_error_chars] if error else "Unknown error"

    if qa_agent is None or agent_context is None or agent_context.ai_provider is None:
        return {"diagnosed": False, "content": error_text, "response": None}

    task = (
        f"Error during {context_label}.\n\n"
        f"## Error Output\n```\n{error_text}\n```\n\n"
        "Diagnose the root cause. Suggest specific fixes the user can apply."
    )

    try:
        qa_response = qa_agent.execute(agent_context, task)
    except Exception:
        logger.debug("QA agent failed during error diagnosis", exc_info=True)
        return {"diagnosed": False, "content": error_text, "response": None}

    if qa_response and token_tracker is not None:
        try:
            token_tracker.record(qa_response)
        except Exception:
            pass

    if qa_response and qa_response.content:
        display = qa_response.content[:max_display_chars]
        print_fn("")
        print_fn("  QA Diagnosis:")
        print_fn(display)

        # Fire-and-forget knowledge contribution
        try:
            _submit_knowledge(qa_response.content, context_label, services, print_fn)
        except Exception:
            pass

        return {"diagnosed": True, "content": qa_response.content, "response": qa_response}

    # QA couldn't diagnose — record as blocker if tracker available
    if escalation_tracker is not None:
        try:
            escalation_tracker.record_blocker(
                context_label,
                error_text,
                source_agent=source_agent or "qa-engineer",
                source_stage=source_stage,
            )
        except Exception:
            pass

    return {"diagnosed": False, "content": error_text, "response": qa_response}


def _submit_knowledge(
    qa_content: str,
    context_label: str,
    services: list[str] | None,
    print_fn: Callable[[str], None],
) -> None:
    """Fire-and-forget knowledge contribution from QA diagnosis."""
    try:
        from azext_prototype.knowledge import KnowledgeLoader
        from azext_prototype.stages.knowledge_contributor import (
            build_finding_from_qa,
            submit_if_gap,
        )

        loader = KnowledgeLoader()
        svc_list = services or []
        for svc in svc_list:
            finding = build_finding_from_qa(
                qa_content,
                service=svc,
                source=context_label,
            )
            submit_if_gap(finding, loader, print_fn=print_fn)
    except Exception:
        pass
