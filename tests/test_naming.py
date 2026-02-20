"""Tests for azext_prototype.naming — naming strategies and constraints."""

import pytest
from knack.util import CLIError

from azext_prototype.naming import (
    ALZ_ZONE_IDS,
    CAF_ABBREVIATIONS,
    REGION_SHORT_CODES,
    CustomStrategy,
    EnterpriseStrategy,
    MicrosoftALZStrategy,
    MicrosoftCAFStrategy,
    NamingStrategy,
    SimpleStrategy,
    create_naming_strategy,
    get_available_strategies,
    get_zone_ids,
)


class TestMicrosoftALZStrategy:
    """Test the default Azure Landing Zone naming strategy."""

    def test_resource_group(self, sample_config):
        strategy = MicrosoftALZStrategy(sample_config)
        name = strategy.resolve("resource_group", "api")
        assert name.startswith("zd-rg-")
        assert "api" in name
        assert "dev" in name

    def test_storage_account_no_hyphens(self, sample_config):
        strategy = MicrosoftALZStrategy(sample_config)
        name = strategy.resolve("storage_account", "data")
        assert "-" not in name
        assert name == name.lower()
        assert len(name) <= 24

    def test_key_vault_max_length(self, sample_config):
        strategy = MicrosoftALZStrategy(sample_config)
        name = strategy.resolve("key_vault", "secrets")
        assert len(name) <= 24

    def test_container_registry_no_hyphens(self, sample_config):
        strategy = MicrosoftALZStrategy(sample_config)
        name = strategy.resolve("container_registry", "apps")
        assert "-" not in name
        assert len(name) <= 50

    def test_zone_id_in_name(self, sample_config):
        strategy = MicrosoftALZStrategy(sample_config)
        name = strategy.resolve("resource_group", "api")
        assert "zd" in name

    def test_different_zone_id(self, sample_config):
        sample_config["naming"]["zone_id"] = "zp"
        strategy = MicrosoftALZStrategy(sample_config)
        name = strategy.resolve("resource_group", "api")
        assert "zp" in name

    def test_prompt_instructions_contain_zone_table(self, sample_config):
        strategy = MicrosoftALZStrategy(sample_config)
        instructions = strategy.to_prompt_instructions()
        assert "Azure Landing Zone" in instructions
        assert "zd" in instructions
        assert "zp" in instructions
        for zone_id in ALZ_ZONE_IDS:
            assert zone_id in instructions


class TestMicrosoftCAFStrategy:
    """Test the Cloud Adoption Framework naming strategy."""

    def test_includes_org(self, sample_config):
        sample_config["naming"]["strategy"] = "microsoft-caf"
        strategy = MicrosoftCAFStrategy(sample_config)
        name = strategy.resolve("resource_group", "api")
        assert "contoso" in name

    def test_includes_instance(self, sample_config):
        sample_config["naming"]["strategy"] = "microsoft-caf"
        sample_config["naming"]["instance"] = "002"
        strategy = MicrosoftCAFStrategy(sample_config)
        name = strategy.resolve("resource_group", "api")
        assert "002" in name


class TestSimpleStrategy:
    """Test the simple naming strategy."""

    def test_basic_format(self, sample_config):
        strategy = SimpleStrategy(sample_config)
        name = strategy.resolve("resource_group", "api")
        assert "contoso" in name
        assert "rg" in name
        assert "dev" in name


class TestEnterpriseStrategy:
    """Test the enterprise naming strategy."""

    def test_includes_business_unit(self, sample_config):
        sample_config["naming"]["business_unit"] = "finops"
        strategy = EnterpriseStrategy(sample_config)
        name = strategy.resolve("resource_group", "api")
        assert "finops" in name


class TestCustomStrategy:
    """Test the custom pattern naming strategy."""

    def test_custom_pattern(self, sample_config):
        sample_config["naming"]["pattern"] = "{org}-{type}-{env}"
        strategy = CustomStrategy(sample_config)
        name = strategy.resolve("resource_group", "api")
        assert "contoso" in name
        assert "rg" in name
        assert "dev" in name


class TestAzureConstraints:
    """Test Azure resource naming constraints are enforced."""

    def test_storage_account_lowercase_no_hyphens(self, sample_config):
        strategy = create_naming_strategy(sample_config)
        name = strategy.resolve("storage_account", "MyService")
        assert name == name.lower()
        assert "-" not in name

    def test_storage_account_max_24(self, sample_config):
        sample_config["naming"]["org"] = "verylongorganizationname"
        strategy = create_naming_strategy(sample_config)
        name = strategy.resolve("storage_account", "verylongservicename")
        assert len(name) <= 24

    def test_key_vault_max_24(self, sample_config):
        sample_config["naming"]["org"] = "verylongorg"
        strategy = create_naming_strategy(sample_config)
        name = strategy.resolve("key_vault", "verylongservicename")
        assert len(name) <= 24

    def test_container_registry_max_50(self, sample_config):
        strategy = create_naming_strategy(sample_config)
        name = strategy.resolve("container_registry", "api")
        assert len(name) <= 50
        assert "-" not in name


class TestCreateNamingStrategy:
    """Test the factory function."""

    def test_all_strategies_instantiate(self, sample_config):
        for strategy_name in get_available_strategies():
            sample_config["naming"]["strategy"] = strategy_name
            strategy = create_naming_strategy(sample_config)
            assert isinstance(strategy, NamingStrategy)

    def test_unknown_strategy_raises(self, sample_config):
        sample_config["naming"]["strategy"] = "nonexistent"
        with pytest.raises(CLIError, match="Unknown naming strategy"):
            create_naming_strategy(sample_config)

    def test_default_strategy_is_alz(self, sample_config):
        del sample_config["naming"]["strategy"]
        strategy = create_naming_strategy(sample_config)
        assert isinstance(strategy, MicrosoftALZStrategy)


class TestZoneIds:
    """Test zone ID helpers."""

    def test_get_zone_ids(self):
        zones = get_zone_ids()
        assert "zd" in zones
        assert "zp" in zones
        assert len(zones) == 7

    def test_all_zone_ids_have_descriptions(self):
        for zone_id, description in ALZ_ZONE_IDS.items():
            assert len(zone_id) == 2
            assert len(description) > 0


class TestRegionShortCodes:
    """Test region mapping completeness."""

    def test_common_regions_mapped(self):
        assert "eastus" in REGION_SHORT_CODES
        assert "westus2" in REGION_SHORT_CODES
        assert "westeurope" in REGION_SHORT_CODES
        assert "northeurope" in REGION_SHORT_CODES

    def test_short_codes_are_short(self):
        for region, code in REGION_SHORT_CODES.items():
            assert len(code) <= 5


class TestCAFAbbreviations:
    """Test CAF abbreviation completeness."""

    def test_core_resource_types(self):
        expected_types = [
            "resource_group", "storage_account", "app_service",
            "key_vault", "cosmos_db", "sql_server",
            "container_registry", "function_app",
        ]
        for rtype in expected_types:
            assert rtype in CAF_ABBREVIATIONS

    def test_abbreviations_are_short(self):
        for rtype, abbrev in CAF_ABBREVIATIONS.items():
            assert len(abbrev) <= 6
