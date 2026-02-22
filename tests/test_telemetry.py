"""Tests for azext_prototype.telemetry — App Insights telemetry collection."""

import logging
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

TELEMETRY_MODULE = "azext_prototype.telemetry"


@contextmanager
def _fake_azure_cli_modules():
    """Inject fake azure.cli.core.* modules into sys.modules so that
    ``from azure.cli.core._environment import get_config_dir`` succeeds
    even when azure-cli-core is not installed (e.g. CI).

    The ``azure`` namespace package may already be in sys.modules (from
    opencensus-ext-azure or azure-core) without having an ``azure.cli``
    submodule.  We must always inject the ``azure.cli.*`` hierarchy and
    wire it into whatever ``azure`` module is present.
    """
    cli_keys = [
        "azure.cli",
        "azure.cli.core",
        "azure.cli.core._environment",
        "azure.cli.core._profile",
    ]
    originals = {k: sys.modules.get(k) for k in cli_keys}
    fakes: dict[str, MagicMock] = {}
    try:
        for k in cli_keys:
            fakes[k] = MagicMock()
            sys.modules[k] = fakes[k]
        # Wire azure.cli into the existing azure namespace package
        azure_mod = sys.modules.get("azure")
        if azure_mod is not None:
            azure_mod.cli = fakes["azure.cli"]  # type: ignore[attr-defined]
        yield
    finally:
        for k, orig in originals.items():
            if orig is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = orig
        # Remove the injected .cli attribute from the real azure package
        azure_mod = sys.modules.get("azure")
        if azure_mod is not None and hasattr(azure_mod, "cli"):
            try:
                delattr(azure_mod, "cli")
            except (AttributeError, TypeError):
                pass


# ======================================================================
# Helpers
# ======================================================================

@pytest.fixture(autouse=True)
def _reset_telemetry():
    """Reset telemetry module state before each test."""
    from azext_prototype.telemetry import reset
    reset()
    yield
    reset()


_FAKE_CONN_STRING = (
    "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
    "IngestionEndpoint=https://test.in.applicationinsights.azure.com"
)


@pytest.fixture
def mock_env_conn_string(monkeypatch):
    """Set APPINSIGHTS_CONNECTION_STRING in the environment."""
    monkeypatch.setenv("APPINSIGHTS_CONNECTION_STRING", _FAKE_CONN_STRING)


# ======================================================================
# _is_cli_telemetry_enabled
# ======================================================================


class TestIsCliTelemetryEnabled:
    """Test Azure CLI telemetry opt-out detection."""

    def test_enabled_by_default(self, monkeypatch):
        """With no env var and no config file, telemetry should be enabled."""
        from azext_prototype.telemetry import _is_cli_telemetry_enabled

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)
        # Mock config dir to a non-existent path
        with patch(f"{TELEMETRY_MODULE}.get_config_dir", return_value="/tmp/__nonexistent__", create=True):
            assert _is_cli_telemetry_enabled() is True

    def test_env_var_yes(self, monkeypatch):
        from azext_prototype.telemetry import _is_cli_telemetry_enabled

        monkeypatch.setenv("AZURE_CORE_COLLECT_TELEMETRY", "yes")
        assert _is_cli_telemetry_enabled() is True

    def test_env_var_true(self, monkeypatch):
        from azext_prototype.telemetry import _is_cli_telemetry_enabled

        monkeypatch.setenv("AZURE_CORE_COLLECT_TELEMETRY", "true")
        assert _is_cli_telemetry_enabled() is True

    def test_env_var_no(self, monkeypatch):
        from azext_prototype.telemetry import _is_cli_telemetry_enabled

        monkeypatch.setenv("AZURE_CORE_COLLECT_TELEMETRY", "no")
        assert _is_cli_telemetry_enabled() is False

    def test_env_var_false(self, monkeypatch):
        from azext_prototype.telemetry import _is_cli_telemetry_enabled

        monkeypatch.setenv("AZURE_CORE_COLLECT_TELEMETRY", "false")
        assert _is_cli_telemetry_enabled() is False

    def test_env_var_zero(self, monkeypatch):
        from azext_prototype.telemetry import _is_cli_telemetry_enabled

        monkeypatch.setenv("AZURE_CORE_COLLECT_TELEMETRY", "0")
        assert _is_cli_telemetry_enabled() is False

    def test_env_var_off(self, monkeypatch):
        from azext_prototype.telemetry import _is_cli_telemetry_enabled

        monkeypatch.setenv("AZURE_CORE_COLLECT_TELEMETRY", "off")
        assert _is_cli_telemetry_enabled() is False

    def test_config_file_disabled(self, monkeypatch, tmp_path):
        """When the az config file says collect_telemetry=no, return False."""

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        config_file = tmp_path / "config"
        config_file.write_text("[core]\ncollect_telemetry = no\n")

        with _fake_azure_cli_modules(), patch(
            "azure.cli.core._environment.get_config_dir",
            return_value=str(tmp_path),
        ):
            from azext_prototype.telemetry import _is_cli_telemetry_enabled

            assert _is_cli_telemetry_enabled() is False

    def test_config_disable_telemetry_true(self, monkeypatch, tmp_path):
        """core.disable_telemetry=true should disable telemetry."""

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        config_file = tmp_path / "config"
        config_file.write_text("[core]\ndisable_telemetry = true\n")

        with _fake_azure_cli_modules(), patch(
            "azure.cli.core._environment.get_config_dir",
            return_value=str(tmp_path),
        ):
            from azext_prototype.telemetry import _is_cli_telemetry_enabled

            assert _is_cli_telemetry_enabled() is False

    def test_config_disable_telemetry_false(self, monkeypatch, tmp_path):
        """core.disable_telemetry=false should keep telemetry enabled."""

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        config_file = tmp_path / "config"
        config_file.write_text("[core]\ndisable_telemetry = false\n")

        with _fake_azure_cli_modules(), patch(
            "azure.cli.core._environment.get_config_dir",
            return_value=str(tmp_path),
        ):
            from azext_prototype.telemetry import _is_cli_telemetry_enabled

            assert _is_cli_telemetry_enabled() is True

    def test_config_disable_telemetry_missing_means_enabled(self, monkeypatch, tmp_path):
        """No disable_telemetry key at all should default to enabled."""

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        config_file = tmp_path / "config"
        config_file.write_text("[core]\nname = test\n")

        with _fake_azure_cli_modules(), patch(
            "azure.cli.core._environment.get_config_dir",
            return_value=str(tmp_path),
        ):
            from azext_prototype.telemetry import _is_cli_telemetry_enabled

            assert _is_cli_telemetry_enabled() is True

    def test_exception_returns_true(self, monkeypatch):
        """On any exception the function should default to True."""
        from azext_prototype.telemetry import _is_cli_telemetry_enabled

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        with _fake_azure_cli_modules(), patch(
            "azure.cli.core._environment.get_config_dir",
            side_effect=ImportError("no CLI"),
        ):
            assert _is_cli_telemetry_enabled() is True


# ======================================================================
# _get_connection_string
# ======================================================================


class TestGetConnectionString:
    """Test connection string resolution priority."""

    def test_env_var_takes_precedence(self, monkeypatch):
        from azext_prototype.telemetry import _get_connection_string

        monkeypatch.setenv("APPINSIGHTS_CONNECTION_STRING", "env-conn-string")
        assert _get_connection_string() == "env-conn-string"

    def test_falls_back_to_builtin(self, monkeypatch):
        from azext_prototype import telemetry
        from azext_prototype.telemetry import _get_connection_string

        monkeypatch.delenv("APPINSIGHTS_CONNECTION_STRING", raising=False)
        original = telemetry._BUILTIN_CONNECTION_STRING
        try:
            telemetry._BUILTIN_CONNECTION_STRING = "builtin-conn-string"
            assert _get_connection_string() == "builtin-conn-string"
        finally:
            telemetry._BUILTIN_CONNECTION_STRING = original

    def test_empty_when_neither_set(self, monkeypatch):
        from azext_prototype import telemetry
        from azext_prototype.telemetry import _get_connection_string

        monkeypatch.delenv("APPINSIGHTS_CONNECTION_STRING", raising=False)
        original = telemetry._BUILTIN_CONNECTION_STRING
        try:
            telemetry._BUILTIN_CONNECTION_STRING = ""
            assert _get_connection_string() == ""
        finally:
            telemetry._BUILTIN_CONNECTION_STRING = original


# ======================================================================
# is_enabled
# ======================================================================


class TestIsEnabled:
    """Test the is_enabled() gate function."""

    def test_disabled_when_no_connection_string(self, monkeypatch):
        from azext_prototype import telemetry
        from azext_prototype.telemetry import is_enabled

        monkeypatch.delenv("APPINSIGHTS_CONNECTION_STRING", raising=False)
        # Also clear the builtin to simulate truly missing connection string
        original = telemetry._BUILTIN_CONNECTION_STRING
        try:
            telemetry._BUILTIN_CONNECTION_STRING = ""
            assert is_enabled() is False
        finally:
            telemetry._BUILTIN_CONNECTION_STRING = original

    def test_disabled_when_cli_telemetry_off(self, monkeypatch, mock_env_conn_string):
        from azext_prototype.telemetry import is_enabled

        monkeypatch.setenv("AZURE_CORE_COLLECT_TELEMETRY", "no")
        assert is_enabled() is False

    def test_enabled_when_both_ok(self, monkeypatch, mock_env_conn_string):
        from azext_prototype.telemetry import is_enabled

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)
        assert is_enabled() is True

    def test_caches_result(self, monkeypatch, mock_env_conn_string):
        from azext_prototype import telemetry

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)
        result1 = telemetry.is_enabled()
        # Even if we change the env, cached result should be returned
        monkeypatch.delenv("APPINSIGHTS_CONNECTION_STRING", raising=False)
        result2 = telemetry.is_enabled()
        assert result1 == result2 is True

    def test_exception_disables(self, monkeypatch):
        from azext_prototype.telemetry import is_enabled

        with patch(
            f"{TELEMETRY_MODULE}._is_cli_telemetry_enabled",
            side_effect=RuntimeError("boom"),
        ):
            assert is_enabled() is False


# ======================================================================
# reset
# ======================================================================


class TestReset:
    """Test the reset() helper for test isolation."""

    def test_clears_cached_state(self, monkeypatch):
        from azext_prototype import telemetry

        monkeypatch.delenv("APPINSIGHTS_CONNECTION_STRING", raising=False)
        # Clear the builtin so is_enabled() returns False
        original = telemetry._BUILTIN_CONNECTION_STRING
        try:
            telemetry._BUILTIN_CONNECTION_STRING = ""
            telemetry.is_enabled()
            assert telemetry._enabled is False
        finally:
            telemetry._BUILTIN_CONNECTION_STRING = original

        telemetry.reset()
        assert telemetry._enabled is None
        assert telemetry._ingestion_endpoint is None
        assert telemetry._instrumentation_key is None


# ======================================================================
# _get_extension_version
# ======================================================================


class TestGetExtensionVersion:
    """Test extension version retrieval from metadata."""

    def test_reads_from_metadata(self):
        from azext_prototype.telemetry import _get_extension_version

        version = _get_extension_version()
        assert version == "0.2.1"

    def test_returns_unknown_on_error(self):
        from azext_prototype.telemetry import _get_extension_version

        with patch(
            "importlib.metadata.version",
            side_effect=Exception("not installed"),
        ):
            with patch("builtins.open", side_effect=FileNotFoundError):
                assert _get_extension_version() == "unknown"


# ======================================================================
# _get_tenant_id
# ======================================================================


class TestGetTenantId:
    """Test tenant ID extraction from CLI context."""

    def test_returns_tenant_on_success(self):
        from azext_prototype.telemetry import _get_tenant_id

        cmd = MagicMock()
        mock_profile = MagicMock()
        mock_profile.get_subscription.return_value = {
            "tenantId": "aaaabbbb-1111-2222-3333-ccccddddeeee"
        }

        with _fake_azure_cli_modules(), patch(
            "azure.cli.core._profile.Profile",
            return_value=mock_profile,
        ):
            result = _get_tenant_id(cmd)
            assert result == "aaaabbbb-1111-2222-3333-ccccddddeeee"

    def test_returns_empty_on_exception(self):
        from azext_prototype.telemetry import _get_tenant_id

        cmd = MagicMock()
        with _fake_azure_cli_modules(), patch(
            "azure.cli.core._profile.Profile",
            side_effect=Exception("no auth"),
        ):
            assert _get_tenant_id(cmd) == ""

    def test_returns_empty_when_no_tenant_key(self):
        from azext_prototype.telemetry import _get_tenant_id

        cmd = MagicMock()
        mock_profile = MagicMock()
        mock_profile.get_subscription.return_value = {}

        with _fake_azure_cli_modules(), patch(
            "azure.cli.core._profile.Profile",
            return_value=mock_profile,
        ):
            assert _get_tenant_id(cmd) == ""


# ======================================================================
# _parse_connection_string
# ======================================================================


class TestParseConnectionString:
    """Test connection string parsing."""

    def test_parses_valid_string(self):
        from azext_prototype.telemetry import _parse_connection_string

        cs = (
            "InstrumentationKey=abc-123;"
            "IngestionEndpoint=https://example.in.applicationinsights.azure.com"
        )
        endpoint, ikey = _parse_connection_string(cs)
        assert endpoint == "https://example.in.applicationinsights.azure.com/v2/track"
        assert ikey == "abc-123"

    def test_strips_trailing_slash(self):
        from azext_prototype.telemetry import _parse_connection_string

        cs = (
            "InstrumentationKey=key1;"
            "IngestionEndpoint=https://host.com/"
        )
        endpoint, _ = _parse_connection_string(cs)
        assert endpoint == "https://host.com/v2/track"

    def test_empty_string(self):
        from azext_prototype.telemetry import _parse_connection_string

        assert _parse_connection_string("") == ("", "")

    def test_missing_ikey(self):
        from azext_prototype.telemetry import _parse_connection_string

        assert _parse_connection_string(
            "IngestionEndpoint=https://host.com"
        ) == ("", "")

    def test_missing_endpoint(self):
        from azext_prototype.telemetry import _parse_connection_string

        assert _parse_connection_string(
            "InstrumentationKey=abc-123"
        ) == ("", "")


# ======================================================================
# _get_ingestion_config
# ======================================================================


class TestGetIngestionConfig:
    """Test cached ingestion config resolution."""

    def test_returns_parsed_config(self, monkeypatch, mock_env_conn_string):
        from azext_prototype.telemetry import _get_ingestion_config

        endpoint, ikey = _get_ingestion_config()
        assert endpoint == "https://test.in.applicationinsights.azure.com/v2/track"
        assert ikey == "00000000-0000-0000-0000-000000000000"

    def test_caches_result(self, monkeypatch, mock_env_conn_string):
        from azext_prototype import telemetry
        from azext_prototype.telemetry import _get_ingestion_config

        _get_ingestion_config()
        # Change env — cached result should be returned
        monkeypatch.setenv("APPINSIGHTS_CONNECTION_STRING", "different")
        endpoint, ikey = _get_ingestion_config()
        assert ikey == "00000000-0000-0000-0000-000000000000"

    def test_empty_when_no_connection_string(self, monkeypatch):
        from azext_prototype import telemetry
        from azext_prototype.telemetry import _get_ingestion_config

        monkeypatch.delenv("APPINSIGHTS_CONNECTION_STRING", raising=False)
        original = telemetry._BUILTIN_CONNECTION_STRING
        try:
            telemetry._BUILTIN_CONNECTION_STRING = ""
            assert _get_ingestion_config() == ("", "")
        finally:
            telemetry._BUILTIN_CONNECTION_STRING = original


# ======================================================================
# track_command
# ======================================================================


class TestTrackCommand:
    """Test the track_command() event sender."""

    def test_noop_when_disabled(self, monkeypatch):
        from azext_prototype import telemetry
        from azext_prototype.telemetry import track_command

        monkeypatch.delenv("APPINSIGHTS_CONNECTION_STRING", raising=False)
        # Clear the builtin so is_enabled() returns False
        original = telemetry._BUILTIN_CONNECTION_STRING
        try:
            telemetry._BUILTIN_CONNECTION_STRING = ""
            # Should not raise and should not make any network calls
            track_command("prototype init", cmd=MagicMock())
        finally:
            telemetry._BUILTIN_CONNECTION_STRING = original

    def test_sends_event_with_all_dimensions(self, monkeypatch, mock_env_conn_string):
        from azext_prototype.telemetry import track_command

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        with patch(f"{TELEMETRY_MODULE}._send_envelope", return_value=True) as mock_send:
            track_command(
                "prototype build",
                cmd=None,
                success=True,
                tenant_id="t-123",
                provider="github-models",
                model="gpt-4o",
                resource_type="Microsoft.Compute/virtualMachines",
                location="westus3",
                sku="Standard_D2s_v3",
            )

            mock_send.assert_called_once()
            envelope = mock_send.call_args[0][0]
            assert envelope["name"] == "Microsoft.ApplicationInsights.Event"
            assert envelope["iKey"] == "00000000-0000-0000-0000-000000000000"

            props = envelope["data"]["baseData"]["properties"]
            assert props["commandName"] == "prototype build"
            assert props["tenantId"] == "t-123"
            assert props["provider"] == "github-models"
            assert props["model"] == "gpt-4o"
            assert props["resourceType"] == "Microsoft.Compute/virtualMachines"
            assert props["location"] == "westus3"
            assert props["sku"] == "Standard_D2s_v3"
            assert props["success"] == "true"
            assert "extensionVersion" in props
            assert "timestamp" in props
            # No error or parameters when not provided
            assert "error" not in props
            assert "parameters" not in props

    def test_sends_event_with_defaults(self, monkeypatch, mock_env_conn_string):
        from azext_prototype.telemetry import track_command

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        with patch(f"{TELEMETRY_MODULE}._send_envelope", return_value=True) as mock_send:
            track_command("prototype status")

            props = mock_send.call_args[0][0]["data"]["baseData"]["properties"]
            assert props["commandName"] == "prototype status"
            assert props["tenantId"] == ""
            assert props["provider"] == ""
            assert props["model"] == ""
            assert props["location"] == ""
            assert props["success"] == "true"

    def test_extracts_tenant_from_cmd(self, monkeypatch, mock_env_conn_string):
        from azext_prototype.telemetry import track_command

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        with patch(f"{TELEMETRY_MODULE}._send_envelope", return_value=True) as mock_send:
            with patch(
                f"{TELEMETRY_MODULE}._get_tenant_id",
                return_value="auto-tenant-id",
            ):
                cmd = MagicMock()
                track_command("prototype deploy", cmd=cmd)

                props = mock_send.call_args[0][0]["data"]["baseData"]["properties"]
                assert props["tenantId"] == "auto-tenant-id"

    def test_explicit_tenant_overrides_cmd(self, monkeypatch, mock_env_conn_string):
        from azext_prototype.telemetry import track_command

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        with patch(f"{TELEMETRY_MODULE}._send_envelope", return_value=True) as mock_send:
            with patch(
                f"{TELEMETRY_MODULE}._get_tenant_id",
                return_value="auto-tenant",
            ):
                track_command("prototype deploy", cmd=MagicMock(), tenant_id="explicit-tenant")

                props = mock_send.call_args[0][0]["data"]["baseData"]["properties"]
                assert props["tenantId"] == "explicit-tenant"

    def test_failure_event(self, monkeypatch, mock_env_conn_string):
        from azext_prototype.telemetry import track_command

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        with patch(f"{TELEMETRY_MODULE}._send_envelope", return_value=True) as mock_send:
            track_command("prototype deploy", success=False)

            props = mock_send.call_args[0][0]["data"]["baseData"]["properties"]
            assert props["success"] == "false"

    def test_error_field_sent_on_failure(self, monkeypatch, mock_env_conn_string):
        from azext_prototype.telemetry import track_command

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        with patch(f"{TELEMETRY_MODULE}._send_envelope", return_value=True) as mock_send:
            track_command(
                "prototype deploy",
                success=False,
                error="CLIError: Resource group not found",
            )

            props = mock_send.call_args[0][0]["data"]["baseData"]["properties"]
            assert props["success"] == "false"
            assert props["error"] == "CLIError: Resource group not found"

    def test_error_field_truncated(self, monkeypatch, mock_env_conn_string):
        from azext_prototype.telemetry import track_command

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        long_error = "x" * 2000
        with patch(f"{TELEMETRY_MODULE}._send_envelope", return_value=True) as mock_send:
            track_command("prototype deploy", success=False, error=long_error)

            props = mock_send.call_args[0][0]["data"]["baseData"]["properties"]
            assert len(props["error"]) == 1024

    def test_parameters_field_sent(self, monkeypatch, mock_env_conn_string):
        import json as json_mod
        from azext_prototype.telemetry import track_command

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        with patch(f"{TELEMETRY_MODULE}._send_envelope", return_value=True) as mock_send:
            track_command(
                "prototype build",
                parameters={"scope": "all", "dry_run": True, "reset": False},
            )

            props = mock_send.call_args[0][0]["data"]["baseData"]["properties"]
            params = json_mod.loads(props["parameters"])
            assert params["scope"] == "all"
            assert params["dry_run"] is True
            assert params["reset"] is False

    def test_parameters_sensitive_keys_redacted(self, monkeypatch, mock_env_conn_string):
        import json as json_mod
        from azext_prototype.telemetry import track_command

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        with patch(f"{TELEMETRY_MODULE}._send_envelope", return_value=True) as mock_send:
            track_command(
                "prototype deploy",
                parameters={
                    "subscription": "abc-123-secret",
                    "resource_group": "my-rg",
                    "token": "ghp_secret",
                },
            )

            props = mock_send.call_args[0][0]["data"]["baseData"]["properties"]
            params = json_mod.loads(props["parameters"])
            assert params["subscription"] == "***"
            assert params["token"] == "***"
            assert params["resource_group"] == "my-rg"

    def test_parameters_omitted_when_none(self, monkeypatch, mock_env_conn_string):
        from azext_prototype.telemetry import track_command

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        with patch(f"{TELEMETRY_MODULE}._send_envelope", return_value=True) as mock_send:
            track_command("prototype status", parameters=None)

            props = mock_send.call_args[0][0]["data"]["baseData"]["properties"]
            assert "parameters" not in props

    def test_graceful_on_send_exception(self, monkeypatch, mock_env_conn_string):
        """If _send_envelope throws, track_command should not raise."""
        from azext_prototype.telemetry import track_command

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        with patch(
            f"{TELEMETRY_MODULE}._send_envelope",
            side_effect=Exception("network error"),
        ):
            # Must not raise
            track_command("prototype init")

    def test_calls_send_envelope(self, monkeypatch, mock_env_conn_string):
        """track_command must call _send_envelope with the correct endpoint."""
        from azext_prototype.telemetry import track_command

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        with patch(f"{TELEMETRY_MODULE}._send_envelope", return_value=True) as mock_send:
            track_command("prototype init")
            mock_send.assert_called_once()
            endpoint = mock_send.call_args[0][1]
            assert endpoint == "https://test.in.applicationinsights.azure.com/v2/track"


# ======================================================================
# _send_envelope
# ======================================================================


class TestSendEnvelope:
    """Test the direct HTTP ingestion function."""

    @pytest.fixture(autouse=True)
    def _no_telemetry_network(self):
        """Override the conftest autouse fixture — this class needs the
        real ``_send_envelope`` function so it can test it with mocked
        ``requests.post`` underneath."""
        yield

    def test_returns_true_on_200(self):
        from azext_prototype.telemetry import _send_envelope

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.post", return_value=mock_resp):
            assert _send_envelope({"test": 1}, "https://host/v2/track") is True

    def test_returns_false_on_non_200(self):
        from azext_prototype.telemetry import _send_envelope

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("requests.post", return_value=mock_resp):
            assert _send_envelope({"test": 1}, "https://host/v2/track") is False

    def test_returns_false_on_exception(self):
        from azext_prototype.telemetry import _send_envelope

        with patch(
            "requests.post",
            side_effect=Exception("timeout"),
        ):
            assert _send_envelope({"test": 1}, "https://host/v2/track") is False

    def test_posts_json_envelope(self):
        from azext_prototype.telemetry import _send_envelope

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.post", return_value=mock_resp) as mock_post:
            _send_envelope({"key": "val"}, "https://host/v2/track")
            mock_post.assert_called_once()
            _, kwargs = mock_post.call_args
            assert kwargs["headers"]["Content-Type"] == "application/json"
            assert kwargs["timeout"] == 5
            import json
            payload = json.loads(kwargs["data"])
            assert payload == [{"key": "val"}]


# ======================================================================
# @track decorator
# ======================================================================


class TestTrackDecorator:
    """Test the @track() command decorator."""

    def test_decorator_passes_through_result(self):
        from azext_prototype.telemetry import track as track_decorator

        @track_decorator("test command")
        def my_command(cmd, location="eastus"):
            return {"status": "ok"}

        with patch(f"{TELEMETRY_MODULE}.track_command") as mock_tc:
            result = my_command(MagicMock(), location="westus2")
            assert result == {"status": "ok"}
            mock_tc.assert_called_once()

    def test_decorator_tracks_success(self):
        from azext_prototype.telemetry import track as track_decorator

        @track_decorator("test success")
        def my_command(cmd):
            return "done"

        with patch(f"{TELEMETRY_MODULE}.track_command") as mock_tc:
            with patch(f"{TELEMETRY_MODULE}._get_ai_config", return_value=("", "")):
                my_command(MagicMock())
                mock_tc.assert_called_once()
                _, kwargs = mock_tc.call_args
                assert kwargs["success"] is True
                assert kwargs["error"] == ""
                assert kwargs["parameters"] == {}
                assert kwargs["location"] == ""
                assert kwargs["provider"] == ""
                assert kwargs["model"] == ""

    def test_decorator_tracks_failure(self):
        from azext_prototype.telemetry import track as track_decorator

        @track_decorator("test fail")
        def my_command(cmd):
            raise ValueError("boom")

        with patch(f"{TELEMETRY_MODULE}.track_command") as mock_tc:
            with pytest.raises(ValueError, match="boom"):
                my_command(MagicMock())

            # Telemetry should still have been sent with success=False
            assert mock_tc.called
            _, kwargs = mock_tc.call_args
            assert kwargs["success"] is False
            assert "ValueError: boom" in kwargs["error"]

    def test_decorator_extracts_location_kwarg(self):
        from azext_prototype.telemetry import track as track_decorator

        @track_decorator("test location")
        def my_command(cmd, location="eastus"):
            return None

        with patch(f"{TELEMETRY_MODULE}.track_command") as mock_tc:
            my_command(MagicMock(), location="westus3")
            _, kwargs = mock_tc.call_args
            assert kwargs["location"] == "westus3"

    def test_decorator_sends_parameters(self):
        """Decorator forwards kwargs as the parameters dict."""
        from azext_prototype.telemetry import track as track_decorator

        @track_decorator("test params")
        def my_command(cmd, scope="all", dry_run=False):
            return "ok"

        with patch(f"{TELEMETRY_MODULE}.track_command") as mock_tc:
            my_command(MagicMock(), scope="infra", dry_run=True)
            _, kwargs = mock_tc.call_args
            assert kwargs["parameters"] == {"scope": "infra", "dry_run": True}

    def test_decorator_sends_error_on_exception(self):
        """Decorator captures exception type and message."""
        from azext_prototype.telemetry import track as track_decorator

        @track_decorator("test error capture")
        def my_command(cmd):
            raise RuntimeError("deploy failed: timeout after 300s")

        with patch(f"{TELEMETRY_MODULE}.track_command") as mock_tc:
            with pytest.raises(RuntimeError):
                my_command(MagicMock())

            _, kwargs = mock_tc.call_args
            assert kwargs["success"] is False
            assert kwargs["error"] == "RuntimeError: deploy failed: timeout after 300s"

    def test_decorator_does_not_break_on_telemetry_error(self):
        """If track_command itself raises, the command should still succeed."""
        from azext_prototype.telemetry import track as track_decorator

        @track_decorator("test resilience")
        def my_command(cmd):
            return {"status": "ok"}

        with patch(
            f"{TELEMETRY_MODULE}.track_command",
            side_effect=Exception("telemetry boom"),
        ):
            # Command must succeed even though telemetry exploded
            result = my_command(MagicMock())
            assert result == {"status": "ok"}

    def test_decorator_preserves_function_name(self):
        from azext_prototype.telemetry import track as track_decorator

        @track_decorator("test name")
        def prototype_my_func(cmd):
            """My docstring."""
            pass

        assert prototype_my_func.__name__ == "prototype_my_func"
        assert prototype_my_func.__doc__ == "My docstring."

    def test_decorator_passes_args_and_kwargs(self):
        from azext_prototype.telemetry import track as track_decorator

        @track_decorator("test args")
        def my_command(cmd, name=None, scope="all"):
            return {"name": name, "scope": scope}

        with patch(f"{TELEMETRY_MODULE}.track_command"):
            result = my_command(MagicMock(), name="proj", scope="infra")
            assert result == {"name": "proj", "scope": "infra"}

    def test_decorator_sends_provider_and_model(self):
        """Decorator reads AI config and forwards provider/model."""
        from azext_prototype.telemetry import track as track_decorator

        @track_decorator("test ai dims")
        def my_command(cmd):
            return "ok"

        with patch(f"{TELEMETRY_MODULE}.track_command") as mock_tc:
            with patch(
                f"{TELEMETRY_MODULE}._get_ai_config",
                return_value=("azure-openai", "gpt-4o"),
            ):
                my_command(MagicMock())
                _, kwargs = mock_tc.call_args
                assert kwargs["provider"] == "azure-openai"
                assert kwargs["model"] == "gpt-4o"

    def test_decorator_prefers_ai_provider_kwarg(self):
        """When ai_provider is passed as a kwarg (e.g. prototype init),
        it should be used instead of _get_ai_config()."""
        from azext_prototype.telemetry import track as track_decorator

        @track_decorator("test init provider")
        def my_command(cmd, ai_provider="copilot"):
            return "ok"

        with patch(f"{TELEMETRY_MODULE}.track_command") as mock_tc:
            with patch(
                f"{TELEMETRY_MODULE}._get_ai_config",
                return_value=("", ""),
            ):
                my_command(MagicMock(), ai_provider="copilot")
                _, kwargs = mock_tc.call_args
                assert kwargs["provider"] == "copilot"
                # Default model should be resolved from provider
                assert kwargs["model"] == "claude-sonnet-4.5"

    def test_decorator_kwarg_provider_with_config_model(self):
        """When ai_provider kwarg is present but model is not,
        provider comes from kwarg and model falls back to config."""
        from azext_prototype.telemetry import track as track_decorator

        @track_decorator("test mixed")
        def my_command(cmd, ai_provider="github-models"):
            return "ok"

        with patch(f"{TELEMETRY_MODULE}.track_command") as mock_tc:
            with patch(
                f"{TELEMETRY_MODULE}._get_ai_config",
                return_value=("github-models", "gpt-4o"),
            ):
                my_command(MagicMock(), ai_provider="github-models")
                _, kwargs = mock_tc.call_args
                assert kwargs["provider"] == "github-models"
                assert kwargs["model"] == "gpt-4o"

    def test_decorator_resolves_default_model_from_provider(self):
        """When provider is known but model can't be read from config
        (e.g. init creates config in a subdirectory), the decorator
        falls back to the default model for that provider."""
        from azext_prototype.telemetry import track as track_decorator

        for prov, expected_model in [
            ("copilot", "claude-sonnet-4.5"),
            ("github-models", "gpt-4o"),
            ("azure-openai", "gpt-4o"),
        ]:
            @track_decorator(f"test default model {prov}")
            def my_command(cmd, ai_provider="copilot"):
                return "ok"

            with patch(f"{TELEMETRY_MODULE}.track_command") as mock_tc:
                with patch(
                    f"{TELEMETRY_MODULE}._get_ai_config",
                    return_value=("", ""),
                ):
                    my_command(MagicMock(), ai_provider=prov)
                    _, kwargs = mock_tc.call_args
                    assert kwargs["provider"] == prov
                    assert kwargs["model"] == expected_model, (
                        f"Expected model '{expected_model}' for provider '{prov}', "
                        f"got '{kwargs['model']}'"
                    )

    def test_decorator_reads_telemetry_overrides(self):
        """When cmd._telemetry_overrides is set, the decorator should
        use those values for location, provider, model, and parameters."""
        from azext_prototype.telemetry import track as track_decorator

        @track_decorator("test overrides")
        def my_command(cmd):
            cmd._telemetry_overrides = {
                "location": "westeurope",
                "ai_provider": "azure-openai",
                "model": "gpt-4o-mini",
                "iac_tool": "bicep",
                "environment": "prod",
            }
            return "ok"

        with patch(f"{TELEMETRY_MODULE}.track_command") as mock_tc:
            with patch(
                f"{TELEMETRY_MODULE}._get_ai_config",
                return_value=("", ""),
            ):
                my_command(MagicMock())
                _, kwargs = mock_tc.call_args
                assert kwargs["location"] == "westeurope"
                assert kwargs["provider"] == "azure-openai"
                assert kwargs["model"] == "gpt-4o-mini"
                # Overrides should be merged into parameters
                assert kwargs["parameters"]["iac_tool"] == "bicep"
                assert kwargs["parameters"]["environment"] == "prod"

    def test_decorator_overrides_take_precedence(self):
        """_telemetry_overrides should take precedence over kwargs."""
        from azext_prototype.telemetry import track as track_decorator

        @track_decorator("test precedence")
        def my_command(cmd, location="eastus"):
            cmd._telemetry_overrides = {"location": "westus2"}
            return "ok"

        with patch(f"{TELEMETRY_MODULE}.track_command") as mock_tc:
            with patch(
                f"{TELEMETRY_MODULE}._get_ai_config",
                return_value=("", ""),
            ):
                my_command(MagicMock(), location="eastus")
                _, kwargs = mock_tc.call_args
                assert kwargs["location"] == "westus2"

    def test_decorator_no_overrides_attr(self):
        """When cmd has no _telemetry_overrides, decorator works normally."""
        from azext_prototype.telemetry import track as track_decorator

        @track_decorator("test no overrides")
        def my_command(cmd, location="eastus"):
            return "ok"

        with patch(f"{TELEMETRY_MODULE}.track_command") as mock_tc:
            with patch(
                f"{TELEMETRY_MODULE}._get_ai_config",
                return_value=("", ""),
            ):
                my_command(MagicMock(spec=[]), location="northeurope")
                _, kwargs = mock_tc.call_args
                assert kwargs["location"] == "northeurope"


# ======================================================================
# _get_ai_config
# ======================================================================


class TestGetAiConfig:
    """Test AI provider/model extraction from project config."""

    def test_returns_provider_and_model(self, tmp_path, monkeypatch):
        from azext_prototype.telemetry import _get_ai_config

        (tmp_path / "prototype.yaml").write_text(
            "ai:\n  provider: github-models\n  model: gpt-4o-mini\n"
        )
        monkeypatch.chdir(tmp_path)
        assert _get_ai_config() == ("github-models", "gpt-4o-mini")

    def test_returns_empty_when_no_config(self, tmp_path, monkeypatch):
        from azext_prototype.telemetry import _get_ai_config

        monkeypatch.chdir(tmp_path)
        assert _get_ai_config() == ("", "")

    def test_returns_empty_when_no_ai_section(self, tmp_path, monkeypatch):
        from azext_prototype.telemetry import _get_ai_config

        (tmp_path / "prototype.yaml").write_text("project:\n  name: test\n")
        monkeypatch.chdir(tmp_path)
        assert _get_ai_config() == ("", "")

    def test_returns_empty_on_malformed_yaml(self, tmp_path, monkeypatch):
        from azext_prototype.telemetry import _get_ai_config

        (tmp_path / "prototype.yaml").write_text(": : : bad yaml {{{\n")
        monkeypatch.chdir(tmp_path)
        # Should not raise — returns empty tuple
        assert _get_ai_config() == ("", "")

    def test_partial_ai_section(self, tmp_path, monkeypatch):
        from azext_prototype.telemetry import _get_ai_config

        (tmp_path / "prototype.yaml").write_text("ai:\n  provider: copilot\n")
        monkeypatch.chdir(tmp_path)
        assert _get_ai_config() == ("copilot", "")


# ======================================================================
# _sanitize_parameters
# ======================================================================


class TestSanitizeParameters:
    """Test parameter sanitization for telemetry."""

    def test_passes_scalar_values(self):
        from azext_prototype.telemetry import _sanitize_parameters

        result = _sanitize_parameters({"scope": "all", "count": 5, "flag": True, "val": None})
        assert result == {"scope": "all", "count": 5, "flag": True, "val": None}

    def test_redacts_sensitive_keys(self):
        from azext_prototype.telemetry import _sanitize_parameters

        result = _sanitize_parameters({
            "subscription": "abc-123",
            "token": "ghp_secret",
            "api_key": "sk-xxx",
            "password": "p@ss",
            "key": "my-key",
            "secret": "shhh",
            "connection_string": "Server=...",
            "name": "my-project",
        })
        assert result["subscription"] == "***"
        assert result["token"] == "***"
        assert result["api_key"] == "***"
        assert result["password"] == "***"
        assert result["key"] == "***"
        assert result["secret"] == "***"
        assert result["connection_string"] == "***"
        assert result["name"] == "my-project"

    def test_skips_private_keys(self):
        from azext_prototype.telemetry import _sanitize_parameters

        result = _sanitize_parameters({"_internal": "hidden", "scope": "all"})
        assert "_internal" not in result
        assert result["scope"] == "all"

    def test_non_serializable_values_show_type(self):
        from azext_prototype.telemetry import _sanitize_parameters

        result = _sanitize_parameters({"cmd": object(), "scope": "all"})
        assert result["cmd"] == "object"
        assert result["scope"] == "all"


# ======================================================================
# Integration with custom.py commands
# ======================================================================


class TestCommandTelemetryIntegration:
    """Verify that telemetry decorators are applied to all commands."""

    def test_all_commands_have_track_decorator(self):
        """All prototype_* functions in custom.py should be decorated."""
        import azext_prototype.custom as custom_mod

        command_functions = [
            name
            for name in dir(custom_mod)
            if name.startswith("prototype_") and callable(getattr(custom_mod, name))
        ]

        for name in command_functions:
            func = getattr(custom_mod, name)
            # Decorated functions have __wrapped__ set by functools.wraps
            assert hasattr(func, "__wrapped__"), (
                f"{name} is missing the @track decorator"
            )

    def test_command_count(self):
        """Sanity check — we expect 22 command functions."""
        import azext_prototype.custom as custom_mod

        command_functions = [
            name
            for name in dir(custom_mod)
            if name.startswith("prototype_") and callable(getattr(custom_mod, name))
        ]

        assert len(command_functions) == 26


# ======================================================================
# TELEMETRY.md field coverage
# ======================================================================


class TestTelemetryFieldCoverage:
    """Verify all TELEMETRY.md fields are present in events."""

    EXPECTED_FIELDS = {
        "commandName",
        "tenantId",
        "provider",
        "model",
        "resourceType",
        "location",
        "sku",
        "extensionVersion",
        "success",
        "timestamp",
    }

    # Fields that are only present conditionally
    CONDITIONAL_FIELDS = {
        "parameters",  # only when parameters dict is provided
        "error",       # only when error string is provided
    }

    def test_all_fields_in_track_command(self, monkeypatch, mock_env_conn_string):
        from azext_prototype.telemetry import track_command

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        with patch(f"{TELEMETRY_MODULE}._send_envelope", return_value=True) as mock_send:
            track_command(
                "prototype build",
                success=True,
                tenant_id="t-1",
                provider="github-models",
                model="gpt-4o",
                resource_type="Microsoft.Web/sites",
                location="eastus",
                sku="S1",
            )

            props = mock_send.call_args[0][0]["data"]["baseData"]["properties"]
            actual_fields = set(props.keys())

            missing = self.EXPECTED_FIELDS - actual_fields
            assert not missing, f"Missing TELEMETRY.md fields: {missing}"

    def test_conditional_fields_when_provided(self, monkeypatch, mock_env_conn_string):
        from azext_prototype.telemetry import track_command

        monkeypatch.delenv("AZURE_CORE_COLLECT_TELEMETRY", raising=False)

        with patch(f"{TELEMETRY_MODULE}._send_envelope", return_value=True) as mock_send:
            track_command(
                "prototype deploy",
                success=False,
                error="CLIError: oops",
                parameters={"dry_run": True},
            )

            props = mock_send.call_args[0][0]["data"]["baseData"]["properties"]
            actual_fields = set(props.keys())

            all_expected = self.EXPECTED_FIELDS | self.CONDITIONAL_FIELDS
            missing = all_expected - actual_fields
            assert not missing, f"Missing fields: {missing}"
