"""Tests for PolicyResolver — 3-way policy violation resolution.

Tier 2: Conditional branches with multiple paths.

Covers:
- No violations → early return ([], False)
- Auto-accept mode → all violations auto-accepted
- Interactive accept path (default choice)
- Override path with justification (provided and empty/default)
- Regenerate path → needs_regen=True
- Mixed resolution paths in a single check
- build_fix_instructions() generation with regen-only and mixed items
- _extract_rule_id() with bracketed prefix and fallback
- build_state.add_policy_check/add_policy_override calls
"""

from unittest.mock import MagicMock

import pytest

from azext_prototype.stages.policy_resolver import PolicyResolution, PolicyResolver

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def mock_governance():
    """GovernanceContext mock with configurable violations."""
    gov = MagicMock()
    gov.check_response_for_violations.return_value = []
    return gov


@pytest.fixture
def mock_build_state():
    """BuildState mock that records policy checks and overrides."""
    state = MagicMock()
    state.add_policy_check = MagicMock()
    state.add_policy_override = MagicMock()
    return state


@pytest.fixture
def resolver(mock_governance):
    """Standard interactive PolicyResolver."""
    return PolicyResolver(
        console=MagicMock(),
        prompt=MagicMock(),
        governance_context=mock_governance,
        auto_accept=False,
    )


@pytest.fixture
def auto_resolver(mock_governance):
    """PolicyResolver with auto_accept=True."""
    return PolicyResolver(
        console=MagicMock(),
        prompt=MagicMock(),
        governance_context=mock_governance,
        auto_accept=True,
    )


# ------------------------------------------------------------------
# No violations — early return
# ------------------------------------------------------------------


class TestNoViolations:
    def test_no_violations_returns_empty(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = []
        resolutions, needs_regen = resolver.check_and_resolve(
            "terraform-agent",
            "resource aws_s3_bucket {}",
            mock_build_state,
            1,
            print_fn=MagicMock(),
        )
        assert resolutions == []
        assert needs_regen is False

    def test_no_violations_does_not_call_build_state(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = []
        resolver.check_and_resolve(
            "bicep-agent",
            "resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {}",
            mock_build_state,
            2,
            print_fn=MagicMock(),
        )
        mock_build_state.add_policy_check.assert_not_called()
        mock_build_state.add_policy_override.assert_not_called()


# ------------------------------------------------------------------
# Auto-accept mode
# ------------------------------------------------------------------


class TestAutoAccept:
    def test_auto_accept_all_violations(self, auto_resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = [
            "[managed-identity] Use managed identity instead of keys",
            "[tls-version] Enforce TLS 1.2",
        ]
        printed = []
        resolutions, needs_regen = auto_resolver.check_and_resolve(
            "terraform-agent",
            "some content",
            mock_build_state,
            1,
            print_fn=printed.append,
        )
        assert len(resolutions) == 2
        assert all(r.action == "accept" for r in resolutions)
        assert needs_regen is False

    def test_auto_accept_extracts_rule_ids(self, auto_resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = [
            "[managed-identity] Use managed identity",
            "[tls-version] Enforce TLS 1.2",
        ]
        resolutions, _ = auto_resolver.check_and_resolve(
            "bicep-agent", "content", mock_build_state, 1, print_fn=MagicMock()
        )
        assert resolutions[0].rule_id == "managed-identity"
        assert resolutions[1].rule_id == "tls-version"

    def test_auto_accept_records_policy_check(self, auto_resolver, mock_governance, mock_build_state):
        violations = ["[sec-001] No public endpoints"]
        mock_governance.check_response_for_violations.return_value = violations
        auto_resolver.check_and_resolve("terraform-agent", "code", mock_build_state, 3, print_fn=MagicMock())
        mock_build_state.add_policy_check.assert_called_once_with(
            3,
            violations=violations,
            overrides=[],
        )

    def test_auto_accept_does_not_call_override(self, auto_resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = ["[rule-x] violation"]
        auto_resolver.check_and_resolve("terraform-agent", "code", mock_build_state, 1, print_fn=MagicMock())
        mock_build_state.add_policy_override.assert_not_called()

    def test_auto_accept_prints_auto_accepted_message(self, auto_resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = ["[rule-x] violation"]
        printed = []
        auto_resolver.check_and_resolve("terraform-agent", "code", mock_build_state, 1, print_fn=printed.append)
        assert any("Auto-accepted" in msg for msg in printed)


# ------------------------------------------------------------------
# Interactive — Accept (default path)
# ------------------------------------------------------------------


class TestInteractiveAccept:
    def test_accept_explicit_a(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = ["[rule-1] issue"]
        resolutions, needs_regen = resolver.check_and_resolve(
            "terraform-agent",
            "code",
            mock_build_state,
            1,
            input_fn=lambda _: "a",
            print_fn=MagicMock(),
        )
        assert len(resolutions) == 1
        assert resolutions[0].action == "accept"
        assert needs_regen is False

    def test_accept_default_empty_input(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = ["[rule-1] issue"]
        resolutions, _ = resolver.check_and_resolve(
            "terraform-agent",
            "code",
            mock_build_state,
            1,
            input_fn=lambda _: "",
            print_fn=MagicMock(),
        )
        assert resolutions[0].action == "accept"

    def test_accept_unknown_input_defaults_to_accept(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = ["[rule-1] issue"]
        resolutions, _ = resolver.check_and_resolve(
            "terraform-agent",
            "code",
            mock_build_state,
            1,
            input_fn=lambda _: "xyz",
            print_fn=MagicMock(),
        )
        assert resolutions[0].action == "accept"

    def test_accept_prints_accepted_message(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = ["[rule-1] issue"]
        printed = []
        resolver.check_and_resolve(
            "terraform-agent",
            "code",
            mock_build_state,
            1,
            input_fn=lambda _: "a",
            print_fn=printed.append,
        )
        assert any("Accepted compliant recommendation" in msg for msg in printed)


# ------------------------------------------------------------------
# Interactive — Override path
# ------------------------------------------------------------------


class TestInteractiveOverride:
    def test_override_with_justification(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = ["[managed-identity] use MI"]
        inputs = iter(["o", "Legacy system requires key auth"])
        resolutions, needs_regen = resolver.check_and_resolve(
            "terraform-agent",
            "code",
            mock_build_state,
            2,
            input_fn=lambda _: next(inputs),
            print_fn=MagicMock(),
        )
        assert len(resolutions) == 1
        assert resolutions[0].action == "override"
        assert resolutions[0].justification == "Legacy system requires key auth"
        assert needs_regen is False

    def test_override_word_form(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = ["[rule-1] issue"]
        inputs = iter(["override", "needed"])
        resolutions, _ = resolver.check_and_resolve(
            "terraform-agent",
            "code",
            mock_build_state,
            1,
            input_fn=lambda _: next(inputs),
            print_fn=MagicMock(),
        )
        assert resolutions[0].action == "override"

    def test_override_empty_justification_uses_default(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = ["[rule-1] issue"]
        inputs = iter(["o", ""])
        resolutions, _ = resolver.check_and_resolve(
            "terraform-agent",
            "code",
            mock_build_state,
            1,
            input_fn=lambda _: next(inputs),
            print_fn=MagicMock(),
        )
        assert resolutions[0].action == "override"
        assert resolutions[0].justification == "User chose to override"

    def test_override_calls_build_state_add_policy_override(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = ["[sec-001] issue"]
        inputs = iter(["o", "Approved by security team"])
        resolver.check_and_resolve(
            "terraform-agent",
            "code",
            mock_build_state,
            1,
            input_fn=lambda _: next(inputs),
            print_fn=MagicMock(),
        )
        mock_build_state.add_policy_override.assert_called_once_with("sec-001", "Approved by security team")

    def test_override_recorded_in_policy_check_overrides(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = ["[sec-001] issue"]
        inputs = iter(["o", "Approved"])
        resolver.check_and_resolve(
            "terraform-agent",
            "code",
            mock_build_state,
            5,
            input_fn=lambda _: next(inputs),
            print_fn=MagicMock(),
        )
        mock_build_state.add_policy_check.assert_called_once()
        args = mock_build_state.add_policy_check.call_args
        assert (
            args.kwargs.get("overrides")
            or args[1].get("overrides")
            or [d for d in (args[1] if len(args) > 1 else []) if isinstance(d, list)]
        )
        # Verify via the call — overrides list should have one item
        call_args = mock_build_state.add_policy_check.call_args
        overrides_arg = call_args[1]["overrides"] if "overrides" in call_args[1] else call_args[0][2]
        assert len(overrides_arg) == 1
        assert overrides_arg[0]["rule_id"] == "sec-001"


# ------------------------------------------------------------------
# Interactive — Regenerate path
# ------------------------------------------------------------------


class TestInteractiveRegenerate:
    def test_regenerate_sets_needs_regen_true(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = ["[rule-1] issue"]
        resolutions, needs_regen = resolver.check_and_resolve(
            "terraform-agent",
            "code",
            mock_build_state,
            1,
            input_fn=lambda _: "r",
            print_fn=MagicMock(),
        )
        assert needs_regen is True
        assert resolutions[0].action == "regenerate"

    def test_regenerate_word_form(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = ["[rule-1] issue"]
        resolutions, needs_regen = resolver.check_and_resolve(
            "terraform-agent",
            "code",
            mock_build_state,
            1,
            input_fn=lambda _: "regenerate",
            print_fn=MagicMock(),
        )
        assert needs_regen is True
        assert resolutions[0].action == "regenerate"

    def test_regenerate_prints_will_regenerate_message(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = ["[rule-1] issue"]
        printed = []
        resolver.check_and_resolve(
            "terraform-agent",
            "code",
            mock_build_state,
            1,
            input_fn=lambda _: "r",
            print_fn=printed.append,
        )
        assert any("regenerate" in msg.lower() for msg in printed)


# ------------------------------------------------------------------
# Mixed resolutions in a single check
# ------------------------------------------------------------------


class TestMixedResolutions:
    def test_mixed_accept_override_regenerate(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = [
            "[rule-a] first issue",
            "[rule-b] second issue",
            "[rule-c] third issue",
        ]
        # First: accept, Second: override with justification, Third: regenerate
        inputs = iter(["a", "o", "Because reasons", "r"])
        resolutions, needs_regen = resolver.check_and_resolve(
            "terraform-agent",
            "code",
            mock_build_state,
            1,
            input_fn=lambda _: next(inputs),
            print_fn=MagicMock(),
        )
        assert len(resolutions) == 3
        assert resolutions[0].action == "accept"
        assert resolutions[1].action == "override"
        assert resolutions[1].justification == "Because reasons"
        assert resolutions[2].action == "regenerate"
        assert needs_regen is True

    def test_mixed_only_override_recorded_in_overrides(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = [
            "[rule-a] first",
            "[rule-b] second",
        ]
        inputs = iter(["a", "o", "justified"])
        resolver.check_and_resolve(
            "terraform-agent",
            "code",
            mock_build_state,
            1,
            input_fn=lambda _: next(inputs),
            print_fn=MagicMock(),
        )
        call_args = mock_build_state.add_policy_check.call_args
        overrides = call_args[1]["overrides"] if "overrides" in call_args[1] else call_args[0][2]
        assert len(overrides) == 1
        assert overrides[0]["rule_id"] == "rule-b"


# ------------------------------------------------------------------
# build_fix_instructions()
# ------------------------------------------------------------------


class TestBuildFixInstructions:
    def test_no_regen_items_returns_empty(self, resolver):
        resolutions = [
            PolicyResolution(rule_id="r1", action="accept", violation_text="v1"),
            PolicyResolution(rule_id="r2", action="override", justification="ok", violation_text="v2"),
        ]
        assert resolver.build_fix_instructions(resolutions) == ""

    def test_regen_items_produces_fix_block(self, resolver):
        resolutions = [
            PolicyResolution(rule_id="r1", action="regenerate", violation_text="missing MI auth"),
        ]
        result = resolver.build_fix_instructions(resolutions)
        assert "## Policy Fix Instructions" in result
        assert "missing MI auth" in result
        assert "Fix these violations" in result

    def test_regen_with_overrides_includes_override_section(self, resolver):
        resolutions = [
            PolicyResolution(rule_id="r1", action="regenerate", violation_text="issue A"),
            PolicyResolution(rule_id="r2", action="override", justification="approved", violation_text="issue B"),
        ]
        result = resolver.build_fix_instructions(resolutions)
        assert "## Policy Fix Instructions" in result
        assert "issue A" in result
        assert "overridden by the user" in result
        assert "r2: approved" in result

    def test_multiple_regen_items(self, resolver):
        resolutions = [
            PolicyResolution(rule_id="r1", action="regenerate", violation_text="A"),
            PolicyResolution(rule_id="r2", action="regenerate", violation_text="B"),
        ]
        result = resolver.build_fix_instructions(resolutions)
        assert "- A" in result
        assert "- B" in result


# ------------------------------------------------------------------
# _extract_rule_id()
# ------------------------------------------------------------------


class TestExtractRuleId:
    def test_bracketed_prefix(self):
        assert PolicyResolver._extract_rule_id("[managed-identity] Use MI") == "managed-identity"

    def test_no_brackets_returns_unknown(self):
        assert PolicyResolver._extract_rule_id("No brackets here") == "unknown"

    def test_empty_brackets_returns_empty_string(self):
        # "[]" has end=1 > 0, so it extracts text[1:1] == ""
        assert PolicyResolver._extract_rule_id("[] empty bracket") == ""

    def test_starts_with_bracket_no_close(self):
        assert PolicyResolver._extract_rule_id("[no-close-bracket") == "unknown"

    def test_nested_brackets_takes_first(self):
        assert PolicyResolver._extract_rule_id("[outer] some [inner] text") == "outer"


# ------------------------------------------------------------------
# iac_tool parameter forwarding
# ------------------------------------------------------------------


class TestIacToolForwarding:
    def test_iac_tool_passed_to_governance(self, resolver, mock_governance, mock_build_state):
        mock_governance.check_response_for_violations.return_value = []
        resolver.check_and_resolve(
            "terraform-agent",
            "code",
            mock_build_state,
            1,
            print_fn=MagicMock(),
            iac_tool="terraform",
        )
        mock_governance.check_response_for_violations.assert_called_once_with(
            "terraform-agent",
            "code",
            iac_tool="terraform",
        )
