"""Knowledge system for agent context composition.

The knowledge module provides a hub-and-spoke system for dynamically composing
agent context from shared reference documents, service-specific patterns, tool
patterns, and language patterns.

Directory layout::

    knowledge/
    ├── __init__.py              # KnowledgeLoader public API (this file)
    ├── constraints.md           # Shared constraints (auth, network, security, tagging)
    ├── service-registry.yaml    # Canonical service reference data (RBAC IDs, DNS, APIs)
    ├── services/                # Per-Azure-service knowledge files
    ├── tools/                   # IaC tool patterns (terraform, bicep, deploy-scripts)
    ├── languages/               # Language-specific patterns (python, csharp, nodejs, auth)
    └── roles/                   # Agent role templates (architect, infrastructure, developer, analyst)

Usage::

    loader = KnowledgeLoader()
    context = loader.compose_context(
        services=["cosmos-db", "key-vault"],
        tool="terraform",
        language="python",
        role="infrastructure",
    )
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Root directory of knowledge files (same directory as this __init__.py)
_KNOWLEDGE_DIR = Path(__file__).parent

# Approximate token budget for composed context (~4 chars per token).
# 10k tokens ~= 40,000 characters.  We aim to stay under this.
DEFAULT_TOKEN_BUDGET = 10_000
_CHARS_PER_TOKEN = 4


class KnowledgeLoader:
    """Load and compose knowledge context for agent system messages.

    The loader reads markdown files and YAML data from the ``knowledge/``
    directory tree and composes them into a single context string that
    fits within a token budget.

    Thread-safe for concurrent reads (all state is derived from the
    filesystem and cached via ``lru_cache``).
    """

    def __init__(
        self,
        knowledge_dir: str | Path | None = None,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
    ):
        self._dir = Path(knowledge_dir) if knowledge_dir else _KNOWLEDGE_DIR
        self._token_budget = token_budget

    # ------------------------------------------------------------------
    # Individual loaders
    # ------------------------------------------------------------------

    def load_service(self, service_name: str) -> str:
        """Load a service knowledge file (e.g. ``cosmos-db``)."""
        return self._read_md("services", f"{service_name}.md")

    def load_tool(self, tool_name: str) -> str:
        """Load a tool pattern file (e.g. ``terraform``)."""
        return self._read_md("tools", f"{tool_name}.md")

    def load_language(self, lang_name: str) -> str:
        """Load a language pattern file (e.g. ``python``)."""
        return self._read_md("languages", f"{lang_name}.md")

    def load_role(self, role_name: str) -> str:
        """Load a role template file (e.g. ``architect``)."""
        return self._read_md("roles", f"{role_name}.md")

    def load_constraints(self) -> str:
        """Load the shared constraints document."""
        return self._read_md(".", "constraints.md")

    def load_service_registry(self, service_name: str | None = None) -> dict | Any:
        """Load the service registry YAML, optionally filtered to one service.

        Args:
            service_name: If provided, return only the entry for that service.
                         If ``None``, return the full registry dict.

        Returns:
            Full registry dict, or a single service entry dict, or ``{}``
            if the service is not found.
        """
        registry = self._read_yaml("service-registry.yaml")
        # Unwrap top-level "services" key if present
        if "services" in registry and isinstance(registry["services"], dict):
            registry = registry["services"]
        if service_name is None:
            return registry
        return registry.get(service_name, {})

    # ------------------------------------------------------------------
    # Context composition
    # ------------------------------------------------------------------

    def compose_context(
        self,
        *,
        services: list[str] | None = None,
        tool: str | None = None,
        language: str | None = None,
        role: str | None = None,
        include_constraints: bool = True,
        include_service_registry: bool = False,
        mode: str = "poc",
    ) -> str:
        """Compose a full knowledge context string from multiple sources.

        Loads the requested knowledge files and concatenates them,
        respecting the token budget.  Files are added in priority order:

        1. Role template (highest priority — defines the agent's identity)
        2. Constraints (shared rules all agents must follow)
        3. Tool patterns (IaC-specific patterns)
        4. Language patterns (language-specific patterns)
        5. Service knowledge files (per-service, loaded in order given)
        6. Service registry entries (raw reference data, lowest priority)

        If the budget is exceeded, lower-priority content is truncated.

        Args:
            mode: Content filtering mode.  ``"poc"`` (default) strips
                ``## Production Backlog Items`` sections from service
                files.  ``"production"`` or ``"all"`` keep everything.

        Returns:
            Composed context string, or empty string if nothing loaded.
        """
        sections: list[tuple[str, str]] = []  # (label, content)

        if role:
            content = self.load_role(role)
            if content:
                sections.append((f"ROLE: {role}", content))

        if include_constraints:
            content = self.load_constraints()
            if content:
                sections.append(("SHARED CONSTRAINTS", content))

        if tool:
            content = self.load_tool(tool)
            if content:
                sections.append((f"TOOL PATTERNS: {tool}", content))

        if language:
            content = self.load_language(language)
            if content:
                sections.append((f"LANGUAGE PATTERNS: {language}", content))

            # Always include auth-patterns alongside a specific language
            if language != "auth-patterns":
                auth = self.load_language("auth-patterns")
                if auth:
                    sections.append(("AUTH PATTERNS (cross-language)", auth))

        if services:
            for svc in services:
                content = self.load_service(svc)
                if content:
                    if mode == "poc":
                        content = _filter_content(content, mode)
                    sections.append((f"SERVICE: {svc}", content))

        if include_service_registry and services:
            registry_lines = []
            for svc in services:
                entry = self.load_service_registry(svc)
                if entry:
                    registry_lines.append(f"## {svc}\n```yaml\n{yaml.dump(entry, default_flow_style=False)}```")
            if registry_lines:
                sections.append(("SERVICE REGISTRY DATA", "\n\n".join(registry_lines)))

        if not sections:
            return ""

        return self._assemble(sections)

    # ------------------------------------------------------------------
    # Production backlog extraction
    # ------------------------------------------------------------------

    def extract_production_items(self, service: str) -> list[str]:
        """Extract production backlog items from a service knowledge file.

        Parses the ``## Production Backlog Items`` section and returns
        the bullet-point items as a list of strings (without the leading
        ``- ``).  Returns an empty list if the section is not found.
        """
        content = self.load_service(service)
        if not content:
            return []
        return _extract_production_section(content)

    # ------------------------------------------------------------------
    # Token estimation
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token count (~4 characters per token)."""
        return len(text) // _CHARS_PER_TOKEN

    # ------------------------------------------------------------------
    # Available files (for introspection / testing)
    # ------------------------------------------------------------------

    def list_services(self) -> list[str]:
        """List available service knowledge file names (without extension)."""
        return self._list_dir("services")

    def list_tools(self) -> list[str]:
        """List available tool pattern file names."""
        return self._list_dir("tools")

    def list_languages(self) -> list[str]:
        """List available language pattern file names."""
        return self._list_dir("languages")

    def list_roles(self) -> list[str]:
        """List available role template file names."""
        return self._list_dir("roles")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_md(self, subdir: str, filename: str) -> str:
        """Read a markdown file, returning empty string on missing/error."""
        if subdir == ".":
            path = self._dir / filename
        else:
            path = self._dir / subdir / filename
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.debug("Knowledge file not found: %s", path)
            return ""
        except Exception as e:
            logger.warning("Error reading knowledge file %s: %s", path, e)
            return ""

    def _read_yaml(self, filename: str) -> dict:
        """Read and parse a YAML file, returning empty dict on error."""
        path = self._dir / filename
        try:
            text = path.read_text(encoding="utf-8")
            result = yaml.safe_load(text)
            return result if isinstance(result, dict) else {}
        except FileNotFoundError:
            logger.debug("Knowledge YAML not found: %s", path)
            return {}
        except Exception as e:
            logger.warning("Error reading knowledge YAML %s: %s", path, e)
            return {}

    def _list_dir(self, subdir: str) -> list[str]:
        """List .md files in a subdirectory, returning stem names."""
        dirpath = self._dir / subdir
        if not dirpath.is_dir():
            return []
        return sorted(p.stem for p in dirpath.iterdir() if p.suffix == ".md" and p.is_file())

    def _assemble(self, sections: list[tuple[str, str]]) -> str:
        """Assemble sections into a single string, respecting token budget."""
        budget_chars = self._token_budget * _CHARS_PER_TOKEN
        parts = []
        used = 0

        for label, content in sections:
            header = f"# {label}\n\n"
            section_text = header + content + "\n\n"
            section_len = len(section_text)

            if used + section_len <= budget_chars:
                parts.append(section_text)
                used += section_len
            else:
                # Fit what we can from this section
                remaining = budget_chars - used
                if remaining > len(header) + 200:
                    # Include at least the header and some content
                    truncated = section_text[: remaining - 50]
                    truncated += "\n\n[... truncated to fit token budget ...]\n"
                    parts.append(truncated)
                    used = budget_chars
                break  # Budget exhausted

        return "".join(parts).rstrip()


# ------------------------------------------------------------------
# Module-level helpers for content filtering
# ------------------------------------------------------------------

_PRODUCTION_HEADING = re.compile(r"^##\s+Production Backlog Items\s*$", re.MULTILINE)


def _filter_content(content: str, mode: str) -> str:
    """Filter knowledge content based on *mode*.

    ``"poc"`` strips the ``## Production Backlog Items`` section (and
    everything after it until the next ``## `` heading or end of file).
    ``"production"`` / ``"all"`` return *content* unchanged.
    """
    if mode != "poc":
        return content

    match = _PRODUCTION_HEADING.search(content)
    if not match:
        return content

    start = match.start()
    # Find the next ## heading after this one (or end of file)
    rest = content[match.end() :]
    next_heading = re.search(r"^## ", rest, re.MULTILINE)
    if next_heading:
        end = match.end() + next_heading.start()
    else:
        end = len(content)

    return (content[:start] + content[end:]).rstrip()


def _extract_production_section(content: str) -> list[str]:
    """Extract bullet items from the ``## Production Backlog Items`` section."""
    match = _PRODUCTION_HEADING.search(content)
    if not match:
        return []

    rest = content[match.end() :]
    # Stop at next heading or end of file
    next_heading = re.search(r"^## ", rest, re.MULTILINE)
    section = rest[: next_heading.start()] if next_heading else rest

    items: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:])
    return items
