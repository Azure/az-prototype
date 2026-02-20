"""Prerequisite guard checks for stages."""

import logging
from pathlib import Path


logger = logging.getLogger(__name__)


def check_prerequisites(stage_name: str, project_dir: str) -> tuple[bool, list[str]]:
    """Run prerequisite checks for a given stage.

    Args:
        stage_name: The stage to check prerequisites for.
        project_dir: Path to the project directory.

    Returns:
        (all_passed, list_of_failure_messages)
    """
    checks = _get_checks(stage_name, project_dir)
    failures = []

    for check_name, check_fn, error_msg in checks:
        try:
            if not check_fn():
                failures.append(f"[{check_name}] {error_msg}")
        except Exception as e:
            failures.append(f"[{check_name}] Check error: {e}")

    return len(failures) == 0, failures


def _get_checks(stage_name: str, project_dir: str) -> list[tuple]:
    """Return checks for a specific stage.

    Returns:
        List of (check_name, check_callable, error_message) tuples.
    """
    p = Path(project_dir)

    common_checks = [
        (
            "project_exists",
            lambda: p.is_dir(),
            f"Project directory not found: {project_dir}. Run 'az prototype init' first.",
        ),
    ]

    stage_checks = {
        "init": [
            (
                "gh_installed",
                _check_gh_installed,
                "GitHub CLI (gh) is not installed. Install from https://cli.github.com/",
            ),
        ],
        "design": [
            *common_checks,
            (
                "config_exists",
                lambda: (p / "prototype.yaml").is_file(),
                "No prototype.yaml found. Run 'az prototype init' first.",
            ),
        ],
        "build": [
            *common_checks,
            (
                "config_exists",
                lambda: (p / "prototype.yaml").is_file(),
                "No prototype.yaml found. Run 'az prototype init' first.",
            ),
            (
                "design_done",
                lambda: (p / ".prototype" / "state" / "design.json").is_file(),
                "Design stage has not been completed. Run 'az prototype design' first.",
            ),
        ],
        "deploy": [
            *common_checks,
            (
                "config_exists",
                lambda: (p / "prototype.yaml").is_file(),
                "No prototype.yaml found. Run 'az prototype init' first.",
            ),
            (
                "build_done",
                lambda: (p / ".prototype" / "state" / "build.json").is_file(),
                "Build stage has not been completed. Run 'az prototype build' first.",
            ),
            (
                "az_logged_in",
                _check_az_logged_in,
                "Not logged into Azure CLI. Run 'az login' first.",
            ),
        ],
    }

    return stage_checks.get(stage_name, common_checks)


def _check_gh_installed() -> bool:
    """Check if GitHub CLI is installed."""
    import subprocess

    try:
        result = subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def _check_az_logged_in() -> bool:
    """Check if user is logged into Azure CLI."""
    import subprocess

    try:
        result = subprocess.run(
            ["az", "account", "show"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False
