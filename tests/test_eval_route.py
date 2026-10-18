"""``python -m eval.route``: the classifier alone on dev records (Step 13 ticket 03).

The records are ``tests.test_eval_run``'s six dev questions (two lookup, one
multi_hop, one global, one decline, one unanswerable) and one test question.
The model replays scripted Haiku-shaped replies through the real response cache
in a temporary folder, so a second run replays every call.
"""
import json

import pytest

from eval.route import EXIT_USAGE, main
from eval.schema import load_records
from retrieve.answer_model import AnswerModelError
from tests.router_fakes import ScriptedRouteModel, class_reply
from tests.test_eval_run import eval_dir  # noqa: F401 (fixture)

PREDICTED = {"q0072": "lookup", "q0073": "local", "q0113": "multi_hop", "q0028": "global",
             "q0004": "decline", "q0011": "decline"}


class Route:
    def __init__(self, directory, tmp_path, bodies=None, spent=0.0):
        records = load_records(directory / "agent_drafted_set.jsonl")
        self.model = ScriptedRouteModel(bodies or {
            r.question: class_reply(PREDICTED.get(r.id, "lookup")) for r in records})
        self.out = tmp_path / "router"
        self.cache = tmp_path / "cache"
        self._dir, self._spent = directory, spent

    def __call__(self, *argv: str) -> int:
        return main(list(argv), eval_dir=self._dir, out_dir=self.out, response_cache=self.cache,
                    model=self.model, spent=lambda: self._spent)

    def report(self, n: int = 1) -> dict:
        return json.loads((self.out / f"route-run{n}.json").read_text())


@pytest.fixture
def route(eval_dir, tmp_path):  # noqa: F811
    return Route(eval_dir, tmp_path)


def test_a_run_writes_accuracy_confusion_false_declines_and_the_unanswerable_share(route):
    assert route("--effort", "medium") == 0
    report = route.report()
    assert report["split"] == "dev" and report["effort"] == "medium"
    assert report["prompt"]["version"].startswith("route/v1@")
    metrics = report["metrics"]
    assert metrics["label"] == "against agent-drafted class labels"
    assert (metrics["accuracy"]["n"], metrics["accuracy"]["correct"]) == (5, 4)  # q0073 wrong
    assert metrics["confusion"]["lookup"] == {"lookup": 1, "local": 1}
    assert metrics["false_declines"] == ["q0011"]
    assert metrics["unanswerable"] == {"n": 1, "share": {"decline": 1.0}}
    questions = {q["id"]: q for q in report["questions"]}
    assert set(questions) == {"q0072", "q0073", "q0113", "q0028", "q0004", "q0011"}
    assert questions["q0072"]["reason"] == "Scripted." and questions["q0072"]["correct"]
    assert report["cost_usd"] > 0 and report["spent_usd"] == pytest.approx(report["cost_usd"])


def test_a_rerun_replays_from_the_cache_and_spends_nothing(route):
    assert route() == 0
    calls = len(route.model.requests)
    assert route() == 0
    assert len(route.model.requests) == calls
    assert route.report(2)["spent_usd"] == 0 and route.report(2)["estimate_usd"] == 0


def test_the_estimate_is_printed_and_a_run_above_max_cost_is_refused(route, capsys):
    assert route("--max-cost", "0.0001") == EXIT_USAGE
    err = capsys.readouterr()
    assert "estimate: $" in err.out and "--max-cost" in err.err
    assert route.model.requests == [] and not route.out.exists()


def test_a_run_over_the_step_13_cap_is_refused(eval_dir, tmp_path, capsys):  # noqa: F811
    route = Route(eval_dir, tmp_path, spent=9.9999)
    assert route() == EXIT_USAGE
    assert "Step 13 cap" in capsys.readouterr().err
    assert route.model.requests == []


def test_the_test_split_is_refused(route, capsys):
    assert route("--split", "test") == EXIT_USAGE
    assert "eval.run --final" in capsys.readouterr().err
    assert route.model.requests == []


def test_an_unusable_reply_is_recorded_as_an_error_and_counted_wrong(eval_dir, tmp_path):  # noqa: F811
    records = load_records(eval_dir / "agent_drafted_set.jsonl")
    bodies = {r.question: class_reply(PREDICTED.get(r.id, "lookup")) for r in records}
    q0072 = next(r.question for r in records if r.id == "q0072")
    bodies[q0072] = {**bodies[q0072], "stop_reason": "max_tokens"}
    route = Route(eval_dir, tmp_path, bodies=bodies)
    assert route() == 0
    report = route.report()
    assert report["metrics"]["errors"] == ["q0072"]
    question = next(q for q in report["questions"] if q["id"] == "q0072")
    assert question["predicted_class"] is None and "cut off" in question["error"]


def test_a_failed_api_call_stops_the_run(eval_dir, tmp_path):  # noqa: F811
    route = Route(eval_dir, tmp_path)

    def fail(request):
        raise AnswerModelError("Anthropic API call failed: APIConnectionError")

    route.model.complete = fail
    assert route() == 3 and not route.out.exists()
