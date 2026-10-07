"""Judge spans: one EVALUATOR span per metric holding one LLM span per judge call (Step 6).

The judges are the real Ragas judges on the scripted backend from the scoring
tests.  Each test builds its own provider with an in-memory exporter, starts a
root span per question as ``eval.run`` does, and hands its context to
``judge_all``, which judges on its thread pool.
"""
import json
import threading

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from eval.judging.openai_backend import parse_openai_reply
from eval.judging.openai_judge import JUDGE_EFFORT, JUDGE_MAX_OUTPUT_TOKENS, JUDGE_MODEL
from eval.judging.runner import judge_all
from eval.judging.scoring import JudgeInput, RagasJudges, metrics_for
from retrieve.answer_model import ApiResponse
from retrieve.pricing import openai_cost
from retrieve.response_cache import CachedAnswerModel
from retrieve.tracing import Kind, SpanRecorder
from tests.judge_fakes import FixedEmbedder, ScriptedBackend
from tests.openai_fixtures import RECORDED
from tests.test_judge_scoring import ANSWERED, GENERATED, QUESTION, SOURCES, _replies

USAGE = {"input_tokens": 1200, "input_tokens_details": {"cached_tokens": 1000,
                                                        "cache_write_tokens": 50},
         "output_tokens": 300, "output_tokens_details": {"reasoning_tokens": 240},
         "total_tokens": 1500}
TOKEN_COUNTS = {
    "llm.token_count.prompt": 1200,
    "llm.token_count.prompt_details.cache_read": 1000,
    "llm.token_count.prompt_details.cache_write": 50,
    "llm.token_count.completion": 300,
    "llm.token_count.completion_details.reasoning": 240,
    "llm.token_count.total": 1500,
}


class _ThreadedBackend(ScriptedBackend):
    """The scripted backend with the reported usage above; records each call's thread."""

    def __init__(self):
        super().__init__(_replies())
        self.threads: set[int] = set()

    def complete(self, request) -> ApiResponse:
        self.threads.add(threading.get_ident())
        response = super().complete(request)
        return ApiResponse({**response.body, "usage": USAGE}, response.api_ms)


@pytest.fixture
def memory():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield provider, exporter
    provider.shutdown()


def _judges(backend, spans=None):
    embedder = FixedEmbedder({QUESTION: (1.0, 0.0), GENERATED: (0.6, 0.8)})
    return RagasJudges(backend, embedder, spans=spans)


def _roots(tracer, n: int):
    """*n* ended question spans, as ``eval.run`` leaves them, and a parent context for each."""
    roots, parents = [], []
    for i in range(n):
        with tracer.start_as_current_span(f"question {i}") as span:
            parents.append(trace.set_span_in_context(span))
        roots.append(span)
    return roots, parents


def _judge_traced(provider, backend, n: int = 2):
    spans = SpanRecorder(provider.get_tracer("test"))
    roots, parents = _roots(provider.get_tracer("test"), n)
    items = [JudgeInput(QUESTION, "lookup", ANSWERED, SOURCES)] * n
    judged = judge_all(_judges(backend, spans), items, parents=parents)
    return roots, judged


def _kind(span) -> str:
    return span.attributes.get("openinference.span.kind", "")


def test_each_judge_call_sits_under_its_metric_under_its_question(memory):
    provider, exporter = memory
    backend = _ThreadedBackend()
    roots, judged = _judge_traced(provider, backend)
    finished = exporter.get_finished_spans()
    by_id = {s.context.span_id: s for s in finished}
    evaluators = [s for s in finished if _kind(s) == Kind.EVALUATOR.value]
    calls = [s for s in finished if _kind(s) == Kind.LLM.value]

    assert threading.get_ident() not in backend.threads  # judged on the pool
    runs = len(judged[0].runs)
    assert len(evaluators) == len(roots) * runs * len(metrics_for("lookup"))
    assert len(calls) == sum(r.calls for q in judged for r in q.runs) == len(backend.requests)
    for root in roots:
        under = [e for e in evaluators if e.parent.span_id == root.context.span_id]
        assert sorted(e.name for e in under) == sorted(metrics_for("lookup") * runs)
        assert {e.context.trace_id for e in under} == {root.context.trace_id}
    for call in calls:
        assert _kind(by_id[call.parent.span_id]) == Kind.EVALUATOR.value


def test_an_evaluator_span_records_its_metric_run_and_score(memory):
    provider, exporter = memory
    _, judged = _judge_traced(provider, _ThreadedBackend(), n=1)
    scores = {r.run: r.scores for r in judged[0].runs}
    for span in exporter.get_finished_spans():
        if _kind(span) != Kind.EVALUATOR.value:
            continue
        attributes = span.attributes
        assert attributes["filingintel.judge.metric"] == span.name
        score = scores[attributes["filingintel.judge.run"]][span.name]
        assert attributes["output.value"] == json.dumps(score)


def test_a_fresh_judge_call_carries_the_token_counts_openai_reported(memory):
    provider, exporter = memory
    _judge_traced(provider, _ThreadedBackend(), n=1)
    calls = [s for s in exporter.get_finished_spans() if _kind(s) == Kind.LLM.value]
    assert calls
    for call in calls:
        attributes = call.attributes
        assert attributes["llm.model_name"] == JUDGE_MODEL
        assert attributes["llm.provider"] == attributes["llm.system"] == "openai"
        assert json.loads(attributes["llm.invocation_parameters"]) == {
            "reasoning": {"effort": JUDGE_EFFORT}, "max_output_tokens": JUDGE_MAX_OUTPUT_TOKENS}
        assert attributes["filingintel.cache_hit"] is False
        assert {k: attributes[k] for k in TOKEN_COUNTS} == TOKEN_COUNTS
        assert attributes["filingintel.cost_usd"] == pytest.approx(
            openai_cost(JUDGE_MODEL, _usage()))


def _usage():
    return parse_openai_reply({**RECORDED, "usage": USAGE}).usage


def test_a_judge_cache_replay_carries_no_token_counts(memory, tmp_path):
    provider, exporter = memory
    cached = CachedAnswerModel(_ThreadedBackend(), tmp_path)
    _judge_traced(provider, cached, n=1)
    exporter.clear()
    _judge_traced(provider, cached, n=1)
    calls = [s for s in exporter.get_finished_spans() if _kind(s) == Kind.LLM.value]
    assert calls
    for call in calls:
        assert call.attributes["filingintel.cache_hit"] is True
        # the run file's cost figure, from the recorded tokens, as for a replayed answer
        assert call.attributes["filingintel.cost_usd"] == pytest.approx(
            openai_cost(JUDGE_MODEL, _usage()))
        assert not [k for k in call.attributes if k.startswith("llm.token_count.")]


def test_judges_without_a_recorder_make_no_spans_and_the_same_scores(memory):
    provider, exporter = memory
    item = JudgeInput(QUESTION, "lookup", ANSWERED, SOURCES)
    untraced = _judges(_ThreadedBackend()).judge(item, run=1)
    traced = _judges(_ThreadedBackend(), SpanRecorder(provider.get_tracer("test"))).judge(item, 1)
    assert untraced == traced
    exporter.clear()
    _judges(_ThreadedBackend()).judge(item, run=1)
    assert exporter.get_finished_spans() == ()


def test_judge_all_without_parents_still_starts_judge_spans_as_their_own_traces(memory):
    provider, exporter = memory
    spans = SpanRecorder(provider.get_tracer("test"))
    judge_all(_judges(_ThreadedBackend(), spans), [JudgeInput(QUESTION, "lookup", ANSWERED,
                                                               SOURCES)])
    evaluators = [s for s in exporter.get_finished_spans() if _kind(s) == Kind.EVALUATOR.value]
    assert evaluators
    assert all(e.parent is None for e in evaluators)


def test_parents_must_match_the_items(memory):
    with pytest.raises(ValueError, match="one parent per item"):
        judge_all(_judges(_ThreadedBackend()), [JudgeInput(QUESTION, "lookup", ANSWERED,
                                                           SOURCES)], parents=[])


def test_a_reply_the_judge_cannot_score_marks_its_metric_span_as_an_error(memory):
    provider, exporter = memory
    backend = ScriptedBackend(_replies(), text='{"reason": "no verdict"}')
    item = JudgeInput(QUESTION, "lookup", ANSWERED, SOURCES)
    scores = _judges(backend, SpanRecorder(provider.get_tracer("test"))).judge(item, run=1)
    evaluators = [s for s in exporter.get_finished_spans() if _kind(s) == Kind.EVALUATOR.value]
    assert {e.name for e in evaluators} == set(scores.errors) == set(metrics_for("lookup"))
    for span in evaluators:
        assert span.status.status_code is StatusCode.ERROR
        assert span.attributes["filingintel.judge.error"] == scores.errors[span.name]
        assert span.attributes["output.value"] == "null"
