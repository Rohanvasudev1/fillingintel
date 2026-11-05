"""Small builders for the UI backend's tests (Step 14)."""
from __future__ import annotations

from dataclasses import replace

from eval.operational import question_cost, replayed
from retrieve.answer import enforce_citations, not_found_without_call
from retrieve.answer_model import TokenUsage
from retrieve.arm import ArmResult, GraphRetrieval, RetrievedChunk, Routing, StageCost
from retrieve.question_filter import QuestionFilter
from ui.backend import UiAnswer, phoenix_trace_url, sample_id_of
from ui.errors import AnswerFailed, SourceNotFound
from ui.spend import check_spend, fresh_cost

CHUNK = "0001045810-26-000021:0013"
PHOENIX = "http://localhost:6006"
NO_FILTER = QuestionFilter((), (), (), None)


def arm_result(*, embed_cost: float = 0.0, answer_cost: float = 0.0, query_cached: bool = False,
               answer_cached: bool = False, graph: GraphRetrieval | None = None,
               routing: Routing | None = None, text: str = f"A rose [{CHUNK}].",
               ) -> ArmResult:
    """An answered result citing ``CHUNK``, with the costs and cache flags a test sets."""
    check = enforce_citations(text, {CHUNK})
    answer = replace(not_found_without_call(), status="answered", text=check.text,
                     raw_text=text, citation_check=check, cost_usd=answer_cost,
                     from_cache=answer_cached, usage=TokenUsage(10, 5))
    return ArmResult(
        retrieved=(RetrievedChunk(CHUNK, 0.9),), question_filter=NO_FILTER,
        companies_without_chunks=(), embed_ms=1.0, search_ms=2.0, query_cached=query_cached,
        embed_tokens=5, embed_cost_usd=embed_cost, graph=graph, routing=routing,
        answer=answer, sources=())


def routing(*, cost: float = 0.0, from_cache: bool = False, arm: str = "vector") -> Routing:
    return Routing(predicted_class="lookup", reason="a single figure", map_name="dev_best",
                   arm=arm, effort="low", prompt_version="route/v2@00000000",
                   prompt_sha256="0" * 64,
                   cost=StageCost(ms=5.0, cost_usd=cost, from_cache=from_cache))


class FakeBackend:
    """A ``ui.backend.Backend`` with scripted answers, for the app's tests.

    *answers* maps (question, option) to the ``ArmResult`` to return; any other
    pair raises the ``BackendError`` in *error*, or ``AnswerFailed``.  Spend
    follows the real cap rule, from *estimate* per question.
    """

    def __init__(self, answers=None, *, samples=None, chips=(), sources=None, metrics=None,
                 estimate=0.05, max_spend=1.0, error=None, trace_id=None, communities=None):
        self._answers = dict(answers or {})
        self._samples = dict(samples or {})
        self._chips = tuple(chips)
        self._sources = dict(sources or {})
        self._metrics = dict(metrics or {})
        self._estimate = estimate
        self._max_spend = max_spend
        self._error = error
        self._trace_id = trace_id
        self._communities = dict(communities or {})
        self.asked: list[tuple[str, str]] = []

    @property
    def max_spend(self):
        return self._max_spend

    def answer(self, question, option, *, spent_usd):
        sample_id = sample_id_of(question, self.samples(option))
        check_spend(estimate=0.0 if sample_id is not None else self._estimate, spent=spent_usd,
                    cap=self._max_spend)
        self.asked.append((question, option))
        if self._error is not None:
            raise self._error
        result = self._answers.get((question, option))
        if result is None:
            raise AnswerFailed(f"no scripted answer for {question!r} through {option}")
        return UiAnswer(option=option, question=question, result=result, total_ms=1200.0,
                        cost=question_cost(result), spent_usd=fresh_cost(result),
                        replayed=replayed(result), sample_id=sample_id,
                        trace_id=self._trace_id,
                        trace_url=phoenix_trace_url(PHOENIX, self._trace_id),
                        communities=self._communities)

    def estimate(self, option):
        return self._estimate

    def source(self, chunk_id):
        try:
            return self._sources[chunk_id]
        except KeyError:
            raise SourceNotFound(f"no stored chunk {chunk_id}") from None

    def samples(self, option):
        return tuple(self._samples.get(option, ()))

    def chips(self):
        return self._chips

    def sample_metrics(self, option, question_id):
        return self._metrics.get((option, question_id))
