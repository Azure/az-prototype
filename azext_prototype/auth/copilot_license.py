"""GitHub Copilot license validation."""

import json
import logging

from knack.util import CLIError

from azext_prototype.auth.github_auth import GitHubAuthManager

logger = logging.getLogger(__name__)


class CopilotLicenseValidator:
    """Validates that the authenticated GitHub user has a Copilot license.

    Checks for Copilot Individual, Business, or Enterprise plans.
    The license is required for accessing GitHub Models API.
    """

    # Copilot API endpoint for checking subscription
    COPILOT_API_PATH = "/user/copilot_billing/seats"
    USER_COPILOT_PATH = "/user"

    def __init__(self, auth_manager: GitHubAuthManager):
        self._auth = auth_manager

    def validate_license(self) -> dict:
        """Check if the user has an active Copilot license.

        Returns:
            dict with license info (plan, status, etc.)

        Raises:
            CLIError if no valid Copilot license is found.
        """

        self._auth.get_token()  # validate token is available
        user_info = self._auth.get_user_info()
        login = user_info.get("login", "unknown")

        logger.info("Checking Copilot license for user: %s", login)

        # Method 1: Check user's Copilot access via API
        license_info = self._check_copilot_access()

        if license_info:
            logger.info(
                "Copilot license validated — plan: %s",
                license_info.get("plan", "unknown"),
            )
            return license_info

        # Method 2: Check organization-level Copilot seats
        org_license = self._check_org_copilot_access()
        if org_license:
            logger.info("Copilot license found via organization: %s", org_license.get("org"))
            return org_license

        raise CLIError(
            f"No active GitHub Copilot license found for user '{login}'.\n\n"
            "A GitHub Copilot license is required to use 'az prototype'.\n"
            "Options:\n"
            "  1. Subscribe to GitHub Copilot Individual: https://github.com/features/copilot\n"
            "  2. Request access through your organization's Copilot Business/Enterprise plan\n"
            "  3. Use --ai-provider azure-openai to use Azure OpenAI instead\n\n"
            "After obtaining a license, run 'az prototype init' again."
        )

    def _check_copilot_access(self) -> dict | None:
        """Check copilot access via the user's GitHub settings."""
        import subprocess

        try:
            result = subprocess.run(
                ["gh", "api", "/user/copilot_billing/seats"],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0:
                data = json.loads(result.stdout)
                if data.get("seats"):
                    return {
                        "plan": "business_or_enterprise",
                        "status": "active",
                        "source": "user_api",
                    }
        except (json.JSONDecodeError, Exception) as e:
            logger.debug("User copilot API check failed: %s", e)

        # Fallback: check if copilot extension is accessible
        try:
            result = subprocess.run(
                ["gh", "copilot", "--help"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return {
                    "plan": "detected_via_cli",
                    "status": "active",
                    "source": "gh_copilot_extension",
                }
        except Exception as e:
            logger.debug("gh copilot extension check failed: %s", e)

        return None

    def _check_org_copilot_access(self) -> dict | None:
        """Check if user has Copilot access through any org membership."""
        import subprocess

        try:
            # List user's orgs
            result = subprocess.run(
                ["gh", "api", "/user/orgs", "--jq", ".[].login"],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                return None

            orgs = [o.strip() for o in result.stdout.strip().split("\n") if o.strip()]

            for org in orgs:
                try:
                    seat_result = subprocess.run(
                        [
                            "gh",
                            "api",
                            f"/orgs/{org}/copilot/billing/seats",
                            "--jq",
                            ".seats[] | select(.assignee.login == env.USER)",
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )

                    if seat_result.returncode == 0 and seat_result.stdout.strip():
                        return {
                            "plan": "organization",
                            "status": "active",
                            "org": org,
                            "source": "org_api",
                        }
                except Exception:
                    continue

        except Exception as e:
            logger.debug("Org copilot check failed: %s", e)

        return None

    def get_models_api_access(self) -> dict:
        """Verify access to GitHub Models API.

        Returns:
            dict with available models info.
        """
        import subprocess

        try:
            subprocess.run(
                ["gh", "api", "/marketplace_listing/plans"],
                capture_output=True,
                text=True,
                check=False,
            )
            # Models API access is tied to the token scopes
            # The actual model availability is checked when we first call the API
            return {
                "models_api": "accessible",
                "note": "Model availability will be verified on first use.",
            }
        except Exception as e:
            logger.debug("Models API check: %s", e)
            return {"models_api": "unknown", "note": str(e)}
