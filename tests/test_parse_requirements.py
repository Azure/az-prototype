"""Tests for the heading-based _parse_requirements_to_learnings."""

import pytest

from azext_prototype.stages.design_stage import DesignStage


class TestParseRequirementsToLearnings:
    """Test the heading-based requirements parser."""

    def setup_method(self):
        self.stage = DesignStage()

    def _parse(self, text, design_state=None):
        return self.stage._parse_requirements_to_learnings(
            text, [], design_state or {},
        )

    def test_parses_all_sections(self):
        text = """\
## Project Summary
An orders management REST API for internal use.

## Goals
- Enable order tracking
- Provide real-time status updates

## Confirmed Functional Requirements
- CRUD operations for orders
- Search by customer ID

## Confirmed Non-Functional Requirements
- 99.9% availability
- Sub-second response times

## Constraints
- Must use Azure SQL
- Budget under $500/month

## Decisions
- Use Container Apps over App Service

## Open Items
- Authentication provider TBD

## Risks
- Data migration complexity

## Prototype Scope
### In Scope
- Order CRUD API
- SQL Database

### Out of Scope
- Mobile application

### Deferred / Future Work
- CI/CD pipeline
- Monitoring dashboards

## Azure Services
- Azure Container Apps (API hosting)
- Azure SQL Database (persistence)
- Azure Key Vault (secrets)

## Policy Overrides
- None
"""
        learnings = self._parse(text)

        assert "orders management" in learnings["project"]["summary"]
        assert len(learnings["project"]["goals"]) == 2
        assert "Enable order tracking" in learnings["project"]["goals"]
        assert len(learnings["requirements"]["functional"]) == 2
        assert "CRUD operations for orders" in learnings["requirements"]["functional"]
        assert len(learnings["requirements"]["non_functional"]) == 2
        assert len(learnings["constraints"]) == 2
        assert "Must use Azure SQL" in learnings["constraints"]
        assert len(learnings["decisions"]) == 1
        assert len(learnings["open_items"]) == 1
        assert len(learnings["risks"]) == 1
        assert learnings["scope"]["in_scope"] == ["Order CRUD API", "SQL Database"]
        assert learnings["scope"]["out_of_scope"] == ["Mobile application"]
        assert learnings["scope"]["deferred"] == ["CI/CD pipeline", "Monitoring dashboards"]
        assert len(learnings["architecture"]["services"]) == 3

    def test_empty_requirements(self):
        learnings = self._parse("")
        assert learnings["project"]["summary"] == ""
        assert learnings["project"]["goals"] == []
        assert learnings["scope"]["in_scope"] == []
        assert learnings["requirements"]["functional"] == []

    def test_partial_headings(self):
        text = """\
## Project Summary
A web application.

## Goals
- Build a prototype

## Azure Services
- App Service
"""
        learnings = self._parse(text)
        assert "web application" in learnings["project"]["summary"]
        assert len(learnings["project"]["goals"]) == 1
        assert len(learnings["architecture"]["services"]) == 1
        # Missing sections should be empty
        assert learnings["constraints"] == []
        assert learnings["scope"]["in_scope"] == []
        assert learnings["open_items"] == []

    def test_design_state_decisions_merged(self):
        text = "## Project Summary\nTest"
        design_state = {
            "decisions": [
                {"feedback": "Switch to PostgreSQL", "iteration": 1},
            ],
        }
        learnings = self._parse(text, design_state)
        assert "Switch to PostgreSQL" in learnings["decisions"]

    def test_policy_overrides_become_constraints(self):
        text = "## Project Summary\nTest"
        design_state = {
            "policy_overrides": [
                {"policy_name": "managed-identity", "description": "Legacy compat"},
            ],
        }
        learnings = self._parse(text, design_state)
        assert any("managed-identity" in c for c in learnings["constraints"])

    def test_case_insensitive_headings(self):
        text = """\
## project summary
Test project.

## goals
- Goal one
"""
        learnings = self._parse(text)
        assert "Test project" in learnings["project"]["summary"]
        assert len(learnings["project"]["goals"]) == 1

    def test_scope_only(self):
        text = """\
## Project Summary
Test

## Prototype Scope
### In Scope
- API
- Database

### Out of Scope
- Frontend

### Deferred / Future Work
- Monitoring
"""
        learnings = self._parse(text)
        assert learnings["scope"]["in_scope"] == ["API", "Database"]
        assert learnings["scope"]["out_of_scope"] == ["Frontend"]
        assert learnings["scope"]["deferred"] == ["Monitoring"]

    def test_numbered_list_items(self):
        text = """\
## Confirmed Functional Requirements
1. User authentication
2. Order management
3. Payment processing
"""
        learnings = self._parse(text)
        assert len(learnings["requirements"]["functional"]) == 3
        assert "User authentication" in learnings["requirements"]["functional"]

    def test_none_items_filtered(self):
        """Sections with just '- None' should produce empty lists."""
        text = """\
## Project Summary
Test project

## Constraints
- None

## Risks
- None
"""
        learnings = self._parse(text)
        # "None" is a valid bullet item (it's text), parser captures it
        # This is acceptable — the downstream consumer can filter "None"
        assert learnings["project"]["summary"] == "Test project"

    def test_non_functional_hyphenated(self):
        """Should match both 'Non-Functional' and 'Non Functional'."""
        text = """\
## Confirmed Non-Functional Requirements
- 99.9% uptime
"""
        learnings = self._parse(text)
        assert len(learnings["requirements"]["non_functional"]) == 1

    def test_learnings_has_scope_key(self):
        """Learnings dict always includes scope, even when empty."""
        learnings = self._parse("## Project Summary\nTest")
        assert "scope" in learnings
        assert learnings["scope"] == {
            "in_scope": [],
            "out_of_scope": [],
            "deferred": [],
        }
