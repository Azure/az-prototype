"""Tests for Phase 7: Runtime Documentation Access.

Covers:
- Web search functions (search_learn, fetch_page_content, search_and_fetch, format_search_results)
- Search cache (TTL, LRU eviction, normalization, stats)
- Search marker interception in BaseAgent
- Content filtering (POC vs production mode)
- Production items extraction
- Backlog session integration
- Session-level integration
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from azext_prototype.ai.provider import AIMessage, AIResponse
from azext_prototype.knowledge.search_cache import SearchCache


# ================================================================== #
# Fixtures
# ================================================================== #

@pytest.fixture
def cache():
    """Fresh search cache with short TTL for testing."""
    return SearchCache(ttl_seconds=2, max_entries=5)


def _mock_search_response(results):
    """Build a mock requests.Response for the Learn search API."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": results}
    resp.raise_for_status = MagicMock()
    return resp


def _mock_page_response(html):
    """Build a mock requests.Response for a page fetch."""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = html
    resp.raise_for_status = MagicMock()
    return resp


# ================================================================== #
# Web Search — search_learn
# ================================================================== #

class TestSearchLearn:
    """Tests for search_learn()."""

    @patch("azext_prototype.knowledge.web_search.requests.get")
    def test_returns_results_for_valid_query(self, mock_get):
        from azext_prototype.knowledge.web_search import search_learn

        mock_get.return_value = _mock_search_response([
            {"title": "Cosmos DB Intro", "url": "https://learn.microsoft.com/cosmos-db", "description": "Overview"},
            {"title": "Cosmos DB API", "url": "https://learn.microsoft.com/cosmos-api", "description": "API ref"},
        ])

        results = search_learn("cosmos db", max_results=3)
        assert len(results) == 2
        assert results[0]["title"] == "Cosmos DB Intro"
        assert results[0]["url"] == "https://learn.microsoft.com/cosmos-db"
        mock_get.assert_called_once()

    @patch("azext_prototype.knowledge.web_search.requests.get")
    def test_returns_empty_on_timeout(self, mock_get):
        from azext_prototype.knowledge.web_search import search_learn

        mock_get.side_effect = Exception("Connection timeout")
        results = search_learn("cosmos db")
        assert results == []

    @patch("azext_prototype.knowledge.web_search.requests.get")
    def test_returns_empty_on_bad_json(self, mock_get):
        from azext_prototype.knowledge.web_search import search_learn

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"unexpected": "format"}
        mock_get.return_value = resp

        results = search_learn("test")
        assert results == []

    @patch("azext_prototype.knowledge.web_search.requests.get")
    def test_respects_max_results(self, mock_get):
        from azext_prototype.knowledge.web_search import search_learn

        items = [
            {"title": f"Result {i}", "url": f"https://learn.microsoft.com/{i}", "description": ""}
            for i in range(10)
        ]
        mock_get.return_value = _mock_search_response(items)

        results = search_learn("test", max_results=2)
        assert len(results) == 2

    @patch("azext_prototype.knowledge.web_search.requests.get")
    def test_skips_entries_without_url(self, mock_get):
        from azext_prototype.knowledge.web_search import search_learn

        mock_get.return_value = _mock_search_response([
            {"title": "No URL", "url": "", "description": "Missing URL"},
            {"title": "Has URL", "url": "https://learn.microsoft.com/ok", "description": "OK"},
        ])

        results = search_learn("test")
        assert len(results) == 1
        assert results[0]["title"] == "Has URL"


# ================================================================== #
# Web Search — fetch_page_content
# ================================================================== #

class TestFetchPageContent:
    """Tests for fetch_page_content()."""

    @patch("azext_prototype.knowledge.web_search.requests.get")
    def test_strips_html(self, mock_get):
        from azext_prototype.knowledge.web_search import fetch_page_content

        mock_get.return_value = _mock_page_response(
            "<html><body><h1>Title</h1><p>Hello world</p></body></html>"
        )

        text = fetch_page_content("https://learn.microsoft.com/test")
        assert "Title" in text
        assert "Hello world" in text
        assert "<h1>" not in text
        assert "<p>" not in text

    @patch("azext_prototype.knowledge.web_search.requests.get")
    def test_truncates_to_max_chars(self, mock_get):
        from azext_prototype.knowledge.web_search import fetch_page_content

        long_text = "A" * 5000
        mock_get.return_value = _mock_page_response(f"<p>{long_text}</p>")

        text = fetch_page_content("https://learn.microsoft.com/test", max_chars=100)
        assert len(text) < 200  # 100 chars + truncation marker
        assert "[... truncated ...]" in text

    @patch("azext_prototype.knowledge.web_search.requests.get")
    def test_returns_empty_on_error(self, mock_get):
        from azext_prototype.knowledge.web_search import fetch_page_content

        mock_get.side_effect = Exception("Network error")
        text = fetch_page_content("https://learn.microsoft.com/test")
        assert text == ""

    @patch("azext_prototype.knowledge.web_search.requests.get")
    def test_strips_script_and_style_tags(self, mock_get):
        from azext_prototype.knowledge.web_search import fetch_page_content

        html = (
            "<html><head><style>body{}</style></head>"
            "<body><script>alert('x')</script>"
            "<p>Real content</p></body></html>"
        )
        mock_get.return_value = _mock_page_response(html)

        text = fetch_page_content("https://learn.microsoft.com/test")
        assert "Real content" in text
        assert "alert" not in text
        assert "body{}" not in text


# ================================================================== #
# Web Search — search_and_fetch + format_search_results
# ================================================================== #

class TestSearchAndFetch:
    """Tests for search_and_fetch() and format_search_results()."""

    @patch("azext_prototype.knowledge.web_search.requests.get")
    def test_combines_search_and_fetch(self, mock_get):
        from azext_prototype.knowledge.web_search import search_and_fetch

        def side_effect(url, **kwargs):
            if "api/search" in url:
                return _mock_search_response([
                    {"title": "Doc 1", "url": "https://learn.microsoft.com/doc1", "description": "Desc"},
                ])
            return _mock_page_response("<p>Content of doc 1</p>")

        mock_get.side_effect = side_effect

        result = search_and_fetch("test query")
        assert "Doc 1" in result
        assert "Content of doc 1" in result
        assert "learn.microsoft.com/doc1" in result

    @patch("azext_prototype.knowledge.web_search.requests.get")
    def test_returns_empty_when_no_search_results(self, mock_get):
        from azext_prototype.knowledge.web_search import search_and_fetch

        mock_get.return_value = _mock_search_response([])
        result = search_and_fetch("nonexistent query")
        assert result == ""

    @patch("azext_prototype.knowledge.web_search.requests.get")
    def test_returns_empty_when_all_fetches_fail(self, mock_get):
        from azext_prototype.knowledge.web_search import search_and_fetch

        call_count = [0]
        def side_effect(url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _mock_search_response([
                    {"title": "Doc", "url": "https://learn.microsoft.com/doc", "description": ""},
                ])
            raise Exception("Fetch failed")

        mock_get.side_effect = side_effect
        result = search_and_fetch("query")
        assert result == ""

    def test_format_search_results_includes_source_urls(self):
        from azext_prototype.knowledge.web_search import format_search_results

        results = [
            {"title": "Guide 1", "url": "https://example.com/1", "content": "Content A"},
            {"title": "Guide 2", "url": "https://example.com/2", "content": "Content B"},
        ]
        formatted = format_search_results(results)
        assert "Guide 1" in formatted
        assert "https://example.com/1" in formatted
        assert "Content A" in formatted
        assert "---" in formatted  # separator between results

    def test_format_search_results_empty(self):
        from azext_prototype.knowledge.web_search import format_search_results

        assert format_search_results([]) == ""


# ================================================================== #
# Search Cache
# ================================================================== #

class TestSearchCache:
    """Tests for SearchCache."""

    def test_initial_empty_state(self, cache):
        stats = cache.stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["entries"] == 0
        assert stats["oldest"] is None

    def test_put_get_round_trip(self, cache):
        cache.put("azure cosmos db", "Result text")
        result = cache.get("azure cosmos db")
        assert result == "Result text"
        assert cache.stats()["hits"] == 1

    def test_ttl_expiry(self):
        cache = SearchCache(ttl_seconds=0)  # Immediate expiry
        cache.put("query", "result")
        # Immediately expired
        result = cache.get("query")
        assert result is None
        assert cache.stats()["misses"] == 1

    def test_cache_miss_returns_none(self, cache):
        result = cache.get("nonexistent")
        assert result is None
        assert cache.stats()["misses"] == 1

    def test_normalized_keys_case(self, cache):
        cache.put("Azure Cosmos DB", "result")
        assert cache.get("azure cosmos db") == "result"
        assert cache.get("AZURE COSMOS DB") == "result"

    def test_normalized_keys_whitespace(self, cache):
        cache.put("  azure   cosmos  db  ", "result")
        assert cache.get("azure cosmos db") == "result"

    def test_max_entries_eviction(self):
        cache = SearchCache(ttl_seconds=60, max_entries=3)
        cache.put("q1", "r1")
        cache.put("q2", "r2")
        cache.put("q3", "r3")
        # All 3 should be present
        assert cache.stats()["entries"] == 3
        # Adding a 4th should evict the oldest
        cache.put("q4", "r4")
        assert cache.stats()["entries"] == 3
        assert cache.get("q4") == "r4"

    def test_clear_flushes_all(self, cache):
        cache.put("q1", "r1")
        cache.put("q2", "r2")
        cache.clear()
        assert cache.stats()["entries"] == 0
        assert cache.stats()["hits"] == 0
        assert cache.stats()["misses"] == 0
        assert cache.get("q1") is None

    def test_stats_tracking(self, cache):
        cache.put("q1", "r1")
        cache.get("q1")  # hit
        cache.get("q1")  # hit
        cache.get("missing")  # miss
        stats = cache.stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["entries"] == 1
        assert stats["oldest"] is not None

    def test_update_existing_key(self, cache):
        cache.put("q1", "old")
        cache.put("q1", "new")
        assert cache.get("q1") == "new"
        assert cache.stats()["entries"] == 1


# ================================================================== #
# Marker Interception — BaseAgent._resolve_searches
# ================================================================== #

class TestMarkerInterception:
    """Tests for [SEARCH: ...] marker detection and resolution."""

    def _make_agent(self, enable_search=True):
        from azext_prototype.agents.base import BaseAgent
        agent = BaseAgent(
            name="test-agent",
            description="Test agent",
            system_prompt="You are a test agent.",
        )
        agent._enable_web_search = enable_search
        agent._governance_aware = False
        return agent

    def _make_context(self, first_content="first response", second_content="final response"):
        from azext_prototype.agents.base import AgentContext
        provider = MagicMock()
        provider.chat.side_effect = [
            AIResponse(
                content=first_content,
                model="gpt-4o",
                usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            ),
            AIResponse(
                content=second_content,
                model="gpt-4o",
                usage={"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
            ),
        ]
        context = AgentContext(
            project_config={},
            project_dir="/tmp/test",
            ai_provider=provider,
        )
        return context, provider

    def test_no_markers_no_recall(self):
        agent = self._make_agent()
        context, provider = self._make_context(first_content="Normal response without markers")
        result = agent.execute(context, "test task")
        assert result.content == "Normal response without markers"
        assert provider.chat.call_count == 1

    @patch("azext_prototype.knowledge.web_search.search_and_fetch")
    def test_single_marker_detected_and_resolved(self, mock_search):
        mock_search.return_value = "## Cosmos DB\nSome documentation..."

        agent = self._make_agent()
        context, provider = self._make_context(
            first_content="I need to check [SEARCH: cosmos db managed identity] for this.",
            second_content="Based on the docs, here is the answer.",
        )

        result = agent.execute(context, "How to set up Cosmos DB?")
        assert result.content == "Based on the docs, here is the answer."
        assert provider.chat.call_count == 2
        mock_search.assert_called_once_with("cosmos db managed identity", max_results=2, max_chars_per_result=2000)

    @patch("azext_prototype.knowledge.web_search.search_and_fetch")
    def test_multiple_markers_up_to_3(self, mock_search):
        mock_search.return_value = "Doc content"

        agent = self._make_agent()
        content_with_markers = (
            "Need [SEARCH: query1] and [SEARCH: query2] and "
            "[SEARCH: query3] and [SEARCH: query4]"
        )
        context, provider = self._make_context(
            first_content=content_with_markers,
            second_content="Final answer",
        )

        result = agent.execute(context, "task")
        assert result.content == "Final answer"
        # Only 3 markers should be processed (4th ignored)
        assert mock_search.call_count == 3

    @patch("azext_prototype.knowledge.web_search.search_and_fetch")
    def test_cache_hit_avoids_http(self, mock_search):
        mock_search.return_value = "Fetched content"

        agent = self._make_agent()
        context, provider = self._make_context(
            first_content="[SEARCH: cosmos db]",
            second_content="Answer 1",
        )

        # Pre-populate cache
        cache = SearchCache()
        cache.put("cosmos db", "Cached content")
        context._search_cache = cache

        agent.execute(context, "task")
        # search_and_fetch should NOT be called since cache has it
        mock_search.assert_not_called()
        assert cache.stats()["hits"] == 1

    @patch("azext_prototype.knowledge.web_search.search_and_fetch")
    def test_search_failure_returns_original(self, mock_search):
        mock_search.return_value = ""  # Empty = no results

        agent = self._make_agent()
        original = "I need [SEARCH: nonexistent thing] for this."
        context, provider = self._make_context(first_content=original)

        result = agent.execute(context, "task")
        # Should return original response since no search results
        assert result.content == original
        assert provider.chat.call_count == 1

    def test_web_search_disabled_ignores_markers(self):
        agent = self._make_agent(enable_search=False)
        context, provider = self._make_context(
            first_content="Here is [SEARCH: something] in my response",
        )

        result = agent.execute(context, "task")
        assert result.content == "Here is [SEARCH: something] in my response"
        assert provider.chat.call_count == 1

    @patch("azext_prototype.knowledge.web_search.search_and_fetch")
    def test_usage_merged_from_both_calls(self, mock_search):
        mock_search.return_value = "Doc content"

        agent = self._make_agent()
        context, provider = self._make_context(
            first_content="[SEARCH: test]",
            second_content="Final",
        )

        result = agent.execute(context, "task")
        # Usage should be merged: 100+200=300 prompt, 50+100=150 completion
        assert result.usage["prompt_tokens"] == 300
        assert result.usage["completion_tokens"] == 150
        assert result.usage["total_tokens"] == 450

    @patch("azext_prototype.knowledge.web_search.search_and_fetch")
    def test_recall_prompt_instructs_no_further_markers(self, mock_search):
        mock_search.return_value = "Doc content"

        agent = self._make_agent()
        context, provider = self._make_context(
            first_content="[SEARCH: test]",
            second_content="Final answer",
        )

        agent.execute(context, "task")

        # Check the second chat call's messages
        second_call_messages = provider.chat.call_args_list[1][0][0]
        last_user_msg = [m for m in second_call_messages if m.role == "user"][-1]
        assert "Do not emit further [SEARCH:]" in last_user_msg.content

    @patch("azext_prototype.knowledge.web_search.search_and_fetch")
    def test_governance_runs_on_final_response(self, mock_search):
        """Governance check should run on the final response, not intermediate."""
        mock_search.return_value = "Doc content"

        agent = self._make_agent()
        agent._governance_aware = True
        # Mock validate_response to track what gets checked
        validated = []
        original_validate = agent.validate_response
        agent.validate_response = lambda text: (validated.append(text), [])[1]

        context, provider = self._make_context(
            first_content="[SEARCH: test]",
            second_content="Final validated content",
        )

        agent.execute(context, "task")
        # Should validate the final content only
        assert len(validated) == 1
        assert validated[0] == "Final validated content"


# ================================================================== #
# Content Filtering — KnowledgeLoader
# ================================================================== #

class TestContentFiltering:
    """Tests for mode-based content filtering in compose_context."""

    def _make_loader(self, tmp_path):
        from azext_prototype.knowledge import KnowledgeLoader

        # Create a minimal knowledge directory structure
        services_dir = tmp_path / "services"
        services_dir.mkdir()

        # Service file WITH production section
        (services_dir / "cosmos-db.md").write_text(
            "# Cosmos DB\n\n"
            "## POC Defaults\n"
            "- Serverless mode\n"
            "- Single region\n\n"
            "## Production Backlog Items\n"
            "- Geo-replication\n"
            "- Autoscale throughput\n"
            "- Custom backup policy\n",
            encoding="utf-8",
        )

        # Service file WITHOUT production section
        (services_dir / "key-vault.md").write_text(
            "# Key Vault\n\n"
            "## POC Defaults\n"
            "- Standard tier\n",
            encoding="utf-8",
        )

        # Service file with production section in the middle
        (services_dir / "app-service.md").write_text(
            "# App Service\n\n"
            "## POC Defaults\n"
            "- B1 SKU\n\n"
            "## Production Backlog Items\n"
            "- Scale out rules\n"
            "- Custom domain\n\n"
            "## Deployment Notes\n"
            "- Use deployment slots\n",
            encoding="utf-8",
        )

        return KnowledgeLoader(knowledge_dir=tmp_path)

    def test_poc_mode_strips_production_section(self, tmp_path):
        loader = self._make_loader(tmp_path)
        ctx = loader.compose_context(services=["cosmos-db"], mode="poc")
        assert "POC Defaults" in ctx
        assert "Production Backlog Items" not in ctx
        assert "Geo-replication" not in ctx

    def test_production_mode_keeps_all(self, tmp_path):
        loader = self._make_loader(tmp_path)
        ctx = loader.compose_context(services=["cosmos-db"], mode="production")
        assert "POC Defaults" in ctx
        assert "Production Backlog Items" in ctx
        assert "Geo-replication" in ctx

    def test_all_mode_keeps_all(self, tmp_path):
        loader = self._make_loader(tmp_path)
        ctx = loader.compose_context(services=["cosmos-db"], mode="all")
        assert "Production Backlog Items" in ctx
        assert "Geo-replication" in ctx

    def test_file_without_production_section_unaffected(self, tmp_path):
        loader = self._make_loader(tmp_path)
        ctx = loader.compose_context(services=["key-vault"], mode="poc")
        assert "Key Vault" in ctx
        assert "Standard tier" in ctx

    def test_multiple_sections_preserved(self, tmp_path):
        loader = self._make_loader(tmp_path)
        ctx = loader.compose_context(services=["app-service"], mode="poc")
        assert "POC Defaults" in ctx
        assert "B1 SKU" in ctx
        assert "Deployment Notes" in ctx
        assert "deployment slots" in ctx
        assert "Production Backlog Items" not in ctx
        assert "Scale out rules" not in ctx

    def test_extract_production_items_returns_bullets(self, tmp_path):
        loader = self._make_loader(tmp_path)
        items = loader.extract_production_items("cosmos-db")
        assert items == ["Geo-replication", "Autoscale throughput", "Custom backup policy"]

    def test_extract_production_items_empty_for_missing_section(self, tmp_path):
        loader = self._make_loader(tmp_path)
        items = loader.extract_production_items("key-vault")
        assert items == []

    def test_extract_production_items_empty_for_missing_file(self, tmp_path):
        loader = self._make_loader(tmp_path)
        items = loader.extract_production_items("nonexistent-service")
        assert items == []

    def test_default_mode_is_poc(self, tmp_path):
        loader = self._make_loader(tmp_path)
        # Default (no mode specified) should be POC
        ctx = loader.compose_context(services=["cosmos-db"])
        assert "Production Backlog Items" not in ctx


# ================================================================== #
# Backlog Integration
# ================================================================== #

class TestBacklogIntegration:
    """Tests for production items injection in BacklogSession."""

    def _make_session(self, tmp_path, items_response="[]"):
        from azext_prototype.agents.base import AgentContext, AgentCapability
        from azext_prototype.agents.registry import AgentRegistry
        from azext_prototype.stages.backlog_session import BacklogSession

        # Mock agent
        pm_agent = MagicMock()
        pm_agent.name = "project-manager"
        pm_agent.get_system_messages.return_value = []

        # Mock registry
        registry = MagicMock(spec=AgentRegistry)
        registry.find_by_capability.return_value = [pm_agent]

        # Mock AI provider
        provider = MagicMock()
        provider.chat.return_value = AIResponse(
            content=items_response,
            model="gpt-4o",
            usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
        )

        # Create project structure
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / ".prototype" / "state").mkdir(parents=True)

        context = AgentContext(
            project_config={"project": {"name": "test"}},
            project_dir=str(project_dir),
            ai_provider=provider,
        )

        session = BacklogSession(context, registry)
        return session, provider, project_dir

    def test_production_items_injected_into_prompt(self, tmp_path):
        session, provider, project_dir = self._make_session(tmp_path)

        # Create discovery state with services
        import yaml
        discovery = {
            "architecture": {"services": ["cosmos-db"]},
            "scope": {},
            "_metadata": {"exchange_count": 1},
        }
        state_file = project_dir / ".prototype" / "state" / "discovery.yaml"
        state_file.write_text(yaml.dump(discovery), encoding="utf-8")

        # Mock knowledge loader
        with patch("azext_prototype.stages.backlog_session.BacklogSession._get_production_items") as mock_items:
            mock_items.return_value = "### cosmos-db\n- Geo-replication\n- Autoscale throughput\n"

            items_json = '[{"epic":"Core","title":"Setup","description":"d","acceptance_criteria":[],"tasks":[],"effort":"S"}]'
            provider.chat.return_value = AIResponse(
                content=items_json,
                model="gpt-4o",
                usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
            )

            session.run(
                design_context="Some architecture",
                scope=None,
                input_fn=lambda p: "done",
                print_fn=lambda s: None,
            )

            # Verify chat was called and the task includes production items
            call_args = provider.chat.call_args_list[0]
            messages = call_args[0][0]
            user_msg = [m for m in messages if m.role == "user"][0]
            assert "Production Backlog Items" in user_msg.content

    def test_empty_production_items_no_injection(self, tmp_path):
        session, provider, project_dir = self._make_session(tmp_path)

        with patch("azext_prototype.stages.backlog_session.BacklogSession._get_production_items") as mock_items:
            mock_items.return_value = ""

            items_json = '[{"epic":"Core","title":"Setup","description":"d","acceptance_criteria":[],"tasks":[],"effort":"S"}]'
            provider.chat.return_value = AIResponse(
                content=items_json,
                model="gpt-4o",
                usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
            )

            session.run(
                design_context="Some architecture",
                scope=None,
                input_fn=lambda p: "done",
                print_fn=lambda s: None,
            )

            call_args = provider.chat.call_args_list[0]
            messages = call_args[0][0]
            user_msg = [m for m in messages if m.role == "user"][0]
            assert "Production Backlog Items" not in user_msg.content

    def test_multiple_services_items_aggregated(self, tmp_path):
        session, provider, project_dir = self._make_session(tmp_path)

        # Create discovery state with multiple services
        import yaml
        discovery = {
            "architecture": {"services": ["cosmos-db", "app-service"]},
            "scope": {},
            "_metadata": {"exchange_count": 1},
        }
        state_file = project_dir / ".prototype" / "state" / "discovery.yaml"
        state_file.write_text(yaml.dump(discovery), encoding="utf-8")

        with patch("azext_prototype.knowledge.KnowledgeLoader") as MockLoader:
            loader = MagicMock()
            loader.extract_production_items.side_effect = lambda svc: {
                "cosmos-db": ["Geo-replication"],
                "app-service": ["Scale rules"],
            }.get(svc, [])
            MockLoader.return_value = loader

            result = session._get_production_items()
            assert "cosmos-db" in result
            assert "Geo-replication" in result
            assert "app-service" in result
            assert "Scale rules" in result

    def test_deferred_epic_includes_production_items(self, tmp_path):
        session, provider, project_dir = self._make_session(tmp_path)

        with patch("azext_prototype.stages.backlog_session.BacklogSession._get_production_items") as mock_items:
            mock_items.return_value = "### cosmos-db\n- Geo-replication\n"

            # AI returns items including deferred epic with production items
            items_json = json.dumps([
                {"epic": "Deferred / Future Work", "title": "Geo-replication",
                 "description": "Set up geo-replication", "acceptance_criteria": [],
                 "tasks": [], "effort": "L"},
            ])
            provider.chat.return_value = AIResponse(
                content=items_json,
                model="gpt-4o",
                usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
            )

            result = session.run(
                design_context="Architecture text",
                scope=None,
                input_fn=lambda p: "done",
                print_fn=lambda s: None,
            )

            assert result.items_generated == 1


# ================================================================== #
# Session Integration
# ================================================================== #

class TestSessionIntegration:
    """Tests for session-level integration of web search."""

    @patch("azext_prototype.knowledge.web_search.search_and_fetch")
    def test_cache_attached_to_context_on_first_search(self, mock_search):
        mock_search.return_value = "Doc content"

        from azext_prototype.agents.base import BaseAgent, AgentContext

        agent = BaseAgent(
            name="test",
            description="test",
            system_prompt="test",
        )
        agent._enable_web_search = True
        agent._governance_aware = False

        provider = MagicMock()
        provider.chat.side_effect = [
            AIResponse(content="[SEARCH: test]", model="m", usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
            AIResponse(content="Final", model="m", usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
        ]

        context = AgentContext(
            project_config={}, project_dir="/tmp", ai_provider=provider,
        )

        assert not hasattr(context, "_search_cache")
        agent.execute(context, "task")
        assert hasattr(context, "_search_cache")
        assert isinstance(context._search_cache, SearchCache)

    @patch("azext_prototype.knowledge.web_search.search_and_fetch")
    def test_cache_shared_across_agents(self, mock_search):
        mock_search.return_value = "Doc content"

        from azext_prototype.agents.base import BaseAgent, AgentContext

        agent1 = BaseAgent(name="a1", description="", system_prompt="")
        agent1._enable_web_search = True
        agent1._governance_aware = False

        agent2 = BaseAgent(name="a2", description="", system_prompt="")
        agent2._enable_web_search = True
        agent2._governance_aware = False

        call_idx = [0]
        def chat_side_effect(messages, **kwargs):
            call_idx[0] += 1
            if call_idx[0] in (1, 3):  # First calls return search markers
                return AIResponse(content="[SEARCH: same query]", model="m",
                                  usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
            return AIResponse(content="Answer", model="m",
                              usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

        provider = MagicMock()
        provider.chat.side_effect = chat_side_effect

        context = AgentContext(
            project_config={}, project_dir="/tmp", ai_provider=provider,
        )

        agent1.execute(context, "task1")
        agent2.execute(context, "task2")

        # Agent 2 should have gotten cache hit, so search_and_fetch only called once
        assert mock_search.call_count == 1
        assert context._search_cache.stats()["hits"] == 1

    @patch("azext_prototype.knowledge.web_search.search_and_fetch")
    def test_token_tracker_records_both_calls(self, mock_search):
        """Both AI calls are visible in the provider's call history."""
        mock_search.return_value = "Doc content"

        from azext_prototype.agents.base import BaseAgent, AgentContext

        agent = BaseAgent(name="t", description="", system_prompt="")
        agent._enable_web_search = True
        agent._governance_aware = False

        provider = MagicMock()
        provider.chat.side_effect = [
            AIResponse(content="[SEARCH: q]", model="m",
                       usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}),
            AIResponse(content="Final", model="m",
                       usage={"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300}),
        ]

        context = AgentContext(
            project_config={}, project_dir="/tmp", ai_provider=provider,
        )

        result = agent.execute(context, "task")
        # Provider should have been called twice
        assert provider.chat.call_count == 2
        # Merged usage
        assert result.usage["prompt_tokens"] == 300
        assert result.usage["total_tokens"] == 450

    @patch("azext_prototype.knowledge.web_search.search_and_fetch")
    def test_search_works_in_build_session_agent_call(self, mock_search):
        """Verify that agents called within sessions can use web search."""
        mock_search.return_value = "Doc content"

        from azext_prototype.agents.base import BaseAgent, AgentContext

        agent = BaseAgent(name="terraform-agent", description="", system_prompt="")
        agent._enable_web_search = True
        agent._governance_aware = False

        provider = MagicMock()
        provider.chat.side_effect = [
            AIResponse(
                content="resource [SEARCH: azurerm_cosmosdb_account] config",
                model="m",
                usage={"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75},
            ),
            AIResponse(
                content="resource azurerm_cosmosdb_account with correct config",
                model="m",
                usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            ),
        ]

        context = AgentContext(
            project_config={}, project_dir="/tmp", ai_provider=provider,
        )

        result = agent.execute(context, "Generate Cosmos DB Terraform")
        assert "correct config" in result.content
        assert provider.chat.call_count == 2


# ================================================================== #
# HTML Text Extractor
# ================================================================== #

class TestHTMLTextExtractor:
    """Tests for the internal HTML parser."""

    def test_strips_nav_header_footer(self):
        from azext_prototype.knowledge.web_search import _html_to_text

        html = (
            "<nav>Nav content</nav>"
            "<header>Header content</header>"
            "<main><p>Main content</p></main>"
            "<footer>Footer content</footer>"
        )
        text = _html_to_text(html)
        assert "Main content" in text
        assert "Nav content" not in text
        assert "Header content" not in text
        assert "Footer content" not in text

    def test_preserves_paragraph_breaks(self):
        from azext_prototype.knowledge.web_search import _html_to_text

        html = "<p>Paragraph 1</p><p>Paragraph 2</p>"
        text = _html_to_text(html)
        assert "Paragraph 1" in text
        assert "Paragraph 2" in text


# Need json import for test_deferred_epic_includes_production_items
import json
