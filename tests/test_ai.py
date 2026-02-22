"""Tests for azext_prototype.ai — factory, providers, validation."""

import pytest
from unittest.mock import MagicMock, patch

from knack.util import CLIError

from azext_prototype.ai.factory import (
    ALLOWED_PROVIDERS,
    BLOCKED_PROVIDERS,
    create_ai_provider,
)
from azext_prototype.ai.provider import AIMessage, AIResponse
from azext_prototype.ai.github_models import GitHubModelsProvider
from azext_prototype.ai.azure_openai import AzureOpenAIProvider


class TestAIProviderFactory:
    """Test create_ai_provider() factory function."""

    @patch("azext_prototype.ai.factory.GitHubModelsProvider")
    @patch("azext_prototype.auth.github_auth.GitHubAuthManager")
    def test_create_github_models(self, mock_auth_cls, mock_provider_cls, sample_config):
        sample_config["ai"]["provider"] = "github-models"
        sample_config["ai"]["model"] = "gpt-4o"
        mock_auth = MagicMock()
        mock_auth.get_token.return_value = "ghp_test"
        mock_auth_cls.return_value = mock_auth
        mock_provider_cls.return_value = MagicMock(spec=GitHubModelsProvider)

        create_ai_provider(sample_config)
        mock_provider_cls.assert_called_once()

    @patch("azext_prototype.ai.factory.AzureOpenAIProvider")
    def test_create_azure_openai(self, mock_cls, sample_config):
        sample_config["ai"]["provider"] = "azure-openai"
        sample_config["ai"]["model"] = "gpt-4o"
        sample_config["ai"]["azure_openai"] = {
            "endpoint": "https://myres.openai.azure.com/",
            "deployment": "gpt-4o",
        }
        mock_cls.return_value = MagicMock(spec=AzureOpenAIProvider)

        create_ai_provider(sample_config)
        mock_cls.assert_called_once()

    def test_blocked_provider_raises(self, sample_config):
        for blocked in BLOCKED_PROVIDERS:
            sample_config["ai"]["provider"] = blocked
            with pytest.raises(CLIError, match="not permitted"):
                create_ai_provider(sample_config)

    def test_unknown_provider_raises(self, sample_config):
        sample_config["ai"]["provider"] = "totally-made-up"
        with pytest.raises(CLIError, match="Unknown"):
            create_ai_provider(sample_config)

    def test_allowed_providers_set(self):
        assert "github-models" in ALLOWED_PROVIDERS
        assert "azure-openai" in ALLOWED_PROVIDERS
        assert "copilot" in ALLOWED_PROVIDERS

    def test_blocked_providers_set(self):
        expected_blocked = {"openai", "anthropic", "google", "aws-bedrock", "cohere"}
        assert expected_blocked.issubset(BLOCKED_PROVIDERS)


class TestModelProviderValidation:
    """Test that Claude models are rejected on non-copilot providers."""

    def test_claude_on_github_models_raises(self, sample_config):
        sample_config["ai"]["provider"] = "github-models"
        sample_config["ai"]["model"] = "claude-sonnet-4.5"
        with pytest.raises(CLIError, match="not available.*github-models"):
            create_ai_provider(sample_config)

    def test_claude_on_azure_openai_raises(self, sample_config):
        sample_config["ai"]["provider"] = "azure-openai"
        sample_config["ai"]["model"] = "claude-opus-4.5"
        sample_config["ai"]["azure_openai"] = {
            "endpoint": "https://test.openai.azure.com/",
            "deployment": "gpt-4o",
        }
        with pytest.raises(CLIError, match="not available.*azure-openai"):
            create_ai_provider(sample_config)

    @patch("azext_prototype.ai.copilot_provider.CopilotProvider")
    def test_claude_on_copilot_succeeds(
        self, mock_provider_cls, sample_config
    ):
        sample_config["ai"]["provider"] = "copilot"
        sample_config["ai"]["model"] = "claude-sonnet-4.5"
        mock_provider_cls.return_value = MagicMock()

        create_ai_provider(sample_config)  # Should not raise
        mock_provider_cls.assert_called_once()

    @patch("azext_prototype.ai.github_models.GitHubModelsProvider")
    @patch("azext_prototype.auth.github_auth.GitHubAuthManager")
    def test_gpt_on_github_models_succeeds(self, mock_auth_cls, mock_provider_cls, sample_config):
        sample_config["ai"]["provider"] = "github-models"
        sample_config["ai"]["model"] = "gpt-4o"
        mock_auth = MagicMock()
        mock_auth.get_token.return_value = "ghp_test"
        mock_auth_cls.return_value = mock_auth
        mock_provider_cls.return_value = MagicMock()

        create_ai_provider(sample_config)  # Should not raise

    def test_error_message_suggests_fix(self, sample_config):
        sample_config["ai"]["provider"] = "github-models"
        sample_config["ai"]["model"] = "claude-haiku-4.5"
        with pytest.raises(CLIError, match="az prototype config set"):
            create_ai_provider(sample_config)


class TestAIMessage:
    """Test AIMessage dataclass."""

    def test_basic_creation(self):
        msg = AIMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.metadata == {}

    def test_with_metadata(self):
        msg = AIMessage(role="assistant", content="Hi", metadata={"source": "test"})
        assert msg.metadata["source"] == "test"


class TestAIResponse:
    """Test AIResponse dataclass."""

    def test_basic_creation(self):
        resp = AIResponse(content="Result", model="gpt-4o")
        assert resp.content == "Result"
        assert resp.model == "gpt-4o"
        assert resp.usage == {}
        assert resp.finish_reason == "stop"

    def test_with_usage(self):
        resp = AIResponse(
            content="Result",
            model="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 20},
            finish_reason="stop",
        )
        assert resp.usage["prompt_tokens"] == 10
        assert resp.finish_reason == "stop"


class TestAzureOpenAIEndpointValidation:
    """Test endpoint validation in AzureOpenAIProvider."""

    def test_valid_endpoint_pattern(self):
        import re
        pattern = re.compile(r"^https://[a-z0-9-]+\.openai\.azure\.com/?$")
        valid = [
            "https://my-resource.openai.azure.com/",
            "https://my-resource.openai.azure.com",
            "https://a1b2c3.openai.azure.com/",
            "https://my-long-resource-name.openai.azure.com/",
        ]
        for ep in valid:
            assert pattern.match(ep), f"Expected valid: {ep}"

    def test_invalid_endpoint_pattern(self):
        import re
        pattern = re.compile(r"^https://[a-z0-9-]+\.openai\.azure\.com/?$")
        invalid = [
            "https://api.openai.com/v1",
            "https://example.com",
            "http://my-resource.openai.azure.com/",  # http not https
            "https://my resource.openai.azure.com/",  # space
        ]
        for ep in invalid:
            assert not pattern.match(ep), f"Expected invalid: {ep}"


class TestGitHubModelsProvider:
    """Test GitHubModelsProvider initialization."""

    @patch("openai.OpenAI")
    def test_init_with_token(self, mock_openai_cls):
        provider = GitHubModelsProvider(
            model="gpt-4o",
            token="ghp_test123",
        )
        assert provider._model == "gpt-4o"
        mock_openai_cls.assert_called_once()

    @patch("openai.OpenAI")
    def test_default_model(self, mock_openai_cls):
        provider = GitHubModelsProvider(token="ghp_test123")
        assert provider._model == "gpt-4o"


class TestCopilotProvider:
    """Test CopilotProvider initialization."""

    def test_init_defaults(self):
        from azext_prototype.ai.copilot_provider import CopilotProvider

        provider = CopilotProvider()
        assert provider._model == "claude-sonnet-4"  # Copilot default

    def test_custom_model(self):
        from azext_prototype.ai.copilot_provider import CopilotProvider

        provider = CopilotProvider(model="gpt-4o-mini")
        assert provider._model == "gpt-4o-mini"

    def test_provider_name(self):
        from azext_prototype.ai.copilot_provider import CopilotProvider

        provider = CopilotProvider()
        assert provider.provider_name == "copilot"

    def test_list_models(self):
        from azext_prototype.ai.copilot_provider import CopilotProvider

        provider = CopilotProvider()
        models = provider.list_models()
        assert len(models) >= 2
        assert any(m["id"] == "claude-sonnet-4" for m in models)

    def test_messages_to_dicts(self):
        from azext_prototype.ai.copilot_provider import CopilotProvider

        msgs = [
            AIMessage(role="system", content="Be helpful"),
            AIMessage(role="user", content="Hello"),
        ]
        dicts = CopilotProvider._messages_to_dicts(msgs)
        assert dicts == [
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "Hello"},
        ]


class TestCopilotFactory:
    """Test that the factory can create a copilot provider."""

    @patch("azext_prototype.ai.copilot_provider.CopilotProvider")
    def test_create_copilot(self, mock_provider_cls, sample_config):
        sample_config["ai"]["provider"] = "copilot"
        mock_provider_cls.return_value = MagicMock()

        create_ai_provider(sample_config)
        mock_provider_cls.assert_called_once()

    @patch("azext_prototype.ai.copilot_provider.CopilotProvider")
    def test_create_copilot_passes_model(self, mock_provider_cls, sample_config):
        sample_config["ai"]["provider"] = "copilot"
        sample_config["ai"]["model"] = "gpt-4o"
        mock_provider_cls.return_value = MagicMock()

        create_ai_provider(sample_config)
        call_kwargs = mock_provider_cls.call_args
        assert call_kwargs[1].get("model") == "gpt-4o"


class TestDefaultModel:
    """Test that the default model is claude-sonnet-4.5 via the Copilot provider."""

    def test_config_default(self):
        from azext_prototype.config import DEFAULT_CONFIG

        assert DEFAULT_CONFIG["ai"]["model"] == "claude-sonnet-4.5"

    def test_copilot_default(self):
        from azext_prototype.ai.copilot_provider import CopilotProvider

        assert CopilotProvider.DEFAULT_MODEL == "claude-sonnet-4"

    def test_github_models_default(self):
        assert GitHubModelsProvider.DEFAULT_MODEL == "gpt-4o"
