"""Tests for azext_prototype.auth — GitHub auth and Copilot license."""

import pytest
from unittest.mock import MagicMock, patch

from knack.util import CLIError

from azext_prototype.auth.github_auth import GitHubAuthManager
from azext_prototype.auth.copilot_license import CopilotLicenseValidator


class TestGitHubAuthManager:
    """Test GitHub auth management."""

    @patch("subprocess.run")
    def test_ensure_authenticated_success(self, mock_run):
        # _check_gh_installed, auth status, _get_user_info (api user)
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="gh version 2.0", stderr=""),
            MagicMock(returncode=0, stdout="Logged in", stderr=""),
            MagicMock(returncode=0, stdout='{"login":"testuser","name":"Test"}', stderr=""),
        ]

        mgr = GitHubAuthManager()
        info = mgr.ensure_authenticated()
        assert info["login"] == "testuser"

    @patch("subprocess.run")
    def test_get_token_returns_token(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ghp_test123token\n", stderr="")

        mgr = GitHubAuthManager()
        token = mgr.get_token()
        assert token == "ghp_test123token"

    @patch("subprocess.run")
    def test_get_token_error_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="auth error")

        mgr = GitHubAuthManager()
        with pytest.raises(CLIError, match="GitHub CLI error"):
            mgr.get_token()

    @patch("subprocess.run")
    def test_get_user_info(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"login":"testuser","name":"Test User"}',
            stderr="",
        )

        mgr = GitHubAuthManager()
        info = mgr.get_user_info()
        assert info["login"] == "testuser"


class TestCopilotLicenseValidator:
    """Test Copilot license validation."""

    @patch("subprocess.run")
    def test_has_valid_license_via_user_api(self, mock_run):
        mock_auth = MagicMock()
        mock_auth.get_token.return_value = "ghp_test"
        mock_auth.get_user_info.return_value = {"login": "testuser"}

        # Mock subprocess for gh api call
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"seats": [{"assignee": {"login": "testuser"}}]}',
        )

        validator = CopilotLicenseValidator(auth_manager=mock_auth)
        result = validator.validate_license()
        assert result is not None

    def test_validator_instantiates(self):
        mock_auth = MagicMock()
        validator = CopilotLicenseValidator(auth_manager=mock_auth)
        assert validator is not None
