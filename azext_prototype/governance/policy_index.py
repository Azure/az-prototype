"""Policy index — embedding-based retrieval of governance rules.

Pre-processes policy rules into vectors for fast semantic retrieval.
Supports caching embeddings to disk so re-indexing is only needed
when policies change.
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

CACHE_FILE = ".prototype/governance/policy_embeddings.json"


@dataclass
class IndexedRule:
    """A single policy rule with its source metadata."""

    rule_id: str
    severity: str
    description: str
    rationale: str
    policy_name: str
    category: str
    services: list[str]
    applies_to: list[str]

    @property
    def text_for_embedding(self) -> str:
        """Combine fields into a single text for embedding."""
        parts = [
            f"[{self.category}] {self.policy_name}",
            f"Rule {self.rule_id} ({self.severity}): {self.description}",
        ]
        if self.rationale:
            parts.append(f"Rationale: {self.rationale}")
        if self.services:
            parts.append(f"Services: {', '.join(self.services)}")
        return " ".join(parts)


class PolicyIndex:
    """Indexed policy rules for fast semantic retrieval.

    Build once from the policy engine's loaded policies, then
    ``retrieve()`` to find the top-k most relevant rules for a task.
    """

    def __init__(self, backend: EmbeddingBackend | None = None) -> None:
        self._backend = backend or create_backend()
        self._rules: list[IndexedRule] = []
        self._vectors: list[list[float]] = []
        self._built = False

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    def load_precomputed(self) -> bool:
        """Load pre-computed neural embeddings shipped with the package.

        These are generated at build time by ``scripts/compute_embeddings.py``
        and bundled inside the wheel as ``policy_vectors.json``.  This is the
        primary path — no embedding computation at runtime, pure Python only.
        """
        vectors_path = Path(__file__).parent / "policies" / "policy_vectors.json"
        if not vectors_path.exists():
            return False
        try:
            data = json.loads(vectors_path.read_text(encoding="utf-8"))
            self._rules = [
                IndexedRule(
                    rule_id=r["rule_id"],
                    severity=r.get("severity", "recommended"),
                    description=r.get("description", ""),
                    rationale=r.get("rationale", ""),
                    policy_name=r.get("policy_name", ""),
                    category=r.get("category", ""),
                    services=r.get("services", []),
                    applies_to=r.get("applies_to", []),
                )
                for r in data.get("rules", [])
            ]
            self._vectors = [r["vector"] for r in data.get("rules", [])]
            self._built = True
            logger.debug("Loaded %d pre-computed policy embeddings (dim=%s)", len(self._rules), data.get("dimension"))
            return True
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Failed to load pre-computed embeddings: %s", exc)
            return False

    def build(self, policies: list[Any]) -> None:
        """Extract rules from loaded policies and compute embeddings.

        Parameters
        ----------
        policies:
            List of ``Policy`` objects from ``PolicyEngine.policies``.
        """
        from azext_prototype.debug_log import log_flow

        self._rules = []
        for policy in policies:
            category = getattr(policy, "category", "")
            policy_name = getattr(policy, "name", "")
            services = getattr(policy, "services", [])
            for rule in getattr(policy, "rules", []):
                self._rules.append(
                    IndexedRule(
                        rule_id=getattr(rule, "id", ""),
                        severity=getattr(rule, "severity", "recommended"),
                        description=getattr(rule, "description", ""),
                        rationale=getattr(rule, "rationale", ""),
                        policy_name=policy_name,
                        category=category,
                        services=services,
                        applies_to=getattr(rule, "applies_to", []),
                    )
                )

        if not self._rules:
            self._built = True
            return

        texts = [r.text_for_embedding for r in self._rules]
        log_flow("PolicyIndex.build", f"Embedding {len(texts)} policy rules")
        self._vectors = self._backend.embed(texts)
        self._built = True
        log_flow("PolicyIndex.build", f"Index built: {len(self._rules)} rules, {len(self._vectors[0])}-dim vectors")

    def retrieve(self, query: str, top_k: int = 10) -> list[IndexedRule]:
        """Find the top-k most relevant rules for a query.

        Parameters
        ----------
        query:
            Task description or context to match against.
        top_k:
            Maximum number of rules to return.

        Returns
        -------
        list[IndexedRule]
            Rules sorted by descending relevance.
        """
        if not self._built or not self._rules:
            return []

        query_vec = self._backend.embed_query(query)
        scored = [(cosine_similarity(query_vec, vec), rule) for vec, rule in zip(self._vectors, self._rules)]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [rule for _, rule in scored[:top_k]]

    def retrieve_for_agent(self, query: str, agent_name: str, top_k: int = 10) -> list[IndexedRule]:
        """Retrieve rules filtered by agent applicability.

        Only returns rules whose ``applies_to`` list includes the
        agent name (or is empty, meaning the rule applies to all).
        """
        candidates = self.retrieve(query, top_k=top_k * 2)
        filtered = []
        for rule in candidates:
            if not rule.applies_to or agent_name in rule.applies_to:
                filtered.append(rule)
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
            "rules": [asdict(r) for r in self._rules],
            "vectors": self._vectors,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        logger.debug("Saved policy index cache to %s", path)

    def load_cache(self, project_dir: str) -> bool:
        """Load a previously cached index. Returns True if successful."""
        path = Path(project_dir) / CACHE_FILE
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._rules = [IndexedRule(**r) for r in data["rules"]]
            self._vectors = data["vectors"]
            self._built = True
            logger.debug("Loaded policy index cache from %s (%d rules)", path, len(self._rules))
            return True
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Failed to load policy index cache: %s", exc)
            return False
