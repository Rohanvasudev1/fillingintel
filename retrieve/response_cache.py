"""A disk cache of Messages API responses, keyed by model, effort and prompt hash (Step 5).

Claude 5.5 models accept no sampling settings, so reruns reproduce answers by
replaying stored responses: a rerun makes no new answer calls.  Each response is
one file, ``{cache_dir}/{model}/{effort}/{request SHA-256}.json``, holding the
response body and how long the original call took, so latency statistics stay
meaningful on a replay.  The key hashes the whole request, ``max_tokens``
included, so changing any part of it (even ``max_tokens``) makes every answer a
cache miss.  The prompt itself is not stored.  A file that cannot be
read or does not match its request is an error, never a silent miss.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from ingest.atomic_json import write_json_atomic
from ingest.provenance import REPO_ROOT
from retrieve.answer_model import AnswerModel, AnswerRequest, ApiResponse
from retrieve.query_cache import CacheError

DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache" / "responses"
_FIELDS = frozenset({"key", "model", "effort", "api_ms", "response"})


class CachedAnswerModel:
    """Wraps an ``AnswerModel``; makes each distinct request at most once."""

    def __init__(self, inner: AnswerModel, cache_dir: Path = DEFAULT_CACHE_DIR):
        self._inner = inner
        self._dir = cache_dir

    def complete(self, request: AnswerRequest) -> ApiResponse:
        """The stored response to *request* (``from_cache`` set), or a fresh one, then stored."""
        key = request.cache_key()
        path = self._dir / request.model / request.effort / f"{key}.json"
        if path.exists():
            return _read(path, request, key)
        response = self._inner.complete(request)
        write_json_atomic(path, {
            "key": key,
            "model": request.model,
            "effort": request.effort,
            "api_ms": response.api_ms,
            "response": dict(response.body),
        })
        return response


def _read(path: Path, request: AnswerRequest, key: str) -> ApiResponse:
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CacheError(f"cannot read response cache file {path.name}: "
                         f"{type(exc).__name__}") from exc
    if not isinstance(stored, dict) or set(stored) != _FIELDS:
        raise CacheError(f"response cache file {path.name} has unexpected fields")
    if (stored["key"], stored["model"], stored["effort"]) != (key, request.model, request.effort):
        raise CacheError(f"response cache file {path.name} is for another request")
    api_ms = stored["api_ms"]
    if not isinstance(api_ms, int | float) or not math.isfinite(api_ms) or api_ms < 0:
        raise CacheError(f"response cache file {path.name}: bad api_ms")
    if not isinstance(stored["response"], dict):
        raise CacheError(f"response cache file {path.name}: response is not an object")
    return ApiResponse(body=stored["response"], api_ms=float(api_ms), from_cache=True)
