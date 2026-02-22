"""Authentication and license management."""

from azext_prototype.auth.copilot_license import CopilotLicenseValidator
from azext_prototype.auth.github_auth import GitHubAuthManager

__all__ = ["GitHubAuthManager", "CopilotLicenseValidator"]
