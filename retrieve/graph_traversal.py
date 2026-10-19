"""The graph_traversal arm: generated Cypher, guarded, with graph_local as fallback (Step 11).

Claude writes one Cypher query from the generated schema (``retrieve.cypher_search``);
the guard checks and runs it, and its ``chunk_ids`` become at most 10 ranked
chunks for the unchanged answer prompt (ADR-0007).  A failure gets one repair.
When the repair fails too, the question goes to ``graph_local``'s search, and
the retrieval keeps both attempts and the reason.  There is no fallback to
``vector``.

Before the first question the arm checks that ``:GraphMeta`` records the code's
ontology version and schema hash, and that graph_local could run.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import psycopg
from neo4j import Driver

from graph.connection import DATABASE
from graph.guard import TIMEOUT_S
from ingest.store import chunk_ids
from ingest.voyage import API_KEY_ENV, DEFAULT_MODEL, QueryEmbedder, VoyageClient
from retrieve.answer_model import (
    ANSWER_EFFORT,
    ANSWER_MAX_TOKENS,
    ANSWER_MODEL,
    AnswerModel,
    AnswerModelError,
    AnthropicAnswerModel,
)
from retrieve.answer_model import API_KEY_ENV as ANTHROPIC_KEY_ENV
from retrieve.answer_prompt import AnswerPrompt
from retrieve.arm import (
    ArmConfig,
    ArmError,
    ArmResult,
    ArmSpec,
    GraphRetrieval,
    Retrieval,
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
from retrieve.chunk_confidence import with_confidence
from retrieve.cypher_prompt import CypherPrompt, PromptError, load_prompt
from retrieve.cypher_request import CYPHER_EFFORT, CYPHER_MODEL, CypherRequest
from retrieve.cypher_search import CypherSearch
from retrieve.graph_local import (
    GRAPH_ERRORS,
    HOPS,
    NEO4J_ENV,
    RETRIEVAL_ERRORS,
    TOP_K,
    LocalSearch,
    anchor_index,
    graph_connections,
    graph_provenance,
)
from retrieve.pricing import embedding_cost
from retrieve.query_cache import CachedBatchEmbedder, CachedQueryEmbedder, CacheError
from retrieve.question_filter import (
    CorpusFiling,
    companies_without_chunks,
    parse_question_filter,
)
from retrieve.response_cache import CachedAnswerModel
from retrieve.run_estimate import GRAPH_TRAVERSAL_RUN, MeasuredEstimate
from retrieve.tracing import (
    FALLBACK_SPAN,
    FILTER_SPAN,
    Kind,
    SpanRecorder,
    fallback_attributes,
    filter_attributes,
    question_input,
)

_MS_PER_SECOND = 1000.0
# Raised as ArmError: a failed Cypher call stops the run, as a lost connection does.
_TRAVERSAL_ERRORS = (*RETRIEVAL_ERRORS, AnswerModelError)


class GraphTraversalArm:
    """Embeds the question, has Claude write guarded Cypher for it (one repair, then
    graph_local), and has the answer model write a cited answer from at most *k*
    chunks.  With no answer model the arm can only ``retrieve``."""

    name = "graph_traversal"

    def __init__(
        self,
        conn: psycopg.Connection,
        driver: Driver,
        embedder: QueryEmbedder,
        node_embeddings: CachedBatchEmbedder,
        cypher_model: AnswerModel,
        answer_model: AnswerModel | None,
        model: str = DEFAULT_MODEL,
        k: int = TOP_K,
        clock: Callable[[], float] = time.perf_counter,
        prompt: AnswerPrompt | None = None,
        cypher_prompt_version: str | None = None,
        cypher_timeout: float = TIMEOUT_S,
        spans: SpanRecorder | None = None,
        database: str = DATABASE,
    ):
        self._embedder = embedder
        self._driver = driver
        self._database = database
        self._model = model
        self._clock = clock
        self._spans = spans if spans is not None else SpanRecorder.off()
        prompt = prompt if prompt is not None else load_answer_prompt()
        check_prices((model, ANSWER_MODEL, CYPHER_MODEL))
        try:
            self._filings = corpus_filings(conn)
            chunker_version = corpus_chunker_version(conn)
            known = chunk_ids(conn)
            provenance = graph_provenance(driver, database)
            index = anchor_index(driver, database, node_embeddings, model)
        except (*GRAPH_ERRORS, CacheError, OSError, ValueError, psycopg.Error) as exc:
            raise arm_error(self.name, exc) from exc
        cypher_prompt = _load_cypher_prompt(cypher_prompt_version, self._filings)
        self.config = ArmConfig(
            models=MappingProxyType({"embedding": model, "cypher": CYPHER_MODEL,
                                     "answer": ANSWER_MODEL}),
            k=k,
            chunker_version=chunker_version,
            efforts=MappingProxyType({"cypher": CYPHER_EFFORT, "answer": ANSWER_EFFORT}),
            answer_prompt=prompt,
            answer_max_tokens=ANSWER_MAX_TOKENS,
            graph=replace(provenance, cypher_prompt_version=cypher_prompt.prompt_version,
                          cypher_prompt_sha256=cypher_prompt.sha256),
        )
        self.cypher_search = CypherSearch(driver, cypher_model, cypher_prompt, known, k, clock,
                                          self._spans, database, cypher_timeout)
        self.local_search = LocalSearch(driver, index, HOPS, k, clock, self._spans, database)
        self._answerer = (None if answer_model is None else
                          ChunkAnswerer(self.name, conn, answer_model, prompt, self._filings,
                                        self._spans))

    def run(self, question: str) -> ArmResult:
        """Retrieve and answer for one question; any failure is raised as ``ArmError``."""
        if self._answerer is None:
            raise ArmError("the graph_traversal arm was opened with no answer model; "
                           "use retrieve()")
        return self._answerer.answer(question, self.retrieve(question))

    def cypher_requests(self, questions: Sequence[str]) -> tuple[CypherRequest, ...]:
        """The first Cypher request each question would send, for a cost estimate."""
        return tuple(self.cypher_search.first_request(q, parse_question_filter(q, self._filings))
                     for q in questions)

    def retrieve(self, question: str) -> Retrieval:
        """Filter, embed, generate and run Cypher (falling back to local search) for one
        question, with no answer model call."""
        with self._spans.span(FILTER_SPAN, Kind.CHAIN, question_input(question)) as span:
            question_filter = parse_question_filter(question, self._filings)
            span.set_attributes(filter_attributes(question_filter))
        try:
            start = self._clock()
            embedding = embed_question(self._embedder, self._model, question, self._spans)
            embedded = self._clock()
            traversal = self.cypher_search.search(question, question_filter)
            graph = GraphRetrieval(stages=traversal.stages, cypher_attempts=traversal.attempts)
            retrieved = with_confidence(self._driver, traversal.retrieved, self._database)
            reason = traversal.fallback_reason
            if reason is not None:
                with self._spans.span(FALLBACK_SPAN, Kind.CHAIN, fallback_attributes(reason)):
                    local = self.local_search.search(question, question_filter, embedding.vector)
                retrieved = local.retrieved
                graph = replace(
                    graph, fallback_reason=reason, anchors=local.anchors, hops=local.hops,
                    no_anchor=not local.anchors,
                    stages=MappingProxyType({**traversal.stages, "anchor_linking":
                                             StageCost(ms=local.anchor_ms)}))
            searched = self._clock()
        except _TRAVERSAL_ERRORS as exc:
            raise arm_error(self.name, exc) from exc
        return Retrieval(
            retrieved=retrieved,
            question_filter=question_filter,
            companies_without_chunks=companies_without_chunks(
                question_filter, (r.chunk_id for r in retrieved), self._filings),
            embed_ms=(embedded - start) * _MS_PER_SECOND,
            search_ms=(searched - embedded) * _MS_PER_SECOND,
            query_cached=embedding.from_cache,
            embed_tokens=embedding.api_token_count,
            embed_cost_usd=embedding_cost(self._model, embedding.api_token_count),
            graph=graph,
        )


def _load_cypher_prompt(version: str | None,
                        filings: Sequence[CorpusFiling]) -> CypherPrompt:
    try:
        return (load_prompt(filings=filings) if version is None
                else load_prompt(version, filings=filings))
    except PromptError as exc:
        raise ArmError(str(exc)) from exc


@contextmanager
def open_graph_traversal_arm(response_cache: Path,
                             spans: SpanRecorder | None = None) -> Iterator[GraphTraversalArm]:
    """The arm on ``DATABASE_URL`` and ``NEO4J_*``, with Voyage, Claude and their caches."""
    with (graph_connections() as (conn, driver), VoyageClient.from_env() as voyage,
          AnthropicAnswerModel.from_env() as claude):
        cached = CachedAnswerModel(claude, response_cache)
        yield GraphTraversalArm(conn, driver, CachedQueryEmbedder(voyage),
                                CachedBatchEmbedder(voyage), cached, cached, spans=spans)


GRAPH_TRAVERSAL = ArmSpec(
    name=GraphTraversalArm.name,
    required_env=(*NEO4J_ENV, "DATABASE_URL", API_KEY_ENV, ANTHROPIC_KEY_ENV),
    open=open_graph_traversal_arm,
    estimate=MeasuredEstimate(GRAPH_TRAVERSAL_RUN, arm="graph_traversal"),
)
