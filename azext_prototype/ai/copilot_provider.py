"""GitHub Copilot provider — direct HTTP calls.

Authenticates using the existing credential resolution in
``copilot_auth`` (OS keychain, env vars, ``gh`` CLI) and calls the
Copilot completions API directly with the raw OAuth token.

The raw ``gho_`` / ``ghu_`` / ``ghp_`` token is sent as a Bearer
token with editor-identification headers to the **enterprise**
endpoint (``api.enterprise.githubcopilot.com``).  No JWT exchange
is required.

No SDK subprocess, no async, no background threads — just a plain
``requests.post``.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Iterator
from typing import Any

import requests
from knack.util import CLIError

from azext_prototype.ai.copilot_auth import (
    get_copilot_token,
)
from azext_prototype.ai.provider import (
    AIMessage,
    AIProvider,
    AIResponse,
    ToolCall,
    messages_to_dicts,
)


class CopilotTimeoutError(CLIError):
    """Raised when the Copilot API request times out.

    Extends ``CLIError`` so it propagates cleanly through the Azure CLI
    error handling, but can be caught specifically by retry logic in the
    build session.
    """


class CopilotPromptTooLargeError(CLIError):
    """Raised when the prompt exceeds the Copilot API's token limit.

    The Copilot API enforces a model-level prompt token cap (typically
    168,000 tokens) that is lower than the model's native context window.
    Callers can catch this and truncate/chunk the prompt before retrying.

    Attributes:
        token_count: Number of tokens the prompt contained.
        token_limit: Maximum tokens the API accepts.
    """

    def __init__(self, message: str, token_count: int = 0, token_limit: int = 0):
        super().__init__(message)
        self.token_count = token_count
        self.token_limit = token_limit


class CopilotRateLimitError(CLIError):
    """Raised when the Copilot API returns HTTP 429 (rate limited).

    Attributes:
        retry_after: Seconds to wait before retrying (from Retry-After header).
    """

    def __init__(self, message: str, retry_after: int = 0):
        super().__init__(message)
        self.retry_after = retry_after


logger = logging.getLogger(__name__)

# Copilot API base URL.  The enterprise endpoint exposes the
# full model catalogue (Claude, GPT, Gemini) whereas the non-
# enterprise endpoint only returns a handful of GPT models.
_BASE_URL = os.environ.get(
    "COPILOT_BASE_URL",
    "https://api.enterprise.githubcopilot.com",
)

_COMPLETIONS_URL = f"{_BASE_URL}/chat/completions"
_MODELS_URL = f"{_BASE_URL}/models"

# Default request timeout in seconds.  Architecture generation and
# large prompts can take several minutes; 10 minutes is a safe default.
# The discovery system prompt alone is ~69KB (governance + templates +
# architect context), and QA remediation prompts can reach 235KB+.
_DEFAULT_TIMEOUT = 600


class CopilotProvider(AIProvider):
    """AI provider that calls the Copilot completions API directly.

    Authentication uses the raw OAuth token (``gho_``, ``ghu_``, etc.)
    resolved by ``copilot_auth``.  The token is sent as a ``Bearer``
    header alongside editor-identification headers that identify us
    as an approved Copilot integration.

    The enterprise endpoint (``api.enterprise.githubcopilot.com``)
    exposes the full model catalogue including Claude, GPT, and
    Gemini families.
    """

    DEFAULT_MODEL = "claude-sonnet-4"

    def __init__(
        self,
        model: str | None = None,
        github_token: str | None = None,  # kept for API compat
    ):
        self._model = model or self.DEFAULT_MODEL
        self._timeout = int(os.environ.get("COPILOT_TIMEOUT", str(_DEFAULT_TIMEOUT)))

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Build HTTP headers for the Copilot completions API.

        The raw OAuth token is sent directly as ``Bearer`` — no JWT
        exchange required.  The editor-identification headers
        mirror those used by the official Copilot CLI to identify
        us as an approved integration.
        """
        token = get_copilot_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "copilot/0.0.410",
            "Copilot-Integration-Id": "copilot-developer-cli",
            "Editor-Version": "copilot/0.0.410",
            "Editor-Plugin-Version": "copilot/0.0.410",
            "X-Request-Id": str(uuid.uuid4()),
        }

    # ------------------------------------------------------------------
    # AIProvider interface
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[AIMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
    ) -> AIResponse:
        """Send a chat completion request to the Copilot API."""
        target_model = model or self._model
        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages_to_dicts(messages, filter_empty=True),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            payload["tools"] = tools

        prompt_chars = sum(
            (
                len(m.content)
                if isinstance(m.content, str)
                else sum(len(p.get("text", "")) for p in m.content if isinstance(p, dict))
            )
            for m in messages
        )
        logger.debug(
            "Copilot request: model=%s, msgs=%d, chars=%d",
            target_model,
            len(messages),
            prompt_chars,
        )

        from azext_prototype.debug_log import debug as _dbg

        _dbg(
            "CopilotProvider.chat",
            "Sending request",
            model=target_model,
            messages=len(messages),
            prompt_chars=prompt_chars,
            max_tokens=max_tokens,
            timeout=self._timeout,
        )

        import time as _time

        _t0 = _time.perf_counter()
        try:
            resp = requests.post(
                _COMPLETIONS_URL,
                headers=self._headers(),
                json=payload,
                timeout=self._timeout,
            )
        except requests.Timeout:
            elapsed = _time.perf_counter() - _t0
            _dbg("CopilotProvider.chat", "TIMEOUT", elapsed_s=f"{elapsed:.1f}", timeout=self._timeout)
            raise CopilotTimeoutError(f"Copilot API timed out after {self._timeout}s.")
        except requests.RequestException as exc:
            raise CLIError(f"Failed to reach Copilot API: {exc}") from exc

        _elapsed = _time.perf_counter() - _t0
        request_id = resp.headers.get("x-request-id", "") or resp.headers.get("x-github-request-id", "")
        _dbg(
            "CopilotProvider.chat",
            "Response received",
            elapsed_s=f"{_elapsed:.1f}",
            status=resp.status_code,
            response_chars=len(resp.text),
            request_id=request_id,
        )

        # 401 → token may be invalid or revoked; retry once
        if resp.status_code == 401:
            logger.debug("Got 401 — retrying request")
            try:
                resp = requests.post(
                    _COMPLETIONS_URL,
                    headers=self._headers(),
                    json=payload,
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                raise CLIError(f"Copilot API retry failed: {exc}") from exc
            request_id = resp.headers.get("x-request-id", "")

        # 429 → rate limited; extract Retry-After header
        if resp.status_code == 429:
            retry_after = 0
            ra_header = resp.headers.get("Retry-After", resp.headers.get("retry-after", ""))
            try:
                retry_after = int(ra_header)
            except (ValueError, TypeError):
                retry_after = 60  # Default if header missing or unparseable
            _dbg(
                "CopilotProvider.chat",
                "RATE_LIMITED",
                retry_after=retry_after,
                request_id=request_id,
            )
            raise CopilotRateLimitError(
                f"Copilot API rate limited (HTTP 429). Retry after {retry_after}s.",
                retry_after=retry_after,
            )

        if resp.status_code != 200:
            body = ""
            try:
                body = resp.text[:500]
            except Exception:
                pass

            # Parse structured error for specific handling
            error_code = ""
            try:
                err_data = resp.json()
                error_obj = err_data.get("error", {})
                error_code = error_obj.get("code", "")
            except Exception:
                pass

            if error_code == "model_max_prompt_tokens_exceeded":
                # Extract token counts from the error message
                import re as _re

                token_count = 0
                token_limit = 0
                match = _re.search(r"(\d+)\s+exceeds the limit of\s+(\d+)", body)
                if match:
                    token_count = int(match.group(1))
                    token_limit = int(match.group(2))
                raise CopilotPromptTooLargeError(
                    f"Prompt too large: {token_count:,} tokens exceeds "
                    f"the Copilot API limit of {token_limit:,} tokens.",
                    token_count=token_count,
                    token_limit=token_limit,
                )

            raise CLIError(f"Copilot API error (HTTP {resp.status_code}):\n{body}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise CLIError("Copilot API returned invalid JSON.") from exc

        content = ""
        tool_calls_data = None
        finish = "stop"
        try:
            choice = data["choices"][0]
            message = choice.get("message", {})
            content = message.get("content") or ""
            finish = choice.get("finish_reason") or "stop"
            raw_tool_calls = message.get("tool_calls")
            if raw_tool_calls:
                tool_calls_data = [
                    ToolCall(
                        id=tc["id"],
                        name=tc["function"]["name"],
                        arguments=tc["function"].get("arguments", "{}"),
                    )
                    for tc in raw_tool_calls
                ]
        except (KeyError, IndexError):
            logger.warning("Copilot response had no content: %s", data)

        usage = data.get("usage", {})

        # Capture PRU (Premium Request Units) — may be in usage body or response headers
        pru = usage.get("premium_request_units") or usage.get("pru") or usage.get("copilot_premium_request_units")
        if pru is None:
            pru_header = resp.headers.get("x-github-copilot-pru") or resp.headers.get("x-copilot-pru")
            if pru_header:
                try:
                    pru = int(pru_header)
                except (ValueError, TypeError):
                    pass

        # Log response headers in debug mode for PRU field discovery
        _dbg(
            "CopilotProvider.chat",
            "Response usage and headers",
            usage_keys=list(usage.keys()),
            finish_reason=finish,
            pru=pru,
        )

        return AIResponse(
            content=content,
            model=target_model,
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "_copilot": True,  # Signals TokenTracker to compute PRUs
            },
            finish_reason=finish,
            tool_calls=tool_calls_data,
        )

    def stream_chat(
        self,
        messages: list[AIMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> Iterator[str]:
        """Stream a chat completion response (SSE)."""
        target_model = model or self._model
        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages_to_dicts(messages, filter_empty=True),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        headers = self._headers()
        headers["Accept"] = "text/event-stream"

        try:
            resp = requests.post(
                _COMPLETIONS_URL,
                headers=headers,
                json=payload,
                timeout=self._timeout,
                stream=True,
            )
            resp.raise_for_status()
        except requests.Timeout:
            raise CopilotTimeoutError(f"Copilot streaming timed out after {self._timeout}s.")
        except requests.RequestException as exc:
            raise CLIError(f"Copilot streaming request failed: {exc}") from exc

        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                text = delta.get("content")
                if text:
                    yield text
            except (json.JSONDecodeError, IndexError, KeyError):
                continue

    def list_models(self) -> list[dict]:
        """List models available through the Copilot API.

        Queries the ``/models`` endpoint dynamically.  Falls back to
        a curated list only if the request fails.
        """
        try:
            headers = self._headers()
            resp = requests.get(_MODELS_URL, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                models = []
                for m in data:
                    mid = m.get("id", "")
                    family = m.get("capabilities", {}).get("family", mid)
                    # Skip embedding-only models
                    if "embedding" in mid:
                        continue
                    models.append({"id": mid, "name": family})
                if models:
                    return models
                logger.debug("Models endpoint returned empty list")
            else:
                logger.debug(
                    "Models endpoint returned %d, using fallback",
                    resp.status_code,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to fetch models: %s", exc)

        # Fallback curated list
        return [
            {"id": "claude-sonnet-4", "name": "Claude Sonnet 4"},
            {"id": "claude-sonnet-4.5", "name": "Claude Sonnet 4.5"},
            {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
            {"id": "gpt-4.1", "name": "GPT-4.1"},
            {"id": "gpt-5-mini", "name": "GPT-5 Mini"},
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro"},
        ]

    @property
    def provider_name(self) -> str:
        return "copilot"

    @property
    def default_model(self) -> str:
        return self._model
