"""A disk cache of query embeddings, keyed by model and text hash (Step 5).

Rerunning the same questions makes no embedding calls.  Each vector is one file,
``{cache_dir}/{model}/{sha256 of the text}.json``, written atomically.  The
question text itself is not stored.  A file that cannot be read or does not
match the model is an error, never a silent miss, so a damaged cache is noticed.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

from ingest.parsed_files import text_sha256
from ingest.voyage import MODELS, Embedding, QueryEmbedder

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache" / "query_embeddings"
_FIELDS = frozenset({"model", "text_sha256", "vector", "api_token_count"})


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
    vector = stored["vector"]
    expected = MODELS[model].dimensions
    if not isinstance(vector, list) or len(vector) != expected:
        raise CacheError(f"query cache file {path.name}: vector lacks {expected} dimensions")
    if not all(isinstance(x, int | float) and math.isfinite(x) for x in vector):
        raise CacheError(f"query cache file {path.name}: vector holds a non-numeric value")
    tokens = stored["api_token_count"]
    if not isinstance(tokens, int) or tokens <= 0:
        raise CacheError(f"query cache file {path.name}: bad api_token_count")
    return Embedding(
        vector=tuple(float(x) for x in vector), api_token_count=tokens, from_cache=True
    )


def _write(path: Path, model: str, sha: str, embedding: Embedding) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "model": model,
        "text_sha256": sha,
        "vector": list(embedding.vector),
        "api_token_count": embedding.api_token_count,
    }
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as fh:
        json.dump(body, fh)
    os.replace(fh.name, path)
