"""Microsoft Learn documentation search and retrieval.

Module-level functions (following the ``deploy_helpers.py`` pattern) that
search the Microsoft Learn API, fetch page content, and format results
for injection into agent context.

All functions return empty results on failure — never raise.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser

import requests

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://learn.microsoft.com/api/search"
_HTTP_TIMEOUT = 10  # seconds


# ------------------------------------------------------------------
# HTML → plain-text helper
# ------------------------------------------------------------------

class _HTMLTextExtractor(HTMLParser):
    """Minimal HTML-to-text converter using only stdlib."""

    def __init__(self) -> None:
        super().__init__()
        self._pieces: list[str] = []
        self._skip = False
        self._skip_tags = frozenset({"script", "style", "nav", "header", "footer"})

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._skip_tags:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skip_tags:
            self._skip = False
        if tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "li", "tr"):
            self._pieces.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._pieces.append(data)

    def get_text(self) -> str:
        raw = "".join(self._pieces)
        # Collapse runs of whitespace but keep paragraph breaks
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _html_to_text(html: str) -> str:
    """Strip HTML to plain text."""
    parser = _HTMLTextExtractor()
    parser.feed(html)
    return parser.get_text()


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def search_learn(query: str, max_results: int = 3) -> list[dict]:
    """Search Microsoft Learn and return a list of ``{title, url, description}`` dicts.

    Returns an empty list on any failure (timeout, network error, bad response).
    """
    try:
        resp = requests.get(
            _SEARCH_URL,
            params={
                "search": query,
                "locale": "en-us",
                "$top": max_results,
            },
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.debug("Learn search failed for query: %s", query)
        return []

    results = []
    for item in data.get("results", []):
        title = item.get("title", "")
        url = item.get("url", "")
        description = item.get("description", "")
        if url:
            results.append({"title": title, "url": url, "description": description})

    return results[:max_results]


def fetch_page_content(url: str, max_chars: int = 8000) -> str:
    """Fetch a learn.microsoft.com page and return plain-text content.

    Returns empty string on any failure.  Truncates to *max_chars*.
    """
    try:
        resp = requests.get(url, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        text = _html_to_text(resp.text)
    except Exception:
        logger.debug("Page fetch failed for: %s", url)
        return ""

    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[... truncated ...]"
    return text


def search_and_fetch(
    query: str,
    max_results: int = 3,
    max_chars_per_result: int = 3000,
) -> str:
    """Search Microsoft Learn, fetch top results, return formatted markdown.

    Combines :func:`search_learn` and :func:`fetch_page_content` into a
    single convenience call.  Returns empty string if nothing found.
    """
    hits = search_learn(query, max_results=max_results)
    if not hits:
        return ""

    fetched: list[dict] = []
    for hit in hits:
        content = fetch_page_content(hit["url"], max_chars=max_chars_per_result)
        if content:
            fetched.append({**hit, "content": content})

    if not fetched:
        return ""

    return format_search_results(fetched)


def format_search_results(results: list[dict]) -> str:
    """Format a list of fetched results into a markdown context string.

    Each element should have ``title``, ``url``, and ``content`` keys.
    """
    if not results:
        return ""

    parts: list[str] = []
    for r in results:
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        content = r.get("content", "")
        source_line = f"Source: [{title}]({url})" if url else f"Source: {title}"
        parts.append(f"### {title}\n{source_line}\n\n{content}")

    return "\n\n---\n\n".join(parts)
