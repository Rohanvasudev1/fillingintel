"""The control arm: exact cosine top-k over voyage-4-large chunk vectors (Step 5).

It applies the question filter (ADR-0001) in SQL, then passes all k retrieved
chunks to the answer model, which writes an answer that cites them; sentences
whose citations were not retrieved are dropped (``retrieve.answer``).  Before the
first question the arm checks that every stored chunk has a current vector, so
a partial embed run cannot silently cap recall, and reads the filing list the
filter resolves against.

Given a span recorder, the arm records the question filter, the query
embedding, the search and the answer call as OpenInference spans
(``retrieve.tracing``); without one it records nothing.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType

import psycopg

from ingest.store import embedding_gaps, nearest_chunks
from ingest.voyage import (
    API_KEY_ENV,
    DEFAULT_MODEL,
    QueryEmbedder,
    VoyageClient,
    VoyageError,
)
from retrieve.answer_model import (
    ANSWER_EFFORT,
    ANSWER_MAX_TOKENS,
    ANSWER_MODEL,
    AnswerModel,
    AnthropicAnswerModel,
)
from retrieve.answer_model import API_KEY_ENV as ANTHROPIC_KEY_ENV
from retrieve.answer_prompt import AnswerPrompt
from retrieve.arm import ArmConfig, ArmError, ArmResult, ArmSpec, Retrieval, RetrievedChunk
from retrieve.arm_steps import (
    ChunkAnswerer,
    arm_error,
    check_prices,
    corpus_chunker_version,
    corpus_filings,
    embed_question,
    load_answer_prompt,
)
from retrieve.pricing import embedding_cost
from retrieve.query_cache import CachedQueryEmbedder, CacheError
from retrieve.question_filter import companies_without_chunks, parse_question_filter
from retrieve.response_cache import CachedAnswerModel
from retrieve.run_estimate import VECTOR_RUN, MeasuredEstimate
from retrieve.tracing import (
    FILTER_SPAN,
    SEARCH_SPAN,
    Kind,
    SpanRecorder,
    filter_attributes,
    question_input,
    retrieval_attributes,
)

TOP_K = 10
_MS_PER_SECOND = 1000.0
# Raised as ArmError.  OSError: a cache file could not be written.
_RETRIEVAL_ERRORS = (VoyageError, CacheError, psycopg.Error, OSError)


class VectorArm:
    """Embeds the question (input type ``query``), retrieves the top *k* chunks within
    the question's filter, and has the answer model write a cited answer from them.

    With no answer model the arm can only ``retrieve``, which is what the offline
    quality gate needs (ADR-0003).
    """

    name = "vector"

    def __init__(
        self,
        conn: psycopg.Connection,
        embedder: QueryEmbedder,
        answer_model: AnswerModel | None,
        model: str = DEFAULT_MODEL,
        k: int = TOP_K,
        clock: Callable[[], float] = time.perf_counter,
        prompt: AnswerPrompt | None = None,
        spans: SpanRecorder | None = None,
    ):
        self._conn = conn
        self._embedder = embedder
        self._model = model
        self._k = k
        self._clock = clock
        self._prompt = prompt if prompt is not None else load_answer_prompt()
        self._spans = spans if spans is not None else SpanRecorder.off()
        check_prices((model, ANSWER_MODEL))
        self.config = ArmConfig(
            models=MappingProxyType({"embedding": model, "answer": ANSWER_MODEL}),
            k=k,
            chunker_version=_check_corpus(conn, model),
            efforts=MappingProxyType({"answer": ANSWER_EFFORT}),
            answer_prompt=self._prompt,
            answer_max_tokens=ANSWER_MAX_TOKENS,
        )
        self._filings = corpus_filings(conn)
        self._answerer = (None if answer_model is None else
                          ChunkAnswerer(self.name, conn, answer_model, self._prompt,
                                        self._filings, self._spans))

    def run(self, question: str) -> ArmResult:
        """Retrieve and answer for one question; any failure is raised as ``ArmError``."""
        if self._answerer is None:
            raise ArmError("the vector arm was opened with no answer model; use retrieve()")
        return self._answerer.answer(question, self.retrieve(question))

    def retrieve(self, question: str) -> Retrieval:
        """Filter, embed and search for one question, with no answer model call.

        ``run`` answers from exactly this result.  Any failure is raised as ``ArmError``.
        """
        with self._spans.span(FILTER_SPAN, Kind.CHAIN, question_input(question)) as span:
            question_filter = parse_question_filter(question, self._filings)
            span.set_attributes(filter_attributes(question_filter))
        try:
            start = self._clock()
            embedding = embed_question(self._embedder, self._model, question, self._spans)
            embedded = self._clock()
            retrieved = self._search(question, embedding.vector, question_filter.accession_nos)
            searched = self._clock()
        except _RETRIEVAL_ERRORS as exc:
            raise arm_error(self.name, exc) from exc
        return Retrieval(
            retrieved=retrieved,
            question_filter=question_filter,
            companies_without_chunks=companies_without_chunks(
                question_filter, (r.chunk_id for r in retrieved), self._filings
            ),
            embed_ms=(embedded - start) * _MS_PER_SECOND,
            search_ms=(searched - embedded) * _MS_PER_SECOND,
            query_cached=embedding.from_cache,
            embed_tokens=embedding.api_token_count,
            embed_cost_usd=embedding_cost(self._model, embedding.api_token_count),
        )

    def _search(self, question: str, vector: Sequence[float],
                accession_nos: Sequence[str] | None) -> tuple[RetrievedChunk, ...]:
        with self._spans.span(SEARCH_SPAN, Kind.RETRIEVER, question_input(question)) as span:
            rows = nearest_chunks(self._conn, self._model, vector, self._k, accession_nos)
            retrieved = tuple(RetrievedChunk(chunk_id, score) for chunk_id, score in rows)
            span.set_attributes(retrieval_attributes(retrieved))
        return retrieved


def _check_corpus(conn: psycopg.Connection, model: str) -> str:
    """The corpus's one chunker version; raises ``ArmError`` if vectors are missing or stale."""
    gaps = embedding_gaps(conn, model)
    if gaps.missing:
        raise ArmError(f"{gaps.missing} chunks have no {model} vector; run python -m ingest.embed")
    if gaps.stale:
        raise ArmError(
            f"{gaps.stale} chunks have a stale {model} vector; run python -m ingest.embed"
        )
    return corpus_chunker_version(conn)


@contextmanager
def open_vector_arm(response_cache: Path,
                    spans: SpanRecorder | None = None) -> Iterator[VectorArm]:
    """The vector arm on ``DATABASE_URL``, with Voyage, Claude and their disk caches."""
    try:
        conn = psycopg.connect(os.environ["DATABASE_URL"])
    except psycopg.Error as exc:  # the message can quote the URL, so only the type is shown
        raise ArmError(f"could not connect to the database: {type(exc).__name__}") from exc
    with conn, VoyageClient.from_env() as voyage, AnthropicAnswerModel.from_env() as claude:
        yield VectorArm(conn, CachedQueryEmbedder(voyage),
                        CachedAnswerModel(claude, response_cache), spans=spans)


VECTOR = ArmSpec(
    name=VectorArm.name,
    required_env=(API_KEY_ENV, ANTHROPIC_KEY_ENV, "DATABASE_URL"),
    open=open_vector_arm,
    estimate=MeasuredEstimate(VECTOR_RUN, arm="vector"),
)
