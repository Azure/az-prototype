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
from azext_prototype.ai.provider import AIProvider, AIMessage, AIResponse, ToolCall

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
# large prompts can take several minutes; 5 minutes is a safe default.
_DEFAULT_TIMEOUT = 300


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
        github_token: str | None = None,      # kept for API compat
    ):
        self._model = model or self.DEFAULT_MODEL
        self._timeout = int(
            os.environ.get("COPILOT_TIMEOUT", str(_DEFAULT_TIMEOUT))
        )

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

    @staticmethod
    def _messages_to_dicts(messages: list[AIMessage]) -> list[dict[str, Any]]:
        """Convert ``AIMessage`` list to OpenAI-style message dicts."""
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
            "messages": self._messages_to_dicts(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            payload["tools"] = tools

        prompt_chars = sum(
            len(m.content) if isinstance(m.content, str)
            else sum(len(p.get("text", "")) for p in m.content if isinstance(p, dict))
            for m in messages
        )
        logger.debug(
            "Copilot request: model=%s, msgs=%d, chars=%d",
            target_model, len(messages), prompt_chars,
        )

        try:
            resp = requests.post(
                _COMPLETIONS_URL,
                headers=self._headers(),
                json=payload,
                timeout=self._timeout,
            )
        except requests.Timeout:
            raise CLIError(
                f"Copilot API timed out after {self._timeout}s.\n"
                "For very large prompts, increase the timeout:\n"
                "  set COPILOT_TIMEOUT=600"
            )
        except requests.RequestException as exc:
            raise CLIError(
                f"Failed to reach Copilot API: {exc}"
            ) from exc

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
                raise CLIError(
                    f"Copilot API retry failed: {exc}"
                ) from exc

        if resp.status_code != 200:
            body = ""
            try:
                body = resp.text[:500]
            except Exception:
                pass
            raise CLIError(
                f"Copilot API error (HTTP {resp.status_code}):\n{body}\n\n"
                "Ensure you have a valid GitHub Copilot Business or Enterprise license."
            )

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

        return AIResponse(
            content=content,
            model=target_model,
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
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
            "messages": self._messages_to_dicts(messages),
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
            raise CLIError(
                f"Copilot streaming timed out after {self._timeout}s."
            )
        except requests.RequestException as exc:
            raise CLIError(
                f"Copilot streaming request failed: {exc}"
            ) from exc

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
