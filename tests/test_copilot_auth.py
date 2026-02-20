"""Tests for azext_prototype.ai.copilot_auth -- credential resolution."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from knack.util import CLIError

from azext_prototype.ai import copilot_auth


# ======================================================================
# _get_copilot_cli_config_dir
# ======================================================================


class TestGetCopilotCliConfigDir:
    """Test Copilot CLI config directory resolution."""

    @patch.dict("os.environ", {"XDG_CONFIG_HOME": "/custom/xdg"})
    def test_uses_xdg_when_set(self):
        result = copilot_auth._get_copilot_cli_config_dir()
        assert result == Path("/custom/xdg")

    @patch.dict("os.environ", {}, clear=False)
    @patch("pathlib.Path.home", return_value=Path("/mock/home"))
    def test_falls_back_to_home_copilot(self, _mock_home):
        import os
        os.environ.pop("XDG_CONFIG_HOME", None)
        result = copilot_auth._get_copilot_cli_config_dir()
        assert result == Path("/mock/home") / ".copilot"


# ======================================================================
# _get_copilot_config_dir (legacy)
# ======================================================================


class TestGetCopilotConfigDir:
    """Test platform-specific legacy config directory resolution."""

    @patch("azext_prototype.ai.copilot_auth.platform.system", return_value="Windows")
    @patch.dict("os.environ", {"LOCALAPPDATA": r"C:\Users\test\AppData\Local"})
    def test_windows(self, _mock_sys):
        result = copilot_auth._get_copilot_config_dir()
        assert result == Path(r"C:\Users\test\AppData\Local") / "github-copilot"

    @patch("azext_prototype.ai.copilot_auth.platform.system", return_value="Darwin")
    def test_macos(self, _mock_sys):
        result = copilot_auth._get_copilot_config_dir()
        assert "github-copilot" in str(result)
        assert "Application Support" in str(result)

    @patch("azext_prototype.ai.copilot_auth.platform.system", return_value="Linux")
    @patch.dict("os.environ", {"XDG_CONFIG_HOME": "/custom/config"}, clear=False)
    def test_linux_xdg(self, _mock_sys):
        result = copilot_auth._get_copilot_config_dir()
        assert result == Path("/custom/config/github-copilot")

    @patch("azext_prototype.ai.copilot_auth.platform.system", return_value="Linux")
    @patch.dict("os.environ", {"HOME": "/home/testuser"})
    def test_linux_default(self, _mock_sys):
        import os
        os.environ.pop("XDG_CONFIG_HOME", None)
        result = copilot_auth._get_copilot_config_dir()
        assert "github-copilot" in str(result)
        assert ".config" in str(result)


# ======================================================================
# _read_keychain_token
# ======================================================================


class TestReadKeychainToken:
    """Test reading tokens from the OS keychain."""

    @patch("azext_prototype.ai.copilot_auth.platform.system", return_value="Linux")
    def test_returns_none_on_non_windows(self, _mock):
        assert copilot_auth._read_keychain_token() is None

    @patch("azext_prototype.ai.copilot_auth.platform.system", return_value="Windows")
    @patch("azext_prototype.ai.copilot_auth._get_copilot_cli_config_dir")
    def test_returns_none_when_no_config(self, mock_dir, _mock_sys, tmp_path):
        mock_dir.return_value = tmp_path  # No config.json exists
        assert copilot_auth._read_keychain_token() is None

    @patch("azext_prototype.ai.copilot_auth.platform.system", return_value="Windows")
    @patch("azext_prototype.ai.copilot_auth._get_copilot_cli_config_dir")
    def test_returns_none_when_no_logged_in_users(self, mock_dir, _mock_sys, tmp_path):
        mock_dir.return_value = tmp_path
        (tmp_path / "config.json").write_text(json.dumps({"staff": True}))
        assert copilot_auth._read_keychain_token() is None


# ======================================================================
# _read_oauth_token
# ======================================================================


class TestReadOAuthToken:
    """Test reading OAuth tokens from hosts.json / apps.json."""

    @patch("azext_prototype.ai.copilot_auth._get_copilot_config_dir")
    def test_reads_from_hosts_json(self, mock_dir, tmp_path):
        mock_dir.return_value = tmp_path
        hosts = {"github.com": {"oauth_token": "ghu_test123"}}
        (tmp_path / "hosts.json").write_text(json.dumps(hosts))
        assert copilot_auth._read_oauth_token() == "ghu_test123"

    @patch("azext_prototype.ai.copilot_auth._get_copilot_config_dir")
    def test_reads_from_apps_json_fallback(self, mock_dir, tmp_path):
        mock_dir.return_value = tmp_path
        apps = {"github.com": {"oauth_token": "ghu_apptoken"}}
        (tmp_path / "apps.json").write_text(json.dumps(apps))
        assert copilot_auth._read_oauth_token() == "ghu_apptoken"

    @patch("azext_prototype.ai.copilot_auth._get_copilot_config_dir")
    def test_hosts_json_preferred_over_apps(self, mock_dir, tmp_path):
        mock_dir.return_value = tmp_path
        (tmp_path / "hosts.json").write_text(
            json.dumps({"github.com": {"oauth_token": "ghu_hosts"}})
        )
        (tmp_path / "apps.json").write_text(
            json.dumps({"github.com": {"oauth_token": "ghu_apps"}})
        )
        assert copilot_auth._read_oauth_token() == "ghu_hosts"

    @patch("azext_prototype.ai.copilot_auth._get_copilot_config_dir")
    def test_returns_none_if_no_files(self, mock_dir, tmp_path):
        mock_dir.return_value = tmp_path
        assert copilot_auth._read_oauth_token() is None

    @patch("azext_prototype.ai.copilot_auth._get_copilot_config_dir")
    def test_returns_none_for_empty_json(self, mock_dir, tmp_path):
        mock_dir.return_value = tmp_path
        (tmp_path / "hosts.json").write_text("{}")
        assert copilot_auth._read_oauth_token() is None

    @patch("azext_prototype.ai.copilot_auth._get_copilot_config_dir")
    def test_returns_none_for_malformed_json(self, mock_dir, tmp_path):
        mock_dir.return_value = tmp_path
        (tmp_path / "hosts.json").write_text("not valid json!!!")
        assert copilot_auth._read_oauth_token() is None

    @patch("azext_prototype.ai.copilot_auth._get_copilot_config_dir")
    def test_skips_entries_without_oauth_token(self, mock_dir, tmp_path):
        mock_dir.return_value = tmp_path
        hosts = {"github.com": {"user": "test", "some_other_field": "value"}}
        (tmp_path / "hosts.json").write_text(json.dumps(hosts))
        assert copilot_auth._read_oauth_token() is None


# ======================================================================
# _read_gh_token
# ======================================================================


class TestReadGhToken:
    """Test reading token from the gh CLI."""

    @patch("azext_prototype.ai.copilot_auth.subprocess.run")
    def test_returns_token_on_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ghp_abc123\n")
        assert copilot_auth._read_gh_token() == "ghp_abc123"

    @patch("azext_prototype.ai.copilot_auth.subprocess.run")
    def test_returns_none_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert copilot_auth._read_gh_token() is None

    @patch("azext_prototype.ai.copilot_auth.subprocess.run", side_effect=FileNotFoundError)
    def test_returns_none_when_gh_not_installed(self, _mock):
        assert copilot_auth._read_gh_token() is None


# ======================================================================
# _resolve_token (priority order)
# ======================================================================


class TestResolveToken:
    """Test the token resolution priority chain."""

    @patch.dict("os.environ", {"COPILOT_GITHUB_TOKEN": "cgt_highest"})
    @patch("azext_prototype.ai.copilot_auth._read_keychain_token", return_value="keychain_token")
    def test_copilot_github_token_wins(self, _mock_keychain):
        token, source = copilot_auth._resolve_token()
        assert token == "cgt_highest"
        assert source == "env:COPILOT_GITHUB_TOKEN"

    @patch.dict("os.environ", {"GH_TOKEN": "ght_second"}, clear=False)
    @patch("azext_prototype.ai.copilot_auth._read_keychain_token", return_value="keychain_token")
    def test_gh_token_second(self, _mock_keychain):
        import os
        os.environ.pop("COPILOT_GITHUB_TOKEN", None)
        token, source = copilot_auth._resolve_token()
        assert token == "ght_second"
        assert source == "env:GH_TOKEN"

    @patch.dict("os.environ", {}, clear=False)
    @patch("azext_prototype.ai.copilot_auth._read_keychain_token", return_value="gho_keychain")
    @patch("azext_prototype.ai.copilot_auth._read_oauth_token", return_value="ghu_legacy")
    @patch("azext_prototype.ai.copilot_auth._read_gh_token", return_value="ghp_cli")
    def test_keychain_before_legacy(self, _gh, _legacy, _kc):
        import os
        os.environ.pop("COPILOT_GITHUB_TOKEN", None)
        os.environ.pop("GH_TOKEN", None)
        token, source = copilot_auth._resolve_token()
        assert token == "gho_keychain"
        assert source == "copilot-cli-keychain"

    @patch.dict("os.environ", {}, clear=False)
    @patch("azext_prototype.ai.copilot_auth._read_keychain_token", return_value=None)
    @patch("azext_prototype.ai.copilot_auth._read_oauth_token", return_value="ghu_legacy")
    @patch("azext_prototype.ai.copilot_auth._read_gh_token", return_value="ghp_cli")
    def test_legacy_before_gh_cli(self, _gh, _legacy, _kc):
        import os
        os.environ.pop("COPILOT_GITHUB_TOKEN", None)
        os.environ.pop("GH_TOKEN", None)
        token, source = copilot_auth._resolve_token()
        assert token == "ghu_legacy"
        assert source == "copilot-sdk-config"

    @patch.dict("os.environ", {}, clear=False)
    @patch("azext_prototype.ai.copilot_auth._read_keychain_token", return_value=None)
    @patch("azext_prototype.ai.copilot_auth._read_oauth_token", return_value=None)
    @patch("azext_prototype.ai.copilot_auth._read_gh_token", return_value="ghp_cli")
    def test_gh_cli_before_github_token_env(self, _gh, _legacy, _kc):
        import os
        os.environ.pop("COPILOT_GITHUB_TOKEN", None)
        os.environ.pop("GH_TOKEN", None)
        os.environ.pop("GITHUB_TOKEN", None)
        token, source = copilot_auth._resolve_token()
        assert token == "ghp_cli"
        assert source == "gh-cli"

    @patch.dict("os.environ", {"GITHUB_TOKEN": "gt_lowest"}, clear=False)
    @patch("azext_prototype.ai.copilot_auth._read_keychain_token", return_value=None)
    @patch("azext_prototype.ai.copilot_auth._read_oauth_token", return_value=None)
    @patch("azext_prototype.ai.copilot_auth._read_gh_token", return_value=None)
    def test_github_token_lowest(self, _gh, _legacy, _kc):
        import os
        os.environ.pop("COPILOT_GITHUB_TOKEN", None)
        os.environ.pop("GH_TOKEN", None)
        token, source = copilot_auth._resolve_token()
        assert token == "gt_lowest"
        assert source == "env:GITHUB_TOKEN"

    @patch.dict("os.environ", {}, clear=False)
    @patch("azext_prototype.ai.copilot_auth._read_keychain_token", return_value=None)
    @patch("azext_prototype.ai.copilot_auth._read_oauth_token", return_value=None)
    @patch("azext_prototype.ai.copilot_auth._read_gh_token", return_value=None)
    def test_returns_none_when_nothing(self, _gh, _legacy, _kc):
        import os
        os.environ.pop("COPILOT_GITHUB_TOKEN", None)
        os.environ.pop("GH_TOKEN", None)
        os.environ.pop("GITHUB_TOKEN", None)
        assert copilot_auth._resolve_token() is None


# ======================================================================
# get_copilot_token (returns raw token)
# ======================================================================


class TestGetCopilotToken:
    """Test raw token retrieval."""

    @patch("azext_prototype.ai.copilot_auth._resolve_token", return_value=("gho_test", "test"))
    def test_returns_raw_token(self, _mock_resolve):
        token = copilot_auth.get_copilot_token()
        assert token == "gho_test"

    @patch("azext_prototype.ai.copilot_auth._resolve_token", return_value=None)
    def test_raises_if_no_credentials(self, _mock):
        with pytest.raises(CLIError, match="No Copilot credentials"):
            copilot_auth.get_copilot_token()


# ======================================================================
# is_copilot_authenticated
# ======================================================================


class TestIsCopilotAuthenticated:
    """Test the quick credential check."""

    @patch("azext_prototype.ai.copilot_auth._resolve_token", return_value=("gho_t", "test"))
    def test_true_when_token_exists(self, _mock):
        assert copilot_auth.is_copilot_authenticated() is True

    @patch("azext_prototype.ai.copilot_auth._resolve_token", return_value=None)
    def test_false_when_no_token(self, _mock):
        assert copilot_auth.is_copilot_authenticated() is False
