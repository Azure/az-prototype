"""GitHub Models API provider."""

import logging
from collections.abc import Iterator
from typing import Any

from knack.util import CLIError

from azext_prototype.ai.provider import AIProvider, AIMessage, AIResponse, ToolCall

logger = logging.getLogger(__name__)

# GitHub Models API endpoint
GITHUB_MODELS_ENDPOINT = "https://models.inference.ai.azure.com"


class GitHubModelsProvider(AIProvider):
    """AI provider using GitHub Models API.

    Uses the authenticated GitHub user's token to access models
    available through GitHub's model marketplace.
    """

    DEFAULT_MODEL = "gpt-4o"

    def __init__(self, token: str, model: str | None = None):
        """Initialize with a GitHub token.

        Args:
            token: GitHub personal access token with models:read scope.
            model: Default model to use (defaults to gpt-4o).
        """
        self._token = token
        self._model = model or self.DEFAULT_MODEL
        self._client = self._create_client()

    def _create_client(self):
        """Create OpenAI-compatible client for GitHub Models."""
        from openai import OpenAI

        return OpenAI(
            base_url=GITHUB_MODELS_ENDPOINT,
            api_key=self._token,
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
        """Send a chat completion via GitHub Models API."""
        target_model = model or self._model

        api_messages = self._messages_to_dicts(messages)

        kwargs: dict[str, Any] = {
            "model": target_model,
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
            logger.error("GitHub Models API error: %s", e)
            raise CLIError(
                f"Failed to get response from GitHub Models API: {e}\n"
                "Check your GitHub token has 'models:read' scope."
            )

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
    ) -> Iterator[str]:
        """Stream a chat completion response from GitHub Models."""
        target_model = model or self._model
        api_messages: list[dict[str, Any]] = [
            {"role": m.role, "content": m.content} for m in messages
        ]

        try:
            stream = self._client.chat.completions.create(
                model=target_model,
                messages=api_messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )

            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            logger.error("GitHub Models streaming error: %s", e)
            raise CLIError(f"Streaming failed from GitHub Models API: {e}")

    def list_models(self) -> list[dict]:
        """List models available through GitHub Models.

        Note: GitHub Models API doesn't have a direct list endpoint,
        so we return known supported models.  Anthropic models are
        NOT available on GitHub Models — use the 'copilot' provider
        for Claude.
        """
        return [
            {"id": "openai/gpt-4o", "name": "GPT-4o", "provider": "openai", "context_length": 128000},
            {"id": "openai/gpt-4.1", "name": "GPT-4.1", "provider": "openai", "context_length": 1048576},
            {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini", "provider": "openai", "context_length": 128000},
            {"id": "openai/o3", "name": "o3", "provider": "openai", "context_length": 200000},
            {"id": "openai/o3-mini", "name": "o3 Mini", "provider": "openai", "context_length": 200000},
            {"id": "meta/meta-llama-3.1-405b-instruct", "name": "Llama 3.1 405B", "provider": "meta", "context_length": 128000},
            {"id": "deepseek/deepseek-r1", "name": "DeepSeek R1", "provider": "deepseek", "context_length": 128000},
        ]

    @property
    def provider_name(self) -> str:
        return "github-models"

    @property
    def default_model(self) -> str:
        return self._model
