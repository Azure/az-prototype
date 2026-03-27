"""Tests for azext_prototype.stages.intent — natural language intent classification."""

from __future__ import annotations

from unittest.mock import MagicMock

from azext_prototype.ai.provider import AIResponse
from azext_prototype.stages.intent import (
    CommandDef,
    IntentClassifier,
    IntentKind,
    IntentPattern,
    build_backlog_classifier,
    build_build_classifier,
    build_deploy_classifier,
    build_discovery_classifier,
    read_files_for_session,
)

# ======================================================================
# Helpers
# ======================================================================


def _make_response(content: str) -> AIResponse:
    return AIResponse(content=content, model="gpt-4o", usage={})


def _make_classifier_with_ai(response_content: str) -> IntentClassifier:
    """Build a classifier with a mock AI provider that returns the given content."""
    provider = MagicMock()
    provider.chat.return_value = _make_response(response_content)
    c = IntentClassifier(ai_provider=provider)
    c.add_command_def(CommandDef("/open", "Show open items"))
    c.add_command_def(CommandDef("/status", "Show status"))
    return c


# ======================================================================
# TestIntentClassifier — core classifier
# ======================================================================


class TestIntentClassifier:
    """Core IntentClassifier tests."""

    def test_empty_input_conversational(self):
        c = IntentClassifier()
        result = c.classify("")
        assert result.kind == IntentKind.CONVERSATIONAL

    def test_whitespace_only_conversational(self):
        c = IntentClassifier()
        result = c.classify("   ")
        assert result.kind == IntentKind.CONVERSATIONAL

    def test_slash_command_passthrough(self):
        """Explicit slash commands should return CONVERSATIONAL for pass-through."""
        c = IntentClassifier()
        result = c.classify("/open")
        assert result.kind == IntentKind.CONVERSATIONAL

    def test_ai_classification_parses_command(self):
        """AI classification used when keywords have partial match."""
        c = _make_classifier_with_ai('{"command": "/open", "args": "", "is_command": true}')
        # Register a keyword with partial signal (one keyword = 0.2, below 0.5 threshold)
        c.register(IntentPattern(command="/open", keywords=["items"], min_confidence=0.5))
        result = c.classify("what are the open items")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/open"

    def test_ai_classification_conversational(self):
        c = _make_classifier_with_ai('{"command": "", "args": "", "is_command": false}')
        result = c.classify("I think we should use PostgreSQL")
        assert result.kind == IntentKind.CONVERSATIONAL

    def test_ai_classification_with_args(self):
        """AI classification used when keywords have partial match."""
        c = _make_classifier_with_ai('{"command": "/deploy", "args": "3", "is_command": true}')
        # Register a keyword with partial signal
        c.register(IntentPattern(command="/deploy", keywords=["deploy"], min_confidence=0.5))
        result = c.classify("deploy stage 3")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/deploy"
        assert result.args == "3"

    def test_ai_classification_falls_back_on_parse_error(self):
        """When AI returns unparseable JSON, fall through to keyword scoring."""
        c = _make_classifier_with_ai("This is not JSON at all")
        # Register a keyword pattern that will match (keyword + phrase = 0.6)
        c.register(
            IntentPattern(
                command="/open",
                keywords=["open"],
                phrases=["open items"],
            )
        )
        result = c.classify("what are the open items")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/open"

    def test_ai_classification_falls_back_when_no_provider(self):
        """When no AI provider, keyword fallback runs."""
        c = IntentClassifier()  # No AI provider
        c.add_command_def(CommandDef("/open", "Show open items"))
        c.register(
            IntentPattern(
                command="/open",
                keywords=["open"],
                phrases=["open items"],
            )
        )
        result = c.classify("what are the open items")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/open"

    def test_keyword_matching_triggers_command(self):
        c = IntentClassifier()
        c.register(
            IntentPattern(
                command="/status",
                keywords=["status"],
                phrases=["build status"],
            )
        )
        result = c.classify("what's the build status")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/status"

    def test_below_threshold_conversational(self):
        c = IntentClassifier()
        c.register(
            IntentPattern(
                command="/deploy",
                keywords=[],
                phrases=["deploy stage"],
                min_confidence=0.5,
            )
        )
        # "the" keyword alone shouldn't match
        result = c.classify("I like the architecture")
        assert result.kind == IntentKind.CONVERSATIONAL

    def test_phrase_outscores_keyword(self):
        c = IntentClassifier()
        c.register(
            IntentPattern(
                command="/files",
                keywords=["files"],
                phrases=["generated files"],
            )
        )
        result = c.classify("show me the generated files")
        # phrase(0.4) + keyword(0.2) = 0.6 > 0.5 threshold
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/files"
        assert result.confidence >= 0.6

    def test_regex_match_extracts_args(self):
        c = IntentClassifier()
        c.register(
            IntentPattern(
                command="/deploy",
                regex_patterns=[r"deploy\s+(?:stage\s+)?\d+"],
                arg_extractor=lambda t: " ".join(__import__("re").findall(r"\d+", t)),
            )
        )
        result = c.classify("deploy stage 3")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/deploy"
        assert result.args == "3"

    def test_file_read_detection(self):
        c = IntentClassifier()
        result = c.classify("read artifacts from ~/docs/requirements")
        assert result.kind == IntentKind.READ_FILES
        assert result.command == "__read_files"
        assert "docs/requirements" in result.args

    def test_file_load_detection(self):
        c = IntentClassifier()
        result = c.classify("load files from /tmp/specs")
        assert result.kind == IntentKind.READ_FILES
        assert "/tmp/specs" in result.args

    def test_file_import_detection(self):
        c = IntentClassifier()
        result = c.classify("import documents from ./design")
        assert result.kind == IntentKind.READ_FILES

    def test_no_false_file_read(self):
        """'I read a book yesterday' should NOT match file read pattern."""
        c = IntentClassifier()
        result = c.classify("I read a book yesterday")
        assert result.kind == IntentKind.CONVERSATIONAL

    def test_ai_markdown_fenced_json(self):
        """AI response with markdown fences should still parse."""
        c = _make_classifier_with_ai('```json\n{"command": "/status", "args": "", "is_command": true}\n```')
        # Register a keyword with partial signal
        c.register(IntentPattern(command="/status", keywords=["status"], min_confidence=0.5))
        result = c.classify("what's the status")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/status"

    def test_ai_network_error_falls_back(self):
        """Network errors should fall through to keyword fallback."""
        provider = MagicMock()
        provider.chat.side_effect = ConnectionError("timeout")
        c = IntentClassifier(ai_provider=provider)
        c.add_command_def(CommandDef("/open", "Show open items"))
        c.register(
            IntentPattern(
                command="/open",
                keywords=["open"],
                phrases=["open items"],
            )
        )
        result = c.classify("what are the open items")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/open"


# ======================================================================
# TestDiscoveryIntents — discovery session factory
# ======================================================================


class TestDiscoveryIntents:
    """Tests for the discovery session classifier (keyword fallback path)."""

    def test_open_items(self):
        c = build_discovery_classifier()
        result = c.classify("What are the open items?")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/open"

    def test_status(self):
        c = build_discovery_classifier()
        result = c.classify("Where do we stand?")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/status"

    def test_summary(self):
        c = build_discovery_classifier()
        result = c.classify("Give me a summary")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/summary"

    def test_conversational_feedback(self):
        """Design feedback should NOT be classified as a command."""
        c = build_discovery_classifier()
        result = c.classify("I don't like the database choice, change it to PostgreSQL")
        assert result.kind == IntentKind.CONVERSATIONAL

    def test_why_command(self):
        c = build_discovery_classifier()
        result = c.classify("Why did we choose Cosmos DB?")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/why"
        assert "Cosmos DB" in result.args

    def test_restart(self):
        c = build_discovery_classifier()
        result = c.classify("let's start over")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/restart"

    def test_unresolved(self):
        c = build_discovery_classifier()
        result = c.classify("What's still unresolved?")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/open"


# ======================================================================
# TestDeployIntents — deploy session factory
# ======================================================================


class TestDeployIntents:
    """Tests for the deploy session classifier (keyword fallback path)."""

    def test_deploy_stage_3(self):
        c = build_deploy_classifier()
        result = c.classify("deploy stage 3")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/deploy"
        assert "3" in result.args

    def test_deploy_all(self):
        c = build_deploy_classifier()
        result = c.classify("deploy all stages")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/deploy"

    def test_rollback_stage_2(self):
        c = build_deploy_classifier()
        result = c.classify("rollback stage 2")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/rollback"
        assert "2" in result.args

    def test_deploy_stages_3_and_4(self):
        c = build_deploy_classifier()
        result = c.classify("deploy stages 3 and 4")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/deploy"
        assert "3" in result.args
        assert "4" in result.args

    def test_deployment_status(self):
        c = build_deploy_classifier()
        result = c.classify("what's the deployment status")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/status"

    def test_describe_stage(self):
        c = build_deploy_classifier()
        result = c.classify("describe stage 3")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/describe"
        assert "3" in result.args

    def test_whats_being_deployed(self):
        c = build_deploy_classifier()
        result = c.classify("what's being deployed in stage 2")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/describe"
        assert "2" in result.args

    def test_rollback_all(self):
        c = build_deploy_classifier()
        result = c.classify("roll back all")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/rollback"
        assert "all" in result.args

    def test_undo_stage(self):
        c = build_deploy_classifier()
        result = c.classify("undo stage 1")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/rollback"
        assert "1" in result.args


# ======================================================================
# TestBuildIntents — build session factory
# ======================================================================


class TestBuildIntents:
    """Tests for the build session classifier (keyword fallback path)."""

    def test_generated_files(self):
        c = build_build_classifier()
        result = c.classify("show me the generated files")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/files"

    def test_build_status(self):
        c = build_build_classifier()
        result = c.classify("what's the build status")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/status"

    def test_describe_stage(self):
        c = build_build_classifier()
        result = c.classify("describe stage 1")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/describe"
        assert "1" in result.args

    def test_conversational_feedback(self):
        """Build feedback should NOT be classified as a command."""
        c = build_build_classifier()
        result = c.classify("I don't like the key vault config")
        assert result.kind == IntentKind.CONVERSATIONAL

    def test_show_policy(self):
        c = build_build_classifier()
        result = c.classify("show policy status")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/policy"


# ======================================================================
# TestBacklogIntents — backlog session factory
# ======================================================================


class TestBacklogIntents:
    """Tests for the backlog session classifier (keyword fallback path)."""

    def test_show_all_items(self):
        c = build_backlog_classifier()
        result = c.classify("show all items")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/list"

    def test_push_item(self):
        c = build_backlog_classifier()
        result = c.classify("push item 3")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/push"
        assert "3" in result.args

    def test_add_story_is_conversational(self):
        """'add a story for API rate limiting' should fall through to AI mutation."""
        c = build_backlog_classifier()
        result = c.classify("add a story for API rate limiting")
        assert result.kind == IntentKind.CONVERSATIONAL

    def test_show_item(self):
        c = build_backlog_classifier()
        result = c.classify("show me item 2")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/show"
        assert "2" in result.args

    def test_remove_item(self):
        c = build_backlog_classifier()
        result = c.classify("remove item 5")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/remove"
        assert "5" in result.args

    def test_save_backlog(self):
        c = build_backlog_classifier()
        result = c.classify("save the backlog")
        assert result.kind == IntentKind.COMMAND
        assert result.command == "/save"


# ======================================================================
# TestFileReadDetection — cross-session file reading
# ======================================================================


class TestFileReadDetection:
    """Tests for the file-read regex detection."""

    def test_read_artifacts_from_path(self):
        c = IntentClassifier()
        result = c.classify("Read artifacts from ~/docs/requirements")
        assert result.kind == IntentKind.READ_FILES
        assert "docs/requirements" in result.args

    def test_load_files_from_path(self):
        c = IntentClassifier()
        result = c.classify("Load files from /tmp/specs")
        assert result.kind == IntentKind.READ_FILES
        assert "/tmp/specs" in result.args

    def test_no_false_read(self):
        """'I read a book yesterday' should NOT match."""
        c = IntentClassifier()
        result = c.classify("I read a book yesterday")
        assert result.kind == IntentKind.CONVERSATIONAL

    def test_import_documents(self):
        c = IntentClassifier()
        result = c.classify("import documents from ./specs")
        assert result.kind == IntentKind.READ_FILES
        assert "specs" in result.args


# ======================================================================
# TestReadFilesForSession — file reading helper
# ======================================================================


class TestReadFilesForSession:
    """Tests for the read_files_for_session helper."""

    def test_nonexistent_path(self, tmp_path):
        output = []
        text, images = read_files_for_session(
            str(tmp_path / "nonexistent"),
            str(tmp_path),
            output.append,
        )
        assert text == ""
        assert images == []
        assert any("not found" in o for o in output)

    def test_read_text_file(self, tmp_path):
        (tmp_path / "hello.txt").write_text("Hello world", encoding="utf-8")
        output = []
        text, images = read_files_for_session(
            str(tmp_path / "hello.txt"),
            str(tmp_path),
            output.append,
        )
        assert "Hello world" in text
        assert images == []

    def test_read_directory(self, tmp_path):
        (tmp_path / "a.txt").write_text("File A", encoding="utf-8")
        (tmp_path / "b.txt").write_text("File B", encoding="utf-8")
        output = []
        text, images = read_files_for_session(
            str(tmp_path),
            str(tmp_path),
            output.append,
        )
        assert "File A" in text
        assert "File B" in text

    def test_read_skips_hidden_files(self, tmp_path):
        (tmp_path / ".hidden").write_text("secret", encoding="utf-8")
        (tmp_path / "visible.txt").write_text("visible", encoding="utf-8")
        output = []
        text, images = read_files_for_session(
            str(tmp_path),
            str(tmp_path),
            output.append,
        )
        assert "visible" in text
        assert "secret" not in text

    def test_relative_path_resolution(self, tmp_path):
        (tmp_path / "specs").mkdir()
        (tmp_path / "specs" / "req.txt").write_text("Requirements", encoding="utf-8")
        output = []
        text, images = read_files_for_session(
            "specs",
            str(tmp_path),
            output.append,
        )
        assert "Requirements" in text


# ======================================================================
# TestAIClassificationPrompt — prompt construction
# ======================================================================


class TestAIClassificationPrompt:
    """Tests that the AI classification prompt is built correctly."""

    def test_prompt_includes_commands(self):
        c = IntentClassifier()
        c.add_command_def(CommandDef("/open", "Show open items"))
        c.add_command_def(CommandDef("/deploy", "Deploy stage", has_args=True, arg_description="N"))
        prompt = c._build_classification_prompt()
        assert "/open" in prompt
        assert "Show open items" in prompt
        assert "/deploy" in prompt
        assert "<N>" in prompt

    def test_prompt_includes_special_commands(self):
        c = IntentClassifier()
        c.add_command_def(CommandDef("/status", "Show status"))
        prompt = c._build_classification_prompt()
        assert "__prompt_context" in prompt
        assert "__read_files" in prompt
