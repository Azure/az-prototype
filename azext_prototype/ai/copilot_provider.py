"""GitHub Copilot provider — direct HTTP calls.

Authenticates using the existing credential resolution in
``copilot_auth`` (OS keychain, env vars, ``gh`` CLI) and calls the
Copilot completions API directly with the raw OAuth token.

The raw ``gho_`` / ``ghu_`` / ``ghp_`` token is sent as a Bearer
token with editor-identification headers — the ``copilot_internal``
token exchange is **not** required.

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
from azext_prototype.ai.provider import AIProvider, AIMessage, AIResponse

logger = logging.getLogger(__name__)

# Copilot chat completions endpoint (OpenAI-compatible).
_COMPLETIONS_URL = "https://api.githubcopilot.com/chat/completions"

# Default request timeout in seconds.  Large prompts (e.g. VTT
# transcripts) can make the model think for a while, but the HTTP
# connection itself shouldn't take more than ~2 minutes.
_DEFAULT_TIMEOUT = 120


class CopilotProvider(AIProvider):
    """AI provider that calls the Copilot completions API directly.

    Authentication uses the raw OAuth token (``gho_``, ``ghu_``, etc.)
    resolved by ``copilot_auth``.  The token is sent as a ``Bearer``
    header alongside editor-identification headers that identify us
    as an approved Copilot integration.
    """

    DEFAULT_MODEL = "claude-sonnet-4.5"

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
        (``Editor-Version``, ``Copilot-Integration-Id``, ``User-Agent``)
        are required by the Copilot API to identify approved clients.
        """
        token = get_copilot_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "GithubCopilot/1.300.0",
            "Copilot-Integration-Id": "vscode-chat",
            "Editor-Version": "vscode/1.100.0",
            "Editor-Plugin-Version": "copilot-chat/0.37.5",
            "X-Request-Id": str(uuid.uuid4()),
        }

    @staticmethod
    def _messages_to_dicts(messages: list[AIMessage]) -> list[dict[str, str]]:
        """Convert ``AIMessage`` list to OpenAI-style message dicts."""
        return [{"role": m.role, "content": m.content} for m in messages]

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
    ) -> AIResponse:
        """Send a chat completion request to the Copilot API."""
        target_model = model or self._model
        payload: dict[str, Any] = {
            "model": target_model,
            "messages": self._messages_to_dicts(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        prompt_chars = sum(len(m.content) for m in messages)
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
                "  set COPILOT_TIMEOUT=300"
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
        try:
            content = data["choices"][0]["message"]["content"]
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
            finish_reason="stop",
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
        """List models available through the Copilot API."""
        # The completions API doesn't have a models endpoint; return
        # a curated list of known-good models.
        return [
            {"id": "claude-sonnet-4.5", "name": "Claude Sonnet 4.5"},
            {"id": "claude-sonnet-4", "name": "Claude Sonnet 4"},
            {"id": "gpt-4o", "name": "GPT-4o"},
            {"id": "gpt-4.1", "name": "GPT-4.1"},
            {"id": "o3-mini", "name": "o3-mini"},
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro"},
        ]

    @property
    def provider_name(self) -> str:
        return "copilot"

    @property
    def default_model(self) -> str:
        return self._model
