"""The control arm: exact cosine top-k over voyage-4-large chunk vectors (Step 5).

It applies the question filter (ADR-0001) in SQL, then passes all k retrieved
chunks to the answer model, which writes an answer that cites them; sentences
whose citations were not retrieved are dropped (``retrieve.answer``).  Before the
first question the arm checks that every stored chunk has a current vector, so
a partial embed run cannot silently cap recall, and reads the filing list the
filter resolves against.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType

import psycopg

from ingest.corpus import TICKER_BY_CIK
from ingest.store import (
    ChunkNotFound,
    chunker_versions,
    embedding_gaps,
    get_chunk,
    list_filings,
    nearest_chunks,
    resolve,
)
from ingest.voyage import API_KEY_ENV, DEFAULT_MODEL, QueryEmbedder, VoyageClient, VoyageError
from retrieve.answer import write_answer
from retrieve.answer_model import (
    ANSWER_EFFORT,
    ANSWER_MAX_TOKENS,
    ANSWER_MODEL,
    AnswerModel,
    AnswerModelError,
    AnthropicAnswerModel,
)
from retrieve.answer_model import API_KEY_ENV as ANTHROPIC_KEY_ENV
from retrieve.answer_prompt import AnswerPrompt, PromptError, SourceChunk, load_prompt
from retrieve.arm import ArmConfig, ArmError, ArmResult, ArmSpec, RetrievedChunk
from retrieve.pricing import PRICE_TABLE_DATE, PRICES, embedding_cost
from retrieve.query_cache import CachedQueryEmbedder, CacheError
from retrieve.question_filter import (
    CorpusFiling,
    check_filings,
    companies_without_chunks,
    parse_question_filter,
)
from retrieve.response_cache import CachedAnswerModel

TOP_K = 10
_MS_PER_SECOND = 1000.0


@dataclass(frozen=True)
class _Retrieval:
    rows: list[tuple[str, float]]  # (chunk_id, cosine similarity), best first
    embed_ms: float
    search_ms: float
    query_cached: bool
    embed_tokens: int


class VectorArm:
    """Embeds the question (input type ``query``), retrieves the top *k* chunks within
    the question's filter, and has the answer model write a cited answer from them."""

    name = "vector"

    def __init__(
        self,
        conn: psycopg.Connection,
        embedder: QueryEmbedder,
        answer_model: AnswerModel,
        model: str = DEFAULT_MODEL,
        k: int = TOP_K,
        clock: Callable[[], float] = time.perf_counter,
        prompt: AnswerPrompt | None = None,
    ):
        self._conn = conn
        self._embedder = embedder
        self._answer_model = answer_model
        self._model = model
        self._k = k
        self._clock = clock
        self._prompt = prompt if prompt is not None else _load_prompt()
        _check_prices((model, ANSWER_MODEL))
        self.config = ArmConfig(
            models=MappingProxyType({"embedding": model, "answer": ANSWER_MODEL}),
            k=k,
            chunker_version=_check_corpus(conn, model),
            efforts=MappingProxyType({"answer": ANSWER_EFFORT}),
            answer_prompt=self._prompt,
            answer_max_tokens=ANSWER_MAX_TOKENS,
        )
        self._filings = _corpus_filings(conn)
        self._tickers = MappingProxyType({f.accession_no: f.ticker for f in self._filings})

    def run(self, question: str) -> ArmResult:
        """Retrieve and answer for one question; any failure is raised as ``ArmError``."""
        question_filter = parse_question_filter(question, self._filings)
        try:
            retrieval = self._retrieve(question, question_filter.accession_nos)
            chunks = self._source_chunks([chunk_id for chunk_id, _ in retrieval.rows])
            answer = write_answer(question, chunks, self._answer_model, self._prompt)
        except (VoyageError, CacheError, psycopg.Error, ChunkNotFound, AnswerModelError,
                OSError) as exc:  # OSError: a cache file could not be written
            raise ArmError(f"vector arm failed ({type(exc).__name__}): {exc}") from exc
        return ArmResult(
            retrieved=tuple(RetrievedChunk(chunk_id, score) for chunk_id, score in retrieval.rows),
            question_filter=question_filter,
            companies_without_chunks=companies_without_chunks(
                question_filter, (chunk_id for chunk_id, _ in retrieval.rows), self._filings
            ),
            embed_ms=retrieval.embed_ms,
            search_ms=retrieval.search_ms,
            query_cached=retrieval.query_cached,
            embed_tokens=retrieval.embed_tokens,
            embed_cost_usd=embedding_cost(self._model, retrieval.embed_tokens),
            answer=answer,
        )

    def _retrieve(self, question: str, accession_nos: Sequence[str] | None) -> _Retrieval:
        start = self._clock()
        embedding = self._embedder.embed_query(question, self._model)
        embedded = self._clock()
        rows = nearest_chunks(self._conn, self._model, embedding.vector, self._k, accession_nos)
        searched = self._clock()
        return _Retrieval(
            rows=rows,
            embed_ms=(embedded - start) * _MS_PER_SECOND,
            search_ms=(searched - embedded) * _MS_PER_SECOND,
            query_cached=embedding.from_cache,
            embed_tokens=embedding.api_token_count,
        )


    def _source_chunks(self, chunk_ids: Sequence[str]) -> tuple[SourceChunk, ...]:
        """Each chunk's resolved text and the source the answer model is told about."""
        chunks = [get_chunk(self._conn, chunk_id) for chunk_id in chunk_ids]
        unknown = sorted({c.accession_no for c in chunks} - set(self._tickers))
        if unknown:
            raise ArmError(f"retrieved chunks from filings outside the corpus: {unknown}")
        return tuple(
            SourceChunk(c.chunk_id, self._tickers[c.accession_no], c.form_type, c.fiscal_period,
                        c.section, resolve(self._conn, c.chunk_id))
            for c in chunks
        )


def _check_prices(models: Sequence[str]) -> None:
    """Refuse to start an arm whose cost could not be reported."""
    unpriced = [m for m in models if m not in PRICES]
    if unpriced:
        raise ArmError(f"no price for {unpriced} in the {PRICE_TABLE_DATE} price table")


def _load_prompt() -> AnswerPrompt:
    try:
        return load_prompt()
    except PromptError as exc:
        raise ArmError(str(exc)) from exc


def _check_corpus(conn: psycopg.Connection, model: str) -> str:
    """The corpus's one chunker version; raises ``ArmError`` if vectors are missing or stale."""
    gaps = embedding_gaps(conn, model)
    if gaps.missing:
        raise ArmError(f"{gaps.missing} chunks have no {model} vector; run python -m ingest.embed")
    if gaps.stale:
        raise ArmError(
            f"{gaps.stale} chunks have a stale {model} vector; run python -m ingest.embed"
        )
    versions = chunker_versions(conn)
    if len(versions) != 1:
        raise ArmError(f"expected one chunker_version in the corpus, found {versions}")
    return versions[0]


def _corpus_filings(conn: psycopg.Connection) -> tuple[CorpusFiling, ...]:
    """The stored filings the question filter resolves against; raises ``ArmError`` if unusable."""
    rows = list_filings(conn)
    unknown = sorted({r.cik for r in rows if r.cik not in TICKER_BY_CIK})
    if unknown:
        raise ArmError(f"the filings table has CIKs outside the corpus: {unknown}")
    filings = tuple(
        CorpusFiling(r.accession_no, TICKER_BY_CIK[r.cik], r.form_type, r.fiscal_period,
                     r.report_date)
        for r in rows
    )
    try:
        check_filings(filings)
    except ValueError as exc:
        raise ArmError(f"cannot build the question filter: {exc}") from exc
    return filings


@contextmanager
def open_vector_arm() -> Iterator[VectorArm]:
    """The vector arm on ``DATABASE_URL``, with Voyage, Claude and their disk caches."""
    try:
        conn = psycopg.connect(os.environ["DATABASE_URL"])
    except psycopg.Error as exc:  # the message can quote the URL, so only the type is shown
        raise ArmError(f"could not connect to the database: {type(exc).__name__}") from exc
    with conn, VoyageClient.from_env() as voyage, AnthropicAnswerModel.from_env() as claude:
        yield VectorArm(conn, CachedQueryEmbedder(voyage), CachedAnswerModel(claude))


VECTOR = ArmSpec(
    name=VectorArm.name,
    required_env=(API_KEY_ENV, ANTHROPIC_KEY_ENV, "DATABASE_URL"),
    open=open_vector_arm,
)
