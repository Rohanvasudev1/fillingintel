"""Bootstrap 95% intervals (Step 5, ticket 06).

Each interval resamples questions with replacement under a seeded generator.
The degenerate cases below have intervals that can be worked out by hand.
"""
from statistics import fmean

import pytest

from eval.bootstrap import (
    BOOTSTRAP_RESAMPLES,
    interval_rng,
    mean_interval,
    ratio_interval,
)

SEED = 20261005


def test_identical_values_give_a_zero_width_interval():
    assert mean_interval([0.4, 0.4, 0.4], interval_rng(SEED, "x")) == pytest.approx(
        {"low": 0.4, "high": 0.4, "resamples": BOOTSTRAP_RESAMPLES})


def test_one_value_gives_a_zero_width_interval():
    assert mean_interval([0.75], interval_rng(SEED, "x"))["low"] == 0.75


def test_two_values_span_both_ends():
    # resampled means of [0, 1] are 0 (p .25), .5 (p .5) and 1 (p .25), so the 2.5th
    # percentile is 0 and the 97.5th is 1
    interval = mean_interval([0.0, 1.0], interval_rng(SEED, "x"))
    assert (interval["low"], interval["high"]) == (0.0, 1.0)


def test_no_values_give_no_interval():
    assert mean_interval([], interval_rng(SEED, "x")) is None


def test_the_same_seed_and_key_reproduce_the_interval():
    values = [i / 29 for i in range(30)]
    first = mean_interval(values, interval_rng(SEED, "agent_drafted", "vector", "lookup", "r@5"))
    again = mean_interval(values, interval_rng(SEED, "agent_drafted", "vector", "lookup", "r@5"))
    assert first == again


def test_a_different_key_draws_a_different_sample():
    values = [i / 29 for i in range(30)]
    a = mean_interval(values, interval_rng(SEED, "lookup"))
    b = mean_interval(values, interval_rng(SEED, "local"))
    assert a != b


def test_the_interval_brackets_the_mean_of_a_spread_sample():
    values = [i / 29 for i in range(30)]
    interval = mean_interval(values, interval_rng(SEED, "x"))
    assert interval["low"] < fmean(values) < interval["high"]
    assert 0.0 < interval["low"] and interval["high"] < 1.0


def test_a_ratio_interval_resamples_numerator_and_denominator_together():
    # pairs (0, 2) and (2, 4): resampled ratios 0/4 = 0, 2/6 = 1/3 and 4/8 = .5
    interval = ratio_interval([(0, 2), (2, 4)], interval_rng(SEED, "x"))
    assert (interval["low"], interval["high"]) == pytest.approx((0.0, 0.5))


def test_resamples_with_a_zero_denominator_are_left_out():
    # pairs (1, 1) and (0, 0): a resample of only (0, 0) has no ratio; every other is 1
    interval = ratio_interval([(1, 1), (0, 0)], interval_rng(SEED, "x"))
    assert (interval["low"], interval["high"]) == (1.0, 1.0)
    assert 0 < interval["resamples"] < BOOTSTRAP_RESAMPLES


def test_a_ratio_with_no_denominator_has_no_interval():
    assert ratio_interval([(0, 0), (0, 0)], interval_rng(SEED, "x")) is None
