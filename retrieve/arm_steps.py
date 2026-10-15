"""The steps every arm shares: the corpus filing list, the price check, the query
embedding and the cited answer written from the retrieved chunks (Step 5;
shared since Step 11).

Arms differ only in how they choose chunks (ADR-0007), so the answer step lives
here once: each chunk's text is resolved from Postgres, the unchanged answer
prompt is sent, and sentences citing chunks outside the retrieval are dropped
(``retrieve.answer``).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields
from types import MappingProxyType

import psycopg

from ingest.corpus import TICKER_BY_CIK
from ingest.store import ChunkNotFound, chunker_versions, get_chunk, list_filings, resolve
from ingest.voyage import Embedding, QueryEmbedder
from retrieve.answer import Answer, write_answer
from retrieve.answer_model import (
    ANSWER_EFFORT,
    ANSWER_MAX_TOKENS,
    ANSWER_MODEL,
    AnswerModel,
    AnswerModelError,
    AnswerRequest,
    ApiResponse,
    ModelRequest,
)
from retrieve.answer_prompt import AnswerPrompt, PromptError, SourceChunk, load_prompt
from retrieve.arm import ArmError, ArmResult, Retrieval
from retrieve.pricing import PRICE_TABLE_DATE, PRICES, embedding_cost
from retrieve.query_cache import CacheError
from retrieve.question_filter import CorpusFiling, check_filings
from retrieve.tracing import (
    EMBED_SPAN,
    GENERATION_SPAN,
    Kind,
    SpanRecorder,
    embedding_attributes,
    embedding_result_attributes,
    generation_attributes,
    generation_request_attributes,
)

# Raised as ArmError.  OSError: a cache file could not be written.
ANSWER_ERRORS = (CacheError, psycopg.Error, ChunkNotFound, AnswerModelError, OSError)


class ChunkAnswerer:
    """Has the answer model write a cited answer from a retrieval's chunks, in order."""

    def __init__(self, arm_name: str, conn: psycopg.Connection, answer_model: AnswerModel,
                 prompt: AnswerPrompt, filings: Sequence[CorpusFiling], spans: SpanRecorder):
        self._arm_name = arm_name
        self._conn = conn
        self._answer_model = answer_model
        self._prompt = prompt
        self._spans = spans
        self._tickers = filing_tickers(filings)

    def answer(self, question: str, retrieval: Retrieval) -> ArmResult:
        """The arm result for *retrieval*; any failure is raised as ``ArmError``."""
        try:
            chunks = source_chunks(self._conn, self._tickers,
                                   [r.chunk_id for r in retrieval.retrieved])
            answer = self._write_answer(question, chunks)
        except ANSWER_ERRORS as exc:
            raise arm_error(self._arm_name, exc) from exc
        return arm_result(retrieval, answer, chunks)

    def _write_answer(self, question: str, chunks: Sequence[SourceChunk]) -> Answer:
        attributes = generation_request_attributes(ANSWER_MODEL, ANSWER_EFFORT, ANSWER_MAX_TOKENS,
                                                   self._prompt.version)
        with self._spans.span(GENERATION_SPAN, Kind.LLM, attributes) as span:
            sent = _SentRequests(self._answer_model)
            answer = write_answer(question, chunks, sent, self._prompt)
            messages = ((sent.last.system, sent.last.user)
                        if self._spans.capture_text and sent.last is not None else None)
            span.set_attributes(generation_attributes(answer, messages))
        return answer


def arm_result(retrieval: Retrieval, answer: Answer,
               sources: Sequence[SourceChunk]) -> ArmResult:
    """*retrieval* with the answer written from it and the chunks the model saw."""
    retrieval_fields = {f.name: getattr(retrieval, f.name) for f in fields(Retrieval)}
    return ArmResult(**retrieval_fields, answer=answer, sources=tuple(sources))


def source_chunks(conn: psycopg.Connection, tickers: Mapping[str, str],
                  chunk_ids: Sequence[str]) -> tuple[SourceChunk, ...]:
    """Each chunk's resolved text and the source a model is told about, in order.

    *tickers* maps the corpus's accession numbers to tickers; a chunk from another
    filing raises ``ArmError``.
    """
    chunks = [get_chunk(conn, chunk_id) for chunk_id in chunk_ids]
    unknown = sorted({c.accession_no for c in chunks} - set(tickers))
    if unknown:
        raise ArmError(f"retrieved chunks from filings outside the corpus: {unknown}")
    return tuple(
        SourceChunk(c.chunk_id, tickers[c.accession_no], c.form_type, c.fiscal_period,
                    c.section, resolve(conn, c.chunk_id))
        for c in chunks
    )


def filing_tickers(filings: Sequence[CorpusFiling]) -> Mapping[str, str]:
    """Each corpus filing's ticker, by accession number."""
    return MappingProxyType({f.accession_no: f.ticker for f in filings})


class _SentRequests:
    """Passes requests to the answer model and keeps the last one, so a span shows the
    prompt exactly as it was sent."""

    def __init__(self, inner: AnswerModel):
        self._inner = inner
        self.last: AnswerRequest | None = None

    def complete(self, request: ModelRequest) -> ApiResponse:
        if isinstance(request, AnswerRequest):
            self.last = request
        return self._inner.complete(request)


def embed_question(embedder: QueryEmbedder, model: str, question: str,
                   spans: SpanRecorder) -> Embedding:
    """The question's query embedding, recorded as a span."""
    with spans.span(EMBED_SPAN, Kind.EMBEDDING, embedding_attributes(model, question)) as span:
        embedding = embedder.embed_query(question, model)
        cost = embedding_cost(model, embedding.api_token_count)
        span.set_attributes(embedding_result_attributes(embedding, cost))
    return embedding


def arm_error(arm_name: str, exc: Exception) -> ArmError:
    return ArmError(f"{arm_name} arm failed ({type(exc).__name__}): {exc}")


def check_prices(models: Sequence[str]) -> None:
    """Refuse to start an arm whose cost could not be reported."""
    unpriced = [m for m in models if m not in PRICES]
    if unpriced:
        raise ArmError(f"no price for {unpriced} in the {PRICE_TABLE_DATE} price table")


def load_answer_prompt() -> AnswerPrompt:
    try:
        return load_prompt()
    except PromptError as exc:
        raise ArmError(str(exc)) from exc


def corpus_filings(conn: psycopg.Connection) -> tuple[CorpusFiling, ...]:
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


def corpus_chunker_version(conn: psycopg.Connection) -> str:
    """The corpus's one chunker version; raises ``ArmError`` for none or several."""
    versions = chunker_versions(conn)
    if len(versions) != 1:
        raise ArmError(f"expected one chunker_version in the corpus, found {versions}")
    return versions[0]
