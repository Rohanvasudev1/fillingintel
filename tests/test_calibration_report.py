"""The calibration report from the user's entries and the judge's scores (Step 13 ticket 04).

Answers are real dev answers with their judge scores replaced by known values, so
every statistic below is worked by hand.
"""
from dataclasses import replace
from types import MappingProxyType

import pytest

from eval.calibration.answers import RUNS_DIR, load_answers
from eval.calibration.report import PASS_AT, calibration_report


@pytest.fixture(scope="module")
def pool():
    return load_answers(RUNS_DIR)


def _answer(base, faith, relevancy, citation):
    judged = {"faithfulness": faith, "answer_relevancy": relevancy,
              "citation_support": citation}
    return replace(base, kept=base.kept[:2], judged=MappingProxyType(judged))


def _entry(key, supported, unsupported, relevancy, support, seconds=40.0, rescore=False):
    claims = ([{"label": "supported"}] * supported
              + [{"label": "not_supported", "reason": "r"}] * unsupported
              + [{"label": "not_a_claim"}])
    return {"key": key, "kind": "answer", "rescore": rescore, "claims": claims,
            "relevancy": relevancy, "citation_support": support, "behaviour_correct": None,
            "seconds": seconds, "looked_again": False}


@pytest.fixture
def case(pool):
    bases = [a for a in pool if a.kind == "answer" and len(a.kept) >= 2][:4]
    # judge faithfulness means 0.2, 0.4, 0.6, 0.8; the user's 0.0, 0.5, 0.5, 1.0
    answers = [_answer(b, (f,) * 3, (r,) * 3, (1.0,) * 3)
               for b, f, r in zip(bases, (0.2, 0.4, 0.6, 0.8), (0.1, 0.4, 0.6, 0.9),
                                  strict=True)]
    entries = [_entry(answers[0].key, 0, 2, 1, [False, True]),
               _entry(answers[1].key, 1, 1, 2, [True, True]),
               _entry(answers[2].key, 1, 1, 4, [True, False], seconds=10.0),
               _entry(answers[3].key, 2, 0, 5, [True, True])]
    verdicts = {a.key: ((True, True),) * 3 for a in answers}
    verdicts[answers[0].key] = ((False, True),) * 3
    return answers, entries, verdicts


def test_faithfulness_compares_claim_shares_with_the_judge_mean(case):
    report = calibration_report(*case, seed=1)
    faith = report["metrics"]["faithfulness"]
    # ranks: user 1, 2.5, 2.5, 4 against judge 1..4 -> rho 0.9486833 (tests/test_calibration_stats)
    assert faith["n"] == 4
    assert faith["value"] == pytest.approx(0.9486833, abs=1e-6)
    assert faith["mae"] == pytest.approx((0.2 + 0.1 + 0.1 + 0.2) / 4)
    assert faith["signed_bias"] == pytest.approx((0.2 - 0.1 + 0.1 - 0.2) / 4)
    assert faith["passed"] is True and PASS_AT == 0.6
    assert faith["label"] == "calibrated, n = 4, claims split by an agent"


def test_relevancy_maps_the_rubric_to_zero_one(case):
    relevancy = calibration_report(*case, seed=1)["metrics"]["answer_relevancy"]
    # user (r-1)/4: 0, 0.25, 0.75, 1.0; judge 0.1, 0.4, 0.6, 0.9
    assert relevancy["mae"] == pytest.approx((0.1 + 0.15 + 0.15 + 0.1) / 4)
    assert relevancy["value"] == pytest.approx(1.0)


def test_citation_support_is_kappa_per_sentence_against_the_judge_majority(case):
    cite = calibration_report(*case, seed=1)["metrics"]["citation_support"]
    # user: F T | T T | T F | T T; judge: F T | T T | T T | T T -> 7 of 8 agree
    # p_user_yes 6/8, p_judge_yes 7/8; p_e = .75*.875 + .25*.125 = .6875; kappa = (.875-.6875)/.3125
    assert cite["n_sentences"] == 8 and cite["disagreements"] == 1
    assert cite["value"] == pytest.approx(0.6)
    assert cite["passed"] is True


def test_an_answer_with_no_checkable_claim_is_left_out_of_faithfulness(case):
    answers, entries, verdicts = case
    entries = [{**entries[0], "claims": [{"label": "not_a_claim"}]}, *entries[1:]]
    faith = calibration_report(answers, entries, verdicts, seed=1)["metrics"]["faithfulness"]
    assert faith["n"] == 3 and faith["excluded"]["no_claims"] == 1


def test_behaviour_is_percent_agreement_with_no_pass_rule(pool):
    decline = [a for a in pool if a.class_ == "decline"][:3]
    entries = [{"key": a.key, "kind": "behaviour", "rescore": False, "claims": None,
                "relevancy": None, "citation_support": None,
                "behaviour_correct": i != 0, "seconds": 20.0, "looked_again": False}
               for i, a in enumerate(decline)]
    report = calibration_report(decline, entries, {}, seed=1)
    row = report["metrics"]["decline_correct"]
    assert (row["n"], row["disagreements"]) == (3, 1)
    assert row["value"] == pytest.approx(2 / 3) and row["passed"] is None


def test_pace_and_rescore_are_reported(case):
    answers, entries, verdicts = case
    again = [_entry(answers[0].key, 0, 2, 1, [False, True], rescore=True),
             _entry(answers[1].key, 2, 0, 3, [True, True], rescore=True)]
    report = calibration_report(answers, [*entries, *again], verdicts, seed=1)
    assert report["pace"]["under_15_seconds"] == 1
    assert report["pace"]["median_seconds"] == pytest.approx(40.0)
    assert report["rescore"]["answers"] == 2
    assert report["rescore"]["citation_support"]["disagreements"] == 0


def test_unscored_answers_are_counted(case):
    answers, entries, verdicts = case
    report = calibration_report(answers, entries[:3], verdicts, seed=1)
    assert report["scored"] == 3 and report["drawn"] == 4
