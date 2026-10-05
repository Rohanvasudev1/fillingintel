"""The judge spot check: comparing two judges and choosing its questions (Step 5, ticket 10).

The comparison is a pure function, so every result below is built by hand.
The question choice is checked against the real dev records.
"""
from collections import Counter
from types import MappingProxyType

import pytest

from eval.judging.runner import JudgedQuestion
from eval.judging.scoring import JudgeConfig, RunScores
from eval.judging.spotcheck import (
    MAX_MEAN_GAP,
    MAX_MISSING_SHARE,
    MIN_AGREEMENT,
    QUOTA,
    MetricComparison,
    SpotCheckError,
    choose_judge,
    compare_judges,
    select_questions,
)
from eval.question_sets import agent_drafted_set, load_question_sets


def _run(run=1, scores=None, verdicts=None) -> RunScores:
    return RunScores(run=run, scores=MappingProxyType(scores or {}), errors=MappingProxyType({}),
                     cost_usd=0.0, calls=0, replayed=0,
                     verdicts=MappingProxyType(verdicts or {}))


def _supported(*verdicts: bool) -> JudgedQuestion:
    """One run with citation-support verdicts only."""
    score = sum(verdicts) / len(verdicts)
    return JudgedQuestion((_run(scores={"citation_support": score},
                                verdicts={"citation_support": tuple(verdicts)}),))


def _scored(**scores) -> JudgedQuestion:
    return JudgedQuestion((_run(scores=scores),))


def _by_metric(comparisons):
    return {c.metric: c for c in comparisons}


def test_the_thresholds_are_the_ones_fixed_in_the_spec():
    assert MIN_AGREEMENT == 0.85
    assert MAX_MEAN_GAP == 0.10
    assert MAX_MISSING_SHARE == 0.15  # user decision 2026-10-05, before the run


def test_identical_judges_agree_on_every_verdict_and_have_no_gap():
    judged = {
        "q1": JudgedQuestion((_run(scores={"faithfulness": 0.8, "answer_relevancy": 0.9,
                                           "citation_support": 0.5},
                                   verdicts={"citation_support": (True, False)}),)),
        "q2": JudgedQuestion((_run(scores={"decline_correct": 1.0},
                                   verdicts={"decline_correct": (True,)}),)),
    }
    result = _by_metric(compare_judges(judged, judged))
    assert result["citation_support"].value == 1.0
    assert result["citation_support"].compared == 2
    assert result["decline_correct"].value == 1.0
    assert result["faithfulness"].value == 0.0
    assert result["answer_relevancy"].value == 0.0
    assert all(c.passed for c in result.values())


def test_verdicts_are_compared_one_by_one_not_by_score():
    # Same score (1 of 2 supported), but the two judges disagree on both sentences.
    result = _by_metric(compare_judges({"q1": _supported(True, False)},
                                       {"q1": _supported(False, True)}))
    assert result["citation_support"].value == 0.0
    assert not result["citation_support"].passed


def _agreeing(agree: int, total: int) -> tuple[dict, dict]:
    reference = {f"q{i}": _supported(True) for i in range(total)}
    candidate = {f"q{i}": _supported(i < agree) for i in range(total)}
    return candidate, reference


def test_agreement_of_exactly_85_percent_passes():
    candidate, reference = _agreeing(17, 20)
    result = _by_metric(compare_judges(candidate, reference))["citation_support"]
    assert result.value == pytest.approx(0.85)
    assert result.passed


def test_agreement_just_under_85_percent_fails():
    candidate, reference = _agreeing(84, 99)  # 84.8%
    result = _by_metric(compare_judges(candidate, reference))["citation_support"]
    assert result.value == pytest.approx(84 / 99)
    assert not result.passed


def test_a_mean_gap_of_exactly_010_passes():
    candidate = {"q1": _scored(faithfulness=0.7), "q2": _scored(faithfulness=0.4)}
    reference = {"q1": _scored(faithfulness=0.8), "q2": _scored(faithfulness=0.3)}
    result = _by_metric(compare_judges(candidate, reference))
    assert result["faithfulness"].value == pytest.approx(0.10)
    assert result["faithfulness"].passed


def test_a_mean_gap_just_over_010_fails():
    result = _by_metric(compare_judges({"q1": _scored(answer_relevancy=0.699)},
                                       {"q1": _scored(answer_relevancy=0.8)}))
    assert result["answer_relevancy"].value == pytest.approx(0.101)
    assert not result["answer_relevancy"].passed


def test_the_gap_is_between_each_judges_mean_over_its_runs():
    candidate = {"q1": JudgedQuestion((_run(1, {"faithfulness": 0.6}),
                                       _run(2, {"faithfulness": 1.0})))}
    reference = {"q1": JudgedQuestion((_run(1, {"faithfulness": 0.9}),
                                       _run(2, {"faithfulness": 0.9})))}
    assert _by_metric(compare_judges(candidate, reference))["faithfulness"].value \
        == pytest.approx(0.1)  # |0.8 - 0.9|, not the mean of |0.6 - 0.9| and |1.0 - 0.9|


def test_verdicts_are_paired_run_by_run():
    candidate = {"q1": JudgedQuestion((_run(1, verdicts={"decline_correct": (True,)}),
                                       _run(2, verdicts={"decline_correct": (False,)})))}
    reference = {"q1": JudgedQuestion((_run(1, verdicts={"decline_correct": (True,)}),
                                       _run(2, verdicts={"decline_correct": (True,)})))}
    result = _by_metric(compare_judges(candidate, reference))["decline_correct"]
    assert (result.value, result.compared) == (0.5, 2)


def test_a_value_from_one_judge_only_is_counted_as_missing_not_compared():
    candidate = {"q1": _scored(faithfulness=None), "q2": _scored(faithfulness=0.5),
                 "q3": JudgedQuestion((_run(scores={"citation_support": None}),))}
    reference = {"q1": _scored(faithfulness=0.9), "q2": _scored(faithfulness=0.5),
                 "q3": _supported(True, True)}
    result = _by_metric(compare_judges(candidate, reference))
    assert (result["faithfulness"].value, result["faithfulness"].compared,
            result["faithfulness"].missing) == (0.0, 1, 1)
    assert (result["citation_support"].compared, result["citation_support"].missing) == (0, 2)


def _gaps_with_missing(missing: int, total: int) -> tuple[dict, dict]:
    reference = {f"q{i}": _scored(faithfulness=0.5) for i in range(total)}
    candidate = {f"q{i}": _scored(faithfulness=None if i < missing else 0.5)
                 for i in range(total)}
    return candidate, reference


def test_missing_values_up_to_15_percent_still_pass():
    candidate, reference = _gaps_with_missing(3, 20)  # 15%
    result = _by_metric(compare_judges(candidate, reference))["faithfulness"]
    assert (result.compared, result.missing, result.passed) == (17, 3, True)


def test_more_than_15_percent_missing_fails_even_when_the_rest_agree():
    candidate, reference = _gaps_with_missing(4, 20)  # 20%
    result = _by_metric(compare_judges(candidate, reference))["faithfulness"]
    assert (result.value, result.passed) == (0.0, False)
    verdicts = {f"q{i}": _supported(True) for i in range(6)}
    failed = {**verdicts, "q0": JudgedQuestion((_run(scores={"citation_support": None}),))}
    result = _by_metric(compare_judges(failed, verdicts))["citation_support"]
    assert (result.value, result.missing, result.passed) == (1.0, 1, False)  # 1 of 6 is 17%


def test_a_metric_with_nothing_to_compare_has_no_value_and_does_not_pass():
    candidate = {"q1": _scored(faithfulness=None)}
    reference = {"q1": _scored(faithfulness=None)}  # skipped by both, e.g. no kept sentence
    result = _by_metric(compare_judges(candidate, reference))["faithfulness"]
    assert (result.value, result.compared, result.missing, result.passed) == (None, 0, 0, False)


def test_judges_on_different_questions_are_refused():
    with pytest.raises(SpotCheckError, match="same questions"):
        compare_judges({"q1": _scored(faithfulness=0.5)}, {"q2": _scored(faithfulness=0.5)})


def test_judges_with_different_runs_are_refused():
    two_runs = {"q1": JudgedQuestion((_run(1, {"faithfulness": 0.5}),
                                      _run(2, {"faithfulness": 0.5})))}
    with pytest.raises(SpotCheckError, match="runs"):
        compare_judges(two_runs, {"q1": _scored(faithfulness=0.5)})


def test_verdict_lists_of_different_lengths_are_refused():
    # Both judges saw the same answer, so they must give one verdict per kept sentence.
    with pytest.raises(SpotCheckError, match="verdicts"):
        compare_judges({"q1": _supported(True)}, {"q1": _supported(True, True)})


# --- the questions ---------------------------------------------------------------------------

AGENT_DRAFTED = agent_drafted_set(load_question_sets()).records
DEV = tuple(r for r in AGENT_DRAFTED if r.split == "dev")


def test_ten_dev_questions_are_chosen_with_the_quota_per_class():
    chosen = select_questions(DEV)
    assert len(chosen) == 10 == sum(QUOTA.values())
    assert Counter(r.class_ for r in chosen) == Counter(QUOTA)
    assert all(r.split == "dev" for r in chosen)
    assert len({r.id for r in chosen}) == 10


def test_the_choice_does_not_depend_on_record_order():
    assert [r.id for r in select_questions(DEV)] == [r.id for r in select_questions(DEV[::-1])]


def test_test_split_records_are_never_chosen():
    assert [r.id for r in select_questions(AGENT_DRAFTED)] == [r.id for r in select_questions(DEV)]


def test_a_class_with_too_few_dev_records_is_refused():
    no_declines = tuple(r for r in DEV if r.class_ != "decline")
    with pytest.raises(SpotCheckError, match="decline"):
        select_questions(no_declines)


# --- the choice --------------------------------------------------------------------------------

MEDIUM = JudgeConfig(model="gpt-6-luna", effort="medium")
HIGH = JudgeConfig(model="gpt-6-luna", effort="high")
SOL = JudgeConfig(model="gpt-6-sol", effort="medium")


def _result(*passed: bool) -> tuple[MetricComparison, ...]:
    return tuple(MetricComparison(f"m{i}", "gap", 0.0, 1, 0, p) for i, p in enumerate(passed))


@pytest.mark.parametrize(("medium", "high", "expected"), [
    ((True, True), (True, True), MEDIUM),  # the cheaper effort wins
    ((True, False), (True, True), HIGH),  # medium must pass every metric
    ((True, False), (False, True), SOL),  # neither passes: the reference judges
])
def test_the_cheapest_candidate_that_passes_every_metric_is_chosen(medium, high, expected):
    results = ((MEDIUM, _result(*medium)), (HIGH, _result(*high)))
    assert choose_judge(results, SOL) == expected


def test_a_candidate_with_no_comparisons_does_not_pass():
    assert choose_judge(((MEDIUM, ()),), SOL) == SOL
