"""A disk cache of query embeddings, keyed by model and text hash (Step 5).

Rerunning the same questions makes no embedding calls.  Each vector is one file,
``{cache_dir}/{model}/{sha256 of the text}.json``, written atomically.  The
question text itself is not stored.  A file that cannot be read or does not
match the model is an error, never a silent miss, so a damaged cache is noticed.

``CachedBatchEmbedder`` (Step 9) does the same for batched entity-name
embeddings, keyed by model, input type and text hash:
``{cache_dir}/{model}/{input_type}/{sha256}.json``.  Voyage reports tokens per
request, not per text, so its files hold no token count; the embedder counts
the calls and tokens of the batches it sends.
"""
from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path

from ingest.atomic_json import write_json_atomic
from ingest.parsed_files import text_sha256
from ingest.voyage import (
    MAX_BATCH_TEXTS,
    MODELS,
    BatchEmbedder,
    BatchEmbedding,
    Embedding,
    InputType,
    QueryEmbedder,
    VoyageError,
)

_CACHE_ROOT = Path(__file__).resolve().parents[1] / "data" / "cache"
DEFAULT_CACHE_DIR = _CACHE_ROOT / "query_embeddings"
BATCH_CACHE_DIR = _CACHE_ROOT / "entity_embeddings"
DEFAULT_BATCH_SIZE = 128
_FIELDS = frozenset({"model", "text_sha256", "vector", "api_token_count"})
_BATCH_FIELDS = frozenset({"model", "input_type", "text_sha256", "vector"})


class CacheError(RuntimeError):
    """A cache file exists but cannot be used."""


class CachedQueryEmbedder:
    """Wraps a ``QueryEmbedder``; embeds each (model, text) pair at most once."""

    def __init__(self, inner: QueryEmbedder, cache_dir: Path = DEFAULT_CACHE_DIR):
        self._inner = inner
        self._dir = cache_dir

    def embed_query(self, text: str, model: str) -> Embedding:
        """The cached embedding of *text* (``from_cache`` set), or a fresh one, then cached."""
        if model not in MODELS:
            raise ValueError(f"unknown embedding model {model!r}")
        sha = text_sha256(text)
        path = self._dir / model / f"{sha}.json"
        if path.exists():
            return _read(path, model, sha)
        embedding = self._inner.embed_query(text, model)
        _write(path, model, sha, embedding)
        return embedding


def _read(path: Path, model: str, sha: str) -> Embedding:
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CacheError(f"cannot read query cache file {path.name}: {type(exc).__name__}") from exc
    if not isinstance(stored, dict) or set(stored) != _FIELDS:
        raise CacheError(f"query cache file {path.name} has unexpected fields")
    if stored["model"] != model or stored["text_sha256"] != sha:
        raise CacheError(f"query cache file {path.name} is for another model or text")
    vector = _checked_vector(path, stored["vector"], model)
    tokens = stored["api_token_count"]
    if not isinstance(tokens, int) or tokens <= 0:
        raise CacheError(f"query cache file {path.name}: bad api_token_count")
    return Embedding(vector=vector, api_token_count=tokens, from_cache=True)


def _checked_vector(path: Path, vector: object, model: str) -> tuple[float, ...]:
    expected = MODELS[model].dimensions
    if not isinstance(vector, list) or len(vector) != expected:
        raise CacheError(f"cache file {path.name}: vector lacks {expected} dimensions")
    if not all(isinstance(x, int | float) and math.isfinite(x) for x in vector):
        raise CacheError(f"cache file {path.name}: vector holds a non-numeric value")
    return tuple(float(x) for x in vector)


def _write(path: Path, model: str, sha: str, embedding: Embedding) -> None:
    write_json_atomic(path, {
        "model": model,
        "text_sha256": sha,
        "vector": list(embedding.vector),
        "api_token_count": embedding.api_token_count,
    })


class CachedBatchEmbedder:
    """Wraps a ``BatchEmbedder``; embeds each (model, input type, text) at most once.

    ``calls`` and ``tokens`` count the requests sent and the tokens Voyage
    reported for them; a fully cached run leaves both at 0.
    """

    def __init__(self, inner: BatchEmbedder, cache_dir: Path = BATCH_CACHE_DIR,
                 batch_size: int = DEFAULT_BATCH_SIZE):
        if not 1 <= batch_size <= MAX_BATCH_TEXTS:
            raise ValueError(f"batch_size must be 1 to {MAX_BATCH_TEXTS}")
        self._inner = inner
        self._dir = cache_dir
        self._batch_size = batch_size
        self.calls = 0
        self.tokens = 0

    def uncached(self, texts: Sequence[str], model: str, input_type: InputType) -> list[str]:
        """The distinct texts of *texts* not in the cache, in first-seen order."""
        return [t for t in dict.fromkeys(texts)
                if not self._path(model, input_type, text_sha256(t)).exists()]

    def embed(self, texts: Sequence[str], model: str, input_type: InputType) -> BatchEmbedding:
        """One vector per text, in order; texts not cached are sent in batches, then cached."""
        if model not in MODELS:
            raise ValueError(f"unknown embedding model {model!r}")
        missing = self.uncached(texts, model, input_type)
        tokens = 0
        for start in range(0, len(missing), self._batch_size):
            batch = missing[start:start + self._batch_size]
            result = self._inner.embed_batch(batch, model, input_type)
            self.calls += 1
            tokens += result.total_tokens
            for text, vector in zip(batch, result.vectors, strict=True):
                sha = text_sha256(text)
                write_json_atomic(self._path(model, input_type, sha), {
                    "model": model, "input_type": input_type, "text_sha256": sha,
                    "vector": list(vector),
                })
        self.tokens += tokens
        vectors = tuple(self._read(model, input_type, text_sha256(t)) for t in texts)
        return BatchEmbedding(vectors=vectors, total_tokens=tokens)

    def _path(self, model: str, input_type: InputType, sha: str) -> Path:
        return self._dir / model / input_type / f"{sha}.json"

    def _read(self, model: str, input_type: InputType, sha: str) -> tuple[float, ...]:
        path = self._path(model, input_type, sha)
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CacheError(f"cannot read cache file {path.name}: {type(exc).__name__}") from exc
        if not isinstance(stored, dict) or set(stored) != _BATCH_FIELDS:
            raise CacheError(f"cache file {path.name} has unexpected fields")
        if (stored["model"], stored["input_type"], stored["text_sha256"]) != (
                model, input_type, sha):
            raise CacheError(f"cache file {path.name} is for another model, input type or text")
        return _checked_vector(path, stored["vector"], model)


class CacheOnly:
    """Stands in for Voyage behind a cache whose every text must already be cached."""

    def __init__(self, hint: str):
        self._hint = hint

    def embed_query(self, text: str, model: str) -> Embedding:
        raise VoyageError(f"a question has no cached embedding; {self._hint}")

    def embed_batch(self, texts: Sequence[str], model: str,
                    input_type: InputType) -> BatchEmbedding:
        raise VoyageError(f"{len(texts)} texts have no cached embedding; {self._hint}")
