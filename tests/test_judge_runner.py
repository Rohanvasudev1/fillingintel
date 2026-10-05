"""The judge runner's ordering and failure handling, and the judged report's edge cases."""
import random
import threading
from dataclasses import replace
from types import MappingProxyType

import pytest

from eval.judging.openai_backend import OpenAIUsage
from eval.judging.report import judged_cell, question_judged
from eval.judging.runner import JudgedQuestion, JudgeRunError, _GuardedJudges, judge_all
from eval.judging.scoring import JudgeConfig, RunScores


def _scores(run: int, value: float | None) -> RunScores:
    return RunScores(run, MappingProxyType({"faithfulness": value}), MappingProxyType({}),
                     0.01, calls=2, replayed=0)


class _ByQuestion:
    config = JudgeConfig()

    def judge(self, item, run):
        return _scores(run, item + run / 10)


class _Failing:
    config = JudgeConfig()

    def __init__(self, error: Exception):
        self._error = error

    def judge(self, item, run):
        raise self._error


class _MeetsAtBarrier:
    """Each job waits for *parties* jobs to be running at once; records whether they met."""

    config = JudgeConfig()

    def __init__(self, parties: int, timeout: float):
        self._barrier = threading.Barrier(parties, timeout=timeout)
        self.met = False

    def judge(self, item, run):
        try:
            self._barrier.wait()
            self.met = True
        except threading.BrokenBarrierError:
            pass
        return _scores(run, 0.5)


# The default is pinned at 4 on purpose: 8 concurrent gpt-6-luna jobs went over the
# account's 200,000 tokens per minute and exhausted the SDK's retries.
def test_judge_all_runs_four_jobs_at_once_by_default():
    judge = _MeetsAtBarrier(parties=4, timeout=10)
    judge_all(judge, [0, 1, 2, 3])  # 3 runs each: 12 jobs, 3 meetings of 4
    assert judge.met


def test_judge_all_never_runs_five_jobs_at_once_by_default():
    judge = _MeetsAtBarrier(parties=5, timeout=0.5)
    judge_all(judge, list(range(4)))
    assert not judge.met


def test_judge_all_returns_every_run_in_question_and_run_order():
    judged = judge_all(_ByQuestion(), [0, 1, 2], workers=4)
    assert [[r.scores["faithfulness"] for r in j.runs] for j in judged] == [
        [0.1, 0.2, 0.3], [1.1, 1.2, 1.3], [2.1, 2.2, 2.3]]


def test_an_unexpected_judging_error_becomes_a_run_error():
    guarded = _GuardedJudges(_Failing(ValueError("bad ragas input")))
    with pytest.raises(JudgeRunError, match="ValueError"):
        guarded.judge(None, 1)


def test_a_run_error_propagates_from_judge_all():
    with pytest.raises(JudgeRunError, match="down"):
        judge_all(_Failing(JudgeRunError("API down")), [0, 1])


def test_a_missing_run_is_reported_as_no_value_not_a_crash():
    partial = JudgedQuestion((_scores(1, 0.5), _scores(3, 0.7)))
    record = question_judged(partial, runs=3)
    assert record["metrics"]["faithfulness"]["runs"] == [0.5, None, 0.7]
    cell = judged_cell([partial], 3, lambda name: random.Random(name))
    assert cell["metrics"]["faithfulness"]["run_means"] == [0.5, None, 0.7]
    assert cell["cost_usd"] == pytest.approx({"total": 0.02, "per_query": 0.02})


def test_usage_totals_report_reasoning_tokens_separately():
    usage = OpenAIUsage(input_tokens=1000, cached_input_tokens=200, cache_write_tokens=0,
                        output_tokens=300, reasoning_tokens=250)
    runs = tuple(replace(_scores(run, 0.5), usage=usage) for run in (1, 2, 3))
    expected = {"input_tokens": 3000, "cached_input_tokens": 600, "cache_write_tokens": 0,
                "output_tokens": 900, "reasoning_tokens": 750}
    assert question_judged(JudgedQuestion(runs), runs=3)["usage"] == expected
    cell = judged_cell([JudgedQuestion(runs)] * 2, 3, lambda name: random.Random(name))
    assert cell["usage"] == {k: 2 * v for k, v in expected.items()}
