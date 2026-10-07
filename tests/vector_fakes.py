"""Fakes and a loaded corpus for the vector arm tests, shared with the tracing tests.

Every chunk in the schema gets a unit vector at angle ``i * STEP`` in the plane
of the first two dimensions, ``i`` being its position in chunk-ID order.  The
fake query vector points along the first dimension, so cosine similarity is
``cos(i * STEP)``: the top 10 must be the first 10 chunks by ID, in that order,
among the chunks the question filter lets through.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable

from ingest.chunker import chunk_filing
from ingest.store import chunks_for_embedding, load_filing, save_embedding
from ingest.voyage import Embedding
from retrieve.answer_model import AnswerRequest, ApiResponse
from tests.anthropic_fixtures import load, with_text
from tests.conftest import FIXTURE_NAMES

MODEL = "voyage-4-large"
DIMS = 1024
STEP = 0.001  # radians; stays below pi/2 for fewer than 1,500 chunks, so the order is strict
FILTER_FIXTURES = ("nvda_10k", "nvda_10q", "intc_10k")
EMBED_TOKENS = 12
EXCERPT_ID = re.compile(r'<excerpt id="([^"]+)"')


def unit(angle: float) -> tuple[float, ...]:
    return (math.cos(angle), math.sin(angle)) + (0.0,) * (DIMS - 2)


class FakeQueryEmbedder:
    def __init__(self, vector=unit(0.0), error: Exception | None = None, cached=False):
        self.vector = vector
        self.cached = cached
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def embed_query(self, text: str, model: str) -> Embedding:
        self.calls.append((text, model))
        if self.error is not None:
            raise self.error
        return Embedding(vector=self.vector, api_token_count=EMBED_TOKENS, from_cache=self.cached)


class ScriptedAnswerModel:
    """Replays the recorded answer response with text written from the excerpt IDs it is sent."""

    def __init__(
        self,
        script: Callable[[list[str]], str] = lambda ids: f"STATUS: answered\nIt rose [{ids[0]}].",
        error: Exception | None = None,
        cached: bool = False,
    ):
        self._script = script
        self._error = error
        self._cached = cached
        self.requests: list[AnswerRequest] = []

    def complete(self, request: AnswerRequest) -> ApiResponse:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        text = self._script(EXCERPT_ID.findall(request.user))
        return ApiResponse(with_text(load("answered_q0072")["response"], text), api_ms=4200.0,
                           from_cache=self._cached)


def embed_fixture_filings(db_conn, fixture_records) -> list[str]:
    """Load NVIDIA's two fixture filings and Intel's 10-K, and embed every chunk in the
    schema; returns chunk IDs in order."""
    db_conn.execute("DELETE FROM chunk_embeddings")
    db_conn.commit()
    for name in FILTER_FIXTURES:
        record = fixture_records[FIXTURE_NAMES.index(name)]
        load_filing(db_conn, record, chunk_filing(record.filing))
    chunks = chunks_for_embedding(db_conn, MODEL)
    assert len(chunks) < math.pi / 2 / STEP
    for i, chunk in enumerate(chunks):
        save_embedding(db_conn, chunk.chunk_id, MODEL, chunk.text, unit(i * STEP), 1)
    return [c.chunk_id for c in chunks]
