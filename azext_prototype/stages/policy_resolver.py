"""Conversational policy conflict resolution for the build stage.

After each agent generates code, :class:`PolicyResolver` runs the
governance post-check and — when violations are found — walks the user
through an interactive resolution flow:

1. Display the violation with context
2. Offer: **Accept** the compliant alternative, **Override** with
   justification, or **Regenerate** with fix instructions
3. Persist the resolution to :class:`~.build_state.BuildState`
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from azext_prototype.agents.governance import GovernanceContext
from azext_prototype.stages.build_state import BuildState
from azext_prototype.ui.console import Console, DiscoveryPrompt
from azext_prototype.ui.console import console as default_console

logger = logging.getLogger(__name__)


@dataclass
class PolicyResolution:
    """Result of resolving a single policy violation."""

    rule_id: str
    action: str  # "accept" | "override" | "regenerate"
    justification: str = ""
    violation_text: str = ""


class PolicyResolver:
    """Interactive policy violation resolution.

    Wraps :meth:`GovernanceContext.check_response_for_violations` with a
    conversational UX that lets the user decide how to handle each
    violation.

    Parameters
    ----------
    console:
        Styled console for output.
    prompt:
        Bordered prompt for user input (reuses DiscoveryPrompt).
    governance_context:
        Pre-initialised governance context (policies + templates).
    """

    def __init__(
        self,
        console: Console | None = None,
        prompt: DiscoveryPrompt | None = None,
        governance_context: GovernanceContext | None = None,
        auto_accept: bool = False,
    ):
        self._console = console or default_console
        self._prompt = prompt or DiscoveryPrompt(self._console)
        self._governance = governance_context or GovernanceContext()
        self._auto_accept = auto_accept

    def check_and_resolve(
        self,
        agent_name: str,
        generated_content: str,
        build_state: BuildState,
        stage_num: int,
        *,
        input_fn: Callable[[str], str] | None = None,
        print_fn: Callable[[str], None] | None = None,
    ) -> tuple[list[PolicyResolution], bool]:
        """Check generated content for policy violations and resolve interactively.

        Parameters
        ----------
        agent_name:
            Name of the agent that generated the content (for policy scoping).
        generated_content:
            The AI-generated code/text to check.
        build_state:
            Build state for persisting resolutions.
        stage_num:
            The deployment stage number being checked.
        input_fn / print_fn:
            Injectable I/O for testing.

        Returns
        -------
        tuple[list[PolicyResolution], bool]:
            (resolutions, needs_regeneration) — if *needs_regeneration* is
            True, the caller should re-run the agent with fix instructions
            derived from the resolutions.
        """
        _print = print_fn or self._console.print
        _input = input_fn or (lambda p: self._prompt.simple_prompt(p))

        # Run the governance check
        violations = self._governance.check_response_for_violations(
            agent_name,
            generated_content,
        )

        if not violations:
            return [], False

        _print("")
        _print(f"Policy check found {len(violations)} issue(s):")
        _print("")

        resolutions: list[PolicyResolution] = []
        needs_regen = False

        # Auto-accept mode: accept all violations without prompting
        if self._auto_accept:
            for i, violation in enumerate(violations, 1):
                safe = violation.replace("[", "\\[")
                _print(f"\\[{i}] {safe}")
                _print("    Auto-accepted compliant recommendation.")
                resolutions.append(
                    PolicyResolution(
                        rule_id=self._extract_rule_id(violation),
                        action="accept",
                        violation_text=violation,
                    )
                )
            _print("")
        else:
            for i, violation in enumerate(violations, 1):
                # Escape brackets in violation text so Rich doesn't interpret
                # "[policy-name]" as a style tag and silently strip it.
                safe = violation.replace("[", "\\[")
                _print(f"\\[{i}] {safe}")
                _print("")
                _print("    \\[A] Accept compliant recommendation (default)")
                _print("    \\[O] Override — provide justification")
                _print("    \\[R] Regenerate — re-run agent with fix instructions")
                _print("")

                choice = _input("    Choice [A/O/R]: ").strip().lower()

                if choice in ("o", "override"):
                    justification = _input("    Justification: ").strip()
                    if not justification:
                        justification = "User chose to override"
                    resolution = PolicyResolution(
                        rule_id=self._extract_rule_id(violation),
                        action="override",
                        justification=justification,
                        violation_text=violation,
                    )
                    build_state.add_policy_override(resolution.rule_id, justification)
                    _print(f"    Override recorded: {resolution.rule_id}")

                elif choice in ("r", "regenerate"):
                    resolution = PolicyResolution(
                        rule_id=self._extract_rule_id(violation),
                        action="regenerate",
                        violation_text=violation,
                    )
                    needs_regen = True
                    _print("    Will regenerate with fix instructions.")

                else:
                    # Default: accept compliant
                    resolution = PolicyResolution(
                        rule_id=self._extract_rule_id(violation),
                        action="accept",
                        violation_text=violation,
                    )
                    _print("    Accepted compliant recommendation.")

                resolutions.append(resolution)
                _print("")

        # Record the policy check in build state
        override_dicts = [
            {"rule_id": r.rule_id, "justification": r.justification} for r in resolutions if r.action == "override"
        ]
        build_state.add_policy_check(
            stage_num,
            violations=[v for v in violations],
            overrides=override_dicts,
        )

        return resolutions, needs_regen

    def build_fix_instructions(self, resolutions: list[PolicyResolution]) -> str:
        """Build fix instructions from resolutions that require regeneration.

        Returns a string to append to the agent task prompt.
        """
        regen_items = [r for r in resolutions if r.action == "regenerate"]
        if not regen_items:
            return ""

        lines = [
            "",
            "## Policy Fix Instructions",
            "The previous generation had the following policy violations that must be fixed:",
            "",
        ]
        for r in regen_items:
            lines.append(f"- {r.violation_text}")
        lines.append("")
        lines.append("Fix these violations in the regenerated output.")

        override_items = [r for r in resolutions if r.action == "override"]
        if override_items:
            lines.append("")
            lines.append("The following violations have been overridden by the user (keep as-is):")
            for r in override_items:
                lines.append(f"- {r.rule_id}: {r.justification}")

        return "\n".join(lines)

    @staticmethod
    def _extract_rule_id(violation_text: str) -> str:
        """Best-effort extraction of a rule ID from a violation message.

        GovernanceContext formats violations like:
        ``[managed-identity] Possible anti-pattern detected: ...``

        Falls back to "unknown" if no bracketed prefix is found.
        """
        if violation_text.startswith("["):
            end = violation_text.find("]")
            if end > 0:
                return violation_text[1:end]
        return "unknown"
