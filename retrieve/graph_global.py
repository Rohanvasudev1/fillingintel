"""The graph_global arm: map-reduce over communities (Step 12, ADR-0008).

``GlobalSelection`` filters the question, embeds it and picks the top N lower
communities by report cosine, each with its filtered evidence chunks closest
to the question.  One map call per community, ``MAP_WORKERS`` at a time,
writes scored map points citing those chunks (``retrieve.global_map``).  The
kept points above 0, best first and within the reduce budget, go to one reduce
call that writes the answer with ``answer/v1``'s status line
(``retrieve.global_reduce``); with no such point the answer is ``not_found``
and no reduce call is made.  The answer filter keeps a sentence only when
every chunk it cites was shown to a map call for this question.

The ranked list scored for retrieval, and the ``sources`` judges read, are the
shown chunks by community rank, then cosine.  The reduce call writes the
answer, so its cost and latency are the answer's; ``community_select`` and
``global_map`` are the graph stages.  A failed API call, or a response the
client would not accept, is an ``ArmError``; a map reply cut off, refused or
off the schema gives that community no points and is recorded.

Before the first question the arm checks ``:GraphMeta`` (ontology version and
schema hash), that every report text has a cached embedding, and that every
model has a price.
"""
from __future__ import annotations

import contextvars
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import psycopg
from neo4j import Driver

from graph.community import LOWER
from graph.connection import DATABASE
from graph.load import COMMUNITIES_DIR
from graph.load import OUTPUT_DIR as LOAD_REPORT_DIR
from graph.report_file import ReportsFile
from ingest.voyage import API_KEY_ENV, DEFAULT_MODEL, QueryEmbedder, VoyageClient
from retrieve.answer import Answer, not_found_without_call, read_answer
from retrieve.answer_model import API_KEY_ENV as ANTHROPIC_KEY_ENV
from retrieve.answer_model import AnswerModel, AnthropicAnswerModel
from retrieve.answer_prompt import AnswerPrompt, SourceChunk
from retrieve.arm import (
    ArmConfig,
    ArmResult,
    ArmSpec,
    GlobalSearch,
    GlobalSearchConfig,
    GraphRetrieval,
    MapCall,
    Retrieval,
    StageCost,
)
from retrieve.arm_steps import (
    ANSWER_ERRORS,
    arm_error,
    arm_result,
    check_prices,
    corpus_chunker_version,
    filing_tickers,
    source_chunks,
)
from retrieve.community_select import CHUNKS_PER_COMMUNITY
from retrieve.global_map import (
    MAP_EFFORT,
    MAP_MAX_TOKENS,
    MAP_MODEL,
    build_map_request,
    read_map_reply,
)
from retrieve.global_prompts import GlobalPrompt, PromptError, load_map_prompt, load_reduce_prompt
from retrieve.global_reduce import (
    REDUCE_BUDGET_TOKENS,
    REDUCE_EFFORT,
    REDUCE_MAX_TOKENS,
    REDUCE_MODEL,
    ReduceInput,
    build_reduce_request,
    mark_reduced,
    select_points,
)
from retrieve.global_retrieval import (
    ARM_NAME,
    COMMUNITIES_N,
    GlobalSelection,
    ShownCommunity,
    loaded_reports_file,
)
from retrieve.graph_local import GRAPH_ERRORS, NEO4J_ENV, graph_connections, graph_provenance
from retrieve.query_cache import CachedBatchEmbedder, CachedQueryEmbedder
from retrieve.response_cache import CachedAnswerModel
from retrieve.run_estimate import GLOBAL_ANSWER_STAGES, GRAPH_GLOBAL_RUN, MeasuredEstimate
from retrieve.tracing import (
    GLOBAL_MAP_SPAN,
    GLOBAL_REDUCE_SPAN,
    Kind,
    SpanRecorder,
    generation_attributes,
    generation_request_attributes,
    map_call_attributes,
    reduce_points_attributes,
)

MAP_WORKERS = 4
_MS_PER_SECOND = 1000.0


class GraphGlobalArm:
    """Selects communities for a question, maps each to scored points and reduces the
    points to a cited answer."""

    name = ARM_NAME

    def __init__(
        self,
        conn: psycopg.Connection,
        driver: Driver,
        embedder: QueryEmbedder,
        report_embeddings: CachedBatchEmbedder,
        model: AnswerModel,
        reports: ReportsFile,
        *,
        n: int = COMMUNITIES_N,
        per_community: int = CHUNKS_PER_COMMUNITY,
        reduce_budget: int = REDUCE_BUDGET_TOKENS,
        workers: int = MAP_WORKERS,
        embedding_model: str = DEFAULT_MODEL,
        clock: Callable[[], float] = time.perf_counter,
        spans: SpanRecorder | None = None,
        database: str = DATABASE,
    ):
        check_prices((embedding_model, MAP_MODEL, REDUCE_MODEL))
        self._conn = conn
        self._model = model
        self._reports = reports
        self._reduce_budget = reduce_budget
        self._workers = workers
        self._clock = clock
        self._spans = spans if spans is not None else SpanRecorder.off()
        self._map_prompt, self._reduce_prompt = _load_prompts()
        self.selection = GlobalSelection(
            conn, driver, embedder, report_embeddings, reports, n=n, level=LOWER,
            per_community=per_community, model=embedding_model, clock=clock, spans=self._spans,
            database=database)
        self._tickers = filing_tickers(self.selection.filings)
        try:
            chunker_version = corpus_chunker_version(conn)
        except psycopg.Error as exc:
            raise arm_error(self.name, exc) from exc
        self.config = ArmConfig(
            models=MappingProxyType({"embedding": embedding_model, "map": MAP_MODEL,
                                     "reduce": REDUCE_MODEL}),
            k=n * per_community,
            chunker_version=chunker_version,
            efforts=MappingProxyType({"map": MAP_EFFORT, "reduce": REDUCE_EFFORT}),
            answer_prompt=_as_answer_prompt(self._reduce_prompt),
            answer_max_tokens=REDUCE_MAX_TOKENS,
            graph=replace(self.selection.provenance, global_search=self._settings(
                n, per_community, reduce_budget, workers)),
        )

    def _settings(self, n: int, per_community: int, reduce_budget: int,
                  workers: int) -> GlobalSearchConfig:
        """The frozen settings and the files read, for the results header."""
        reports = self._reports
        return GlobalSearchConfig(
            n=n, level=LOWER, chunks_per_community=per_community,
            reduce_budget_tokens=reduce_budget, map_workers=workers,
            map_prompt_version=self._map_prompt.prompt_version,
            map_prompt_sha256=self._map_prompt.sha256,
            reduce_prompt_version=self._reduce_prompt.prompt_version,
            reduce_prompt_sha256=self._reduce_prompt.sha256,
            report_prompt_versions=tuple(sorted({e.prompt_version
                                                 for e in reports.entries.values()})),
            communities_file=reports.communities_file, reports_file=reports.name)

    def retrieve(self, question: str) -> Retrieval:
        """The ranked shown chunks ``run`` would map, with no map or reduce call."""
        return self.selection.retrieve(question).retrieval

    def run(self, question: str) -> ArmResult:
        """Select, map and reduce for one question; any failure is raised as ``ArmError``."""
        found = self.selection.retrieve(question)
        retrieval = found.retrieval
        try:
            sources = source_chunks(self._conn, self._tickers,
                                    [r.chunk_id for r in retrieval.retrieved])
            start = self._clock()
            calls = self._map_all(question, found.communities,
                                  {s.chunk_id: s for s in sources})
            mapped = self._clock()
            selected = select_points(calls, self._reduce_budget)
            answer = self._reduce(question, selected, {s.chunk_id for s in sources})
        except ANSWER_ERRORS as exc:
            raise arm_error(self.name, exc) from exc
        map_ms = (mapped - start) * _MS_PER_SECOND
        graph = GraphRetrieval(
            stages=_stages(retrieval.search_ms, map_ms, calls),
            global_search=GlobalSearch(
                calls=mark_reduced(calls, selected), reduce_points=len(selected.points),
                reduce_input_tokens=selected.tokens, cut_by_budget=selected.cut_by_budget))
        return arm_result(replace(retrieval, search_ms=retrieval.search_ms + map_ms, graph=graph),
                          answer, sources)

    def _map_all(self, question: str, communities: Sequence[ShownCommunity],
                 chunks: Mapping[str, SourceChunk]) -> tuple[MapCall, ...]:
        """One map call per community, *workers* at a time; the first failure cancels the
        calls not yet started and is raised.  Each call keeps the question's span as parent."""
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            futures = [pool.submit(contextvars.copy_context().run, self._map_one, question, c,
                                   [chunks[k.chunk_id] for k in c.chunks])
                       for c in communities]
            try:
                return tuple(f.result() for f in futures)
            except BaseException:
                for future in futures:
                    future.cancel()
                raise

    def _map_one(self, question: str, community: ShownCommunity,
                 chunks: Sequence[SourceChunk]) -> MapCall:
        entry = self._reports.entries[community.community_id]
        request = build_map_request(self._map_prompt, question, entry.title, entry.report, chunks)
        attributes = generation_request_attributes(MAP_MODEL, MAP_EFFORT, MAP_MAX_TOKENS,
                                                   self._map_prompt.prompt_version)
        with self._spans.span(GLOBAL_MAP_SPAN, Kind.LLM, attributes) as span:
            response = self._model.complete(request)
            shown = tuple(c.chunk_id for c in chunks)
            reply = read_map_reply(response.body, shown, community.community_id)
            call = MapCall(community.community_id, community.rank, community.similarity, shown,
                           reply.outcome, reply.points,
                           StageCost.anthropic(response.api_ms, MAP_MODEL, reply.usage,
                                               from_cache=response.from_cache))
            messages = ((request.system, request.user, reply.text)
                        if self._spans.capture_text else None)
            span.set_attributes(map_call_attributes(call, MAP_MODEL, reply.usage, messages))
        return call

    def _reduce(self, question: str, selected: ReduceInput, shown: set[str]) -> Answer:
        """The answer from the selected points; ``not_found`` with no call when there are none."""
        if not selected.points:
            return not_found_without_call()
        request = build_reduce_request(self._reduce_prompt, question, selected.points)
        attributes = {
            **generation_request_attributes(REDUCE_MODEL, REDUCE_EFFORT, REDUCE_MAX_TOKENS,
                                            self._reduce_prompt.prompt_version),
            **reduce_points_attributes(len(selected.points)),
        }
        with self._spans.span(GLOBAL_REDUCE_SPAN, Kind.LLM, attributes) as span:
            answer = read_answer(self._model.complete(request), shown)
            messages = (request.system, request.user) if self._spans.capture_text else None
            span.set_attributes(generation_attributes(answer, messages))
        return answer


def _stages(select_ms: float, map_ms: float,
            calls: Sequence[MapCall]) -> Mapping[str, StageCost]:
    """community_select always; global_map when a community was selected, its latency the
    map step's wall time and its cost and tokens summed over the calls."""
    stages: dict[str, StageCost] = {"community_select": StageCost(ms=select_ms)}
    if calls:
        stages["global_map"] = StageCost(
            ms=map_ms,
            cost_usd=sum(c.cost.cost_usd for c in calls),
            input_tokens=sum(c.cost.input_tokens for c in calls),
            output_tokens=sum(c.cost.output_tokens for c in calls),
            from_cache=all(c.cost.from_cache for c in calls))
    return MappingProxyType(stages)


def _load_prompts() -> tuple[GlobalPrompt, GlobalPrompt]:
    try:
        return load_map_prompt(), load_reduce_prompt()
    except PromptError as exc:
        raise arm_error(ARM_NAME, exc) from exc


def _as_answer_prompt(prompt: GlobalPrompt) -> AnswerPrompt:
    """The reduce prompt as the results header's answer prompt: it writes the answer."""
    return AnswerPrompt(version=prompt.prompt_version, sha256=prompt.sha256, path=prompt.path,
                        system=prompt.system, user_template=prompt.user_template)


@contextmanager
def open_graph_global_arm(response_cache: Path,
                          spans: SpanRecorder | None = None) -> Iterator[GraphGlobalArm]:
    """The arm on ``DATABASE_URL`` and ``NEO4J_*``, with Voyage, Claude and their caches,
    reading the reports file of the load run :GraphMeta records."""
    with (graph_connections() as (conn, driver), VoyageClient.from_env() as voyage,
          AnthropicAnswerModel.from_env() as claude):
        try:
            load_run = graph_provenance(driver, DATABASE).load_run
        except GRAPH_ERRORS as exc:
            raise arm_error(ARM_NAME, exc) from exc
        reports = loaded_reports_file(LOAD_REPORT_DIR, COMMUNITIES_DIR, load_run)
        yield GraphGlobalArm(conn, driver, CachedQueryEmbedder(voyage),
                             CachedBatchEmbedder(voyage), CachedAnswerModel(claude, response_cache),
                             reports, spans=spans)


GRAPH_GLOBAL = ArmSpec(
    name=ARM_NAME,
    required_env=(*NEO4J_ENV, "DATABASE_URL", API_KEY_ENV, ANTHROPIC_KEY_ENV),
    open=open_graph_global_arm,
    estimate=MeasuredEstimate(GRAPH_GLOBAL_RUN, answer_stages=GLOBAL_ANSWER_STAGES),
)
