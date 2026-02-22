"""Tests for AI providers, auth modules, cost_analyst, qa_engineer, and loader."""

from unittest.mock import MagicMock, patch

import pytest
from knack.util import CLIError

from azext_prototype.ai.provider import AIMessage, AIResponse


# ======================================================================
# AzureOpenAIProvider — extended
# ======================================================================


class TestAzureOpenAIProviderExtended:
    """Extended tests for AzureOpenAIProvider."""

    def test_validate_endpoint_empty_raises(self):
        from azext_prototype.ai.azure_openai import AzureOpenAIProvider
        with pytest.raises(CLIError, match="endpoint is required"):
            AzureOpenAIProvider._validate_endpoint("")

    def test_validate_endpoint_blocked(self):
        from azext_prototype.ai.azure_openai import AzureOpenAIProvider
        with pytest.raises(CLIError, match="not permitted"):
            AzureOpenAIProvider._validate_endpoint("https://api.openai.com/v1")

    def test_validate_endpoint_invalid_pattern(self):
        from azext_prototype.ai.azure_openai import AzureOpenAIProvider
        with pytest.raises(CLIError, match="Invalid Azure OpenAI"):
            AzureOpenAIProvider._validate_endpoint("https://example.com/openai")

    def test_validate_endpoint_valid(self):
        from azext_prototype.ai.azure_openai import AzureOpenAIProvider
        # Should not raise
        AzureOpenAIProvider._validate_endpoint("https://my-resource.openai.azure.com/")

    @patch("azext_prototype.ai.azure_openai.AzureOpenAIProvider._create_client")
    def test_chat(self, mock_create):
        from azext_prototype.ai.azure_openai import AzureOpenAIProvider

        mock_client = MagicMock()
        mock_create.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello!"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.model = "gpt-4o"
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 20
        mock_response.usage.total_tokens = 30
        mock_client.chat.completions.create.return_value = mock_response

        provider = AzureOpenAIProvider("https://test.openai.azure.com/")
        result = provider.chat([AIMessage(role="user", content="Hi")])
        assert result.content == "Hello!"
        assert result.model == "gpt-4o"

    @patch("azext_prototype.ai.azure_openai.AzureOpenAIProvider._create_client")
    def test_chat_with_response_format(self, mock_create):
        from azext_prototype.ai.azure_openai import AzureOpenAIProvider

        mock_client = MagicMock()
        mock_create.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"key": "value"}'
        mock_response.choices[0].finish_reason = "stop"
        mock_response.model = "gpt-4o"
        mock_response.usage = None
        mock_client.chat.completions.create.return_value = mock_response

        provider = AzureOpenAIProvider("https://test.openai.azure.com/")
        result = provider.chat(
            [AIMessage(role="user", content="json")],
            response_format={"type": "json_object"},
        )
        assert result.content == '{"key": "value"}'
        assert result.usage == {}

    @patch("azext_prototype.ai.azure_openai.AzureOpenAIProvider._create_client")
    def test_chat_error_raises(self, mock_create):
        from azext_prototype.ai.azure_openai import AzureOpenAIProvider

        mock_client = MagicMock()
        mock_create.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("API Error")

        provider = AzureOpenAIProvider("https://test.openai.azure.com/")
        with pytest.raises(CLIError, match="Azure OpenAI request failed"):
            provider.chat([AIMessage(role="user", content="Hi")])

    @patch("azext_prototype.ai.azure_openai.AzureOpenAIProvider._create_client")
    def test_stream_chat(self, mock_create):
        from azext_prototype.ai.azure_openai import AzureOpenAIProvider

        mock_client = MagicMock()
        mock_create.return_value = mock_client

        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta.content = "Hello"
        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta.content = " World"
        mock_client.chat.completions.create.return_value = iter([chunk1, chunk2])

        provider = AzureOpenAIProvider("https://test.openai.azure.com/")
        chunks = list(provider.stream_chat([AIMessage(role="user", content="Hi")]))
        assert chunks == ["Hello", " World"]

    @patch("azext_prototype.ai.azure_openai.AzureOpenAIProvider._create_client")
    def test_stream_chat_error(self, mock_create):
        from azext_prototype.ai.azure_openai import AzureOpenAIProvider

        mock_client = MagicMock()
        mock_create.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("Stream error")

        provider = AzureOpenAIProvider("https://test.openai.azure.com/")
        with pytest.raises(CLIError, match="Streaming failed"):
            list(provider.stream_chat([AIMessage(role="user", content="Hi")]))

    @patch("azext_prototype.ai.azure_openai.AzureOpenAIProvider._create_client")
    def test_list_models(self, mock_create):
        from azext_prototype.ai.azure_openai import AzureOpenAIProvider
        mock_create.return_value = MagicMock()

        provider = AzureOpenAIProvider("https://test.openai.azure.com/", deployment="gpt-4o")
        models = provider.list_models()
        assert len(models) == 1
        assert models[0]["id"] == "gpt-4o"

    @patch("azext_prototype.ai.azure_openai.AzureOpenAIProvider._create_client")
    def test_properties(self, mock_create):
        from azext_prototype.ai.azure_openai import AzureOpenAIProvider
        mock_create.return_value = MagicMock()

        provider = AzureOpenAIProvider("https://test.openai.azure.com/")
        assert provider.provider_name == "azure-openai"
        assert provider.default_model == "gpt-4o"

    def test_create_client_missing_azure_identity(self):
        from azext_prototype.ai.azure_openai import AzureOpenAIProvider

        with patch.dict("sys.modules", {"azure.identity": None}):
            with patch("azext_prototype.ai.azure_openai.AzureOpenAIProvider._validate_endpoint"):
                # We need to mock out the inside of _create_client
                with pytest.raises((CLIError, ImportError)):
                    AzureOpenAIProvider("https://test.openai.azure.com/")


# ======================================================================
# GitHubModelsProvider — extended
# ======================================================================


class TestGitHubModelsProviderExtended:

    @patch("azext_prototype.ai.github_models.GitHubModelsProvider._create_client")
    def test_chat(self, mock_create):
        from azext_prototype.ai.github_models import GitHubModelsProvider

        mock_client = MagicMock()
        mock_create.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Response"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.model = "gpt-4o"
        mock_response.usage.prompt_tokens = 5
        mock_response.usage.completion_tokens = 10
        mock_response.usage.total_tokens = 15
        mock_client.chat.completions.create.return_value = mock_response

        provider = GitHubModelsProvider("fake-token")
        result = provider.chat([AIMessage(role="user", content="Hi")])
        assert result.content == "Response"

    @patch("azext_prototype.ai.github_models.GitHubModelsProvider._create_client")
    def test_chat_error(self, mock_create):
        from azext_prototype.ai.github_models import GitHubModelsProvider

        mock_client = MagicMock()
        mock_create.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("fail")

        provider = GitHubModelsProvider("fake-token")
        with pytest.raises(CLIError, match="Failed to get response"):
            provider.chat([AIMessage(role="user", content="Hi")])

    @patch("azext_prototype.ai.github_models.GitHubModelsProvider._create_client")
    def test_stream_chat(self, mock_create):
        from azext_prototype.ai.github_models import GitHubModelsProvider

        mock_client = MagicMock()
        mock_create.return_value = mock_client

        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = "chunk1"
        mock_client.chat.completions.create.return_value = iter([chunk])

        provider = GitHubModelsProvider("fake-token")
        result = list(provider.stream_chat([AIMessage(role="user", content="Hi")]))
        assert result == ["chunk1"]

    @patch("azext_prototype.ai.github_models.GitHubModelsProvider._create_client")
    def test_stream_chat_error(self, mock_create):
        from azext_prototype.ai.github_models import GitHubModelsProvider

        mock_client = MagicMock()
        mock_create.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("stream fail")

        provider = GitHubModelsProvider("fake-token")
        with pytest.raises(CLIError, match="Streaming failed"):
            list(provider.stream_chat([AIMessage(role="user", content="Hi")]))

    @patch("azext_prototype.ai.github_models.GitHubModelsProvider._create_client")
    def test_list_models(self, mock_create):
        from azext_prototype.ai.github_models import GitHubModelsProvider
        mock_create.return_value = MagicMock()

        provider = GitHubModelsProvider("fake-token")
        models = provider.list_models()
        assert len(models) >= 4
        model_ids = [m["id"] for m in models]
        assert "openai/gpt-4o" in model_ids

    @patch("azext_prototype.ai.github_models.GitHubModelsProvider._create_client")
    def test_properties(self, mock_create):
        from azext_prototype.ai.github_models import GitHubModelsProvider
        mock_create.return_value = MagicMock()

        provider = GitHubModelsProvider("fake-token", model="o1-mini")
        assert provider.provider_name == "github-models"
        assert provider.default_model == "o1-mini"

    @patch("azext_prototype.ai.github_models.GitHubModelsProvider._create_client")
    def test_chat_with_response_format(self, mock_create):
        from azext_prototype.ai.github_models import GitHubModelsProvider

        mock_client = MagicMock()
        mock_create.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "{}"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.model = "gpt-4o"
        mock_response.usage = None
        mock_client.chat.completions.create.return_value = mock_response

        provider = GitHubModelsProvider("fake-token")
        result = provider.chat(
            [AIMessage(role="user", content="json")],
            response_format={"type": "json_object"},
        )
        assert result.usage == {}


# ======================================================================
# CopilotProvider — extended
# ======================================================================


class TestCopilotProviderExtended:
    """Tests for direct-HTTP CopilotProvider.

    We mock ``get_copilot_token`` and ``requests.post`` so tests
    never hit the real Copilot API.
    """

    def _make_provider(self, **kwargs):
        from azext_prototype.ai.copilot_provider import CopilotProvider
        return CopilotProvider(**kwargs)

    def _mock_ok_response(self, content="Copilot says hi"):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
        return resp

    @patch("azext_prototype.ai.copilot_provider.get_copilot_token", return_value="gho_test_token")
    @patch("azext_prototype.ai.copilot_provider.requests.post")
    def test_chat(self, mock_post, _mock_token):
        from azext_prototype.ai.copilot_provider import CopilotProvider

        mock_post.return_value = self._mock_ok_response("Copilot says hi")
        provider = self._make_provider()

        result = provider.chat([AIMessage(role="user", content="Hi")])

        assert result.content == "Copilot says hi"
        assert result.model == CopilotProvider.DEFAULT_MODEL
        mock_post.assert_called_once()

    @patch("azext_prototype.ai.copilot_provider.get_copilot_token", return_value="gho_test_token")
    @patch("azext_prototype.ai.copilot_provider.requests.post")
    def test_chat_sends_correct_payload(self, mock_post, _mock_token):
        mock_post.return_value = self._mock_ok_response()
        provider = self._make_provider(model="gpt-4o")

        provider.chat(
            [AIMessage(role="system", content="Be helpful"),
             AIMessage(role="user", content="Hello")],
            temperature=0.5,
            max_tokens=2048,
        )

        payload = mock_post.call_args[1]["json"]
        assert payload["model"] == "gpt-4o"
        assert payload["temperature"] == 0.5
        assert payload["max_tokens"] == 2048
        assert payload["messages"] == [
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "Hello"},
        ]

    @patch("azext_prototype.ai.copilot_provider.get_copilot_token", return_value="gho_test_token")
    @patch("azext_prototype.ai.copilot_provider.requests.post")
    def test_chat_error(self, mock_post, _mock_token):
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "Internal Server Error"
        mock_post.return_value = resp
        provider = self._make_provider()

        with pytest.raises(CLIError, match="HTTP 500"):
            provider.chat([AIMessage(role="user", content="Hi")])

    @patch("azext_prototype.ai.copilot_provider.get_copilot_token", return_value="gho_test_token")
    @patch("azext_prototype.ai.copilot_provider.requests.post")
    def test_chat_timeout(self, mock_post, _mock_token):
        import requests as req
        mock_post.side_effect = req.Timeout()
        provider = self._make_provider()

        with pytest.raises(CLIError, match="timed out"):
            provider.chat([AIMessage(role="user", content="Hi")])

    @patch("azext_prototype.ai.copilot_provider.get_copilot_token", return_value="gho_test_token")
    @patch("azext_prototype.ai.copilot_provider.requests.post")
    def test_chat_retries_on_401(self, mock_post, _mock_token):
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_ok = self._mock_ok_response("retried")
        mock_post.side_effect = [resp_401, resp_ok]
        provider = self._make_provider()

        result = provider.chat([AIMessage(role="user", content="Hi")])
        assert result.content == "retried"
        assert mock_post.call_count == 2

    @patch("azext_prototype.ai.copilot_provider.get_copilot_token", return_value="gho_test_token")
    @patch("azext_prototype.ai.copilot_provider.requests.post")
    def test_stream_chat(self, mock_post, _mock_token):
        """stream_chat yields SSE chunks."""
        lines = [
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            "data: [DONE]",
        ]
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.iter_lines.return_value = iter(lines)
        mock_post.return_value = resp
        provider = self._make_provider()

        result = list(provider.stream_chat([AIMessage(role="user", content="Hi")]))
        assert result == ["Hello", " world"]

    @patch("azext_prototype.ai.copilot_provider.get_copilot_token", return_value="gho_test_token")
    @patch("azext_prototype.ai.copilot_provider.requests.post")
    def test_stream_chat_error(self, mock_post, _mock_token):
        import requests as req
        mock_post.side_effect = req.Timeout()
        provider = self._make_provider()

        with pytest.raises(CLIError, match="streaming timed out"):
            list(provider.stream_chat([AIMessage(role="user", content="Hi")]))

    def test_list_models(self):
        provider = self._make_provider()
        models = provider.list_models()

        assert len(models) >= 2
        ids = [m["id"] for m in models]
        assert "claude-sonnet-4" in ids

    def test_properties(self):
        provider = self._make_provider(model="gpt-4o-mini")
        assert provider.provider_name == "copilot"
        assert provider.default_model == "gpt-4o-mini"


# ======================================================================
# Auth — GitHubAuthManager
# ======================================================================


class TestGitHubAuthManagerExtended:

    def test_check_gh_installed_success(self):
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mgr = GitHubAuthManager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            mgr._check_gh_installed()  # Should not raise

    def test_check_gh_installed_missing(self):
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mgr = GitHubAuthManager()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(CLIError, match="not installed"):
                mgr._check_gh_installed()

    @patch("subprocess.run")
    def test_ensure_authenticated_already(self, mock_run):
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mock_run.side_effect = [
            MagicMock(returncode=0),  # gh --version
            MagicMock(returncode=0, stdout="token"),  # auth status
            MagicMock(returncode=0, stdout='{"login":"user1"}'),  # api user
        ]

        mgr = GitHubAuthManager()
        result = mgr.ensure_authenticated()
        assert result["login"] == "user1"

    @patch("subprocess.run")
    def test_get_token(self, mock_run):
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mock_run.return_value = MagicMock(returncode=0, stdout="ghp_abc123\n", stderr="")
        mgr = GitHubAuthManager()
        token = mgr.get_token()
        assert token == "ghp_abc123"

    @patch("subprocess.run")
    def test_get_token_cached(self, mock_run):
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mgr = GitHubAuthManager()
        mgr._token = "cached-token"
        token = mgr.get_token()
        assert token == "cached-token"
        mock_run.assert_not_called()

    def test_get_user_info_cached(self):
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mgr = GitHubAuthManager()
        mgr._user_info = {"login": "cached"}
        info = mgr.get_user_info()
        assert info["login"] == "cached"

    @patch("subprocess.run")
    def test_create_repo(self, mock_run):
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),  # repo create
            MagicMock(returncode=0, stdout='{"url":"https://github.com/user/repo"}', stderr=""),  # repo view
        ]

        mgr = GitHubAuthManager()
        result = mgr.create_repo("my-repo", private=True, description="test")
        assert result["url"] == "https://github.com/user/repo"

    @patch("subprocess.run")
    def test_clone_repo(self, mock_run):
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mock_run.return_value = MagicMock(returncode=0)
        mgr = GitHubAuthManager()
        result = mgr.clone_repo("user/repo", "/tmp/repo")
        assert result == "/tmp/repo"

    @patch("subprocess.run")
    def test_clone_repo_default_dir(self, mock_run):
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mock_run.return_value = MagicMock(returncode=0)
        mgr = GitHubAuthManager()
        result = mgr.clone_repo("user/repo")
        assert result == "repo"

    @patch("subprocess.run")
    def test_run_gh_error(self, mock_run):
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mock_run.return_value = MagicMock(returncode=1, stderr="permission denied", stdout="")
        mgr = GitHubAuthManager()
        with pytest.raises(CLIError, match="permission denied"):
            mgr._run_gh(["api", "user"])

    @patch("subprocess.run")
    def test_initiate_login_failure(self, mock_run):
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mock_run.return_value = MagicMock(returncode=1)
        mgr = GitHubAuthManager()
        with pytest.raises(CLIError, match="authentication failed"):
            mgr._initiate_login()


# ======================================================================
# Auth — CopilotLicenseValidator
# ======================================================================


class TestCopilotLicenseValidatorExtended:

    @patch("subprocess.run")
    def test_check_copilot_access_via_api(self, mock_run):
        from azext_prototype.auth.copilot_license import CopilotLicenseValidator
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mock_run.return_value = MagicMock(returncode=0, stdout='{"seats": [{"id": 1}]}')

        mock_auth = MagicMock(spec=GitHubAuthManager)
        validator = CopilotLicenseValidator(mock_auth)
        result = validator._check_copilot_access()
        assert result is not None
        assert result["plan"] == "business_or_enterprise"

    @patch("subprocess.run")
    def test_check_copilot_access_via_cli(self, mock_run):
        from azext_prototype.auth.copilot_license import CopilotLicenseValidator
        from azext_prototype.auth.github_auth import GitHubAuthManager

        # First call (API) fails, second call (CLI extension) succeeds
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout=""),  # API fails
            MagicMock(returncode=0, stdout=""),  # gh copilot --help succeeds
        ]

        mock_auth = MagicMock(spec=GitHubAuthManager)
        validator = CopilotLicenseValidator(mock_auth)
        result = validator._check_copilot_access()
        assert result is not None
        assert result["source"] == "gh_copilot_extension"

    @patch("subprocess.run")
    def test_check_copilot_access_none(self, mock_run):
        from azext_prototype.auth.copilot_license import CopilotLicenseValidator
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mock_run.return_value = MagicMock(returncode=1, stdout="")
        mock_auth = MagicMock(spec=GitHubAuthManager)
        validator = CopilotLicenseValidator(mock_auth)
        result = validator._check_copilot_access()
        assert result is None

    @patch("subprocess.run")
    def test_check_org_copilot_access(self, mock_run):
        from azext_prototype.auth.copilot_license import CopilotLicenseValidator
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="myorg\n"),  # list orgs
            MagicMock(returncode=0, stdout='{"id": 1}'),  # org seat
        ]

        mock_auth = MagicMock(spec=GitHubAuthManager)
        validator = CopilotLicenseValidator(mock_auth)
        result = validator._check_org_copilot_access()
        assert result is not None
        assert result["org"] == "myorg"

    @patch("subprocess.run")
    def test_check_org_copilot_no_orgs(self, mock_run):
        from azext_prototype.auth.copilot_license import CopilotLicenseValidator
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mock_run.return_value = MagicMock(returncode=1, stdout="")
        mock_auth = MagicMock(spec=GitHubAuthManager)
        validator = CopilotLicenseValidator(mock_auth)
        result = validator._check_org_copilot_access()
        assert result is None

    @patch("subprocess.run")
    def test_validate_license_success(self, mock_run):
        from azext_prototype.auth.copilot_license import CopilotLicenseValidator
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mock_auth = MagicMock(spec=GitHubAuthManager)
        mock_auth.get_token.return_value = "token"
        mock_auth.get_user_info.return_value = {"login": "testuser"}

        mock_run.return_value = MagicMock(returncode=0, stdout='{"seats": [{"id": 1}]}')

        validator = CopilotLicenseValidator(mock_auth)
        result = validator.validate_license()
        assert result["status"] == "active"

    @patch("subprocess.run")
    def test_validate_license_no_license_raises(self, mock_run):
        from azext_prototype.auth.copilot_license import CopilotLicenseValidator
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mock_auth = MagicMock(spec=GitHubAuthManager)
        mock_auth.get_token.return_value = "token"
        mock_auth.get_user_info.return_value = {"login": "testuser"}

        # All checks fail
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        validator = CopilotLicenseValidator(mock_auth)
        with pytest.raises(CLIError, match="No active GitHub Copilot license"):
            validator.validate_license()

    @patch("subprocess.run")
    def test_get_models_api_access(self, mock_run):
        from azext_prototype.auth.copilot_license import CopilotLicenseValidator
        from azext_prototype.auth.github_auth import GitHubAuthManager

        mock_run.return_value = MagicMock(returncode=0)
        mock_auth = MagicMock(spec=GitHubAuthManager)
        validator = CopilotLicenseValidator(mock_auth)
        result = validator.get_models_api_access()
        assert result["models_api"] == "accessible"


# ======================================================================
# CostAnalystAgent
# ======================================================================


class TestCostAnalystAgent:

    def test_instantiation(self):
        from azext_prototype.agents.builtin.cost_analyst import CostAnalystAgent
        agent = CostAnalystAgent()
        assert agent.name == "cost-analyst"
        assert agent._temperature == 0.0

    def test_parse_components_valid_json(self):
        from azext_prototype.agents.builtin.cost_analyst import CostAnalystAgent
        agent = CostAnalystAgent()

        result = agent._parse_components('[{"serviceName": "App Service"}]')
        assert len(result) == 1
        assert result[0]["serviceName"] == "App Service"

    def test_parse_components_markdown_fences(self):
        from azext_prototype.agents.builtin.cost_analyst import CostAnalystAgent
        agent = CostAnalystAgent()

        result = agent._parse_components('```json\n[{"serviceName": "AKS"}]\n```')
        assert len(result) == 1

    def test_parse_components_invalid(self):
        from azext_prototype.agents.builtin.cost_analyst import CostAnalystAgent
        agent = CostAnalystAgent()

        result = agent._parse_components("not json at all")
        assert result == []

    def test_arm_to_family_known(self):
        from azext_prototype.agents.builtin.cost_analyst import CostAnalystAgent
        assert CostAnalystAgent._arm_to_family("Microsoft.Web/sites") == "Compute"
        assert CostAnalystAgent._arm_to_family("Microsoft.Sql/servers") == "Databases"
        assert CostAnalystAgent._arm_to_family("Microsoft.Storage/storageAccounts") == "Storage"
        assert CostAnalystAgent._arm_to_family("Microsoft.Network/virtualNetworks") == "Networking"

    def test_arm_to_family_unknown(self):
        from azext_prototype.agents.builtin.cost_analyst import CostAnalystAgent
        assert CostAnalystAgent._arm_to_family("Microsoft.Unknown/thing") == "Compute"

    @patch("requests.get")
    def test_query_retail_price_success(self, mock_get):
        from azext_prototype.agents.builtin.cost_analyst import CostAnalystAgent
        agent = CostAnalystAgent()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "Items": [{"retailPrice": 100.0, "unitOfMeasure": "1 Hour", "meterName": "Compute"}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = agent._query_retail_price("Microsoft.Web/sites", "P1v3", "", "eastus")
        assert result["retailPrice"] == 100.0

    @patch("requests.get", side_effect=Exception("network error"))
    def test_query_retail_price_error(self, mock_get):
        from azext_prototype.agents.builtin.cost_analyst import CostAnalystAgent
        agent = CostAnalystAgent()

        result = agent._query_retail_price("Microsoft.Web", "P1v3", "", "eastus")
        assert result["retailPrice"] is None

    @patch("requests.get")
    def test_fetch_pricing(self, mock_get):
        from azext_prototype.agents.builtin.cost_analyst import CostAnalystAgent
        agent = CostAnalystAgent()

        mock_response = MagicMock()
        mock_response.json.return_value = {"Items": [{"retailPrice": 50.0, "unitOfMeasure": "1 Hour"}]}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        context = MagicMock()
        context.project_config = {"project": {"location": "eastus"}}

        components = [
            {"serviceName": "App Service", "armResourceType": "Microsoft.Web/sites",
             "skuSmall": "B1", "skuMedium": "P1v3", "skuLarge": "P3v3"}
        ]

        result = agent._fetch_pricing(components, context)
        assert len(result) == 3  # Small, Medium, Large
        assert all(r["region"] == "eastus" for r in result)

    def test_execute(self, mock_ai_provider):
        from azext_prototype.agents.builtin.cost_analyst import CostAnalystAgent
        from azext_prototype.agents.base import AgentContext

        agent = CostAnalystAgent()

        # First call: extraction response (JSON components)
        # Second call: report
        mock_ai_provider.chat.side_effect = [
            AIResponse(content='[{"serviceName":"App Service","armResourceType":"Microsoft.Web/sites","skuSmall":"B1","skuMedium":"P1v3","skuLarge":"P3v3"}]', model="gpt-4o"),
            AIResponse(content="| Service | Small | Medium | Large |", model="gpt-4o"),
        ]

        ctx = AgentContext(
            project_config={"project": {"location": "eastus"}},
            project_dir="/tmp",
            ai_provider=mock_ai_provider,
        )

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"Items": [{"retailPrice": 50.0}]}
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            result = agent.execute(ctx, "Estimate costs")
            assert "Service" in result.content


# ======================================================================
# QAEngineerAgent
# ======================================================================


class TestQAEngineerAgent:

    def test_instantiation(self):
        from azext_prototype.agents.builtin.qa_engineer import QAEngineerAgent
        agent = QAEngineerAgent()
        assert agent.name == "qa-engineer"

    def test_encode_image(self, tmp_path):
        from azext_prototype.agents.builtin.qa_engineer import QAEngineerAgent
        agent = QAEngineerAgent()

        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        encoded = agent._encode_image(str(img))
        import base64
        decoded = base64.b64decode(encoded)
        assert decoded[:4] == b"\x89PNG"

    def test_execute_with_image_success(self, tmp_path):
        from azext_prototype.agents.builtin.qa_engineer import QAEngineerAgent
        from azext_prototype.agents.base import AgentContext

        agent = QAEngineerAgent()
        img = tmp_path / "error.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 50)

        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Image analysis"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.model = "gpt-4o"
        mock_response.usage = None
        mock_provider._client = MagicMock()
        mock_provider._client.chat.completions.create.return_value = mock_response
        mock_provider.default_model = "gpt-4o"

        ctx = AgentContext(
            project_config={}, project_dir=str(tmp_path), ai_provider=mock_provider
        )

        result = agent.execute_with_image(ctx, "Analyze this error", str(img))
        assert result.content == "Image analysis"

    def test_execute_with_image_fallback(self, tmp_path):
        from azext_prototype.agents.builtin.qa_engineer import QAEngineerAgent
        from azext_prototype.agents.base import AgentContext

        agent = QAEngineerAgent()
        img = tmp_path / "error.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 50)

        mock_provider = MagicMock()
        mock_provider._client = MagicMock()
        mock_provider._client.chat.completions.create.side_effect = Exception("No vision")
        mock_provider.default_model = "gpt-4o"
        mock_provider.chat.return_value = AIResponse(content="Text fallback", model="gpt-4o")

        ctx = AgentContext(
            project_config={}, project_dir=str(tmp_path), ai_provider=mock_provider
        )

        result = agent.execute_with_image(ctx, "Analyze", str(img))
        assert result.content == "Text fallback"

    def test_execute_uses_base_class(self, mock_ai_provider):
        """QAEngineerAgent.execute() should use the base class default."""
        from azext_prototype.agents.builtin.qa_engineer import QAEngineerAgent
        from azext_prototype.agents.base import AgentContext

        agent = QAEngineerAgent()
        ctx = AgentContext(
            project_config={}, project_dir="/tmp", ai_provider=mock_ai_provider
        )
        result = agent.execute(ctx, "Analyze this error log")
        assert result.content == "Mock AI response content"


# ======================================================================
# Agent Loader — extended
# ======================================================================


class TestAgentLoaderExtended:

    def test_yaml_agent_execute(self, tmp_path, mock_ai_provider):
        from azext_prototype.agents.loader import YAMLAgent
        from azext_prototype.agents.base import AgentContext

        definition = {
            "name": "test-yaml",
            "description": "Test YAML agent",
            "capabilities": ["develop"],
            "system_prompt": "You are a test.",
            "examples": [
                {"user": "Hi", "assistant": "Hello!"},
            ],
            "role": "developer",
        }

        agent = YAMLAgent(definition)
        ctx = AgentContext(
            project_config={}, project_dir=str(tmp_path), ai_provider=mock_ai_provider
        )
        result = agent.execute(ctx, "Build something")
        assert result.content == "Mock AI response content"

    def test_yaml_agent_can_handle(self):
        from azext_prototype.agents.loader import YAMLAgent

        definition = {
            "name": "test-agent",
            "description": "Test",
            "capabilities": [],
            "system_prompt": "test",
            "role": "architect",
        }

        agent = YAMLAgent(definition)
        score = agent.can_handle("I need an architect to design something")
        assert score > 0.3

    def test_yaml_agent_can_handle_name_match(self):
        from azext_prototype.agents.loader import YAMLAgent

        definition = {
            "name": "data-processor",
            "description": "Processes data",
            "capabilities": [],
            "system_prompt": "test",
        }

        agent = YAMLAgent(definition)
        score = agent.can_handle("process the data")
        assert score > 0.3

    def test_load_agents_from_directory(self, tmp_path):
        from azext_prototype.agents.loader import load_agents_from_directory

        (tmp_path / "agent1.yaml").write_text(
            "name: agent1\ndescription: A\ncapabilities: []\nsystem_prompt: test\n",
            encoding="utf-8",
        )
        (tmp_path / "agent2.yaml").write_text(
            "name: agent2\ndescription: B\ncapabilities: []\nsystem_prompt: test\n",
            encoding="utf-8",
        )
        (tmp_path / "_skip.py").write_text("# skipped", encoding="utf-8")

        agents = load_agents_from_directory(str(tmp_path))
        assert len(agents) == 2

    def test_load_agents_from_nonexistent_dir(self, tmp_path):
        from azext_prototype.agents.loader import load_agents_from_directory
        agents = load_agents_from_directory(str(tmp_path / "nonexistent"))
        assert agents == []

    def test_load_agents_handles_invalid_files(self, tmp_path):
        from azext_prototype.agents.loader import load_agents_from_directory

        (tmp_path / "bad.yaml").write_text("not: valid: yaml: [}", encoding="utf-8")
        agents = load_agents_from_directory(str(tmp_path))
        assert agents == []  # Should log warning but not crash

    def test_yaml_agent_missing_name_raises(self):
        from azext_prototype.agents.loader import YAMLAgent
        with pytest.raises(CLIError, match="must include 'name'"):
            YAMLAgent({"description": "no name"})

    def test_load_yaml_agent_not_found(self):
        from azext_prototype.agents.loader import load_yaml_agent
        with pytest.raises(CLIError, match="not found"):
            load_yaml_agent("/nonexistent/path.yaml")

    def test_load_yaml_agent_wrong_ext(self, tmp_path):
        from azext_prototype.agents.loader import load_yaml_agent
        (tmp_path / "test.txt").write_text("test")
        with pytest.raises(CLIError, match=".yaml"):
            load_yaml_agent(str(tmp_path / "test.txt"))

    def test_load_yaml_agent_not_mapping(self, tmp_path):
        from azext_prototype.agents.loader import load_yaml_agent
        (tmp_path / "bad.yaml").write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(CLIError, match="mapping"):
            load_yaml_agent(str(tmp_path / "bad.yaml"))

    def test_load_python_agent_not_found(self):
        from azext_prototype.agents.loader import load_python_agent
        with pytest.raises(CLIError, match="not found"):
            load_python_agent("/nonexistent/agent.py")

    def test_load_python_agent_wrong_ext(self, tmp_path):
        from azext_prototype.agents.loader import load_python_agent
        (tmp_path / "test.yaml").write_text("test")
        with pytest.raises(CLIError, match=".py"):
            load_python_agent(str(tmp_path / "test.yaml"))

    def test_load_python_agent_with_agent_class(self, tmp_path):
        from azext_prototype.agents.loader import load_python_agent

        code = '''
from azext_prototype.agents.base import BaseAgent, AgentCapability

class MyAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="py-agent",
            description="Python agent",
            capabilities=[AgentCapability.DEVELOP],
            system_prompt="test",
        )

AGENT_CLASS = MyAgent
'''
        (tmp_path / "my_agent.py").write_text(code, encoding="utf-8")
        agent = load_python_agent(str(tmp_path / "my_agent.py"))
        assert agent.name == "py-agent"

    def test_load_python_agent_auto_discover(self, tmp_path):
        from azext_prototype.agents.loader import load_python_agent

        code = '''
from azext_prototype.agents.base import BaseAgent, AgentCapability

class AutoAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="auto-agent",
            description="Auto-discover agent",
            capabilities=[AgentCapability.DEVELOP],
            system_prompt="test",
        )
'''
        (tmp_path / "auto_agent.py").write_text(code, encoding="utf-8")
        agent = load_python_agent(str(tmp_path / "auto_agent.py"))
        assert agent.name == "auto-agent"

    def test_load_python_agent_no_class_raises(self, tmp_path):
        from azext_prototype.agents.loader import load_python_agent

        (tmp_path / "empty.py").write_text("# no agent here\nx = 1\n", encoding="utf-8")
        with pytest.raises(CLIError, match="No BaseAgent subclass found"):
            load_python_agent(str(tmp_path / "empty.py"))

    def test_load_python_agent_multiple_classes_raises(self, tmp_path):
        from azext_prototype.agents.loader import load_python_agent

        code = '''
from azext_prototype.agents.base import BaseAgent, AgentCapability

class AgentA(BaseAgent):
    def __init__(self):
        super().__init__(name="a", description="A", capabilities=[], system_prompt="")

class AgentB(BaseAgent):
    def __init__(self):
        super().__init__(name="b", description="B", capabilities=[], system_prompt="")
'''
        (tmp_path / "multi.py").write_text(code, encoding="utf-8")
        with pytest.raises(CLIError, match="Multiple BaseAgent"):
            load_python_agent(str(tmp_path / "multi.py"))
