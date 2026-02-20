"""Tests for azext_prototype.anti_patterns — post-generation output scanning.

Tests the YAML-based anti-pattern loader and scanner across all domains.
"""

import pytest
from pathlib import Path

from azext_prototype.governance import anti_patterns
from azext_prototype.governance.anti_patterns import AntiPatternCheck, load, scan, reset_cache


@pytest.fixture(autouse=True)
def _clean_cache():
    """Reset anti-pattern cache before and after each test."""
    reset_cache()
    yield
    reset_cache()


# ------------------------------------------------------------------ #
# Loader tests
# ------------------------------------------------------------------ #

class TestAntiPatternLoader:
    """Test YAML loading from the anti_patterns directory."""

    def test_load_returns_non_empty(self):
        checks = load()
        assert len(checks) > 0

    def test_load_returns_anti_pattern_check_objects(self):
        checks = load()
        assert all(isinstance(c, AntiPatternCheck) for c in checks)

    def test_all_checks_have_domain(self):
        checks = load()
        for check in checks:
            assert check.domain, f"Check missing domain: {check.warning_message}"

    def test_all_checks_have_search_patterns(self):
        checks = load()
        for check in checks:
            assert len(check.search_patterns) > 0, (
                f"Check has no search_patterns: {check.warning_message}"
            )

    def test_all_checks_have_warning_message(self):
        checks = load()
        for check in checks:
            assert check.warning_message, f"Check missing warning_message in domain {check.domain}"

    def test_search_patterns_are_lowercased(self):
        checks = load()
        for check in checks:
            for pat in check.search_patterns:
                assert pat == pat.lower(), f"Pattern not lowercased: {pat}"

    def test_safe_patterns_are_lowercased(self):
        checks = load()
        for check in checks:
            for pat in check.safe_patterns:
                assert pat == pat.lower(), f"Safe pattern not lowercased: {pat}"

    def test_load_is_cached(self):
        first = load()
        second = load()
        assert first is second

    def test_reset_cache_clears(self):
        first = load()
        reset_cache()
        second = load()
        assert first is not second
        assert len(first) == len(second)

    def test_domains_loaded(self):
        checks = load()
        domains = {c.domain for c in checks}
        assert "security" in domains
        assert "networking" in domains
        assert "authentication" in domains

    def test_load_from_missing_directory(self):
        checks = load(directory=Path("/nonexistent"))
        assert checks == []

    def test_load_from_empty_directory(self, tmp_path):
        checks = load(directory=tmp_path)
        assert checks == []

    def test_load_from_custom_directory(self, tmp_path):
        yaml_content = (
            "domain: test\n"
            "patterns:\n"
            "  - search_patterns:\n"
            "      - \"test_pattern\"\n"
            "    safe_patterns: []\n"
            "    warning_message: \"Test warning\"\n"
        )
        (tmp_path / "test.yaml").write_text(yaml_content)
        reset_cache()
        checks = load(directory=tmp_path)
        assert len(checks) == 1
        assert checks[0].domain == "test"
        assert checks[0].search_patterns == ["test_pattern"]

    def test_load_skips_invalid_yaml(self, tmp_path):
        (tmp_path / "bad.yaml").write_text("{{invalid yaml")
        reset_cache()
        checks = load(directory=tmp_path)
        assert checks == []

    def test_load_skips_entries_without_search_patterns(self, tmp_path):
        yaml_content = (
            "domain: test\n"
            "patterns:\n"
            "  - search_patterns: []\n"
            "    warning_message: \"Empty search\"\n"
        )
        (tmp_path / "test.yaml").write_text(yaml_content)
        reset_cache()
        checks = load(directory=tmp_path)
        assert checks == []

    def test_load_skips_entries_without_warning_message(self, tmp_path):
        yaml_content = (
            "domain: test\n"
            "patterns:\n"
            "  - search_patterns:\n"
            "      - \"foo\"\n"
            "    warning_message: \"\"\n"
        )
        (tmp_path / "test.yaml").write_text(yaml_content)
        reset_cache()
        checks = load(directory=tmp_path)
        assert checks == []


# ------------------------------------------------------------------ #
# Scanner tests — Security domain
# ------------------------------------------------------------------ #

class TestSecurityPatterns:
    """Test security anti-pattern detection."""

    @pytest.mark.parametrize("pattern", [
        "connection_string",
        "connectionstring",
        "access_key",
        "accesskey",
        "account_key",
        "accountkey",
        "shared_access_key",
        "client_secret",
        'password = "bad"',
        "password=\"bad\"",
        "password='bad'",
    ])
    def test_credential_patterns_detected(self, pattern):
        warnings = scan(f"Use {pattern} for auth")
        assert any("credential" in w.lower() or "managed identity" in w.lower() for w in warnings), (
            f"Pattern '{pattern}' should trigger credential warning"
        )

    def test_app_insights_connection_string_safe(self):
        warnings = scan("applicationinsights_connection_string = InstrumentationKey=...")
        credential_warnings = [w for w in warnings if "credential" in w.lower()]
        assert credential_warnings == []

    def test_admin_credentials_detected(self):
        warnings = scan("admin_enabled = true")
        assert any("admin" in w.lower() for w in warnings)

    def test_admin_password_detected(self):
        warnings = scan('admin_password = "hunter2"')
        assert len(warnings) > 0

    def test_clean_text_no_warnings(self):
        warnings = scan("Use managed identity with Key Vault RBAC.")
        assert warnings == []

    def test_empty_text_no_warnings(self):
        warnings = scan("")
        assert warnings == []


# ------------------------------------------------------------------ #
# Scanner tests — Networking domain
# ------------------------------------------------------------------ #

class TestNetworkingPatterns:
    """Test networking anti-pattern detection."""

    def test_public_network_access_detected(self):
        warnings = scan('public_network_access_enabled = true')
        assert any("public network" in w.lower() for w in warnings)

    def test_open_firewall_detected(self):
        warnings = scan("Allow 0.0.0.0/0 in the NSG")
        assert any("0.0.0.0" in w for w in warnings)

    def test_full_range_firewall_detected(self):
        warnings = scan("Set range 0.0.0.0-255.255.255.255")
        assert any("0.0.0.0" in w for w in warnings)


# ------------------------------------------------------------------ #
# Scanner tests — Authentication domain
# ------------------------------------------------------------------ #

class TestAuthenticationPatterns:
    """Test authentication anti-pattern detection."""

    def test_sql_auth_detected(self):
        warnings = scan("Use SQL authentication with username/password")
        assert any("sql authentication" in w.lower() or "entra" in w.lower() for w in warnings)

    def test_access_policies_detected(self):
        warnings = scan('access_policy = { tenant_id = "..." }')
        assert any("access policies" in w.lower() or "rbac" in w.lower() for w in warnings)


# ------------------------------------------------------------------ #
# Scanner tests — Storage domain
# ------------------------------------------------------------------ #

class TestStoragePatterns:
    """Test storage anti-pattern detection."""

    def test_account_level_keys_detected(self):
        warnings = scan("Use account-level keys for Cosmos DB access")
        assert any("account-level" in w.lower() or "managed identity" in w.lower() for w in warnings)

    def test_public_blob_access_detected(self):
        warnings = scan('allow_blob_public_access = true')
        assert any("public" in w.lower() and "blob" in w.lower() for w in warnings)


# ------------------------------------------------------------------ #
# Scanner tests — Containers domain
# ------------------------------------------------------------------ #

class TestContainerPatterns:
    """Test container anti-pattern detection."""

    def test_admin_registry_detected(self):
        warnings = scan("admin_user_enabled = true")
        assert any("registry" in w.lower() or "admin" in w.lower() for w in warnings)


# ------------------------------------------------------------------ #
# Scanner — safe pattern exemptions
# ------------------------------------------------------------------ #

class TestSafePatternExemptions:
    """Test that safe patterns properly exempt matches."""

    def test_app_insights_exempted(self):
        """App Insights connection strings should not trigger credential warning."""
        text = "appinsights_connection_string = InstrumentationKey=abc123"
        warnings = scan(text)
        credential_warnings = [w for w in warnings if "credential" in w.lower()]
        assert credential_warnings == []

    def test_safe_pattern_must_coexist(self):
        """A safe pattern only exempts if it's in the SAME text."""
        # This has the trigger but NOT the safe pattern
        warnings = scan("connection_string = Server=db;Password=oops")
        assert len(warnings) > 0

    def test_do_not_hardcode_is_safe(self):
        """Instructions telling agents not to hardcode should not trigger."""
        warnings = scan("Do not hardcode secrets in config files.")
        hardcode_warnings = [w for w in warnings if "hard-coded" in w.lower() or "hardcod" in w.lower()]
        assert hardcode_warnings == []


# ------------------------------------------------------------------ #
# Scanner tests — Encryption domain
# ------------------------------------------------------------------ #

class TestEncryptionPatterns:
    """Test encryption anti-pattern detection."""

    def test_old_tls_detected(self):
        warnings = scan('min_tls_version = "1.0"')
        assert any("tls" in w.lower() for w in warnings)

    def test_tls_11_detected(self):
        warnings = scan('minimum_tls_version = "1.1"')
        assert any("tls" in w.lower() for w in warnings)

    def test_https_disabled_detected(self):
        warnings = scan("https_only = false")
        assert any("https" in w.lower() for w in warnings)

    def test_tls_12_not_flagged(self):
        warnings = scan('min_tls_version = "1.2"')
        tls_warnings = [w for w in warnings if "tls" in w.lower()]
        assert tls_warnings == []


# ------------------------------------------------------------------ #
# Scanner tests — Monitoring domain
# ------------------------------------------------------------------ #

class TestMonitoringPatterns:
    """Test monitoring anti-pattern detection."""

    def test_zero_retention_detected(self):
        warnings = scan("retention_in_days = 0")
        assert any("retention" in w.lower() for w in warnings)


# ------------------------------------------------------------------ #
# Scanner tests — Cost domain
# ------------------------------------------------------------------ #

class TestCostPatterns:
    """Test cost anti-pattern detection."""

    def test_premium_sku_detected(self):
        warnings = scan('sku_name = "premium"')
        assert any("premium" in w.lower() or "sku" in w.lower() for w in warnings)

    def test_premium_with_production_safe(self):
        warnings = scan('sku_name = "premium" for production high availability')
        cost_warnings = [w for w in warnings if "premium" in w.lower()]
        assert cost_warnings == []


# ------------------------------------------------------------------ #
# Loader — domain coverage
# ------------------------------------------------------------------ #

class TestDomainCoverage:
    """Verify all expected domains are present."""

    def test_all_domains_loaded(self):
        checks = load()
        domains = {c.domain for c in checks}
        expected = {"security", "networking", "authentication", "storage", "containers", "encryption", "monitoring", "cost"}
        assert expected.issubset(domains), f"Missing domains: {expected - domains}"


# ------------------------------------------------------------------ #
# Scanner — deduplication
# ------------------------------------------------------------------ #

class TestScannerDeduplication:
    """Test that the scanner produces one warning per check."""

    def test_multiple_triggers_same_check_produce_one_warning(self):
        """Even if multiple search_patterns match, only one warning per check."""
        text = "connection_string and access_key and account_key"
        warnings = scan(text)
        credential_warnings = [w for w in warnings if "credential" in w.lower()]
        # Should be exactly 1, not 3
        assert len(credential_warnings) == 1
