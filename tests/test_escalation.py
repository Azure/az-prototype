"""Tests for azext_prototype.stages.escalation — blocker tracking and escalation chain.

Covers:
- EscalationEntry serialization and defaults
- EscalationTracker state management (record, attempt, resolve, save/load)
- Escalation chain (level 1→2 technical, 1→2 scope, 2→3 web, 3→4 human)
- Auto-escalation timing
- Integration with qa_router
- Edge cases
- Report formatting
- State persistence across sessions
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from azext_prototype.stages.escalation import EscalationEntry, EscalationTracker


# ======================================================================
# Helpers
# ======================================================================

def _make_entry(**kwargs) -> EscalationEntry:
    defaults = {
        "task_description": "Build Stage 3: Data Layer",
        "blocker": "Cosmos DB requires premium tier",
        "source_agent": "terraform-agent",
        "source_stage": "build",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_escalated_at": datetime.now(timezone.utc).isoformat(),
    }
    defaults.update(kwargs)
    return EscalationEntry(**defaults)


def _make_registry(architect_response=None, pm_response=None):
    from azext_prototype.agents.base import AgentCapability

    architect = MagicMock()
    architect.name = "cloud-architect"
    if architect_response:
        architect.execute.return_value = architect_response
    else:
        architect.execute.return_value = MagicMock(content="Use Standard tier instead")

    pm = MagicMock()
    pm.name = "project-manager"
    if pm_response:
        pm.execute.return_value = pm_response
    else:
        pm.execute.return_value = MagicMock(content="Descope this item")

    registry = MagicMock()
    def find_by_cap(cap):
        if cap == AgentCapability.ARCHITECT:
            return [architect]
        if cap == AgentCapability.BACKLOG_GENERATION:
            return [pm]
        return []
    registry.find_by_capability.side_effect = find_by_cap

    return registry, architect, pm


def _make_context():
    from azext_prototype.agents.base import AgentContext
    return AgentContext(
        project_config={"project": {"name": "test"}},
        project_dir="/tmp/test",
        ai_provider=MagicMock(),
    )


# ======================================================================
# EscalationEntry tests
# ======================================================================

class TestEscalationEntry:

    def test_default_values(self):
        entry = EscalationEntry(task_description="task", blocker="blocked")
        assert entry.escalation_level == 1
        assert entry.resolved is False
        assert entry.resolution == ""
        assert entry.attempted_solutions == []

    def test_to_dict_roundtrip(self):
        entry = _make_entry(attempted_solutions=["Try A", "Try B"])
        d = entry.to_dict()
        restored = EscalationEntry.from_dict(d)

        assert restored.task_description == entry.task_description
        assert restored.blocker == entry.blocker
        assert restored.attempted_solutions == ["Try A", "Try B"]
        assert restored.escalation_level == entry.escalation_level
        assert restored.source_agent == entry.source_agent

    def test_from_dict_missing_keys(self):
        entry = EscalationEntry.from_dict({})
        assert entry.task_description == ""
        assert entry.blocker == ""
        assert entry.escalation_level == 1


# ======================================================================
# EscalationTracker state management tests
# ======================================================================

class TestEscalationTrackerState:

    def test_record_blocker(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))

        entry = tracker.record_blocker(
            "Deploy Redis", "Premium tier required",
            "terraform-agent", "deploy",
        )

        assert entry.task_description == "Deploy Redis"
        assert entry.blocker == "Premium tier required"
        assert entry.escalation_level == 1
        assert entry.created_at != ""
        assert len(tracker.get_active_blockers()) == 1

    def test_record_attempted_solution(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        entry = tracker.record_blocker("task", "blocked", "agent", "stage")

        tracker.record_attempted_solution(entry, "Tried standard tier")
        tracker.record_attempted_solution(entry, "Tried basic tier")

        assert len(entry.attempted_solutions) == 2
        assert "Tried standard tier" in entry.attempted_solutions

    def test_resolve_blocker(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        entry = tracker.record_blocker("task", "blocked", "agent", "stage")

        tracker.resolve(entry, "Used standard tier instead")

        assert entry.resolved is True
        assert entry.resolution == "Used standard tier instead"
        assert len(tracker.get_active_blockers()) == 0

    def test_get_active_blockers_filters_resolved(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        e1 = tracker.record_blocker("task1", "blocked1", "a1", "s1")
        e2 = tracker.record_blocker("task2", "blocked2", "a2", "s2")
        tracker.resolve(e1, "fixed")

        active = tracker.get_active_blockers()
        assert len(active) == 1
        assert active[0].task_description == "task2"

    def test_save_load_roundtrip(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        tracker.record_blocker("task1", "blocked1", "agent1", "stage1")
        tracker.record_blocker("task2", "blocked2", "agent2", "stage2")

        tracker2 = EscalationTracker(str(tmp_project))
        tracker2.load()

        assert len(tracker2.get_active_blockers()) == 2
        assert tracker2.get_active_blockers()[0].task_description == "task1"

    def test_save_creates_yaml(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        tracker.record_blocker("task", "blocked", "agent", "stage")

        yaml_path = Path(str(tmp_project)) / ".prototype" / "state" / "escalation.yaml"
        assert yaml_path.exists()

        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        assert len(data["entries"]) == 1

    def test_exists_property(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        assert not tracker.exists

        tracker.record_blocker("task", "blocked", "agent", "stage")
        assert tracker.exists

    def test_empty_load(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        tracker.load()  # No file exists
        assert tracker.get_active_blockers() == []

    def test_multiple_records_and_resolves(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        e1 = tracker.record_blocker("t1", "b1", "a", "s")
        e2 = tracker.record_blocker("t2", "b2", "a", "s")
        e3 = tracker.record_blocker("t3", "b3", "a", "s")

        tracker.resolve(e1, "fixed")
        tracker.resolve(e3, "workaround")

        assert len(tracker.get_active_blockers()) == 1
        assert tracker.get_active_blockers()[0].task_description == "t2"


# ======================================================================
# Escalation chain tests
# ======================================================================

class TestEscalationChain:

    def test_level_1_to_2_technical(self, tmp_project):
        """Technical blocker escalates to architect."""
        tracker = EscalationTracker(str(tmp_project))
        entry = tracker.record_blocker(
            "Deploy Cosmos DB", "Premium tier required for multi-region",
            "terraform-agent", "build",
        )

        registry, architect, pm = _make_registry()
        ctx = _make_context()
        printed = []

        result = tracker.escalate(entry, registry, ctx, printed.append)

        assert result["escalated"] is True
        assert result["level"] == 2
        assert entry.escalation_level == 2
        architect.execute.assert_called_once()
        pm.execute.assert_not_called()

    def test_level_1_to_2_scope(self, tmp_project):
        """Scope blocker escalates to project-manager."""
        tracker = EscalationTracker(str(tmp_project))
        entry = tracker.record_blocker(
            "Backlog items", "Scope of feature is unclear",
            "biz-analyst", "design",
        )

        registry, architect, pm = _make_registry()
        ctx = _make_context()
        printed = []

        result = tracker.escalate(entry, registry, ctx, printed.append)

        assert result["escalated"] is True
        assert result["level"] == 2
        pm.execute.assert_called_once()
        architect.execute.assert_not_called()

    @patch("azext_prototype.stages.escalation.EscalationTracker._escalate_to_web_search")
    def test_level_2_to_3_web_search(self, mock_web, tmp_project):
        """Level 2→3 triggers web search."""
        mock_web.return_value = "Found: Azure docs suggest..."

        tracker = EscalationTracker(str(tmp_project))
        entry = tracker.record_blocker("task", "blocked", "agent", "stage")
        entry.escalation_level = 2  # Already at level 2

        registry, _, _ = _make_registry()
        ctx = _make_context()
        printed = []

        result = tracker.escalate(entry, registry, ctx, printed.append)

        assert result["escalated"] is True
        assert result["level"] == 3
        mock_web.assert_called_once()

    def test_level_3_to_4_human(self, tmp_project):
        """Level 3→4 flags for human intervention."""
        tracker = EscalationTracker(str(tmp_project))
        entry = tracker.record_blocker("task", "blocked", "agent", "stage")
        entry.escalation_level = 3  # Already at level 3

        registry, _, _ = _make_registry()
        ctx = _make_context()
        printed = []

        result = tracker.escalate(entry, registry, ctx, printed.append)

        assert result["escalated"] is True
        assert result["level"] == 4
        assert any("HUMAN INTERVENTION" in p for p in printed)

    def test_already_at_level_4_no_escalation(self, tmp_project):
        """Cannot escalate past level 4."""
        tracker = EscalationTracker(str(tmp_project))
        entry = tracker.record_blocker("task", "blocked", "agent", "stage")
        entry.escalation_level = 4

        registry, _, _ = _make_registry()
        ctx = _make_context()
        printed = []

        result = tracker.escalate(entry, registry, ctx, printed.append)

        assert result["escalated"] is False
        assert result["level"] == 4

    def test_no_agent_available_for_escalation(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        entry = tracker.record_blocker("task", "blocked", "agent", "stage")

        registry = MagicMock()
        registry.find_by_capability.return_value = []
        ctx = _make_context()
        printed = []

        result = tracker.escalate(entry, registry, ctx, printed.append)

        assert result["level"] == 2
        assert "No cloud-architect available" in result["content"]

    def test_agent_escalation_failure(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        entry = tracker.record_blocker("task", "blocked", "agent", "stage")

        registry, architect, _ = _make_registry()
        architect.execute.side_effect = RuntimeError("AI crashed")
        ctx = _make_context()
        printed = []

        result = tracker.escalate(entry, registry, ctx, printed.append)

        assert result["level"] == 2
        assert "failed" in result["content"].lower()

    def test_web_search_failure_graceful(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        entry = tracker.record_blocker("task", "blocked", "agent", "stage")
        entry.escalation_level = 2

        printed = []

        with patch("azext_prototype.stages.escalation.EscalationTracker._escalate_to_web_search") as mock_ws:
            mock_ws.return_value = "Web search failed: connection error"

            registry, _, _ = _make_registry()
            ctx = _make_context()
            result = tracker.escalate(entry, registry, ctx, printed.append)

        assert result["level"] == 3
        assert "failed" in result["content"].lower()


# ======================================================================
# Auto-escalation tests
# ======================================================================

class TestAutoEscalation:

    def test_timeout_triggers_escalation(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        entry = tracker.record_blocker("task", "blocked", "agent", "stage")

        # Set last_escalated_at to 5 minutes ago
        old_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        entry.last_escalated_at = old_time.isoformat()

        assert tracker.should_auto_escalate(entry, timeout_seconds=120)

    def test_not_yet_timed_out(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        entry = tracker.record_blocker("task", "blocked", "agent", "stage")

        # Just created, so not timed out
        assert not tracker.should_auto_escalate(entry, timeout_seconds=120)

    def test_resolved_stops_escalation(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        entry = tracker.record_blocker("task", "blocked", "agent", "stage")
        tracker.resolve(entry, "fixed")

        old_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        entry.last_escalated_at = old_time.isoformat()

        assert not tracker.should_auto_escalate(entry)

    def test_level_4_stops_escalation(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        entry = tracker.record_blocker("task", "blocked", "agent", "stage")
        entry.escalation_level = 4

        old_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        entry.last_escalated_at = old_time.isoformat()

        assert not tracker.should_auto_escalate(entry)

    def test_invalid_timestamp_returns_false(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        entry = tracker.record_blocker("task", "blocked", "agent", "stage")
        entry.last_escalated_at = "not-a-timestamp"

        assert not tracker.should_auto_escalate(entry)


# ======================================================================
# Integration with qa_router
# ======================================================================

class TestQARouterIntegration:

    def test_qa_router_records_blocker_on_undiagnosed(self, tmp_project):
        from azext_prototype.stages.qa_router import route_error_to_qa
        from azext_prototype.ai.provider import AIResponse

        tracker = EscalationTracker(str(tmp_project))

        # QA returns empty — undiagnosed
        qa = MagicMock()
        qa.execute.return_value = AIResponse(content="", model="gpt-4o", usage={})

        ctx = _make_context()

        result = route_error_to_qa(
            "Deployment failed", "Deploy Stage 1",
            qa, ctx, None, lambda m: None,
            escalation_tracker=tracker,
            source_agent="terraform-agent",
            source_stage="deploy",
        )

        assert result["diagnosed"] is False
        assert len(tracker.get_active_blockers()) == 1
        blocker = tracker.get_active_blockers()[0]
        assert blocker.source_agent == "terraform-agent"
        assert blocker.source_stage == "deploy"

    def test_qa_router_no_tracker_no_error(self, tmp_project):
        from azext_prototype.stages.qa_router import route_error_to_qa
        from azext_prototype.ai.provider import AIResponse

        qa = MagicMock()
        qa.execute.return_value = AIResponse(content="", model="gpt-4o", usage={})

        ctx = _make_context()

        # No escalation tracker — should not raise
        result = route_error_to_qa(
            "error", "context",
            qa, ctx, None, lambda m: None,
            escalation_tracker=None,
        )

        assert result["diagnosed"] is False

    @patch("azext_prototype.stages.qa_router._submit_knowledge")
    def test_qa_router_diagnosed_no_blocker(self, mock_knowledge, tmp_project):
        from azext_prototype.stages.qa_router import route_error_to_qa
        from azext_prototype.ai.provider import AIResponse

        tracker = EscalationTracker(str(tmp_project))

        qa = MagicMock()
        qa.execute.return_value = AIResponse(content="Root cause: X", model="gpt-4o", usage={})

        ctx = _make_context()

        result = route_error_to_qa(
            "error", "context",
            qa, ctx, None, lambda m: None,
            escalation_tracker=tracker,
        )

        assert result["diagnosed"] is True
        # No blocker should be recorded when QA diagnoses successfully
        assert len(tracker.get_active_blockers()) == 0

    def test_build_session_has_escalation_tracker(self, tmp_project):
        from azext_prototype.stages.build_session import BuildSession
        from azext_prototype.agents.base import AgentContext

        ctx = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )

        registry = MagicMock()
        registry.find_by_capability.return_value = []

        with patch("azext_prototype.stages.build_session.ProjectConfig") as mock_config:
            mock_config.return_value.load.return_value = None
            mock_config.return_value.get.side_effect = lambda k, d=None: {
                "project.iac_tool": "terraform",
                "project.name": "test",
            }.get(k, d)
            mock_config.return_value.to_dict.return_value = {"naming": {"strategy": "simple"}, "project": {"name": "test"}}
            session = BuildSession(ctx, registry)

        assert hasattr(session, "_escalation_tracker")
        assert isinstance(session._escalation_tracker, EscalationTracker)

    def test_deploy_session_has_escalation_tracker(self, tmp_project):
        from azext_prototype.stages.deploy_session import DeploySession
        from azext_prototype.stages.deploy_state import DeployState
        from azext_prototype.agents.base import AgentContext

        ctx = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )

        registry = MagicMock()
        registry.find_by_capability.return_value = []

        with patch("azext_prototype.stages.deploy_session.ProjectConfig") as mock_config:
            mock_config.return_value.load.return_value = None
            mock_config.return_value.get.side_effect = lambda k, d=None: {
                "project.iac_tool": "terraform",
            }.get(k, d)
            session = DeploySession(ctx, registry, deploy_state=DeployState(str(tmp_project)))

        assert hasattr(session, "_escalation_tracker")
        assert isinstance(session._escalation_tracker, EscalationTracker)

    def test_backlog_session_has_escalation_tracker(self, tmp_project):
        from azext_prototype.stages.backlog_session import BacklogSession
        from azext_prototype.stages.backlog_state import BacklogState
        from azext_prototype.agents.base import AgentContext

        ctx = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )

        registry = MagicMock()
        registry.find_by_capability.return_value = []

        session = BacklogSession(ctx, registry, backlog_state=BacklogState(str(tmp_project)))

        assert hasattr(session, "_escalation_tracker")
        assert isinstance(session._escalation_tracker, EscalationTracker)


# ======================================================================
# Report formatting tests
# ======================================================================

class TestReportFormatting:

    def test_empty_report(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        report = tracker.format_escalation_report()
        assert "No blockers recorded" in report

    def test_report_with_active_and_resolved(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        e1 = tracker.record_blocker("Deploy Redis", "Premium needed", "tf", "build")
        e2 = tracker.record_blocker("Deploy Cosmos", "Multi-region", "tf", "build")
        tracker.resolve(e2, "Used single region")

        report = tracker.format_escalation_report()

        assert "Active Blockers (1)" in report
        assert "Deploy Redis" in report
        assert "Resolved (1)" in report
        assert "Used single region" in report


# ======================================================================
# State persistence across sessions
# ======================================================================

class TestStatePersistence:

    def test_state_survives_session_restart(self, tmp_project):
        tracker1 = EscalationTracker(str(tmp_project))
        tracker1.record_blocker("task1", "b1", "a1", "s1")
        e2 = tracker1.record_blocker("task2", "b2", "a2", "s2")
        tracker1.record_attempted_solution(e2, "Tried A")
        tracker1.resolve(e2, "Used workaround B")

        # Simulate session restart
        tracker2 = EscalationTracker(str(tmp_project))
        tracker2.load()

        assert len(tracker2.get_active_blockers()) == 1
        assert tracker2.get_active_blockers()[0].task_description == "task1"

        # Check resolved entry
        all_entries = tracker2._entries
        resolved = [e for e in all_entries if e.resolved]
        assert len(resolved) == 1
        assert resolved[0].resolution == "Used workaround B"
        assert resolved[0].attempted_solutions == ["Tried A"]

    def test_escalation_level_persists(self, tmp_project):
        tracker1 = EscalationTracker(str(tmp_project))
        entry = tracker1.record_blocker("task", "blocked", "agent", "stage")

        registry, _, _ = _make_registry()
        ctx = _make_context()
        tracker1.escalate(entry, registry, ctx, lambda m: None)

        # Simulate restart
        tracker2 = EscalationTracker(str(tmp_project))
        tracker2.load()

        assert tracker2.get_active_blockers()[0].escalation_level == 2
