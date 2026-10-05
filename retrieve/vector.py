"""The control arm: exact cosine top-k over voyage-4-large chunk vectors (Step 5).

It applies the question filter (ADR-0001) in SQL and writes no answer yet.
Before the first question the arm checks that every stored chunk has a current
vector, so a partial embed run cannot silently cap recall, and reads the filing
list the filter resolves against.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import MappingProxyType

import psycopg

from ingest.corpus import TICKER_BY_CIK
from ingest.store import chunker_versions, embedding_gaps, list_filings, nearest_chunks
from ingest.voyage import API_KEY_ENV, DEFAULT_MODEL, QueryEmbedder, VoyageClient, VoyageError
from retrieve.arm import ArmConfig, ArmError, ArmResult, ArmSpec, RetrievedChunk
from retrieve.query_cache import CachedQueryEmbedder, CacheError
from retrieve.question_filter import (
    CorpusFiling,
    check_filings,
    companies_without_chunks,
    parse_question_filter,
)

TOP_K = 10
_MS_PER_SECOND = 1000.0


class VectorArm:
    """Embeds the question (input type ``query``) and returns the top *k* chunks
    within the question's filter."""

    name = "vector"

    def __init__(
        self,
        conn: psycopg.Connection,
        embedder: QueryEmbedder,
        model: str = DEFAULT_MODEL,
        k: int = TOP_K,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self._conn = conn
        self._embedder = embedder
        self._model = model
        self._k = k
        self._clock = clock
        self.config = ArmConfig(
            models=MappingProxyType({"embedding": model}),
            k=k,
            chunker_version=_check_corpus(conn, model),
        )
        self._filings = _corpus_filings(conn)

    def run(self, question: str) -> ArmResult:
        """Retrieve for one question; any failure is raised as ``ArmError``."""
        start = self._clock()
        question_filter = parse_question_filter(question, self._filings)
        try:
            embedding = self._embedder.embed_query(question, self._model)
            embedded = self._clock()
            rows = nearest_chunks(
                self._conn, self._model, embedding.vector, self._k, question_filter.accession_nos
            )
        except (VoyageError, CacheError, psycopg.Error) as exc:
            raise ArmError(f"vector retrieval failed ({type(exc).__name__}): {exc}") from exc
        searched = self._clock()
        return ArmResult(
            retrieved=tuple(RetrievedChunk(chunk_id, score) for chunk_id, score in rows),
            question_filter=question_filter,
            companies_without_chunks=companies_without_chunks(
                question_filter, (chunk_id for chunk_id, _ in rows), self._filings
            ),
            embed_ms=(embedded - start) * _MS_PER_SECOND,
            search_ms=(searched - embedded) * _MS_PER_SECOND,
            query_cached=embedding.from_cache,
        )


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
    """The vector arm on ``DATABASE_URL``, with Voyage and the disk query cache."""
    try:
        conn = psycopg.connect(os.environ["DATABASE_URL"])
    except psycopg.Error as exc:  # the message can quote the URL, so only the type is shown
        raise ArmError(f"could not connect to the database: {type(exc).__name__}") from exc
    with conn, VoyageClient.from_env() as client:
        yield VectorArm(conn, CachedQueryEmbedder(client))


VECTOR = ArmSpec(
    name=VectorArm.name, required_env=(API_KEY_ENV, "DATABASE_URL"), open=open_vector_arm
)
