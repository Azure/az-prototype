"""Factory for creating AI provider instances.

SECURITY CONSTRAINT: Only approved providers are permitted.
The allowlist is enforced here at construction time, and again
at the config layer when users run `az prototype config set`.
"""

import logging

from knack.util import CLIError

from azext_prototype.ai.provider import AIProvider
from azext_prototype.ai.github_models import GitHubModelsProvider
from azext_prototype.ai.azure_openai import AzureOpenAIProvider

logger = logging.getLogger(__name__)

# Providers that are allowed to be instantiated.
ALLOWED_PROVIDERS = frozenset({"github-models", "azure-openai", "copilot"})

# Provider names that are explicitly blocked (catch typos / social-engineering).
BLOCKED_PROVIDERS = frozenset({
    "openai",
    "chatgpt",
    "public-openai",
    "anthropic",
    "cohere",
    "google",
    "aws-bedrock",
    "huggingface",
})

# Models that require a specific provider.  Any model whose ID starts
# with one of these prefixes will be rejected when paired with an
# incompatible provider.
_COPILOT_ONLY_PREFIXES = ("claude-",)
_PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "copilot": "claude-sonnet-4.5",
    "github-models": "gpt-4o",
    "azure-openai": "gpt-4o",
}


def _validate_model_provider(provider_name: str, model: str | None) -> str | None:
    """Validate that *model* is compatible with *provider_name*.

    Returns the (possibly corrected) model name, or raises ``CLIError``
    with an actionable message.
    """
    if not model:
        return model

    model_lower = model.lower()

    # Claude models are only available via the Copilot API.
    if provider_name != "copilot" and any(
        model_lower.startswith(p) for p in _COPILOT_ONLY_PREFIXES
    ):
        suggested = _PROVIDER_DEFAULT_MODELS.get(provider_name, "gpt-4o")
        raise CLIError(
            f"Model '{model}' is not available on the '{provider_name}' provider.\n"
            f"Anthropic Claude models are only accessible through the 'copilot' provider.\n\n"
            f"To fix, either:\n"
            f"  1. Switch to the copilot provider:\n"
            f"       az prototype config set --key ai.provider --value copilot\n"
            f"  2. Or use a model supported by '{provider_name}':\n"
            f"       az prototype config set --key ai.model --value {suggested}"
        )

    return model


def create_ai_provider(config: dict) -> AIProvider:
    """Create an AI provider based on project configuration.

    Args:
        config: Project configuration dict containing 'ai' section:
            {
                "ai": {
                    "provider": "github-models" | "azure-openai",
                    "model": "gpt-4o",
                    "azure_openai": {
                        "endpoint": "https://...",
                        "deployment": "gpt-4o",
                        "api_key": null,
                        "use_managed_identity": true
                    }
                }
            }

    Returns:
        Configured AIProvider instance.
    """
    ai_config = config.get("ai", {})
    provider_name = ai_config.get("provider", "copilot").lower().strip()
    model = ai_config.get("model")

    # --- Provider allowlist enforcement ---
    if provider_name in BLOCKED_PROVIDERS:
        raise CLIError(
            f"AI provider '{provider_name}' is not permitted.\n"
            "Only Azure-hosted AI services are allowed. "
            "Supported providers: 'github-models', 'azure-openai', 'copilot'."
        )

    if provider_name not in ALLOWED_PROVIDERS:
        raise CLIError(
            f"Unknown AI provider: '{provider_name}'.\n"
            "Supported providers: 'github-models', 'azure-openai', 'copilot'."
        )

    # Catch model / provider mismatches before hitting the remote API.
    model = _validate_model_provider(provider_name, model)

    if provider_name == "github-models":
        return _create_github_models(ai_config, model)
    elif provider_name == "azure-openai":
        return _create_azure_openai(ai_config, model)
    elif provider_name == "copilot":
        return _create_copilot(ai_config, model)
    else:
        raise CLIError(f"Unhandled AI provider: '{provider_name}'.")


def _create_github_models(ai_config: dict, model: str | None) -> GitHubModelsProvider:
    """Create a GitHub Models provider."""
    from azext_prototype.auth.github_auth import GitHubAuthManager

    auth = GitHubAuthManager()
    auth.ensure_authenticated()
    token = auth.get_token()

    return GitHubModelsProvider(token=token, model=model)


def _create_azure_openai(ai_config: dict, model: str | None) -> AzureOpenAIProvider:
    """Create an Azure OpenAI provider."""
    aoai_config = ai_config.get("azure_openai", {})

    endpoint = aoai_config.get("endpoint")
    if not endpoint:
        raise CLIError(
            "Azure OpenAI endpoint is required. Set it via:\n"
            "  az prototype config set --key ai.azure_openai.endpoint --value https://your-resource.openai.azure.com/"
        )

    return AzureOpenAIProvider(
        endpoint=endpoint,
        deployment=model or aoai_config.get("deployment"),
    )


def _create_copilot(ai_config: dict, model: str | None) -> AIProvider:
    """Create a GitHub Copilot provider (direct HTTP).

    Token resolution and exchange are handled internally by the
    ``CopilotProvider`` via ``copilot_auth``.
    """
    from azext_prototype.ai.copilot_provider import CopilotProvider

    return CopilotProvider(model=model)
