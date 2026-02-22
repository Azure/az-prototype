"""Token usage tracker for interactive sessions.

Accumulates ``AIResponse.usage`` across AI turns within a session,
providing at-a-glance token counts and context-window budget tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Model context-window sizes (prompt token budget).
# Used for budget-percentage display.  Values are the *input* context
# window (not total output limit).
_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-4-32k": 32_768,
    "gpt-35-turbo": 16_385,
    "gpt-3.5-turbo": 16_385,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o1-preview": 128_000,
    "o3-mini": 200_000,
}


@dataclass
class TokenTracker:
    """Accumulates token usage across AI turns within a session.

    After each AI call, pass the ``AIResponse`` to :meth:`record`.  The
    tracker keeps a running session total and remembers the most-recent
    turn's counts so the UI can display both.

    Usage::

        tracker = TokenTracker()
        response = ai_provider.chat(messages)
        tracker.record(response)
        print(tracker.format_status())
        # → "1,847 tokens this turn · 12,340 session · ~62%"
    """

    _this_turn_prompt: int = field(default=0, repr=False)
    _this_turn_completion: int = field(default=0, repr=False)
    _session_prompt: int = field(default=0, repr=False)
    _session_completion: int = field(default=0, repr=False)
    _turn_count: int = field(default=0, repr=False)
    _model: str = field(default="", repr=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, response) -> None:
        """Record usage from an :class:`~.provider.AIResponse`.

        Accepts any object with ``.usage`` (dict) and ``.model`` (str)
        attributes — duck-typed so callers don't need to import AIResponse.
        """
        usage = getattr(response, "usage", None) or {}
        self._this_turn_prompt = usage.get("prompt_tokens", 0)
        self._this_turn_completion = usage.get("completion_tokens", 0)
        self._session_prompt += self._this_turn_prompt
        self._session_completion += self._this_turn_completion
        self._turn_count += 1

        model = getattr(response, "model", "")
        if model:
            self._model = model

    @property
    def this_turn(self) -> int:
        """Tokens used in the most recent turn (prompt + completion)."""
        return self._this_turn_prompt + self._this_turn_completion

    @property
    def session_total(self) -> int:
        """Cumulative tokens across all turns (prompt + completion)."""
        return self._session_prompt + self._session_completion

    @property
    def session_prompt_total(self) -> int:
        """Cumulative *prompt* tokens only (for budget calculation)."""
        return self._session_prompt

    @property
    def turn_count(self) -> int:
        """Number of AI turns recorded."""
        return self._turn_count

    @property
    def model(self) -> str:
        """Most recently seen model name."""
        return self._model

    @property
    def budget_pct(self) -> float | None:
        """Percentage of context window consumed (prompt tokens only).

        Returns ``None`` when the model is unknown.
        """
        window = self._get_context_window()
        if window and self._session_prompt > 0:
            return (self._session_prompt / window) * 100
        return None

    def format_status(self) -> str:
        """One-line summary suitable for dim/muted display.

        Returns a string like::

            1,847 tokens this turn · 12,340 session · ~62%
        """
        if self.session_total == 0:
            return ""

        parts = [
            f"{self.this_turn:,} tokens this turn",
            f"{self.session_total:,} session",
        ]
        pct = self.budget_pct
        if pct is not None:
            parts.append(f"~{pct:.0f}%")
        return " \u00b7 ".join(parts)

    def to_dict(self) -> dict:
        """Serialisable snapshot (for state persistence or telemetry)."""
        return {
            "this_turn": {
                "prompt": self._this_turn_prompt,
                "completion": self._this_turn_completion,
            },
            "session": {
                "prompt": self._session_prompt,
                "completion": self._session_completion,
            },
            "turn_count": self._turn_count,
            "model": self._model,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_context_window(self) -> int | None:
        """Look up the context window for the current model."""
        if not self._model:
            return None

        model_lower = self._model.lower()

        # Exact match first
        if model_lower in _CONTEXT_WINDOWS:
            return _CONTEXT_WINDOWS[model_lower]

        # Substring match (e.g. "gpt-4o-2024-05-13" matches "gpt-4o")
        for key, window in _CONTEXT_WINDOWS.items():
            if key in model_lower:
                return window

        return None
