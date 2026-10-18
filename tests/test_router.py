"""The router through ``Arm.run`` with in-memory stand-in arms (Step 13 ticket 03).

The classifier is the real one over scripted Haiku-shaped replies; the routing
maps are the pre-registered one and the committed dev-best file.  The stand-in
arms record which questions reach them.
"""
import json

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from eval.operational import latency_cell, question_cost
from ingest.provenance import REPO_ROOT
from retrieve.answer import DECLINE_TEXT
from retrieve.arm import ArmError
from retrieve.route_classifier import ROUTE_EFFORT, ROUTE_PROMPT_VERSION, RouteClassifier
from retrieve.route_prompt import load_route_prompt
from retrieve.router import Router, open_router
from retrieve.routing_maps import (
    DEV_BEST,
    PRE_REGISTERED,
    RoutingMapError,
    load_map,
)
from retrieve.tracing import (
    PREDICTED_CLASS_ATTR,
    ROUTE_ARM_ATTR,
    ROUTE_CLASSIFIER_SPAN,
    ROUTE_MAP_ATTR,
    ROUTE_SPAN,
    SpanRecorder,
)
from tests.router_fakes import (
    QUESTIONS,
    RecordedRouteModel,
    ScriptedRouteModel,
    StandInArms,
    class_reply,
)

DEV_BEST_FILE = REPO_ROOT / "benchmarks" / "router" / "dev-best-map.json"
PRE = {"lookup": "vector", "local": "graph_local", "multi_hop": "graph_traversal",
       "global": "graph_global"}


def _router(arms: StandInArms, map_name: str = PRE_REGISTERED, bodies=None, spans=None,
            from_cache=False, tmp_path=None):
    bodies = bodies or {q: class_reply(c) for c, q in QUESTIONS.items()}
    model = ScriptedRouteModel(bodies, from_cache=from_cache)
    classifier = RouteClassifier(model, load_route_prompt("v1"), "medium", spans)
    routing_map = load_map(map_name, DEV_BEST_FILE)
    return Router(classifier, routing_map, arms.specs, tmp_path or REPO_ROOT, spans), model


@pytest.mark.parametrize("predicted", ["lookup", "local", "multi_hop", "global"])
def test_each_class_goes_to_the_arm_the_pre_registered_map_names(predicted):
    arms = StandInArms()
    router, _ = _router(arms)
    with router:
        result = router.run(QUESTIONS[predicted])
    chosen = PRE[predicted]
    assert arms.arms[chosen].questions == [QUESTIONS[predicted]]
    assert all(not a.questions for n, a in arms.arms.items() if n != chosen)
    assert arms.opened == [chosen] and arms.closed == [chosen]
    assert result.routing.arm == chosen and result.answer.status == "answered"


def test_the_dev_best_map_is_read_from_its_file():
    document = json.loads(DEV_BEST_FILE.read_text(encoding="utf-8"))
    arms = StandInArms()
    router, _ = _router(arms, DEV_BEST)
    with router:
        for predicted in ("lookup", "global"):
            assert router.run(QUESTIONS[predicted]).routing.arm == document["map"][predicted]
    routing_map = load_map(DEV_BEST, DEV_BEST_FILE)
    assert routing_map.file == "benchmarks/router/dev-best-map.json"
    assert routing_map.sha256 != load_map(PRE_REGISTERED, DEV_BEST_FILE).sha256


def test_a_missing_dev_best_file_refuses_at_open(tmp_path):
    with pytest.raises(RoutingMapError, match="dev-best map"):
        load_map(DEV_BEST, tmp_path / "missing.json")
    opener = open_router(DEV_BEST, arms=StandInArms().specs, dev_best_file=tmp_path / "x.json")
    with pytest.raises(ArmError, match="dev-best map"), opener(tmp_path):
        pass


@pytest.mark.parametrize("document", [
    {"map": {"lookup": "vector"}},  # classes missing
    {"map": {**PRE, "lookup": "bm25"}},  # an unknown arm
    {"no_map": True},
])
def test_a_malformed_dev_best_file_is_refused(tmp_path, document):
    path = tmp_path / "map.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RoutingMapError):
        load_map(DEV_BEST, path)


def test_decline_returns_the_fixed_sentence_with_no_retrieval_and_no_arm():
    arms = StandInArms()
    router, _ = _router(arms)
    with router:
        result = router.run(QUESTIONS["decline"])
    assert result.answer.status == "declined" and result.answer.text == DECLINE_TEXT
    assert result.answer.cost_usd == 0 and not result.answer.from_cache
    assert result.retrieved == () and result.sources == ()
    assert arms.opened == [] and all(not a.questions for a in arms.arms.values())
    assert result.routing.arm == "decline" and result.routing.declined


def test_the_routing_record_holds_class_reason_map_effort_prompt_and_cost():
    arms = StandInArms()
    bodies = {QUESTIONS["local"]: class_reply("local", "One company, one subject.")}
    router, model = _router(arms, bodies=bodies, from_cache=True)
    with router:
        routing = router.run(QUESTIONS["local"]).routing
    assert (routing.predicted_class, routing.reason) == ("local", "One company, one subject.")
    assert (routing.map_name, routing.arm, routing.effort) == (PRE_REGISTERED, "graph_local",
                                                              "medium")
    prompt = load_route_prompt("v1")
    assert (routing.prompt_version, routing.prompt_sha256) == (prompt.prompt_version,
                                                               prompt.sha256)
    assert routing.cost.ms == 250.0 and routing.cost.from_cache and routing.cost.cost_usd > 0
    params = model.requests[0].params()  # the request: model, effort, schema, pinned prompt
    assert params["model"] == "claude-haiku-5-5"
    assert params["output_config"]["effort"] == "medium"
    assert params["system"][0]["text"] == prompt.system


def test_routing_is_a_stage_with_its_cost_and_latency(tmp_path):
    arms = StandInArms()
    router, _ = _router(arms)
    with router:
        routed = router.run(QUESTIONS["lookup"])
        declined = router.run(QUESTIONS["decline"])
    for result in (routed, declined):
        cost = question_cost(result)
        assert cost["routing"] == result.routing.cost.cost_usd > 0
        assert cost["total"] == pytest.approx(
            cost["embedding"] + cost["routing"] + cost["generation"])
    latency = latency_cell([routed, declined], lambda name: __import__("random").Random(0))
    assert latency["routing"]["n"] == 2 and latency["routing"]["p50"] == 250.0
    assert latency["retrieval"]["n"] == 0  # a declined question has no retrieval stage
    assert latency["search"]["n"] == 1 and latency["generation"]["n"] == 1


@pytest.mark.parametrize("body", [
    {**class_reply("lookup"), "stop_reason": "max_tokens"},
    class_reply("unanswerable"),
])
def test_an_unusable_classifier_reply_is_an_arm_error_and_no_arm_opens(body):
    arms = StandInArms()
    router, _ = _router(arms, bodies={QUESTIONS["lookup"]: body})
    with router, pytest.raises(ArmError, match="router"):
        router.run(QUESTIONS["lookup"])
    assert arms.opened == []


def test_an_arm_opens_once_and_closes_with_the_router():
    arms = StandInArms()
    router, _ = _router(arms)
    with router:
        router.run(QUESTIONS["lookup"])
        router.run(QUESTIONS["lookup"])
        router.run(QUESTIONS["global"])
        assert arms.closed == []
    assert arms.opened == ["vector", "graph_global"]
    assert sorted(arms.closed) == ["graph_global", "vector"]


def test_retrieve_routes_without_an_answer():
    arms = StandInArms()
    router, _ = _router(arms)
    with router:
        found = router.retrieve(QUESTIONS["multi_hop"])
        declined = router.retrieve(QUESTIONS["decline"])
    assert arms.arms["graph_traversal"].retrieved_only == [QUESTIONS["multi_hop"]]
    assert arms.arms["graph_traversal"].questions == []
    assert found.routing.arm == "graph_traversal" and declined.retrieved == ()


def test_the_config_records_the_router_and_each_arm_it_opened():
    arms = StandInArms()
    router, _ = _router(arms)
    with router:
        assert router.config.router.arms == {}
        router.run(QUESTIONS["lookup"])
        config = router.config
    routing = config.router
    assert (routing.model, routing.effort, routing.map_name) == ("claude-haiku-5-5", "medium",
                                                                PRE_REGISTERED)
    assert routing.prompt_version == load_route_prompt("v1").prompt_version
    assert dict(routing.map) == PRE and routing.map_file is None
    assert set(routing.arms) == {"vector"}
    assert config.models == {"vector.answer": "claude-sonnet-5-5"}
    assert config.chunker_version == "chunker-test" and config.k == 10


def test_spans_hold_the_route_and_the_classifier_without_prompt_text():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    spans = SpanRecorder(provider.get_tracer("test"), capture_text=False)
    router, _ = _router(StandInArms(), spans=spans)
    with router:
        router.run(QUESTIONS["global"])
    finished = {s.name: s for s in exporter.get_finished_spans()}
    route, classifier = finished[ROUTE_SPAN], finished[ROUTE_CLASSIFIER_SPAN]
    assert route.attributes["openinference.span.kind"] == "CHAIN"
    assert (route.attributes[PREDICTED_CLASS_ATTR], route.attributes[ROUTE_ARM_ATTR],
            route.attributes[ROUTE_MAP_ATTR]) == ("global", "graph_global", PRE_REGISTERED)
    assert classifier.parent.span_id == route.context.span_id
    assert classifier.attributes["openinference.span.kind"] == "LLM"
    assert classifier.attributes["llm.model_name"] == "claude-haiku-5-5"
    assert not any(k.startswith("llm.input_messages") for k in classifier.attributes)


def test_recorded_haiku_replies_route_each_invented_question_to_its_arm():
    model = RecordedRouteModel("invented")
    prompt = load_route_prompt(ROUTE_PROMPT_VERSION)
    assert (model.prompt_version, model.effort) == (prompt.prompt_version, ROUTE_EFFORT)
    arms = StandInArms()
    classifier = RouteClassifier(model, prompt, ROUTE_EFFORT)
    with Router(classifier, load_map(PRE_REGISTERED), arms.specs, REPO_ROOT) as router:
        routed = {c: router.run(q).routing for c, q in QUESTIONS.items()}
    assert {c: r.predicted_class for c, r in routed.items()} == {c: c for c in QUESTIONS}
    assert {c: r.arm for c, r in routed.items()} == {**PRE, "decline": "decline"}
    assert all(r.reason and r.cost.cost_usd > 0 for r in routed.values())
