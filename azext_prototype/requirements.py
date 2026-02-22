"""Central external-tool requirements with semver constraint checking.

Declares all external tool dependencies (Azure CLI, Terraform, etc.) in a
single registry with minimum-version constraints.  Provides functions to
check whether each tool is installed and satisfies its version constraint.

This module is **self-contained** — it uses only the Python standard library
and has zero intra-package imports so it can be loaded very early in the
startup path.

Public API
----------
- ``check_tool(req)``         — check one ``ToolRequirement``
- ``check_all(iac_tool)``     — check all requirements, skipping inapplicable
- ``check_all_or_fail(...)``  — same, but raises on any failure
- ``get_requirement(name)``   — lookup by display name (case-insensitive)
- ``parse_version(s)``        — ``"1.45.3"`` → ``(1, 45, 3)``
- ``check_constraint(v, c)``  — ``"1.45.3", ">=1.5.0"`` → ``True``
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ======================================================================
# Version constants — easy to find and update in one place
# ======================================================================

_PYTHON_VERSION = ">=3.9.0"
_AZURE_CLI_VERSION = ">=2.50.0"
_GITHUB_CLI_VERSION = ">=2.30.0"
_TERRAFORM_VERSION = ">=1.14.0"

# ======================================================================
# Dependency versions — not CLI-validated, used as reference by agents
# ======================================================================

_AZURE_API_VERSION = "2025-06-01"
_AZAPI_PROVIDER_VERSION = "2.8.0"

# Registry of dependency versions for programmatic lookup.
DEPENDENCY_VERSIONS: dict[str, str] = {
    "azure_api": _AZURE_API_VERSION,
    "azapi": _AZAPI_PROVIDER_VERSION,
}


def get_dependency_version(name: str) -> str | None:
    """Look up a dependency version by name (case-insensitive).

    These are reference versions for components like the Azure ARM API
    version — not validated at runtime, but used by agents to target
    the correct API surface for both Terraform (azapi) and Bicep.
    """
    return DEPENDENCY_VERSIONS.get(name.lower())


# ======================================================================
# Data structures
# ======================================================================


@dataclass
class ToolRequirement:
    """Declaration of an external tool dependency."""

    name: str  # Display name, e.g. "Terraform"
    command: str  # Binary name or path, e.g. "terraform"
    version_args: list[str] = field(default_factory=list)  # e.g. ["--version"]
    version_pattern: str = ""  # Regex with named group ``version``
    constraint: str = ""  # e.g. ">=1.5.0", "~1.45.0", "^2.0.0"
    condition: str | None = None  # Only check when iac_tool matches
    install_hint: str = ""  # URL or install guidance


@dataclass
class CheckResult:
    """Outcome of checking a single tool requirement."""

    name: str
    status: str  # "pass" | "fail" | "missing" | "skip"
    installed_version: str | None
    required: str
    message: str
    install_hint: str = ""


# ======================================================================
# Tool registry
# ======================================================================

TOOL_REQUIREMENTS: list[ToolRequirement] = [
    ToolRequirement(
        name="Python",
        command=sys.executable,
        version_args=["--version"],
        version_pattern=r"Python\s+(?P<version>\d+\.\d+\.\d+)",
        constraint=_PYTHON_VERSION,
        install_hint="https://www.python.org/downloads/",
    ),
    ToolRequirement(
        name="Azure CLI",
        command="az",
        version_args=["version", "-o", "tsv"],
        version_pattern=r"(?P<version>\d+\.\d+\.\d+)",
        constraint=_AZURE_CLI_VERSION,
        install_hint="https://learn.microsoft.com/cli/azure/install-azure-cli",
    ),
    ToolRequirement(
        name="GitHub CLI",
        command="gh",
        version_args=["--version"],
        version_pattern=r"gh version\s+(?P<version>\d+\.\d+\.\d+)",
        constraint=_GITHUB_CLI_VERSION,
        install_hint="https://cli.github.com/",
    ),
    ToolRequirement(
        name="Terraform",
        command="terraform",
        version_args=["--version"],
        version_pattern=r"Terraform\s+v?(?P<version>\d+\.\d+\.\d+)",
        constraint=_TERRAFORM_VERSION,
        condition="terraform",
        install_hint="https://developer.hashicorp.com/terraform/install",
    ),
]

# ======================================================================
# Version parsing
# ======================================================================

_VERSION_RE = re.compile(r"v?(\d+(?:\.\d+)*)")


def parse_version(s: str) -> tuple[int, ...]:
    """Parse a version string into an int tuple, padded to at least 3 elements.

    Accepts ``"1.45.3"``, ``"v1.7.0"``, ``"1.7.0-beta1"``, ``"2.1"`` etc.
    Raises ``ValueError`` on unparseable input.

    >>> parse_version("1.45.3")
    (1, 45, 3)
    >>> parse_version("v2.1")
    (2, 1, 0)
    """
    m = _VERSION_RE.match(s.strip())
    if not m:
        raise ValueError(f"Cannot parse version: {s!r}")
    parts = tuple(int(p) for p in m.group(1).split("."))
    # Pad to at least 3 components
    while len(parts) < 3:
        parts = parts + (0,)
    return parts


# ======================================================================
# Constraint checking
# ======================================================================

_CONSTRAINT_RE = re.compile(r"^(?P<op>>=|<=|!=|==|>|<|~|\^)\s*(?P<ver>v?\d+(?:\.\d+)*)$")


def check_constraint(version: str, constraint: str) -> bool:
    """Check whether *version* satisfies *constraint*.

    Supported operators: ``>=``, ``>``, ``<=``, ``<``, ``==``, ``!=``,
    ``~`` (tilde — pin major.minor), ``^`` (caret — pin major).

    >>> check_constraint("1.45.3", ">=1.5.0")
    True
    >>> check_constraint("1.4.0", "~1.4.0")
    True
    >>> check_constraint("1.5.0", "~1.4.0")
    False
    """
    m = _CONSTRAINT_RE.match(constraint.strip())
    if not m:
        raise ValueError(f"Invalid constraint: {constraint!r}")
    op = m.group("op")
    ver = parse_version(version)
    req = parse_version(m.group("ver"))

    if op == ">=":
        return ver >= req
    if op == ">":
        return ver > req
    if op == "<=":
        return ver <= req
    if op == "<":
        return ver < req
    if op == "==":
        return ver == req
    if op == "!=":
        return ver != req
    if op == "~":
        # Tilde: >=req and <(major.minor+1.0)
        upper = (req[0], req[1] + 1, 0)
        return ver >= req and ver < upper
    if op == "^":
        # Caret: >=req and <(major+1.0.0)
        upper = (req[0] + 1, 0, 0)
        return ver >= req and ver < upper

    raise ValueError(f"Unknown operator: {op!r}")  # pragma: no cover


# ======================================================================
# Tool resolution
# ======================================================================


def _find_tool(command: str) -> str | None:
    """Locate the executable for *command*, returning its path or ``None``.

    For ``az``, replicates the fallback logic from ``deploy_helpers._find_az``
    (look in the bin dir next to ``sys.executable``, including .cmd on Windows).
    For ``Python``, ``sys.executable`` is used directly.
    """
    # If the command is sys.executable (Python), it's always available
    if command == sys.executable:
        return sys.executable

    found = shutil.which(command)
    if found:
        return found

    # az-specific fallback: try the bin dir next to the Python interpreter
    if command == "az":
        bin_dir = os.path.dirname(sys.executable)
        candidate = os.path.join(bin_dir, "az")
        if os.path.isfile(candidate):
            return candidate
        candidate_cmd = candidate + ".cmd"
        if os.path.isfile(candidate_cmd):
            return candidate_cmd

    return None


# ======================================================================
# Version extraction
# ======================================================================


def _get_tool_version(req: ToolRequirement) -> tuple[str | None, str | None]:
    """Run the tool's version command and extract the version string.

    Returns ``(version, path)`` — e.g. ``("1.45.3", "/usr/bin/terraform")``.
    Either or both may be ``None`` if the tool is missing, times out, or
    produces unparseable output.
    """
    path = _find_tool(req.command)
    if path is None:
        return None, None

    try:
        result = subprocess.run(
            [path] + req.version_args,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return None, path
    except subprocess.TimeoutExpired:
        logger.warning("Timed out checking version for %s", req.name)
        return None, path

    # Try stdout first, then stderr (some tools print to stderr)
    for output in (result.stdout, result.stderr):
        if not output:
            continue
        m = re.search(req.version_pattern, output)
        if m:
            return m.group("version"), path

    return None, path


# ======================================================================
# Public API
# ======================================================================


def check_tool(req: ToolRequirement) -> CheckResult:
    """Check whether a single tool requirement is satisfied."""
    version, resolved_path = _get_tool_version(req)

    if version is None:
        return CheckResult(
            name=req.name,
            status="missing",
            installed_version=None,
            required=req.constraint,
            message=f"{req.name} is not installed",
            install_hint=req.install_hint,
        )

    if not req.constraint:
        return CheckResult(
            name=req.name,
            status="pass",
            installed_version=version,
            required="(any)",
            message=f"{req.name} {version} found",
        )

    try:
        ok = check_constraint(version, req.constraint)
    except ValueError:
        return CheckResult(
            name=req.name,
            status="fail",
            installed_version=version,
            required=req.constraint,
            message=f"{req.name} {version} — cannot parse version",
            install_hint=req.install_hint,
        )

    if ok:
        return CheckResult(
            name=req.name,
            status="pass",
            installed_version=version,
            required=req.constraint,
            message=f"{req.name} {version} satisfies {req.constraint}",
        )

    # Include the resolved path in the failure message so users can
    # diagnose PATH issues (e.g. az CLI finding a different binary
    # than the user's interactive shell).
    path_hint = f" (from {resolved_path})" if resolved_path else ""
    return CheckResult(
        name=req.name,
        status="fail",
        installed_version=version,
        required=req.constraint,
        message=f"{req.name} {version}{path_hint} does not satisfy {req.constraint}",
        install_hint=req.install_hint,
    )


def check_all(iac_tool: str | None = None) -> list[CheckResult]:
    """Check all tool requirements, skipping conditional ones that don't apply.

    Parameters
    ----------
    iac_tool:
        The IaC tool in use (``"terraform"`` or ``"bicep"``).  Requirements
        with a ``condition`` that doesn't match are skipped.
    """
    results: list[CheckResult] = []
    for req in TOOL_REQUIREMENTS:
        if req.condition is not None and req.condition != iac_tool:
            results.append(
                CheckResult(
                    name=req.name,
                    status="skip",
                    installed_version=None,
                    required=req.constraint,
                    message=f"{req.name} skipped (iac_tool={iac_tool!r})",
                )
            )
            continue
        results.append(check_tool(req))
    return results


def check_all_or_fail(iac_tool: str | None = None) -> list[CheckResult]:
    """Like :func:`check_all`, but raises :class:`RuntimeError` if any check
    fails or a required tool is missing.

    Returns the full results list on success.
    """
    results = check_all(iac_tool)
    problems = [r for r in results if r.status in ("fail", "missing")]
    if problems:
        lines = [f"  - {r.name}: {r.message}" for r in problems]
        hints = [f"    Install: {r.install_hint}" for r in problems if r.install_hint]
        detail = "\n".join(lines + hints)
        raise RuntimeError(f"Tool requirements not met:\n{detail}")
    return results


def get_requirement(name: str) -> ToolRequirement | None:
    """Look up a tool requirement by display name (case-insensitive)."""
    lower = name.lower()
    for req in TOOL_REQUIREMENTS:
        if req.name.lower() == lower:
            return req
    return None
