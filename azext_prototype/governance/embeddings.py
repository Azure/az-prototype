"""Embedding backends for policy retrieval.

Provides pluggable backends for converting policy rule text into
vectors for similarity search.

- **TFIDFBackend**: Pure-Python TF-IDF. Zero dependencies, near-instant
  for small corpora. Default backend (always available).
- **NeuralBackend**: Uses ``sentence-transformers`` (optional).
  Auto-detected when installed. Requires ``torch`` which is unavailable
  on Azure CLI's 32-bit Windows Python. Install manually:
  ``pip install sentence-transformers``.
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)


class EmbeddingBackend(ABC):
    """Abstract interface for embedding text into vectors."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into vectors."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed a single query text."""


# ------------------------------------------------------------------ #
# TF-IDF backend — pure Python, always available
# ------------------------------------------------------------------ #


class TFIDFBackend(EmbeddingBackend):
    """TF-IDF embedding backend using pure Python.

    Suitable for small corpora (<1000 documents). For policy rules
    (~60 items), vectorization and retrieval are near-instant.
    """

    def __init__(self) -> None:
        self._vocab: dict[str, int] = {}
        self._idf: dict[str, float] = {}
        self._fitted = False

    def fit(self, corpus: list[str]) -> None:
        """Build vocabulary and IDF weights from a corpus."""
        # Build vocabulary
        vocab_set: set[str] = set()
        doc_freq: Counter[str] = Counter()
        for doc in corpus:
            tokens = set(self._tokenize(doc))
            vocab_set.update(tokens)
            for token in tokens:
                doc_freq[token] += 1

        self._vocab = {word: idx for idx, word in enumerate(sorted(vocab_set))}
        n = len(corpus)
        self._idf = {word: math.log((n + 1) / (freq + 1)) + 1 for word, freq in doc_freq.items()}
        self._fitted = True

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts using TF-IDF vectors. Calls ``fit()`` if needed."""
        if not self._fitted:
            self.fit(texts)
        return [self._vectorize(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query."""
        if not self._fitted:
            raise RuntimeError("TFIDFBackend must be fit() before embed_query()")
        return self._vectorize(text)

    def _tokenize(self, text: str) -> list[str]:
        """Simple whitespace + lowercase tokenizer."""
        return [w.strip(".,;:!?()[]{}\"'").lower() for w in text.split() if len(w) > 1]

    def _vectorize(self, text: str) -> list[float]:
        """Convert text to a TF-IDF vector."""
        tokens = self._tokenize(text)
        tf = Counter(tokens)
        vec = [0.0] * len(self._vocab)
        for token, count in tf.items():
            if token in self._vocab:
                idx = self._vocab[token]
                vec[idx] = count * self._idf.get(token, 0.0)
        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


# ------------------------------------------------------------------ #
# Neural backend — sentence-transformers
# ------------------------------------------------------------------ #

_neural_model: Any = None


class NeuralBackend(EmbeddingBackend):
    """Sentence-transformers embedding backend.

    Uses ``all-MiniLM-L6-v2`` (~80MB) for fast, high-quality embeddings.
    The model is loaded once and cached for the session.
    """

    MODEL_NAME = "all-MiniLM-L6-v2"

    def __init__(self, status_fn: Any = None) -> None:
        self._status_fn = status_fn
        self._model = self._get_or_load_model()

    def _get_or_load_model(self) -> Any:
        """Load model (cached across instances within a session)."""
        global _neural_model
        if _neural_model is not None:
            return _neural_model

        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model %s...", self.MODEL_NAME)
        _neural_model = SentenceTransformer(self.MODEL_NAME)
        return _neural_model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts using the neural model."""
        embeddings = self._model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
        return [e.tolist() for e in embeddings]

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query."""
        embedding = self._model.encode([text], show_progress_bar=False, convert_to_numpy=True)
        return embedding[0].tolist()


# ------------------------------------------------------------------ #
# Similarity
# ------------------------------------------------------------------ #


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ------------------------------------------------------------------ #
# Backend factory
# ------------------------------------------------------------------ #


def create_backend(prefer_neural: bool = True, status_fn: Any = None) -> EmbeddingBackend:
    """Create the best available embedding backend.

    Defaults to TF-IDF (always available, zero dependencies).
    Upgrades to neural (sentence-transformers) when installed and
    *prefer_neural* is True.  Falls back silently to TF-IDF when
    ``sentence-transformers`` or ``torch`` is unavailable (e.g. Azure
    CLI 32-bit Windows Python).
    """
    if prefer_neural:
        try:
            return NeuralBackend(status_fn=status_fn)
        except Exception as exc:
            logger.info("Neural embedding backend unavailable (%s), using TF-IDF", exc)
    return TFIDFBackend()
