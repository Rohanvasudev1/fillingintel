"""The graph_local arm: anchors, their evidence and their edges' evidence (Step 11).

The question is linked to anchors (``retrieve.anchors``), each anchor's chunks are
collected from Neo4j and ranked (``retrieve.graph_expand``), and at most 10
chunks go to the unchanged answer prompt, as for ``vector`` (ADR-0007).  The
question filter (ADR-0001) limits both the anchors and the chunks.  No anchor,
or no chunk left after the filter, gives an empty retrieval; there is no
fallback to ``vector``.

Before the first question the arm checks that ``:GraphMeta`` records the code's
ontology and that every node text has a cached embedding, so it never embeds a
node itself; ``python -m retrieve.node_embed`` does that, with its estimate first.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import psycopg
from neo4j import Driver
from neo4j.exceptions import DriverError, Neo4jError

from graph.connection import DATABASE, GraphSettingsMissing, driver_from_env, read_session
from graph.constraints import GRAPH_META_QUERY, graph_meta_problem
from graph.load import OUTPUT_DIR as LOAD_REPORT_DIR
from graph.load_report import latest_load_run
from graph.ontology import ONTOLOGY_VERSION
from graph.schema_text import SCHEMA_TEXT_SHA256
from ingest.voyage import API_KEY_ENV, DEFAULT_MODEL, QueryEmbedder, VoyageClient, VoyageError
from retrieve.anchors import ANCHOR_LIMIT, AnchorIndex
from retrieve.answer_model import (
    ANSWER_EFFORT,
    ANSWER_MAX_TOKENS,
    ANSWER_MODEL,
    AnswerModel,
    AnthropicAnswerModel,
)
from retrieve.answer_model import API_KEY_ENV as ANTHROPIC_KEY_ENV
from retrieve.answer_prompt import AnswerPrompt
from retrieve.arm import (
    Anchor,
    ArmConfig,
    ArmError,
    ArmResult,
    ArmSpec,
    GraphProvenance,
    GraphRetrieval,
    Retrieval,
    RetrievedChunk,
    StageCost,
)
from retrieve.arm_steps import (
    ChunkAnswerer,
    arm_error,
    check_prices,
    corpus_chunker_version,
    corpus_filings,
    embed_question,
    load_answer_prompt,
)
from retrieve.graph_expand import expand, rank_chunks
from retrieve.graph_nodes import NODE_INPUT_TYPE, distinct_texts, read_anchor_nodes
from retrieve.pricing import embedding_cost
from retrieve.query_cache import CachedBatchEmbedder, CachedQueryEmbedder, CacheError
from retrieve.question_filter import QuestionFilter, companies_without_chunks, parse_question_filter
from retrieve.response_cache import CachedAnswerModel
from retrieve.tracing import (
    ANCHOR_SPAN,
    EXPAND_SPAN,
    FILTER_SPAN,
    Kind,
    SpanRecorder,
    anchor_attributes,
    filter_attributes,
    question_input,
    retrieval_attributes,
)

TOP_K = 10
# 1 or 2 hops, chosen on dev retrieval-only scores (Step 11 ticket 05, BUILD-LOG).
HOPS = 1
NEO4J_ENV = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")
_MS_PER_SECOND = 1000.0
# Raised as ArmError.  OSError: a cache file could not be written.
_GRAPH_ERRORS = (Neo4jError, DriverError)
_RETRIEVAL_ERRORS = (VoyageError, CacheError, OSError, *_GRAPH_ERRORS)


@dataclass(frozen=True)
class LocalResult:
    """What one local search found: the ranked chunks and the anchors they came from."""

    retrieved: tuple[RetrievedChunk, ...]
    anchors: tuple[Anchor, ...]
    hops: int
    anchor_ms: float


class LocalSearch:
    """Anchor linking and expansion over one graph, for a question already filtered and
    embedded.  ``graph_traversal`` falls back to it."""

    def __init__(self, driver: Driver, index: AnchorIndex, hops: int = HOPS, k: int = TOP_K,
                 clock: Callable[[], float] = time.perf_counter,
                 spans: SpanRecorder | None = None, database: str = DATABASE):
        self._driver = driver
        self._index = index
        self.hops = hops
        self._k = k
        self._clock = clock
        self._spans = spans if spans is not None else SpanRecorder.off()
        self._database = database

    def search(self, question: str, question_filter: QuestionFilter,
               query_vector: Sequence[float]) -> LocalResult:
        """The ranked chunks; raises ``Neo4jError`` or ``DriverError`` on a graph failure."""
        accession_nos = question_filter.accession_nos
        with self._spans.span(ANCHOR_SPAN, Kind.CHAIN, question_input(question)) as span:
            start = self._clock()
            anchors = self._index.link(question, query_vector, accession_nos, ANCHOR_LIMIT)
            linked = self._clock()
            span.set_attributes(anchor_attributes(anchors))
        with self._spans.span(EXPAND_SPAN, Kind.RETRIEVER, question_input(question)) as span:
            reach = expand(self._driver, anchors, self.hops, self._database) if anchors else {}
            retrieved = rank_chunks(anchors, reach, accession_nos, self._k)
            span.set_attributes(retrieval_attributes(retrieved))
        return LocalResult(retrieved, anchors, self.hops, (linked - start) * _MS_PER_SECOND)


class GraphLocalArm:
    """Embeds the question (input type ``query``), links it to anchors, ranks their
    evidence chunks within the question's filter, and has the answer model write a
    cited answer from at most *k* of them.

    With no answer model the arm can only ``retrieve``, which is what retrieval-only
    scoring needs.
    """

    name = "graph_local"

    def __init__(
        self,
        conn: psycopg.Connection,
        driver: Driver,
        embedder: QueryEmbedder,
        node_embeddings: CachedBatchEmbedder,
        answer_model: AnswerModel | None,
        model: str = DEFAULT_MODEL,
        hops: int = HOPS,
        k: int = TOP_K,
        clock: Callable[[], float] = time.perf_counter,
        prompt: AnswerPrompt | None = None,
        spans: SpanRecorder | None = None,
        load_reports: Path = LOAD_REPORT_DIR,
        database: str = DATABASE,
    ):
        self._embedder = embedder
        self._model = model
        self._clock = clock
        self._spans = spans if spans is not None else SpanRecorder.off()
        prompt = prompt if prompt is not None else load_answer_prompt()
        check_prices((model, ANSWER_MODEL))
        try:
            provenance = _provenance(driver, database, load_reports)
            index = _anchor_index(driver, database, node_embeddings, model)
        except (*_GRAPH_ERRORS, CacheError, OSError, ValueError) as exc:
            raise arm_error(self.name, exc) from exc
        self.config = ArmConfig(
            models=MappingProxyType({"embedding": model, "answer": ANSWER_MODEL}),
            k=k,
            chunker_version=corpus_chunker_version(conn),
            efforts=MappingProxyType({"answer": ANSWER_EFFORT}),
            answer_prompt=prompt,
            answer_max_tokens=ANSWER_MAX_TOKENS,
            graph=provenance,
        )
        self.search = LocalSearch(driver, index, hops, k, clock, self._spans, database)
        self._filings = corpus_filings(conn)
        self._answerer = (None if answer_model is None else
                          ChunkAnswerer(self.name, conn, answer_model, prompt, self._filings,
                                        self._spans))

    def run(self, question: str) -> ArmResult:
        """Retrieve and answer for one question; any failure is raised as ``ArmError``."""
        if self._answerer is None:
            raise ArmError("the graph_local arm was opened with no answer model; use retrieve()")
        return self._answerer.answer(question, self.retrieve(question))

    def retrieve(self, question: str) -> Retrieval:
        """Filter, embed, link and expand for one question, with no answer model call."""
        with self._spans.span(FILTER_SPAN, Kind.CHAIN, question_input(question)) as span:
            question_filter = parse_question_filter(question, self._filings)
            span.set_attributes(filter_attributes(question_filter))
        try:
            start = self._clock()
            embedding = embed_question(self._embedder, self._model, question, self._spans)
            embedded = self._clock()
            found = self.search.search(question, question_filter, embedding.vector)
            searched = self._clock()
        except _RETRIEVAL_ERRORS as exc:
            raise arm_error(self.name, exc) from exc
        return Retrieval(
            retrieved=found.retrieved,
            question_filter=question_filter,
            companies_without_chunks=companies_without_chunks(
                question_filter, (r.chunk_id for r in found.retrieved), self._filings
            ),
            embed_ms=(embedded - start) * _MS_PER_SECOND,
            search_ms=(searched - embedded) * _MS_PER_SECOND,
            query_cached=embedding.from_cache,
            embed_tokens=embedding.api_token_count,
            embed_cost_usd=embedding_cost(self._model, embedding.api_token_count),
            graph=GraphRetrieval(
                stages=MappingProxyType({"anchor_linking": StageCost(ms=found.anchor_ms)}),
                anchors=found.anchors,
                hops=found.hops,
                no_anchor=not found.anchors,
            ),
        )


def _provenance(driver: Driver, database: str, load_reports: Path) -> GraphProvenance:
    """What the header records about the graph; raises ``ArmError`` for another ontology."""
    with read_session(driver, database) as session:
        rows = session.execute_read(lambda tx: [r.data() for r in tx.run(GRAPH_META_QUERY)])
    problem = graph_meta_problem(rows, required=True)
    if problem:
        raise ArmError(problem)
    load_run = latest_load_run(load_reports)
    if load_run is None:
        raise ArmError(f"no graph load report in {load_reports}; run python -m graph.load")
    return GraphProvenance(ontology_version=ONTOLOGY_VERSION, schema_sha256=SCHEMA_TEXT_SHA256,
                           load_run=load_run)


def _anchor_index(driver: Driver, database: str, node_embeddings: CachedBatchEmbedder,
                  model: str) -> AnchorIndex:
    """Every anchor candidate with its cached vector; raises ``ArmError`` if one is missing."""
    nodes = read_anchor_nodes(driver, database)
    texts = distinct_texts(nodes)
    missing = node_embeddings.uncached(texts, model, NODE_INPUT_TYPE)
    if missing:
        raise ArmError(f"{len(missing)} graph node texts have no cached {model} embedding; "
                       "run python -m retrieve.node_embed")
    vectors = node_embeddings.embed(texts, model, NODE_INPUT_TYPE).vectors
    return AnchorIndex(nodes, dict(zip(texts, vectors, strict=True)))


@contextmanager
def open_graph_local_arm(response_cache: Path,
                         spans: SpanRecorder | None = None) -> Iterator[GraphLocalArm]:
    """The arm on ``DATABASE_URL`` and ``NEO4J_*``, with Voyage, Claude and their caches."""
    try:
        conn = psycopg.connect(os.environ["DATABASE_URL"])
    except psycopg.Error as exc:  # the message can quote the URL, so only the type is shown
        raise ArmError(f"could not connect to the database: {type(exc).__name__}") from exc
    try:
        driver = driver_from_env()
    except GraphSettingsMissing as exc:
        conn.close()
        raise ArmError(str(exc)) from exc
    with (conn, driver, VoyageClient.from_env() as voyage,
          AnthropicAnswerModel.from_env() as claude):
        yield GraphLocalArm(conn, driver, CachedQueryEmbedder(voyage),
                            CachedBatchEmbedder(voyage), CachedAnswerModel(claude, response_cache),
                            spans=spans)


GRAPH_LOCAL = ArmSpec(
    name=GraphLocalArm.name,
    required_env=(*NEO4J_ENV, "DATABASE_URL", API_KEY_ENV, ANTHROPIC_KEY_ENV),
    open=open_graph_local_arm,
)
