"""Which questions a run would replay from the response cache (Step 13, ticket 05).

The frozen record is the committed ``benchmarks/router/frozen.json``.  Arm run
files are built from the committed dev router run's header, whose inner arm
provenance matches that record, so a file "under the frozen config" here is one
whose header a real run under that config would write.
"""
import json
from pathlib import Path

import pytest

from eval.freeze import read_frozen
from eval.judging.openai_judge import JUDGE_EFFORT, JUDGE_MODEL
from eval.replays import known_replays
from eval.results import JUDGE_KEY
from eval.router_report import PROVENANCE_KEYS, arm_provenance
from retrieve.arm import EstimateQuestion
from retrieve.run_estimate import GRAPH_LOCAL_RUN, MeasuredEstimate

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "benchmarks" / "runs"
ROUTER_DEV = RUNS / "2026-10-18-router-pre_registered-dev-c1b171d.json"
FROZEN = read_frozen(REPO / "benchmarks" / "router" / "frozen.json")
CACHE = "responses"


def _router_run() -> dict:
    return json.loads(ROUTER_DEV.read_text(encoding="utf-8"))


def _arm_run(arm: str, questions: list[dict], **header) -> dict:
    """A run file of *arm* whose header holds the frozen provenance, as eval.run writes it."""
    router = _router_run()["header"]
    provenance = router["router"]["arms"][arm]
    models = {**provenance["models"], JUDGE_KEY: JUDGE_MODEL}
    efforts = {**provenance["efforts"], JUDGE_KEY: JUDGE_EFFORT}
    return {"header": {"arm": arm, "split": "test", "mode": "full",
                       "response_cache": {"uncached": False, "dir": CACHE},
                       "judging": router["judging"],
                       **provenance, "models": models, "efforts": efforts, **header},
            "questions": questions}


def _answered(qid: str) -> dict:
    return {"id": qid, "answer": {"from_cache": False}, "judged": {"cost_usd": 0.01}}


def _write(runs: Path, name: str, document: dict) -> None:
    runs.mkdir(exist_ok=True)
    (runs / name).write_text(json.dumps(document), encoding="utf-8")


def test_a_full_run_under_the_frozen_config_replays_its_answered_and_judged_questions(tmp_path):
    _write(tmp_path, "v.json", _arm_run("vector", [_answered("q0001"), _answered("q0002"),
                                                   {"id": "q0003", "answer": None}]))
    replays = known_replays(tmp_path, FROZEN, CACHE)
    assert replays.answered == {"vector": frozenset({"q0001", "q0002"})}


def test_a_run_under_another_config_replays_nothing(tmp_path):
    other = _arm_run("graph_local", [_answered("q0001")])
    other["header"]["k"] = 11
    _write(tmp_path, "g.json", other)
    _write(tmp_path, "old.json", json.loads(GRAPH_LOCAL_RUN.read_text(encoding="utf-8")))
    assert known_replays(tmp_path, FROZEN, CACHE).answered == {}


def test_a_run_judged_by_another_judge_replays_nothing(tmp_path):
    run = _arm_run("vector", [_answered("q0001")])
    run["header"]["judging"] = {**run["header"]["judging"], "effort": "high"}
    _write(tmp_path, "v.json", run)
    assert known_replays(tmp_path, FROZEN, CACHE).answered == {}


@pytest.mark.parametrize("header", [{"mode": "retrieval_only"},
                                    {"response_cache": {"uncached": True, "dir": "x"}},
                                    {"response_cache": {"uncached": False, "dir": "other"}}])
def test_retrieval_only_runs_and_runs_on_another_cache_replay_nothing(tmp_path, header):
    _write(tmp_path, "v.json", _arm_run("vector", [_answered("q0001")], **header))
    assert known_replays(tmp_path, FROZEN, CACHE).answered == {}


def test_a_router_run_under_the_frozen_router_replays_its_classifications(tmp_path):
    _write(tmp_path, "r.json", _router_run())
    replays = known_replays(tmp_path, FROZEN, CACHE)
    assert len(replays.routed) == 93 and "q0004" in replays.routed
    assert replays.answered == {}  # a router run's answers are not credited to the arms


def test_a_classification_under_another_router_prompt_is_not_replayed(tmp_path):
    run = _router_run()
    for q in run["questions"]:
        q["routing"]["prompt_sha256"] = "0" * 64
    _write(tmp_path, "r.json", run)
    assert known_replays(tmp_path, FROZEN, CACHE).routed == frozenset()


def test_the_provenance_keys_are_the_ones_arm_provenance_writes():
    from tests.test_eval_run import _ALL, FakeArm

    config = FakeArm(list(_ALL.values())).config
    assert tuple(arm_provenance(config)) == PROVENANCE_KEYS


def test_an_unreadable_run_file_is_named(tmp_path):
    _write(tmp_path, "bad.json", {"header": {}})
    with pytest.raises(ValueError, match="bad.json"):
        known_replays(tmp_path, FROZEN, CACHE)


def test_a_question_whose_answer_replays_costs_nothing():
    estimate = MeasuredEstimate(GRAPH_LOCAL_RUN, arm="graph_local")
    fresh = EstimateQuestion("q0072", "lookup")
    replayed = EstimateQuestion("q0072", "lookup", answered_by=frozenset({"graph_local"}))
    elsewhere = EstimateQuestion("q0072", "lookup", answered_by=frozenset({"vector"}))
    assert estimate([fresh], retrieval_only=False) > 0
    assert estimate([replayed], retrieval_only=False) == 0
    assert estimate([replayed], retrieval_only=True) == 0
    assert estimate([elsewhere], retrieval_only=False) == estimate([fresh], retrieval_only=False)
