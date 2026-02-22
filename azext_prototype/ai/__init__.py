"""AI provider abstraction layer."""

from azext_prototype.ai.azure_openai import AzureOpenAIProvider
from azext_prototype.ai.copilot_provider import CopilotProvider
from azext_prototype.ai.factory import create_ai_provider
from azext_prototype.ai.github_models import GitHubModelsProvider
from azext_prototype.ai.provider import AIMessage, AIProvider, AIResponse

__all__ = [
    "AIProvider",
    "AIMessage",
    "AIResponse",
    "GitHubModelsProvider",
    "AzureOpenAIProvider",
    "CopilotProvider",
    "create_ai_provider",
]
