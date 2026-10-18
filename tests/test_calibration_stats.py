"""Agreement statistics for judge calibration (Step 13 ticket 04), on small known cases.

Expected values are worked by hand or taken from textbook examples, never recomputed
the way the code computes them.
"""
import math
import random

import pytest

from eval.calibration.stats import (
    cohen_kappa,
    fisher_interval,
    kappa_by_answer_interval,
    mean_absolute_error,
    mean_signed_difference,
    percent_agreement,
    spearman,
)


def test_spearman_of_a_monotone_pair_is_one_and_of_a_reversed_pair_minus_one():
    assert spearman([1, 2, 3, 4], [10, 20, 35, 90]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)


def test_spearman_known_case_without_ties():
    # ranks x 1..5, y 2,1,4,3,5: d^2 = 1+1+1+1+0 = 4; rho = 1 - 6*4/(5*24) = 0.8
    assert spearman([1, 2, 3, 4, 5], [2, 1, 4, 3, 5]) == pytest.approx(0.8)


def test_spearman_averages_tied_ranks():
    # x ranks 1, 2.5, 2.5, 4; y ranks 1..4; Pearson of the ranks = 0.9486833 (by hand)
    assert spearman([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.9486833, abs=1e-6)


def test_spearman_is_undefined_when_one_side_is_constant():
    assert spearman([1, 1, 1], [1, 2, 3]) is None
    assert spearman([1, 2], [2, 1]) is None, "fewer than 3 pairs"


def test_fisher_interval_matches_the_research_note():
    # docs/research/router-langgraph-and-judge-calibration.md: rho 0.60 at n = 60 is
    # about [0.41, 0.74]
    low, high = fisher_interval(0.6, 60)
    assert (round(low, 2), round(high, 2)) == (0.41, 0.74)
    assert fisher_interval(0.6, 3) is None, "n - 3 must be positive"


def test_mae_and_signed_difference_are_judge_minus_human():
    human, judge = [0.5, 1.0, 0.0], [0.7, 0.9, 0.0]
    assert mean_absolute_error(human, judge) == pytest.approx(0.1)
    assert mean_signed_difference(human, judge) == pytest.approx((0.2 - 0.1 + 0.0) / 3)


def test_cohen_kappa_textbook_case():
    # 50 items: both yes 20, both no 15, a yes/b no 5, a no/b yes 10.
    # p_o = 0.7; p_yes(a)=0.5, p_yes(b)=0.6; p_e = 0.3 + 0.2 = 0.5; kappa = 0.4
    a = [True] * 25 + [False] * 25
    b = [True] * 20 + [False] * 5 + [True] * 10 + [False] * 15
    assert cohen_kappa(a, b) == pytest.approx(0.4)


def test_cohen_kappa_is_undefined_when_chance_agreement_is_total():
    assert cohen_kappa([True, True], [True, True]) is None


def test_percent_agreement_counts_disagreements():
    assert percent_agreement([True, False, True], [True, True, True]) == (2 / 3, 1)


def test_kappa_interval_resamples_answers_not_sentences():
    # Two answers agree perfectly, one disagrees on every sentence; resampling whole answers
    # keeps a sentence with its answer, so every resample's kappa comes from those blocks.
    answers = [([True, False], [True, False]), ([True, False], [True, False]),
               ([True, True, False], [False, False, True])]
    interval = kappa_by_answer_interval(answers, random.Random(0))
    assert interval is not None
    assert interval["low"] < interval["high"] <= 1.0
    assert not math.isnan(interval["low"])


def test_kappa_interval_is_none_without_answers():
    assert kappa_by_answer_interval([], random.Random(0)) is None
