"""Generated Cypher, guarded, with one repair: graph_traversal's search (Step 11, ADR-0007).

The Cypher model writes a query from the prompt (``retrieve.cypher_prompt``).
The query passes the guard's static checks and EXPLAIN (``cypher_guard`` span),
then runs (``cypher_execute`` span), and its rows become at most *k* ranked
chunks (``retrieve.cypher_rows``).  A reply with no query, a guard failure, an
error, a timeout or no chunk IDs gets one repair, which resends the exchange
with the failed query and what went wrong.  The caller falls back to
``graph_local`` when the repair fails too; ``fallback_reason`` says why.

A lost connection is not a query failure: the guard raises it, and so does
this search, as does a failed API call.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from neo4j import Driver

from graph.connection import DATABASE
from graph.guard import (
    TIMEOUT_S,
    GuardFailure,
    GuardPass,
    execute,
    explain_check,
    static_check,
)
from retrieve.answer_model import AnswerModel
from retrieve.arm import (
    CypherAttempt,
    FallbackReason,
    GraphStage,
    RetrievedChunk,
    StageCost,
)
from retrieve.cypher_prompt import CypherPrompt
from retrieve.cypher_request import (
    CypherReply,
    CypherRequest,
    first_request,
    parse_cypher_reply,
    repair_request,
)
from retrieve.cypher_rows import rank_rows
from retrieve.question_filter import QuestionFilter
from retrieve.tracing import (
    CYPHER_EXECUTE_SPAN,
    CYPHER_GENERATION_SPAN,
    CYPHER_GUARD_SPAN,
    Kind,
    SpanRecorder,
    cypher_attributes,
    cypher_reply_attributes,
    question_input,
)

# The stages that are Cypher model calls, generation then repair.
CALL_STAGES: tuple[GraphStage, ...] = ("cypher_generation", "cypher_repair")
REPLY_RULE = "reply"  # the attempt's rule when the reply held no query
_MS_PER_SECOND = 1000.0
_NO_TEXT = "(no reply text)"  # the API refuses an empty assistant turn


@dataclass(frozen=True)
class TraversalResult:
    retrieved: tuple[RetrievedChunk, ...]
    attempts: tuple[CypherAttempt, ...]
    stages: MappingProxyType[GraphStage, StageCost]

    @property
    def fallback_reason(self) -> FallbackReason | None:
        """The last attempt's outcome when no attempt returned chunks."""
        last = self.attempts[-1].outcome
        return None if last == "ok" else last


@dataclass(frozen=True)
class _Try:
    attempt: CypherAttempt
    retrieved: tuple[RetrievedChunk, ...]
    reply_text: str
    generation: StageCost
    execution_ms: float


class CypherSearch:
    """Generates, guards and runs Cypher for one question, repairing once."""

    def __init__(self, driver: Driver, model: AnswerModel, prompt: CypherPrompt,
                 known_chunks: Collection[str], k: int,
                 clock: Callable[[], float] = time.perf_counter,
                 spans: SpanRecorder | None = None, database: str = DATABASE,
                 timeout: float = TIMEOUT_S):
        self._driver = driver
        self._model = model
        self._prompt = prompt
        self._known = known_chunks
        self._k = k
        self._clock = clock
        self._spans = spans if spans is not None else SpanRecorder.off()
        self._database = database
        self._timeout = timeout

    def search(self, question: str, question_filter: QuestionFilter) -> TraversalResult:
        """The ranked chunks and every attempt; raises ``AnswerModelError`` on a failed
        API call and the driver's error on a lost connection."""
        request = self.first_request(question, question_filter)
        first = self._try(question, request, question_filter)
        if first.attempt.outcome == "ok":
            return _result(first.retrieved, (first,), {"cypher_generation": first.generation})
        repair = repair_request(self._prompt, request, first.reply_text, first.attempt.query,
                                first.attempt.message or "")
        second = self._try(question, repair, question_filter)
        return _result(second.retrieved, (first, second),
                       {"cypher_generation": first.generation, "cypher_repair": second.generation})

    def first_request(self, question: str, question_filter: QuestionFilter) -> CypherRequest:
        """The generation request for *question*, as ``search`` sends it."""
        return first_request(self._prompt, question, question_filter)

    def _try(self, question: str, request: CypherRequest,
             question_filter: QuestionFilter) -> _Try:
        reply, generation = self._generate(question, request)
        start = self._clock()
        if reply.cypher is None:
            attempt = CypherAttempt("", "invalid", rule=REPLY_RULE, message=reply.error)
            retrieved: tuple[RetrievedChunk, ...] = ()
        else:
            attempt, retrieved = self._run(reply.cypher, question_filter)
        return _Try(attempt, retrieved, reply.text or _NO_TEXT, generation,
                    (self._clock() - start) * _MS_PER_SECOND)

    def _generate(self, question: str, request: CypherRequest) -> tuple[CypherReply, StageCost]:
        with self._spans.span(CYPHER_GENERATION_SPAN, Kind.LLM,
                              question_input(question)) as span:
            response = self._model.complete(request)
            reply = parse_cypher_reply(response.body)
            cost = StageCost.anthropic(response.api_ms, request.model, reply.usage,
                                       from_cache=response.from_cache)
            messages = ((request.system, request.last_user, reply.text)
                        if self._spans.capture_text else None)
            span.set_attributes(cypher_reply_attributes(
                reply.cypher or "", cost_usd=cost.cost_usd, from_cache=response.from_cache,
                usage=reply.usage, messages=messages))
        return reply, cost

    def _run(self, cypher: str, question_filter: QuestionFilter
             ) -> tuple[CypherAttempt, tuple[RetrievedChunk, ...]]:
        with self._spans.span(CYPHER_GUARD_SPAN, Kind.CHAIN, {}) as span:
            failure = static_check(cypher) or explain_check(
                self._driver, cypher, database=self._database, timeout=self._timeout)
            if failure is not None:
                attempt = _failed(cypher, failure)
                span.set_attributes(cypher_attributes(attempt))
                return attempt, ()
        with self._spans.span(CYPHER_EXECUTE_SPAN, Kind.RETRIEVER, {}) as span:
            result = execute(self._driver, cypher, database=self._database,
                             timeout=self._timeout)
            if isinstance(result, GuardFailure):
                attempt, retrieved = _failed(cypher, result), ()
            else:
                attempt, retrieved = self._ranked(cypher, result, question_filter)
            span.set_attributes(cypher_attributes(attempt))
        return attempt, retrieved

    def _ranked(self, cypher: str, result: GuardPass, question_filter: QuestionFilter
                ) -> tuple[CypherAttempt, tuple[RetrievedChunk, ...]]:
        retrieved = rank_rows(result.rows, question_filter.accession_nos, self._known, self._k)
        if retrieved:
            return CypherAttempt(cypher, "ok", rows=len(result.rows)), retrieved
        return CypherAttempt(cypher, "empty", rows=len(result.rows), message=(
            f"The query ran and returned {len(result.rows)} rows, but no chunk ID in them "
            "belongs to the filings the question asks about. Look for the evidence through "
            "other nodes, edges or name words.")), ()


def _failed(cypher: str, failure: GuardFailure) -> CypherAttempt:
    return CypherAttempt(cypher, failure.category.value, rule=failure.rule.value,
                         message=failure.message)


def _result(retrieved: tuple[RetrievedChunk, ...], tries: tuple[_Try, ...],
            calls: Mapping[GraphStage, StageCost]) -> TraversalResult:
    execution = StageCost(ms=sum(t.execution_ms for t in tries))
    return TraversalResult(retrieved, tuple(t.attempt for t in tries),
                           MappingProxyType({**calls, "cypher_execution": execution}))
