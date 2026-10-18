"""``python -m eval.run --arm router --map ...`` and the ``--final`` freeze check
(Step 13 ticket 03).

The router is the real one, over ``tests.test_eval_run``'s fake arm (standing in
for every arm the map names) and a scripted classifier that predicts each
record's gold class (``lookup`` for an unanswerable one).  The judges are the
fake ones.
"""
import json
from contextlib import contextmanager, nullcontext
from pathlib import Path

import pytest

from eval.freeze import arm_config_sha256, frozen_document, write_frozen
from eval.run import EXIT_USAGE, main
from eval.schema import load_records
from retrieve.arm import ArmSpec
from retrieve.route_classifier import RouteClassifier
from retrieve.route_prompt import load_route_prompt
from retrieve.router import Router
from retrieve.routing_maps import DEV_BEST, PRE_REGISTERED, load_map
from tests.router_fakes import ScriptedRouteModel, class_reply
from tests.test_eval_run import (
    KEY_ENV,
    RESPONSE_CACHE,
    TEST_ID,
    TODAY,
    FakeArm,
    FakeJudge,
    _judge_spec,
    env,  # noqa: F401 (fixture)
    eval_dir,  # noqa: F401 (fixture)
    no_cost,
)

ARM_NAMES = ("vector", "graph_local", "graph_traversal", "graph_global")


class RouterHarness:
    def __init__(self, directory: Path, tmp_path: Path):
        records = load_records(directory / "agent_drafted_set.jsonl")
        self.arm = FakeArm(records)
        self.judge = FakeJudge(records)
        self.runs = tmp_path / "runs"
        self.frozen = tmp_path / "frozen.json"
        self.model = ScriptedRouteModel({r.question: class_reply(_predicted(r.class_))
                                         for r in records}, from_cache=True)
        self._dir, self._tmp = directory, tmp_path
        arm_specs = {n: ArmSpec(name=n, required_env=(KEY_ENV,),
                                open=lambda cache, spans=None: nullcontext(self.arm),
                                estimate=no_cost) for n in ARM_NAMES}
        self.arm_specs = arm_specs

        def with_map(map_name: str) -> ArmSpec:
            @contextmanager
            def open_router(cache, spans=None):
                classifier = RouteClassifier(self.model, load_route_prompt("v1"), "low", spans)
                routing_map = load_map(map_name, ARM_NAMES)
                with Router(classifier, routing_map, arm_specs, cache, spans) as router:
                    yield router

            return ArmSpec(name="router", required_env=(KEY_ENV,), open=open_router,
                           estimate=no_cost)

        self.spec = ArmSpec(name="router", required_env=(KEY_ENV,), open=None,
                            estimate=no_cost, with_map=with_map)

    def __call__(self, *argv: str) -> int:
        return main(list(argv), arms={"router": self.spec, "vector": self.arm_specs["vector"]},
                    judges=_judge_spec(self.judge), eval_dir=self._dir, runs_dir=self.runs,
                    today=TODAY, response_cache=RESPONSE_CACHE,
                    uncached_root=self._tmp / "uncached", frozen_file=self.frozen)

    def result(self) -> tuple[str, dict]:
        (path,) = self.runs.glob("*.json")
        return path.name, json.loads(path.read_text())


def _predicted(class_: str) -> str:
    return "lookup" if class_ == "unanswerable" else class_


@pytest.fixture
def harness(eval_dir, tmp_path, env):  # noqa: F811
    return RouterHarness(eval_dir, tmp_path)


def test_map_is_refused_for_an_arm(harness, capsys):
    assert harness("--arm", "vector", "--map", PRE_REGISTERED) == EXIT_USAGE
    assert "--map applies only to --arm router" in capsys.readouterr().err


def test_the_router_needs_a_map(harness, capsys):
    assert harness("--arm", "router") == EXIT_USAGE
    assert "needs --map" in capsys.readouterr().err


def test_a_router_run_names_its_map_and_records_each_routing(harness):
    assert harness("--arm", "router", "--map", PRE_REGISTERED) == 0
    name, result = harness.result()
    assert name.startswith(f"{TODAY.isoformat()}-router-{PRE_REGISTERED}-dev-")
    header = result["header"]
    assert header["arm"] == "router" and header["routing_map"] == PRE_REGISTERED
    router = header["router"]
    assert router["model"] == "claude-haiku-5-5" and router["effort"] == "low"
    assert router["prompt"]["version"] == load_route_prompt("v1").prompt_version
    assert router["map"]["name"] == PRE_REGISTERED and router["map"]["arms"]["global"] == \
        "graph_global"
    assert router["map"]["sha256"] == load_map(PRE_REGISTERED, ARM_NAMES).sha256
    assert router["prices"]["date"] == "2026-10-10"
    # every arm the router opened, with its own provenance as its own run records it
    assert set(router["arms"]) == {"vector", "graph_traversal", "graph_global"}
    assert router["arms"]["vector"]["answer_prompt"]["version"] == "v1"
    questions = {q["id"]: q for q in result["questions"]}
    assert questions["q0004"]["routing"]["arm"] == "decline"  # the decline record
    assert questions["q0004"]["answer"]["status"] == "declined"
    assert questions["q0072"]["routing"]["arm"] == "vector"
    assert questions["q0072"]["routing"]["predicted_class"] == "lookup"
    assert questions["q0072"]["cost_usd"]["routing"] > 0
    assert "routing" in header["metric_definitions"]
    cell = result["cells"]["agent_drafted"]["router"]["lookup"]
    assert cell["latency_ms"]["routing"]["n"] == 2
    assert cell["cost_usd"]["per_query_routing"] > 0


def test_a_dev_best_run_reads_the_committed_map(harness):
    assert harness("--arm", "router", "--map", DEV_BEST) == 0
    name, result = harness.result()
    assert f"-router-{DEV_BEST}-dev-" in name
    assert result["header"]["router"]["map"]["file"] == "benchmarks/router/dev-best-map.json"
    questions = {q["id"]: q for q in result["questions"]}
    assert questions["q0072"]["routing"]["arm"] == "graph_local"


def _freeze(harness: RouterHarness, **changes) -> None:
    configs = {n: harness.arm.config for n in ARM_NAMES}
    document = frozen_document(
        commit="abc1234", router_prompt=load_route_prompt("v1"), router_effort="low",
        maps=[load_map(PRE_REGISTERED, ARM_NAMES), load_map(DEV_BEST, ARM_NAMES)],
        arm_configs=configs)
    write_frozen({**document, **changes}, harness.frozen)


def test_final_is_refused_without_a_frozen_record(harness, capsys):
    assert harness("--arm", "vector", "--split", "test", "--final") == EXIT_USAGE
    assert "frozen record" in capsys.readouterr().err
    assert not harness.runs.exists()


def test_final_runs_when_the_frozen_record_matches(harness):
    _freeze(harness)
    assert harness("--arm", "router", "--map", PRE_REGISTERED, "--split", "test",
                   "--final") == 0
    _, result = harness.result()
    assert [q["id"] for q in result["questions"]] == [TEST_ID]


def test_final_is_refused_when_an_arm_differs_from_the_frozen_record(harness, capsys):
    _freeze(harness)
    frozen = json.loads(harness.frozen.read_text())
    frozen["arms"]["vector"]["config_sha256"] = "0" * 64
    harness.frozen.write_text(json.dumps(frozen))
    assert harness("--arm", "vector", "--split", "test", "--final") == EXIT_USAGE
    assert "vector differs from the frozen record" in capsys.readouterr().err
    assert not harness.runs.exists()


def test_final_is_refused_when_the_router_differs_from_the_frozen_record(harness, capsys):
    _freeze(harness, router={"model": "claude-haiku-5-5", "effort": "high",
                             "prompt_version": load_route_prompt("v1").prompt_version,
                             "prompt_sha256": load_route_prompt("v1").sha256})
    assert harness("--arm", "router", "--map", PRE_REGISTERED, "--split", "test",
                   "--final") == EXIT_USAGE
    assert "router differs from the frozen record" in capsys.readouterr().err


def test_the_frozen_record_pins_each_arm_by_its_config():
    arm = FakeArm([])
    assert arm_config_sha256(arm.config) == arm_config_sha256(FakeArm([]).config)


def test_final_refuses_a_drifted_inner_arm_before_any_question_is_answered(harness, capsys):
    _freeze(harness)
    frozen = json.loads(harness.frozen.read_text())
    frozen["arms"]["graph_global"]["config_sha256"] = "0" * 64
    harness.frozen.write_text(json.dumps(frozen))
    assert harness("--arm", "router", "--map", PRE_REGISTERED, "--split", "test",
                   "--final") == EXIT_USAGE
    assert "graph_global differs from the frozen record" in capsys.readouterr().err
    assert harness.arm.questions == [] and harness.model.requests == []
