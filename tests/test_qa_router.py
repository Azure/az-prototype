"""Tests for azext_prototype.stages.qa_router — shared QA error routing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from azext_prototype.agents.base import AgentContext
from azext_prototype.ai.provider import AIResponse
from azext_prototype.stages.qa_router import route_error_to_qa


# ======================================================================
# Helpers
# ======================================================================

def _make_response(content: str = "Root cause: X. Fix: do Y.") -> AIResponse:
    return AIResponse(content=content, model="gpt-4o", usage={})


def _make_qa_agent(response: AIResponse | None = None, raises: Exception | None = None):
    agent = MagicMock()
    agent.name = "qa-engineer"
    if raises:
        agent.execute.side_effect = raises
    else:
        agent.execute.return_value = response or _make_response()
    return agent


def _make_context():
    return AgentContext(
        project_config={"project": {"name": "test"}},
        project_dir="/tmp/test",
        ai_provider=MagicMock(),
    )


def _make_tracker():
    tracker = MagicMock()
    return tracker


# ======================================================================
# Core routing tests
# ======================================================================

class TestRouteErrorToQA:
    """Tests for route_error_to_qa()."""

    def test_qa_agent_available_diagnoses_error(self):
        qa = _make_qa_agent()
        ctx = _make_context()
        tracker = _make_tracker()
        printed = []

        result = route_error_to_qa(
            "Something broke", "Build Stage 1",
            qa, ctx, tracker, printed.append,
        )

        assert result["diagnosed"] is True
        assert result["content"] == "Root cause: X. Fix: do Y."
        assert result["response"] is not None
        qa.execute.assert_called_once()
        tracker.record.assert_called_once()

    def test_qa_agent_none_returns_graceful_fallback(self):
        ctx = _make_context()
        printed = []

        result = route_error_to_qa(
            "Something broke", "Build Stage 1",
            None, ctx, None, printed.append,
        )

        assert result["diagnosed"] is False
        assert result["content"] == "Something broke"
        assert result["response"] is None
        assert len(printed) == 0  # no output when undiagnosed

    def test_string_error_input(self):
        qa = _make_qa_agent()
        ctx = _make_context()
        printed = []

        result = route_error_to_qa(
            "Connection refused", "Deploy Stage 2",
            qa, ctx, None, printed.append,
        )

        assert result["diagnosed"] is True
        assert "Connection refused" in qa.execute.call_args[0][1]

    def test_exception_error_input(self):
        qa = _make_qa_agent()
        ctx = _make_context()
        printed = []

        result = route_error_to_qa(
            ValueError("bad value"), "Build Stage 3",
            qa, ctx, None, printed.append,
        )

        assert result["diagnosed"] is True
        assert "bad value" in qa.execute.call_args[0][1]

    def test_long_error_truncated_at_max_chars(self):
        qa = _make_qa_agent()
        ctx = _make_context()
        printed = []

        long_error = "x" * 5000

        result = route_error_to_qa(
            long_error, "Build Stage 1",
            qa, ctx, None, printed.append,
            max_error_chars=100,
        )

        assert result["diagnosed"] is True
        task_text = qa.execute.call_args[0][1]
        # The error in the task should be truncated
        assert "x" * 100 in task_text
        assert "x" * 5000 not in task_text

    def test_qa_agent_raises_returns_undiagnosed(self):
        qa = _make_qa_agent(raises=RuntimeError("QA crashed"))
        ctx = _make_context()
        printed = []

        result = route_error_to_qa(
            "Original error", "Build Stage 1",
            qa, ctx, None, printed.append,
        )

        assert result["diagnosed"] is False
        assert result["content"] == "Original error"
        assert result["response"] is None

    def test_token_tracker_records_response(self):
        qa = _make_qa_agent()
        ctx = _make_context()
        tracker = _make_tracker()

        route_error_to_qa(
            "error", "context",
            qa, ctx, tracker, lambda m: None,
        )

        tracker.record.assert_called_once()

    def test_token_tracker_none_does_not_crash(self):
        qa = _make_qa_agent()
        ctx = _make_context()

        result = route_error_to_qa(
            "error", "context",
            qa, ctx, None, lambda m: None,
        )

        assert result["diagnosed"] is True

    def test_print_fn_called_with_diagnosis(self):
        qa = _make_qa_agent(_make_response("Fix: restart the service"))
        ctx = _make_context()
        printed = []

        route_error_to_qa(
            "error", "context",
            qa, ctx, None, printed.append,
        )

        assert any("QA Diagnosis" in p for p in printed)
        assert any("Fix: restart the service" in p for p in printed)

    def test_display_truncated_at_max_display_chars(self):
        long_response = "a" * 3000
        qa = _make_qa_agent(_make_response(long_response))
        ctx = _make_context()
        printed = []

        route_error_to_qa(
            "error", "context",
            qa, ctx, None, printed.append,
            max_display_chars=500,
        )

        # One of the printed lines should be truncated
        display_lines = [p for p in printed if "a" in p]
        assert any(len(p) <= 500 for p in display_lines)

    def test_no_ai_provider_returns_undiagnosed(self):
        qa = _make_qa_agent()
        ctx = _make_context()
        ctx.ai_provider = None
        printed = []

        result = route_error_to_qa(
            "error", "context",
            qa, ctx, None, printed.append,
        )

        assert result["diagnosed"] is False

    def test_empty_error_uses_unknown(self):
        qa = _make_qa_agent()
        ctx = _make_context()

        result = route_error_to_qa(
            "", "context",
            qa, ctx, None, lambda m: None,
        )

        assert result["diagnosed"] is True
        # Should have used "Unknown error"
        task_text = qa.execute.call_args[0][1]
        assert "Unknown error" in task_text

    def test_none_error_uses_unknown(self):
        qa = _make_qa_agent()
        ctx = _make_context()

        result = route_error_to_qa(
            None, "context",
            qa, ctx, None, lambda m: None,
        )

        assert result["diagnosed"] is True
        task_text = qa.execute.call_args[0][1]
        assert "Unknown error" in task_text

    def test_qa_returns_empty_content(self):
        qa = _make_qa_agent(_make_response(""))
        ctx = _make_context()
        printed = []

        result = route_error_to_qa(
            "error", "context",
            qa, ctx, None, printed.append,
        )

        assert result["diagnosed"] is False

    @patch("azext_prototype.stages.qa_router._submit_knowledge")
    def test_knowledge_contribution_attempted(self, mock_submit):
        qa = _make_qa_agent()
        ctx = _make_context()

        route_error_to_qa(
            "error", "Build Stage 1",
            qa, ctx, None, lambda m: None,
            services=["key-vault"],
        )

        mock_submit.assert_called_once()
        args = mock_submit.call_args[0]
        assert args[0] == "Root cause: X. Fix: do Y."
        assert args[1] == "Build Stage 1"
        assert args[2] == ["key-vault"]

    @patch("azext_prototype.stages.qa_router._submit_knowledge", side_effect=Exception("boom"))
    def test_knowledge_failure_swallowed(self, mock_submit):
        qa = _make_qa_agent()
        ctx = _make_context()

        # Should not raise
        result = route_error_to_qa(
            "error", "context",
            qa, ctx, None, lambda m: None,
            services=["svc"],
        )

        assert result["diagnosed"] is True

    def test_services_none_no_knowledge_submitted(self):
        qa = _make_qa_agent()
        ctx = _make_context()

        with patch("azext_prototype.stages.qa_router._submit_knowledge") as mock_submit:
            route_error_to_qa(
                "error", "context",
                qa, ctx, None, lambda m: None,
            )

            mock_submit.assert_called_once()
            # services should be None
            assert mock_submit.call_args[0][2] is None

    def test_context_label_in_task_prompt(self):
        qa = _make_qa_agent()
        ctx = _make_context()

        route_error_to_qa(
            "error", "Deploy Stage 5: Redis Cache",
            qa, ctx, None, lambda m: None,
        )

        task_text = qa.execute.call_args[0][1]
        assert "Deploy Stage 5: Redis Cache" in task_text

    def test_token_tracker_record_failure_swallowed(self):
        qa = _make_qa_agent()
        ctx = _make_context()
        tracker = MagicMock()
        tracker.record.side_effect = Exception("tracker boom")

        # Should not raise
        result = route_error_to_qa(
            "error", "context",
            qa, ctx, tracker, lambda m: None,
        )

        assert result["diagnosed"] is True


# ======================================================================
# Integration: Build session QA routing
# ======================================================================

class TestBuildSessionQARouting:
    """Test that build session routes errors through qa_router."""

    def _make_session(self, tmp_project, qa_agent=None, response=None):
        from azext_prototype.stages.build_session import BuildSession
        from azext_prototype.stages.build_state import BuildState

        ctx = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )

        registry = MagicMock()

        # IaC agent that fails
        iac_agent = MagicMock()
        iac_agent.name = "terraform-agent"
        if response is not None:
            iac_agent.execute.return_value = response
        else:
            iac_agent.execute.side_effect = RuntimeError("AI exploded")

        doc_agent = MagicMock()
        doc_agent.name = "doc-agent"
        doc_agent.execute.return_value = _make_response("# Docs")

        qa = qa_agent or _make_qa_agent()

        def find_by_cap(cap):
            from azext_prototype.agents.base import AgentCapability
            if cap == AgentCapability.TERRAFORM:
                return [iac_agent]
            if cap == AgentCapability.QA:
                return [qa]
            if cap == AgentCapability.DOCUMENT:
                return [doc_agent]
            if cap == AgentCapability.ARCHITECT:
                return []
            return []

        registry.find_by_capability.side_effect = find_by_cap

        build_state = BuildState(str(tmp_project))
        build_state.set_deployment_plan([
            {
                "stage": 1, "name": "Foundation", "category": "infra",
                "dir": "concept/infra/terraform/stage-1-foundation",
                "services": [{"name": "key-vault", "computed_name": "kv-1", "resource_type": "", "sku": ""}],
                "status": "pending", "files": [],
            },
        ])

        with patch("azext_prototype.stages.build_session.ProjectConfig") as mock_config:
            mock_config.return_value.load.return_value = None
            mock_config.return_value.get.side_effect = lambda k, d=None: {
                "project.iac_tool": "terraform",
                "project.name": "test",
            }.get(k, d)
            mock_config.return_value.to_dict.return_value = {"naming": {"strategy": "simple"}, "project": {"name": "test"}}
            session = BuildSession(ctx, registry, build_state=build_state)

        return session, qa

    @patch("azext_prototype.stages.qa_router._submit_knowledge")
    def test_stage_generation_failure_routes_to_qa(self, mock_knowledge, tmp_project):
        session, qa = self._make_session(tmp_project)
        printed = []

        result = session.run(
            design={"architecture": "Simple web app"},
            input_fn=lambda p: "done",
            print_fn=printed.append,
        )

        qa.execute.assert_called()
        assert any("QA Diagnosis" in p for p in printed)

    @patch("azext_prototype.stages.qa_router._submit_knowledge")
    def test_empty_response_routes_to_qa(self, mock_knowledge, tmp_project):
        empty_resp = AIResponse(content="", model="gpt-4o", usage={})
        session, qa = self._make_session(tmp_project, response=empty_resp)
        printed = []

        result = session.run(
            design={"architecture": "Simple web app"},
            input_fn=lambda p: "done",
            print_fn=printed.append,
        )

        # QA should be called for empty response
        qa.execute.assert_called()


# ======================================================================
# Integration: Discovery session QA routing
# ======================================================================

class TestDiscoveryQARouting:
    """Test that discovery routes non-vision errors through qa_router."""

    @patch("azext_prototype.stages.qa_router._submit_knowledge")
    def test_non_vision_error_routes_to_qa(self, mock_knowledge, tmp_project):
        from azext_prototype.stages.discovery import DiscoverySession

        ctx = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )

        biz_agent = MagicMock()
        biz_agent.name = "biz-analyst"
        biz_agent.capabilities = []
        biz_agent._temperature = 0.5
        biz_agent._max_tokens = 8192
        biz_agent.get_system_messages.return_value = []

        qa = _make_qa_agent()

        registry = MagicMock()

        from azext_prototype.agents.base import AgentCapability
        def find_by_cap(cap):
            if cap == AgentCapability.BIZ_ANALYSIS:
                return [biz_agent]
            if cap == AgentCapability.QA:
                return [qa]
            return []

        registry.find_by_capability.side_effect = find_by_cap

        ctx.ai_provider.chat.side_effect = RuntimeError("API error")

        session = DiscoverySession(ctx, registry)

        with pytest.raises(RuntimeError, match="API error"):
            session.run(
                seed_context="test",
                input_fn=lambda p: "done",
                print_fn=lambda m: None,
            )

        # QA should have been called for the error diagnosis
        qa.execute.assert_called_once()


# ======================================================================
# Integration: Backlog session QA routing
# ======================================================================

class TestBacklogQARouting:
    """Test that backlog session routes errors through qa_router."""

    def _make_session(self, tmp_project, items_response="[]"):
        from azext_prototype.stages.backlog_session import BacklogSession
        from azext_prototype.stages.backlog_state import BacklogState

        ctx = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )

        pm = MagicMock()
        pm.name = "project-manager"
        pm.get_system_messages.return_value = []
        qa = _make_qa_agent()

        registry = MagicMock()
        from azext_prototype.agents.base import AgentCapability
        def find_by_cap(cap):
            if cap == AgentCapability.BACKLOG_GENERATION:
                return [pm]
            if cap == AgentCapability.QA:
                return [qa]
            return []

        registry.find_by_capability.side_effect = find_by_cap

        ctx.ai_provider.chat.return_value = AIResponse(
            content=items_response, model="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

        session = BacklogSession(ctx, registry, backlog_state=BacklogState(str(tmp_project)))
        return session, qa, ctx

    @patch("azext_prototype.stages.qa_router._submit_knowledge")
    def test_empty_parse_triggers_qa(self, mock_knowledge, tmp_project):
        session, qa, ctx = self._make_session(tmp_project, items_response="not valid json at all")
        printed = []

        result = session.run(
            design_context="web app architecture",
            input_fn=lambda p: "done",
            print_fn=printed.append,
        )

        qa.execute.assert_called()
        assert result.cancelled is True

    @patch("azext_prototype.stages.qa_router._submit_knowledge")
    @patch("azext_prototype.stages.backlog_session.check_gh_auth", return_value=True)
    @patch("azext_prototype.stages.backlog_session.push_github_issue")
    def test_push_error_triggers_qa(self, mock_push, mock_auth, mock_knowledge, tmp_project):
        import json
        items = [{"epic": "Infra", "title": "Setup VNet", "description": "Create VNet", "tasks": [], "effort": "M"}]
        session, qa, ctx = self._make_session(tmp_project, items_response=json.dumps(items))

        mock_push.return_value = {"error": "gh: auth required"}

        printed = []
        result = session.run(
            design_context="web app",
            provider="github",
            org="myorg",
            project="myrepo",
            quick=True,
            input_fn=lambda p: "y",
            print_fn=printed.append,
        )

        qa.execute.assert_called()


# ======================================================================
# Integration: Deploy session refactored QA routing
# ======================================================================

class TestDeploySessionRefactoredQA:
    """Test that refactored deploy session still works correctly."""

    def test_handle_deploy_failure_uses_qa_router(self, tmp_project):
        from azext_prototype.stages.deploy_session import DeploySession
        from azext_prototype.stages.deploy_state import DeployState

        ctx = AgentContext(
            project_config={"project": {"name": "test", "location": "eastus"}},
            project_dir=str(tmp_project),
            ai_provider=MagicMock(),
        )

        qa = _make_qa_agent(_make_response("Root cause: missing permissions"))
        registry = MagicMock()
        from azext_prototype.agents.base import AgentCapability
        def find_by_cap(cap):
            if cap == AgentCapability.QA:
                return [qa]
            return []
        registry.find_by_capability.side_effect = find_by_cap

        with patch("azext_prototype.stages.deploy_session.ProjectConfig") as mock_config:
            mock_config.return_value.load.return_value = None
            mock_config.return_value.get.side_effect = lambda k, d=None: {
                "project.iac_tool": "terraform",
            }.get(k, d)
            session = DeploySession(ctx, registry, deploy_state=DeployState(str(tmp_project)))

        printed = []
        stage = {"stage": 1, "name": "Foundation", "services": [{"name": "rg"}]}
        result = {"error": "Deployment failed: access denied"}

        session._handle_deploy_failure(
            stage, result, False, printed.append, lambda p: "",
        )

        qa.execute.assert_called_once()
        assert any("QA Diagnosis" in p for p in printed)
        assert any("missing permissions" in p for p in printed)
        assert any("Options:" in p for p in printed)

    def test_handle_deploy_failure_no_qa_shows_error(self, tmp_project):
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

        printed = []
        stage = {"stage": 1, "name": "Foundation", "services": []}
        result = {"error": "access denied"}

        session._handle_deploy_failure(
            stage, result, False, printed.append, lambda p: "",
        )

        assert any("Error:" in p for p in printed)
        assert any("Options:" in p for p in printed)
