"""Abstract AI provider interface."""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """A tool call requested by the AI model."""

    id: str
    name: str
    arguments: str  # JSON string of arguments


@dataclass
class AIMessage:
    """A message in an AI conversation."""

    role: str  # "system", "user", "assistant", "tool"
    content: str | list  # str for text, list for multi-modal content arrays
    metadata: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[ToolCall] | None = None  # For assistant messages with tool calls
    tool_call_id: str | None = None  # For tool result messages


@dataclass
class AIResponse:
    """Response from an AI provider."""

    content: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)  # tokens
    metadata: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = "stop"
    tool_calls: list[ToolCall] | None = None  # Tool calls requested by the model


class AIProvider(ABC):
    """Abstract base class for AI providers.

    Implementations provide a unified interface regardless of whether
    the backend is GitHub Models API or Azure OpenAI.
    """

    @abstractmethod
    def chat(
        self,
        messages: list[AIMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
    ) -> AIResponse:
        """Send a chat completion request.

        Args:
            messages: Conversation history.
            model: Model to use (provider-specific, uses default if None).
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in response.
            response_format: Optional structured output format (e.g., JSON mode).
            tools: Optional list of tool definitions in OpenAI function-calling
                format. When provided, the model may return tool_calls instead
                of (or in addition to) content.

        Returns:
            AIResponse with the model's reply (and optional tool_calls).
        """

    @abstractmethod
    def stream_chat(
        self,
        messages: list[AIMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> Iterator[str]:
        """Stream a chat completion response.

        Args:
            messages: Conversation history.
            model: Model to use.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in response.

        Yields:
            str chunks of the response content.
        """

    @abstractmethod
    def list_models(self) -> list[dict]:
        """List available models from this provider.

        Returns:
            List of dicts with model info (id, name, context_length, etc.)
        """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (e.g., 'github-models', 'azure-openai')."""

    @property
    @abstractmethod
    def default_model(self) -> str:
        """Return the default model ID for this provider."""
