"""In-memory TTL cache for web search results.

Provides a simple, session-scoped cache that avoids redundant HTTP
requests when multiple agents search for similar queries.
"""

from __future__ import annotations

import re
import time


class SearchCache:
    """In-memory cache with TTL expiry and LRU eviction.

    Parameters
    ----------
    ttl_seconds:
        Time-to-live for cached entries (default 30 minutes).
    max_entries:
        Maximum number of cached entries (default 50).  When exceeded,
        the oldest entry is evicted.
    """

    def __init__(self, ttl_seconds: int = 1800, max_entries: int = 50) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._store: dict[str, tuple[float, str]] = {}  # key → (timestamp, result)
        self._hits = 0
        self._misses = 0

    # ----------------------------------------------------------
    # Public API
    # ----------------------------------------------------------

    def get(self, query: str) -> str | None:
        """Return cached result for *query*, or ``None`` if missing/expired."""
        key = self._normalize(query)
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None

        ts, result = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            self._misses += 1
            return None

        self._hits += 1
        return result

    def put(self, query: str, result: str) -> None:
        """Store *result* under normalised *query* with the current timestamp."""
        key = self._normalize(query)
        # Evict oldest if at capacity (and this is a new key)
        if key not in self._store and len(self._store) >= self._max_entries:
            oldest_key = min(self._store, key=lambda k: self._store[k][0])
            del self._store[oldest_key]
        self._store[key] = (time.monotonic(), result)

    def clear(self) -> None:
        """Flush all entries and reset stats."""
        self._store.clear()
        self._hits = 0
        self._misses = 0

    def stats(self) -> dict:
        """Return cache statistics."""
        oldest = None
        if self._store:
            oldest = min(ts for ts, _ in self._store.values())
        return {
            "hits": self._hits,
            "misses": self._misses,
            "entries": len(self._store),
            "oldest": oldest,
        }

    # ----------------------------------------------------------
    # Internal
    # ----------------------------------------------------------

    @staticmethod
    def _normalize(query: str) -> str:
        """Lower-case, collapse whitespace."""
        return re.sub(r"\s+", " ", query.strip().lower())
