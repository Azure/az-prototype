"""Governance index — embedding-based retrieval of policies, anti-patterns, and standards.

Pre-processes governance items (rules, patterns, principles) into vectors
for fast semantic retrieval.  Supports pre-computed embeddings shipped with
the wheel as well as runtime computation and disk caching.

Vector files are stored in ``governance/`` as:
- ``policy.vectors.json``
- ``anti-pattern.vectors.json``
- ``standard.vectors.json``
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from azext_prototype.governance.embeddings import (
    EmbeddingBackend,
    cosine_similarity,
    create_backend,
)

logger = logging.getLogger(__name__)

CACHE_FILE = ".prototype/governance/governance_embeddings.json"

# Pre-computed vector filenames (shipped with the wheel)
_VECTOR_FILES = {
    "policy": "policy.vectors.json",
    "anti-pattern": "anti-pattern.vectors.json",
    "standard": "standard.vectors.json",
}


@dataclass
class IndexedItem:
    """A single governance item (rule, pattern, or principle) with metadata."""

    kind: str  # "policy" | "anti-pattern" | "standard"
    item_id: str
    severity: str  # "required" | "recommended" | "optional" | "" (standards)
    description: str
    rationale: str
    source_name: str  # policy name, anti-pattern category, standard category
    category: str
    services: list[str]  # ARM namespaces
    applies_to: list[str]  # agent names

    @property
    def text_for_embedding(self) -> str:
        """Combine fields into a single text for embedding."""
        parts = [
            f"[{self.kind}:{self.category}] {self.source_name}",
            f"{self.item_id}: {self.description}",
        ]
        if self.severity:
            parts[1] = f"{self.item_id} ({self.severity}): {self.description}"
        if self.rationale:
            parts.append(f"Rationale: {self.rationale}")
        if self.services:
            parts.append(f"Services: {', '.join(self.services)}")
        return " ".join(parts)


class GovernanceIndex:
    """Indexed governance items for fast semantic retrieval.

    Build once from the loaded policies, anti-patterns, and standards,
    then ``retrieve()`` to find the top-k most relevant items for a task.
    """

    def __init__(self, backend: EmbeddingBackend | None = None) -> None:
        self._backend = backend or create_backend()
        self._items: list[IndexedItem] = []
        self._vectors: list[list[float]] = []
        self._built = False

    @property
    def rule_count(self) -> int:
        """Total number of indexed items (backward compat name)."""
        return len(self._items)

    def load_precomputed(self) -> bool:
        """Load pre-computed embeddings shipped with the package.

        Reads all three vector files (policy, anti-pattern, standard) from
        the ``governance/`` directory and merges them into a single index.
        Falls back gracefully if any file is missing.
        """
        governance_dir = Path(__file__).parent
        loaded_any = False

        for kind, filename in _VECTOR_FILES.items():
            vectors_path = governance_dir / filename
            if not vectors_path.exists():
                # Also try legacy location for policies
                if kind == "policy":
                    vectors_path = governance_dir / "policies" / "policy_vectors.json"
                    if not vectors_path.exists():
                        continue
                else:
                    continue
            try:
                data = json.loads(vectors_path.read_text(encoding="utf-8"))
                for r in data.get("rules", data.get("items", [])):
                    self._items.append(
                        IndexedItem(
                            kind=r.get("kind", kind),
                            item_id=r.get("item_id", r.get("rule_id", "")),
                            severity=r.get("severity", ""),
                            description=r.get("description", ""),
                            rationale=r.get("rationale", ""),
                            source_name=r.get("source_name", r.get("policy_name", "")),
                            category=r.get("category", ""),
                            services=r.get("services", []),
                            applies_to=r.get("applies_to", []),
                        )
                    )
                    self._vectors.append(r["vector"])
                loaded_any = True
                logger.debug("Loaded %s vectors from %s", kind, vectors_path.name)
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                logger.warning("Failed to load %s vectors: %s", kind, exc)

        self._built = loaded_any and len(self._items) > 0
        if self._built:
            logger.debug("Total governance index: %d items", len(self._items))
        return self._built

    def build(self, policies: list[Any]) -> None:
        """Extract items from loaded policies and compute embeddings.

        Parameters
        ----------
        policies:
            List of ``Policy`` objects from ``PolicyEngine.list_policies()``.
            Anti-patterns and standards are loaded internally.
        """
        from azext_prototype.debug_log import log_flow

        self._items = []

        # Index policies
        for policy in policies:
            category = getattr(policy, "category", "")
            source_name = getattr(policy, "name", "")
            services = getattr(policy, "services", [])
            for rule in getattr(policy, "rules", []):
                rule_targets = getattr(rule, "targets", [])
                rule_services: list[str] = []
                for t in (rule_targets if isinstance(rule_targets, list) else []):
                    if isinstance(t, dict):
                        rule_services.extend(t.get("services", []))
                rule_services = rule_services or services
                self._items.append(
                    IndexedItem(
                        kind="policy",
                        item_id=getattr(rule, "id", ""),
                        severity=getattr(rule, "severity", "recommended"),
                        description=getattr(rule, "description", ""),
                        rationale=getattr(rule, "rationale", ""),
                        source_name=source_name,
                        category=category,
                        services=rule_services,
                        applies_to=getattr(rule, "applies_to", []),
                    )
                )

        # Index anti-patterns
        try:
            from azext_prototype.governance.anti_patterns import load as load_anti_patterns

            for check in load_anti_patterns():
                self._items.append(
                    IndexedItem(
                        kind="anti-pattern",
                        item_id=check.id,
                        severity="",
                        description=check.description or check.warning_message,
                        rationale=check.rationale,
                        source_name=check.domain,
                        category=check.domain,
                        services=[s for t in check.targets if isinstance(t, dict) for s in t.get("services", [])],
                        applies_to=check.applies_to,
                    )
                )
        except Exception as exc:
            logger.debug("Skipping anti-pattern indexing: %s", exc)

        # Index standards
        try:
            from azext_prototype.governance.standards import load as load_standards

            for standard in load_standards():
                for principle in standard.principles:
                    self._items.append(
                        IndexedItem(
                            kind="standard",
                            item_id=principle.id,
                            severity="",
                            description=principle.description,
                            rationale=principle.rationale,
                            source_name=standard.category,
                            category=standard.category,
                            services=[],
                            applies_to=principle.applies_to,
                        )
                    )
        except Exception as exc:
            logger.debug("Skipping standards indexing: %s", exc)

        if not self._items:
            self._built = True
            return

        texts = [item.text_for_embedding for item in self._items]
        log_flow("GovernanceIndex.build", f"Embedding {len(texts)} governance items")
        self._vectors = self._backend.embed(texts)
        self._built = True
        log_flow(
            "GovernanceIndex.build",
            f"Index built: {len(self._items)} items, {len(self._vectors[0])}-dim vectors",
        )

    def retrieve(self, query: str, top_k: int = 10, kind: str | None = None) -> list[IndexedItem]:
        """Find the top-k most relevant items for a query.

        Parameters
        ----------
        query:
            Task description or context to match against.
        top_k:
            Maximum number of items to return.
        kind:
            Optional filter — only return items of this kind
            ("policy", "anti-pattern", "standard").
        """
        if not self._built or not self._items:
            return []

        query_vec = self._backend.embed_query(query)
        scored = [(cosine_similarity(query_vec, vec), item) for vec, item in zip(self._vectors, self._items)]
        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for _, item in scored:
            if kind and item.kind != kind:
                continue
            results.append(item)
            if len(results) >= top_k:
                break
        return results

    def retrieve_for_agent(
        self, query: str, agent_name: str, top_k: int = 10, kind: str | None = None
    ) -> list[IndexedItem]:
        """Retrieve items filtered by agent applicability."""
        candidates = self.retrieve(query, top_k=top_k * 2, kind=kind)
        filtered = []
        for item in candidates:
            if not item.applies_to or agent_name in item.applies_to:
                filtered.append(item)
                if len(filtered) >= top_k:
                    break
        return filtered

    # ------------------------------------------------------------------ #
    # Cache
    # ------------------------------------------------------------------ #

    def save_cache(self, project_dir: str) -> None:
        """Persist the index to disk for fast reload."""
        if not self._built:
            return
        path = Path(project_dir) / CACHE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "items": [asdict(item) for item in self._items],
            "vectors": self._vectors,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        logger.debug("Saved governance index cache to %s", path)

    def load_cache(self, project_dir: str) -> bool:
        """Load a previously cached index. Returns True if successful."""
        path = Path(project_dir) / CACHE_FILE
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._items = [IndexedItem(**item) for item in data["items"]]
            self._vectors = data["vectors"]
            self._built = True
            logger.debug("Loaded governance index cache from %s (%d items)", path, len(self._items))
            return True
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Failed to load governance index cache: %s", exc)
            return False
