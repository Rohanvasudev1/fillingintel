"""Voyage embeddings over httpx (Step 5, ADR-0002).  No Voyage SDK.

API facts from https://docs.voyageai.com/reference/embeddings-api (read 2026-10-05):
POST ``/v1/embeddings`` with a bearer key; ``input_type`` is ``query`` or
``document``; ``truncation`` defaults to true, so it is always sent as false; the
response gives one ``usage.total_tokens`` per request, not per input.  Each call
here therefore embeds one text, so the token count the API reports belongs to
exactly one chunk.

The key is held only in the request header.  It never goes into a log line, an
exception message or a returned value.
"""
from __future__ import annotations

import logging
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType, TracebackType
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
API_KEY_ENV = "VOYAGE_API_KEY"
REQUEST_TIMEOUT_SECONDS = 60.0
MAX_ATTEMPTS = 6
BACKOFF_SECONDS = 2.0  # doubled after each failed attempt
MAX_RETRY_AFTER_SECONDS = 120.0
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_ERROR_BODY_CHARS = 300

InputType = Literal["query", "document"]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelSpec:
    """What this code relies on about one Voyage model (ADR-0002)."""

    name: str
    dimensions: int
    context_tokens: int


VOYAGE_4_LARGE = ModelSpec(name="voyage-4-large", dimensions=1024, context_tokens=32_000)
MODELS = MappingProxyType({VOYAGE_4_LARGE.name: VOYAGE_4_LARGE})
DEFAULT_MODEL = VOYAGE_4_LARGE.name


class VoyageError(RuntimeError):
    """The Voyage API failed, or returned something this code does not accept."""


class MissingApiKey(VoyageError):
    """``VOYAGE_API_KEY`` is not set."""


@dataclass(frozen=True)
class Embedding:
    """One embedded text and the token count Voyage reported for it."""

    vector: tuple[float, ...]
    api_token_count: int
    from_cache: bool = False  # True when read from the query cache, not the API


class Embedder(Protocol):
    """Embeds chunk text.  ``VoyageClient`` is the real one; tests replay recorded responses."""

    def embed_document(self, text: str, model: str) -> Embedding: ...


class QueryEmbedder(Protocol):
    """Embeds a question for retrieval, with ``input_type`` query."""

    def embed_query(self, text: str, model: str) -> Embedding: ...


class _Item(BaseModel):
    model_config = ConfigDict(extra="ignore")
    embedding: list[float]
    index: int


class _Usage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    total_tokens: int = Field(gt=0)


class _Response(BaseModel):
    model_config = ConfigDict(extra="ignore")
    data: list[_Item]
    model: str
    usage: _Usage


def _parse(body: object, model: str) -> Embedding:
    """Validate a one-input response against what was asked for."""
    try:
        response = _Response.model_validate(body)
    except ValidationError as exc:
        raise VoyageError(f"unexpected response shape: {exc.error_count()} errors") from exc
    if response.model != model:
        raise VoyageError(f"asked for {model}, response is from {response.model}")
    if len(response.data) != 1 or response.data[0].index != 0:
        raise VoyageError(f"expected one embedding, got {len(response.data)}")
    vector = response.data[0].embedding
    expected = MODELS[model].dimensions
    if len(vector) != expected:
        raise VoyageError(f"{model} vector has {len(vector)} dimensions, expected {expected}")
    if not all(math.isfinite(x) for x in vector):
        raise VoyageError("vector holds a non-finite value")
    return Embedding(vector=tuple(vector), api_token_count=response.usage.total_tokens)


class VoyageClient:
    """Calls the Voyage embeddings endpoint, one text per request, retrying rate limits."""

    def __init__(
        self,
        api_key: str,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not api_key:
            raise MissingApiKey(f"{API_KEY_ENV} is empty")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._http = http if http is not None else httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        self._sleep = sleep

    @classmethod
    def from_env(cls, http: httpx.Client | None = None) -> VoyageClient:
        """A client keyed from ``VOYAGE_API_KEY``; raises ``MissingApiKey`` if unset."""
        key = os.environ.get(API_KEY_ENV, "")
        if not key:
            raise MissingApiKey(f"{API_KEY_ENV} is not set (run with: uv run --env-file .env ...)")
        return cls(key, http=http)

    def __repr__(self) -> str:  # never show the key
        return "VoyageClient()"

    def __enter__(self) -> VoyageClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP connection pool."""
        self._http.close()

    def embed_document(self, text: str, model: str) -> Embedding:
        """Embed one chunk with ``input_type`` document and truncation off."""
        return self._embed(text, model, "document")

    def embed_query(self, text: str, model: str) -> Embedding:
        """Embed one question with ``input_type`` query and truncation off."""
        return self._embed(text, model, "query")

    def _embed(self, text: str, model: str, input_type: InputType) -> Embedding:
        if model not in MODELS:
            raise ValueError(f"unknown Voyage model {model!r}; known: {sorted(MODELS)}")
        payload = {"input": [text], "model": model, "input_type": input_type, "truncation": False}
        return _parse(self._post(payload), model)

    def _post(self, payload: dict[str, object]) -> object:
        """POST with backoff on 429, 5xx and transport errors; honours ``Retry-After``."""
        delay = BACKOFF_SECONDS
        for attempt in range(1, MAX_ATTEMPTS + 1):
            outcome = self._attempt(payload)
            if not isinstance(outcome, _Retry):
                return outcome
            if attempt == MAX_ATTEMPTS:
                raise VoyageError(f"gave up after {MAX_ATTEMPTS} attempts; last: {outcome.problem}")
            wait = outcome.wait if outcome.wait is not None else delay
            logger.warning("attempt %d of %d failed (%s); retrying in %.0f s",
                           attempt, MAX_ATTEMPTS, outcome.problem, wait)
            self._sleep(wait)
            delay *= 2
        raise AssertionError("unreachable")

    def _attempt(self, payload: dict[str, object]) -> object:
        """The parsed JSON body, a ``_Retry``, or a raised ``VoyageError``."""
        try:
            response = self._http.post(VOYAGE_URL, json=payload, headers=self._headers)
        except httpx.TransportError as exc:
            return _Retry(f"transport error {type(exc).__name__}", None)
        if response.status_code == 200:
            try:
                return response.json()
            except ValueError as exc:
                raise VoyageError("response body is not JSON") from exc
        problem = f"HTTP {response.status_code}: {response.text[:_ERROR_BODY_CHARS]}"
        if response.status_code not in _RETRY_STATUSES:
            raise VoyageError(problem)
        return _Retry(problem, _retry_after(response))


@dataclass(frozen=True)
class _Retry:
    problem: str
    wait: float | None  # seconds the server asked for, if it said


def _retry_after(response: httpx.Response) -> float | None:
    """``Retry-After`` in seconds, capped; None if absent or not a number of seconds."""
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS) if math.isfinite(seconds) else None
