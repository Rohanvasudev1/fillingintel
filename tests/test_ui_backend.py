"""The UI backend: one call answers a question through a picker option (Step 14 ticket 01).

Arms are fakes registered under the real arm names, as eval.run's tests do, so
the backend's own rules are tested here: opening once, settings, prices, the
spend cap, database errors, tracing and the community lookup.  The real arms
are tested in their own files.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from types import MappingProxyType

import pytest
from neo4j.exceptions import ServiceUnavailable
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from eval.operational import question_cost
from retrieve.answer_prompt import load_prompt
from retrieve.arm import (
    ArmConfig,
    ArmError,
    ArmSpec,
    GlobalSearch,
    GraphRetrieval,
    MapCall,
    StageCost,
)
from retrieve.tracing import FILTER_SPAN, Kind
from tests.ui_fakes import arm_result
from ui.backend import Backend, LiveBackend
from ui.communities import CommunityInfo
from ui.errors import (
    AnswerFailed,
    BackendError,
    DatabaseUnavailable,
    SettingsMissing,
    SpendCapReached,
    UnpricedModel,
)
from ui.samples import Sample, Samples
from ui.source import SourcePassage

LIVE = "What did NVIDIA say about its gross margin?"
SAMPLE = Sample("q0072", "lookup", "In NVIDIA's fiscal year 2026, what share of revenue?")
ENV = {"FAKE_KEY": "x"}
PROMPT = load_prompt("v1")


def _config(models=None) -> ArmConfig:
    return ArmConfig(models=MappingProxyType(models or {"answer": "claude-sonnet-5-5"}), k=10,
                     chunker_version="c1", efforts=MappingProxyType({}), answer_prompt=PROMPT,
                     answer_max_tokens=1000)


class FakeArm:
    def __init__(self, name, result, spans, models=None, fail=None):
        self.name = name
        self.config = _config(models)
        self._result = result
        self._spans = spans
        self._fail = fail
        self.asked: list[str] = []

    def run(self, question):
        self.asked.append(question)
        if self._spans is not None:
            with self._spans.span(FILTER_SPAN, Kind.CHAIN):
                pass
        if self._fail is not None:
            raise self._fail
        return self._result

    def retrieve(self, question):
        raise NotImplementedError


class Registry:
    """Fake specs under the real arm names, counting how often each is opened."""

    def __init__(self, result=None, *, models=None, fail=None):
        self.result = result or arm_result(answer_cost=0.02, embed_cost=0.001)
        self.models = models
        self.fail = fail
        self.opened: list[str] = []
        self.arms: list[FakeArm] = []

    def spec(self, name, required_env=("FAKE_KEY",)) -> ArmSpec:
        @contextmanager
        def open_arm(response_cache, spans=None):
            self.opened.append(name)
            arm = FakeArm(name, self.result, spans, self.models, self.fail)
            self.arms.append(arm)
            yield arm

        return ArmSpec(name=name, required_env=required_env, open=open_arm,
                       estimate=lambda questions, retrieval_only: 0.0)

    def specs(self):
        base = {n: self.spec(n) for n in ("vector", "graph_local", "graph_traversal",
                                          "graph_global")}
        router = ArmSpec(name="router", required_env=("FAKE_KEY",), open=None,
                         estimate=lambda questions, retrieval_only: 0.0,
                         with_map=lambda map_name: self.spec(f"router:{map_name}"))
        return {**base, "router": router}


def _samples():
    return Samples(seed=1, options=MappingProxyType({"vector": (SAMPLE,)}),
                   skipped=MappingProxyType({}), chips=(SAMPLE,))


def _backend(registry, *, environ=ENV, probe=None, tracing=None, communities=None,
             source=None, ticks=None, tmp_path=None):
    clock = iter(ticks or [0.0, 0.25] * 20).__next__
    return LiveBackend(
        environ=environ, arms=registry.specs(), samples=_samples,
        probe=probe or (lambda spec: None), tracing=tracing or (lambda environ: None),
        communities=communities or (lambda ids: {}),
        source_reader=source or (lambda chunk_id: None),
        clock=clock, response_cache=tmp_path)


def test_a_sample_is_answered_with_its_cost_and_time_and_spends_nothing(tmp_path):
    registry = Registry(arm_result(answer_cost=0.02, query_cached=True, answer_cached=True))
    answer = _backend(registry, tmp_path=tmp_path).answer(SAMPLE.question, "vector",
                                                          spent_usd=0.5)
    assert answer.result is registry.result
    assert answer.option == "vector"
    assert answer.is_sample is True
    assert answer.total_ms == pytest.approx(250.0)
    assert answer.cost == question_cost(registry.result)
    assert answer.spent_usd == 0.0
    assert answer.replayed is True
    assert answer.trace_id is None


def test_each_option_is_opened_once_and_reused(tmp_path):
    registry = Registry()
    backend = _backend(registry, tmp_path=tmp_path)
    backend.answer(LIVE, "graph_local", spent_usd=0.0)
    backend.answer(LIVE, "graph_local", spent_usd=0.0)
    backend.answer(LIVE, "vector", spent_usd=0.0)
    assert registry.opened == ["graph_local", "vector"]


def test_a_router_option_opens_the_router_under_its_map(tmp_path):
    registry = Registry()
    backend = _backend(registry, tmp_path=tmp_path)
    answer = backend.answer(LIVE, "router (dev-best)", spent_usd=0.0)
    backend.answer(LIVE, "router (pre-registered)", spent_usd=0.0)
    assert answer.option == "router (dev-best)"
    assert registry.opened == ["router:dev_best", "router:pre_registered"]


def test_a_live_question_adds_what_its_fresh_calls_cost(tmp_path):
    registry = Registry(arm_result(answer_cost=0.02, embed_cost=0.001))
    answer = _backend(registry, tmp_path=tmp_path).answer(LIVE, "vector", spent_usd=0.0)
    assert answer.is_sample is False
    assert answer.spent_usd == pytest.approx(0.021)
    assert answer.replayed is False


def test_the_cap_refuses_a_live_question_before_anything_opens(tmp_path):
    registry = Registry()
    backend = _backend(registry, environ={**ENV, "FILINGINTEL_UI_MAX_SPEND": "0.05"},
                       tmp_path=tmp_path)
    estimate = backend.estimate("vector")
    with pytest.raises(SpendCapReached, match="cap"):
        backend.answer(LIVE, "vector", spent_usd=0.05 - estimate / 2)
    assert registry.opened == []
    backend.answer(SAMPLE.question, "vector", spent_usd=0.05)  # a sample needs no estimate
    with pytest.raises(SpendCapReached):  # but a session already past the cap is refused
        backend.answer(SAMPLE.question, "vector", spent_usd=0.06)


def test_the_estimate_and_cap_are_reported(tmp_path):
    backend = _backend(Registry(), tmp_path=tmp_path)
    assert 0 < backend.estimate("vector") < 0.1
    assert backend.max_spend == 1.0


def test_a_missing_setting_is_named_before_anything_opens(tmp_path):
    registry = Registry()
    with pytest.raises(SettingsMissing, match="FAKE_KEY") as caught:
        _backend(registry, environ={}, tmp_path=tmp_path).answer(LIVE, "vector", spent_usd=0)
    assert ".env" in str(caught.value)
    assert registry.opened == []


def test_an_unpriced_model_is_refused_before_the_question(tmp_path):
    registry = Registry(models={"answer": "claude-unknown-9"})
    with pytest.raises(UnpricedModel, match="claude-unknown-9"):
        _backend(registry, tmp_path=tmp_path).answer(LIVE, "vector", spent_usd=0)
    assert registry.arms[0].asked == []


def test_an_unreachable_database_is_a_plain_error(tmp_path):
    def down(spec):
        raise DatabaseUnavailable("Postgres is not reachable; start it with docker compose up -d")

    registry = Registry()
    with pytest.raises(DatabaseUnavailable, match="docker compose up -d"):
        _backend(registry, probe=down, tmp_path=tmp_path).answer(LIVE, "vector", spent_usd=0)
    assert registry.opened == []


def test_a_lost_graph_connection_drops_the_arm_so_the_next_question_reopens(tmp_path):
    registry = Registry(fail=ServiceUnavailable("connection refused"))
    backend = _backend(registry, tmp_path=tmp_path)
    with pytest.raises(DatabaseUnavailable, match="Neo4j") as caught:
        backend.answer(LIVE, "graph_local", spent_usd=0)
    assert "connection refused" not in str(caught.value)
    registry.fail = None
    backend.answer(LIVE, "graph_local", spent_usd=0)
    assert registry.opened == ["graph_local", "graph_local"]


def test_an_arm_failure_is_shown_in_the_arms_words(tmp_path):
    registry = Registry(fail=ArmError("the vector arm failed: VoyageError: 401"))
    with pytest.raises(AnswerFailed, match="vector arm failed"):
        _backend(registry, tmp_path=tmp_path).answer(LIVE, "vector", spent_usd=0)


def test_an_arm_that_cannot_open_is_shown_in_its_words(tmp_path):
    registry = Registry()
    specs = registry.specs()

    @contextmanager
    def refuse(response_cache, spans=None):
        raise ArmError("12 graph node texts have no cached voyage-4-large embedding")
        yield

    specs["graph_local"] = replace(specs["graph_local"], open=refuse)
    backend = LiveBackend(environ=ENV, arms=specs, samples=_samples, probe=lambda spec: None,
                          tracing=lambda environ: None, communities=lambda ids: {},
                          source_reader=lambda chunk_id: None, response_cache=tmp_path)
    with pytest.raises(AnswerFailed, match="no cached"):
        backend.answer(LIVE, "graph_local", spent_usd=0)


def test_an_unknown_option_is_a_backend_error(tmp_path):
    with pytest.raises(BackendError, match="unknown picker option"):
        _backend(Registry(), tmp_path=tmp_path).answer(LIVE, "bm25", spent_usd=0)


def test_tracing_roots_each_question_in_an_answer_question_span(tmp_path):
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    backend = _backend(Registry(), tracing=lambda environ: provider, tmp_path=tmp_path)
    answer = backend.answer(LIVE, "vector", spent_usd=0)
    spans = {s.name: s for s in exporter.get_finished_spans()}
    root = spans["answer_question"]
    assert answer.trace_id == f"{root.context.trace_id:032x}"
    assert spans[FILTER_SPAN].parent.span_id == root.context.span_id
    assert root.parent is None


def test_a_traced_answer_links_to_its_trace_in_phoenix(tmp_path):
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
    environ = {**ENV, "PHOENIX_COLLECTOR_ENDPOINT": "http://localhost:6006/"}
    backend = _backend(Registry(), environ=environ, tracing=lambda environ: provider,
                       tmp_path=tmp_path)
    answer = backend.answer(LIVE, "vector", spent_usd=0)
    assert answer.trace_url == f"http://localhost:6006/redirects/traces/{answer.trace_id}"


def test_with_tracing_off_no_provider_is_built(tmp_path):
    built = []
    backend = _backend(Registry(), tracing=lambda environ: built.append(environ) or None,
                       tmp_path=tmp_path)
    answer = backend.answer(LIVE, "vector", spent_usd=0)
    assert answer.trace_id is None and answer.trace_url is None
    assert built == [ENV]  # asked once, at start; build_provider imports no SDK when unset


def test_graph_global_answers_carry_their_communities_titles(tmp_path):
    calls = tuple(MapCall(cid, rank, 0.5, (), "ok", (), StageCost(ms=1))
                  for rank, cid in enumerate(("lower-a", "lower-b"), start=1))
    graph = GraphRetrieval(global_search=GlobalSearch(calls, 0, 0, 0))
    asked = []

    def communities(ids):
        asked.append(tuple(ids))
        return {"lower-a": CommunityInfo("lower-a", "Supply", "TSMC makes wafers.")}

    registry = Registry(arm_result(graph=graph))
    answer = _backend(registry, communities=communities, tmp_path=tmp_path).answer(
        LIVE, "graph_global", spent_usd=0)
    assert asked == [("lower-a", "lower-b")]
    assert answer.communities["lower-a"].title == "Supply"
    vector = _backend(Registry(), communities=communities, tmp_path=tmp_path).answer(
        LIVE, "vector", spent_usd=0)
    assert vector.communities == {}
    assert len(asked) == 1


def test_samples_chips_and_metrics_come_from_the_committed_files(tmp_path):
    backend = _backend(Registry(), tmp_path=tmp_path)
    assert backend.samples("vector") == (SAMPLE,)
    assert backend.samples("graph_global") == ()
    assert backend.chips() == (SAMPLE,)
    metrics = backend.sample_metrics("vector", "q0072")
    assert metrics.class_ == "lookup"
    assert metrics.run_file.startswith("2026-10-05-vector-dev")


def test_the_source_passage_comes_from_the_reader(tmp_path):
    passage = SourcePassage("c:0001", "a", "1", "NVDA", "NVIDIA CORP", "10-K", "FY2026",
                            "item_1", 0, 3, 0, "", "abc", "")
    backend = _backend(Registry(), source=lambda chunk_id: passage, tmp_path=tmp_path)
    assert backend.source("c:0001") is passage


def test_the_fake_backend_keeps_the_real_cap_rule():
    from tests.ui_fakes import FakeBackend

    result = arm_result(answer_cost=0.02)
    fake = FakeBackend({(LIVE, "vector"): result}, samples={"vector": (SAMPLE,)},
                       estimate=0.3, max_spend=1.0)
    backend: Backend = fake
    assert backend.answer(LIVE, "vector", spent_usd=0.5).spent_usd == pytest.approx(0.02)
    with pytest.raises(SpendCapReached):
        backend.answer(LIVE, "vector", spent_usd=0.8)
    with pytest.raises(AnswerFailed):
        backend.answer(SAMPLE.question, "vector", spent_usd=1.0)  # a sample needs no estimate
