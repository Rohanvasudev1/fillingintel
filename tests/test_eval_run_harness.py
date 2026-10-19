"""``python -m eval.run``: end-to-end time, the cost estimate and retrieval-only runs
(Step 13, ticket 01).

The fake arm, judge and eval records are ``tests.test_eval_run``'s.  A fake clock
moves forward inside each arm call by a fixed time per question, so every
``total_ms`` below is known.  The fake arm's q0073 is the one question with no
replayed call: its query embedding is made afresh and its answer is not cached.
"""
import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from eval.run import EXIT_RUN_ERROR, EXIT_USAGE, main
from eval.schema import load_records
from retrieve.arm import ArmSpec, EstimateQuestion
from tests.test_eval_run import (
    DEV_IDS,
    JUDGE_KEY_ENV,
    KEY_ENV,
    RESPONSE_CACHE,
    TEST_ID,
    TODAY,
    FakeArm,
    FakeJudge,
    _judge_spec,
    env,  # noqa: F401 (fixture)
    eval_dir,  # noqa: F401 (fixture)
    freeze_fake,
)

ARM_MS = {"q0072": 1000.0, "q0073": 3000.0, "q0113": 200.0}  # others 100


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TimedArm:
    """The fake arm, with the clock moving on by the question's time inside each call."""

    def __init__(self, arm: FakeArm, clock: Clock, ids: dict[str, str]):
        self.name, self.config = arm.name, arm.config
        self._arm, self._clock, self._ids = arm, clock, ids

    def run(self, question):
        self._clock.now += ARM_MS.get(self._ids[question], 100.0) / 1000
        return self._arm.run(question)

    def retrieve(self, question):
        self._clock.now += ARM_MS.get(self._ids[question], 100.0) / 1000
        return self._arm.retrieve(question)


class Estimate:
    def __init__(self, usd: float):
        self.usd = usd
        self.calls: list[tuple[tuple[EstimateQuestion, ...], bool]] = []

    def __call__(self, questions, *, retrieval_only):
        self.calls.append((tuple(questions), retrieval_only))
        return self.usd


class Harness:
    def __init__(self, directory: Path, tmp_path: Path, usd: float = 0.25):
        records = load_records(directory / "agent_drafted_set.jsonl")
        self.arm = FakeArm(records)
        self.judge = FakeJudge(records)
        self.estimate = Estimate(usd)
        self.opened: list[Path] = []
        self.judge_opened: list[Path] = []
        self.runs = tmp_path / "runs"
        self._eval_dir, self._tmp = directory, tmp_path
        self._clock = Clock()
        self._timed = TimedArm(self.arm, self._clock, {r.question: r.id for r in records})
        self.spent: float | Exception = 0.0  # committed Step 13 spend, or its read error

    def _open(self, response_cache, spans=None):
        self.opened.append(response_cache)
        return nullcontext(self._timed)

    def __call__(self, *argv: str) -> int:
        spec = ArmSpec(name="fake", required_env=(KEY_ENV,), open=self._open,
                       estimate=self.estimate)
        return main(["--arm", "fake", *argv], arms={"fake": spec},
                    judges=_judge_spec(self.judge, self.judge_opened), eval_dir=self._eval_dir,
                    runs_dir=self.runs, today=TODAY, response_cache=RESPONSE_CACHE,
                    uncached_root=self._tmp / "uncached", clock=self._clock,
                    frozen_file=self._tmp / "frozen.json", step13_spent=self._spent)

    def _spent(self) -> float:
        if isinstance(self.spent, Exception):
            raise self.spent
        return self.spent

    def result(self) -> dict:
        (path,) = self.runs.glob("*.json")
        return json.loads(path.read_text())


@pytest.fixture
def harness(eval_dir, tmp_path, env):  # noqa: F811
    return Harness(eval_dir, tmp_path)


def _questions(result) -> dict[str, dict]:
    return {q["id"]: q for q in result["questions"]}


# ── total_ms ───────────────────────────────────────────────────────────────────

def test_every_question_record_holds_the_time_around_its_whole_arm_call(harness):
    assert harness() == 0
    questions = _questions(harness.result())
    assert {i: q["total_ms"] for i, q in questions.items()} == pytest.approx(
        {"q0072": 1000.0, "q0073": 3000.0, "q0113": 200.0, "q0028": 100.0, "q0004": 100.0,
         "q0011": 100.0})


def test_a_question_with_any_replayed_call_is_flagged(harness):
    assert harness() == 0
    questions = _questions(harness.result())
    # Only q0073 embedded its question afresh and wrote a new answer.
    assert {i: q["replayed"] for i, q in questions.items()} == {
        i: i != "q0073" for i in DEV_IDS}


def test_cells_give_p50_and_p95_total_time_and_count_replayed_questions(harness):
    assert harness() == 0
    lookup = harness.result()["cells"]["agent_drafted"]["fake"]["lookup"]  # q0072, q0073
    total = lookup["total_ms"]
    assert total["n"] == 2 and total["replayed"] == 1
    assert total["p50"] == pytest.approx(2000.0)  # halfway between 1000 and 3000
    assert total["p95"] == pytest.approx(2900.0)  # 1000 + 0.95 * 2000
    interval = total["intervals"]["p50"]
    assert 1000.0 <= interval["low"] <= interval["high"] <= 3000.0
    assert "generation" in lookup["latency_ms"]  # the per-stage latency stays


# ── the estimate and --max-cost ────────────────────────────────────────────────

def test_the_estimate_is_printed_before_the_arm_opens(harness, capsys):
    assert harness() == 0
    assert "$0.25" in capsys.readouterr().out
    ((questions, retrieval_only),) = harness.estimate.calls
    assert [q.id for q in questions] == DEV_IDS
    assert [q.class_ for q in questions] == ["lookup", "lookup", "multi_hop", "global",
                                             "decline", "unanswerable"]
    assert retrieval_only is False


def test_the_estimate_covers_only_the_selected_classes(harness):
    assert harness("--classes", "global,decline") == 0
    ((questions, _),) = harness.estimate.calls
    assert [q.id for q in questions] == ["q0028", "q0004"]


def test_a_run_estimated_above_max_cost_is_refused_opening_nothing(eval_dir, tmp_path, env,  # noqa: F811
                                                                    capsys):
    harness = Harness(eval_dir, tmp_path, usd=1.20)
    assert harness("--max-cost", "1.00") == EXIT_USAGE
    err = capsys.readouterr().err
    assert "$1.20" in err and "$1.00" in err
    assert harness.opened == [] and harness.judge_opened == []
    assert not harness.runs.exists()


def test_a_run_estimated_at_or_below_max_cost_runs(harness):
    assert harness("--max-cost", "0.25") == 0
    assert len(_questions(harness.result())) == len(DEV_IDS)


@pytest.mark.parametrize("value", ["-1", "nothing", "nan"])
def test_a_max_cost_that_is_not_an_amount_is_refused(harness, value):
    with pytest.raises(SystemExit) as exc:
        harness("--max-cost", value)
    assert exc.value.code == EXIT_USAGE
    assert harness.opened == []


# ── the Step 13 cap ─────────────────────────────────────────────────────────────

@pytest.fixture
def final(eval_dir, tmp_path, env):  # noqa: F811
    harness = Harness(eval_dir, tmp_path, usd=1.00)
    freeze_fake(tmp_path / "frozen.json", harness.arm.config)
    return harness


def test_a_test_run_whose_estimate_and_step_13_spend_pass_the_cap_is_refused(final, capsys):
    final.spent = 9.50
    assert final("--split", "test", "--final") == EXIT_USAGE
    err = capsys.readouterr().err
    assert "$1.00" in err and "$9.50" in err and "$10.00" in err
    assert final.opened == [] and not final.runs.exists()


def test_a_test_run_that_fits_under_the_cap_runs(final):
    final.spent = 9.00
    assert final("--split", "test", "--final") == 0


def test_a_dev_run_of_an_arm_is_not_step_13_spend(final):
    final.spent = 100.0
    assert final() == 0


def test_an_unreadable_step_13_spend_stops_the_run(final, capsys):
    final.spent = ValueError("cannot read route-run9.json")
    assert final("--split", "test", "--final") == EXIT_RUN_ERROR
    assert "route-run9.json" in capsys.readouterr().err and final.opened == []


def test_a_question_a_committed_run_answered_under_the_frozen_config_is_estimated_as_replayed(
        final):
    assert final("--split", "test", "--final") == 0
    assert final("--split", "test", "--final") == 0
    first, second = final.estimate.calls
    assert [q.answered_by for q in first[0]] == [frozenset()]
    assert [(q.id, q.answered_by) for q in second[0]] == [(TEST_ID, frozenset({"fake"}))]


def test_an_uncached_run_credits_no_replay(final):
    assert final("--split", "test", "--final") == 0
    assert final("--split", "test", "--final", "--uncached") == 0
    assert [q.answered_by for q in final.estimate.calls[1][0]] == [frozenset()]


# ── --retrieval-only ───────────────────────────────────────────────────────────

def test_retrieval_only_makes_no_answer_or_judge_call(harness):
    assert harness("--retrieval-only") == 0
    assert harness.arm.questions == []
    assert len(harness.arm.retrieved_questions) == len(DEV_IDS)
    assert harness.judge.calls == [] and harness.judge_opened == []


def test_retrieval_only_is_estimated_as_retrieval_only(harness):
    assert harness("--retrieval-only") == 0
    ((_, retrieval_only),) = harness.estimate.calls
    assert retrieval_only is True


def test_retrieval_only_needs_no_judge_settings(harness, monkeypatch):
    monkeypatch.delenv(JUDGE_KEY_ENV)
    assert harness("--retrieval-only") == 0


def test_the_header_records_the_mode(harness):
    assert harness() == 0
    assert harness.result()["header"]["mode"] == "full"


def test_a_retrieval_only_file_holds_retrieval_scores_and_latency_only(harness):
    assert harness("--retrieval-only") == 0
    result = harness.result()
    header = result["header"]
    assert header["mode"] == "retrieval_only"
    for absent in ("judging", "answer_prompt", "answer_max_tokens", "prices"):
        assert absent not in header
    assert "judge" not in header["models"] and "judge" not in header["efforts"]
    lookup = result["cells"]["agent_drafted"]["fake"]["lookup"]
    assert set(lookup) == {"n", "directional", "metrics", "intervals", "wrong_evidence",
                           "latency_ms"}
    assert lookup["metrics"]["recall@10"] == pytest.approx(0.5)  # q0072 1, q0073 0
    assert set(lookup["latency_ms"]) == {"retrieval", "embed", "search"}
    assert "judge_usage" not in result
    record = _questions(result)["q0072"]
    for absent in ("answer", "judged", "cost_usd", "generation_ms", "total_ms", "replayed"):
        assert absent not in record
    assert record["metrics"]["recall@10"] == 1.0
    assert record["retrieved"][0]["chunk_id"] == record["gold_chunk_ids"][0]


def test_a_retrieval_only_wrong_evidence_rate_counts_ranks_only(harness):
    # q0073 cites an unretrieved hard negative in the full run; retrieval-only has no answer.
    assert harness() == 0
    full = _questions(harness.result())["q0073"]["wrong_evidence"]
    harness.runs = harness.runs.parent / "retrieval"
    assert harness("--retrieval-only") == 0
    retrieval = _questions(harness.result())["q0073"]["wrong_evidence"]
    assert [h for h in full if h["cited"]] and not [h for h in retrieval if h["cited"]]


def test_retrieval_only_runs_with_classes(harness):
    assert harness("--retrieval-only", "--classes", "lookup") == 0
    result = harness.result()
    assert list(result["cells"]["agent_drafted"]["fake"]) == ["lookup"]
    assert result["header"]["classes"] == ["lookup"]


def test_retrieval_only_runs_on_test_with_final(harness, tmp_path):
    freeze_fake(tmp_path / "frozen.json", harness.arm.config)
    assert harness("--retrieval-only", "--split", "test", "--final") == 0
    result = harness.result()
    assert [q["id"] for q in result["questions"]] == [TEST_ID]
    assert result["header"]["final"] is True and result["header"]["mode"] == "retrieval_only"


def test_retrieval_only_on_test_still_needs_final(harness):
    assert harness("--retrieval-only", "--split", "test") == EXIT_USAGE
    assert harness.opened == []
