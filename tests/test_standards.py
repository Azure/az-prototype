"""Tests for azext_prototype.standards — curated design principles and reference patterns."""

import pytest
from pathlib import Path

from azext_prototype.governance import standards
from azext_prototype.governance.standards import Standard, StandardPrinciple, load, format_for_prompt, reset_cache


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_cache()
    yield
    reset_cache()


# ------------------------------------------------------------------ #
# Loader tests
# ------------------------------------------------------------------ #

class TestStandardsLoader:
    """Test YAML loading from the standards directory."""

    def test_load_returns_non_empty(self):
        loaded = load()
        assert len(loaded) > 0

    def test_load_returns_standard_objects(self):
        loaded = load()
        assert all(isinstance(s, Standard) for s in loaded)

    def test_all_standards_have_domain(self):
        for s in load():
            assert s.domain, f"Standard missing domain: {s}"

    def test_all_standards_have_principles(self):
        for s in load():
            assert len(s.principles) > 0, f"Standard has no principles: {s.domain}"

    def test_all_principles_have_id(self):
        for s in load():
            for p in s.principles:
                assert p.id, f"Principle missing id in {s.domain}"

    def test_all_principles_have_name(self):
        for s in load():
            for p in s.principles:
                assert p.name, f"Principle missing name in {s.domain}: {p.id}"

    def test_all_principles_have_description(self):
        for s in load():
            for p in s.principles:
                assert p.description, f"Principle missing description: {p.id}"

    def test_design_principles_loaded(self):
        loaded = load()
        domains = {s.domain for s in loaded}
        assert "Design Principles" in domains

    def test_coding_standards_loaded(self):
        loaded = load()
        domains = {s.domain for s in loaded}
        assert "Coding Standards" in domains

    def test_load_is_cached(self):
        first = load()
        second = load()
        assert first is second

    def test_reset_clears_cache(self):
        first = load()
        reset_cache()
        second = load()
        assert first is not second

    def test_load_from_missing_directory(self):
        loaded = load(directory=Path("/nonexistent"))
        assert loaded == []

    def test_load_from_empty_directory(self, tmp_path):
        loaded = load(directory=tmp_path)
        assert loaded == []

    def test_load_from_custom_directory(self, tmp_path):
        yaml_content = (
            "domain: Custom\n"
            "category: test\n"
            "principles:\n"
            "  - id: TST-001\n"
            "    name: Test Principle\n"
            "    description: A test principle\n"
        )
        (tmp_path / "custom.yaml").write_text(yaml_content)
        reset_cache()
        loaded = load(directory=tmp_path)
        assert len(loaded) == 1
        assert loaded[0].domain == "Custom"
        assert loaded[0].principles[0].id == "TST-001"


# ------------------------------------------------------------------ #
# Prompt formatting tests
# ------------------------------------------------------------------ #

class TestFormatForPrompt:
    """Test standards prompt formatting."""

    def test_format_returns_non_empty(self):
        text = format_for_prompt()
        assert len(text) > 0

    def test_format_includes_heading(self):
        text = format_for_prompt()
        assert "Design Standards" in text

    def test_format_includes_principle_ids(self):
        text = format_for_prompt()
        assert "DES-001" in text
        assert "CODE-001" in text

    def test_format_includes_principle_names(self):
        text = format_for_prompt()
        assert "Single Responsibility" in text
        assert "DRY" in text

    def test_format_by_category(self):
        text = format_for_prompt(category="principles")
        assert "Design Standards" in text

    def test_format_by_unknown_category_returns_empty(self):
        text = format_for_prompt(category="nonexistent")
        assert text == ""

    def test_format_includes_examples(self):
        text = format_for_prompt()
        assert "Terraform" in text or "Application" in text


# ------------------------------------------------------------------ #
# Specific principles content
# ------------------------------------------------------------------ #

class TestPrincipleContent:
    """Verify specific principle content is correct."""

    def test_solid_principles_present(self):
        loaded = load()
        all_ids = {p.id for s in loaded for p in s.principles}
        assert "DES-001" in all_ids  # Single Responsibility
        assert "DES-002" in all_ids  # DRY
        assert "DES-003" in all_ids  # Open/Closed

    def test_coding_standards_present(self):
        loaded = load()
        all_ids = {p.id for s in loaded for p in s.principles}
        assert "CODE-001" in all_ids  # Meaningful Names
        assert "CODE-004" in all_ids  # Consistent Module Structure

    def test_applies_to_includes_agents(self):
        loaded = load()
        all_applies_to = set()
        for s in loaded:
            for p in s.principles:
                all_applies_to.update(p.applies_to)
        assert "terraform-agent" in all_applies_to
        assert "bicep-agent" in all_applies_to
        assert "app-developer" in all_applies_to

    def test_terraform_standards_loaded(self):
        loaded = load()
        domains = {s.domain for s in loaded}
        assert "Terraform Module Structure" in domains

    def test_bicep_standards_loaded(self):
        loaded = load()
        domains = {s.domain for s in loaded}
        assert "Bicep Module Structure" in domains

    def test_python_standards_loaded(self):
        loaded = load()
        domains = {s.domain for s in loaded}
        assert "Python Application Standards" in domains

    def test_terraform_has_file_layout(self):
        loaded = load()
        tf_standards = [s for s in loaded if s.domain == "Terraform Module Structure"]
        assert len(tf_standards) == 1
        ids = {p.id for p in tf_standards[0].principles}
        assert "TF-001" in ids
        assert "TF-005" in ids

    def test_bicep_has_module_composition(self):
        loaded = load()
        bcp_standards = [s for s in loaded if s.domain == "Bicep Module Structure"]
        assert len(bcp_standards) == 1
        ids = {p.id for p in bcp_standards[0].principles}
        assert "BCP-001" in ids
        assert "BCP-003" in ids

    def test_python_has_default_credential(self):
        loaded = load()
        py_standards = [s for s in loaded if s.domain == "Python Application Standards"]
        assert len(py_standards) == 1
        ids = {p.id for p in py_standards[0].principles}
        assert "PY-001" in ids

    def test_format_terraform_category(self):
        text = format_for_prompt(category="terraform")
        assert "TF-001" in text or "Terraform" in text

    def test_format_bicep_category(self):
        text = format_for_prompt(category="bicep")
        assert "BCP-001" in text or "Bicep" in text

    def test_format_application_category(self):
        text = format_for_prompt(category="application")
        assert "PY-001" in text or "Python" in text
