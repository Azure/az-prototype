"""Azure OpenAI provider.

SECURITY CONSTRAINT: Only Azure-hosted OpenAI instances are permitted.
Public OpenAI (api.openai.com / ChatGPT) and instances hosted on other
cloud providers are explicitly blocked.  This is enforced at three layers:
  1. Endpoint validation here (regex + blocked-list)
  2. Provider allowlist in factory.py
  3. Config-time validation in config/__init__.py
"""

import logging
import re
from typing import Any

from knack.util import CLIError

from azext_prototype.ai.provider import AIMessage, AIProvider, AIResponse, ToolCall

logger = logging.getLogger(__name__)

# Only endpoints matching this pattern are allowed.
# Format: https://<resource-name>.openai.azure.com
_AZURE_OPENAI_ENDPOINT_PATTERN = re.compile(r"^https://[a-zA-Z0-9][a-zA-Z0-9\-]*\.openai\.azure\.com/?$")

# Endpoints that are explicitly forbidden, regardless of pattern.
_BLOCKED_ENDPOINTS = [
    "api.openai.com",
    "chat.openai.com",
    "platform.openai.com",
    "openai.com",
]


class AzureOpenAIProvider(AIProvider):
    """AI provider using Azure OpenAI Service.

    Uses Azure identity (DefaultAzureCredential) for authentication,
    consistent with the managed identity requirement.
    """

    DEFAULT_MODEL = "gpt-4o"

    def __init__(
        self,
        endpoint: str,
        deployment: str | None = None,
        api_version: str = "2024-10-21",
    ):
        """Initialize Azure OpenAI provider.

        Authentication is always via DefaultAzureCredential (managed identity
        or 'az login').  Raw API keys are not accepted — this ensures that
        credentials stay within the customer's Azure tenant.

        Args:
            endpoint: Azure OpenAI endpoint URL (must be *.openai.azure.com).
            deployment: Deployment name (defaults to gpt-4o).
            api_version: Azure OpenAI API version.

        Raises:
            CLIError: If the endpoint fails Azure-only validation.
        """
        self._validate_endpoint(endpoint)
        self._endpoint = endpoint
        self._deployment = deployment or self.DEFAULT_MODEL
        self._api_version = api_version
        self._client = self._create_client()

    @staticmethod
    def _validate_endpoint(endpoint: str):
        """Validate that the endpoint is an Azure-hosted OpenAI instance.

        Raises:
            CLIError: If the endpoint is not a valid Azure OpenAI endpoint.
        """
        if not endpoint:
            raise CLIError(
                "Azure OpenAI endpoint is required. Set it via:\n"
                "  az prototype config set --key ai.azure_openai.endpoint "
                "--value https://your-resource.openai.azure.com/"
            )

        # Block known public / non-Azure endpoints.
        for blocked in _BLOCKED_ENDPOINTS:
            if blocked in endpoint.lower():
                raise CLIError(
                    f"Public OpenAI endpoints are not permitted: {endpoint}\n"
                    "Only Azure-hosted OpenAI instances (*.openai.azure.com) are allowed.\n"
                    "Provision an Azure OpenAI resource and use that endpoint instead."
                )

        # Enforce the Azure OpenAI URL pattern.
        if not _AZURE_OPENAI_ENDPOINT_PATTERN.match(endpoint):
            raise CLIError(
                f"Invalid Azure OpenAI endpoint: {endpoint}\n"
                "Endpoint must match the pattern: https://<resource>.openai.azure.com/\n"
                "Only Azure-hosted OpenAI instances are supported. Public OpenAI, "
                "ChatGPT, or third-party hosted endpoints are not allowed."
            )

    def _create_client(self):
        """Create Azure OpenAI client using DefaultAzureCredential.

        API-key authentication is intentionally not supported — all auth
        flows go through Azure identity so credentials remain within the
        customer's Azure tenant.
        """
        from openai import AzureOpenAI

        try:
            from azure.identity import (  # type: ignore[import-untyped]
                DefaultAzureCredential,
                get_bearer_token_provider,
            )

            credential = DefaultAzureCredential()
            token_provider = get_bearer_token_provider(
                credential,
                "https://cognitiveservices.azure.com/.default",
            )

            return AzureOpenAI(
                azure_endpoint=self._endpoint,
                azure_ad_token_provider=token_provider,
                api_version=self._api_version,
            )
        except ImportError:
            raise CLIError(
                "azure-identity package is required for Azure OpenAI auth. "
                "Install it with: pip install azure-identity"
            )
        except Exception as e:
            raise CLIError(
                f"Failed to authenticate with Azure: {e}\n"
                "Ensure you are logged in via 'az login' or have managed identity configured."
            )

    @staticmethod
    def _messages_to_dicts(messages: list[AIMessage]) -> list[dict[str, Any]]:
        """Convert AIMessage list to OpenAI-style message dicts."""
        result = []
        for m in messages:
            msg: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    }
                    for tc in m.tool_calls
                ]
            if m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            result.append(msg)
        return result

    @staticmethod
    def _extract_tool_calls(choice: Any) -> list[ToolCall] | None:
        """Extract tool calls from an OpenAI SDK response choice."""
        if not hasattr(choice.message, "tool_calls") or not choice.message.tool_calls:
            return None
        return [
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=tc.function.arguments or "{}",
            )
            for tc in choice.message.tool_calls
        ]

    def chat(
        self,
        messages: list[AIMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
    ) -> AIResponse:
        """Send a chat completion via Azure OpenAI."""
        deployment = model or self._deployment
        api_messages = self._messages_to_dicts(messages)

        kwargs: dict[str, Any] = {
            "model": deployment,
            "messages": api_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if response_format:
            kwargs["response_format"] = response_format

        if tools:
            kwargs["tools"] = tools

        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error("Azure OpenAI error: %s", e)
            raise CLIError(f"Azure OpenAI request failed: {e}")

        choice = response.choices[0]
        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return AIResponse(
            content=choice.message.content or "",
            model=response.model,
            usage=usage,
            finish_reason=choice.finish_reason or "stop",
            tool_calls=self._extract_tool_calls(choice),
        )

    def stream_chat(
        self,
        messages: list[AIMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        """Stream a chat completion response from Azure OpenAI."""
        deployment = model or self._deployment
        api_messages: list[dict[str, Any]] = [{"role": m.role, "content": m.content} for m in messages]

        try:
            stream = self._client.chat.completions.create(
                model=deployment,
                messages=api_messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )

            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            logger.error("Azure OpenAI streaming error: %s", e)
            raise CLIError(f"Streaming failed from Azure OpenAI: {e}")

    def list_models(self) -> list[dict]:
        """List deployed models in Azure OpenAI resource."""
        try:
            # Azure OpenAI doesn't have a standard list via the openai client;
            # we'd need the management API. Return the configured deployment.
            return [
                {
                    "id": self._deployment,
                    "name": self._deployment,
                    "provider": "azure-openai",
                    "endpoint": self._endpoint,
                }
            ]
        except Exception:
            return []

    @property
    def provider_name(self) -> str:
        return "azure-openai"

    @property
    def default_model(self) -> str:
        return self._deployment
