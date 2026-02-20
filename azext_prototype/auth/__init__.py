"""Authentication and license management."""

from azext_prototype.auth.github_auth import GitHubAuthManager
from azext_prototype.auth.copilot_license import CopilotLicenseValidator

__all__ = ["GitHubAuthManager", "CopilotLicenseValidator"]
