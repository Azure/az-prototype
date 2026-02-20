"""Tests for azext_prototype.requirements — version parsing, constraint
checking, tool resolution, and the public check API.

All subprocess and shutil.which calls are mocked — no real tool invocations.
"""

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from azext_prototype.requirements import (
    DEPENDENCY_VERSIONS,
    TOOL_REQUIREMENTS,
    CheckResult,
    ToolRequirement,
    _AZAPI_PROVIDER_VERSION,
    _AZURE_API_VERSION,
    check_all,
    check_all_or_fail,
    check_constraint,
    check_tool,
    get_dependency_version,
    get_requirement,
    parse_version,
)


# ======================================================================
# TestParseVersion
# ======================================================================


class TestParseVersion:
    """parse_version() — standard, two-part, v-prefix, prerelease, invalid."""

    def test_standard_three_part(self):
        assert parse_version("1.45.3") == (1, 45, 3)

    def test_two_part_padded(self):
        assert parse_version("2.1") == (2, 1, 0)

    def test_single_part_padded(self):
        assert parse_version("5") == (5, 0, 0)

    def test_v_prefix(self):
        assert parse_version("v1.7.0") == (1, 7, 0)

    def test_prerelease_suffix_ignored(self):
        # "1.7.0-beta1" — only the numeric prefix is parsed
        assert parse_version("1.7.0-beta1") == (1, 7, 0)

    def test_four_parts(self):
        assert parse_version("1.2.3.4") == (1, 2, 3, 4)

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Cannot parse version"):
            parse_version("not-a-version")


# ======================================================================
# TestCheckConstraint
# ======================================================================


class TestCheckConstraint:
    """check_constraint() — all operators, including tilde/caret boundaries."""

    # --- Standard comparison operators ---

    def test_gte_pass(self):
        assert check_constraint("1.5.0", ">=1.5.0") is True

    def test_gte_higher(self):
        assert check_constraint("2.0.0", ">=1.5.0") is True

    def test_gte_fail(self):
        assert check_constraint("1.4.9", ">=1.5.0") is False

    def test_gt_pass(self):
        assert check_constraint("1.5.1", ">1.5.0") is True

    def test_gt_equal_is_false(self):
        assert check_constraint("1.5.0", ">1.5.0") is False

    def test_lte_pass(self):
        assert check_constraint("1.5.0", "<=1.5.0") is True

    def test_lte_fail(self):
        assert check_constraint("1.5.1", "<=1.5.0") is False

    def test_lt_pass(self):
        assert check_constraint("1.4.9", "<1.5.0") is True

    def test_lt_fail(self):
        assert check_constraint("1.5.0", "<1.5.0") is False

    def test_eq_pass(self):
        assert check_constraint("1.5.0", "==1.5.0") is True

    def test_eq_fail(self):
        assert check_constraint("1.5.1", "==1.5.0") is False

    def test_neq_pass(self):
        assert check_constraint("1.5.1", "!=1.5.0") is True

    def test_neq_fail(self):
        assert check_constraint("1.5.0", "!=1.5.0") is False

    # --- Tilde (~) — pin major.minor ---

    def test_tilde_exact(self):
        assert check_constraint("1.4.0", "~1.4.0") is True

    def test_tilde_patch_higher(self):
        assert check_constraint("1.4.9", "~1.4.0") is True

    def test_tilde_minor_bump_excluded(self):
        assert check_constraint("1.5.0", "~1.4.0") is False

    # --- Caret (^) — pin major ---

    def test_caret_exact(self):
        assert check_constraint("1.3.0", "^1.3.0") is True

    def test_caret_minor_higher(self):
        assert check_constraint("1.99.99", "^1.3.0") is True

    def test_caret_major_bump_excluded(self):
        assert check_constraint("2.0.0", "^1.3.0") is False

    def test_caret_below_floor(self):
        assert check_constraint("1.2.9", "^1.3.0") is False

    def test_invalid_constraint_raises(self):
        with pytest.raises(ValueError, match="Invalid constraint"):
            check_constraint("1.0.0", "~=1.0")


# ======================================================================
# TestCheckTool
# ======================================================================


def _make_req(**overrides) -> ToolRequirement:
    """Build a ToolRequirement with sensible defaults for testing."""
    defaults = dict(
        name="TestTool",
        command="testtool",
        version_args=["--version"],
        version_pattern=r"TestTool\s+v?(?P<version>\d+\.\d+\.\d+)",
        constraint=">=1.0.0",
        install_hint="https://example.com",
    )
    defaults.update(overrides)
    return ToolRequirement(**defaults)


class TestCheckTool:
    """check_tool() — pass, fail, missing, unparseable, timeout, stderr."""

    @patch("azext_prototype.requirements._find_tool", return_value="/usr/bin/testtool")
    @patch("azext_prototype.requirements.subprocess.run")
    def test_pass(self, mock_run, mock_find):
        mock_run.return_value = MagicMock(stdout="TestTool v2.3.1\n", stderr="")
        result = check_tool(_make_req())
        assert result.status == "pass"
        assert result.installed_version == "2.3.1"

    @patch("azext_prototype.requirements._find_tool", return_value="/usr/bin/testtool")
    @patch("azext_prototype.requirements.subprocess.run")
    def test_fail_version_too_low(self, mock_run, mock_find):
        mock_run.return_value = MagicMock(stdout="TestTool v0.9.0\n", stderr="")
        result = check_tool(_make_req())
        assert result.status == "fail"
        assert result.installed_version == "0.9.0"
        assert "does not satisfy" in result.message

    @patch("azext_prototype.requirements._find_tool", return_value="/mnt/c/tools/terraform.exe")
    @patch("azext_prototype.requirements.subprocess.run")
    def test_fail_message_includes_resolved_path(self, mock_run, mock_find):
        """Version-mismatch failure message shows the binary path for diagnosis."""
        mock_run.return_value = MagicMock(stdout="TestTool v0.9.0\n", stderr="")
        result = check_tool(_make_req())
        assert result.status == "fail"
        assert "/mnt/c/tools/terraform.exe" in result.message

    @patch("azext_prototype.requirements._find_tool", return_value=None)
    def test_missing(self, mock_find):
        result = check_tool(_make_req())
        assert result.status == "missing"
        assert result.installed_version is None
        assert result.install_hint == "https://example.com"

    @patch("azext_prototype.requirements._find_tool", return_value="/usr/bin/testtool")
    @patch("azext_prototype.requirements.subprocess.run")
    def test_unparseable_output(self, mock_run, mock_find):
        mock_run.return_value = MagicMock(stdout="garbage output\n", stderr="")
        result = check_tool(_make_req())
        assert result.status == "missing"

    @patch("azext_prototype.requirements._find_tool", return_value="/usr/bin/testtool")
    @patch("azext_prototype.requirements.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10))
    def test_timeout(self, mock_run, mock_find):
        result = check_tool(_make_req())
        assert result.status == "missing"

    @patch("azext_prototype.requirements._find_tool", return_value="/usr/bin/testtool")
    @patch("azext_prototype.requirements.subprocess.run")
    def test_stderr_fallback(self, mock_run, mock_find):
        """Version string on stderr is detected when stdout has no match."""
        mock_run.return_value = MagicMock(stdout="", stderr="TestTool v1.2.3\n")
        result = check_tool(_make_req())
        assert result.status == "pass"
        assert result.installed_version == "1.2.3"


# ======================================================================
# TestCheckAll
# ======================================================================


class TestCheckAll:
    """check_all() — conditional skip/include, bicep skips terraform, results."""

    @patch("azext_prototype.requirements.check_tool")
    def test_skips_terraform_when_bicep(self, mock_check):
        mock_check.return_value = CheckResult(
            name="x", status="pass", installed_version="1.0.0",
            required=">=1.0.0", message="ok",
        )
        results = check_all(iac_tool="bicep")
        names = [r.name for r in results]
        tf_result = [r for r in results if r.name == "Terraform"][0]
        assert tf_result.status == "skip"

    @patch("azext_prototype.requirements.check_tool")
    def test_includes_terraform_when_terraform(self, mock_check):
        def _side_effect(req):
            return CheckResult(
                name=req.name, status="pass", installed_version="1.6.0",
                required=req.constraint, message="ok",
            )
        mock_check.side_effect = _side_effect
        results = check_all(iac_tool="terraform")
        tf_results = [r for r in results if r.name == "Terraform"]
        assert len(tf_results) == 1
        # check_tool was called for Terraform (not skipped)
        assert tf_results[0].status == "pass"

    @patch("azext_prototype.requirements.check_tool")
    def test_skips_terraform_when_no_iac(self, mock_check):
        mock_check.return_value = CheckResult(
            name="x", status="pass", installed_version="1.0.0",
            required=">=1.0.0", message="ok",
        )
        results = check_all(iac_tool=None)
        tf_result = [r for r in results if r.name == "Terraform"][0]
        assert tf_result.status == "skip"

    @patch("azext_prototype.requirements.check_tool")
    def test_returns_all_requirements(self, mock_check):
        mock_check.return_value = CheckResult(
            name="x", status="pass", installed_version="1.0.0",
            required=">=1.0.0", message="ok",
        )
        results = check_all(iac_tool="terraform")
        assert len(results) == len(TOOL_REQUIREMENTS)

    @patch("azext_prototype.requirements.check_tool")
    def test_check_all_or_fail_raises(self, mock_check):
        """check_all_or_fail raises RuntimeError on failures."""
        mock_check.return_value = CheckResult(
            name="BadTool", status="fail", installed_version="0.1.0",
            required=">=1.0.0", message="BadTool 0.1.0 does not satisfy >=1.0.0",
            install_hint="https://example.com",
        )
        with pytest.raises(RuntimeError, match="Tool requirements not met"):
            check_all_or_fail(iac_tool="terraform")


# ======================================================================
# TestGetRequirement
# ======================================================================


class TestGetRequirement:
    """get_requirement() — by name, case-insensitive, missing."""

    def test_by_exact_name(self):
        req = get_requirement("Terraform")
        assert req is not None
        assert req.command == "terraform"

    def test_case_insensitive(self):
        req = get_requirement("azure cli")
        assert req is not None
        assert req.name == "Azure CLI"

    def test_missing_returns_none(self):
        assert get_requirement("nonexistent") is None


# ======================================================================
# TestToolRegistry
# ======================================================================


class TestToolRegistry:
    """TOOL_REQUIREMENTS — valid patterns, parseable constraints, no dupes."""

    def test_all_patterns_compile(self):
        import re
        for req in TOOL_REQUIREMENTS:
            if req.version_pattern:
                pat = re.compile(req.version_pattern)
                assert "version" in pat.groupindex, (
                    f"{req.name} pattern missing named group 'version'"
                )

    def test_all_constraints_parseable(self):
        for req in TOOL_REQUIREMENTS:
            if req.constraint:
                # Should not raise
                check_constraint("99.99.99", req.constraint)

    def test_no_duplicate_names(self):
        names = [req.name for req in TOOL_REQUIREMENTS]
        assert len(names) == len(set(names)), "Duplicate tool names in registry"


# ======================================================================
# TestDependencyVersions
# ======================================================================


class TestDependencyVersions:
    """get_dependency_version() — lookup, case-insensitive, missing."""

    def test_azure_api_version_constant(self):
        assert _AZURE_API_VERSION == "2025-06-01"

    def test_get_dependency_version_found(self):
        assert get_dependency_version("azure_api") == "2025-06-01"

    def test_get_dependency_version_case_insensitive(self):
        assert get_dependency_version("Azure_API") == "2025-06-01"

    def test_get_dependency_version_missing(self):
        assert get_dependency_version("nonexistent") is None

    def test_azapi_provider_version_constant(self):
        assert _AZAPI_PROVIDER_VERSION == "2.8.0"

    def test_get_azapi_provider_version(self):
        assert get_dependency_version("azapi") == "2.8.0"

    def test_dependency_versions_dict_contains_azure_api(self):
        assert "azure_api" in DEPENDENCY_VERSIONS

    def test_dependency_versions_dict_contains_azapi(self):
        assert "azapi" in DEPENDENCY_VERSIONS
