from __future__ import annotations

"""Tests for EscalationTracker — 4-level escalation chain.

Tier 2: Conditional branches with multiple paths.

Covers:
- EscalationEntry dataclass: to_dict / from_dict round-trip
- record_blocker() creates L1 entry, persists
- record_attempted_solution() appends and saves
- resolve() marks resolved, saves
- get_active_blockers() filters resolved
- Escalation chain:
  - L1 (documented) -> L2 (agent: architect vs PM)
  - L2 scope keywords -> project-manager, else -> cloud-architect
  - L2 with no agent available -> fallback message
  - L2 agent execution failure -> error message
  - L3 web search -> success / failure / import error
  - L4 human flag
  - Already at L4 -> no escalation
- should_auto_escalate():
  - resolved entry -> False
  - L4 entry -> False
  - within timeout -> False
  - exceeded timeout -> True
  - bad timestamp -> False
- format_escalation_report() formatting
- State persistence: save / load round-trip
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from azext_prototype.stages.escalation import EscalationEntry, EscalationTracker

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def tracker(tmp_path):
    """EscalationTracker with a temp project directory."""
    project_dir = str(tmp_path / "test-project")
    (tmp_path / "test-project" / ".prototype" / "state").mkdir(parents=True)
    return EscalationTracker(project_dir)


@pytest.fixture
def sample_entry():
    """A sample escalation entry at L1."""
    now = datetime.now(timezone.utc).isoformat()
    return EscalationEntry(
        task_description="Deploy container app",
        blocker="Container registry not accessible",
        attempted_solutions=["Checked ACR network rules"],
        escalation_level=1,
        source_agent="terraform-agent",
        source_stage="build",
        created_at=now,
        last_escalated_at=now,
    )


# ------------------------------------------------------------------
# EscalationEntry dataclass
# ------------------------------------------------------------------


class TestEscalationEntry:
    def test_to_dict_round_trip(self, sample_entry):
        d = sample_entry.to_dict()
        restored = EscalationEntry.from_dict(d)
        assert restored.task_description == sample_entry.task_description
        assert restored.blocker == sample_entry.blocker
        assert restored.attempted_solutions == sample_entry.attempted_solutions
        assert restored.escalation_level == sample_entry.escalation_level
        assert restored.source_agent == sample_entry.source_agent
        assert restored.resolved == sample_entry.resolved

    def test_from_dict_missing_fields_uses_defaults(self):
        entry = EscalationEntry.from_dict({"task_description": "Deploy", "blocker": "Blocked"})
        assert entry.escalation_level == 1
        assert entry.attempted_solutions == []
        assert entry.resolved is False
        assert entry.source_agent == ""

    def test_from_dict_empty_dict(self):
        entry = EscalationEntry.from_dict({})
        assert entry.task_description == ""
        assert entry.blocker == ""


# ------------------------------------------------------------------
# Blocker management
# ------------------------------------------------------------------


class TestBlockerManagement:
    def test_record_blocker_creates_l1_entry(self, tracker):
        entry = tracker.record_blocker(
            "Deploy app",
            "Auth failure",
            source_agent="terraform-agent",
            source_stage="deploy",
        )
        assert entry.escalation_level == 1
        assert entry.blocker == "Auth failure"
        assert entry.source_agent == "terraform-agent"
        assert entry.created_at != ""

    def test_record_blocker_persists(self, tracker):
        tracker.record_blocker("task", "blocker", source_agent="agent", source_stage="stage")
        assert tracker._state_path.exists()

    def test_record_attempted_solution(self, tracker, sample_entry):
        tracker._entries.append(sample_entry)
        tracker.record_attempted_solution(sample_entry, "Tried a different SKU")
        assert "Tried a different SKU" in sample_entry.attempted_solutions

    def test_resolve_marks_entry(self, tracker, sample_entry):
        tracker._entries.append(sample_entry)
        tracker.resolve(sample_entry, "Switched to public ACR")
        assert sample_entry.resolved is True
        assert sample_entry.resolution == "Switched to public ACR"

    def test_get_active_blockers_excludes_resolved(self, tracker):
        e1 = tracker.record_blocker("t1", "b1", source_agent="a", source_stage="s")
        tracker.record_blocker("t2", "b2", source_agent="a", source_stage="s")
        tracker.resolve(e1, "Fixed")
        active = tracker.get_active_blockers()
        assert len(active) == 1
        assert active[0].task_description == "t2"


# ------------------------------------------------------------------
# State persistence
# ------------------------------------------------------------------


class TestStatePersistence:
    def test_save_and_load_round_trip(self, tracker):
        tracker.record_blocker("task1", "blocker1", source_agent="agent1", source_stage="build")
        tracker.record_blocker("task2", "blocker2", source_agent="agent2", source_stage="deploy")

        tracker2 = EscalationTracker(tracker._project_dir)
        tracker2.load()
        assert len(tracker2._entries) == 2
        assert tracker2._entries[0].task_description == "task1"
        assert tracker2._entries[1].blocker == "blocker2"

    def test_load_nonexistent_file(self, tmp_path):
        t = EscalationTracker(str(tmp_path / "no-project"))
        t.load()
        assert t._entries == []

    def test_exists_property(self, tracker):
        assert tracker.exists is False
        tracker.record_blocker("t", "b", source_agent="a", source_stage="s")
        assert tracker.exists is True


# ------------------------------------------------------------------
# Escalation chain — L1 -> L2
# ------------------------------------------------------------------


class TestEscalateToAgent:
    def _make_registry_and_context(self, agent_response="Here is the fix"):
        mock_agent = MagicMock()
        mock_agent.execute.return_value = MagicMock(content=agent_response)

        registry = MagicMock()
        registry.find_by_capability.return_value = [mock_agent]

        agent_context = MagicMock()
        agent_context.ai_provider = MagicMock()

        return registry, agent_context, mock_agent

    def test_technical_blocker_escalates_to_architect(self, tracker, sample_entry):
        tracker._entries.append(sample_entry)
        registry, ctx, agent = self._make_registry_and_context()
        printed = []

        result = tracker.escalate(sample_entry, registry, ctx, printed.append)

        assert result["escalated"] is True
        assert result["level"] == 2
        # Should use ARCHITECT capability (not BACKLOG_GENERATION)
        from azext_prototype.agents.base import AgentCapability

        registry.find_by_capability.assert_called_once_with(AgentCapability.ARCHITECT)

    def test_scope_blocker_escalates_to_pm(self, tracker):
        entry = EscalationEntry(
            task_description="Define feature scope",
            blocker="Unclear requirement for the backlog story",
            source_agent="biz-analyst",
            source_stage="design",
            created_at=datetime.now(timezone.utc).isoformat(),
            last_escalated_at=datetime.now(timezone.utc).isoformat(),
        )
        tracker._entries.append(entry)

        registry, ctx, agent = self._make_registry_and_context()
        result = tracker.escalate(entry, registry, ctx, MagicMock())

        from azext_prototype.agents.base import AgentCapability

        registry.find_by_capability.assert_called_once_with(AgentCapability.BACKLOG_GENERATION)
        assert result["level"] == 2

    def test_scope_keywords_detected(self, tracker):
        """Each scope keyword routes to PM."""
        scope_keywords = ["scope", "requirement", "backlog", "story", "feature", "stakeholder", "priority", "sprint"]
        for kw in scope_keywords:
            entry = EscalationEntry(
                task_description="task",
                blocker=f"Issue with {kw}",
                source_agent="a",
                source_stage="s",
                created_at=datetime.now(timezone.utc).isoformat(),
                last_escalated_at=datetime.now(timezone.utc).isoformat(),
            )
            tracker._entries.append(entry)

            registry, ctx, _ = self._make_registry_and_context()
            tracker.escalate(entry, registry, ctx, MagicMock())

            from azext_prototype.agents.base import AgentCapability

            registry.find_by_capability.assert_called_once_with(AgentCapability.BACKLOG_GENERATION)

    def test_no_agent_available(self, tracker, sample_entry):
        tracker._entries.append(sample_entry)
        registry = MagicMock()
        registry.find_by_capability.return_value = []
        ctx = MagicMock()
        ctx.ai_provider = MagicMock()
        printed = []

        result = tracker.escalate(sample_entry, registry, ctx, printed.append)

        assert result["level"] == 2
        assert "No cloud-architect available" in result["content"]

    def test_no_agent_context(self, tracker, sample_entry):
        tracker._entries.append(sample_entry)
        registry = MagicMock()
        registry.find_by_capability.return_value = [MagicMock()]

        result = tracker.escalate(sample_entry, registry, None, MagicMock())
        assert "No cloud-architect available" in result["content"]

    def test_no_ai_provider_on_context(self, tracker, sample_entry):
        tracker._entries.append(sample_entry)
        registry = MagicMock()
        registry.find_by_capability.return_value = [MagicMock()]
        ctx = MagicMock()
        ctx.ai_provider = None

        result = tracker.escalate(sample_entry, registry, ctx, MagicMock())
        assert "No cloud-architect available" in result["content"]

    def test_agent_execution_failure(self, tracker, sample_entry):
        tracker._entries.append(sample_entry)
        mock_agent = MagicMock()
        mock_agent.execute.side_effect = RuntimeError("model down")

        registry = MagicMock()
        registry.find_by_capability.return_value = [mock_agent]
        ctx = MagicMock()
        ctx.ai_provider = MagicMock()

        result = tracker.escalate(sample_entry, registry, ctx, MagicMock())
        assert "Agent escalation failed" in result["content"]

    def test_agent_returns_none_response(self, tracker, sample_entry):
        tracker._entries.append(sample_entry)
        mock_agent = MagicMock()
        mock_agent.execute.return_value = None

        registry = MagicMock()
        registry.find_by_capability.return_value = [mock_agent]
        ctx = MagicMock()
        ctx.ai_provider = MagicMock()

        result = tracker.escalate(sample_entry, registry, ctx, MagicMock())
        assert result["content"] == ""


# ------------------------------------------------------------------
# Escalation chain — L2 -> L3 (web search)
# ------------------------------------------------------------------


class TestEscalateToWebSearch:
    def test_web_search_success(self, tracker, sample_entry):
        sample_entry.escalation_level = 2
        tracker._entries.append(sample_entry)

        with patch(
            "azext_prototype.stages.escalation.EscalationTracker._escalate_to_web_search",
            return_value="Found docs on ACR networking",
        ):
            result = tracker.escalate(sample_entry, MagicMock(), MagicMock(), MagicMock())

        assert result["level"] == 3
        assert result["escalated"] is True

    def test_web_search_with_real_import(self, tracker, sample_entry):
        sample_entry.escalation_level = 2
        tracker._entries.append(sample_entry)

        with patch("azext_prototype.knowledge.web_search.search_and_fetch", return_value="Doc content"):
            printed = []
            result = tracker.escalate(sample_entry, MagicMock(), MagicMock(), printed.append)

        assert result["level"] == 3
        assert result["content"] == "Doc content"

    def test_web_search_no_results(self, tracker, sample_entry):
        sample_entry.escalation_level = 2
        tracker._entries.append(sample_entry)

        with patch("azext_prototype.knowledge.web_search.search_and_fetch", return_value=""):
            printed = []
            result = tracker.escalate(sample_entry, MagicMock(), MagicMock(), printed.append)

        assert result["level"] == 3
        assert "No web results found" in result["content"]

    def test_web_search_exception(self, tracker, sample_entry):
        sample_entry.escalation_level = 2
        tracker._entries.append(sample_entry)

        with patch(
            "azext_prototype.knowledge.web_search.search_and_fetch",
            side_effect=RuntimeError("network down"),
        ):
            printed = []
            result = tracker.escalate(sample_entry, MagicMock(), MagicMock(), printed.append)

        assert result["level"] == 3
        assert "Web search failed" in result["content"]


# ------------------------------------------------------------------
# Escalation chain — L3 -> L4 (human)
# ------------------------------------------------------------------


class TestEscalateToHuman:
    def test_human_escalation(self, tracker, sample_entry):
        sample_entry.escalation_level = 3
        tracker._entries.append(sample_entry)

        printed = []
        result = tracker.escalate(sample_entry, MagicMock(), MagicMock(), printed.append)

        assert result["level"] == 4
        assert result["escalated"] is True
        assert "Flagged for human intervention" in result["content"]
        assert any("HUMAN INTERVENTION REQUIRED" in msg for msg in printed)

    def test_human_escalation_includes_details(self, tracker, sample_entry):
        sample_entry.escalation_level = 3
        sample_entry.attempted_solutions = ["Tried A", "Tried B"]
        tracker._entries.append(sample_entry)

        printed = []
        tracker.escalate(sample_entry, MagicMock(), MagicMock(), printed.append)

        full_output = "\n".join(printed)
        assert sample_entry.task_description in full_output
        assert sample_entry.blocker in full_output
        assert "Tried A" in full_output
        assert "Tried B" in full_output


# ------------------------------------------------------------------
# Already at L4 — no further escalation
# ------------------------------------------------------------------


class TestAlreadyAtMaxLevel:
    def test_l4_cannot_escalate(self, tracker, sample_entry):
        sample_entry.escalation_level = 4
        tracker._entries.append(sample_entry)

        result = tracker.escalate(sample_entry, MagicMock(), MagicMock(), MagicMock())

        assert result["escalated"] is False
        assert result["level"] == 4
        assert "Already at human level" in result["content"]


# ------------------------------------------------------------------
# should_auto_escalate()
# ------------------------------------------------------------------


class TestShouldAutoEscalate:
    def test_resolved_entry_returns_false(self, tracker):
        entry = EscalationEntry(
            task_description="t",
            blocker="b",
            resolved=True,
            last_escalated_at=datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat(),
        )
        assert tracker.should_auto_escalate(entry, timeout_seconds=0) is False

    def test_l4_entry_returns_false(self, tracker):
        entry = EscalationEntry(
            task_description="t",
            blocker="b",
            escalation_level=4,
            last_escalated_at=datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat(),
        )
        assert tracker.should_auto_escalate(entry, timeout_seconds=0) is False

    def test_within_timeout_returns_false(self, tracker):
        recent = datetime.now(timezone.utc).isoformat()
        entry = EscalationEntry(
            task_description="t",
            blocker="b",
            escalation_level=1,
            last_escalated_at=recent,
        )
        assert tracker.should_auto_escalate(entry, timeout_seconds=120) is False

    def test_exceeded_timeout_returns_true(self, tracker):
        old = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
        entry = EscalationEntry(
            task_description="t",
            blocker="b",
            escalation_level=1,
            last_escalated_at=old,
        )
        assert tracker.should_auto_escalate(entry, timeout_seconds=120) is True

    def test_bad_timestamp_returns_false(self, tracker):
        entry = EscalationEntry(
            task_description="t",
            blocker="b",
            escalation_level=1,
            last_escalated_at="not-a-date",
        )
        assert tracker.should_auto_escalate(entry, timeout_seconds=0) is False

    def test_empty_timestamp_returns_false(self, tracker):
        entry = EscalationEntry(
            task_description="t",
            blocker="b",
            escalation_level=1,
            last_escalated_at="",
        )
        assert tracker.should_auto_escalate(entry, timeout_seconds=0) is False

    def test_l2_entry_can_auto_escalate(self, tracker):
        old = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
        entry = EscalationEntry(
            task_description="t",
            blocker="b",
            escalation_level=2,
            last_escalated_at=old,
        )
        assert tracker.should_auto_escalate(entry, timeout_seconds=120) is True

    def test_l3_entry_can_auto_escalate(self, tracker):
        old = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
        entry = EscalationEntry(
            task_description="t",
            blocker="b",
            escalation_level=3,
            last_escalated_at=old,
        )
        assert tracker.should_auto_escalate(entry, timeout_seconds=120) is True


# ------------------------------------------------------------------
# format_escalation_report()
# ------------------------------------------------------------------


class TestFormatReport:
    def test_no_entries(self, tracker):
        report = tracker.format_escalation_report()
        assert "No blockers recorded" in report

    def test_active_blockers_in_report(self, tracker):
        tracker.record_blocker("Deploy app", "Auth error", source_agent="tf", source_stage="deploy")
        report = tracker.format_escalation_report()
        assert "Active Blockers (1)" in report
        assert "Deploy app" in report
        assert "Auth error" in report
        assert "Documented" in report  # L1 label

    def test_resolved_in_report(self, tracker):
        entry = tracker.record_blocker("task", "blocker", source_agent="a", source_stage="s")
        tracker.resolve(entry, "Fixed by reconfig")
        report = tracker.format_escalation_report()
        assert "Resolved (1)" in report
        assert "Fixed by reconfig" in report

    def test_mixed_active_and_resolved(self, tracker):
        e1 = tracker.record_blocker("t1", "b1", source_agent="a", source_stage="s")
        tracker.record_blocker("t2", "b2", source_agent="a", source_stage="s")
        tracker.resolve(e1, "done")
        report = tracker.format_escalation_report()
        assert "Active Blockers (1)" in report
        assert "Resolved (1)" in report

    def test_level_labels(self, tracker):
        entry = tracker.record_blocker("t", "b", source_agent="a", source_stage="s")
        entry.escalation_level = 2
        report = tracker.format_escalation_report()
        assert "Agent" in report

    def test_attempted_solutions_count(self, tracker):
        entry = tracker.record_blocker("t", "b", source_agent="a", source_stage="s")
        tracker.record_attempted_solution(entry, "sol1")
        tracker.record_attempted_solution(entry, "sol2")
        report = tracker.format_escalation_report()
        assert "Attempts: 2" in report


# --- Additional imports from merged flat test ---
from azext_prototype.agents.base import AgentContext
from azext_prototype.ai.provider import AIResponse
from azext_prototype.stages.backlog_session import BacklogSession
from azext_prototype.stages.backlog_state import BacklogState
from azext_prototype.stages.build_session import BuildSession
from azext_prototype.stages.deploy_session import DeploySession
from azext_prototype.stages.deploy_state import DeployState
from azext_prototype.stages.qa_router import route_error_to_qa
from pathlib import Path
import yaml


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


class TestEscalationTrackerState:

    def test_record_blocker(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))

        entry = tracker.record_blocker(
            "Deploy Redis",
            "Premium tier required",
            "terraform-agent",
            "deploy",
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
        e2 = tracker.record_blocker("task2", "blocked2", "a2", "s2")  # noqa: F841
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
        e2 = tracker.record_blocker("t2", "b2", "a", "s")  # noqa: F841
        e3 = tracker.record_blocker("t3", "b3", "a", "s")

        tracker.resolve(e1, "fixed")
        tracker.resolve(e3, "workaround")

        assert len(tracker.get_active_blockers()) == 1
        assert tracker.get_active_blockers()[0].task_description == "t2"

# ======================================================================


class TestEscalationChain:

    def test_level_1_to_2_technical(self, tmp_project):
        """Technical blocker escalates to architect."""
        tracker = EscalationTracker(str(tmp_project))
        entry = tracker.record_blocker(
            "Deploy Cosmos DB",
            "Premium tier required for multi-region",
            "terraform-agent",
            "build",
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
            "Backlog items",
            "Scope of feature is unclear",
            "biz-analyst",
            "design",
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


class TestQARouterIntegration:

    def test_qa_router_records_blocker_on_undiagnosed(self, tmp_project):
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.stages.qa_router import route_error_to_qa

        tracker = EscalationTracker(str(tmp_project))

        # QA returns empty — undiagnosed
        qa = MagicMock()
        qa.execute.return_value = AIResponse(content="", model="gpt-4o", usage={})

        ctx = _make_context()

        result = route_error_to_qa(
            "Deployment failed",
            "Deploy Stage 1",
            qa,
            ctx,
            None,
            lambda m: None,
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
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.stages.qa_router import route_error_to_qa

        qa = MagicMock()
        qa.execute.return_value = AIResponse(content="", model="gpt-4o", usage={})

        ctx = _make_context()

        # No escalation tracker — should not raise
        result = route_error_to_qa(
            "error",
            "context",
            qa,
            ctx,
            None,
            lambda m: None,
            escalation_tracker=None,
        )

        assert result["diagnosed"] is False

    @patch("azext_prototype.stages.qa_router._submit_knowledge")
    def test_qa_router_diagnosed_no_blocker(self, mock_knowledge, tmp_project):
        from azext_prototype.ai.provider import AIResponse
        from azext_prototype.stages.qa_router import route_error_to_qa

        tracker = EscalationTracker(str(tmp_project))

        qa = MagicMock()
        qa.execute.return_value = AIResponse(content="Root cause: X", model="gpt-4o", usage={})

        ctx = _make_context()

        result = route_error_to_qa(
            "error",
            "context",
            qa,
            ctx,
            None,
            lambda m: None,
            escalation_tracker=tracker,
        )

        assert result["diagnosed"] is True
        # No blocker should be recorded when QA diagnoses successfully
        assert len(tracker.get_active_blockers()) == 0

    def test_build_session_has_escalation_tracker(self, tmp_project):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.stages.build_session import BuildSession

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
            mock_config.return_value.to_dict.return_value = {
                "naming": {"strategy": "simple"},
                "project": {"name": "test"},
            }
            session = BuildSession(ctx, registry)

        assert hasattr(session, "_escalation_tracker")
        assert isinstance(session._escalation_tracker, EscalationTracker)

    def test_deploy_session_has_escalation_tracker(self, tmp_project):
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.stages.deploy_session import DeploySession
        from azext_prototype.stages.deploy_state import DeployState

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
        from azext_prototype.agents.base import AgentContext
        from azext_prototype.stages.backlog_session import BacklogSession
        from azext_prototype.stages.backlog_state import BacklogState

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


class TestReportFormatting:

    def test_empty_report(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        report = tracker.format_escalation_report()
        assert "No blockers recorded" in report

    def test_report_with_active_and_resolved(self, tmp_project):
        tracker = EscalationTracker(str(tmp_project))
        e1 = tracker.record_blocker("Deploy Redis", "Premium needed", "tf", "build")  # noqa: F841
        e2 = tracker.record_blocker("Deploy Cosmos", "Multi-region", "tf", "build")
        tracker.resolve(e2, "Used single region")

        report = tracker.format_escalation_report()

        assert "Active Blockers (1)" in report
        assert "Deploy Redis" in report
        assert "Resolved (1)" in report
        assert "Used single region" in report
